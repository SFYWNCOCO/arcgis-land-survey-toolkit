"""把外业照片按 EXIF 落成点，并就近匹配设计点。

用法：
    python scripts/photo_geotag.py --photos <照片目录> --out points.csv \
        [--design 设计点清单.csv] [--geojson points.geojson] [--max-distance 30]

只读照片，不改原图；没有 GPS/时间的照片单独列出，不猜坐标。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
from pathlib import Path

from PIL import ExifTags, Image

GPS_IFD_TAG = getattr(getattr(ExifTags, "IFD", None), "GPSInfo", 0x8825)


def dms_to_degrees(value) -> float | None:
    try:
        parts = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if len(parts) != 3:
        return None
    return parts[0] + parts[1] / 60.0 + parts[2] / 3600.0


def read_photo(path: Path) -> dict:
    record = {"file": path.name, "datetime": "", "lon": None, "lat": None,
              "alt": None, "azimuth": None}
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            record["datetime"] = str(exif.get(ExifTags.Base.DateTimeOriginal) or "")
            get_ifd = getattr(exif, "get_ifd", None)
            gps = (get_ifd(GPS_IFD_TAG) if callable(get_ifd) else exif.get(GPS_IFD_TAG)) or {}
    except Exception:  # noqa: BLE001 - 坏图按无 GPS 处理
        return record
    lat = dms_to_degrees(gps.get(2))
    lon = dms_to_degrees(gps.get(4))
    if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if lon is not None and str(gps.get(3, "E")).upper().startswith("W"):
        lon = -lon
    record["lat"], record["lon"] = lat, lon
    try:
        record["alt"] = float(gps.get(6))
    except (TypeError, ValueError):
        pass
    try:
        record["azimuth"] = float(gps.get(17))
    except (TypeError, ValueError):
        pass
    return record


def haversine_m(lon1, lat1, lon2, lat2) -> float:
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def load_design(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            try:
                lon = float(row.get("LON") or "")
                lat = float(row.get("LAT") or "")
            except (TypeError, ValueError):
                continue
            rows.append({"point_id": row.get("POINT_ID", ""), "sample_id": row.get("SAMPLE_ID", ""),
                         "lon": lon, "lat": lat})
    return rows


def match(record: dict, design: list[dict], max_distance: float) -> dict:
    if record["lon"] is None or record["lat"] is None:
        return {"point_id": "", "sample_id": "", "distance_m": None, "status": "NO_GPS"}
    best, best_distance = None, None
    for row in design:
        distance = haversine_m(record["lon"], record["lat"], row["lon"], row["lat"])
        if best_distance is None or distance < best_distance:
            best, best_distance = row, distance
    if best is None:
        return {"point_id": "", "sample_id": "", "distance_m": None, "status": "NO_DESIGN"}
    status = "MATCHED" if best_distance <= 5 else "NEAR" if best_distance <= max_distance else "FAR"
    return {"point_id": best["point_id"], "sample_id": best["sample_id"],
            "distance_m": round(best_distance, 1), "status": status}


def run(photo_dir: Path, out_csv: Path, design_csv: Path | None, geojson: Path | None,
        max_distance: float) -> dict:
    design = load_design(design_csv) if design_csv else []
    rows = []
    for path in sorted(Path(photo_dir).rglob("*")):
        if path.suffix.lower() not in (".jpg", ".jpeg"):
            continue
        record = read_photo(path)
        if design:
            record.update(match(record, design, max_distance))
        else:
            record.update({"point_id": "", "sample_id": "", "distance_m": None,
                           "status": "GEOTAGGED" if record["lon"] is not None else "NO_GPS"})
        rows.append(record)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    columns = ["file", "datetime", "lon", "lat", "alt", "azimuth",
               "point_id", "sample_id", "distance_m", "status"]
    with out_csv.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    if geojson:
        features = [{"type": "Feature", "geometry": {"type": "Point",
                     "coordinates": [row["lon"], row["lat"]]}, "properties": row}
                    for row in rows if row["lon"] is not None]
        geojson.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                      ensure_ascii=False), encoding="utf-8")
    summary = {"photos": len(rows),
               "no_gps": sum(row["status"] == "NO_GPS" for row in rows),
               "matched": sum(row["status"] in ("MATCHED", "NEAR") for row in rows),
               "far": sum(row["status"] == "FAR" for row in rows),
               "out": str(out_csv)}
    (out_csv.with_suffix(".summary.json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--geojson", type=Path)
    parser.add_argument("--max-distance", type=float, default=30.0)
    args = parser.parse_args()
    print(json.dumps(run(args.photos, args.out, args.design, args.geojson,
                         args.max_distance), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
