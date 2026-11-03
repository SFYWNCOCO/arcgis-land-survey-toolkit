# -*- coding: utf-8 -*-
"""附录C「补充耕地质量鉴定图斑」取值规则（纯函数）与 FileGDB 建库（arcpy）。

依据：《广西补充耕地质量验收作业指导书 v1.1（2026-08-17）》附录C
（PDF 第49–50页，印刷页43–44）及其「修订页」第5条。字段契约直接取自
``acceptance.qa.APPENDIX_C``，本模块不另维护字段表，避免与质检器漂移。

取值状态口径
------------
CONFIRMED 源数据直接可得（含经办显式提供的值，报告注明来源）
DERIVED   按规范或标准码推导，须与国土变更调查核对
ASSUMED   业务推定，须经办确认
MISSING   无来源：写 NULL 并入待补清单；禁止写「待填/未知/××」等占位文本

本模块不 import arcpy：纯函数可在系统 Python 下测试；``build_layer`` 仅在
ArcGIS Pro Python 下调用（函数体内 import arcpy）。
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .qa import (
    APPENDIX_C,
    APPENDIX_C_CODE_FIELDS,
    APPENDIX_C_DECIMALS,
    APPENDIX_C_SOURCE,
    APPENDIX_C_VALUE_DOMAINS,
)

LAYER_NAME = "补充耕地质量鉴定图斑"

STATUS_CONFIRMED = "CONFIRMED"
STATUS_DERIVED = "DERIVED"
STATUS_ASSUMED = "ASSUMED"
STATUS_MISSING = "MISSING"
STATUSES = (STATUS_CONFIRMED, STATUS_DERIVED, STATUS_ASSUMED, STATUS_MISSING)
STATUS_LABELS = {
    STATUS_CONFIRMED: "源数据直接可得",
    STATUS_DERIVED: "按规范/标准码推导，须与变更调查核对",
    STATUS_ASSUMED: "业务推定，须经办确认",
    STATUS_MISSING: "无来源，置 NULL 并列入待补清单",
}
STATUS_RULE = (
    "CONFIRMED/DERIVED/ASSUMED/MISSING 四分级由本工具记录；"
    "任何非 CONFIRMED 值都不得在成果中按已核实处理。"
)

# 占位文本判定规则（审核修复轮 E-2）：
#   * 只有“整值等于占位词”或“整值是纯符号串（破折号/斜杠/省略号等）”才判占位；
#   * 正文里包含占位词（如“待确认后补”“暂无资料，见附件”）不得误杀；
#   * “无”“-1”“A-1”等带语义/数字的值不判占位。
PLACEHOLDER_TOKENS = (
    "待填", "待补", "待确认", "待核实", "待定", "未知", "暂无",
    "××", "×", "xx", "XX",
)
PLACEHOLDER_ASCII_TOKENS = ("TBD", "N/A", "NA", "NULL", "NONE")
PLACEHOLDER_SYMBOLS = re.compile(r"^[-—–－_/\\|~·.。…、\s]+$")

# 源表字段 → 附录C 的映射依据（示例区项目源字段；缺字段时按无来源处理）。
SOURCE_FIELD_MAP = {
    "landtype": "LANDTYPE",   # 变更后地类名称 → DLMC/DLBM
    "purpose": "PURPOSE",     # 用途 → BCGDLY
    "project_no": "XMBH",     # 项目编号 → XMBH/XDM/BCFS
    "note": "BZ",             # 备注 → BZ
    "patchtype": "PATCHTYPE", # 新增/恢复等，用于 JZGDMJ 推定条件
}

# GB/T 21010 地类编码（仅本批可确定的耕地类型；未列出的不给猜测值）。
LANDTYPE_TO_DLBM = {"水田": "0101", "水浇地": "0102", "旱地": "0103"}
# 附录C BCGDLY 值域：恢复 / 垦造。
PURPOSE_TO_BCGDLY = {"开垦": "垦造", "垦造": "垦造", "恢复": "恢复"}

FIELD_BY_NAME = {
    row[0]: {
        "name": row[0], "alias": row[1], "type": row[2], "length": row[3],
        "required": row[4], "domain": row[5],
    }
    for row in APPENDIX_C
}
FIELD_ORDER = tuple(row[0] for row in APPENDIX_C)

ARCGIS_FIELD_TYPE = {"String": "TEXT", "Double": "DOUBLE", "Single": "FLOAT", "Date": "DATE"}

NUMERIC_FIELDS = ("TBMJ", "KCXS", "KCMJ", "TBDLMJ", "JZGDMJ")


# ---------------------------------------------------------------------------
# 基础工具（纯函数）
# ---------------------------------------------------------------------------
def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def is_placeholder(value: Any) -> bool:
    """整值占位判定：整值等于占位词，或整值为纯符号串；不做子串匹配。"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text.upper() in PLACEHOLDER_ASCII_TOKENS:
        return True
    if text in PLACEHOLDER_TOKENS:
        return True
    return bool(PLACEHOLDER_SYMBOLS.match(text))


def clean_text(value: Any, label: str = "字段"):
    """去空白；占位文本按无来源处理。返回 (文本或None, 说明或None)。"""
    if is_blank(value):
        return None, None
    text = str(value).strip()
    if is_placeholder(text):
        return None, "{}命中占位词“{}”，按无来源置 NULL".format(label, text)
    return text, None


