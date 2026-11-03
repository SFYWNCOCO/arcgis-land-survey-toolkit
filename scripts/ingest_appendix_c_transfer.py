# -*- coding: utf-8 -*-
"""自然资源移交包接入与套合（ArcGIS Pro Python）：移交 gdb → 附录C成果 + 图斑-地块对照表。

依据：《广西补充耕地质量验收作业指导书 v1.1（2026-08-17）》附录C、附录I 注2。
字段契约取自 acceptance.qa.APPENDIX_C；纯逻辑在 acceptance/transfer_intake.py。

路径规则
--------
* 移交的鉴定图斑若 29 字段名/类型/长度全对 → 原样 CopyFeatures（不改字段/改值/重编号）；
* 不符合（旧28字段版、缺字段、类型长度不符）→ 按附录C重建，只承接已有值，缺的留 NULL 进待补；
* 只有 DLTB → 按字段映射生成附录C底稿，逐字段记状态；
* 项目地块与成品层套合输出《图斑-地块对照表.csv》，坐标系不一致直接报错（不猜转换）；
* 所有输出拒绝覆盖，运行前后对移交 gdb 与项目 shp 做哈希比对写入报告。

用法（Pro Python）：
  python scripts/ingest_appendix_c_transfer.py --transfer-gdb 移交.gdb --out-dir 输出目录 \
      [--project-shp 地块.shp] [--dltb 层名] [--appendix-c 层名] \
      [--project-name 批次名] [--project-no F...] [--county-code 990001] \
      [--county-name 示例区] [--attrs-json attrs.json] [--region-table 区划.csv] \
      [--expect-wkid 4524] [--dry-run]

输出（--out-dir 下）：移交核对报告.json、附录C质检.json、待补清单.csv、
图斑-地块对照表.csv、附录C鉴定图斑.gdb（仅非 dry-run）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acceptance import appendix_c, transfer_intake  # noqa: E402
from acceptance.appendix_c import (  # noqa: E402
    BuildOptions,
    FIELD_BY_NAME,
    field_source_rows,
    gap_table,
    source_family_hashes,
    write_csv,
    write_json,
)
from acceptance.crs import parse_wkid  # noqa: E402
from acceptance.transfer_intake import (  # noqa: E402
    GAP_CSV_HEADER,
    GAP_NAME,
    LAYER_NAME,
    MATCH_CSV_HEADER,
    MATCH_NAME,
    OUT_GDB_NAME,
    QA_NAME,
    REPORT_NAME,
    UNVERIFIED_ITEMS,
    assert_expected_wkid,
    assert_same_crs,
    classify_layers,
    collect_conflicts,
    copy_layer_unchanged,
    decide_action,
    ensure_outputs_absent,
    gdb_fingerprint,
    load_region_table,
    map_appendix_c_rows,
    map_dltb_rows,
    compute_overlaps,
    qa_appendix_c,
    rebuild_appendix_c,
    resolve_layers,
    scan_transfer_gdb,
)

SOURCE_TABLE_NAME = "字段来源表.csv"
SOURCE_TABLE_HEADER = [
    "地块编号", "序号", "字段名", "字段别名", "必填性", "状态", "取值", "来源依据", "备注",
]

ID_FIELD_CANDIDATES = ("DIKUAI_NO", "DKBH", "PL_NAME")


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
        str(key) for key in list(values)
        + [field for item in by_dkbh.values() if isinstance(item, dict) for field in item]
        + [field for item in by_oid.values() if isinstance(item, dict) for field in item]
        if str(key).strip().upper() not in FIELD_BY_NAME
    )
    if unknown:
        raise ValueError("--attrs-json 含附录C之外的字段：{}".format(", ".join(unknown)))
    return {"values": values, "by_dkbh": by_dkbh, "by_oid": by_oid}


def _provided_for(oid: Any, feature_id: Any, attrs: Mapping[str, Any]) -> dict:
    merged = dict(attrs.get("values") or {})
    by_dkbh = attrs.get("by_dkbh") or {}
    by_oid = attrs.get("by_oid") or {}
    key = None if transfer_intake.is_blank(feature_id) else str(feature_id)
    if key and isinstance(by_dkbh.get(key), dict):
        merged.update(by_dkbh[key])
    if isinstance(by_oid.get(str(oid)), dict):
        merged.update(by_oid[str(oid)])
    return merged


def _pick_id_field(field_names: Sequence[str], requested: Optional[str] = None) -> str:
    upper = {name.upper(): name for name in field_names}
    if requested:
        if requested.upper() not in upper:
            raise ValueError("--id-field {!r} 不在项目地块字段中：{}".format(
                requested, ", ".join(field_names)))
        return upper[requested.upper()]
    for candidate in ID_FIELD_CANDIDATES:
        if candidate in upper:
            return upper[candidate]
    raise ValueError("项目地块无可用编号字段（候选 {}），请补充".format("/".join(ID_FIELD_CANDIDATES)))


def _feature_id(row_attrs: Mapping[str, Any], oid: Any) -> Any:
    for name in ID_FIELD_CANDIDATES:
        value = row_attrs.get(name)
        if not transfer_intake.is_blank(value):
            return value
    return oid


def _layer_info(layers: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    for item in layers:
        if item.get("name") == name:
            return item
    raise ValueError("扫描结果中缺少图层：{}".format(name))


def _read_project_features(arcpy_module: Any, project_shp: Path, id_field: str,
                           patch_fc: str) -> dict:
    """校验坐标系一致并计算套合结果（双方 WKID 必须相等，不转换）。"""
    project_wkid = int(getattr(arcpy_module.Describe(str(project_shp)).spatialReference,
                               "factoryCode", 0) or 0)
    patch_wkid = int(getattr(arcpy_module.Describe(patch_fc).spatialReference,
                             "factoryCode", 0) or 0)
    assert_same_crs(patch_wkid, project_wkid, "鉴定图斑", "项目地块")
    result = compute_overlaps(str(project_shp), patch_fc, id_field)
    return {"project_wkid": project_wkid, "match": result}


def _read_transfer_rows(arcpy_module: Any, dataset: str) -> list:
    fields = [field.name for field in arcpy_module.ListFields(dataset)
              if field.type not in ("OID", "Geometry")]
    rows = []
    with arcpy_module.da.SearchCursor(dataset, ["OID@"] + fields) as cursor:
        for values in cursor:
            record = dict(zip(["OID@"] + fields, values))
            rows.append(record)
    return rows


def _build_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transfer-gdb", required=True, help="自然资源移交 gdb")
    parser.add_argument("--out-dir", help="输出目录（必须不存在；--dry-run 时可省略）")
    parser.add_argument("--project-shp", help="项目地块 shp（用于套合《图斑-地块对照表.csv》）")
    parser.add_argument("--dltb", help="DLTB 图层名（未自动识别时显式指定）")
    parser.add_argument("--appendix-c", dest="appendix_c", help="鉴定图斑图层名（未自动识别时显式指定）")
    parser.add_argument("--project-name", help="项目名称/批次（XMMC）")
    parser.add_argument("--project-no", help="项目编号（XMBH）")
    parser.add_argument("--county-code", help="县级行政区代码（XDM）")
    parser.add_argument("--county-name", help="县级行政区名称（XMC）")
    parser.add_argument("--attrs-json", help="显式提供的字段值 JSON（values/by_dkbh/by_oid）")
    parser.add_argument("--region-table", help="行政区划名称表 CSV（代码,名称），用于 XZMC")
    parser.add_argument("--expect-wkid", type=int, help="期望移交图层 WKID，不一致则拒绝写入")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做收件核对与识别，不建库、不写文件（报告输出到 stdout）")
    return parser


def run(transfer_gdb: Path, out_dir: Optional[Path], project_shp: Optional[Path] = None,
        dltb: Optional[str] = None, appendix_c_layer: Optional[str] = None,
        project_name: Optional[str] = None, project_no: Optional[str] = None,
        county_code: Optional[str] = None, county_name: Optional[str] = None,
        attrs_json: Optional[Path] = None, region_table: Optional[Path] = None,
        expect_wkid: Optional[int] = None, dry_run: bool = False) -> dict:
    try:
        import arcpy
    except ImportError:
        raise RuntimeError("本脚本须在 ArcGIS Pro Python 环境下运行（缺少 arcpy）")

    transfer_gdb = Path(transfer_gdb).expanduser().resolve()
    if not arcpy.Exists(str(transfer_gdb)):
        raise FileNotFoundError("移交 gdb 不存在：{}".format(transfer_gdb))
    expect_wkid = parse_wkid(expect_wkid, "--expect-wkid")
    if not dry_run:
        if out_dir is None:
            raise ValueError("非 --dry-run 必须提供 --out-dir")
        out_dir = Path(out_dir).expanduser().resolve()
        ensure_outputs_absent([out_dir])
    attrs = load_attrs(attrs_json)
    region = load_region_table(region_table) if region_table else {}
    options = BuildOptions(county_code=county_code, county_name=county_name,
                           project_name=project_name, project_no=project_no)

    transfer_fingerprint_before = gdb_fingerprint(transfer_gdb)
    scan = scan_transfer_gdb(transfer_gdb)
    layers = scan["layers"]
    classified = classify_layers(layers)
    resolved = resolve_layers(layers, dltb, appendix_c_layer)
    decision = decide_action(resolved, layers)
    source_layer = decision["source_layer"]
    action = decision["action"]
    source_name = resolved["appendix_c"] if action in ("copy", "rebuild") else resolved["dltb"]
    source_info = _layer_info(layers, source_name)
    source_wkid = int(source_info.get("wkid") or 0)
    assert_expected_wkid(source_wkid, expect_wkid, source_name)

    project_info = None
    project_wkid = None
    project_hashes_before = None
    if project_shp is not None:
        project_shp = Path(project_shp).expanduser().resolve()
        if not project_shp.exists():
            raise FileNotFoundError("项目地块 shp 不存在：{}".format(project_shp))
        project_hashes_before = source_family_hashes(project_shp)
        project_wkid = int(getattr(arcpy.Describe(str(project_shp)).spatialReference,
                                   "factoryCode", 0) or 0)
        assert_same_crs(source_wkid, project_wkid, source_name, "项目地块")
        project_info = {
            "shp": str(project_shp),
            "wkid": project_wkid,
            "crs_name": str(getattr(arcpy.Describe(str(project_shp)).spatialReference,
                                    "name", "Unknown")),
            "hashes_before": project_hashes_before,
        }

    base_report = {
        "tool": "scripts/ingest_appendix_c_transfer.py",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(dry_run),
        "guide": appendix_c.contract_echo()["source"],
        "transfer": {
            "gdb": str(transfer_gdb),
            "fingerprint_before": transfer_fingerprint_before,
            "hash_status_before": "PASS",
        },
        "project": project_info,
        "parameters": {
            "transfer_gdb": str(transfer_gdb),
            "out_dir": None if out_dir is None else str(out_dir),
            "project_shp": None if project_shp is None else str(project_shp),
            "dltb": dltb,
            "appendix_c": appendix_c_layer,
            "project_name": project_name,
            "project_no": project_no,
            "county_code": county_code,
            "county_name": county_name,
            "attrs_json": None if attrs_json is None else str(Path(attrs_json).resolve()),
            "region_table": None if region_table is None else str(Path(region_table).resolve()),
            "expect_wkid": expect_wkid,
        },
        "identification": {
            "resolved": {"dltb": resolved["dltb"], "appendix_c": resolved["appendix_c"]},
            "explicit": resolved["explicit"],
            "classified": classified,
        },
        "layers": layers,
        "action": decision,
        "attribute_conflicts": [],
        "attribute_conflict_note": "dry-run 只做收件核对，未做属性映射，冲突未评估",
        "unverified_items": UNVERIFIED_ITEMS,
    }

    if dry_run:
        after = gdb_fingerprint(transfer_gdb)
        report = dict(base_report)
        report["transfer"]["fingerprint_after"] = after
        report["transfer"]["hash_status"] = (
            "PASS" if after["aggregate_sha256"] == transfer_fingerprint_before["aggregate_sha256"]
            else "CHANGED")
        report["note"] = "dry-run：只读取移交 gdb 做收件核对，未建库、未写任何文件"
        return report

    out_dir = Path(out_dir)
    out_gdb = out_dir / OUT_GDB_NAME
    report_path = out_dir / REPORT_NAME
    qa_path = out_dir / QA_NAME
    gap_path = out_dir / GAP_NAME
    match_path = out_dir / MATCH_NAME
    source_table_path = out_dir / SOURCE_TABLE_NAME
    ensure_outputs_absent([out_gdb, report_path, qa_path, gap_path, match_path,
                           source_table_path])

    created_dir = False
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
        created_dir = True
        rows_by_feature = None
        feature_ids = None
        gap = []
        field_source = []
        conflicts = []
        if action == "copy":
            output = copy_layer_unchanged(out_gdb, source_info["path"])
            qa_output = qa_appendix_c(output["feature_class"])
            gap = _gap_from_qa(qa_output)
            conflicts = []
            if not output.get("unchanged"):
                raise RuntimeError(
                    "原样复制校验未通过（要素数/面积合计/取值摘要不一致）：{}".format(
                        json.dumps({key: output.get(key) for key in (
                            "count_before", "count_after", "area_sum_before",
                            "area_sum_after", "attribute_digest_before",
                            "attribute_digest_after")}, ensure_ascii=False)))
        else:
            transfer_rows = _read_transfer_rows(arcpy, source_info["path"])
            rows_by_feature = []
            feature_ids = []
            for record in transfer_rows:
                oid = record["OID@"]
                source_attrs = {key: value for key, value in record.items() if key != "OID@"}
                feature_id = _feature_id(source_attrs, oid)
                feature_ids.append(feature_id)
                provided = _provided_for(oid, feature_id, attrs)
                if action == "dltb":
                    rows = map_dltb_rows(source_attrs, options, provided, region)
                else:
                    rows = map_appendix_c_rows(source_attrs, options, provided, region)
                rows_by_feature.append(rows)
            feature_values = [
                {"oid": record["OID@"],
                 "values": {row["field"]: row["value"] for row in rows}}
                for record, rows in zip(transfer_rows, rows_by_feature)
            ]
            output = rebuild_appendix_c(out_gdb, source_info["path"], feature_values,
                                        layer_name=LAYER_NAME, expect_wkid=expect_wkid)
            qa_output = qa_appendix_c(output["feature_class"])
            gap = gap_table(rows_by_feature, feature_ids)
            field_source = field_source_rows(rows_by_feature, feature_ids)
            conflicts = collect_conflicts(rows_by_feature)

        overlay = {"status": "SKIPPED", "reason": "未提供 --project-shp，未做图斑-地块套合"}
        overlay_rows = []
        if project_shp is not None:
            id_field = _pick_id_field(
                [field.name for field in arcpy.ListFields(str(project_shp))])
            overlay_result = _read_project_features(arcpy, project_shp, id_field,
                                                   output["feature_class"])
            overlay_rows = overlay_result["match"]["rows"]
            overlay = {
                "status": "PASS",
                "project_id_field": id_field,
                "project_wkid": overlay_result["project_wkid"],
                "csv": str(match_path),
                "summary": overlay_result["match"]["summary"],
                "unmatched_parcels": overlay_result["match"]["unmatched_parcels"],
                "unmatched_patches": overlay_result["match"]["unmatched_patches"],
                "cross_boundary": overlay_result["match"]["cross_boundary"],
                "note": overlay_result["match"]["note"],
                "rows_in_csv": len(overlay_result["match"]["rows"]),
            }

        transfer_fingerprint_after = gdb_fingerprint(transfer_gdb)
        transfer_hash_status = (
            "PASS" if transfer_fingerprint_after["aggregate_sha256"]
            == transfer_fingerprint_before["aggregate_sha256"] else "CHANGED")
        project_hashes_after = (
            source_family_hashes(project_shp) if project_shp is not None else None)
        project_hash_status = (
            None if project_hashes_before is None
            else ("PASS" if project_hashes_after == project_hashes_before else "CHANGED"))

        report = dict(base_report)
        report.update({
            "transfer": {
                "gdb": str(transfer_gdb),
                "fingerprint_before": transfer_fingerprint_before,
                "fingerprint_after": transfer_fingerprint_after,
                "hash_status": transfer_hash_status,
            },
            "project": None if project_info is None else dict(
                project_info, hashes_after=project_hashes_after,
                hash_status=project_hash_status),
            "output": {
                "geodatabase": output["geodatabase"],
                "feature_class": output["feature_class"],
                "layer_name": output.get("layer_name", LAYER_NAME),
                "count": output.get("count", output.get("count_after")),
                "wkid": output.get("wkid"),
                "crs_name": output.get("crs_name"),
                "schema": output.get("schema"),
                "area_check": output.get("area_check"),
                "copy_rule": output.get("copy_rule"),
                "unchanged": output.get("unchanged"),
                "count_before": output.get("count_before"),
                "count_after": output.get("count_after"),
                "area_sum_before": output.get("area_sum_before"),
                "area_sum_after": output.get("area_sum_after"),
                "attribute_digest_before": output.get("attribute_digest_before"),
                "attribute_digest_after": output.get("attribute_digest_after"),
            },
            "qa": qa_output,
            "overlay": overlay,
            "attribute_conflicts": conflicts,
            "attribute_conflict_count": len(conflicts),
            "attribute_conflict_note": (
                "源层已有值优先，参数/--attrs-json 不静默覆盖；下列冲突须经办确认"
                if conflicts else "无参数/提供值与源层值冲突"),
            "gap_list_count": len(gap),
            "artifacts": {
                "report": str(report_path),
                "qa_json": str(qa_path),
                "gap_list": str(gap_path),
                "match_table": str(match_path) if project_shp is not None else None,
                "field_source_table": str(source_table_path) if field_source else None,
                "geodatabase": str(out_gdb),
            },
        })
        write_csv(gap_path, GAP_CSV_HEADER, _gap_csv_rows(gap))
        write_json(qa_path, qa_output)
        if project_shp is not None:
            write_csv(match_path, MATCH_CSV_HEADER, overlay_rows)
        if field_source:
            write_csv(source_table_path, SOURCE_TABLE_HEADER, field_source)
        write_json(report_path, report)
        if transfer_hash_status != "PASS" or project_hash_status == "CHANGED":
            raise RuntimeError("源数据在运行期间发生变化（移交={}，项目={}），见报告".format(
                transfer_hash_status, project_hash_status))
        return _summary(report)
    except Exception:
        try:
            if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
                arcpy.management.Delete(str(out_gdb))
        except Exception:
            pass
        if created_dir:
            shutil.rmtree(out_dir, ignore_errors=True)
        raise


def _gap_from_qa(qa_output: Mapping[str, Any]) -> list:
    """原样复制路径不改源值，待补清单由 qa 检出的空必填字段生成（字段级）。"""
    fields = qa_output.get("missing_required_fields") or []
    rows = []
    for name in fields:
        meta = FIELD_BY_NAME[name]
        rows.append({
            "field": name, "alias": meta["alias"], "required": meta["required"],
            "value": None, "affected_features": [],
            "reason": "移交原件该必填字段为空；按附录I 注2 保持原样，须移交方补充",
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
            "涉及要素": "、".join(str(value) for value in item.get("affected_features") or []),
            "附录C依据": "附录C 序号{} 必填性{}".format(
                appendix_c.FIELD_ORDER.index(item["field"]) + 1, meta["required"]),
        })
    return rows


def _summary(report: Mapping[str, Any]) -> dict:
    overlay = report.get("overlay") or {}
    return {
        "dry_run": report.get("dry_run"),
        "action": (report.get("action") or {}).get("action"),
        "source_layer": (report.get("action") or {}).get("source_layer"),
        "geodatabase": (report.get("artifacts") or {}).get("geodatabase"),
        "feature_count": (report.get("output") or {}).get("count"),
        "wkid": (report.get("output") or {}).get("wkid"),
        "copy_unchanged": (report.get("output") or {}).get("unchanged"),
        "qa_status": (report.get("qa") or {}).get("qa_status"),
        "qa_missing_required_fields": (report.get("qa") or {}).get("missing_required_fields"),
        "qa_missing_required_rows": (report.get("qa") or {}).get("missing_required_rows"),
        "gap_list_count": report.get("gap_list_count"),
        "attribute_conflict_count": report.get("attribute_conflict_count", 0),
        "transfer_hash_status": (report.get("transfer") or {}).get("hash_status"),
        "project_hash_status": (report.get("project") or {}).get("hash_status"),
        "overlay_status": overlay.get("status"),
        "overlay_summary": overlay.get("summary"),
        "artifacts": report.get("artifacts"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_args(argv).parse_args(argv)
    try:
        result = run(
            transfer_gdb=Path(args.transfer_gdb),
            out_dir=Path(args.out_dir) if args.out_dir else None,
            project_shp=Path(args.project_shp) if args.project_shp else None,
            dltb=args.dltb,
            appendix_c_layer=args.appendix_c,
            project_name=args.project_name,
            project_no=args.project_no,
            county_code=args.county_code,
            county_name=args.county_name,
            attrs_json=Path(args.attrs_json) if args.attrs_json else None,
            region_table=Path(args.region_table) if args.region_table else None,
            expect_wkid=args.expect_wkid,
            dry_run=bool(args.dry_run),
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print("FAIL: {}\n".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
