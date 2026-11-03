"""高德行政区划区图：递归解析、polyline 分环、GCJ-02 转换、范围过滤与 arcpy 写库契约。

全部离线：网络函数 monkeypatch；arcpy 用桩注入；几何/解析为纯函数直接测试。
"""
from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from acceptance import district
from acceptance import gcj02
from acceptance import poi_reference

EXTENT = (108.40, 23.30, 108.75, 23.55)


def _fixture_payload():
    """结构真实的高德 config/district（extensions=all）返回片段。"""
    outer = "108.55,23.40;108.65,23.40;108.65,23.47;108.55,23.47;108.55,23.40"
    hole = "108.58,23.43;108.60,23.43;108.60,23.45;108.58,23.45;108.58,23.43"
    town = "108.57,23.41;108.59,23.41;108.59,23.43;108.57,23.43;108.57,23.41"
    return {
        "status": "1", "info": "OK", "infocode": "10000", "count": "1",
        "districts": [{
            "citycode": "0771", "adcode": "450000", "name": "广西壮族自治区",
            "center": "108.32,22.82", "level": "province", "polyline": "",
            "districts": [{
                "citycode": "0771", "adcode": "450100", "name": "南宁市",
                "center": "108.37,22.82", "level": "city", "polyline": "",
                "districts": [{
                    "citycode": "0771", "adcode": "450125", "name": "上林县",
                    "center": "108.602884,23.432063", "centroid": "108.60,23.43",
                    "level": "district", "polyline": outer + "|" + hole,
                    "districts": [
                        {"citycode": "0771", "adcode": "450125100", "name": "大丰镇",
                         "center": "108.61,23.44", "level": "street", "polyline": town},
                        {"citycode": "0771", "adcode": "450125101", "name": "明亮镇",
                         "center": "108.58,23.42", "level": "street", "polyline": ""},
                    ],
                }],
            }],
        }],
    }


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_parse_polyline_splits_rings_and_points():
    rings = district.parse_polyline("108.5,23.4;108.6,23.4;108.6,23.5|108.55,23.45;108.56,23.45;108.56,23.46")
    assert len(rings) == 2
    assert rings[0] == [(108.5, 23.4), (108.6, 23.4), (108.6, 23.5)]
    assert rings[1][0] == (108.55, 23.45)
    assert district.parse_polyline("") == []
    assert district.parse_polyline("[]") == []


@pytest.mark.parametrize("text", ["108.5;108.6,23.4", "108.5,abc;1,2;3,4", "nan,23.4;1,2;3,4",
                                  "200.0,23.4;1,2;3,4"])
def test_parse_polyline_rejects_malformed_coordinates(text):
    with pytest.raises(ValueError):
        district.parse_polyline(text)


def test_parse_center_returns_none_on_missing_or_invalid():
    assert district.parse_center("108.60,23.43") == (108.60, 23.43)
    for value in ("", "[]", "108.6", "a,b", "200.0,23.0"):
        assert district.parse_center(value) is None


# ---------------------------------------------------------------------------
# 范围过滤
# ---------------------------------------------------------------------------

def test_ring_intersects_rect_covers_containment_and_crossing():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert district.ring_intersects_rect(ring, (1, 1, 2, 2))       # 环包住矩形
    assert district.ring_intersects_rect(ring, (-5, -5, 5, 5))     # 部分相交
    assert district.ring_intersects_rect(ring, (10, 10, 20, 20))   # 只碰角点
    assert not district.ring_intersects_rect(ring, (20, 20, 30, 30))
    # bbox 有重叠但实际不相交：三角形斜边不进入矩形
    triangle = [(0, 30), (30, 30), (30, 0)]
    assert not district.ring_intersects_rect(triangle, (0, 0, 10, 10))


def test_padding_expands_query_extent_by_ten_percent():
    padded = district._pad_rect((108.0, 22.0, 109.0, 23.0), 0.10)
    assert padded == pytest.approx((107.9, 21.9, 109.1, 23.1))


# ---------------------------------------------------------------------------
# 递归解析 + GCJ-02 转换
# ---------------------------------------------------------------------------