def round_area(value: Any) -> Optional[float]:
    """面积按 2 位小数四舍五入（ROUND_HALF_UP，避免二进制浮点造成 0.005 争议）。"""
    if is_blank(value):
        return None
    number = float(value)
    return float(Decimal(repr(number)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _to_float(value: Any) -> Optional[float]:
    if is_blank(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("数值字段无法解析：{!r}".format(value))
    return number


def parse_date(value: Any) -> Optional[dt.datetime]:
    """只接受 datetime 或 YYYYMMDD / YYYY-MM-DD 文本，拒绝用运行日期充数。"""
    if is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("日期字段须为 YYYYMMDD 或 YYYY-MM-DD：{!r}".format(value))


def parse_xmbh(value: Any) -> dict:
    """解析 15 位项目编号：补充方式码1位 + 县级行政区划6位 + 年度4位 + 顺序号4位。"""
    text, note = clean_text(value, "项目编号XMBH")
    if text is None:
        return {"raw": None, "parts": None, "error": note or "项目编号为空"}
    if len(text) != 15:
        return {"raw": text, "parts": None, "error": "项目编号不是15位：{!r}".format(text)}
    if not text[1:].isdigit():
        return {"raw": text, "parts": None, "error": "项目编号第2–15位应为数字：{!r}".format(text)}
    return {
        "raw": text,
        "parts": {
            "bcfs": text[0].upper(),
            "county_code": text[1:7],
            "year": text[7:11],
            "serial": text[11:15],
        },
        "error": None,
    }


def derive_xdm(xmbh_info: Mapping[str, Any], county_code: Any = None) -> dict:
    """XDM 由 XMBH 第2–7位推导；与 --county-code 不一致时报错（供调用方退出）。"""
    parts = xmbh_info.get("parts")
    explicit, _ = clean_text(county_code, "--county-code")
    derived = parts["county_code"] if parts else None
    if derived and explicit and explicit != derived:
        raise ValueError(
            "--county-code {!r} 与 XMBH 推导的县级行政区代码 {!r} 不一致，拒绝写入".format(
                explicit, derived,
            )
        )
    if derived:
        return {
            "value": derived, "status": STATUS_DERIVED,
            "source": "由 XMBH 第2–7位推导",
            "note": "须与国土变更调查县级行政区代码一致",
        }
    if explicit:
        return {
            "value": explicit, "status": STATUS_CONFIRMED,
            "source": "--county-code 参数",
            "note": "项目编号缺失，无法自动推导，按参数采用；须核对",
        }
    return {
        "value": None, "status": STATUS_MISSING, "source": "无",
        "note": "项目编号XMBH缺失，无法推导县级行政区代码",
    }


def derive_bcfs(xmbh_info: Mapping[str, Any]) -> dict:
    """BCFS 由 XMBH 首字符推导，并校验 ∈ 补充方式码值域 {Z,G,J,K,Q,F}。"""
    parts = xmbh_info.get("parts")
    note = "按15位编号规则首字符推导，须与广西耕地占补平衡动态监管系统验收阶段备案编号核对"
    if not parts:
        return {
            "value": None, "status": STATUS_MISSING, "source": "无",
            "note": "项目编号无法解析，补充方式码无来源：{}".format(xmbh_info.get("error")),
        }
    code = parts["bcfs"]
    if code not in APPENDIX_C_VALUE_DOMAINS["BCFS"]:
        return {
            "value": None, "status": STATUS_MISSING, "source": "XMBH 首字符",
            "note": "XMBH 首字符“{}”不在补充方式码值域 Z/G/J/K/Q/F 内，置 NULL 待核".format(code),
        }
    return {"value": code, "status": STATUS_DERIVED, "source": "XMBH 首字符", "note": note}


def plan_bsm_suffixes(values: Sequence[Any], max_length: int = 30):
    """按附录C注1处理标识码重复：首个保留原值，其后加 -1、-2……且全局唯一。

    规则（审核修复轮 E-1）：
    * 第一次出现的值原样保留，不做改动；
    * 同一基值再次出现时，从 -1 开始递增尝试，候选名若已被占用（源数据已有
      的值或本轮已生成的值）则跳过，直到找到未占用且不超长的候选；
    * 所有候选都超过字段长度（默认30）时不得截断冒充，置 NULL 待人工处理。
    返回 (normalized, details)。
    """
    max_length = int(max_length)
    cleaned = [clean_text(raw, "标识码")[0] for raw in values]
    used = {text for text in cleaned if text is not None}
    normalized: list = []
    details: list = []
    seen: dict = {}
    for index, text in enumerate(cleaned):
        if text is None:
            normalized.append(None)
            details.append({
                "index": index, "base": None, "result": None,
                "action": "missing", "note": "无标识码来源",
            })
            continue
        count = seen.get(text, 0)
        seen[text] = count + 1
        if count == 0:
            normalized.append(text)
            details.append({
                "index": index, "base": text, "result": text,
                "action": "keep", "note": None,
            })
            continue
        candidate_index = count
        result = None
        while candidate_index < count + max(len(cleaned), 1) + 1:
            candidate = "{}-{}".format(text, candidate_index)
            if len(candidate) > max_length:
                break
            if candidate not in used:
                result = candidate
                break
            candidate_index += 1
        if result is None:
            details.append({
                "index": index, "base": text, "result": None,
                "action": "overflow_null",
                "note": "标识码加注1后缀后超过字段长度{}位，置 NULL 待人工处理".format(max_length),
            })
            normalized.append(None)
            continue
        used.add(result)
        normalized.append(result)
        skipped = candidate_index != count
        details.append({
            "index": index, "base": text, "result": result,
            "action": "suffix", "suffix_index": candidate_index,
            "note": "同批标识码重复，按附录C注1加后缀 -{}{}".format(
                candidate_index, "（候选后缀已被占用，已跳过）" if skipped else ""),
        })
    return normalized, details


def _row(rows: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    for row in rows:
        if row["field"] == name:
            return row
    raise KeyError("字段不在附录C：{}".format(name))


def row_value(rows: Sequence[Mapping[str, Any]], name: str) -> Any:
    return _row(rows, name)["value"]


# ---------------------------------------------------------------------------
# 字段取值规则
# ---------------------------------------------------------------------------
@dataclass
class BuildOptions:
    """CLI 参数中与取值相关的部分；不含路径。"""

    county_code: Optional[str] = None
    county_name: Optional[str] = None
    project_name: Optional[str] = None
    project_no: Optional[str] = None
    assume_first_reevaluation: bool = True


def build_field_rows(
    source_row: Mapping[str, Any],
    options: Optional[BuildOptions] = None,
    provided: Optional[Mapping[str, Any]] = None,
) -> list:
    """按附录C顺序生成一个图斑的 29 个字段行（含状态、来源、说明）。

    ``source_row`` 键：id_field, feature_id, landtype, purpose, project_no,
    patchtype, note, area_m2, part_count。
    ``provided`` 为经办显式提供的 {字段名: 值}，状态记 CONFIRMED 并注明来源。
    """
    options = options or BuildOptions()
    provided = {
        str(key).strip().upper(): value
        for key, value in dict(provided or {}).items()
        if str(key).strip().upper() in FIELD_BY_NAME
    }

    rows = []
    by_name: dict = {}

    def put(name: str, value: Any, status: str, source: str, note: Optional[str] = None):
        meta = FIELD_BY_NAME[name]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                value = None
        if value is not None and is_placeholder(value):
            note = _join_note(note, "命中占位词“{}”，按无来源置 NULL".format(value))
            value, status = None, STATUS_MISSING
        if value is None and status != STATUS_MISSING:
            note = _join_note(note, "无可用来源，置 NULL")
            status = STATUS_MISSING
        by_name[name] = {
            "field": name,
            "alias": meta["alias"],
            "type": meta["type"],
            "length": meta["length"],
            "required": meta["required"],
            "domain": meta["domain"],
            "value": value,
            "status": status,
            "source": source,
            "note": note,
        }
        rows.append(by_name[name])

    xmbh_info = parse_xmbh(source_row.get("project_no") or options.project_no)
    landtype, landtype_note = clean_text(source_row.get("landtype"), "源LANDTYPE")
    purpose, purpose_note = clean_text(source_row.get("purpose"), "源PURPOSE")
    feature_id, feature_id_note = clean_text(source_row.get("feature_id"), "地块编号")
    bz, bz_note = clean_text(source_row.get("note"), "源BZ")
    patchtype, _ = clean_text(source_row.get("patchtype"), "源PATCHTYPE")
    gross_area = _to_float(source_row.get("gross_area_m2"))
    if gross_area is None:  # 兼容仅提供净面积的调用
        gross_area = _to_float(source_row.get("area_m2"))
    hole_area = _to_float(source_row.get("hole_area_m2"))
    id_field = str(source_row.get("id_field") or "地块编号")

    provided_kcmj = _to_float(provided.get("KCMJ"))
    provided_kcxs = _to_float(provided.get("KCXS"))

    # 1 标识码
    put("BSM", None, STATUS_MISSING, "源数据无标识码字段",
        "须由自然资源部门移交的国土变更调查标识码（附录C注1）；当前无来源，置 NULL 并列入待补清单")

    # 2 县级行政区代码
    xdm = derive_xdm(xmbh_info, options.county_code)
    put("XDM", xdm["value"], xdm["status"], xdm["source"], xdm["note"])

    # 3 县级行政区名称
    county_name, county_note = clean_text(options.county_name, "--county-name")
    put("XMC", county_name, STATUS_CONFIRMED if county_name else STATUS_MISSING,
        "--county-name 参数" if county_name else "无", county_note)

    # 4/5 乡镇代码、乡镇名称
    put("XZDM", None, STATUS_MISSING, "无乡镇资料", "缺少乡镇（街道）代码资料，须由经办补充")
    put("XZMC", None, STATUS_MISSING, "无乡镇资料", "缺少乡镇（街道）名称资料，须由经办补充")

    # 6/7 变更前地类编码、名称
    put("BGQDLBM", None, STATUS_MISSING, "无变更调查资料",
        "须由国土变更调查移交变更前地类编码")
    put("BGQDLMC", None, STATUS_MISSING, "无变更调查资料",
        "须由国土变更调查移交变更前地类名称")

    # 8/9 地类编码、名称
    dlbm = LANDTYPE_TO_DLBM.get(landtype) if landtype else None
    if dlbm:
        put("DLBM", dlbm, STATUS_DERIVED, "由源LANDTYPE映射GB/T 21010",
            "须与国土变更调查地类编码一致（水田0101/水浇地0102/旱地0103）")
    else:
        put("DLBM", None, STATUS_MISSING, "源LANDTYPE不在映射范围",
            "地类“{}”不在本批GB/T 21010映射表内，须按变更调查确认".format(landtype or "空"))
    put("DLMC", landtype, STATUS_CONFIRMED if landtype else STATUS_MISSING,
        "源LANDTYPE原值" if landtype else "无", landtype_note)

    # 10/11 坐落单位代码、名称
    put("ZLDWDM", None, STATUS_MISSING, "无村级资料", "缺少村级坐落单位代码资料，须由经办补充")
    put("ZLDWMC", None, STATUS_MISSING, "无村级资料", "缺少村级坐落单位名称资料，须由经办补充")

    # 12 图斑面积（毛面积 = 外环面积合计；附录C注2 要求重算）
    if gross_area is not None and gross_area > 0:
        put("TBMJ", round_area(gross_area), STATUS_DERIVED, "重算：外环面积合计（毛面积）",
            "TBMJ 为毛面积（含扣除地类），孔洞面积另计 KCMJ；EPSG:4524 平面面积（m²），保留2位小数；须与变更调查图斑面积口径核对")
    else:
        put("TBMJ", None, STATUS_MISSING, "无有效几何", "几何面积无效或缺失，无法重算图斑面积")

    # 13/14/15 扣除地类编码、系数、面积（C 条件必填）
    put("KCDLBM", None, STATUS_MISSING, "无扣除地类资料",
        "孔洞来源为源shp.xml血缘中的 Erase 森林资源调查2025年，按现有资料推不出扣除地类编码；须与变更调查核对")
    put("KCXS", provided_kcxs, STATUS_CONFIRMED if provided_kcxs is not None else STATUS_MISSING,
        "用户提供（--attrs-json）" if provided_kcxs is not None else "无扣除地类资料",
        "未提供扣除地类系数；孔洞不能硬填系数 1（KCXS 值域 [0,1)），C 条件必填")
    if provided_kcmj is not None:
        put("KCMJ", provided_kcmj, STATUS_CONFIRMED, "用户提供（--attrs-json）",
            "经办显式提供的扣除地类面积，须核对")
    elif hole_area is not None:
        put("KCMJ", round_area(hole_area), STATUS_DERIVED, "重算：几何孔洞（内环）面积合计",
            "孔洞来自源shp.xml血缘 Erase 森林资源调查2025年；扣除地类编码/系数无法从源数据确定，须与变更调查核对")
    else:
        put("KCMJ", None, STATUS_MISSING, "无扣除地类资料", "无孔洞/扣除资料，C 条件必填")

    # 16 图斑地类面积（净面积 = 毛面积 - 扣除）
    tbmj = row_value(rows, "TBMJ")
    kcmj = row_value(rows, "KCMJ")
    if tbmj is not None:
        put("TBDLMJ", round_area(tbmj - (kcmj or 0.0)), STATUS_DERIVED,
            "TBDLMJ = TBMJ - KCMJ",
            "按净面积计；KCMJ 为{}".format(
                "用户提供值" if provided_kcmj is not None else "几何孔洞面积合计"))
    else:
        put("TBDLMJ", None, STATUS_MISSING, "无有效几何", "图斑面积缺失，无法计算图斑地类面积")

    # 17/18 耕地类型、耕地坡度级别（C 条件必填）
    put("GDLX", None, STATUS_MISSING, "无利用类型资料",
        "无权威码表与利用类型资料，不得自造代码；C 条件必填")
    put("GDPDJB", None, STATUS_MISSING, "无坡度级别资料",
        "无坡度级别资料，不得自造代码；C 条件必填")

    # 19 更新时间
    put("GXSJ", None, STATUS_MISSING, "无变更调查资料",
        "须取国土变更调查更新时间；禁止用本次运行日期充数")

    # 20 项目名称/批次
    project_name, project_name_note = clean_text(options.project_name, "--project-name")
    if project_name:
        put("XMMC", project_name, STATUS_ASSUMED, "--project-name 参数",
            _join_note("按本批批次名业务推定，须与源文件名及shp.xml血缘名称核对",
                       project_name_note))
    else:
        put("XMMC", None, STATUS_MISSING, "无项目名称来源",
            "未提供 --project-name，且源数据无项目名称字段")

    # 21 项目编号
    explicit_project_no, _ = clean_text(options.project_no, "--project-no")
    xmbh_value = xmbh_info.get("raw")
    if xmbh_value:
        put("XMBH", xmbh_value, STATUS_CONFIRMED, "源XMBH", 
            "须核实是否为广西耕地占补平衡动态监管系统验收阶段备案编号")
    elif explicit_project_no:
        put("XMBH", explicit_project_no, STATUS_CONFIRMED, "--project-no 参数",
            "源数据无项目编号，按参数采用；须核对")
    else:
        put("XMBH", None, STATUS_MISSING, "无来源",
            "源数据与参数均无项目编号，须从监管系统取得")

    # 22 地块编号
    put("DKBH", feature_id, STATUS_CONFIRMED if feature_id else STATUS_MISSING,
        "源{}字段".format(id_field) if feature_id else "无",
        _join_note("源地块编号原值", feature_id_note))

    # 23 净增耕地面积
    tbdmj = row_value(rows, "TBDLMJ")
    if tbdmj is not None and patchtype == "新增" and provided_kcmj is None:
        put("JZGDMJ", tbdmj, STATUS_ASSUMED, "按净面积 TBDLMJ 推定",
            "本批 PATCHTYPE=新增、变更前非耕地，净增耕地面积按净面积 TBDLMJ（=TBMJ-KCMJ）推定；须经办确认")
    else:
        reason = "缺少变更前后耕地面积资料"
        if patchtype and patchtype != "新增":
            reason = "源PATCHTYPE={}，非新增图斑，须按变更前后数据计算".format(patchtype)
        put("JZGDMJ", None, STATUS_MISSING, "无变更前后面积资料", reason)

    # 24/25 是否有客土或物料填埋、是否是未利用地/复垦
    put("SFYKT", None, STATUS_MISSING, "无申请表/污染调查资料",
        "须由申请表、污染调查或客土资料确定，不得推定")
    put("SFFK", None, STATUS_MISSING, "无申请表/复垦资料",
        "须由申请表或复垦资料确定，不得推定")

    # 26 是否再评价
    if options.assume_first_reevaluation:
        put("SFZPJ", "否", STATUS_ASSUMED, "按首次鉴定推定",
            "本批为首次鉴定、无再评价记录，按“否”业务推定；须经办确认")
    else:
        put("SFZPJ", None, STATUS_MISSING, "无再评价资料", "无再评价记录资料，不得推定")

    # 27 补充方式
    bcfs = derive_bcfs(xmbh_info)
    put("BCFS", bcfs["value"], bcfs["status"], bcfs["source"], bcfs["note"])

    # 28 补充耕地来源
    bcgdly = PURPOSE_TO_BCGDLY.get(purpose) if purpose else None
    if bcgdly:
        put("BCGDLY", bcgdly, STATUS_DERIVED, "由源PURPOSE映射",
            "附录C值域：恢复/垦造；源PURPOSE={}，须与项目批复核对".format(purpose))
    else:
        put("BCGDLY", None, STATUS_MISSING, "源PURPOSE缺失或不在映射内",
            _join_note("补充耕地来源无权威来源，须项目批复确认", purpose_note))

    # 29 备注（C 条件必填）
    put("BZ", bz, STATUS_CONFIRMED if bz else STATUS_MISSING,
        "源BZ原值" if bz else "源BZ为空",
        _join_note(None if bz else "源BZ为空，置 NULL；C 条件必填", bz_note))

    # 经办显式提供的值覆盖自动推导值（来源可追溯，不冒称 CONFIRMED 源数据）。
    for name, value in provided.items():
        if name in ("KCMJ", "KCXS"):
            continue  # 已在面积计算中消费
        text = None if is_blank(value) else str(value).strip()
        if text is None:
            continue
        target = by_name[name]
        if is_placeholder(text):
            target.update({
                "value": None,
                "status": STATUS_MISSING,
                "source": "用户提供（--attrs-json）",
                "note": _join_note(target.get("note"),
                                   "经办提供值命中占位词“{}”，按无来源置 NULL".format(text)),
            })
            continue
        target.update({
            "value": text if target["type"] == "String" else text,
            "status": STATUS_CONFIRMED,
            "source": "用户提供（--attrs-json）",
            "note": _join_note(target.get("note"), "经办显式提供值，未经自动推导，须核对"),
        })

    ordered = []
    for name in FIELD_ORDER:
        row = by_name[name]
        row["value"] = _coerce_value(row["field"], row["value"])
        if row["value"] is None and row["status"] != STATUS_MISSING:
            row["status"] = STATUS_MISSING
            row["note"] = _join_note(row.get("note"), "无可用来源，置 NULL")
        ordered.append(row)
    return ordered


def _join_note(*parts: Optional[str]) -> Optional[str]:
    joined = [str(part).strip() for part in parts if part and str(part).strip()]
    return "；".join(joined) if joined else None


def _coerce_value(name: str, value: Any) -> Any:
    if value is None:
        return None
    field_type = FIELD_BY_NAME[name]["type"]
    if field_type in ("Double", "Single"):
        return float(value)
    if field_type == "Date":
        return parse_date(value)
    return str(value).strip()


def apply_bsm_suffixes(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]],
                       feature_ids: Optional[Sequence[Any]] = None):
    """跨图斑处理 BSM 重复：首个保留原值，其余按注1加 -1、-2……（全局唯一）。

    加后缀的值属于按规范推导，状态记 DERIVED，不得记 CONFIRMED（审核 4.2）。
    """
    base_values = [row_value(rows, "BSM") for rows in rows_by_feature]
    normalized, details = plan_bsm_suffixes(base_values, FIELD_BY_NAME["BSM"]["length"])
    feature_ids = list(feature_ids or range(len(rows_by_feature)))
    for rows, value, detail, feature_id in zip(rows_by_feature, normalized, details, feature_ids):
        target = _row(rows, "BSM")
        target["value"] = value
        detail["feature_id"] = None if feature_id is None else str(feature_id)
        if value is None:
            target["status"] = STATUS_MISSING
            target["note"] = _join_note(target.get("note"), detail.get("note"))
        elif detail["action"] == "suffix":
            target["status"] = STATUS_DERIVED
            target["source"] = "用户提供BSM + 附录C注1后缀"
            target["note"] = _join_note(target.get("note"), detail["note"])
    return rows_by_feature, details


# ---------------------------------------------------------------------------
# 校验与统计（纯函数）
# ---------------------------------------------------------------------------
def row_errors(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]]) -> list:
    """结构性错误：占位文本、超长、类型不符、数值非有限。"""
    errors = []
    for rows in rows_by_feature:
        for row in rows:
            name = row["field"]
            value = row["value"]
            if value is None:
                continue
            if is_placeholder(value):
                errors.append({"field": name, "value": value, "reason": "占位文本不得写入"})
                continue
            if row["type"] == "String":
                text = str(value)
                if len(text) > int(row["length"]):
                    errors.append({
                        "field": name, "value": text,
                        "reason": "长度{}超过字段长度{}".format(len(text), row["length"]),
                    })
            elif row["type"] in ("Double", "Single"):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    errors.append({"field": name, "value": value, "reason": "不是数值"})
                    continue
                if number != number or number in (float("inf"), float("-inf")):
                    errors.append({"field": name, "value": value, "reason": "数值非有限"})
    return errors


