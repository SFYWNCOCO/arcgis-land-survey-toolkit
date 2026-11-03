"""外业回传质检：照片点/采集表 vs 设计点。

用法：
    python scripts/field_return_qa.py --design 设计点清单.csv --returned points.csv \
        --out report.json [--max-offset 15]

只做结构核对：缺哪点、错哪号、偏多远、照片几张。设计点是否被实采、
照片内容是否合格仍须人审，不自动判通过。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from photo_geotag import haversine_m

SAMPLE_RE = re.compile(r"^(RZ)?F\d{14}E\d{4}$")
PHOTO_MIN_UNIT = 6  # 全景≥4 + 近景≥2；工作照另计，见采集说明
PHOTO_CATEGORY_HINTS = {
    "景观照": ("景观", "landscape", "jingguan", "jg"),
    "工作照": ("工作", "work", "gongzuo", "gz"),
    "剖面/土钻": ("剖面", "土钻", "断面", "profile", "drill", "pm", "zt"),
    "混合样": ("混合样", "混样", "mixed", "hhy"),
    "四分法": ("四分法", "sifen", "sff"),
    "容重": ("容重", "ring", "rz"),
    "侵入体/砾石": ("侵入体", "砾石", "intrusion", "gravel", "qr", "ls"),
}
EXPECTED_PHOTO_CATEGORIES = tuple(PHOTO_CATEGORY_HINTS)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def classify_photo(name: str) -> str:
    text = str(name or "").casefold()
    for category, hints in PHOTO_CATEGORY_HINTS.items():
        if any(hint.casefold() in text for hint in hints):
            return category
    return "未分类"


def scan_photo_categories(root: Path) -> dict:
    counts = {category: 0 for category in PHOTO_CATEGORY_HINTS}
    counts["未分类"] = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES:
            counts[classify_photo(path.parent.name + "/" + path.name)] += 1
    missing = [category for category in EXPECTED_PHOTO_CATEGORIES if counts.get(category, 0) == 0]
    return {"root": str(root), "counts": counts, "missing_categories": missing,
            "status": "NEEDS_REVIEW" if missing or counts["未分类"] else "PASS",
            "note": "按目录名/文件名分类；未分类照片不计入通过，内容真实性仍须人审。"}


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as stream:
        return [{key.strip().upper(): value for key, value in row.items()}
                for row in csv.DictReader(stream)]


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run(design_csv: Path, returned_csv: Path, out_json: Path,
        max_offset: float = 15.0, photos_root: Path | None = None) -> dict:
    design = _rows(design_csv)
    returned = _rows(returned_csv)
    by_point: dict[str, dict] = {}
    for row in design:
        key = row.get("POINT_ID") or row.get("SAMPLE_ID") or ""
        by_point[key] = {"point_id": row.get("POINT_ID", ""), "sample_id": row.get("SAMPLE_ID", ""),
                         "unit": row.get("PJDYBH", ""), "x": _float(row.get("X")),
                         "y": _float(row.get("Y")), "lon": _float(row.get("LON")),
                         "lat": _float(row.get("LAT")), "photos": 0, "offset": None,
                         "time": "", "returned_ids": set()}
    extra, duplicates, no_gps = [], [], 0
    for row in returned:
        key = row.get("POINT_ID") or row.get("SAMPLE_ID") or ""
        if not key:
            no_gps += 1
            continue
        item = by_point.get(key)
        if item is None:
            extra.append(key)
            continue
        item["returned_ids"].add(key)
        item["photos"] += 1
        item["time"] = item["time"] or row.get("DATETIME") or row.get("COLLECT_TIME", "")
        distance = _float(row.get("DISTANCE_M"))
        if distance is None:
            lon, lat = _float(row.get("LON")), _float(row.get("LAT"))
            if lon is not None and lat is not None and item["lon"] is not None:
                distance = haversine_m(lon, lat, item["lon"], item["lat"])
        if distance is not None:
            item["offset"] = distance if item["offset"] is None else max(item["offset"], distance)
    # 混合样编号＝单元编号，同单元多个分样点共用一个样号是制度设计；
    # 真正要拦的是同一样号被跨单元复用。
    sample_units: dict[str, set[str]] = {}
    for item in by_point.values():
        if item["sample_id"]:
            sample_units.setdefault(item["sample_id"], set()).add(item["unit"])
    duplicates = sorted(sample for sample, units in sample_units.items() if len(units) > 1)
    missing = [item["point_id"] or item["sample_id"] for item in by_point.values()
               if not item["returned_ids"]]
    invalid_ids = sorted({item["sample_id"] for item in by_point.values()
                          if item["sample_id"] and not SAMPLE_RE.match(item["sample_id"])})
    far = [item["point_id"] for item in by_point.values()
           if item["offset"] is not None and item["offset"] > max_offset]
    no_photo = [item["point_id"] for item in by_point.values()
                if item["returned_ids"] and item["photos"] < 1]
    unit_photos: dict[str, int] = {}
    for item in by_point.values():
        if item["unit"]:
            unit_photos[item["unit"]] = unit_photos.get(item["unit"], 0) + item["photos"]
    thin_units = sorted(unit for unit, count in unit_photos.items() if count < PHOTO_MIN_UNIT)
    categories = None
    if photos_root:
        categories = scan_photo_categories(Path(photos_root))
    status = "PASS"
    if missing or extra or duplicates or invalid_ids or no_photo:
        status = "FAIL"
    elif far or thin_units or no_gps or (categories and categories["status"] != "PASS"):
        status = "NEEDS_REVIEW"
    report = {
        "photo_categories": categories,
        "status": status,
        "design_points": len(by_point),
        "returned_rows": len(returned),
        "missing": missing,
        "extra": sorted(set(extra)),
        "duplicate_sample_ids": duplicates,
        "invalid_sample_ids": invalid_ids,
        "offset_over_limit": {"limit_m": max_offset, "points": far},
        "points_without_photo": no_photo,
        "rows_without_gps": no_gps,
        "units_below_photo_minimum": {"minimum": PHOTO_MIN_UNIT, "units": thin_units},
        "per_point": [{key: (sorted(value) if isinstance(value, set) else value)
                       for key, value in item.items()} for item in by_point.values()],
        "note": "结构核对不替代现场真实性与照片内容审核；空缺项不得由工具补造。",
    }
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--returned", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-offset", type=float, default=15.0)
    parser.add_argument("--photos", type=Path, help="回传照片目录；按目录名/文件名核对类别")
    args = parser.parse_args()
    result = run(args.design, args.returned, args.out, args.max_offset, args.photos)
    print(json.dumps({key: result[key] for key in ("status", "design_points", "returned_rows",
                                                   "missing", "extra", "duplicate_sample_ids")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
