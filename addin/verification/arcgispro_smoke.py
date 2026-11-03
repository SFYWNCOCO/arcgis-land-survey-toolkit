"""Run a disposable synthetic workflow against the installed ArcGIS Pro ArcPy."""
from __future__ import annotations

import json
import sys
import datetime as dt
import hashlib
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLKIT))
sys.path.insert(0, str(TOOLKIT / "scripts"))

import arcpy
from acceptance import ingest, qa, sampling
from acceptance.qa import APPENDIX_C
from acceptance.thematic_raster import summarize_continuous_raster
from acceptance.sampling_qa import check_composite_design
from acceptance.delivery import checklist as delivery_checklist
from acceptance.project import create_project
from delivery_package import _check_gdb
from setup_quality_gdb import create_quality_gdb


def run(out_dir: Path) -> dict:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    project_init = create_project(
        str(out_dir / "project_initialization"), "999998", "模拟县",
        "SIM-ACCEPTANCE-001", 4525, delivery_level="county_initial",
        project_type="project", pollution_report_required=False, reevaluation=False,
    )
    project_root = Path(project_init["project"])
    expected_project_folders = [
        project_root / "数据库成果", project_root / "图件成果",
        project_root / "文字成果", project_root / "附件",
        project_root / "附件" / "相关举证照片",
    ]
    if not all(path.is_dir() for path in expected_project_folders):
        raise RuntimeError("新建项目未生成全部10.1–10.4目录")
    delivery_report = delivery_checklist(project_root)
    if delivery_report["status"] not in {"FAIL", "NEEDS_REVIEW"}:
        raise RuntimeError("空白合成项目汇交检查不应自动判为通过：{}".format(delivery_report["status"]))
    if delivery_report.get("detailed_package_check", {}).get("status") == "PASS":
        raise RuntimeError("缺少实际成果和人工审核证据的合成项目不得被汇交检查判为通过")

    gdb = out_dir / "synthetic_acceptance.gdb"
    create_quality_gdb(gdb, 4525)
    sr = arcpy.SpatialReference(4525)

    parcels = str(gdb / "模拟图斑")
    arcpy.management.CreateFeatureclass(str(gdb), "模拟图斑", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(parcels, "UNIT_ID", "TEXT", field_length=30)
    arcpy.management.AddField(parcels, "PARCEL_ID", "TEXT", field_length=30)
    polygon = arcpy.Polygon(arcpy.Array([
        arcpy.Point(37500000, 2500000), arcpy.Point(37500010, 2500000),
        arcpy.Point(37500010, 2500005), arcpy.Point(37500000, 2500005),
        arcpy.Point(37500000, 2500000),
    ]), sr)
    with arcpy.da.InsertCursor(parcels, ["SHAPE@", "UNIT_ID", "PARCEL_ID"]) as cursor:
        cursor.insertRow([polygon, "SIM-UNIT-01", "SIM-PARCEL-01"])
    units = str(gdb / "模拟评价单元")
    arcpy.management.CreateFeatureclass(str(gdb), "模拟评价单元", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(units, "UNIT_ID", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(units, ["SHAPE@", "UNIT_ID"]) as cursor:
        cursor.insertRow([polygon, "SIM-UNIT-01"])

    # A blank project database should correctly fail the required Appendix C
    # handoff-layer check. Build one clearly synthetic, schema-valid record so
    # this smoke run exercises a usable project and should end in NEEDS_REVIEW,
    # not a fixture-induced FAIL.
    appendix_c = str(gdb / "补充耕地质量鉴定图斑")
    arcpy.management.CreateFeatureclass(str(gdb), "补充耕地质量鉴定图斑", "POLYGON", spatial_reference=sr)
    arcpy_types = {"String": "TEXT", "Double": "DOUBLE", "Single": "FLOAT", "Date": "DATE"}
    field_names = []
    for field_name, _alias, field_type, length, _required, _domain in APPENDIX_C:
        kwargs = {"field_length": length} if field_type == "String" else {}
        arcpy.management.AddField(appendix_c, field_name, arcpy_types[field_type], **kwargs)
        field_names.append(field_name)
    synthetic_record = {
        "BSM": "SIM-BSM-001", "XDM": "999999", "XMC": "模拟县",
        "XZDM": "999999001", "XZMC": "模拟镇", "BGQDLBM": "1202",
        "BGQDLMC": "模拟变更前地类", "DLBM": "0101", "DLMC": "模拟水田",
        "ZLDWDM": "9999990010010000001", "ZLDWMC": "模拟村",
        "TBMJ": 1000.25, "KCDLBM": None, "KCXS": None, "KCMJ": None,
        "TBDLMJ": 1000.25, "GDLX": "01", "GDPDJB": "01",
        "GXSJ": dt.datetime(2026, 10, 20),
        "XMMC": "合成烟雾项目（模拟）", "XMBH": "SIM-ACCEPTANCE-001",
        "DKBH": "SIM-PARCEL-01", "JZGDMJ": 1000.25, "SFYKT": "否",
        "SFFK": "否", "SFZPJ": "否", "BCFS": "Z", "BCGDLY": "恢复",
        "BZ": "仅用于插件烟雾验证，不是实际成果。",
    }
    with arcpy.da.InsertCursor(appendix_c, ["SHAPE@"] + field_names) as cursor:
        cursor.insertRow([polygon] + [synthetic_record[name] for name in field_names])

    profiles = str(gdb / "模拟质地构型调查点")
    arcpy.management.CreateFeatureclass(str(gdb), "模拟质地构型调查点", "POINT", spatial_reference=sr)
    arcpy.management.AddField(profiles, "UNIT_ID", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(profiles, ["SHAPE@XY", "UNIT_ID"]) as cursor:
        cursor.insertRow([(37500005, 2500002.5), "SIM-UNIT-01"])

    mix = sampling.composite_candidates(parcels, "UNIT_ID", "PARCEL_ID", str(gdb), "模拟混合样设计点", units, "UNIT_ID")
    density = sampling.bulk_density_candidates(profiles, "UNIT_ID", str(gdb), "模拟容重设计点", units, "UNIT_ID")
    mix_fields = {field.name for field in arcpy.ListFields(mix["output"])}
    density_fields = {field.name for field in arcpy.ListFields(density["output"])}
    if not {"SAMPLE_ID", "POINT_ID", "DEPTH_TOP", "DEPTH_BOT"}.issubset(mix_fields):
        raise RuntimeError("混合样设计点缺少规范样品编号、设计点编号或深度字段")
    if not {"SAMPLE_ID", "REP_NO", "RING_CM3"}.issubset(density_fields):
        raise RuntimeError("容重设计点缺少规范样品编号、平行样序号或环刀容积字段")
    with arcpy.da.SearchCursor(mix["output"], ["SAMPLE_ID", "POINT_ID", "DEPTH_TOP", "DEPTH_BOT"]) as cursor:
        mix_records = list(cursor)
    with arcpy.da.SearchCursor(density["output"], ["SAMPLE_ID", "REP_NO", "RING_CM3"]) as cursor:
        density_records = list(cursor)
    if any(row[0] != "SIM-UNIT-01" or row[2:] != (0, 20) for row in mix_records):
        raise RuntimeError("混合样编号或0–20cm设计深度读回不符")
    if sorted(row[1] for row in density_records) != [1, 2, 3] or any(
        row[0] != "RZSIM-UNIT-01" or row[2] != 100 for row in density_records
    ):
        raise RuntimeError("容重样编号、平行样序号或100cm³环刀字段读回不符")
    sampling_qa_path = out_dir / "synthetic_sampling_design_qa.json"
    sampling_qa = check_composite_design(
        parcels, units, mix["output"], "UNIT_ID", "PARCEL_ID", "UNIT_ID", "PJDYBH", "DKBH",
        "POINT_ID", "SAMPLE_ID", "DESIGN_ST", str(sampling_qa_path), arcpy_module=arcpy,
    )
    if sampling_qa["status"] != "NEEDS_REVIEW":
        raise RuntimeError("合成采样点应因小图斑/人工事项保留 NEEDS_REVIEW：{}".format(sampling_qa["status"]))
    if sampling_qa["unit_checks"][0]["generated"] != 3 or sampling_qa["parcel_checks"][0]["generated"] != 3:
        raise RuntimeError("采样空间质检未读回正确单元/图斑点数")
    if sampling_qa["spatial_relation_failures"] or not sampling_qa["spacing_violations"]:
        raise RuntimeError("小图斑采样空间关系或一般15m例外诊断不符合预期")
    rtk_csv = out_dir / "synthetic_rtk.csv"
    rtk_csv.write_text("X,Y,Z\n37500005,2500002.5,103.2\n37500006,2500003.5,103.6\n", encoding="utf-8")
    rtk = ingest.import_rtk_csv(
        str(rtk_csv), str(gdb), "SIM_RTK", "X", "Y", 4525, 4525,
        z_field="Z", axis_order="XY",
    )
    expected_hash = hashlib.sha256(rtk_csv.read_bytes()).hexdigest()
    if rtk["source_files"][0]["sha256"] != expected_hash or rtk["vertical_reference_status"] != "UNVERIFIED":
        raise RuntimeError("RTK 源文件校验值或垂直基准状态错误")
    rtk_attribute_readback = {}
    for label, path in (("raw", rtk["raw_points"]), ("projected", rtk["projected_points"])):
        with arcpy.da.SearchCursor(path, ["Z_STATUS", "Z_FIELD", "VERT_REF"]) as cursor:
            values = list(cursor)
        if values != [("UNVERIFIED", "Z", "未提供/未核实")] * 2:
            raise RuntimeError("RTK {} 点层未保留 Z 高程待核验标记：{}".format(label, values))
        rtk_attribute_readback[label] = values
    quality = qa.check_project(str(gdb), expected_wkid=4525)
    hard_failures = [row for row in quality["checks"] if row.get("status") == "FAIL"]
    if hard_failures:
        raise RuntimeError("合成项目数据库质检出现意外硬失败：{}".format(hard_failures))
    previous_workspace = arcpy.env.workspace
    _check_gdb(gdb, 4525)
    if arcpy.env.workspace != previous_workspace:
        raise RuntimeError("汇交 GDB 检查未恢复 ArcPy 全局 workspace")

    # Exercise the single-date thematic-raster summary on a tiny local raster.
    # Values are intentionally simple so the output can be checked by hand.
    import numpy as np
    thematic_path = str(out_dir / "synthetic_thematic.tif")
    thematic_array = np.array([[5, 6, 7, 8], [1, 2, -9999, 4]], dtype="float32")
    thematic_raster = arcpy.NumPyArrayToRaster(
        thematic_array, arcpy.Point(37500000, 2500000), 1, 1, -9999
    )
    thematic_raster.save(thematic_path)
    arcpy.management.DefineProjection(thematic_path, sr)
    with open(thematic_path, "rb") as source_stream:
        source_hash_before = hashlib.sha256(source_stream.read()).hexdigest()
    previous_output_crs = arcpy.env.outputCoordinateSystem
    previous_transformations = arcpy.env.geographicTransformations
    inherited_output_crs = arcpy.SpatialReference(4326)
    arcpy.env.outputCoordinateSystem = inherited_output_crs
    try:
        thematic_result = summarize_continuous_raster(
            thematic_path, parcels, "UNIT_ID", str(out_dir), max_cells=100
        )
        if arcpy.env.outputCoordinateSystem.factoryCode != 4326:
            raise RuntimeError("单期专题栅格统计未恢复调用方输出坐标系环境")
        if arcpy.env.geographicTransformations != previous_transformations:
            raise RuntimeError("单期专题栅格统计未恢复调用方地理转换环境")
    finally:
        arcpy.env.outputCoordinateSystem = previous_output_crs
        arcpy.env.geographicTransformations = previous_transformations
    thematic_unit = thematic_result["units"][0]
    if thematic_unit["valid_pixel_count"] != 7 or thematic_unit["total_pixel_count"] != 8:
        raise RuntimeError("单期专题栅格像元/NoData数量与独立预期不符：{}".format(thematic_unit))
    if thematic_unit["minimum"] != 1.0 or thematic_unit["maximum"] != 8.0:
        raise RuntimeError("单期专题栅格最小/最大值读回不符：{}".format(thematic_unit))
    if abs(thematic_unit["mean"] - (33.0 / 7.0)) > 1e-9:
        raise RuntimeError("单期专题栅格均值与独立手算不符：{}".format(thematic_unit))
    if thematic_result["status"] != "NEEDS_FIELD_REVIEW":
        raise RuntimeError("单期专题栅格摘要不得输出自动验收结论。")
    with open(thematic_path, "rb") as source_stream:
        source_hash_after = hashlib.sha256(source_stream.read()).hexdigest()
    if source_hash_before != source_hash_after:
        raise RuntimeError("单期专题栅格摘要意外改写了源栅格。")
    result = {
        "arcgis_pro_python": sys.version,
        "arcpy_version": arcpy.GetInstallInfo().get("Version"),
        "wkid": 4525,
        "project_initialization": {
            "status": "PASS",
            "project": str(project_root),
            "gdb": project_init["gdb"],
            "manifest": project_init["manifest"],
            "delivery_manifest": project_init["delivery_manifest"],
            "required_folders_present": all(path.is_dir() for path in expected_project_folders),
        },
        "synthetic_delivery_check": {
            "status": delivery_report["status"],
            "detailed_status": (delivery_report.get("detailed_package_check") or {}).get("status"),
            "failure_count": len((delivery_report.get("detailed_package_check") or {}).get("failures", [])),
            "pending_manual_review_count": len((delivery_report.get("detailed_package_check") or {}).get("pending_manual_reviews", [])),
            "report_scope": delivery_report["scope"],
        },
        "evaluation_unit_schema": next(row for row in quality["checks"] if row["item"] == "附录G字段结构"),
        "quality_status": quality["status"],
        "mix_sampling": {key: mix[key] for key in ("count", "status", "unit_checks", "output")},
        "bulk_density_sampling": {key: density[key] for key in ("count", "status", "rule", "output")},
        "sampling_attribute_readback": {"mix_records": mix_records, "bulk_density_records": density_records},
        "sampling_design_spatial_qa": {
            "status": sampling_qa["status"],
            "unit_checks": sampling_qa["unit_checks"],
            "parcel_checks": sampling_qa["parcel_checks"],
            "spacing_violations": sampling_qa["spacing_violations"],
            "spatial_relation_failures": sampling_qa["spatial_relation_failures"],
            "output_json": sampling_qa["output_json"],
        },
        "rtk_intake": {"source_sha256": expected_hash, "vertical_reference_status": rtk["vertical_reference_status"], "attribute_readback": rtk_attribute_readback, "raw_points": rtk["raw_points"], "projected_points": rtk["projected_points"]},
        "delivery_check_workspace_restored": arcpy.env.workspace == previous_workspace,
        "single_date_thematic_raster": {
            "status": thematic_result["status"],
            "unit_summary": thematic_unit,
            "source_sha256_before": source_hash_before,
            "source_sha256_after": source_hash_after,
            "source_unchanged_by_workflow": source_hash_before == source_hash_after,
            "inherited_output_crs_tested": 4326,
            "caller_output_crs_restored": True,
            "cleanup_warnings": thematic_result["cleanup_warnings"],
            "outputs": thematic_result["outputs"],
        },
        "scope": "全部为合成数据与设计点；不代表真实项目验收或实际采样。",
    }
    (out_dir / "smoke-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: arcgispro_smoke.py <new-output-directory>")
    print(json.dumps(run(Path(sys.argv[1])), ensure_ascii=False, indent=2))