def test_parse_district_payload_collects_county_and_converts_gcj02():
    parsed = district.parse_district_payload(_fixture_payload(), "县", EXTENT)
    assert len(parsed["nodes"]) == 1
    node = parsed["nodes"][0]
    assert node["adcode"] == "450125" and node["name"] == "上林县"
    assert node["level"] == "district"
    assert len(node["rings_gcj"]) == 2 and len(node["rings_wgs"]) == 2
    # 原始 GCJ-02 保留，WGS84 逐点反算且与 gcj02 模块一致。
    expected_lon, expected_lat = gcj02.gcj02_to_wgs84(108.55, 23.40)
    assert node["rings_wgs"][0][0] == pytest.approx((expected_lon, expected_lat))
    assert node["rings_gcj"][0][0] == (108.55, 23.40)
    expected_center = gcj02.gcj02_to_wgs84(108.602884, 23.432063)
    assert node["center_wgs"] == pytest.approx(expected_center)
    assert node["center_wgs"][0] < node["center_gcj"][0]  # 高德坐标整体西偏


def test_parse_district_payload_extracts_street_level():
    parsed = district.parse_district_payload(_fixture_payload(), "乡", EXTENT)
    names = [node["name"] for node in parsed["nodes"]]
    assert names == ["大丰镇", "明亮镇"]
    assert all(node["level"] == "street" for node in parsed["nodes"])
    assert parsed["target_level"] == "street"


def test_parse_district_payload_filters_by_extent_and_records_skipped():
    parsed = district.parse_district_payload(_fixture_payload(), "县", (100.0, 30.0, 101.0, 31.0))
    assert parsed["nodes"] == []
    assert parsed["skipped"] and parsed["skipped"][0]["name"] == "上林县"


def test_parse_district_payload_uses_ten_percent_padding():
    # 中心点落在给定范围外、但落在 10% 外扩范围内 → 保留；再外移则剔除。
    center_wgs = gcj02.gcj02_to_wgs84(108.75, 23.44)
    payload = {"districts": [{"name": "边界乡", "adcode": "450125200", "level": "street",
                              "center": "108.75,23.44", "polyline": ""}]}
    inside_extent = (center_wgs[0] - 0.20, center_wgs[1] - 0.10, center_wgs[0] - 0.01, center_wgs[1] + 0.10)
    inside = district.parse_district_payload(payload, "乡", inside_extent)
    assert [node["name"] for node in inside["nodes"]] == ["边界乡"]
    outside_extent = (center_wgs[0] - 0.20, center_wgs[1] - 0.10, center_wgs[0] - 0.06, center_wgs[1] + 0.10)
    outside = district.parse_district_payload(payload, "乡", outside_extent)
    assert outside["nodes"] == []


def test_parse_district_payload_deduplicates_by_adcode_and_rejects_bad_level():
    payload = _fixture_payload()
    duplicated = json.loads(json.dumps(payload))
    duplicated["districts"][0]["districts"][0]["districts"].append(
        dict(duplicated["districts"][0]["districts"][0]["districts"][0]))
    parsed = district.parse_district_payload(duplicated, "县", EXTENT)
    assert len(parsed["nodes"]) == 1
    with pytest.raises(ValueError, match="县|乡|level"):
        district.parse_district_payload(payload, "省", EXTENT)


# ---------------------------------------------------------------------------
# 环语义与 arcpy 几何
# ---------------------------------------------------------------------------

def test_split_rings_separates_holes_from_outer_rings():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(2, 2), (4, 2), (4, 4), (2, 4)]
    island = [(20, 20), (23, 20), (23, 23), (20, 23)]
    parts = district.split_rings([hole, island, outer])
    assert len(parts) == 2
    assert parts[0][0] == outer and parts[0][1] == [hole]


def _fake_arcpy_geometry():
    class FakeArray(list):
        pass

    class FakePolygon:
        def __init__(self, rings, spatial_reference=None):
            self.rings = rings
            self.spatial_reference = spatial_reference

    return SimpleNamespace(
        Array=lambda items: FakeArray(items),
        Point=lambda x, y: (x, y),
        Polygon=FakePolygon,
    )