def domain_violations(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]]) -> list:
    """值域校验：附录C显式值域 + 数值约束（TBMJ/TBDLMJ/JZGDMJ>0、KCMJ>=0、KCXS∈[0,1)）。"""
    issues = []
    for rows in rows_by_feature:
        for row in rows:
            name = row["field"]
            value = row["value"]
            if value is None:
                continue
            if name in APPENDIX_C_VALUE_DOMAINS:
                normalized = str(value).strip().upper() if name == "BCFS" else str(value).strip()
                if normalized not in APPENDIX_C_VALUE_DOMAINS[name]:
                    issues.append({
                        "field": name, "value": str(value),
                        "domain": sorted(APPENDIX_C_VALUE_DOMAINS[name]),
                        "reason": "值域不符",
                    })
                continue
            if row["domain"] is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                issues.append({"field": name, "value": str(value), "domain": row["domain"]})
                continue
            if name in ("TBMJ", "TBDLMJ", "JZGDMJ"):
                valid = number > 0
            elif name == "KCMJ":
                valid = number >= 0
            elif name == "KCXS":
                valid = 0 <= number < 1
            else:
                valid = True
            if not valid:
                issues.append({"field": name, "value": str(value), "domain": row["domain"]})
    return issues


def field_status_map(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]]) -> dict:
    mapping = {}
    for name in FIELD_ORDER:
        statuses = {_row(rows, name)["status"] for rows in rows_by_feature}
        mapping[name] = statuses.pop() if len(statuses) == 1 else "MIXED"
    return mapping


