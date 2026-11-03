"""设计点导航导出：KML 2.2、GPX 1.1 与 WGS84 定位 CSV。

纯函数部分（校验与写文件）在系统 Python 下可测；ArcPy 读取与投影在
load_design_points 里，需在 ArcGIS Pro Python 下运行。
导出的是设计候选，不是实采位置。
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

KML_NS = "http://www.opengis.net/kml/2.2"
GPX_NS = "http://www.topografix.com/GPX/1/1"
CSV_COLUMNS = ["POINT_ID", "LON_WGS84", "LAT_WGS84", "ALT_M", "图斑编号", "评价单元"]


def _number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("{}不是有效数字：{!r}".format(label, value)) from None
    if not result == result or result in (float("inf"), float("-inf")):
        raise ValueError("{}不是有限值：{!r}".format(label, value))
    return result


def validate(record):
    """逐点校验；越界或缺失直接报错，不静默跳过。"""
    lon = _number(record.get("lon"), record.get("point_id", "?") + " 经度")
    lat = _number(record.get("lat"), record.get("point_id", "?") + " 纬度")
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise ValueError("{}经纬度越界：{}, {}".format(record.get("point_id"), lon, lat))
    if not str(record.get("point_id") or "").strip():
        raise ValueError("设计点缺少 POINT_ID，无法作为导航目标")
    altitude = record.get("alt")
    return {"point_id": str(record["point_id"]).strip(), "lon": lon, "lat": lat,
            "alt": _number(altitude, record["point_id"] + " 海拔") if altitude not in (None, "") else None,
            "parcel": str(record.get("parcel") or ""), "unit": str(record.get("unit") or "")}


def write_kml(records, path, name="设计采样点"):
    rows = [validate(r) for r in records]
    root = ET.Element("kml", {"xmlns": KML_NS})
    document = ET.SubElement(root, "Document")
    ET.SubElement(document, "name").text = name
    for row in rows:
        placemark = ET.SubElement(document, "Placemark")
        ET.SubElement(placemark, "name").text = row["point_id"]
        description = "图斑：{}；评价单元：{}；状态：设计候选，未实采".format(
            row["parcel"] or "未关联", row["unit"] or "未关联")
        ET.SubElement(placemark, "description").text = description
        point = ET.SubElement(placemark, "Point")
        altitude = "" if row["alt"] is None else "{}".format(row["alt"])
        ET.SubElement(point, "coordinates").text = "{:.8f},{:.8f},{}".format(row["lon"], row["lat"], altitude)
    ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)
    return len(rows)


def write_gpx(records, path, name="设计采样点"):
    rows = [validate(r) for r in records]
    root = ET.Element("gpx", {"version": "1.1", "creator": "land_survey_toolkit", "xmlns": GPX_NS})
    ET.SubElement(root, "metadata").append(_text_node("name", name))
    for row in rows:
        waypoint = ET.SubElement(root, "wpt", {"lat": "{:.8f}".format(row["lat"]),
                                               "lon": "{:.8f}".format(row["lon"])})
        if row["alt"] is not None:
            ET.SubElement(waypoint, "ele").text = "{}".format(row["alt"])
        ET.SubElement(waypoint, "name").text = row["point_id"]
        ET.SubElement(waypoint, "desc").text = "图斑 {}；设计候选，未实采".format(row["parcel"] or "未关联")
    ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)
    return len(rows)


def _text_node(tag, text):
    node = ET.Element(tag)
    node.text = text
    return node


def write_csv(records, path):
    rows = [validate(r) for r in records]
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([row["point_id"], "{:.8f}".format(row["lon"]), "{:.8f}".format(row["lat"]),
                             "" if row["alt"] is None else row["alt"], row["parcel"], row["unit"]])
    return len(rows)


def load_design_points(source, arcpy):
    """从设计点图层读取 POINT_ID/图斑/评价单元/海拔并投影到 WGS84（EPSG:4326）。

    坐标系未知、几何为空、投影失败都直接报错；不跳过、不猜坐标。
    """
    sr = arcpy.Describe(source).spatialReference
    if str(getattr(sr, "name", "Unknown")) == "Unknown":
        raise ValueError("设计点图层坐标系未知，无法生成 WGS84 导航文件")
    available = {field.name for field in arcpy.ListFields(source)}
    required = {"POINT_ID", "DKBH", "PJDYBH"}
    missing = sorted(required - available)
    if missing:
        raise ValueError("设计点图层缺少字段：" + "、".join(missing))
    altitude_field = "Z_DEM_M" if "Z_DEM_M" in available else None
    columns = ["POINT_ID", "DKBH", "PJDYBH", "SHAPE@"] + ([altitude_field] if altitude_field else [])
    wgs84 = arcpy.SpatialReference(4326)
    records = []
    with arcpy.da.SearchCursor(source, columns) as cursor:
        for row in cursor:
            point_id, parcel, unit, geometry = row[:4]
            if geometry is None:
                raise ValueError("设计点 {} 几何为空".format(point_id))
            lonlat = geometry.projectAs(wgs84).firstPoint
            records.append({"point_id": point_id, "lon": lonlat.X, "lat": lonlat.Y,
                            "alt": row[4] if altitude_field else None,
                            "parcel": parcel, "unit": unit})
    return records


def write_all(records, out_dir):
    """在 out_dir 写 设计点导航.kml / .gpx / 设计点定位.csv；已存在则拒绝覆盖。"""
    out_dir = Path(out_dir)
    targets = {"kml": out_dir / "设计点导航.kml", "gpx": out_dir / "设计点导航.gpx",
               "csv": out_dir / "设计点定位.csv"}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise FileExistsError("导航文件已存在，不覆盖：" + "、".join(existing))
    out_dir.mkdir(parents=True, exist_ok=True)
    return {"kml": write_kml(records, targets["kml"]), "gpx": write_gpx(records, targets["gpx"]),
            "csv": write_csv(records, targets["csv"]), "files": {k: str(v) for k, v in targets.items()}}
