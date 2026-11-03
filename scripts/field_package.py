"""把采样设计导出为外业采集包：GeoPackage + 清单 + 采集说明 + KML/GPX/定位CSV。

用法（ArcGIS Pro Python）：
    python scripts/field_package.py --sampling-gdb <采样成果.gdb> --out <新目录>

只复制设计点，不改源库；采集字段留空，由外业填写或用回传工具回填。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
from pathlib import Path

import arcpy

LAYERS = {
    "设计采样点": ("设计点", ["COLLECTED", "COLLECT_TIME", "GPS_LON", "GPS_LAT", "ALT",
                          "ACC_M", "PHOTO_COUNT", "PHOTO", "SAMPLE_TYPE", "OPERATOR"]),
    "容重平行样_设计": ("容重点", ["COLLECTED", "COLLECT_TIME", "GPS_LON", "GPS_LAT", "ALT",
                              "ACC_M", "PHOTO_COUNT", "PHOTO", "OPERATOR"]),
    "质地构型调查_候选": ("质地构型", ["COLLECTED", "COLLECT_TIME", "GPS_LON", "GPS_LAT", "ALT",
                                 "ACC_M", "PHOTO_COUNT", "PHOTO", "OPERATOR"]),
}
FIELD_SPEC = {
    "COLLECTED": ("SHORT", None),
    "COLLECT_TIME": ("TEXT", 19),
    "GPS_LON": ("DOUBLE", None),
    "GPS_LAT": ("DOUBLE", None),
    "ALT": ("DOUBLE", None),
    "ACC_M": ("DOUBLE", None),
    "PHOTO_COUNT": ("SHORT", None),
    "PHOTO": ("TEXT", 255),
    "SAMPLE_TYPE": ("TEXT", 20),
    "OPERATOR": ("TEXT", 50),
}


def _add_fields(layer: Path, fields: list[str]) -> list[str]:
    existing = {field.name for field in arcpy.ListFields(str(layer))}
    added = []
    for name in fields:
        if name in existing:
            continue
        kind, length = FIELD_SPEC[name]
        arcpy.management.AddField(str(layer), name, kind, field_length=length)
        added.append(name)
    if "COLLECTED" in fields:
        with arcpy.da.UpdateCursor(str(layer), ["COLLECTED"]) as cursor:
            for _row in cursor:
                cursor.updateRow([0])
    return added


def _write_csv(layer: Path, path: Path) -> int:
    fields = [field.name for field in arcpy.ListFields(str(layer))
              if field.type not in ("Geometry", "OID")]
    source_sr = arcpy.Describe(str(layer)).spatialReference
    wgs = arcpy.SpatialReference(4326)
    columns = fields + ["X", "Y", "LON", "LAT"]
    with arcpy.da.SearchCursor(str(layer), fields + ["SHAPE@XY"]) as cursor, \
            path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        count = 0
        for row in cursor:
            x, y = row[-1]
            try:
                point = arcpy.PointGeometry(arcpy.Point(x, y), source_sr).projectAs(wgs).centroid
            except Exception:  # noqa: BLE001 - 投影失败保留空值，不猜坐标
                point = None
            writer.writerow(list(row[:-1]) + [x, y,
                                              point.X if point else "", point.Y if point else ""])
            count += 1
    return count


def build(sampling_gdb: Path, out_dir: Path) -> dict:
    sampling_gdb, out_dir = Path(sampling_gdb).resolve(), Path(out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError("输出目录必须为空：" + str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "外业采集.gpkg"
    arcpy.management.CreateSQLiteDatabase(str(gpkg), "GEOPACKAGE")
    layers, counts = {}, {}
    for source_name, (target_name, extra_fields) in LAYERS.items():
        source = sampling_gdb / source_name
        if not arcpy.Exists(str(source)):
            continue
        target = gpkg / target_name
        arcpy.management.CopyFeatures(str(source), str(target))
        _add_fields(target, extra_fields)
        layers[target_name] = str(target)
        counts[target_name] = int(arcpy.management.GetCount(str(target))[0])
    design = Path(layers["设计点"])
    _write_csv(design, out_dir / "设计点清单.csv")
    from acceptance.field_navigation import load_design_points, write_all
    navigation = write_all(load_design_points(str(design), arcpy), out_dir)
    (out_dir / "采集说明.md").write_text(
        "# 外业采集包说明\n\n"
        "1. 本包只含设计点，不代表已采样；实采坐标、照片、样品号必须现场填写或回传。\n"
        "2. 照片硬要求（指导书 v1.1）：每单元全景不少于 4 张（东南西北）、近景不少于 2 张、"
        "现场工作照 1 张带水印；剖面/混合样/容重/侵入体按每分样点 1 张。\n"
        "3. 混合样编号＝评价单元编号；容重样编号＝RZ+评价单元编号，3 个平行样各自装袋。\n"
        "4. 回传后用 `scripts/photo_geotag.py` 落照片、`scripts/field_return_qa.py` 做回收质检。\n",
        encoding="utf-8")
    manifest = {
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_gdb": str(sampling_gdb),
        "gpkg": str(gpkg),
        "layers": layers,
        "counts": counts,
        "navigation": navigation,
        "note": "设计点未经现场核实；空字段须由外业填写，不得由工具补造。",
    }
    (out_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sampling-gdb", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = build(args.sampling_gdb, args.out)
    print(json.dumps(result["counts"], ensure_ascii=False))
    print("输出：" + result["gpkg"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