def status_counts(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]]) -> dict:
    per_field = {status: 0 for status in STATUSES}
    for status in field_status_map(rows_by_feature).values():
        if status in per_field:
            per_field[status] += 1
    per_feature_row = {status: 0 for status in STATUSES}
    for rows in rows_by_feature:
        for row in rows:
            if row["status"] in per_feature_row:
                per_feature_row[row["status"]] += 1
    return {"per_field": per_field, "per_feature_row": per_feature_row}


def missing_fields(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]],
                   required: str = "M",
                   feature_ids: Optional[Sequence[Any]] = None) -> list:
    """缺失字段清单；required='M' 为必填缺口，'C' 为条件必填缺口。"""
    feature_ids = list(feature_ids or range(len(rows_by_feature)))
    result = []
    for name in FIELD_ORDER:
        meta = FIELD_BY_NAME[name]
        if meta["required"] != required:
            continue
        if all(_row(rows, name)["status"] == STATUS_MISSING for rows in rows_by_feature):
            affected = [
                str(feature_id) for rows, feature_id in zip(rows_by_feature, feature_ids)
                if _row(rows, name)["status"] == STATUS_MISSING
            ]
            reason = None
            for rows in rows_by_feature:
                reason = _row(rows, name).get("note")
                if reason:
                    break
            result.append({
                "field": name, "alias": meta["alias"], "required": required,
                "value": None, "affected_features": affected, "reason": reason,
            })
    return result


