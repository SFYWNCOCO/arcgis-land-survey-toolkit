"""网格法集成测试：需在 ArcGIS Pro Python 下运行。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

arcpy = pytest.importorskip("arcpy")

from acceptance import grid_sampling as G  # noqa: E402


def _square(x, y, w, h, sr):
    ring = arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + w, y),
                        arcpy.Point(x + w, y + h), arcpy.Point(x, y + h),
                        arcpy.Point(x, y)])
    return arcpy.Polygon(ring, sr)


class _DistancePoint:
    def __init__(self, x):
        self.x = x

    def angleAndDistanceTo(self, other, method):
        assert method == "GEODESIC"
        return 0.0, abs(self.x - other.x)


class _PickPointsArcpy:
    @staticmethod
    def PointGeometry(point, _sr):
        return point


@pytest.fixture()
def gdb(tmp_path):
    path = str(tmp_path / "t.gdb")
    arcpy.management.CreateFileGDB(str(tmp_path), "t.gdb")
    return path


def _make_parcels(gdb, sr, rows):
    fc = f"{gdb}/parcels"
    arcpy.management.CreateFeatureclass(gdb, "parcels", "POLYGON", spatial_reference=sr)
    for name in ("PJDYBH", "DKBH"):
        arcpy.management.AddField(fc, name, "TEXT", field_length=30)
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "PJDYBH", "DKBH"]) as cur:
        for uid, did, geom in rows:
            cur.insertRow([geom, uid, did])
    return fc


def test_grid_design_covers_every_parcel_in_a_multi_parcel_unit(gdb):
    sr = arcpy.SpatialReference(4524)
    rows = [
        ("U1", "P1", _square(37377500, 2712500, 120, 80, sr)),
        ("U1", "P2", _square(37377620, 2712500, 40, 30, sr)),
        ("U1", "P3", _square(37377500, 2712580, 30, 20, sr)),
    ]
    fc = _make_parcels(gdb, sr, rows)
    report = G.grid_design(fc, "PJDYBH", "DKBH", gdb, cell_size_m=20.0,
                           min_points=3, min_spacing_m=15.0, inset_m=5.0)
    assert report["counts"]["设计点"] >= 3
    assert report["counts"]["方格"] > 0 and report["counts"]["交点"] > 0
    grid_rows = list(arcpy.da.SearchCursor(
        report["grid"], ["IN_UNIT", "CELL_M2", "SHAPE@"]
    ))
    assert len(grid_rows) == 40  # 整个 160 m × 100 m 外包矩形的 20 m 方格
    assert all(shape.area == pytest.approx(400.0) for _, _, shape in grid_rows)
    assert any(in_unit == 0 and area == 0 for in_unit, area, _ in grid_rows)
    assert all((in_unit == 1) == (area > 0) for in_unit, area, _ in grid_rows)
    centers = list(arcpy.da.SearchCursor(report["center"], ["IN_UNIT", "SHAPE@XY"]))
    assert len(centers) == len(grid_rows)
    assert any(in_unit == 0 for in_unit, _ in centers)
    assert report["counts"]["交点"] == 54  # 外包矩形上的完整 9 × 6 节点阵列
    outside_nodes = []
    for (xy,) in arcpy.da.SearchCursor(report["node"], ["SHAPE@XY"]):
        point = arcpy.PointGeometry(arcpy.Point(*xy), sr)
        if not any(geom.contains(point) for _, _, geom in rows):
            outside_nodes.append(xy)
    assert outside_nodes
    unit = report["units"][0]
    assert unit["覆盖缺失图斑"] == []
    assert unit["间距达标"] is True
    assert report["spatial_reference_check"]["zone_width_degrees"] == 3
    assert report["spatial_reference_check"]["spacing_method"] == "GEODESIC"
    # 每个图斑至少一个点
    seen = {}
    with arcpy.da.SearchCursor(report["sample"], ["PJDYBH", "DKBH", "SHAPE@XY"]) as cur:
        points = list(cur)
    for uid, did, _ in points:
        assert uid == "U1"
        seen[did] = seen.get(did, 0) + 1
    assert set(seen) == {"P1", "P2", "P3"}
    # 同单元任意两点间距不小于 15 m
    geoms = [arcpy.PointGeometry(arcpy.Point(*p[2]), sr) for p in points]
    for i, a in enumerate(geoms):
        for b in geoms[i + 1:]:
            assert a.angleAndDistanceTo(b, "GEODESIC")[1] >= 15.0 - 1e-6
    # 采样点必须落在地块内
    for _, did, xy in points:
        geom = dict((p[1], p[2]) for p in [(r[0], r[1], r[2]) for r in rows])[did]
        assert geom.contains(arcpy.PointGeometry(arcpy.Point(*xy), sr))


def test_pick_points_uses_parcel_label_point_as_first_pick_reference():
    geometry = type("ParcelGeometry", (), {
        "area": 100.0,
        "centroid": _DistancePoint(0.0),
        "labelPoint": _DistancePoint(10.0),
    })()
    candidates = [
        {"parcel": "P1", "pt": _DistancePoint(1.0), "cell_no": 1},
        {"parcel": "P1", "pt": _DistancePoint(9.0), "cell_no": 2},
    ]

    chosen, spacing_ok, notes = G._pick_points(
        _PickPointsArcpy(), candidates, [{"parcel": "P1", "geom": geometry}],
        unit_geom=None, min_points=1, min_spacing=15.0, inset_m=0.0, sr=None,
    )

    assert chosen == [candidates[1]]
    assert spacing_ok is True
    assert notes == []


def test_grid_design_rejects_geographic_crs(gdb):
    sr = arcpy.SpatialReference(4326)
    fc = _make_parcels(gdb, sr, [("U1", "P1", _square(108.0, 22.8, 0.001, 0.001, sr))])
    with pytest.raises(ValueError, match="投影坐标系"):
        G.grid_design(fc, "PJDYBH", "DKBH", gdb)


def test_grid_design_rejects_meter_based_utm_crs(gdb):
    sr = arcpy.SpatialReference(32649)
    fc = _make_parcels(gdb, sr, [
        ("U1", "P1", _square(500000, 2520000, 40, 40, sr)),
    ])
    with pytest.raises(ValueError, match="CGCS2000.*三度高斯—克吕格"):
        G.grid_design(fc, "PJDYBH", "DKBH", gdb)


def test_sampling_crs_reads_arcgis_pro_cm_style_three_degree_definition():
    sr = arcpy.SpatialReference(4550)

    result = G._validate_sampling_crs(sr)

    assert result["wkid"] == 4550
    assert result["central_meridian"] == pytest.approx(123.0)
    assert result["false_easting"] == pytest.approx(500_000.0)


def test_grid_design_rejects_duplicate_parcel_ids(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _make_parcels(gdb, sr, [
        ("U1", "P1", _square(37377500, 2712500, 40, 40, sr)),
        ("U1", "P1", _square(37377560, 2712500, 40, 40, sr)),
    ])
    with pytest.raises(ValueError, match="图斑编号重复"):
        G.grid_design(fc, "PJDYBH", "DKBH", gdb)


def test_adaptive_grid_matches_point_count_and_nodes_are_regular(gdb):
    """指导书 6.1.4：按布点数划分均匀网格，格数与布点数同量级，节点是规则格网节点。"""
    sr = arcpy.SpatialReference(4524)
    rows = [("U1", "P1", _square(37377500, 2712500, 120, 80, sr))]
    fc = _make_parcels(gdb, sr, rows)
    report = G.grid_design(fc, "PJDYBH", "DKBH", gdb, cell_size_m=None,
                           min_points=3, min_spacing_m=15.0, inset_m=5.0)
    unit = report["units"][0]
    assert report["cell_mode"] == "adaptive"
    assert unit["网格尺寸"] > 25.0        # 3 点对应大格网，不再是 20 m 固定网格
    assert 3 <= unit["方格数"] <= 12      # 格数与布点数同量级
    assert unit["每格一点"] is True
    assert unit["覆盖缺失图斑"] == []
    # 交点只能是规则格网节点：相邻节点坐标差必须等于同一格距
    coords = []
    with arcpy.da.SearchCursor(report["node"], ["SHAPE@XY"]) as cur:
        for row in cur:
            value = row[0]
            coords.append(tuple(value) if not isinstance(value, (int, float)) else (value,))
    xs = sorted({round(x, 3) for x, _ in coords})
    ys = sorted({round(y, 3) for _, y in coords})
    dx = {round(b - a, 1) for a, b in zip(xs, xs[1:])}
    dy = {round(b - a, 1) for a, b in zip(ys, ys[1:])}
    assert len(dx) <= 1 and len(dy) <= 1
    assert (not dx or abs(list(dx)[0] - unit["网格尺寸"]) < 0.3)
    assert len(coords) <= 12


def test_point_selection_uses_interior_candidates_before_edge_candidates():
    sr = arcpy.SpatialReference(4524)
    x, y = 36550000, 2600000
    geometry = _square(x, y, 100, 100, sr)
    candidates = [dict(parcel="P1", cell_no=i,
                       pt=arcpy.PointGeometry(arcpy.Point(x + dx, y + 50), sr))
                  for i, dx in enumerate((1, 25, 50, 75, 99), 1)]
    chosen, spacing_ok, notes = G._pick_points(
        arcpy, candidates, [dict(parcel="P1", geom=geometry)], geometry,
        min_points=3, min_spacing=15, inset_m=10, sr=sr)
    assert len(chosen) == 3 and spacing_ok
    assert all(geometry.boundary().distanceTo(c["pt"]) >= 10 for c in chosen)
    assert not notes


def test_clipped_edge_cells_keep_the_regular_square_center(gdb):
    sr = arcpy.SpatialReference(4524)
    x, y = 36550000, 2600000
    triangle = arcpy.Polygon(arcpy.Array([
        arcpy.Point(x, y), arcpy.Point(x + 100, y),
        arcpy.Point(x, y + 100), arcpy.Point(x, y)]), sr)
    fc = _make_parcels(gdb, sr, [("U1", "P1", triangle)])
    result = G.grid_design(fc, "PJDYBH", "DKBH", gdb, cell_size_m=20)
    squares = {cell: shape for cell, shape in arcpy.da.SearchCursor(
        result["grid"], ["CELL_NO", "SHAPE@"]) }
    centers = list(arcpy.da.SearchCursor(result["center"], ["CELL_NO", "IN_UNIT", "SHAPE@XY"]))
    assert len(centers) == len(squares)
    assert any(not inside for _, inside, _ in centers)
    for cell, _, xy in centers:
        extent = squares[cell].extent
        assert squares[cell].area == pytest.approx(400)
        assert xy == pytest.approx(((extent.XMin + extent.XMax) / 2,
                                    (extent.YMin + extent.YMax) / 2))


def test_missing_parcel_candidate_never_borrows_from_another_parcel():
    sr = arcpy.SpatialReference(4524)
    x, y = 36550000, 2600000
    parcels = [dict(parcel="P1", geom=_square(x, y, 100, 100, sr)),
               dict(parcel="P2", geom=_square(x + 200, y, 50, 50, sr))]
    candidates = [dict(parcel="P1", cell_no=i,
                       pt=arcpy.PointGeometry(arcpy.Point(x + dx, y + 50), sr))
                  for i, dx in enumerate((25, 75), 1)]
    with pytest.raises(ValueError, match="P2"):
        G._pick_points(arcpy, candidates, parcels, None, 2, 15, 0, sr)


def test_shapefile_empty_owner_is_not_a_changed_identifier(gdb, tmp_path):
    source = gdb + '/nullable_centers'
    arcpy.management.CreateFeatureclass(gdb, 'nullable_centers', 'POINT',
                                       spatial_reference=arcpy.SpatialReference(4524))
    for field in ('PJDYBH', 'DKBH'):
        arcpy.management.AddField(source, field, 'TEXT', field_length=30)
    with arcpy.da.InsertCursor(source, ['SHAPE@XY', 'PJDYBH', 'DKBH']) as cursor:
        cursor.insertRow([(36550000, 2600000), 'U1', None])
    arcpy.conversion.FeatureClassToFeatureClass(source, str(tmp_path), 'centers.shp')
    output = str(tmp_path / 'centers.shp')
    assert G.verify_shapefile_identifiers(arcpy, source, output)['status'] == 'PASS'
    with arcpy.da.UpdateCursor(output, ['SHAPE@XY']) as cursor:
        for row in cursor:
            cursor.updateRow([(36550001, 2600000)])
    assert G.verify_shapefile_identifiers(arcpy, source, output)['status'] == 'FAIL'
    with arcpy.da.UpdateCursor(output, ['DKBH']) as cursor:
        for row in cursor:
            cursor.updateRow(['changed'])
    assert G.verify_shapefile_identifiers(arcpy, source, output)['status'] == 'FAIL'


def test_fixed_grid_readback_rejects_corrupt_geometry_and_preserves_node_design(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _make_parcels(gdb, sr, [('U1', 'P1', _square(36550000, 2600000, 100, 100, sr))])
    report = G.grid_design(fc, 'PJDYBH', 'DKBH', gdb, cell_size_m=20)
    assert G.verify_grid_geometry(arcpy, report, 20)['status'] == 'PASS'
    for adaptive in ('auto', '自适应', ''):
        assert G.verify_grid_geometry(arcpy, report, adaptive)['status'] == 'PASS'
    with arcpy.da.UpdateCursor(report['sample'], ['CELL_NO', 'SHAPE@XY']) as cursor:
        row = next(cursor)
        grid_geom = next(g for uid, cell, g in arcpy.da.SearchCursor(
            report['grid'], ['PJDYBH', 'CELL_NO', 'SHAPE@']) if cell == row[0])
        cursor.updateRow([row[0], (grid_geom.extent.XMin, grid_geom.extent.YMin)])
    assert G.verify_grid_geometry(arcpy, report, 20)['status'] == 'PASS'
    with arcpy.da.UpdateCursor(report['grid'], ['SHAPE@']) as cursor:
        row = next(cursor)
        e = row[0].extent
        cursor.updateRow([_square(e.XMin, e.YMin, 40, 20, sr)])
    assert G.verify_grid_geometry(arcpy, report, 20)['status'] == 'FAIL'


def test_statistics_are_recomputed_after_project_point_reduction(gdb):
    from acceptance.point_adjustment import apply_project_point_counts
    sr = arcpy.SpatialReference(4524)
    fc = _make_parcels(gdb, sr, [('U1', 'P1', _square(36550000, 2600000, 100, 100, sr))])
    report = G.grid_design(fc, 'PJDYBH', 'DKBH', gdb, cell_size_m=20)
    apply_project_point_counts(arcpy, report['sample'], fc, 'PJDYBH', {'U1': 1})
    G.refresh_grid_statistics(arcpy, report, fc, 'PJDYBH')
    row = report['units'][0]
    assert row['设计点数'] == row['采样质量']['设计点数'] == 1
    assert row['采样质量']['最近邻中位_m'] is None
    assert row['采样质量']['平均每点_亩'] == pytest.approx(15, abs=.01)
    assert row['采样质量']['覆盖半径估计方法'] == '边界及内部探针'


def test_fixed_grid_uses_one_origin_and_keeps_each_units_shared_nodes(gdb):
    sr = arcpy.SpatialReference(4524)
    x, y = 36550000, 2600000
    source = _make_parcels(gdb, sr, [
        ('U1', 'P1', _square(x, y, 40, 40, sr)),
        ('U2', 'P2', _square(x + 40, y, 40, 40, sr))])
    result = G.grid_design(source, 'PJDYBH', 'DKBH', gdb, cell_size_m=20)
    nodes = list(arcpy.da.SearchCursor(result['node'], ['PJDYBH', 'SHAPE@XY']))
    assert {unit: sum(uid == unit for uid, _ in nodes) for unit in ('U1', 'U2')} == {'U1': 9, 'U2': 9}
    for _, xy in arcpy.da.SearchCursor(result['center'], ['PJDYBH', 'SHAPE@XY']):
        assert xy[0] % 20 == pytest.approx(10)
        assert xy[1] % 20 == pytest.approx(10)


def test_manual_sampling_group_retains_parcels_and_generates_three_total(gdb):
    from acceptance import unit_review as R
    sr = arcpy.SpatialReference(4524)
    x, y = 36550000, 2600000
    source = _make_parcels(gdb, sr, [('old22', '22', _square(x, y, 60, 60, sr)),
                                   ('old111', '111', _square(x + 62, y, 60, 60, sr))])
    before = [(uid, pid, bytes(g.WKB)) for uid, pid, g in arcpy.da.SearchCursor(source, ['PJDYBH', 'DKBH', 'SHAPE@'])]
    working = gdb + '/working'
    arcpy.management.CopyFeatures(source, working)
    field, plan = R.apply_sampling_groups(arcpy, working, 'DKBH',
        [{'group_id': 'SG_22_111', 'parcels': ['22', '111'], 'basis': '用户确认设计组'}])
    result = G.grid_design(working, field, 'DKBH', gdb, cell_size_m=20)
    points = list(arcpy.da.SearchCursor(result['sample'], ['PJDYBH', 'DKBH']))
    assert len(points) == 3 and {p for _, p in points} == {'22', '111'}
    assert {u for u, _ in points} == {'SG_22_111'} and plan['formal_unit_mapping'] == {}
    assert before == [(uid, pid, bytes(g.WKB)) for uid, pid, g in arcpy.da.SearchCursor(source, ['PJDYBH', 'DKBH', 'SHAPE@'])]


def test_manual_sampling_group_respects_custom_factor_fields(gdb):
    from acceptance import unit_review as R
    sr = arcpy.SpatialReference(4524)
    source = _make_parcels(gdb, sr, [('a', '22', _square(36550000, 2600000, 60, 60, sr)),
                                   ('b', '111', _square(36550062, 2600000, 60, 60, sr))])
    arcpy.management.AddField(source, 'CUSTOM_SOIL', 'TEXT')
    with arcpy.da.UpdateCursor(source, ['DKBH', 'CUSTOM_SOIL']) as cursor:
        for row in cursor:
            row[1] = 'A' if row[0] == '22' else 'B'
            cursor.updateRow(row)
    with pytest.raises(ValueError, match='冲突'):
        R.apply_sampling_groups(arcpy, source, 'DKBH',
            [{'group_id': 'SG_pair', 'parcels': ['22', '111'], 'basis': '确认'}], factor_fields=['CUSTOM_SOIL'])
    assert 'SAMP_GRP' not in [f.name for f in arcpy.ListFields(source)]


def test_unit_quality_is_computed_even_when_unit_has_single_grid_cell(gdb):
    """单格单元不得让覆盖半径/均匀性整项退回未计算（2026-10-31 Pro 实跑发现）。"""
    sr = arcpy.SpatialReference(4524)
    fc = _make_parcels(gdb, sr, [("U1", "P1", _square(37377500, 2712500, 20, 20, sr))])
    report = G.grid_design(fc, "PJDYBH", "DKBH", gdb, cell_size_m=20.0,
                           min_points=1, min_spacing_m=15.0, inset_m=2.0)
    quality = report["units"][0]["采样质量"]
    print("UNIT", report["units"][0])
    assert quality["覆盖半径_m"] is not None and quality["覆盖半径_m"] > 0, quality
    assert quality["均匀性"] is not None, quality
    assert quality["需人工调点"] is True  # 单点单元仍要人工复核，只是不再显示“未计算”


def test_boundary_rings_preserve_separation_at_null_placeholder_points():
    """示例区真实数据：带洞面 getPart 返回含 None 的环，边界环构建不得崩溃。"""
    class _Ring(list):
        def getObject(self, k):
            return self[k]

    class _Geom:
        partCount = 1
        def getPart(self, _i):
            return _Ring([arcpy.Point(0, 0), None, arcpy.Point(10, 0), arcpy.Point(0, 10)])

    rings = G._boundary_rings(_Geom())
    assert rings == [[(0.0, 0.0)], [(10.0, 0.0), (0.0, 10.0)]]


def test_grid_readback_rejects_mismatched_center_coordinate_system(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _make_parcels(gdb, sr, [('U1', 'P1', _square(36550000, 2600000, 100, 100, sr))])
    report = G.grid_design(fc, 'PJDYBH', 'DKBH', gdb, cell_size_m=20)
    arcpy.management.DefineProjection(report['center'], arcpy.SpatialReference(3857))
    result = G.verify_grid_geometry(arcpy, report, 20)
    assert result['status'] == 'FAIL'
    assert any('坐标系' in reason for reason in result['failures'])
