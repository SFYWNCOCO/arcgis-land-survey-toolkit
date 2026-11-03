"""GCJ-02 公开近似算法：WGS84 与 GCJ-02 互算、偏移米数估算、范围内偏移变化量表。

适用范围与免责（必须随成果原样保留）：

- GCJ-02 是公开的近似算法（公开发布的偏移多项式 + 椭球参数拟合），只用于把
  高德/腾讯等 GCJ-02 公共底图与 WGS84/CGCS2000 数据做显示对齐与参考图层叠加。
- 它不能替代控制点配准、测绘成果精度核查或任何法定坐标转换；纠偏后也不得
  声称"已精校正"、不得标定为测量精度合格成果。
- 迭代反算把算法自身的反算残差压到容差 ≤1e-9 度（约 0.1 mm 量级）。该数值只是
  算法可逆性指标，不代表底图的实际位置精度，也不构成精度证明。
- 中国境外按算法定义不加偏移（返回原坐标）；把境外坐标当境内偏移使用会得到
  错误结果，调用方须自行判断适用区。适用区只按矩形判断（经度 72.004–137.8347、
  纬度 0.8293–55.8271），香港/澳门/台湾也落在该矩形内、会被加偏移；本模块并未
  单独排除港澳台，业务需要排除时须另行实现并测试。

输出约定：

- 标量输入返回 ``(lon, lat)`` 或 ``(east_m, north_m)`` 浮点元组。
- list/tuple 输入返回两个同形 list；numpy.ndarray 输入返回两个 ndarray（按
  numpy 广播规则）。无 numpy 时 list 输入只支持等长序列或标量与序列组合。
"""
from __future__ import annotations

import math

try:  # numpy 是可选依赖：Pro Python 自带，纯标准库环境仍可使用标量/列表路径
    import numpy as _np
except ImportError:  # pragma: no cover - 取决于运行环境
    _np = None

# GCJ-02 多项式沿用 Krassovsky 1940 椭球参数。
_A = 6378245.0
_EE = 0.00669342162296594323
# 当地纬度米制换算使用 WGS84 椭球。
_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)

# 公开算法只在公告范围内生效；范围外不猜测偏移。
CHINA_LON = (72.004, 137.8347)
CHINA_LAT = (0.8293, 55.8271)

INVERSE_TOLERANCE_DEG = 1e-9
_INVERSE_TARGET_DEG = 1e-10
_INVERSE_MAX_ITERATIONS = 50

OFFSET_NOTE = ("GCJ-02 为公开近似算法，只用于公共底图对齐；偏移量不能替代控制点配准、"
               "测绘成果精度核查，纠偏后不得声称“已精校正”。")


def inside_china(lon, lat):
    """矩形判定：公开算法声明适用区为矩形范围；未单独排除港澳台，需要时由调用方处理。"""
    return CHINA_LON[0] <= float(lon) <= CHINA_LON[1] and CHINA_LAT[0] <= float(lat) <= CHINA_LAT[1]


def _inside_arrays(lon, lat):
    return ((lon >= CHINA_LON[0]) & (lon <= CHINA_LON[1])
            & (lat >= CHINA_LAT[0]) & (lat <= CHINA_LAT[1]))


def _transform_lat(x, y):
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return value


def _transform_lon(x, y):
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    value += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return value


def _forward_scalar(lon, lat):
    if not inside_china(lon, lat):
        return lon, lat
    x, y = lon - 105.0, lat - 35.0
    dlat, dlon = _transform_lat(x, y), _transform_lon(x, y)
    rad_lat = math.radians(lat)
    magic = 1.0 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    # 标准公式：dlat 除以子午圈曲率半径 M = a(1-e²)/(magic·sqrt(magic))。
    dlat = dlat * 180.0 / (((_A * (1.0 - _EE)) / (magic * sqrt_magic)) * math.pi)
    dlon = dlon * 180.0 / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lon + dlon, lat + dlat


def _forward_arrays(lon, lat):
    x, y = lon - 105.0, lat - 35.0
    dlat = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * _np.sqrt(_np.abs(x))
            + (20.0 * _np.sin(6.0 * x * _np.pi) + 20.0 * _np.sin(2.0 * x * _np.pi)) * 2.0 / 3.0
            + (20.0 * _np.sin(y * _np.pi) + 40.0 * _np.sin(y / 3.0 * _np.pi)) * 2.0 / 3.0
            + (160.0 * _np.sin(y / 12.0 * _np.pi) + 320.0 * _np.sin(y * _np.pi / 30.0)) * 2.0 / 3.0)
    dlon = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * _np.sqrt(_np.abs(x))
            + (20.0 * _np.sin(6.0 * x * _np.pi) + 20.0 * _np.sin(2.0 * x * _np.pi)) * 2.0 / 3.0
            + (20.0 * _np.sin(x * _np.pi) + 40.0 * _np.sin(x / 3.0 * _np.pi)) * 2.0 / 3.0
            + (150.0 * _np.sin(x / 12.0 * _np.pi) + 300.0 * _np.sin(x / 30.0 * _np.pi)) * 2.0 / 3.0)
    rad_lat = _np.radians(lat)
    magic = 1.0 - _EE * _np.sin(rad_lat) ** 2
    sqrt_magic = _np.sqrt(magic)
    dlat = dlat * 180.0 / (((_A * (1.0 - _EE)) / (magic * sqrt_magic)) * _np.pi)
    dlon = dlon * 180.0 / (_A / sqrt_magic * _np.cos(rad_lat) * _np.pi)
    inside = _inside_arrays(lon, lat)
    return _np.where(inside, lon + dlon, lon), _np.where(inside, lat + dlat, lat)