def gap_table(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]],
              feature_ids: Optional[Sequence[Any]] = None) -> list:
    """待补清单：字段级汇总缺失字段（M 必填 + C 条件必填）。"""
    feature_ids = list(feature_ids or range(len(rows_by_feature)))
    rows = []
    for required in ("M", "C"):
        rows.extend(missing_fields(rows_by_feature, required=required, feature_ids=feature_ids))
    return rows


def field_source_rows(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]],
                      feature_ids: Optional[Sequence[Any]] = None) -> list:
    """字段来源表（逐图斑×逐字段），用于追溯每个值的状态与依据。"""
    feature_ids = list(feature_ids or range(len(rows_by_feature)))
    out = []
    for rows, feature_id in zip(rows_by_feature, feature_ids):
        for row in rows:
            out.append({
                "地块编号": "" if feature_id is None else str(feature_id),
                "序号": FIELD_ORDER.index(row["field"]) + 1,
                "字段名": row["field"],
                "字段别名": row["alias"],
                "必填性": row["required"],
                "状态": row["status"],
                "取值": "" if row["value"] is None else row["value"],
                "来源依据": row["source"],
                "备注": row["note"] or "",
            })
    return out


def summarize(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]],
              feature_ids: Optional[Sequence[Any]] = None) -> dict:
    feature_ids = list(feature_ids or range(len(rows_by_feature)))
    counts = status_counts(rows_by_feature)
    return {
        "field_count": len(APPENDIX_C),
        "feature_count": len(rows_by_feature),
        "status_counts": counts,
        "status_rule": STATUS_RULE,
        "field_status": field_status_map(rows_by_feature),
        "missing_required_fields": missing_fields(rows_by_feature, "M", feature_ids),
        "missing_conditional_fields": missing_fields(rows_by_feature, "C", feature_ids),
        "value_domain_violations": domain_violations(rows_by_feature),
        "row_errors": row_errors(rows_by_feature),
    }


