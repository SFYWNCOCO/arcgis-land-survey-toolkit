"""统一底图快照 CLI：天地图影像/矢量/地形晕渲 + 高德卫星/矢量（GCJ-02 逐像素纠偏）。

在 ArcGIS Pro Python 中运行，输出带地理参考的 GeoTIFF 与来源清单。天地图 key 沿用
DPAPI 保护的 basemaps.json（与 add-in 同一来源），不打印、不落明文；高德瓦片不需要
key，但坐标是 GCJ-02，必须走逐像素反算纠偏，不能直接当 CGCS2000 成果。

用法::

    py basemap_snapshot.py --out D:\\out\\tdt.tif --wkid 4525 \\
        --extent 108.0 22.0 108.5 22.5 --source tianditu_image --labels true
    py basemap_snapshot.py --out D:\\out\\amap.tif --wkid 4525 \\
        --extent 108.0 22.0 108.5 22.5 --source amap_satellite

stdout 最后一行固定为 JSON：
``{"status","coverage_status","output","zoom","output_pixel_size_m","correction","warning","ok"}``
成功 exit 0；失败输出 ``{"status":"FAIL","error":...}`` 且 exit 1。纠偏结果仅供底图
参考，不能替代控制点配准与测绘成果精度核查。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许按 `py acceptance/basemap_snapshot.py` 直接运行（Pro 的 add-in 即按脚本调用），
# 这样 `acceptance` 包及其相对导入仍可解析。
_TOOLKIT_ROOT = str(Path(__file__).resolve().parents[1])
if _TOOLKIT_ROOT not in sys.path:
    sys.path.insert(0, _TOOLKIT_ROOT)

SOURCE_PROVIDERS = {
    "tianditu_image": "tianditu",
    "tianditu_vector": "tianditu_vector",
    "tianditu_terrain": "tianditu_terrain",
    "amap_satellite": "amap_satellite",
    "amap_vector": "amap_vector",
}
AMAP_SOURCES = ("amap_satellite", "amap_vector")
TIANDITU_SOURCES = ("tianditu_image", "tianditu_vector", "tianditu_terrain")
SOURCES = tuple(SOURCE_PROVIDERS)


class _ArgumentParser(argparse.ArgumentParser):
    """参数错误也输出 JSON FAIL，而不是只有 argparse 的 stderr 文本。"""

    def error(self, message):
        raise ValueError("参数错误：" + message)


def provider_for(source):
    """CLI 来源名 → imagery 内置 provider 名。"""
    try:
        return SOURCE_PROVIDERS[str(source)]
    except KeyError:
        raise ValueError("不支持的底图来源：{}".format(source)) from None


def needs_tianditu_key(source):
    return str(source) in TIANDITU_SOURCES


def _tianditu_key():
    """复用 tianditu_snapshot 的 DPAPI 读取；不在本模块复制解密逻辑。"""
    from acceptance.tianditu_snapshot import _tianditu_key as read_key
    return read_key()


def _prepare(**kwargs):
    """薄封装，便于测试替换；真实实现走 acceptance.imagery.prepare_imagery。"""
    from acceptance import imagery
    return imagery.prepare_imagery(**kwargs)


def _run(args):
    source = str(args.source)
    provider = provider_for(source)
    out = Path(args.out).expanduser().resolve()
    if out.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError("--out 必须以 .tif 或 .tiff 结尾：{}".format(out))
    if int(args.max_tiles) < 1:
        raise ValueError("--max-tiles 必须为正整数")
    out.parent.mkdir(parents=True, exist_ok=True)
    import arcpy  # 由 ArcGIS Pro Python 提供；非 Pro 环境会在此明确失败

    extent = arcpy.Extent(*[float(value) for value in args.extent],
                          spatial_reference=arcpy.SpatialReference(int(args.wkid)))
    tk = _tianditu_key() if needs_tianditu_key(source) else None
    gcj02_correct = source in AMAP_SOURCES
    manifest = _prepare(
        out_folder=str(out.parent), target_sr_wkid=int(args.wkid), extent=extent,
        provider=provider, tk=tk, max_tiles=max(1, int(args.max_tiles)),
        max_zoom=int(args.max_zoom), label=out.stem, cache_dir=str(out.parent / "tiles"),
        labels=str(args.labels).lower() not in ("false", "0", "no"),
        gcj02_correct=gcj02_correct,
        resample_alg="bilinear" if gcj02_correct else "lanczos")
    output = manifest.get("output") or ""
    result = {
        "status": manifest.get("status"),
        "coverage_status": manifest.get("coverage_status"),
        "output": output,
        "zoom": manifest.get("zoom"),
        "output_pixel_size_m": manifest.get("output_pixel_size_m"),
        "correction": manifest.get("correction"),
        "warning": manifest.get("warning"),
        "primary_reason": manifest.get("primary_reason"),
        "ok": bool(output and os.path.isfile(output)),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def main(argv=None):
    parser = _ArgumentParser(description="生成天地图/高德底图的本地 GeoTIFF 快照。")
    parser.add_argument("--out", required=True, help="输出 GeoTIFF 路径（.tif/.tiff）")
    parser.add_argument("--wkid", required=True, type=int, help="目标坐标系 WKID")
    parser.add_argument("--extent", required=True, nargs=4, type=float,
                        metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="目标坐标系范围")
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument("--labels", default="true", help="是否叠加注记（true/false）")
    parser.add_argument("--max-tiles", type=int, default=200)
    parser.add_argument("--max-zoom", type=int, default=18)
    try:
        args = parser.parse_args(argv)
        return _run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 调用方需要真实失败原因
        print(json.dumps({"status": "FAIL", "error": "{}: {}".format(type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
