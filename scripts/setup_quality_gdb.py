# -*- coding: utf-8 -*-
"""Create a blank Appendix G evaluation-unit FileGDB schema.

Run with ArcGIS Pro Python. The two transferred parcel feature classes are
intentionally not fabricated here; import them unchanged from the source GDB.
Without --epsg, the empty polygon feature class has an unknown spatial reference
and must not receive project data until the approved project CRS is assigned.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Optional

from delivery_package import APPENDIX_G
from acceptance.crs import parse_wkid, spatial_reference as resolve_spatial_reference


def create_quality_gdb(out_gdb: Path, epsg: Optional[int] = None) -> dict:
    import arcpy

    out_gdb = out_gdb.expanduser().resolve()
    if out_gdb.suffix.lower() != ".gdb":
        raise ValueError("输出路径必须以 .gdb 结尾")
    epsg = parse_wkid(epsg, "项目坐标系 WKID")
    manifest_path = out_gdb.parent / f"{out_gdb.stem}_setup.json"
    if arcpy.Exists(str(out_gdb)) or out_gdb.exists():
        raise FileExistsError(f"输出已存在，不覆盖：{out_gdb}")
    if manifest_path.exists():
        raise FileExistsError(f"建库清单已存在，不覆盖：{manifest_path}")
    spatial_reference = resolve_spatial_reference(arcpy, epsg, "项目坐标系 WKID")[1] if epsg else "#"

    out_gdb.parent.mkdir(parents=True, exist_ok=True)
    gdb_created = False
    manifest_created = False
    try:
        # The paths were confirmed absent above; any partial object after this point
        # belongs to this invocation, even if ArcPy raises during creation.
        gdb_created = True
        arcpy.management.CreateFileGDB(str(out_gdb.parent), out_gdb.name, "CURRENT")
        # ArcPy's default spatial_reference sentinel creates an empty FC with an
        # Unknown coordinate system; an uninitialized SpatialReference object fails.
        feature_class = str(out_gdb / "耕地质量等级评价评价单元图")
        arcpy.management.CreateFeatureclass(
            str(out_gdb), "耕地质量等级评价评价单元图", "POLYGON",
            has_m="DISABLED", has_z="DISABLED", spatial_reference=spatial_reference,
        )

        field_types = {
            "String": "TEXT", "Double": "DOUBLE", "Single": "FLOAT",
            "Integer": "LONG", "SmallInteger": "SHORT",
        }
        field_descriptions = []
        for name, alias, source_type, length in APPENDIX_G:
            field_descriptions.append([
                name, field_types[source_type], alias,
                length if source_type == "String" else "", "", "",
            ])
        arcpy.management.AddFields(feature_class, field_descriptions)

        info = {
            "template_type": "blank_schema_only",
            "source": "广西补充耕地质量验收作业指导书 v1.1 (2026-08-17), Appendix G",
            "geodatabase": str(out_gdb),
            "feature_class": "耕地质量等级评价评价单元图",
            "appendix_g_field_count": len(APPENDIX_G),
            "horizontal_factory_code": epsg or 0,
            "horizontal_crs_status": "set" if epsg else "unset_pending_project_source",
            "vertical_datum": "1985国家高程基准；涉及高程属性时须与项目源数据核实",
            "transferred_layers_to_import_unchanged": [
                "补充耕地国土变更图斑", "补充耕地质量鉴定图斑",
            ],
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        with manifest_path.open("x", encoding="utf-8") as stream:
            manifest_created = True
            json.dump(info, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return info
    except Exception as original_error:
        if manifest_created:
            manifest_path.unlink(missing_ok=True)
        if gdb_created:
            try:
                if out_gdb.exists() or arcpy.Exists(str(out_gdb)):
                    arcpy.management.Delete(str(out_gdb))
            except Exception as cleanup_error:
                raise RuntimeError("建库失败：{}；回滚 FileGDB 未完成：{}".format(
                    original_error, cleanup_error,
                )) from cleanup_error
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-gdb", required=True, help="新建的 .gdb 输出路径")
    parser.add_argument("--epsg", type=int, help="项目资料确认后的 CGCS2000 三度带 EPSG；未知时留空")
    args = parser.parse_args()
    try:
        result = create_quality_gdb(Path(args.out_gdb), args.epsg)
    except (ValueError, FileExistsError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