# ---------------------------------------------------------------------------
# 源文件哈希与元数据（纯函数）
# ---------------------------------------------------------------------------
SOURCE_HASH_SUFFIXES = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".shp.xml", ".sbn", ".sbx")


def source_family_hashes(source_path: Any) -> dict:
    """记录源 shp 家族各文件的大小与 sha256（用于运行前后可比对，禁止改源）。"""
    path = Path(source_path)
    result = {}
    for suffix in SOURCE_HASH_SUFFIXES:
        candidate = path.with_suffix(suffix) if suffix.startswith(".") else None
        if candidate is None or not candidate.exists():
            continue
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        result[suffix] = {
            "file": candidate.name,
            "size": candidate.stat().st_size,
            "sha256": digest,
            "sha256_16": digest[:16].upper(),
        }
    return result


def read_source_metadata_names(source_path: Any) -> dict:
    """从 shp.xml 元数据血缘/标题提取源图层名，供与文件名、项目名比对。"""
    path = Path(source_path)
    xml_path = path.with_suffix(".shp.xml")
    info = {
        "xml_file": str(xml_path),
        "res_title": None,
        "lineage_names": [],
        "xmbh_source_name": None,
    }
    if not xml_path.exists():
        return info
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    title = re.search(r"<resTitle[^>]*>(.*?)</resTitle>", text)
    if title:
        info["res_title"] = title.group(1).strip()
    names = []
    for match in re.finditer(r"CalculateField\s+([^\s<]+)\s+([A-Za-z0-9_]+)", text):
        name, field = match.group(1).strip(), match.group(2).strip()
        if name not in names:
            names.append(name)
        if field.upper() == "XMBH" and not info["xmbh_source_name"]:
            info["xmbh_source_name"] = name
    info["lineage_names"] = names
    return info


def _years(text: Optional[str]) -> set:
    return set(re.findall(r"(20\d{2})", text or ""))


def polygon_ring_stats(geometry_json: Any) -> dict:
    """统计 Esri JSON 的环：外环数、孔洞数（用符号面积区分外环与孔洞）。

    ArcPy 对含孔洞的 Polygon 会返回 ``isMultipart=True`` 而 ``partCount=1``；
    附录C注6 的「多部件」应看独立外环数（或 partCount>1），不能直接用
    ``isMultipart``，否则含扣除地类孔洞的正常单部件图斑会被误判。
    """
    data = geometry_json
    if isinstance(data, str):
        data = json.loads(data)
    rings = (data or {}).get("rings") or []
    # 先平移到局部原点再算符号面积：false easting 约 3.65e7，直接做鞋带公式
    # 会因 float64 抵消损失 0.01–0.05 m² 精度。
    origin_x = float(rings[0][0][0]) if rings and rings[0] else 0.0
    origin_y = float(rings[0][0][1]) if rings and rings[0] else 0.0
    signed = []
    for ring in rings:
        total = 0.0
        for index in range(len(ring) - 1):
            x1, y1 = float(ring[index][0]) - origin_x, float(ring[index][1]) - origin_y
            x2, y2 = float(ring[index + 1][0]) - origin_x, float(ring[index + 1][1]) - origin_y
            total += x1 * y2 - x2 * y1
        signed.append(total / 2.0)
    if not signed:
        return {"ring_count": 0, "exterior_rings": 0, "hole_rings": 0,
                "multipart": False, "gross_area": 0.0, "hole_area": 0.0, "net_area": 0.0}
    reference = max(signed, key=abs)
    reference_positive = reference > 0
    exterior = sum(1 for area in signed if (area > 0) == reference_positive)
    exterior_signed = sum(area for area in signed if (area > 0) == reference_positive)
    hole_signed = sum(area for area in signed if (area > 0) != reference_positive)
    return {
        "ring_count": len(rings),
        "exterior_rings": exterior,
        "hole_rings": len(rings) - exterior,
        "multipart": exterior > 1,
        "gross_area": abs(exterior_signed),
        "hole_area": abs(hole_signed),
        "net_area": abs(sum(signed)),
    }


def project_name_conflicts(source_path: Any, project_name: Optional[str]) -> list:
    """项目名称/批次冲突：文件名 vs shp.xml 血缘（XMBH 赋值过程名）/标题 vs 参数。

    只并列来源与差异，不自行选定项目名；是否冲突由经办核定。
    """
    stem = Path(source_path).stem
    meta = read_source_metadata_names(source_path)
    xmbh_name = meta.get("xmbh_source_name")
    res_title = meta.get("res_title")
    values = {
        "源文件名": stem,
        "shp.xml中XMBH赋值来源名": xmbh_name,
        "shp.xml resTitle（元数据常未更新，仅供参考）": res_title,
        "--project-name": project_name,
    }
    conflicts = []
    if xmbh_name and xmbh_name != stem:
        stem_years, xml_years = _years(stem), _years(xmbh_name)
        year_conflict = bool(stem_years and xml_years and not (stem_years & xml_years))
        conflicts.append({
            "item": "项目名称/批次与源文件名的年度或批次不一致",
            "status": "待核",
            "year_conflict": year_conflict,
            "values": values,
            "reason": (
                "文件名为“{}”，shp.xml 中给 XMBH 赋值的源图层名为“{}”；"
                "两者{}不一致。不得按文件名或元数据任一方自行选定批次，须经办确认后写入 XMMC。"
            ).format(stem, xmbh_name, "年度" if year_conflict else "批次"),
            "source": "{} 的 <lineage><Process ToolSource=CalculateField> 过程名".format(
                meta["xml_file"]),
        })
    if project_name and xmbh_name and project_name not in xmbh_name and xmbh_name not in project_name:
        conflicts.append({
            "item": "--project-name 与 shp.xml 血缘名称不一致",
            "status": "待核",
            "year_conflict": False,
            "values": values,
            "reason": "参数批次名与源数据血缘名称不一致，须核对后确认 XMMC",
            "source": meta["xml_file"],
        })
    distinct = {value for key, value in values.items() if value and "resTitle" not in key}
    if len(distinct) > 1 and not conflicts:
        conflicts.append({
            "item": "项目名称存在多个来源且不完全一致",
            "status": "待核",
            "year_conflict": False,
            "values": values,
            "reason": "须经办确认批次与年度后再定稿 XMMC",
            "source": meta["xml_file"],
        })
    return conflicts