def _inverse_scalar(lon, lat):
    if not inside_china(lon, lat):
        return lon, lat
    x, y = lon, lat
    for _ in range(_INVERSE_MAX_ITERATIONS):
        shifted_lon, shifted_lat = _forward_scalar(x, y)
        dx, dy = shifted_lon - lon, shifted_lat - lat
        x -= dx
        y -= dy
        if abs(dx) <= _INVERSE_TARGET_DEG and abs(dy) <= _INVERSE_TARGET_DEG:
            break
    return x, y


def _inverse_arrays(lon, lat):
    inside = _inside_arrays(lon, lat)
    x, y = lon.copy(), lat.copy()
    if x.size == 0:
        return x, y
    for _ in range(_INVERSE_MAX_ITERATIONS):
        shifted_lon, shifted_lat = _forward_arrays(x, y)
        dx, dy = shifted_lon - lon, shifted_lat - lat
        x = x - dx
        y = y - dy
        if float(_np.max(_np.abs(dx))) <= _INVERSE_TARGET_DEG and float(_np.max(_np.abs(dy))) <= _INVERSE_TARGET_DEG:
            break
    return _np.where(inside, x, lon), _np.where(inside, y, lat)


def _is_sequence(value):
    if isinstance(value, (list, tuple)):
        return True
    return _np is not None and isinstance(value, _np.ndarray)


