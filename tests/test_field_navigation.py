"""外业导航文件导出（KML/GPX/CSV）；系统 Python 3.12 可运行，不依赖 arcpy。

金丝雀：已知点 (北纬 22.5°, 东经 108.5°) 必须原样出现在三种文件里，
越界或缺失的坐标必须报错而不是写空。
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET

import pytest

from acceptance import field_navigation as N

KNOWN = [{"point_id": "U1-01", "lon": 108.5, "lat": 22.5, "alt": 95.2, "parcel": "P1", "unit": "U1"},
         {"point_id": "U1-02", "lon": 108.5009, "lat": 22.5009, "alt": "", "parcel": "P1", "unit": "U1"}]


def _root(path):
    return ET.parse(str(path)).getroot()


def test_kml_roundtrip_keeps_known_coordinate_and_ids(tmp_path):
    path = tmp_path / "设计点.kml"
    assert N.write_kml(KNOWN, path) == 2
    root = _root(path)
    ns = "{http://www.opengis.net/kml/2.2}"
    placemarks = root.findall(".//" + ns + "Placemark")
    assert [p.find(ns + "name").text for p in placemarks] == ["U1-01", "U1-02"]
    coords = placemarks[0].find(ns + "Point").find(ns + "coordinates").text.split(",")
    assert float(coords[0]) == pytest.approx(108.5, abs=1e-8)
    assert float(coords[1]) == pytest.approx(22.5, abs=1e-8)
    assert float(coords[2]) == pytest.approx(95.2)
    assert "未实采" in placemarks[0].find(ns + "description").text


def test_gpx_roundtrip_keeps_known_coordinate_and_ids(tmp_path):
    path = tmp_path / "设计点.gpx"
    assert N.write_gpx(KNOWN, path) == 2
    waypoints = _root(path).findall("{http://www.topografix.com/GPX/1/1}wpt")
    assert [w.find("{http://www.topografix.com/GPX/1/1}name").text for w in waypoints] == ["U1-01", "U1-02"]
    assert float(waypoints[0].get("lat")) == pytest.approx(22.5, abs=1e-8)
    assert float(waypoints[0].get("lon")) == pytest.approx(108.5, abs=1e-8)
    assert waypoints[1].find("{http://www.topografix.com/GPX/1/1}ele") is None


def test_csv_roundtrip_uses_wgs84_columns(tmp_path):
    path = tmp_path / "设计点定位.csv"
    assert N.write_csv(KNOWN, path) == 2
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert list(rows[0]) == N.CSV_COLUMNS
    assert float(rows[0]["LON_WGS84"]) == pytest.approx(108.5, abs=1e-8)
    assert float(rows[0]["LAT_WGS84"]) == pytest.approx(22.5, abs=1e-8)
    assert rows[0]["图斑编号"] == "P1" and rows[1]["ALT_M"] == ""


def test_out_of_range_coordinate_is_rejected_for_every_format(tmp_path):
    bad = [{"point_id": "X-01", "lon": 210.0, "lat": 22.5}]
    for writer, name in ((N.write_kml, "a.kml"), (N.write_gpx, "a.gpx"), (N.write_csv, "a.csv")):
        with pytest.raises(ValueError, match="越界"):
            writer(bad, tmp_path / name)
        assert not (tmp_path / name).exists()


def test_missing_or_non_numeric_coordinate_is_rejected_not_skipped():
    with pytest.raises(ValueError, match="不是有效数字"):
        N.validate({"point_id": "X-01", "lon": "", "lat": 22.5})
    with pytest.raises(ValueError, match="不是有限值"):
        N.validate({"point_id": "X-02", "lon": 108.5, "lat": float("nan")})
    with pytest.raises(ValueError, match="POINT_ID"):
        N.validate({"point_id": " ", "lon": 108.5, "lat": 22.5})


def test_empty_point_list_writes_valid_empty_file(tmp_path):
    assert N.write_kml([], tmp_path / "e.kml") == 0
    assert N.write_gpx([], tmp_path / "e.gpx") == 0