def test_polygon_geometry_uses_arcgis_ring_orientation():
    arcpy = _fake_arcpy_geometry()
    outer_ccw = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole_ccw = [(2, 2), (4, 2), (4, 4), (2, 4)]
    geometry = district.polygon_geometry(arcpy, [outer_ccw, hole_ccw], "wgs84")
    assert len(geometry.rings) == 2
    assert district._signed_area(geometry.rings[0]) < 0   # 外环顺时针
    assert district._signed_area(geometry.rings[1]) > 0   # 内环逆时针
    with pytest.raises(ValueError, match="环"):
        district.polygon_geometry(arcpy, [], "wgs84")


# ---------------------------------------------------------------------------
# write_gdb（arcpy 桩）
# ---------------------------------------------------------------------------

class _FakeInsertCursor:
    instances = []

    def __init__(self, feature_class, fields):
        self.feature_class = feature_class
        self.fields = fields
        self.rows = []
        _FakeInsertCursor.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def insertRow(self, row):
        self.rows.append(row)


def _fake_arcpy_writer():
    _FakeInsertCursor.instances = []
    calls = {"create_gdb": [], "create_fc": [], "add_field": [], "project": [], "delete": [],
             "copy": []}

    class SpatialReference:
        def __init__(self, wkid):
            self.wkid = wkid

    class FakeArray(list):
        pass

    class FakePolygon:
        def __init__(self, rings, spatial_reference=None):
            self.rings = rings
            self.spatial_reference = spatial_reference

    class FakePointGeometry:
        def __init__(self, point, spatial_reference=None):
            self.point = point
            self.spatial_reference = spatial_reference

    class Management:
        @staticmethod
        def CreateFileGDB(parent, name):
            calls["create_gdb"].append((parent, name))

        @staticmethod
        def CreateFeatureclass(workspace, name, shape_type, spatial_reference=None):
            calls["create_fc"].append((name, shape_type, spatial_reference))

        @staticmethod
        def AddField(feature_class, field, kind, **options):
            calls["add_field"].append((feature_class, field, kind, options))

        @staticmethod
        def Project(source, target, spatial_reference):
            calls["project"].append((source, target, spatial_reference))

        @staticmethod
        def CopyFeatures(source, target):
            calls["copy"].append((source, target))

        @staticmethod
        def Delete(target):
            calls["delete"].append(target)

    return SimpleNamespace(
        Exists=lambda _path: False,
        SpatialReference=SpatialReference,
        Array=lambda items: FakeArray(items),
        Point=lambda x, y: (x, y),
        Polygon=FakePolygon,
        PointGeometry=FakePointGeometry,
        management=Management,
        da=SimpleNamespace(InsertCursor=_FakeInsertCursor),
    ), calls


def test_write_gdb_creates_two_feature_classes_projects_and_reports_counts(tmp_path):
    arcpy, calls = _fake_arcpy_writer()
    nodes = district.parse_district_payload(_fixture_payload(), "县", EXTENT)["nodes"]
    result = district.write_gdb(arcpy, str(tmp_path / "区图.gdb"), "上林", 4525, nodes)

    assert calls["create_gdb"] == [(str(tmp_path), "区图.gdb")]
    created = {name: (shape_type, sr.wkid) for name, shape_type, sr in calls["create_fc"]}
    assert created["上林_区界_WGS84"] == ("POLYGON", 4326)
    assert created["上林_驻地_WGS84"] == ("POINT", 4326)
    fields = {field for _fc, field, _kind, _options in calls["add_field"]}
    assert {"ADCODE", "NAME", "LEVEL", "CENTER_LON", "CENTER_LAT", "SOURCE"} == fields
    assert len(calls["project"]) == 2
    assert all(spatial_reference.wkid == 4525 for _s, _t, spatial_reference in calls["project"])
    assert len(calls["delete"]) == 2

    cursors = {cursor.feature_class: cursor for cursor in _FakeInsertCursor.instances}
    boundary = cursors[str(tmp_path / "区图.gdb" / "上林_区界_WGS84")]
    labels = cursors[str(tmp_path / "区图.gdb" / "上林_驻地_WGS84")]
    assert len(boundary.rows) == 1 and len(labels.rows) == 1
    row = boundary.rows[0]
    assert row[1] == "450125" and row[2] == "上林县" and row[3] == "区县"
    assert row[4] == pytest.approx(gcj02.gcj02_to_wgs84(108.602884, 23.432063)[0])
    assert row[6] == district.SOURCE_TEXT
    assert result["feature_count"] == 1 and result["label_count"] == 1
    assert result["boundary_fc"].endswith("上林_区界")
    assert result["label_fc"].endswith("上林_驻地")


