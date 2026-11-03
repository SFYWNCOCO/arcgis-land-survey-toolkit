"""Build a local Tianditu raster snapshot for the current map extent.

Runs under ArcGIS Pro Python. The API key is read from the same DPAPI-protected
basemaps.json used by the add-in and is never printed or written to disk.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _unprotect(raw: str) -> bytes:
    data = base64.b64decode(raw)
    buffer = ctypes.create_string_buffer(data)
    blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise RuntimeError("底图配置解密失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _tianditu_key() -> str:
    root = Path(os.environ["LOCALAPPDATA"]) / "ProAiBridge"
    path = root / "basemaps.json"
    if not path.is_file():
        raise RuntimeError("未找到底图配置，请先在侧栏底图设置填写天地图 Key")
    payload = json.loads(_unprotect(path.read_text(encoding="utf-8")).decode("utf-8"))
    key = str(payload.get("TiandituKey") or "").strip()
    if not key:
        raise RuntimeError("天地图 Key 为空，请先在侧栏底图设置填写")
    return key


def _provider(source: str) -> str:
    providers = {"tianditu_image": "tianditu", "tianditu_vector": "tianditu_vector"}
    try:
        return providers[source]
    except KeyError as exc:
        raise ValueError("不支持的天地图底图类型") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--wkid", required=True, type=int)
    parser.add_argument("--extent", required=True, nargs=4, type=float,
                        metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    parser.add_argument("--label", default="tianditu_snapshot")
    parser.add_argument("--max-tiles", type=int, default=200)
    parser.add_argument("--max-zoom", type=int, default=18)
    parser.add_argument("--labels", default="true")
    parser.add_argument("--source", choices=("tianditu_image", "tianditu_vector"), default="tianditu_image")
    args = parser.parse_args()

    import arcpy  # provided by ArcGIS Pro Python
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from acceptance import imagery

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    extent = arcpy.Extent(*args.extent, spatial_reference=arcpy.SpatialReference(args.wkid))
    try:
        manifest = imagery.prepare_imagery(
            str(out.parent), args.wkid, extent=extent, provider=_provider(args.source),
            tk=_tianditu_key(), max_tiles=max(1, args.max_tiles), max_zoom=args.max_zoom,
            label=args.label, cache_dir=str(out.parent / "tiles"),
            labels=str(args.labels).lower() not in ("false", "0", "no"), resample_alg="lanczos")
    except Exception as exc:  # noqa: BLE001 - the add-in needs the real failure
        print(json.dumps({"status": "FAIL", "error": "{}: {}".format(type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1
    output = manifest.get("output") or ""
    result = {
        "status": manifest.get("status"),
        "coverage_status": manifest.get("coverage_status"),
        "output": output,
        "zoom": manifest.get("zoom"),
        "output_pixel_size_m": manifest.get("output_pixel_size_m"),
        "warning": manifest.get("warning"),
        "primary_reason": manifest.get("primary_reason"),
        "ok": bool(output and os.path.isfile(output)),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
