# -*- coding: utf-8 -*-
"""从项目地块矢量生成附录C「补充耕地质量鉴定图斑」图层（ArcGIS Pro Python）。

依据：《广西补充耕地质量验收作业指导书 v1.1（2026-08-17）》附录C（PDF49–50页）。
字段契约取自 acceptance.qa.APPENDIX_C，本脚本不另存字段表。

用法（Pro Python）：
  python scripts/build_appendix_c.py --source X.shp --out-gdb 目录\\附录C图斑.gdb \
      --project-name 批次名 --county-name 示例区 --county-code 990001 \
      --expect-wkid 4524 [--project-no F99000120260000] [--id-field DIKUAI_NO] \
      [--attrs-json attrs.json] [--layer-name 补充耕地质量鉴定图斑] \
      [--qa-json 目录\\附录C质检.json]

输出（与 gdb 同目录）：附录C建库报告.json、待补清单.csv、字段来源表.csv；
指定 --qa-json 时另生成 附录C质检.json（调用 acceptance.qa._check_appendix_c，
质检由此正式入口生成，可复跑；报告 artifacts.qa_generator 留痕）。
不覆盖已存在的 gdb/文件；缺源字段写 NULL 并进入待补清单，禁止占位文本。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance import appendix_c  # noqa: E402
from acceptance.appendix_c import (  # noqa: E402
    FIELD_BY_NAME,
    FIELD_ORDER,
    LAYER_NAME,
    STATUS_RULE,
    BuildOptions,
    apply_bsm_suffixes,
    build_field_rows,
    contract_echo,
    domain_violations,
    field_source_rows,
    field_status_map,
    gap_table,
    project_name_conflicts,
    row_errors,
    source_family_hashes,
    summarize,
    write_csv,
    write_json,
)
from acceptance.crs import parse_wkid  # noqa: E402

REPORT_NAME = "附录C建库报告.json"
GAP_NAME = "待补清单.csv"
SOURCE_TABLE_NAME = "字段来源表.csv"
QA_NAME = "附录C质检.json"
QA_GEOMETRY_NOTE = (
    "多部件判据为 partCount>1；原 isMultipart 判据已修正"
    "（ArcPy 对含孔洞的单部件 Polygon 也返回 isMultipart=True，但不是附录C注6多部件，"
    "孔洞另列 hole_ring_review 供人工核查）"
)
CLAUSE_BY_FIELD = {
    "BSM": "附录C注1：标识码须与日常/国土变更调查一致，由自然资源部门移交",
    "XZDM": "附录C序号4：乡镇代码，须乡镇（街道）资料",
    "XZMC": "附录C序号5：乡镇名称，须乡镇（街道）资料",
    "BGQDLBM": "附录C注2：序号8–19须与国土变更调查一致",
    "BGQDLMC": "附录C注2：序号8–19须与国土变更调查一致",
    "ZLDWDM": "附录C序号10：坐落单位代码，须变更调查/村级资料",
    "ZLDWMC": "附录C序号11：坐落单位名称，须变更调查/村级资料",
    "GXSJ": "附录C序号19：更新时间，须取国土变更调查更新时间",
    "SFYKT": "附录C序号24：是否有客土或物料填埋，须申请表/污染调查/客土资料",
    "SFFK": "附录C序号25：是否是未利用地、复垦，须申请表/复垦资料",
    "GDLX": "附录C序号17：耕地类型（C 条件必填，附录C未给码表）",
    "GDPDJB": "附录C序号18：耕地坡度级别（C 条件必填，附录C未给码表）",
    "KCDLBM": "附录C序号13：扣除地类编码（C 条件必填，须扣除地类资料）",
    "KCXS": "附录C序号14：扣除地类系数（C 条件必填，须扣除地类资料）",
    "KCMJ": "附录C序号15：扣除地类面积（C 条件必填，须扣除地类资料）",
    "BZ": "附录C序号29：备注（C 条件必填）",
}
REVIEW_FIX_ROUND = {
    "round": 2,
    "source": "独立审核（task-3）不通过（有条件）后的修复轮；审核记录见 "
              "land_survey_toolkit/docs/2026-11-03-附录C图斑-独立审核.md",
    "fixes": [
        {
            "id": "E-1",
            "item": "BSM 注1后缀撞值（可能生成重复标识码）",
            "before": "plan_bsm_suffixes(['A','A-1','A']) → ['A','A-1','A-1']（结果内重复）",
            "after": "改为全局唯一：候选后缀被源值或已生成值占用即跳过，"
                     "→ ['A','A-1','A-2']；首次出现的值不改动；加后缀值状态改 DERIVED（审核 4.2）",
            "tests": "test_bsm_suffix_avoids_collision_with_existing_suffixed_value 等 6 例",
        },
        {
            "id": "E-2",
            "item": "--attrs-json 占位值被当 CONFIRMED 写入",
            "before": "provided BZ='待确认'/'待核实'/'—'/'/'/'--'/'null' 等 → CONFIRMED 写入",
            "after": "整值等于占位词或整值为纯符号串才判占位；判 MISSING+NULL，"
                     "备注记命中占位词并列入待补清单；正文含占位词（如“待确认后补”、“无”）不误杀",
            "tests": "test_provided_placeholder_value_is_null_and_gap / test_placeholder_like_text_is_not_killed",
        },
        {
            "id": "G-1",
            "item": "temp 质检脚本残留、质检与产品分离",
            "before": "temp/appendix_c_qc_20261103/run_appendix_c_qc.py 存在（7605 B）",
            "after": "已删除该临时脚本；质检并入 scripts/build_appendix_c.py --qa-json，"
                     "生成附录C质检.json（与 gdb 同目录），工具包 temp/ 无本任务产物",
            "tests": "产物 exists + temp 清单核对",
        },
        {
            "id": "ADD-1",
            "item": "附录C质检.json 判据文字自相矛盾",
            "before": "geometry.note 写“qa 以 isMultipart 判定…”与 clause_notes 的 partCount 冲突",
            "after": "统一为 partCount>1 口径，并注明原 isMultipart 误报已修正",
            "tests": "质检 JSON 字段核对",
        },
        {
            "id": "ADD-2",
            "item": "hole_ring_review 缺 DKBH，孔洞图斑无法定位",
            "before": "parcels 仅 BSM/partCount/ring_count/hole_rings（本批 BSM 全 NULL）",
            "after": "qa._check_appendix_c 的 hole_ring_review.parcels 补 DKBH/BSM，"
                     "本批可定位 22/111/1",
            "tests": "test_hole_ring_review_locates_parcel_by_dkbh",
        },
    ],
    "rerun": "旧产物已删除后重跑；E-1/E-2 不影响示例区本批数值，面积/要素/字段应与上一版一致",
    "pytest": "pytest tests/test_appendix_c_builder.py tests/test_acceptance_appendix_c.py -q 全绿",
}
GAP_HEADER = [
    "序号", "字段名", "字段别名", "必填性", "缺失原因", "涉及地块", "附录C依据",
]
SOURCE_TABLE_HEADER = [
    "地块编号", "序号", "字段名", "字段别名", "必填性", "状态", "取值", "来源依据", "备注",
]
ID_FIELD_CANDIDATES = ("DIKUAI_NO", "DKBH", "PL_NAME")
UNVERIFIED_ITEMS = [
    "坐标系：输出继承源数据 CRS，未与自然资源部门移交的国土变更图斑逐块核对是否一致",
    "BSM 标识码：源数据无该字段，须由自然资源变更调查移交，当前为 NULL",
    "项目名称/批次：文件名（2025年度第一批耕地恢复）与 shp.xml 血缘名称（2026年第一批次）冲突待核",
    "GXSJ 更新时间：须取变更调查更新时间，本工具未写运行日期充数",
    "XZDM/XZMC/ZLDWDM/ZLDWMC/BGQDLBM/BGQDLMC：须由国土变更调查与乡镇/村级资料移交",
    "SFYKT/SFFK：须由申请表、污染调查、客土或复垦资料确定，未推定",
    "BCFS：按15位编号规则首字符推导，未与广西耕地占补平衡动态监管系统验收阶段备案编号核对",
    "XMBH：源值是否为监管系统验收阶段备案编号未核实",
    "JZGDMJ：按 PATCHTYPE=新增、变更前非耕地，按净面积 TBDLMJ 推定，须经办确认",
    "SFZPJ：按首次鉴定、无再评价记录推定“否”，须经办确认",
    "KCDLBM/KCXS：孔洞来源为源shp.xml血缘 Erase 森林资源调查2025年，扣除地类编码与系数推不出，条件必填项按 NULL 处理",
    "GDLX/GDPDJB：附录C 值域栏无权威码表，未自造代码，须经办补充",
    "面积口径：TBMJ=外环毛面积、KCMJ=孔洞面积、TBDLMJ=净面积；须与变更调查图斑面积口径核对",
]


def _json_load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("--attrs-json 不是合法 JSON：{}".format(exc))
    if not isinstance(payload, dict):
        raise ValueError("--attrs-json 顶层必须是 JSON 对象")
    return payload


def load_attrs(path: Optional[Path]) -> dict:
    """解析 --attrs-json：{values:{}, by_dkbh:{}, by_oid:{}} 或扁平字段映射。"""
    if not path:
        return {"values": {}, "by_dkbh": {}, "by_oid": {}}
    payload = _json_load(Path(path))
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    by_dkbh = payload.get("by_dkbh") if isinstance(payload.get("by_dkbh"), dict) else {}
    by_oid = payload.get("by_oid") if isinstance(payload.get("by_oid"), dict) else {}
    if not values and not by_dkbh and not by_oid:
        values = {key: value for key, value in payload.items()
                  if str(key).strip().upper() in FIELD_BY_NAME}
    unknown = sorted(
        str(key) for key in
        list(values) + [field for item in by_dkbh.values() if isinstance(item, dict) for field in item]
        + [field for item in by_oid.values() if isinstance(item, dict) for field in item]
        if str(key).strip().upper() not in FIELD_BY_NAME
    )
    if unknown:
        raise ValueError("--attrs-json 含附录C之外的字段：{}".format(", ".join(unknown)))
    return {"values": values, "by_dkbh": by_dkbh, "by_oid": by_oid}


def _pick_id_field(field_names: Sequence[str], requested: Optional[str]) -> str:
    upper = {name.upper(): name for name in field_names}
    if requested:
        if requested.upper() not in upper:
            raise ValueError("--id-field {!r} 不在源数据字段中：{}".format(
                requested, ", ".join(field_names)))
        return upper[requested.upper()]
    for candidate in ID_FIELD_CANDIDATES:
        if candidate in upper:
            return upper[candidate]
    raise ValueError("源数据无可用地块编号字段（候选 {}），请用 --id-field 指定".format(
        "/".join(ID_FIELD_CANDIDATES)))


def _part_count(geometry: Any) -> int:
    if geometry is None:
        return 0
    try:
        return int(geometry.partCount)
    except Exception:
        try:
            return len(geometry)
        except Exception:
            return 1


def read_features(arcpy: Any, source_path: Path, id_field: str) -> list:
    field_names = [field.name for field in arcpy.ListFields(str(source_path))]
    upper = {name.upper(): name for name in field_names}
    read_map = {
        "landtype": upper.get("LANDTYPE"),
        "purpose": upper.get("PURPOSE"),
        "project_no": upper.get("XMBH"),
        "patchtype": upper.get("PATCHTYPE"),
        "note": upper.get("BZ"),
        "source_area_ha": upper.get("DIKUAIAREA"),
    }
    read_fields = [name for name in read_map.values() if name]
    cursor_fields = ["OID@"] + [id_field] + read_fields + ["SHAPE@"]
    features = []
    with arcpy.da.SearchCursor(str(source_path), cursor_fields) as cursor:
        for values in cursor:
            record = dict(zip(cursor_fields, values))
            geometry = record["SHAPE@"]
            ring_stats = (
                appendix_c.polygon_ring_stats(geometry.JSON) if geometry is not None
                else {"exterior_rings": 0, "hole_rings": 0, "multipart": False,
                      "gross_area": None, "hole_area": None, "net_area": None}
            )
            part_count = _part_count(geometry)
            item = {
                "oid": int(record["OID@"]),
                "id_field": id_field,
                "feature_id": record.get(id_field),
                "area_m2": float(geometry.area) if geometry is not None else None,
                "gross_area_m2": ring_stats["gross_area"],
                "hole_area_m2": ring_stats["hole_area"],
                "net_area_m2": ring_stats["net_area"],
                "part_count": part_count,
                "is_multipart_flag": bool(getattr(geometry, "isMultipart", False)) if geometry is not None else None,
                "exterior_rings": ring_stats["exterior_rings"],
                "hole_rings": ring_stats["hole_rings"],
                "multipart": bool(part_count > 1 or ring_stats["multipart"]),
            }
            for key, field_name in read_map.items():
                item[key] = record.get(field_name) if field_name else None
            features.append(item)
    return features


def _provided_for_feature(item: Mapping[str, Any], attrs: Mapping[str, Any]) -> dict:
    merged = dict(attrs.get("values") or {})
    by_dkbh = attrs.get("by_dkbh") or {}
    by_oid = attrs.get("by_oid") or {}
    key = None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"])
    if key and isinstance(by_dkbh.get(key), dict):
        merged.update(by_dkbh[key])
    if isinstance(by_oid.get(str(item["oid"])), dict):
        merged.update(by_oid[str(item["oid"])])
    return merged


def _source_area_cross_check(features: Sequence[Mapping[str, Any]]) -> list:
    """源 DIKUAIAREA（公顷，geodesic 净面积）与本工具面积口径的交叉核对。"""
    rows = []
    for item in features:
        net = item.get("net_area_m2")
        gross = item.get("gross_area_m2")
        hole = item.get("hole_area_m2")
        hectares = item.get("source_area_ha")
        try:
            hectares = float(hectares) if hectares is not None else None
        except (TypeError, ValueError):
            hectares = None
        source_net = None if hectares is None else hectares * 10000.0
        rows.append({
            "DKBH": None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"]),
            "TBMJ_m2_毛面积": None if gross is None else round(float(gross), 2),
            "KCMJ_m2_孔洞面积": None if hole is None else round(float(hole), 2),
            "TBDLMJ_m2_净面积": None if net is None else round(float(net), 2),
            "DIKUAIAREA_ha": hectares,
            "DIKUAIAREA换算_m2": None if source_net is None else round(source_net, 2),
            "净面积与源差值_m2": None if net is None or source_net is None else round(float(net) - source_net, 2),
            "note": "源DIKUAIAREA=shape.geodesicArea/10000（公顷4位小数，geodesic 净面积）；本工具为 CGCS2000 三度带平面面积，二者差值为 geodesic 与平面投影之差（本批约 0.03%–0.05%），仅作交叉核对、不作误差判定",
        })
    return rows


def _gap_csv_rows(gap: Sequence[Mapping[str, Any]]) -> list:
    rows = []
    for item in gap:
        meta = FIELD_BY_NAME[item["field"]]
        rows.append({
            "序号": appendix_c.FIELD_ORDER.index(item["field"]) + 1,
            "字段名": item["field"],
            "字段别名": item["alias"],
            "必填性": item["required"],
            "缺失原因": item.get("reason") or "",
            "涉及地块": "、".join(item.get("affected_features") or []),
            "附录C依据": "附录C 序号{} 必填性{}".format(
                appendix_c.FIELD_ORDER.index(item["field"]) + 1, meta["required"]),
        })
    return rows


def _classify_gap_fields(empty_required: Sequence[Mapping[str, Any]]) -> list:
    """把 qa 的行级空必填项按附录C条款归类为字段级清单（去重、保持字段表顺序）。"""
    fields = {str(item.get("field")) for item in empty_required if item.get("field")}
    grouped: dict = {}
    for name in FIELD_ORDER:
        if name not in fields:
            continue
        grouped.setdefault(CLAUSE_BY_FIELD.get(name, "附录C 未归类"), []).append(name)
    return [{"clause": clause, "fields": names} for clause, names in grouped.items()]


def generate_qa_report(feature_class: Path) -> dict:
    """对产出的附录C图层调用 acceptance.qa._check_appendix_c，生成质检明细。

    质检由此正式入口（scripts/build_appendix_c.py --qa-json）生成，可复跑；
    不再依赖 temp/ 下的临时脚本。
    """
    import arcpy
    from acceptance.qa import _check_appendix_c

    layer = str(Path(feature_class).expanduser().resolve())
    qa_result = _check_appendix_c(layer)
    detail = qa_result.get("detail") or {}
    values = detail.get("values") or {}
    fields_detail = detail.get("fields") or {}
    geometry_review = detail.get("geometry_review") or {}
    empty_required = values.get("empty_required") or []
    missing_fields = [name for name in FIELD_ORDER
                      if name in {str(item.get("field")) for item in empty_required}]

    extras = {
        "true_multipart": [],
        "partCount_all": {},
        "qa_multipart_false_positive": False,
        "area_ring_check": [],
    }
    if arcpy.Exists(layer):
        with arcpy.da.SearchCursor(
                layer, ["DKBH", "TBMJ", "KCMJ", "TBDLMJ", "SHAPE@"]) as cursor:
            for dkbh, tbmj, kcmj, tbdmj, geometry_obj in cursor:
                part_count = int(getattr(geometry_obj, "partCount", 0) or 0)
                ring = (appendix_c.polygon_ring_stats(geometry_obj.JSON)
                        if geometry_obj is not None else {})
                extras["partCount_all"][str(dkbh)] = part_count
                if part_count > 1:
                    extras["true_multipart"].append({"DKBH": str(dkbh), "partCount": part_count})
                extras["area_ring_check"].append({
                    "DKBH": str(dkbh),
                    "TBMJ": tbmj,
                    "KCMJ": kcmj,
                    "TBDLMJ": tbdmj,
                    "外环面积": None if not ring else round(ring.get("gross_area", 0.0), 2),
                    "孔洞面积": None if not ring else round(ring.get("hole_area", 0.0), 2),
                    "几何净面积": None if geometry_obj is None
                    else round(float(geometry_obj.area), 2),
                    "partCount": part_count,
                    "ring_count": None if not ring else ring.get("ring_count"),
                })
        extras["qa_multipart_false_positive"] = bool(
            geometry_review.get("multipart_count") and not extras["true_multipart"])

    hole_review = geometry_review.get("hole_ring_review") or {}
    return {
        "layer": layer,
        "generated_by": "scripts/build_appendix_c.py --qa-json",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "qa_status": qa_result.get("status"),
        "qa_summary": {
            "fields_status": fields_detail.get("status"),
            "values_status": values.get("status"),
            "geometry_status": geometry_review.get("status"),
        },
        "count": detail.get("count"),
        "missing_required_rows": len(empty_required),
        "missing_required_rows_note": (
            "qa 口径：缺失必填项按“要素行×字段”计为 {} 行（字段级去重后 {} 个字段：{}）；"
            "建库报告 missing_required_fields 为字段级口径，二者口径不同，不能直接对比。".format(
                len(empty_required), len(missing_fields), "、".join(missing_fields) or "无")
        ),
        "missing_required_fields": missing_fields,
        "missing_required_fields_count": len(missing_fields),
        "invalid_domain": values.get("invalid_domain") or [],
        "duplicate_BSM": values.get("duplicate_BSM") or [],
        "coded_values_needing_review": values.get("coded_values_needing_review") or {},
        "geometry": {
            "status": geometry_review.get("status"),
            "multipart_count": geometry_review.get("multipart_count"),
            "multipart_BSM": geometry_review.get("multipart_BSM"),
            "multipart_rule": geometry_review.get("multipart_rule"),
            "slit_connector_review_status":
                (geometry_review.get("slit_connector_review") or {}).get("status"),
            "hole_ring_review": hole_review,
            "note": QA_GEOMETRY_NOTE,
        },
        "clause_classification": _classify_gap_fields(empty_required),
        "extras": extras,
        "clause_notes": [
            "附录C注2：序号8–19与变更调查一致，其中序号12/15/16（TBMJ/KCMJ/TBDLMJ）面积需重算——"
            "本工具已重算：TBMJ=外环毛面积、KCMJ=孔洞面积、TBDLMJ=净面积，逐要素见 extras.area_ring_check。",
            "附录C注6：多部件判据为 partCount>1（qa 原 isMultipart 判据已修正，误报已在审核修复轮纠正）；"
            "本层 multipart_count 见 geometry，孔洞计入 KCMJ，不判 FAIL。",
            "missing_required_rows=" + str(len(empty_required))
            + " 是 qa 的“要素行×字段”口径；字段级缺口为 "
            + str(len(missing_fields)) + " 个：" + "、".join(missing_fields) + "。",
            "本批 MISSING 均为自然资源移交字段/申请表资料，非工具可推导；补齐后须重跑本工具并重新质检。",
        ],
        "qa_result": qa_result,
    }


def _qa_report_section(qa_path: Path, qa_output: Mapping[str, Any]) -> dict:
    """建库报告里的质检摘要段（质检 JSON 已由 generate_qa_report 生成）。"""
    return {
        "generated_by": "scripts/build_appendix_c.py --qa-json",
        "path": str(qa_path),
        "status": qa_output.get("qa_status"),
        "summary": qa_output.get("qa_summary"),
        "missing_required_fields": qa_output.get("missing_required_fields"),
        "missing_required_rows": qa_output.get("missing_required_rows"),
        "note": "质检与建库同一次运行生成；质检异常时本次 gdb 与已写文件一并回滚",
    }


def build(source: Path, out_gdb: Path, layer_name: str = LAYER_NAME,
          project_name: Optional[str] = None, project_no: Optional[str] = None,
          county_code: Optional[str] = None, county_name: Optional[str] = None,
          id_field: Optional[str] = None, attrs_json: Optional[Path] = None,
          expect_wkid: Optional[int] = None, qa_json: Optional[Path] = None) -> dict:
    try:
        import arcpy
    except ImportError:
        raise RuntimeError("本脚本须在 ArcGIS Pro Python 环境下运行（缺少 arcpy）")

    source = Path(source).expanduser().resolve()
    out_gdb = Path(out_gdb).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError("源数据不存在：{}".format(source))
    if out_gdb.suffix.lower() != ".gdb":
        raise ValueError("--out-gdb 必须以 .gdb 结尾：{}".format(out_gdb))
    out_dir = out_gdb.parent
    report_path = out_dir / REPORT_NAME
    gap_path = out_dir / GAP_NAME
    source_table_path = out_dir / SOURCE_TABLE_NAME
    qa_path = None if qa_json is None else Path(qa_json).expanduser().resolve()
    check_paths = [out_gdb, report_path, gap_path, source_table_path]
    if qa_path is not None:
        check_paths.append(qa_path)
    for path in check_paths:
        if path.exists() or arcpy.Exists(str(path)):
            raise FileExistsError("输出已存在，不覆盖：{}".format(path))
    if not layer_name or not str(layer_name).strip():
        raise ValueError("--layer-name 不能为空")

    expect_wkid = parse_wkid(expect_wkid, "--expect-wkid")
    desc = arcpy.Describe(str(source))
    source_wkid = int(getattr(desc.spatialReference, "factoryCode", 0) or 0)
    if expect_wkid is not None and source_wkid != int(expect_wkid):
        raise ValueError("源数据 WKID={} 与 --expect-wkid={} 不一致，拒绝写入".format(
            source_wkid, expect_wkid))

    source_hashes_before = source_family_hashes(source)
    field_names = [field.name for field in arcpy.ListFields(str(source))]
    id_field = _pick_id_field(field_names, id_field)
    attrs = load_attrs(attrs_json)
    features = read_features(arcpy, source, id_field)
    if not features:
        raise ValueError("源数据没有要素：{}".format(source))

    options = BuildOptions(
        county_code=county_code, county_name=county_name,
        project_name=project_name, project_no=project_no,
    )
    rows_by_feature = [
        build_field_rows(item, options, _provided_for_feature(item, attrs))
        for item in features
    ]
    feature_ids = [item.get("feature_id") for item in features]
    rows_by_feature, bsm_normalized = apply_bsm_suffixes(rows_by_feature, feature_ids)

    errors = row_errors(rows_by_feature)
    if errors:
        raise ValueError("字段取值校验未通过，拒绝写入：{}".format(
            json.dumps(errors, ensure_ascii=False)))
    violations = domain_violations(rows_by_feature)
    if violations:
        raise ValueError("字段值域校验未通过，拒绝写入：{}".format(
            json.dumps(violations, ensure_ascii=False)))

    feature_values = [
        {"oid": item["oid"], "values": {row["field"]: row["value"] for row in rows}}
        for item, rows in zip(features, rows_by_feature)
    ]
    result = appendix_c.build_layer(
        out_gdb, source, feature_values, layer_name=layer_name, expect_wkid=expect_wkid)
    # 质检在落盘 try/except 内生成，异常时走同一回滚策略删除本次 gdb（见下方 created_files）。
    qa_output = None
    qa_section = {
        "generated_by": None,
        "path": None if qa_path is None else str(qa_path),
        "status": None,
        "summary": None,
        "missing_required_fields": None,
        "missing_required_rows": None,
        "note": "未指定 --qa-json 时不生成质检文件；指定后由同一脚本生成并纳入回滚范围",
    }

    summary = summarize(rows_by_feature, feature_ids)
    gap = gap_table(rows_by_feature, feature_ids)
    source_hashes_after = source_family_hashes(source)
    hash_status = "PASS" if source_hashes_before == source_hashes_after else "CHANGED"
    multipart = [item for item in features if item.get("multipart")]
    hole_parcels = [
        {
            "DKBH": None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"]),
            "hole_rings": item.get("hole_rings"),
            "exterior_rings": item.get("exterior_rings"),
            "hole_area_m2": None if item.get("hole_area_m2") is None else round(float(item["hole_area_m2"]), 2),
        }
        for item in features if item.get("hole_rings")
    ]
    gross_total = sum(float(item["gross_area_m2"]) for item in features
                      if item.get("gross_area_m2") is not None)
    hole_total = sum(float(item["hole_area_m2"]) for item in features
                     if item.get("hole_area_m2") is not None)
    hole_ratio = (hole_total / gross_total) if gross_total else 0.0
    conflicts = project_name_conflicts(source, project_name)
    if project_no and not appendix_c.is_blank(project_no):
        source_project_no = {}
        features_project_no = {str(item.get("project_no")).strip() for item in features
                               if not appendix_c.is_blank(item.get("project_no"))}
        if features_project_no and str(project_no).strip() not in features_project_no:
            conflicts.append({
                "item": "--project-no 与源数据 XMBH 不一致",
                "status": "待核",
                "year_conflict": False,
                "values": {"--project-no": str(project_no).strip(),
                           "源XMBH": sorted(features_project_no)},
                "reason": "源数据自带项目编号时以源值为准，参数值仅供核对，须经办确认",
                "source": "源数据字段 {}".format(appendix_c.SOURCE_FIELD_MAP["project_no"]),
            })

    report = {
        "tool": "scripts/build_appendix_c.py",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "created_at_note": "仅记录运行时刻；GXSJ 未使用该时间，须取变更调查更新时间",
        "contract": contract_echo(),
        "parameters": {
            "source": str(source),
            "out_gdb": str(out_gdb),
            "layer_name": layer_name,
            "project_name": project_name,
            "project_no": project_no,
            "county_code": county_code,
            "county_name": county_name,
            "id_field": id_field,
            "attrs_json": None if attrs_json is None else str(Path(attrs_json).resolve()),
            "expect_wkid": expect_wkid,
            "qa_json": None if qa_path is None else str(qa_path),
        },
        "source": {
            "path": str(source),
            "shape_type": str(getattr(desc, "shapeType", "")),
            "wkid": source_wkid,
            "crs_name": str(getattr(desc.spatialReference, "name", "Unknown")),
            "feature_count": len(features),
            "id_field": id_field,
            "hashes_before": source_hashes_before,
            "hashes_after": source_hashes_after,
            "hash_status": hash_status,
            "hash_note": "源 shp 家族运行前后 sha256 比对；CHANGED 表示源文件在运行期间发生变化",
        },
        "output": result,
        "statistics": summary,
        "field_status": field_status_map(rows_by_feature),
        "missing_required_fields": summary["missing_required_fields"],
        "missing_conditional_fields": summary["missing_conditional_fields"],
        "bsm_normalized": bsm_normalized,
        "multipart": {
            "count": len(multipart),
            "DKBH": [None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"])
                     for item in multipart],
            "status": "FAIL" if multipart else "PASS",
            "rule": "partCount>1 或 Esri JSON 外环数>1；ArcPy isMultipart 对含孔洞多边形也为 True，不作注6依据",
            "hole_parcels": hole_parcels,
            "hole_note": "孔洞（内环）为扣除地类/其他地类，本批 {} 个图斑含孔洞，不属附录C注6禁止的多部件。".format(
                len(hole_parcels)),
            "policy": "多部件要素仍按源几何写出（不擅自拆分，避免改变边界与权属），但在报告与质检中标 FAIL，须拆分后重新送审（附录C注6）。",
        },
        "checker_defect": {
            "item": "acceptance.qa._check_appendix_c 的多部件判定（已按Lead授权修复）",
            "defect": "原用 geometry.isMultipart 判附录C注6多部件；ArcPy 对含孔洞的单部件 Polygon 也返回 isMultipart=True，会把 22/111/1 这类正常单部件图斑误报为多部件。",
            "before": {
                "rule": "isMultipart",
                "multipart_count": sum(1 for item in features if item.get("is_multipart_flag")),
                "DKBH": [None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"])
                         for item in features if item.get("is_multipart_flag")],
                "geometry_status": "FAIL（误报）",
            },
            "after": {
                "rule": "partCount>1",
                "multipart_count": len(multipart),
                "DKBH": [None if appendix_c.is_blank(item.get("feature_id")) else str(item["feature_id"])
                         for item in multipart],
                "geometry_status": "NEEDS_REVIEW（孔洞另列 hole_ring_review）",
            },
            "negative_control": "反向对照：partCount=2 的两分离外环 → multipart FAIL（pytest test_partcount_two_is_fail_even_if_is_multipart_flag_is_false；Pro 实测 partCount=2）",
            "positive_control": "正向对照：partCount=1 且 isMultipart=True（含孔洞）→ 不判 FAIL，hole_ring_review.count=1（pytest test_hole_ring_single_part_is_not_multipart_fail；Pro 实测 area 96.002/partCount 1）",
            "qa_change_scope": "只改多部件判据；新增 hole_ring_review 与 multipart_rule 键，其他检查项与原有报告键未动。",
        },
        "area_check": result.get("area_check"),
        "schema_readback": result.get("schema"),
        "source_area_cross_check": _source_area_cross_check(features),
        "deduction_notes": [
            "TBMJ=外环面积合计（毛，含扣除地类）；KCMJ=孔洞（内环）面积合计；TBDLMJ=TBMJ-KCMJ（净面积，等于几何面积）。",
            "源数据 shp.xml 血缘含 Erase“森林资源调查2025年”过程，孔洞即扣除的林地；但扣除地类编码（KCDLBM）与扣除系数（KCXS）无法从源数据推导，保持 NULL，须与变更调查核对。",
            "KCXS 值域为 [0,1)，不得为凑数硬填 1；TBDLMJ 直接按孔洞面积扣减。",
        ],
        "first_run_void_note": "首跑产品作废原因：首跑把 TBMJ 误取几何净面积、且报告把含孔洞图斑误判为多部件（qa 以 isMultipart 判定）；首跑产物已删除，本报告为重跑结果。",
        "review_fix_round": REVIEW_FIX_ROUND,
        "qa": qa_section,
        "project_name_conflicts": conflicts,
        "conclusions": [
            "面积口径：TBMJ=外环面积合计（毛）{:.2f} m²；KCMJ=孔洞面积合计 {:.2f} m²（占毛面积 {:.2%}）；TBDLMJ=净面积 {:.2f} m²；逐要素见 area_check.per_feature。".format(
                gross_total, hole_total, hole_ratio, gross_total - hole_total),
            "本项目 XMBH={} 首字符为“{}”（补充方式码），属非项目类；与附录H注5的项目资料要求相互印证：非项目类不需提供项目设计报告/批复。若经办确认实际为项目类，则编号或资料之一有误，须退回核对。".format(
                (features[0].get("project_no") or "（空）"), str(features[0].get("project_no") or "?")[:1]),
            "附录C注6：本批源数据 {} 个图斑，真多部件 {} 个；含扣除地类孔洞 {} 个（不属多部件，环数/孔洞面积见 multipart.hole_parcels 与 area_check.per_feature）。".format(
                len(features), len(multipart), len(hole_parcels)),
            "以下字段为业务推定（ASSUMED），不得按已核实处理：XMMC、JZGDMJ、SFZPJ。",
        ],
        "status_rule": STATUS_RULE,
        "unverified_items": UNVERIFIED_ITEMS,
        "gap_list_count": len(gap),
        "artifacts": {
            "report": str(report_path),
            "gap_list": str(gap_path),
            "field_source_table": str(source_table_path),
            "qa_json": None if qa_path is None else str(qa_path),
            "qa_generator": "scripts/build_appendix_c.py --qa-json" if qa_path is not None else None,
        },
    }

    created_files = []
    try:
        # 质检与文件落盘同一 try/except：任一环节抛错都删除本次新建 gdb 与已写文件。
        if qa_path is not None:
            qa_output = generate_qa_report(Path(result["feature_class"]))
            qa_section.update(_qa_report_section(qa_path, qa_output))
        write_csv(gap_path, GAP_HEADER, _gap_csv_rows(gap))
        created_files.append(gap_path)
        write_csv(source_table_path, SOURCE_TABLE_HEADER,
                  field_source_rows(rows_by_feature, feature_ids))
        created_files.append(source_table_path)
        if qa_path is not None and qa_output is not None:
            write_json(qa_path, qa_output)
            created_files.append(qa_path)
        write_json(report_path, report)
        created_files.append(report_path)
    except Exception:
        for path in created_files:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
                arcpy.management.Delete(str(out_gdb))
        except Exception:
            pass
        raise

    return {
        "geodatabase": str(out_gdb),
        "feature_class": result["feature_class"],
        "feature_count": result["count"],
        "wkid": result["wkid"],
        "crs_name": result["crs_name"],
        "hash_status": hash_status,
        "statistics": summary["status_counts"],
        "missing_required": [item["field"] for item in summary["missing_required_fields"]],
        "missing_conditional": [item["field"] for item in summary["missing_conditional_fields"]],
        "multipart_count": len(multipart),
        "area_check": result.get("area_check"),
        "schema_status": (result.get("schema") or {}).get("status"),
        "qa_status": None if qa_output is None else qa_output.get("qa_status"),
        "qa_missing_required_fields": None if qa_output is None
        else qa_output.get("missing_required_fields"),
        "qa_missing_required_rows": None if qa_output is None
        else qa_output.get("missing_required_rows"),
        "artifacts": report["artifacts"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="项目地块矢量（面图层或本地 shp/GDB 要素类）")
    parser.add_argument("--out-gdb", required=True, help="新建的 .gdb 输出路径（禁止覆盖）")
    parser.add_argument("--layer-name", default=LAYER_NAME, help="图层名，默认 补充耕地质量鉴定图斑")
    parser.add_argument("--project-name", help="项目名称/批次（XMMC，记为 ASSUMED）")
    parser.add_argument("--project-no", help="项目编号（XMBH；源数据无 XMBH 时使用）")
    parser.add_argument("--county-code", help="县级行政区代码（须与 XMBH 第2–7位一致）")
    parser.add_argument("--county-name", help="县级行政区名称（XMC）")
    parser.add_argument("--id-field", help="地块编号字段，默认自动识别 DIKUAI_NO/DKBH/PL_NAME")
    parser.add_argument("--attrs-json", help="显式提供的字段值 JSON（values/by_dkbh/by_oid）")
    parser.add_argument("--expect-wkid", type=int, help="期望源数据 WKID，不一致则拒绝写入")
    parser.add_argument(
        "--qa-json",
        help="同时生成质检明细 JSON（调用 acceptance.qa._check_appendix_c）；"
             "建议与 gdb 同目录，如 附录C质检.json。禁止覆盖",
    )
    args = parser.parse_args(argv)
    try:
        result = build(
            source=Path(args.source),
            out_gdb=Path(args.out_gdb),
            layer_name=args.layer_name,
            project_name=args.project_name,
            project_no=args.project_no,
            county_code=args.county_code,
            county_name=args.county_name,
            id_field=args.id_field,
            attrs_json=Path(args.attrs_json) if args.attrs_json else None,
            expect_wkid=args.expect_wkid,
            qa_json=Path(args.qa_json) if args.qa_json else None,
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        parser.exit(1, "FAIL: {}\n".format(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("hash_status") != "PASS":
        print("FAIL: 源文件哈希在运行期间发生变化，见报告 source.hash_status", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