# ---------------------------------------------------------------------------
# FileGDB 建库（仅 ArcGIS Pro Python）
# ---------------------------------------------------------------------------
def field_descriptions() -> list:
    """AddFields 参数：字段名、类型、别名、长度（数值型长度由存储决定）。"""
    descriptions = []
    for name, alias, field_type, length, _required, _domain in APPENDIX_C:
        descriptions.append([
            name,
            ARCGIS_FIELD_TYPE[field_type],
            alias,
            length if field_type == "String" else "",
            "",
            "",
        ])
    return descriptions


def compare_schema(actual_fields: Iterable[Any]) -> dict:
    """读回字段与 APPENDIX_C 比对（只对 String 比长度，数值型长度是存储属性）。"""
    actual = {str(field.name).upper(): field for field in actual_fields}
    missing = []
    mismatched = []
    for name, _alias, expected_type, expected_length, _required, _domain in APPENDIX_C:
        field = actual.get(name)
        if field is None:
            missing.append(name)
            continue
        actual_type = str(getattr(field, "type", "") or "")
        length_mismatch = expected_type == "String" and getattr(field, "length", None) != expected_length
        if actual_type.lower() != expected_type.lower() or length_mismatch:
            mismatched.append({
                "field": name,
                "expected": {"type": expected_type, "length": expected_length},
                "actual": {"type": actual_type, "length": getattr(field, "length", None)},
            })
    return {
        "status": "PASS" if not missing and not mismatched else "FAIL",
        "expected_count": len(APPENDIX_C),
        "actual_count": len(actual),
        "missing": missing,
        "mismatched": mismatched,
    }


def _insert_value(row: Mapping[str, Any]) -> Any:
    value = row["value"]
    if value is None:
        return None
    if row["type"] == "Date":
        return parse_date(value)
    if row["type"] in ("Double", "Single"):
        return float(value)
    return str(value)


def build_layer(out_gdb: Any, source_path: Any, feature_values: Sequence[Mapping[str, Any]],
                layer_name: str = LAYER_NAME, expect_wkid: Optional[int] = None) -> dict:
    """按 APPENDIX_C 建 29 字段图层并写入要素；CRS 继承源数据（不重投影）。

    ``feature_values`` 为 [{"oid": int, "values": {字段名: 值}}, ...]。
    不覆盖已存在的 gdb；失败时回滚本次创建的 gdb。多部件要素仍写出以便复核，
    但在返回结果中标记 FAIL（附录C注6禁止，策略见报告）。
    """
    import arcpy

    out_gdb = Path(out_gdb).expanduser().resolve()
    source_path = Path(source_path).expanduser().resolve()
    if out_gdb.suffix.lower() != ".gdb":
        raise ValueError("输出路径必须以 .gdb 结尾")
    if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
        raise FileExistsError("输出已存在，不覆盖：{}".format(out_gdb))
    if not arcpy.Exists(str(source_path)):
        raise FileNotFoundError("源数据不存在：{}".format(source_path))

    source_desc = arcpy.Describe(str(source_path))
    spatial_reference = source_desc.spatialReference
    wkid = int(getattr(spatial_reference, "factoryCode", 0) or 0)
    if expect_wkid is not None and wkid != int(expect_wkid):
        raise ValueError("源数据坐标系 WKID={} 与 --expect-wkid={} 不一致，拒绝写入".format(
            wkid, expect_wkid))

    out_gdb.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        arcpy.management.CreateFileGDB(str(out_gdb.parent), out_gdb.name, "CURRENT")
        created = True
        feature_class = str(out_gdb / layer_name)
        arcpy.management.CreateFeatureclass(
            str(out_gdb), layer_name, "POLYGON",
            has_m="DISABLED", has_z="DISABLED", spatial_reference=spatial_reference,
        )
        arcpy.management.AddFields(feature_class, field_descriptions())

        geometries = {}
        with arcpy.da.SearchCursor(str(source_path), ["OID@", "SHAPE@"]) as cursor:
            for oid, geometry in cursor:
                geometries[oid] = geometry
        missing_oids = [item["oid"] for item in feature_values if item["oid"] not in geometries]
        if missing_oids:
            raise ValueError("源数据缺少 OID：{}".format(missing_oids))

        fields = list(FIELD_ORDER)
        with arcpy.da.InsertCursor(feature_class, fields + ["SHAPE@"]) as cursor:
            for item in feature_values:
                values = item["values"]
                row = [_insert_value(FIELD_BY_NAME[name] | {"value": values.get(name)})
                       for name in fields]
                cursor.insertRow(row + [geometries[item["oid"]]])

        count = int(arcpy.management.GetCount(feature_class)[0])
        actual_fields = list(arcpy.ListFields(feature_class))
        schema = compare_schema(actual_fields)
        readback = _readback_check(feature_class)
        return {
            "geodatabase": str(out_gdb),
            "feature_class": feature_class,
            "layer_name": layer_name,
            "count": count,
            "wkid": wkid,
            "crs_name": str(getattr(spatial_reference, "name", "Unknown")),
            "schema": schema,
            "fields": [
                {"name": field.name, "type": field.type, "length": getattr(field, "length", None)}
                for field in actual_fields
            ],
            "area_check": readback,
        }
    except Exception:
        if created:
            try:
                if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
                    arcpy.management.Delete(str(out_gdb))
            except Exception:
                pass
        raise


