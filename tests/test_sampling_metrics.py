"""采样质量指标；系统 Python 3.12 可运行，不依赖 arcpy。"""
from __future__ import annotations

from acceptance.sampling_metrics import (apply_area_weights, boundary_probes,
                                         coverage_radius, nearest_distances, unit_quality)


def _cells(xs, ys, size, area=None):
    return [{"xy": (x, y), "inside_area": size * size if area is None else area}
            for x in xs for y in ys]


def test_nearest_distances_are_in_meters_and_ignore_the_point_itself():
    coords = [(0.0, 0.0), (14.0, 0.0), (0.0, 20.0)]
    assert nearest_distances(coords) == [14.0, 14.0, 20.0]


def test_single_point_reports_no_distribution_instead_of_zero():
    assert nearest_distances([(1.0, 2.0)]) == []
    report = unit_quality([{"pid": "A-01", "xy": (1.0, 2.0)}])
    assert report["最近邻最小_m"] is None
    assert report["覆盖半径_m"] is None
    assert report["需人工调点"] is True
    assert "无法评估分布" in report["调点原因"][0]


def test_density_and_nearest_median_use_final_points_and_actual_unit_area():
    points = [{'pid': str(i), 'xy': xy} for i, xy in enumerate([(0, 0), (20, 0), (60, 0)])]
    report = unit_quality(points, area_m2=10000)
    assert report['采样密度_点每公顷'] == 3
    assert report['平均每点_亩'] == 5
    assert report['最近邻中位_m'] == 20
    single = unit_quality(points[:1], area_m2=10000)
    assert single['平均每点_亩'] == 15
    assert single['最近邻中位_m'] is None
    empty = unit_quality([], area_m2=10000)
    assert empty['采样密度_点每公顷'] == 0
    assert empty['平均每点_亩'] is None


def test_two_points_below_general_spacing_are_flagged_for_manual_adjustment():
    points = [{"pid": "A-01", "xy": (10.0, 10.0)}, {"pid": "A-02", "xy": (12.0, 10.0)}]
    report = unit_quality(points)
    assert report["最近邻最小_m"] == 2.0
    assert report["需人工调点"] is True
    assert "15.0 m" in report["调点原因"][0]


def test_points_near_parcel_edge_are_listed_by_point_id():
    points = [{"pid": "A-01", "xy": (3.0, 50.0)}, {"pid": "A-02", "xy": (50.0, 50.0)}]
    report = unit_quality(points, unit_boundary=lambda xy: abs(xy[0] - 3.0))
    assert any("A-01" in reason for reason in report["调点原因"])


def _square_ring(side, step):
    half, n = side / 2, int(side / step)
    bottom = [(-half + step * i, -half) for i in range(n)]
    right = [(half, -half + step * i) for i in range(n)]
    top = [(half - step * i, half) for i in range(n)]
    left = [(-half, half - step * i) for i in range(n)]
    return bottom + right + top + left


def test_well_spread_points_have_high_uniformity_and_no_reason():
    # 4 × 4 格中心，边长 120 m，格长 30 m；边界探针最远离最近点约 21 m
    points = [{"pid": "P{}".format(i), "xy": (-45.0 + 30.0 * (i % 4), -45.0 + 30.0 * (i // 4))}
              for i in range(16)]
    probes = boundary_probes(_square_ring(120.0, 30.0), 30.0)
    report = unit_quality(points, probes=probes)
    assert report["需人工调点"] is False, report["调点原因"]
    assert report["最近邻最小_m"] >= 15.0
    assert 0.0 < report["均匀性"] <= 1.0


def test_uneven_layout_scores_lower_than_even_layout():
    probes = boundary_probes(_square_ring(120.0, 30.0), 30.0)
    even = [{"pid": "e{}".format(i), "xy": (-45.0 + 30.0 * (i % 4), -45.0 + 30.0 * (i // 4))}
            for i in range(16)]
    clustered = [{"pid": "c{}".format(i), "xy": (-50.0 + i * 0.5, -50.0 + i * 0.5)} for i in range(16)]
    assert unit_quality(clustered, probes=probes)["均匀性"] < unit_quality(even, probes=probes)["均匀性"]


def test_boundary_probes_follow_every_edge_without_duplicating_corners():
    probes = boundary_probes([(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0), (0.0, 0.0)], 10.0)
    assert (0.0, 0.0) in probes and (30.0, 30.0) in probes
    assert len(probes) == len(set(probes))
    assert all(0.0 <= x <= 30.0 and 0.0 <= y <= 30.0 for x, y in probes)


def test_area_weights_follow_real_inside_area_not_bounding_box():
    # 右侧两格只有一半落在单元内，左侧两格完整
    cells = [{"xy": (15.0, 15.0), "inside_area": 900.0}, {"xy": (15.0, 45.0), "inside_area": 900.0},
             {"xy": (45.0, 15.0), "inside_area": 450.0}, {"xy": (45.0, 45.0), "inside_area": 450.0}]
    weights = apply_area_weights([(20.0, 20.0), (50.0, 20.0)], cells)
    assert weights[0] > weights[1]


def test_area_weights_without_inside_cells_returns_none_not_guess():
    assert apply_area_weights([(0.0, 0.0)], [{"xy": (0.0, 0.0), "inside_area": 0.0}]) is None


def test_coverage_radius_is_none_without_probes_instead_of_zero():
    assert coverage_radius([(0.0, 0.0), (10.0, 0.0)], []) is None
    assert coverage_radius([], [(0.0, 0.0)]) is None


def test_coverage_radius_grows_when_one_point_is_removed():
    probes = boundary_probes(_square_ring(90.0, 30.0), 30.0)
    grid = [(-30.0 + 30.0 * (i % 3), -30.0 + 30.0 * (i // 3)) for i in range(9)]
    assert coverage_radius(grid[:-1], probes) > coverage_radius(grid, probes)
