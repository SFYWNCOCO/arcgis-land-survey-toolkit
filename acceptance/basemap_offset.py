"""高德 GCJ-02 底图相对 WGS84/CGCS2000 的位置偏移报告 CLI（纯数学，不依赖 arcpy/gdal）。

用法（WGS84 经纬度）::

    py basemap_offset.py --extent 108.0 22.0 108.5 22.5
    py basemap_offset.py --center 108.25 22.25
    py basemap_offset.py --extent 108.0 22.0 108.5 22.5 --grid 5

输出说明：中心与四角偏移米数、范围内偏移变化量按 GCJ-02 公开近似算法估算，用于
判断高德底图与原位矢量叠加会漂移多少。该偏移量不能替代控制点配准、测绘成果精度
核查；纠偏后也不得声称"已精校正"。
"""
from __future__ import annotations

import argparse
import json
import sys

try:  # 作为包导入（Pro 内嵌解释器 / 测试）
    from . import gcj02
except ImportError:  # 作为脚本直接运行（py acceptance/basemap_offset.py）
    import gcj02  # type: ignore[no-redef]

METHOD = "GCJ-02 公开近似算法：WGS84→GCJ-02 求偏移，按当地纬度做米制换算"
NOTE = ("GCJ-02 为公开近似算法，只用于公共底图对齐；偏移量不能替代控制点配准、"
        "测绘成果影像精度核查，纠偏后不得声称“已精校正”。")

# 中心模式没有四角输入，用中心 ±0.0005°（约 50 m）的小方框估算四角与变化量。
CENTER_HALF_SPAN_DEG = 0.0005
CORNER_ORDER = ("SW", "SE", "NW", "NE")


class _ArgumentParser(argparse.ArgumentParser):
    """参数错误也走 JSON FAIL，而不是只留 argparse 的 stderr 退出码。"""

    def error(self, message):
        raise ValueError("参数错误：" + message)


def build_report(extent=None, center=None, grid=3):
    """构建偏移报告；``extent`` 与 ``center`` 必须二选一（WGS84 经纬度）。"""
    if (extent is None) == (center is None):
        raise ValueError("请提供 --extent 或 --center 二者之一")
    if center is not None:
        try:
            lon, lat = float(center[0]), float(center[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("--center 需要 2 个数字：lon lat") from None
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise ValueError("--center 经纬度超出有效范围")
        bounds = (lon - CENTER_HALF_SPAN_DEG, lat - CENTER_HALF_SPAN_DEG,
                  lon + CENTER_HALF_SPAN_DEG, lat + CENTER_HALF_SPAN_DEG)
        scope_note = "中心模式：四角与变化量按中心 ±{}°（约 50 m）方框估算。".format(CENTER_HALF_SPAN_DEG)
    else:
        try:
            bounds = tuple(float(value) for value in extent)
        except (TypeError, ValueError):
            raise ValueError("--extent 需要 4 个数字：lonmin latmin lonmax latmax") from None
        scope_note = "范围模式：四角为给定 extent 四角。"
    table = gcj02.offset_table(bounds, grid=grid)
    corners = []
    for name in CORNER_ORDER:
        entry = dict(table["corners"][name])
        entry["corner"] = name
        corners.append(entry)
    return {
        "status": "OK",
        "method": METHOD,
        "center": table["center"],
        "corners": corners,
        "variation_m": table["variation_m"],
        "max_offset_m": table["max_offset_m"],
        "note": NOTE + scope_note,
        "extent": list(bounds),
        "grid": table["grid"],
        "samples": table["samples"],
        "inside_china": table["inside_china"],
    }


def main(argv=None):
    parser = _ArgumentParser(
        description="计算高德 GCJ-02 底图在给定范围内的偏移量（只读、纯数学）。")
    parser.add_argument("--extent", nargs=4, type=float,
                        metavar=("LONMIN", "LATMIN", "LONMAX", "LATMAX"),
                        help="WGS84 经纬度范围")
    parser.add_argument("--center", nargs=2, type=float, metavar=("LON", "LAT"),
                        help="WGS84 中心点（与 --extent 二选一）")
    parser.add_argument("--grid", type=int, default=3, help="每边采样点数（2–21，默认 3）")
    try:
        args = parser.parse_args(argv)
        report = build_report(extent=args.extent, center=args.center, grid=args.grid)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI 需要把真实原因交给调用方
        print(json.dumps({"status": "FAIL", "error": "{}: {}".format(type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
