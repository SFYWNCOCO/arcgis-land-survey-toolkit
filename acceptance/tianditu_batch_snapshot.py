"""Generate one local Tianditu raster snapshot per polygon feature.

Each parcel uses its own extent at the native zoom (default 18), with labels and
Lanczos resampling, so a large project does not degrade to a single low-zoom
overview. The API key is read from the DPAPI-protected basemaps.json and is
never printed or written to disk.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path


def _safe_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_\-一-鿿]+", "_", str(value or "")).strip("_")
    return (name or fallback)[:60]


def _field_name(fields, requested: str) -> str:
    names = {str(field.name).upper(): str(field.name) for field in fields}
    if requested:
        found = names.get(requested.upper())
        if not found:
            raise ValueError("找不到编号字段：" + requested)
        return found
    for candidate in ("DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME"):
        if candidate in names:
            return names[candidate]
    raise ValueError("请指定图斑编号字段（例如 DIKUAI_NO 或 DKBH）。")


def run(source_path, out_dir_path, id_field="", wkid=0, labels=True, max_tiles=120,
        max_zoom=18, pad_m=40.0, limit=50):
    """Generate per-feature TIFFs and return a manifest summary."""
    import arcpy
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from acceptance import imagery
    from acceptance.task_state import _atomic_json
    from acceptance.tianditu_snapshot import _tianditu_key

    source = arcpy.Describe(source_path)
    if getattr(source, "shapeType", "") != "Polygon":
        raise ValueError("批量快照要求面图层。")
    fields = arcpy.ListFields(source_path)
    id_field = _field_name(fields, id_field)
    source_sr = getattr(source, "spatialReference", None)
    if source_sr is None or getattr(source_sr, "name", "Unknown") == "Unknown":
        raise ValueError("源图层没有坐标系定义。")
    source_wkid = int(getattr(source_sr, "factoryCode", 0) or 0)
    is_projected = (getattr(source_sr, "type", "") == "Projected") or bool(getattr(source_sr, "projected", False))
    target_wkid = int(wkid) if wkid else (source_wkid if is_projected else 3857)
    target_sr = arcpy.SpatialReference(target_wkid)

    out_dir = os.path.abspath(out_dir_path)
    os.makedirs(out_dir, exist_ok=True)
    manifest_path = os.path.join(out_dir, "批量快照清单.json")
    key = _tianditu_key()
    rows = []
    used = set()
    with arcpy.da.SearchCursor(source_path, ["SHAPE@", id_field]) as cursor:
        for index, (shape, parcel_id) in enumerate(cursor):
            if index >= max(1, int(limit)):
                break
            if shape is None:
                continue
            label = _safe_name(parcel_id, "parcel_%04d" % (index + 1))
            while label in used:
                label += "_%02d" % (index + 1)
            used.add(label)
            extent = shape.extent
            if target_wkid != source_wkid:
                points = []
                for x, y in ((extent.XMin, extent.YMin), (extent.XMax, extent.YMax)):
                    projected = arcpy.PointGeometry(arcpy.Point(x, y), source_sr).projectAs(target_sr)
                    points.append((projected.centroid.X, projected.centroid.Y))
                (xmin, ymin), (xmax, ymax) = points
            else:
                xmin, ymin, xmax, ymax = extent.XMin, extent.YMin, extent.XMax, extent.YMax
            pad = max(0.0, float(pad_m))
            target_extent = arcpy.Extent(xmin - pad, ymin - pad, xmax + pad, ymax + pad,
                                         spatial_reference=target_sr)
            try:
                manifest = imagery.prepare_imagery(
                    out_dir, target_wkid, extent=target_extent, provider="tianditu", tk=key,
                    max_tiles=max(1, int(max_tiles)), max_zoom=min(18, int(max_zoom)),
                    label=label, cache_dir=os.path.join(out_dir, "tiles"),
                    labels=bool(labels), resample_alg="lanczos")
                rows.append({
                    "id": parcel_id, "label": label,
                    "output": manifest.get("output"),
                    "zoom": manifest.get("zoom"),
                    "output_pixel_size_m": manifest.get("output_pixel_size_m"),
                    "coverage_status": manifest.get("coverage_status"),
                    "status": manifest.get("status"),
                    "warning": manifest.get("warning"),
                })
            except Exception as exc:  # noqa: BLE001 - keep the batch going, record the failure
                rows.append({"id": parcel_id, "label": label, "status": "FAIL",
                             "error": "{}: {}".format(type(exc).__name__, exc)})
            _atomic_json(manifest_path, {
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "source": os.path.abspath(source_path), "id_field": id_field,
                "target_wkid": target_wkid, "labels": bool(labels),
                "count": len(rows), "parcels": rows,
            })
    if not rows:
        raise ValueError("没有可处理的图斑。")
    return {"ok": True, "count": len(rows), "manifest": manifest_path,
            "outputs": [row for row in rows if row.get("output")]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--id-field", default="")
    parser.add_argument("--wkid", type=int, default=0)
    parser.add_argument("--labels", default="true")
    parser.add_argument("--max-tiles", type=int, default=120)
    parser.add_argument("--max-zoom", type=int, default=18)
    parser.add_argument("--pad-m", type=float, default=40.0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = run(args.source, args.out_dir, args.id_field, args.wkid,
                 str(args.labels).lower() not in ("false", "0", "no"),
                 args.max_tiles, args.max_zoom, args.pad_m, args.limit)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
