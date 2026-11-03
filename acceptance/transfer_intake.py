# -*- coding: utf-8 -*-
"""自然资源移交包接入：收件核对、附录C原样复制/重建、DLTB→附录C映射、图斑-地块套合。

依据：《广西补充耕地质量验收作业指导书 v1.1（2026-08-17）》附录C、附录I 注2，
以及桂农厅发〔2026〕42号实施细则。字段契约直接取自 ``acceptance.qa.APPENDIX_C``，
本模块不另存字段表。

设计要点
--------
* 纯逻辑（图层识别、字段契约比对、DLTB/重建取值映射、套合匹配分类、哈希、
  区划表解析、输出占用检查）不 import arcpy，可在系统 Python 下测试；
* 与 arcpy 相关的函数在函数体内 import arcpy（Pro Python 下调用）；
* 移交的附录C鉴定图斑若 29 字段名/类型/长度全对 → 原样 CopyFeatures，不改字段、
  不改值、不重编号（附录I 注2）；只有不符合时才按附录C重建，且只承接已有值；
* 缺源一律 NULL 并进待补清单，禁止占位文本，不凭空补值。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .appendix_c import (
    FIELD_BY_NAME,
    FIELD_ORDER,
    STATUS_ASSUMED,
    STATUS_CONFIRMED,
    STATUS_DERIVED,
    STATUS_MISSING,
    BuildOptions,
    build_layer,
    compare_schema,
    contract_echo,
    field_source_rows,
    gap_table,
    is_blank,
    is_placeholder,
    parse_date,
    summarize,
    write_csv,
    write_json,
)
from .qa import APPENDIX_C, APPENDIX_C_DECIMALS, APPENDIX_C_VALUE_DOMAINS

LAYER_NAME = "补充耕地质量鉴定图斑"
DLTB_HINTS = ("国土变更", "变更图斑", "DLTB")
APPENDIX_C_HINTS = ("鉴定图斑",)
MATCH_TOLERANCE_M2 = 0.01
MATCH_TYPES = ("1:1", "1:N", "N:1", "无匹配", "跨界")
MATCH_CSV_HEADER = [
    "DKBH", "特征OID", "鉴定图斑BSM", "重叠面积_m2",
    "占地块面积比", "占图斑面积比", "匹配类型",
]
GAP_CSV_HEADER = [
    "序号", "字段名", "字段别名", "必填性", "缺失原因", "涉及要素", "附录C依据",
]
QA_NAME = "附录C质检.json"
REPORT_NAME = "移交核对报告.json"
GAP_NAME = "待补清单.csv"
MATCH_NAME = "图斑-地块对照表.csv"
OUT_GDB_NAME = "附录C鉴定图斑.gdb"

UNVERIFIED_ITEMS = [
    "“已符合附录C”判定仅比较 29 字段名/类型/String 长度，不校验取值语义与业务正确性",
    "移交图层的业务真实性（是否为本项目、是否为变更调查成果）须经办确认",
    "XZMC 需行政区划名称表（--region-table）或项目资料，未提供时为 NULL",
    "BGQDLBM/BGQDLMC 需变更前图层或项目资料，本工具不从现有字段反推",
    "项目字段（XMMC/XMBH/DKBH/JZGDMJ/SFYKT/SFFK/SFZPJ/BCFS/BCGDLY）取参数/--attrs-json，未提供为 NULL",
    "套合仅做双方坐标系 WKID 相等校验，不做坐标转换；不一致须先投影",
    "套合面积按移交图层所在投影平面计算，未做椭球面积改算",
]


# ---------------------------------------------------------------------------
# 基础工具（纯函数）
# ---------------------------------------------------------------------------
def _norm_name(value: Any) -> str:
    return str(value or "").strip().upper()


def _upper_map(values: Mapping[str, Any]) -> dict:
    return {str(key).strip().upper(): value for key, value in dict(values or {}).items()}


def layer_spec(name: str, feature_count: int, shape_type: str, wkid: int,
               crs_name: str, fields: Sequence[Mapping[str, Any]]) -> dict:
    return {
        "name": name,
        "feature_count": int(feature_count),
        "shape_type": shape_type,
        "wkid": int(wkid or 0),
        "crs_name": crs_name,
        "fields": [
            {
                "name": str(field.get("name")),
                "type": str(field.get("type") or ""),
                "length": field.get("length"),
                "alias": field.get("alias"),
            }
            for field in fields
        ],
    }


def classify_layers(layer_infos: Sequence[Mapping[str, Any]]) -> dict:
    """按名称识别 DLTB 与鉴定图斑（仅面图层），返回候选与全部面图层清单。"""
    polygons = [
        dict(info) for info in layer_infos
        if str(info.get("shape_type", "")).strip().lower() == "polygon"
    ]
    dltb_candidates = [
        info["name"] for info in polygons
        if any(hint in str(info.get("name", "")) for hint in DLTB_HINTS)
    ]
    appendix_c_candidates = [
        info["name"] for info in polygons
        if any(hint in str(info.get("name", "")) for hint in APPENDIX_C_HINTS)
    ]
    ambiguous = (
        len(dltb_candidates) > 1 or len(appendix_c_candidates) > 1
    )
    return {
        "polygon_layers": [
            {
                "name": info["name"],
                "feature_count": info.get("feature_count"),
                "shape_type": info.get("shape_type"),
                "wkid": info.get("wkid"),
                "crs_name": info.get("crs_name"),
                "fields": info.get("fields"),
            }
            for info in polygons
        ],
        "dltb_candidates": dltb_candidates,
        "appendix_c_candidates": appendix_c_candidates,
        "dltb": dltb_candidates[0] if len(dltb_candidates) == 1 else None,
        "appendix_c": appendix_c_candidates[0] if len(appendix_c_candidates) == 1 else None,
        "ambiguous": ambiguous,
        "unrecognized_non_polygon": [
            info["name"] for info in layer_infos
            if str(info.get("shape_type", "")).strip().lower() != "polygon"
        ],
    }


def resolve_layers(layer_infos: Sequence[Mapping[str, Any]],
                   dltb: Optional[str] = None,
                   appendix_c: Optional[str] = None) -> dict:
    """确定参与接入的图层；识别不到或名称重复且未显式指定时抛错。"""
    classified = classify_layers(layer_infos)
    polygons = [item["name"] for item in classified["polygon_layers"]]
    chosen = {"dltb": None, "appendix_c": None, "explicit": {"dltb": False, "appendix_c": False}}
    if dltb:
        if dltb not in polygons:
            raise ValueError("--dltb 指定的图层不存在或不是面图层：{}；可用面图层：{}".format(
                dltb, "、".join(polygons) or "无"))
        chosen["dltb"] = dltb
        chosen["explicit"]["dltb"] = True
    else:
        candidates = classified["dltb_candidates"]
        if len(candidates) == 1:
            chosen["dltb"] = candidates[0]
        elif len(candidates) > 1:
            raise ValueError("移交 gdb 中可能有多个 DLTB 候选：{}；请用 --dltb 明确指定".format(
                "、".join(candidates)))
    if appendix_c:
        if appendix_c not in polygons:
            raise ValueError("--appendix-c 指定的图层不存在或不是面图层：{}；可用面图层：{}".format(
                appendix_c, "、".join(polygons) or "无"))
        chosen["appendix_c"] = appendix_c
        chosen["explicit"]["appendix_c"] = True
    else:
        candidates = classified["appendix_c_candidates"]
        if len(candidates) == 1:
            chosen["appendix_c"] = candidates[0]
        elif len(candidates) > 1:
            raise ValueError("移交 gdb 中可能有多个鉴定图斑候选：{}；请用 --appendix-c 明确指定".format(
                "、".join(candidates)))
    if chosen["dltb"] and chosen["dltb"] == chosen["appendix_c"]:
        raise ValueError("DLTB 与鉴定图斑不能是同一图层：{}".format(chosen["dltb"]))
    if not chosen["dltb"] and not chosen["appendix_c"]:
        raise ValueError(
            "未能从移交 gdb 识别 DLTB 或鉴定图斑；面图层：{}。请用 --dltb/--appendix-c 显式指定".format(
                "、".join(polygons) or "无"))
    chosen["classified"] = classified
    return chosen


def contract_diff(field_specs: Sequence[Mapping[str, Any]]) -> dict:
    """比对字段与附录C：29 字段名/类型/String 长度全对才算 compliant。

    数值型长度是 FileGDB 存储属性，与 qa._check_appendix_c 口径一致，只比 String 长度。
    """
    actual = {_norm_name(spec.get("name")): spec for spec in field_specs or []}
    missing = []
    mismatched = []
    for name, _alias, expected_type, expected_length, _required, _domain in APPENDIX_C:
        spec = actual.get(name)
        if spec is None:
            missing.append(name)
            continue
        actual_type = str(spec.get("type") or "")
        length_mismatch = (
            expected_type == "String" and spec.get("length") != expected_length
        )
        if actual_type.lower() != expected_type.lower() or length_mismatch:
            mismatched.append({
                "field": name,
                "expected": {"type": expected_type, "length": expected_length},
                "actual": {"type": actual_type, "length": spec.get("length")},
            })
    return {
        "compliant": not missing and not mismatched,
        "expected_count": len(APPENDIX_C),
        "actual_count": len(actual),
        "missing": missing,
        "mismatched": mismatched,
        "numeric_storage_note": "数值字段长度/小数位依附录C原表；FileGDB 存储长度不作不符判定",
    }


def decide_action(resolved: Mapping[str, Any],
                  layer_infos: Sequence[Mapping[str, Any]]) -> dict:
    """决定接入路径：copy（附录C原样复制）/ rebuild（按附录C重建）/ dltb（从 DLTB 生成）。"""
    appendix_c = resolved.get("appendix_c")
    dltb = resolved.get("dltb")
    if appendix_c:
        info = next(item for item in layer_infos if item.get("name") == appendix_c)
        diff = contract_diff(info.get("fields") or [])
        return {
            "action": "copy" if diff["compliant"] else "rebuild",
            "source_layer": appendix_c,
            "contract": diff,
        }
    if dltb:
        info = next(item for item in layer_infos if item.get("name") == dltb)
        return {
            "action": "dltb",
            "source_layer": dltb,
            "contract": contract_diff(info.get("fields") or []),
        }
    raise ValueError("未选择任何移交图层，无法决定接入路径")


def assert_same_crs(first_wkid: Any, second_wkid: Any,
                    first_label: str = "移交图层", second_label: str = "项目地块") -> None:
    """套合前强制校验坐标系一致；不做任何转换、不猜基准。"""
    try:
        first = int(first_wkid or 0)
        second = int(second_wkid or 0)
    except (TypeError, ValueError):
        raise ValueError("坐标系 WKID 无法解析：{}={!r}，{}={!r}".format(
            first_label, first_wkid, second_label, second_wkid))
    if first <= 0 or second <= 0:
        raise ValueError("坐标系未知（{}={}，{}={}），拒绝套合；请补齐坐标系资料".format(
            first_label, first, second_label, second))
    if first != second:
        raise ValueError(
            "坐标系不一致（{} WKID={}，{} WKID={}），请先投影统一后再套合；"
            "本工具不做坐标转换、不猜基准".format(first_label, first, second_label, second))


def assert_expected_wkid(wkid: Any, expected: Optional[int], label: str = "移交图层") -> None:
    if expected is None:
        return
    actual = int(wkid or 0)
    if actual != int(expected):
        raise ValueError("{}坐标系 WKID={} 与 --expect-wkid={} 不一致，拒绝写入".format(
            label, actual, expected))


def ensure_outputs_absent(paths: Iterable[Any]) -> None:
    existing = [str(path) for path in paths if Path(path).exists()]
    if existing:
        raise FileExistsError("输出已存在，不覆盖：{}".format("；".join(existing)))


def gdb_fingerprint(path: Any) -> dict:
    """目录级指纹：逐个文件 sha256 + 聚合哈希（用于运行前后比对，证明未改原件）。

    FileGDB 的 ``*.lock``（如 ``_gdb.MM-*.sr.lock``）是临时锁文件，可能被其他
    ArcGIS 会话占用或随会话变化，不参与指纹；不可读文件记入 unreadable_files。
    """
    root = Path(path)
    files = sorted(item for item in root.rglob("*") if item.is_file()) if root.exists() else []
    entries = []
    ignored_lock_files = []
    unreadable_files = []
    aggregate = hashlib.sha256()
    total = 0
    for item in files:
        relative = str(item.relative_to(root)).replace("\\", "/")
        if ".lock" in item.name.lower():
            ignored_lock_files.append(relative)
            continue
        try:
            payload = item.read_bytes()
        except OSError as exc:
            unreadable_files.append({"file": relative, "error": "{}: {}".format(
                type(exc).__name__, exc)})
            continue
        digest = hashlib.sha256(payload).hexdigest()
        size = len(payload)
        total += size
        entries.append({"file": relative, "size": size, "sha256": digest})
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return {
        "path": str(root),
        "exists": root.exists(),
        "file_count": len(entries),
        "total_bytes": total,
        "aggregate_sha256": aggregate.hexdigest(),
        "files": entries,
        "ignored_lock_files": ignored_lock_files,
        "unreadable_files": unreadable_files,
    }


def load_region_table(path: Any) -> dict:
    """读取行政区划名称表 CSV（乡镇代码→名称）；支持常见列名。"""
    import csv

    table_path = Path(path)
    if not table_path.exists():
        raise FileNotFoundError("区划名称表不存在：{}".format(table_path))
    with table_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("区划名称表为空或缺少表头：{}".format(table_path))
        names = [str(name).strip() for name in reader.fieldnames]
        code_key = next((name for name in names if "代码" in name or "编码" in name), None)
        name_key = next((name for name in names if "名称" in name), None)
        if code_key is None or name_key is None:
            raise ValueError("区划名称表须包含“代码”和“名称”列：{}".format(names))
        table = {}
        for row in reader:
            code = str(row.get(code_key) or "").strip()
            name = str(row.get(name_key) or "").strip()
            if code and name:
                table[code] = name
    return table


def lookup_region_name(region_table: Optional[Mapping[str, str]], code: Optional[str]) -> Optional[str]:
    """按 9 位乡镇代码查名称；兼容表中存 12 位/6 位代码（前缀匹配，取最短键）。"""
    if not region_table or is_blank(code):
        return None
    text = str(code).strip()
    if text in region_table:
        return region_table[text]
    for candidate in sorted(region_table, key=len):
        if candidate.startswith(text) or text.startswith(candidate):
            return region_table[candidate]
    return None


# ---------------------------------------------------------------------------
# 取值映射（纯函数）：DLTB→附录C、旧版/缺字段→附录C重建
# ---------------------------------------------------------------------------
# 同名附录C字段一律按字段名承接；下表只补 DLTB 常见异名列。
DLTB_ALIAS_MAP = {
    "DLBM": ("DLBM_CODE",),
    "DLMC": ("DLMC_NAME",),
    "ZLDWDM": ("ZLDW_CODE",),
    "ZLDWMC": ("ZLDW_NAME",),
    "TBMJ": ("TBMJ_M2", "TBMJ_AREA"),
    "TBDLMJ": ("TDLMJ",),
    "GDPDJB": ("PDJB",),
    "GXSJ": ("GXSJ_TIME", "GXTIME", "UPDATE_TIME"),
    "DKBH": ("DIKUAI_NO", "DK_NO"),
}
NUMERIC_FIELDS = ("TBMJ", "KCXS", "KCMJ", "TBDLMJ", "JZGDMJ")
# 参数与源层冲突时默认以源层为准，并把冲突写入报告（--attrs-json 同理）。
PARAM_FIELD_LABELS = {
    "XDM": "--county-code 参数",
    "XMC": "--county-name 参数",
    "XMMC": "--project-name 参数",
    "XMBH": "--project-no 参数",
}


def _pick(attrs_upper: Mapping[str, Any], names: Sequence[str]):
    for name in names:
        if name in attrs_upper and not is_blank(attrs_upper[name]):
            return attrs_upper[name]
    return None


def _coerce_value(name: str, value: Any):
    """按附录C字段类型转换；无法解析时返回 (None, 说明)。"""
    if is_blank(value):
        return None, None
    field_type = FIELD_BY_NAME[name]["type"]
    try:
        if field_type in ("Double", "Single"):
            return float(value), None
        if field_type == "Date":
            return parse_date(value), None
    except (TypeError, ValueError):
        return None, "源值 {!r} 无法解析为{}，置 NULL 待核".format(
            value, "数值" if field_type in ("Double", "Single") else "日期")
    return str(value).strip(), None


def _new_row(by_name: dict, name: str, value: Any, status: str, source: str,
             note: Optional[str] = None) -> None:
    meta = FIELD_BY_NAME[name]
    if isinstance(value, str):
        value = value.strip() or None
    if value is not None and is_placeholder(value):
        note = _join(note, "命中占位词“{}”，按无来源置 NULL".format(value))
        value, status = None, STATUS_MISSING
    if value is None and status != STATUS_MISSING:
        note = _join(note, "无可用来源，置 NULL")
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


def _join(*parts: Optional[str]) -> Optional[str]:
    joined = [str(part).strip() for part in parts if part and str(part).strip()]
    return "；".join(joined) if joined else None


def _values_equal(first: Any, second: Any) -> bool:
    """比较源层值与参数/提供值是否等价（数值按 float、日期按日期比较）。"""
    if first is None or second is None:
        return first is second
    try:
        return float(first) == float(second)
    except (TypeError, ValueError):
        pass
    try:
        return parse_date(first) == parse_date(second)
    except (TypeError, ValueError):
        pass
    return str(first).strip() == str(second).strip()


def _conflict(name: str, source_value: Any, ignored_value: Any,
              ignored_source: str) -> dict:
    meta = FIELD_BY_NAME[name]
    return {
        "field": name,
        "alias": meta["alias"],
        "source_value": None if source_value is None else str(source_value),
        "ignored_value": None if ignored_value is None else str(ignored_value),
        "ignored_source": ignored_source,
        "kept": "源层值",
        "rule": "源层已有值优先，参数/--attrs-json 不静默覆盖；冲突须经办确认",
    }


def _init_rows(by_name: dict, source_kind: str) -> None:
    for name in FIELD_ORDER:
        _new_row(by_name, name, None, STATUS_MISSING, source_kind,
                 "{}未提供该字段，按附录C重建时置 NULL 并入待补清单".format(source_kind))


def _carry_source(by_name: dict, attrs_upper: Mapping[str, Any],
                  source_label: str, source_values: dict) -> None:
    """按字段名承接源层全部 29 字段中已存在的值（不改值、不重算、不重编号）。"""
    for name in FIELD_ORDER:
        raw = attrs_upper.get(name)
        if is_blank(raw):
            continue
        _new_row(by_name, name, raw, STATUS_CONFIRMED,
                 source_label.format(name=name),
                 "按字段名承接原值，未重算/未重编号，须与变更调查核对")
        source_values[name] = by_name[name]["value"]


def _fill_missing(by_name: dict, options: BuildOptions,
                  region_table: Optional[Mapping[str, str]]) -> None:
    """源层为空的字段才用推导/参数补；不覆盖已有承接值。"""
    if by_name["XZDM"]["value"] is None and by_name["ZLDWDM"]["value"] is not None:
        _new_row(by_name, "XZDM", str(by_name["ZLDWDM"]["value"])[:9], STATUS_DERIVED,
                 "由 ZLDWDM 前9位推导", "须与乡镇区划代码核对")
    if by_name["XZMC"]["value"] is None:
        result = _derive_xzmc(by_name["XZDM"]["value"], region_table)
        if result["value"] is not None:
            _new_row(by_name, "XZMC", result["value"], result["status"],
                     result["source"], result["note"])
    if by_name["XDM"]["value"] is None:
        result = _derive_xdm({}, options, by_name["XMBH"]["value"])
        if result["value"] is not None:
            _new_row(by_name, "XDM", result["value"], result["status"],
                     result["source"], result["note"])
    county_name = None if is_blank(options.county_name) else str(options.county_name).strip()
    if by_name["XMC"]["value"] is None and county_name:
        _new_row(by_name, "XMC", county_name, STATUS_CONFIRMED, "--county-name 参数",
                 "源层未提供 XMC，按参数采用；须核对")
    project_no = None if is_blank(options.project_no) else str(options.project_no).strip()
    if by_name["XMBH"]["value"] is None and project_no:
        _new_row(by_name, "XMBH", project_no, STATUS_CONFIRMED, "--project-no 参数",
                 "源层未提供项目编号，按参数采用；须核对")
    project_name = None if is_blank(options.project_name) else str(options.project_name).strip()
    if by_name["XMMC"]["value"] is None and project_name:
        _new_row(by_name, "XMMC", project_name, STATUS_ASSUMED, "--project-name 参数",
                 "源层未提供项目名称，按参数业务推定；须经办确认")


def _finish(by_name: Mapping[str, dict], provided: Mapping[str, Any],
            source_values: Mapping[str, Any],
            param_values: Sequence[Sequence[Any]]) -> list:
    """源层已有值优先：参数/--attrs-json 只填空白；冲突记录到 row['conflict_detail']。"""
    provided_upper = {
        _norm_name(key): value for key, value in dict(provided or {}).items()
        if _norm_name(key) in FIELD_BY_NAME
    }
    conflicts = []
    for name, value in provided_upper.items():
        if is_blank(value):
            continue
        target = by_name[name]
        text = str(value).strip()
        if is_placeholder(text):
            if target["value"] is None:
                target.update({
                    "value": None, "status": STATUS_MISSING,
                    "source": "用户提供（--attrs-json）",
                    "note": _join(target.get("note"),
                                  "经办提供值命中占位词“{}”，按无来源置 NULL".format(text)),
                })
            else:
                conflict = _conflict(name, target["value"], text, "用户提供（--attrs-json）")
                conflicts.append(conflict)
                target["note"] = _join(
                    target.get("note"),
                    "与用户提供占位值“{}”冲突，已保留源值".format(text))
            continue
        converted, problem = _coerce_value(name, text)
        if target["value"] is not None:
            if not _values_equal(target["value"], converted):
                conflict = _conflict(name, target["value"], text, "用户提供（--attrs-json）")
                conflicts.append(conflict)
                target["note"] = _join(
                    target.get("note"),
                    "与用户提供值“{}”冲突，已保留源层值".format(text))
            continue
        target.update({
            "value": converted,
            "status": STATUS_CONFIRMED if converted is not None else STATUS_MISSING,
            "source": "用户提供（--attrs-json）",
            "note": _join(target.get("note"), "经办显式提供值，未经自动推导，须核对",
                          problem),
        })
    for name, value, label in param_values:
        if is_blank(value):
            continue
        source_value = source_values.get(name)
        if source_value is None:
            continue  # 源层为空的字段已由 _fill_missing 用参数填充
        if _values_equal(source_value, value):
            continue
        conflict = _conflict(name, source_value, value, label)
        conflicts.append(conflict)
        by_name[name]["note"] = _join(
            by_name[name].get("note"),
            "与{}值“{}”冲突，已保留源层值".format(label, value))
    for conflict in conflicts:
        by_name[conflict["field"]]["conflict_detail"] = conflict

    ordered = []
    for name in FIELD_ORDER:
        row = by_name[name]
        converted, problem = _coerce_value(name, row["value"])
        if problem:
            row["note"] = _join(row.get("note"), problem)
            row["value"] = None
        else:
            row["value"] = converted
        if row["value"] is None and row["status"] != STATUS_MISSING:
            row["status"] = STATUS_MISSING
            row["note"] = _join(row.get("note"), "无可用来源，置 NULL")
        ordered.append(row)
    return ordered


def collect_conflicts(rows_by_feature: Sequence[Sequence[Mapping[str, Any]]]) -> list:
    """汇总各图斑行上的参数/提供值与源层值冲突（源层值优先，未覆盖）。"""
    conflicts = []
    seen = set()
    for rows in rows_by_feature:
        for row in rows:
            detail = row.get("conflict_detail")
            if not detail:
                continue
            key = (detail["field"], detail["source_value"], detail["ignored_value"])
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(detail)
    return conflicts


def _param_values(options: BuildOptions) -> list:
    return [
        ("XDM", options.county_code, PARAM_FIELD_LABELS["XDM"]),
        ("XMC", options.county_name, PARAM_FIELD_LABELS["XMC"]),
        ("XMMC", options.project_name, PARAM_FIELD_LABELS["XMMC"]),
        ("XMBH", options.project_no, PARAM_FIELD_LABELS["XMBH"]),
    ]


def _as_options(options: Optional[Any]) -> BuildOptions:
    if options is None:
        return BuildOptions()
    if isinstance(options, BuildOptions):
        return options
    if isinstance(options, Mapping):
        return BuildOptions(
            county_code=options.get("county_code"),
            county_name=options.get("county_name"),
            project_name=options.get("project_name"),
            project_no=options.get("project_no"),
        )
    raise TypeError("options 必须是 BuildOptions 或映射：{!r}".format(options))


def _derive_xdm(attrs_upper: Mapping[str, Any], options: BuildOptions,
                project_no: Any = None) -> dict:
    county_code = str(options.county_code or "").strip()
    if county_code:
        return {"value": county_code, "status": STATUS_CONFIRMED,
                "source": "--county-code 参数", "note": "须与变更调查县级行政区代码核对"}
    project_no = project_no if not is_blank(project_no) else _pick(
        attrs_upper, ("XMBH", "XMBH_CODE", "PROJECT_NO"))
    text = None if is_blank(project_no) else str(project_no).strip()
    if text and len(text) == 15:
        return {"value": text[1:7], "status": STATUS_DERIVED,
                "source": "由 XMBH 第2–7位推导", "note": "须与变更调查县级行政区代码核对"}
    return {"value": None, "status": STATUS_MISSING, "source": "无",
            "note": "未提供 --county-code，且无 15 位项目编号可推导县级行政区代码"}


def _derive_xzmc(xzdm: Any, region_table: Optional[Mapping[str, str]]) -> dict:
    name = lookup_region_name(region_table, None if is_blank(xzdm) else str(xzdm).strip())
    if name:
        return {"value": name, "status": STATUS_DERIVED,
                "source": "行政区划名称表（--region-table）",
                "note": "按 XZDM 9位代码匹配，须与乡镇区划资料核对"}
    return {"value": None, "status": STATUS_MISSING, "source": "无区划名称表",
            "note": "XZMC 需行政区划名称表（--region-table）或乡镇资料；未提供为 NULL"}


def map_dltb_rows(attrs: Mapping[str, Any],
                  options: Optional[BuildOptions] = None,
                  provided: Optional[Mapping[str, Any]] = None,
                  region_table: Optional[Mapping[str, str]] = None) -> list:
    """从 DLTB 属性映射生成附录C 29 字段底稿（逐字段记状态，缺即 NULL）。

    顺序：先按字段名承接源 DLTB 已有值（状态 CONFIRMED、来源记源 DLTB 字段），
    再用 DLTB 异名列补空缺，最后才用推导/参数；源层已有值不被参数覆盖，
    冲突由 collect_conflicts() 汇总进报告。
    """
    options = _as_options(options)
    attrs_upper = _upper_map(attrs)
    by_name: dict = {}
    source_values: dict = {}
    _init_rows(by_name, "源DLTB")
    _carry_source(by_name, attrs_upper, "源DLTB字段承接（{name}）", source_values)
    for name, aliases in DLTB_ALIAS_MAP.items():
        if by_name[name]["value"] is not None:
            continue
        value = _pick(attrs_upper, aliases)
        if value is None:
            continue
        _new_row(by_name, name, value, STATUS_CONFIRMED,
                 "源DLTB别名字段 {}".format(aliases[0]),
                 "承接自 DLTB 别名字段，须与变更调查核对")
        source_values[name] = by_name[name]["value"]
    _fill_missing(by_name, options, region_table)
    return _finish(by_name, provided or {}, source_values, _param_values(options))


def map_appendix_c_rows(attrs: Mapping[str, Any],
                        options: Optional[BuildOptions] = None,
                        provided: Optional[Mapping[str, Any]] = None,
                        region_table: Optional[Mapping[str, str]] = None) -> list:
    """旧版/缺字段鉴定图斑按附录C重建：先承接源层已有值，缺的留 NULL。

    凡源图层存在同名附录C字段即按字段名承接（含 XMC/XMMC/XMBH 等），
    只有源层为空的字段才用推导/参数/--attrs-json 补；绝不重算、不重编号、不补造。
    """
    options = _as_options(options)
    attrs_upper = _upper_map(attrs)
    by_name: dict = {}
    source_values: dict = {}
    _init_rows(by_name, "源层")
    _carry_source(by_name, attrs_upper, "源层字段承接（{name}）", source_values)
    _fill_missing(by_name, options, region_table)
    return _finish(by_name, provided or {}, source_values, _param_values(options))


# ---------------------------------------------------------------------------
# 图斑-地块套合（纯函数）
# ---------------------------------------------------------------------------
def extents_intersect(first: Optional[Sequence[float]],
                      second: Optional[Sequence[float]]) -> bool:
    """包围盒是否相交；用于套合前粗筛。extent=(xmin,ymin,xmax,ymax)。"""
    if not first or not second:
        return True
    return not (
        first[2] < second[0] or second[2] < first[0]
        or first[3] < second[1] or second[3] < first[1]
    )


def classify_matches(parcel_meta: Mapping[Any, Mapping[str, Any]],
                     patch_meta: Mapping[Any, Mapping[str, Any]],
                     overlaps: Iterable[Sequence[Any]],
                     tolerance: float = MATCH_TOLERANCE_M2) -> dict:
    """按重叠面积判定 1:1 / 1:N / N:1 / 无匹配 / 跨界，并列出无匹配与跨界。

    ``overlaps`` 元素为 (地块键, 图斑键, 重叠面积m²)。判定优先级：
    图斑跨多地块→1:N；地块跨多图斑→N:1；单向部分覆盖→跨界；否则 1:1。
    同时多对多（M:N）按 1:N 归类并在汇总中提示需人工复核。
    """
    parcel_patches: dict = {}
    patch_parcels: dict = {}
    pair_areas: dict = {}
    for parcel_key, patch_key, area in overlaps:
        if is_blank(area):
            continue
        value = float(area)
        if value <= tolerance:
            continue
        parcel_patches.setdefault(parcel_key, []).append(patch_key)
        patch_parcels.setdefault(patch_key, []).append(parcel_key)
        pair_areas[(parcel_key, patch_key)] = value

    rows = []
    both_multi = 0
    for (parcel_key, patch_key), area in sorted(pair_areas.items(),
                                                 key=lambda item: (str(item[0][0]), str(item[0][1]))):
        parcel = parcel_meta.get(parcel_key) or {}
        patch = patch_meta.get(patch_key) or {}
        parcel_area = float(parcel.get("area") or 0.0)
        patch_area = float(patch.get("area") or 0.0)
        ratio_parcel = (area / parcel_area) if parcel_area > 0 else None
        ratio_patch = (area / patch_area) if patch_area > 0 else None
        if len(patch_parcels[patch_key]) > 1:
            match_type = "1:N"
            if len(parcel_patches[parcel_key]) > 1:
                both_multi += 1
        elif len(parcel_patches[parcel_key]) > 1:
            match_type = "N:1"
        elif (ratio_patch is not None and ratio_patch < 0.999) or (
                ratio_parcel is not None and ratio_parcel < 0.999):
            match_type = "跨界"
        else:
            match_type = "1:1"
        rows.append({
            "DKBH": parcel.get("DKBH") or "",
            "特征OID": patch.get("OID"),
            "鉴定图斑BSM": patch.get("BSM") or "",
            "重叠面积_m2": round(area, 4),
            "占地块面积比": None if ratio_parcel is None else round(ratio_parcel, 4),
            "占图斑面积比": None if ratio_patch is None else round(ratio_patch, 4),
            "匹配类型": match_type,
        })

    unmatched_parcels = [
        {
            "DKBH": (parcel_meta.get(key) or {}).get("DKBH") or "",
            "特征OID": "",
            "鉴定图斑BSM": "",
            "重叠面积_m2": 0,
            "占地块面积比": None,
            "占图斑面积比": None,
            "匹配类型": "无匹配",
        }
        for key in parcel_meta if key not in parcel_patches
    ]
    unmatched_patches = [
        {
            "DKBH": "",
            "特征OID": (patch_meta.get(key) or {}).get("OID"),
            "鉴定图斑BSM": (patch_meta.get(key) or {}).get("BSM") or "",
            "重叠面积_m2": 0,
            "占地块面积比": None,
            "占图斑面积比": None,
            "匹配类型": "无匹配",
        }
        for key in patch_meta if key not in patch_parcels
    ]
    all_rows = rows + unmatched_parcels + unmatched_patches
    summary = {match_type: 0 for match_type in MATCH_TYPES}
    for row in all_rows:
        summary[row["匹配类型"]] = summary.get(row["匹配类型"], 0) + 1
    return {
        "rows": all_rows,
        "summary": {
            "row_count": len(all_rows),
            "by_type": summary,
            "parcel_count": len(parcel_meta),
            "patch_count": len(patch_meta),
            "matched_parcel_count": len(parcel_patches),
            "matched_patch_count": len(patch_parcels),
            "both_multi_count": both_multi,
        },
        "unmatched_parcels": unmatched_parcels,
        "unmatched_patches": unmatched_patches,
        "cross_boundary": [row for row in all_rows if row["匹配类型"] == "跨界"],
        "note": "M:N（图斑与地块均多对多）按 1:N 归类，须人工复核；无匹配/跨界逐条列于本表",
    }


# ---------------------------------------------------------------------------
# FileGDB 侧函数（仅 ArcGIS Pro Python）
# ---------------------------------------------------------------------------
def scan_transfer_gdb(transfer_gdb: Any) -> dict:
    """只读扫描移交 gdb：列出全部要素类的名称/要素数/几何类型/CRS/字段。"""
    import arcpy

    gdb = Path(transfer_gdb).expanduser().resolve()
    if not arcpy.Exists(str(gdb)):
        raise FileNotFoundError("移交 gdb 不存在：{}".format(gdb))
    previous = arcpy.env.workspace
    arcpy.env.workspace = str(gdb)
    try:
        names = sorted(arcpy.ListFeatureClasses() or [])
        layers = []
        for name in names:
            desc = arcpy.Describe(name)
            reference = getattr(desc, "spatialReference", None)
            layers.append(layer_spec(
                name=name,
                feature_count=int(arcpy.management.GetCount(name)[0]),
                shape_type=str(getattr(desc, "shapeType", "")),
                wkid=int(getattr(reference, "factoryCode", 0) or 0),
                crs_name=str(getattr(reference, "name", "Unknown")),
                fields=[
                    {"name": field.name, "type": field.type,
                     "length": getattr(field, "length", None),
                     "alias": getattr(field, "aliasName", None)}
                    for field in arcpy.ListFields(name)
                ],
            ))
            layers[-1]["path"] = str(gdb / name)
        return {"gdb": str(gdb), "layers": layers}
    finally:
        arcpy.env.workspace = previous


def layer_area_sum(arcpy_module: Any, dataset: Any) -> float:
    total = 0.0
    with arcpy_module.da.SearchCursor(dataset, ["SHAPE@"]) as cursor:
        for (geometry,) in cursor:
            if geometry is not None:
                total += float(geometry.area)
    return round(total, 4)


def attribute_digest(arcpy_module: Any, dataset: Any,
                     fields: Sequence[str] = FIELD_ORDER) -> str:
    """对指定字段按 OID 顺序做取值摘要（用于证明复制未改值；不含 OID 本身）。"""
    names = [name for name in fields]
    existing = {field.name.upper() for field in arcpy_module.ListFields(dataset)}
    names = [name for name in names if name.upper() in existing]
    digest = hashlib.sha256()
    with arcpy_module.da.SearchCursor(dataset, names) as cursor:
        for row in cursor:
            payload = "\0".join("" if value is None else str(value) for value in row)
            digest.update(payload.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def copy_layer_unchanged(out_gdb: Any, source_fc: Any,
                         layer_name: str = LAYER_NAME) -> dict:
    """附录I 注2：原样复制鉴定图斑层，不改字段/改值/重编号。"""
    import arcpy

    out_gdb = Path(out_gdb).expanduser().resolve()
    if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
        raise FileExistsError("输出 gdb 已存在，不覆盖：{}".format(out_gdb))
    source_desc = arcpy.Describe(str(source_fc))
    wkid = int(getattr(source_desc.spatialReference, "factoryCode", 0) or 0)
    count_before = int(arcpy.management.GetCount(str(source_fc))[0])
    area_before = layer_area_sum(arcpy, str(source_fc))
    digest_before = attribute_digest(arcpy, str(source_fc))
    created = False
    try:
        out_gdb.parent.mkdir(parents=True, exist_ok=True)
        arcpy.management.CreateFileGDB(str(out_gdb.parent), out_gdb.name, "CURRENT")
        created = True
        feature_class = str(out_gdb / layer_name)
        arcpy.management.CopyFeatures(str(source_fc), feature_class)
        count_after = int(arcpy.management.GetCount(feature_class)[0])
        area_after = layer_area_sum(arcpy, feature_class)
        digest_after = attribute_digest(arcpy, feature_class)
        return {
            "geodatabase": str(out_gdb),
            "feature_class": feature_class,
            "layer_name": layer_name,
            "wkid": wkid,
            "crs_name": str(getattr(source_desc.spatialReference, "name", "Unknown")),
            "count_before": count_before,
            "count_after": count_after,
            "area_sum_before": area_before,
            "area_sum_after": area_after,
            "attribute_digest_before": digest_before,
            "attribute_digest_after": digest_after,
            "unchanged": (
                count_before == count_after
                and abs(area_before - area_after) <= 0.01
                and digest_before == digest_after
            ),
            "schema": compare_schema(list(arcpy.ListFields(feature_class))),
            "copy_rule": "附录I 注2：原样复制，不改字段/不改值/不重编号",
        }
    except Exception:
        if created:
            try:
                if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
                    arcpy.management.Delete(str(out_gdb))
            except Exception:
                pass
        raise


def rebuild_appendix_c(out_gdb: Any, source_fc: Any,
                       feature_values: Sequence[Mapping[str, Any]],
                       layer_name: str = LAYER_NAME,
                       expect_wkid: Optional[int] = None) -> dict:
    """按附录C重建图层（复用 appendix_c.build_layer），只写入承接/推导/显式值。"""
    return build_layer(out_gdb, source_fc, feature_values,
                       layer_name=layer_name, expect_wkid=expect_wkid)


def compute_overlaps(parcel_fc: Any, patch_fc: Any, id_field: str,
                     bsm_field: str = "BSM") -> dict:
    """嵌套计算项目地块与鉴定图斑的相交面积，返回 classify_matches 结果。"""
    import arcpy

    parcel_meta = {}
    parcel_geometries = {}
    with arcpy.da.SearchCursor(str(parcel_fc), ["OID@", id_field, "SHAPE@"]) as cursor:
        for oid, dkbh, geometry in cursor:
            parcel_meta[oid] = {
                "DKBH": "" if dkbh is None else str(dkbh).strip(),
                "area": 0.0 if geometry is None else float(geometry.area),
            }
            parcel_geometries[oid] = geometry
    patch_fields = {field.name.upper() for field in arcpy.ListFields(str(patch_fc))}
    read_fields = ["OID@"] + ([bsm_field] if bsm_field.upper() in patch_fields else []) + ["SHAPE@"]
    patch_meta = {}
    patch_geometries = {}
    with arcpy.da.SearchCursor(str(patch_fc), read_fields) as cursor:
        for row in cursor:
            oid = row[0]
            bsm = "" if len(read_fields) == 2 or row[1] is None else str(row[1]).strip()
            geometry = row[-1]
            patch_meta[oid] = {
                "OID": oid,
                "BSM": bsm,
                "area": 0.0 if geometry is None else float(geometry.area),
            }
            patch_geometries[oid] = geometry
    overlaps = []
    for parcel_key, parcel_geometry in parcel_geometries.items():
        if parcel_geometry is None:
            continue
        parcel_extent = _extent_tuple(parcel_geometry)
        for patch_key, patch_geometry in patch_geometries.items():
            if patch_geometry is None:
                continue
            if not extents_intersect(parcel_extent, _extent_tuple(patch_geometry)):
                continue
            try:
                intersects = parcel_geometry.intersect(patch_geometry, 4)
            except Exception:
                continue
            if intersects is None:
                continue
            area = float(intersects.area)
            if area > MATCH_TOLERANCE_M2:
                overlaps.append((parcel_key, patch_key, area))
    result = classify_matches(parcel_meta, patch_meta, overlaps)
    result["overlap_pair_count"] = len(overlaps)
    return result


def _extent_tuple(geometry: Any) -> Optional[tuple]:
    extent = getattr(geometry, "extent", None)
    if extent is None:
        return None
    try:
        return (float(extent.XMin), float(extent.YMin),
                float(extent.XMax), float(extent.YMax))
    except Exception:
        return None


def qa_appendix_c(feature_class: Any) -> dict:
    """对产出层调用 qa._check_appendix_c，返回质检明细（含行级/字段级口径）。"""
    from .qa import _check_appendix_c

    qa_result = _check_appendix_c(str(feature_class))
    detail = qa_result.get("detail") or {}
    values = detail.get("values") or {}
    empty_required = values.get("empty_required") or []
    missing_fields = [name for name in FIELD_ORDER
                      if name in {str(item.get("field")) for item in empty_required}]
    return {
        "generated_by": "scripts/ingest_appendix_c_transfer.py",
        "qa_status": qa_result.get("status"),
        "qa_summary": {
            "fields_status": (detail.get("fields") or {}).get("status"),
            "values_status": values.get("status"),
            "geometry_status": (detail.get("geometry_review") or {}).get("status"),
        },
        "missing_required_rows": len(empty_required),
        "missing_required_rows_note": (
            "qa 口径：缺失必填项按“要素行×字段”计为 {} 行（字段级去重后 {} 个字段：{}）；"
            "待补清单为字段级口径，二者口径不同。".format(
                len(empty_required), len(missing_fields), "、".join(missing_fields) or "无")
        ),
        "missing_required_fields": missing_fields,
        "invalid_domain": values.get("invalid_domain") or [],
        "duplicate_BSM": values.get("duplicate_BSM") or [],
        "geometry": {
            "status": (detail.get("geometry_review") or {}).get("status"),
            "multipart_count": (detail.get("geometry_review") or {}).get("multipart_count"),
            "multipart_rule": (detail.get("geometry_review") or {}).get("multipart_rule"),
            "hole_ring_review": (detail.get("geometry_review") or {}).get("hole_ring_review"),
        },
        "qa_result": qa_result,
    }
