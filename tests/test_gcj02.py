"""GCJ-02 公开近似算法：公开样例、正反算往返、偏移量纲与批量接口。

这些用例只验证算法实现本身，不构成底图位置精度证明。
"""
from __future__ import annotations

import math

import pytest

from acceptance import gcj02

# eviltransform / 公开博客广泛使用的参考样例（WGS84 → GCJ-02）。
# 若与本实现不符，应先核对公式与 (lon, lat) 顺序，不能改期望值掩盖错误。
EVILTRANSFORM_WGS84 = (121.5272106, 31.1774276)   # lon, lat
EVILTRANSFORM_GCJ02 = (121.531541859215, 31.17530398364597)

GUANGXI_POINTS = [(108.3669, 22.8170), (109.4117, 24.3255), (110.2991, 25.2736)]


def test_public_reference_vector_matches_eviltransform():
    lon, lat = gcj02.wgs84_to_gcj02(*EVILTRANSFORM_WGS84)
    assert lon == pytest.approx(EVILTRANSFORM_GCJ02[0], abs=1e-6)
    assert lat == pytest.approx(EVILTRANSFORM_GCJ02[1], abs=1e-6)
    back_lon, back_lat = gcj02.gcj02_to_wgs84(*EVILTRANSFORM_GCJ02)
    assert back_lon == pytest.approx(EVILTRANSFORM_WGS84[0], abs=1e-7)
    assert back_lat == pytest.approx(EVILTRANSFORM_WGS84[1], abs=1e-7)


@pytest.mark.parametrize("lon,lat", [EVILTRANSFORM_WGS84] + GUANGXI_POINTS)
def test_round_trip_tolerance_is_one_e_minus_seven_degree(lon, lat):
    gcj_lon, gcj_lat = gcj02.wgs84_to_gcj02(lon, lat)
    wgs_lon, wgs_lat = gcj02.gcj02_to_wgs84(gcj_lon, gcj_lat)
    assert abs(wgs_lon - lon) <= 1e-7
    assert abs(wgs_lat - lat) <= 1e-7


@pytest.mark.parametrize("lon,lat", GUANGXI_POINTS)
def test_guangxi_offsets_are_tens_to_hundreds_of_metres(lon, lat):
    east, north = gcj02.offset_m(lon, lat)
    distance = math.hypot(east, north)
    assert 50.0 <= distance <= 1000.0
    # 米制换算使用当地纬度：同一段经纬度差在低纬应折算出更大的东向米数。
    gcj_lon, gcj_lat = gcj02.wgs84_to_gcj02(lon, lat)
    assert east == pytest.approx((gcj_lon - lon) * math.cos(math.radians(lat)) * 111319.49, rel=0.01)


def test_outside_china_is_returned_unchanged_and_reported():
    lon, lat = 2.3522, 48.8566  # Paris
    assert gcj02.wgs84_to_gcj02(lon, lat) == (lon, lat)
    assert gcj02.gcj02_to_wgs84(lon, lat) == (lon, lat)
    assert gcj02.offset_m(lon, lat) == (0.0, 0.0)
    table = gcj02.offset_table([lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01])
    assert table["inside_china"] is False
    assert table["max_offset_m"] == 0.0 and table["variation_m"] == 0.0


def test_batch_api_supports_lists_and_numpy_arrays():
    np = pytest.importorskip("numpy")
    lons = [point[0] for point in GUANGXI_POINTS]
    lats = [point[1] for point in GUANGXI_POINTS]
    list_lon, list_lat = gcj02.wgs84_to_gcj02(lons, lats)
    assert isinstance(list_lon, list) and isinstance(list_lat, list)
    assert len(list_lon) == len(list_lat) == 3
    assert list_lon[0] == pytest.approx(gcj02.wgs84_to_gcj02(lons[0], lats[0])[0], abs=1e-12)

    array_lon, array_lat = gcj02.wgs84_to_gcj02(np.asarray(lons), np.asarray(lats))
    assert isinstance(array_lon, np.ndarray) and array_lon.shape == (3,)
    assert np.allclose(array_lon, list_lon, atol=1e-12)

    # 标量与序列广播；反算与偏移量同样支持批量。
    broadcast_lon, broadcast_lat = gcj02.gcj02_to_wgs84(array_lon, array_lat)
    assert np.allclose(gcj02.wgs84_to_gcj02(broadcast_lon, broadcast_lat),
                       (array_lon, array_lat), atol=1e-9)
    east, north = gcj02.offset_m(np.asarray(lons), np.asarray(lats))
    assert east.shape == north.shape == (3,)