def _readback_check(feature_class: Any) -> dict:
    """读回 TBMJ/TBDLMJ 与几何面积差异（≤0.01 m²），并统计真多部件与孔洞。

    多部件判定用 ``partCount>1``（或 Esri JSON 外环数>1），不用 ArcPy 的
    ``isMultipart``：含孔洞的多边形 ``isMultipart`` 也为 True。
    """
    import arcpy

    mismatches = []
    multipart_ids = []
    hole_parcels = []
    per_feature = []
    checked = 0
    max_diff = 0.0
    with arcpy.da.SearchCursor(feature_class, ["DKBH", "TBMJ", "TBDLMJ", "KCMJ", "SHAPE@"]) as cursor:
        for dkbh, tbmj, tbdmj, kcmj, geometry in cursor:
            checked += 1
            net_area = float(geometry.area) if geometry is not None else None
            part_count = int(getattr(geometry, "partCount", 1)) if geometry is not None else 0
            ring_stats = polygon_ring_stats(geometry.JSON) if geometry is not None else {
                "exterior_rings": 0, "hole_rings": 0, "multipart": False,
                "gross_area": None, "hole_area": None, "net_area": None,
            }
            if part_count > 1 or ring_stats["multipart"]:
                multipart_ids.append(str(dkbh))
            if ring_stats["hole_rings"]:
                hole_parcels.append({
                    "DKBH": str(dkbh),
                    "hole_rings": ring_stats["hole_rings"],
                    "exterior_rings": ring_stats["exterior_rings"],
                    "hole_area_m2": round(ring_stats["hole_area"], 2),
                })
            item = {
                "DKBH": None if dkbh is None else str(dkbh),
                "TBMJ": None if tbmj is None else float(tbmj),
                "KCMJ": None if kcmj is None else float(kcmj),
                "TBDLMJ": None if tbdmj is None else float(tbdmj),
                "gross_area_m2": None if ring_stats["gross_area"] is None else round(ring_stats["gross_area"], 4),
                "hole_area_m2": None if ring_stats["hole_area"] is None else round(ring_stats["hole_area"], 4),
                "net_area_m2": None if net_area is None else round(net_area, 4),
                "partCount": part_count,
                "ring_count": ring_stats.get("ring_count"),
                "exterior_rings": ring_stats["exterior_rings"],
                "hole_rings": ring_stats["hole_rings"],
            }
            per_feature.append(item)
            if net_area is None or tbmj is None or ring_stats["gross_area"] is None:
                mismatches.append({"DKBH": item["DKBH"], "reason": "空几何或空图斑面积"})
                continue
            # TBMJ 为毛面积（外环面积合计）
            gross_diff = round(abs(float(tbmj) - ring_stats["gross_area"]), 4)
            max_diff = max(max_diff, gross_diff)
            if gross_diff > 0.01:
                mismatches.append({
                    "DKBH": item["DKBH"], "TBMJ": float(tbmj),
                    "gross_area": round(ring_stats["gross_area"], 4),
                    "diff": gross_diff,
                    "reason": "TBMJ 与外环面积合计（毛面积）差异超过0.01 m²",
                })
            # TBDLMJ 为净面积（= 几何面积）
            if tbdmj is not None:
                net_diff = round(abs(float(tbdmj) - net_area), 4)
                max_diff = max(max_diff, net_diff)
                if net_diff > 0.01:
                    mismatches.append({
                        "DKBH": item["DKBH"], "TBDLMJ": float(tbdmj),
                        "net_area": round(net_area, 4), "diff": net_diff,
                        "reason": "TBDLMJ 与几何净面积差异超过0.01 m²",
                    })
            if tbdmj is not None:
                relation_diff = round(abs(float(tbdmj) - (float(tbmj) - float(kcmj or 0.0))), 4)
                if relation_diff > 0.01:
                    mismatches.append({
                        "DKBH": item["DKBH"], "TBDLMJ": float(tbdmj), "TBMJ": float(tbmj),
                        "KCMJ": None if kcmj is None else float(kcmj),
                        "diff": relation_diff,
                        "reason": "TBDLMJ 应等于 TBMJ - KCMJ（KCMJ缺省0）",
                    })
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "tolerance_m2": 0.01,
        "checked_count": checked,
        "max_abs_diff_m2": round(max_diff, 6),
        "mismatches": mismatches,
        "per_feature": per_feature,
        "multipart_count": len(multipart_ids),
        "multipart_DKBH": multipart_ids,
        "multipart_rule": "partCount>1 或 Esri JSON 外环数>1；ArcPy isMultipart 对含孔洞多边形也为 True，不能作注6判定",
        "hole_parcels": hole_parcels,
        "hole_note": "孔洞（内环）为扣除地类，计入 KCMJ；不属附录C注6禁止的多部件",
        "area_rule": "TBMJ=外环面积合计（毛）；KCMJ=孔洞面积合计；TBDLMJ=TBMJ-KCMJ（=几何净面积）；各差≤0.01 m²",
    }


# ---------------------------------------------------------------------------
# 落盘工具
# ---------------------------------------------------------------------------
def write_json(path: Any, payload: Mapping[str, Any]) -> str:
    path = Path(path)
    if path.exists():
        raise FileExistsError("输出已存在，不覆盖：{}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=_json_default)
        stream.write("\n")
    return str(path)


def write_csv(path: Any, header: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    path = Path(path)
    if path.exists():
        raise FileExistsError("输出已存在，不覆盖：{}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(header), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return str(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def contract_echo() -> dict:
    """报告用：附录C字段契约与来源（直接取自 acceptance.qa）。"""
    return {
        "source": dict(APPENDIX_C_SOURCE),
        "field_count": len(APPENDIX_C),
        "fields": [
            {
                "field": name, "alias": alias, "type": field_type, "length": length,
                "required": required, "value_domain": domain,
                "decimals": APPENDIX_C_DECIMALS.get(name),
            }
            for name, alias, field_type, length, required, domain in APPENDIX_C
        ],
        "code_fields": list(APPENDIX_C_CODE_FIELDS),
        "value_domains": {name: sorted(values) for name, values in APPENDIX_C_VALUE_DOMAINS.items()},
    }