def test_write_gdb_rejects_existing_feature_names(tmp_path):
    arcpy, calls = _fake_arcpy_writer()
    arcpy.Exists = lambda path: "上林_" in str(path)
    nodes = district.parse_district_payload(_fixture_payload(), "县", EXTENT)["nodes"]
    with pytest.raises(ValueError, match="已存在"):
        district.write_gdb(arcpy, str(tmp_path / "区图.gdb"), "上林", 4525, nodes)
    assert calls["create_fc"] == []


def test_write_gdb_requires_gdb_suffix(tmp_path):
    arcpy, _calls = _fake_arcpy_writer()
    with pytest.raises(ValueError, match="gdb"):
        district.write_gdb(arcpy, str(tmp_path / "not_a_gdb"), "上林", 4525, [])


# ---------------------------------------------------------------------------
# CLI / run 契约
# ---------------------------------------------------------------------------

def _argv(tmp_path, *extra):
    return ["--out-gdb", str(tmp_path / "区图.gdb"), "--name", "上林", "--wkid", "4525",
            "--extent", "108.40", "23.30", "108.75", "23.55"] + list(extra)


def test_run_uses_reverse_lookup_when_keywords_missing(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(district, "_amap_key", lambda: "test-key")

    def fake_reverse(lon, lat, key):
        calls["regeo"] = (lon, lat, key)
        return "450125"

    def fake_query(key, keywords, subdistrict):
        calls["district"] = (key, keywords, subdistrict)
        return _fixture_payload()

    monkeypatch.setattr(district, "reverse_adcode", fake_reverse)
    monkeypatch.setattr(district, "query_district", fake_query)
    written = {}

    def fake_write(arcpy_module, out_gdb, name, target_wkid, nodes):
        written.update({"out_gdb": out_gdb, "name": name, "wkid": target_wkid, "nodes": nodes})
        return {"gdb": out_gdb, "boundary_fc": out_gdb + "\\" + name + "_区界",
                "label_fc": out_gdb + "\\" + name + "_驻地",
                "feature_count": len(nodes), "label_count": len(nodes)}

    monkeypatch.setattr(district, "write_gdb", fake_write)
    monkeypatch.setitem(sys.modules, "arcpy", SimpleNamespace())
    args = argparse.Namespace(
        out_gdb=str(tmp_path / "区图.gdb"), name="上林", wkid=4525,
        extent=[108.40, 23.30, 108.75, 23.55], keywords="", subdistrict=2, level="县")
    result = district.run(args)

    expected_gcj_lon, expected_gcj_lat = gcj02.wgs84_to_gcj02(108.575, 23.425)
    assert calls["regeo"][0] == pytest.approx(expected_gcj_lon)
    assert calls["regeo"][1] == pytest.approx(expected_gcj_lat)
    assert calls["regeo"][2] == "test-key"
    # 负向对照：不能把 WGS84 中心直接传给高德 regeo（边界附近会取错 adcode）。
    assert abs(calls["regeo"][0] - 108.575) > 0.003
    assert abs(calls["regeo"][1] - 23.425) > 0.001
    assert calls["district"] == ("test-key", "450125", 2)
    assert result["requested_level"] == "县"
    assert written["wkid"] == 4525 and written["name"] == "上林"
    assert result["status"] == "OK"
    assert result["feature_count"] == 1 and result["label_count"] == 1
    assert result["levels"] == ["区县"]
    assert result["adcodes"] == ["450125"]
    assert result["source"] == "高德行政区划"
    assert result["coordinate"] == "GCJ-02→WGS84→目标CRS"
    assert result["legal_status"] == "REFERENCE_ONLY_NOT_LEGAL_BOUNDARY"
    assert "三调" in result["note"] and "七参数" in result["datum_note"]


def test_cli_success_prints_json_and_exit_zero(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(district, "_amap_key", lambda: "k")
    monkeypatch.setattr(district, "query_district", lambda *args: _fixture_payload())
    monkeypatch.setattr(district, "write_gdb", lambda *args: {
        "gdb": args[1], "boundary_fc": args[1] + "\\上林_区界", "label_fc": args[1] + "\\上林_驻地",
        "feature_count": 1, "label_count": 1})
    monkeypatch.setitem(sys.modules, "arcpy", SimpleNamespace())
    code = district.main(_argv(tmp_path, "--keywords", "上林县"))
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "OK"
    assert {"gdb", "boundary_fc", "label_fc", "feature_count", "levels", "adcodes",
            "source", "coordinate", "legal_status", "note"} <= set(payload)


def test_cli_missing_key_is_json_fail_without_network(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(district, "_amap_key", lambda: (_ for _ in ()).throw(
        ValueError("尚未配置高德 Web 服务 Key；请先点击“配置高德Key”。")))
    code = district.main(_argv(tmp_path))
    assert code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "FAIL" and "高德" in payload["error"]


@pytest.mark.parametrize("argv,message", [
    (["--out-gdb", "x.gdb", "--name", "上林", "--wkid", "4525",
      "--extent", "108.75", "23.30", "108.40", "23.55"], "extent"),
    (["--out-gdb", "x.gdb", "--name", "上/林", "--wkid", "4525",
      "--extent", "108.40", "23.30", "108.75", "23.55"], "name"),
    (["--out-gdb", "x.gdb", "--name", "上林", "--wkid", "4525",
      "--extent", "108.40", "23.30", "108.75", "23.55", "--level", "省"], "level"),
])
def test_cli_invalid_arguments_are_json_fail(monkeypatch, capsys, argv, message):
    monkeypatch.setattr(district, "_amap_key", lambda: "k")
    monkeypatch.setitem(sys.modules, "arcpy", SimpleNamespace())
    code = district.main(argv)
    assert code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "FAIL"
    assert message in payload["error"]


def test_public_key_loader_is_a_thin_wrapper(monkeypatch):
    monkeypatch.setattr(poi_reference, "_load_amap_key", lambda: "secret-value")
    assert poi_reference.load_amap_key() == "secret-value"


# ---------------------------------------------------------------------------
# 县乡两级 / NO_BOUNDARY / 临时要素类清理
# ---------------------------------------------------------------------------

def _install_run(monkeypatch, payload=None):
    monkeypatch.setattr(district, "_amap_key", lambda: "k")
    monkeypatch.setattr(district, "query_district", lambda *args: payload or _fixture_payload())
    monkeypatch.setitem(sys.modules, "arcpy", SimpleNamespace())
    recorded = {}

    def fake_write(arcpy_module, out_gdb, name, target_wkid, nodes):
        recorded["nodes"] = nodes
        return {"gdb": out_gdb, "boundary_fc": out_gdb + "\\界", "label_fc": out_gdb + "\\点",
                "feature_count": sum(1 for node in nodes if node.get("rings_wgs")),
                "label_count": sum(1 for node in nodes if node.get("center_wgs") is not None)}

    monkeypatch.setattr(district, "write_gdb", fake_write)
    return recorded


def _last_json(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _center_only_payload():
    return {"districts": [{"name": "只有驻地乡", "adcode": "450125200", "level": "street",
                           "center": "108.75,23.44", "polyline": ""}]}


def test_parse_district_payload_county_township_collects_both_levels():
    parsed = district.parse_district_payload(_fixture_payload(), "县乡", EXTENT)
    assert [node["name"] for node in parsed["nodes"]] == ["上林县", "大丰镇", "明亮镇"]
    assert {node["level"] for node in parsed["nodes"]} == {"district", "street"}
    assert parsed["target_level"] == "district+street"


def test_cli_default_county_township_outputs_both_levels(monkeypatch, capsys, tmp_path):
    recorded = _install_run(monkeypatch)
    code = district.main(_argv(tmp_path, "--keywords", "上林县"))
    assert code == 0
    payload = _last_json(capsys)
    assert payload["status"] == "OK"
    assert payload["requested_level"] == "县乡"
    assert payload["levels"] == ["区县", "乡镇/街道"]
    assert payload["feature_count"] == 2   # 县界 + 大丰镇界（明亮镇无 polyline）
    assert payload["label_count"] == 3
    assert {node["level"] for node in recorded["nodes"]} == {"district", "street"}


@pytest.mark.parametrize("level,levels,features,labels", [
    ("县", ["区县"], 1, 1),
    ("乡", ["乡镇/街道"], 1, 2),
    ("县乡", ["区县", "乡镇/街道"], 2, 3),
])
def test_cli_level_filter_and_feature_counts(monkeypatch, capsys, tmp_path, level, levels, features, labels):
    recorded = _install_run(monkeypatch)
    code = district.main(_argv(tmp_path, "--keywords", "上林县", "--level", level))
    assert code == 0
    payload = _last_json(capsys)
    assert payload["levels"] == levels
    assert payload["feature_count"] == features and payload["label_count"] == labels
    assert payload["requested_level"] == level
    assert len(recorded["nodes"]) == labels


def test_center_only_result_is_no_boundary_failure(monkeypatch, capsys, tmp_path):
    _install_run(monkeypatch, payload=_center_only_payload())
    monkeypatch.setattr(district, "write_gdb", lambda *args: pytest.fail("没有界线时不应写库"))
    code = district.main(_argv(tmp_path, "--keywords", "上林县", "--level", "乡"))
    assert code == 1
    payload = _last_json(capsys)
    assert payload["status"] == "FAIL" and "NO_BOUNDARY" in payload["error"]


def test_write_gdb_rejects_center_only_nodes(tmp_path):
    arcpy, calls = _fake_arcpy_writer()
    nodes = [{"adcode": "450125200", "name": "只有驻地乡", "level": "street",
              "center_gcj": None, "center_wgs": (108.74, 23.44),
              "rings_gcj": [], "rings_wgs": []}]
    with pytest.raises(ValueError, match="NO_BOUNDARY"):
        district.write_gdb(arcpy, str(tmp_path / "区图.gdb"), "上林", 4525, nodes)
    assert calls["create_gdb"] == [] and calls["create_fc"] == []


def test_write_gdb_cleans_temp_feature_class_when_label_insert_fails(tmp_path):
    """P2-18：驻地插入失败时也必须删掉已建的 WGS84 临时界线类，主错误不被清理覆盖。"""
    arcpy, calls = _fake_arcpy_writer()

    class FailingCursor:
        def __init__(self, feature_class, fields):
            self.feature_class = feature_class

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def insertRow(self, row):
            if "驻地" in self.feature_class:
                raise RuntimeError("synthetic label insert failure")

    arcpy.da = SimpleNamespace(InsertCursor=FailingCursor)
    nodes = district.parse_district_payload(_fixture_payload(), "县", EXTENT)["nodes"]
    with pytest.raises(ValueError) as excinfo:
        district.write_gdb(arcpy, str(tmp_path / "区图.gdb"), "上林", 4525, nodes)
    assert "写入或投影" in str(excinfo.value)
    assert "synthetic label insert failure" in str(excinfo.value)
    deleted = [str(path) for path in calls["delete"]]
    assert any("上林_区界_WGS84" in path for path in deleted)
    assert any("上林_驻地_WGS84" in path for path in deleted)
    assert not any(path.endswith("上林_区界") for path in deleted)