def test_list_path_still_works_without_numpy(monkeypatch):
    lons = [point[0] for point in GUANGXI_POINTS]
    lats = [point[1] for point in GUANGXI_POINTS]
    expected = gcj02.wgs84_to_gcj02(lons, lats)
    monkeypatch.setattr(gcj02, "_np", None)
    actual = gcj02.wgs84_to_gcj02(lons, lats)
    assert actual[0] == pytest.approx(expected[0], abs=1e-12)
    assert actual[1] == pytest.approx(expected[1], abs=1e-12)
    scalar = gcj02.wgs84_to_gcj02(lons[0], lats[0])
    assert scalar[0] == pytest.approx(expected[0][0], abs=1e-12)
    with pytest.raises(ValueError, match="长度"):
        gcj02.wgs84_to_gcj02([108.0, 109.0], [22.0])


def test_offset_table_reports_centre_corners_and_variation():
    bounds = [107.0, 21.5, 109.0, 23.5]
    table = gcj02.offset_table(bounds, grid=3)
    assert table["bounds"] == bounds and table["grid"] == 3 and table["samples"] == 9
    for name in ("SW", "SE", "NW", "NE"):
        entry = table["corners"][name]
        assert {"lon", "lat", "east_m", "north_m", "distance_m"} <= set(entry)
        assert entry["distance_m"] == pytest.approx(
            math.hypot(entry["east_m"], entry["north_m"]), abs=1e-3)
    center = table["center"]
    assert center["lon"] == pytest.approx(108.0) and center["lat"] == pytest.approx(22.5)
    assert table["max_offset_m"] >= table["min_offset_m"] >= 0
    # 同一范围不同位置的偏移方向/量级不同：变化量必须真实反映在 3×3 采样上。
    assert table["variation_m"] > 0.0
    assert table["variation_m"] <= 2 * table["max_offset_m"] + 1e-9


def test_offset_table_validates_bounds_and_grid():
    with pytest.raises(ValueError, match="lonmin"):
        gcj02.offset_table([109.0, 22.0, 108.0, 23.0])
    with pytest.raises(ValueError, match="4 个数字"):
        gcj02.offset_table([109.0, 22.0, 108.0])
    with pytest.raises(ValueError, match="格网"):
        gcj02.offset_table([108.0, 22.0, 109.0, 23.0], grid=1)
    with pytest.raises(ValueError, match="有限"):
        gcj02.offset_table([108.0, 22.0, float("nan"), 23.0])


def test_module_documents_approximation_and_no_precise_correction_claim():
    text = gcj02.__doc__ or ""
    assert "公开近似算法" in text
    assert "不能替代" in text
    assert "已精校正" in text
    assert "不能替代" in gcj02.OFFSET_NOTE


def test_inside_china_scope_is_documented_as_rectangle_without_hk_mo_tw_exclusion():
    """P2-16：适用区实际只是矩形，文档不得声称已排除港澳台。"""
    text = gcj02.inside_china.__doc__ or ""
    assert "矩形" in text
    assert "未单独排除" in text
    assert "不含港" not in text and "特殊处理区" not in text
    # 如实记录当前行为：香港坐标落在矩形内，会被公开算法加偏移。
    hong_kong = gcj02.wgs84_to_gcj02(114.1694, 22.3193)
    assert hong_kong != (114.1694, 22.3193)
    # 矩形外（例如巴黎）不变。
    assert gcj02.wgs84_to_gcj02(2.3522, 48.8566) == (2.3522, 48.8566)