def _apply(lon, lat, scalar_func, array_func):
    """统一标量/列表/ndarray 分发；返回形状与输入约定一致的两个值。"""
    if not _is_sequence(lon) and not _is_sequence(lat):
        return scalar_func(float(lon), float(lat))
    if _np is None:
        if _is_sequence(lon) and _is_sequence(lat):
            if len(lon) != len(lat):
                raise ValueError("经度与纬度序列长度必须一致（无 numpy 时不支持广播）")
            pairs = [scalar_func(float(a), float(b)) for a, b in zip(lon, lat)]
        elif _is_sequence(lon):
            pairs = [scalar_func(float(a), float(lat)) for a in lon]
        else:
            pairs = [scalar_func(float(lon), float(b)) for b in lat]
        return [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    lon_values, lat_values = _np.broadcast_arrays(
        _np.asarray(lon, dtype=_np.float64), _np.asarray(lat, dtype=_np.float64))
    out_lon, out_lat = array_func(lon_values, lat_values)
    if isinstance(lon, _np.ndarray) or isinstance(lat, _np.ndarray):
        return out_lon, out_lat
    return out_lon.tolist(), out_lat.tolist()


def wgs84_to_gcj02(lon, lat):
    """WGS84/CRS84 → GCJ-02。中国境外返回原坐标；支持标量、list/tuple、ndarray。"""
    return _apply(lon, lat, _forward_scalar, _forward_arrays)


def gcj02_to_wgs84(lon, lat):
    """GCJ-02 → WGS84（不动点迭代反算，容差 ≤1e-9 度）。"""
    return _apply(lon, lat, _inverse_scalar, _inverse_arrays)


def _meters_per_degree(lat):
    """返回 (每度经度米数, 每度纬度米数)，按 WGS84 椭球与当地纬度计算。"""
    rad = math.radians(lat)
    sin_lat = math.sin(rad)
    denominator = 1.0 - _WGS84_E2 * sin_lat * sin_lat
    prime_vertical = _WGS84_A / math.sqrt(denominator)
    meridian = _WGS84_A * (1.0 - _WGS84_E2) / denominator ** 1.5
    scale = math.pi / 180.0
    return scale * prime_vertical * math.cos(rad), scale * meridian


def _meters_per_degree_arrays(lat):
    rad = _np.radians(lat)
    sin_lat = _np.sin(rad)
    denominator = 1.0 - _WGS84_E2 * sin_lat * sin_lat
    prime_vertical = _WGS84_A / _np.sqrt(denominator)
    meridian = _WGS84_A * (1.0 - _WGS84_E2) / denominator ** 1.5
    scale = _np.pi / 180.0
    return scale * prime_vertical * _np.cos(rad), scale * meridian


def offset_m(lon, lat):
    """WGS84 → GCJ-02 的位移（米）：返回 (east_m, north_m)，东/北为正。"""
    if not _is_sequence(lon) and not _is_sequence(lat):
        lon, lat = float(lon), float(lat)
        gcj_lon, gcj_lat = _forward_scalar(lon, lat)
        east_scale, north_scale = _meters_per_degree(lat)
        return (gcj_lon - lon) * east_scale, (gcj_lat - lat) * north_scale
    if _np is None:
        if _is_sequence(lon) and _is_sequence(lat):
            if len(lon) != len(lat):
                raise ValueError("经度与纬度序列长度必须一致（无 numpy 时不支持广播）")
            pairs = [offset_m(float(a), float(b)) for a, b in zip(lon, lat)]
        elif _is_sequence(lon):
            pairs = [offset_m(float(a), lat) for a in lon]
        else:
            pairs = [offset_m(lon, float(b)) for b in lat]
        return [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    lon_values, lat_values = _np.broadcast_arrays(
        _np.asarray(lon, dtype=_np.float64), _np.asarray(lat, dtype=_np.float64))
    gcj_lon, gcj_lat = _forward_arrays(lon_values, lat_values)
    east_scale, north_scale = _meters_per_degree_arrays(lat_values)
    east = (gcj_lon - lon_values) * east_scale
    north = (gcj_lat - lat_values) * north_scale
    if isinstance(lon, _np.ndarray) or isinstance(lat, _np.ndarray):
        return east, north
    return east.tolist(), north.tolist()


def _validate_bounds(bounds):
    try:
        values = [float(value) for value in bounds]
    except (TypeError, ValueError):
        raise ValueError("范围必须是 4 个数字：lonmin latmin lonmax latmax") from None
    if len(values) != 4:
        raise ValueError("范围必须是 4 个数字：lonmin latmin lonmax latmax")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("范围的经纬度必须是有限数字")
    lon_min, lat_min, lon_max, lat_max = values
    if not (-180.0 <= lon_min < lon_max <= 180.0):
        raise ValueError("经度范围须满足 -180 ≤ lonmin < lonmax ≤ 180")
    if not (-90.0 <= lat_min < lat_max <= 90.0):
        raise ValueError("纬度范围须满足 -90 ≤ latmin < latmax ≤ 90")
    return lon_min, lat_min, lon_max, lat_max


def _entry(lon, lat):
    east, north = offset_m(lon, lat)
    return {"lon": round(lon, 8), "lat": round(lat, 8),
            "east_m": round(east, 4), "north_m": round(north, 4),
            "distance_m": round(math.hypot(east, north), 4)}


def offset_table(bounds, grid=3):
    """范围内规则采样（默认 3×3）的中心/四角偏移与变化量，供 CLI 与测试复用。

    ``bounds`` 为 WGS84 ``(lonmin, latmin, lonmax, latmax)``；``grid`` 为每边采样
    点数（≥2）。返回中心与 SW/SE/NW/NE 偏移、采样点偏移的 ``max_offset_m`` 以及
    采样点之间的最大偏移差异 ``variation_m``（整体平移带来的误差上限指标）。
    """
    lon_min, lat_min, lon_max, lat_max = _validate_bounds(bounds)
    try:
        grid = int(grid)
    except (TypeError, ValueError):
        raise ValueError("采样格网数必须是 2–21 的整数") from None
    if not 2 <= grid <= 21:
        raise ValueError("采样格网数必须是 2–21 的整数")
    longitudes = [lon_min + (lon_max - lon_min) * index / (grid - 1) for index in range(grid)]
    latitudes = [lat_min + (lat_max - lat_min) * index / (grid - 1) for index in range(grid)]
    samples = [(lon, lat) for lat in latitudes for lon in longitudes]
    offsets = [offset_m(lon, lat) for lon, lat in samples]
    distances = [math.hypot(east, north) for east, north in offsets]
    variation = 0.0
    for first in range(len(offsets)):
        for second in range(first + 1, len(offsets)):
            distance = math.hypot(offsets[first][0] - offsets[second][0],
                                  offsets[first][1] - offsets[second][1])
            if distance > variation:
                variation = distance
    center_lon, center_lat = (lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0
    corners = {
        "SW": _entry(lon_min, lat_min),
        "SE": _entry(lon_max, lat_min),
        "NW": _entry(lon_min, lat_max),
        "NE": _entry(lon_max, lat_max),
    }
    return {
        "bounds": [lon_min, lat_min, lon_max, lat_max],
        "grid": grid,
        "samples": len(samples),
        "center": _entry(center_lon, center_lat),
        "corners": corners,
        "max_offset_m": round(max(distances) if distances else 0.0, 4),
        "min_offset_m": round(min(distances) if distances else 0.0, 4),
        "variation_m": round(variation, 4),
        "inside_china": inside_china(center_lon, center_lat),
        "note": OFFSET_NOTE,
    }
