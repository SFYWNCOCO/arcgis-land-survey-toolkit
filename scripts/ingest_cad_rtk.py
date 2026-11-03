# -*- coding: utf-8 -*-
"""CAD/RTK ingestion helpers for the land-survey toolkit.

Modes:
  dms-csv   Convert a CSV with D/M/S/HEMI columns to decimal-degree CSV.
  rtk       Create a point feature class from CSV and project it to CGCS2000 GK.
  cad       Convert CAD dataset layers to a FileGDB with CADToGeodatabase.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lsplan import choose_gk_epsg, dms_to_dd, load_profile  # noqa: E402


def convert_dms_csv(src: str, dst: str, lon_cols: tuple[str, str, str, str] = ("LON_D", "LON_M", "LON_S", "LON_HEMI"), lat_cols: tuple[str, str, str, str] = ("LAT_D", "LAT_M", "LAT_S", "LAT_HEMI")) -> int:
    n = 0
    with open(src, "r", encoding="utf-8-sig", newline="") as fin, open(dst, "w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        cols = list(reader.fieldnames or []) + ["LONGITUDE_DD", "LATITUDE_DD"]
        writer = csv.DictWriter(fout, fieldnames=cols)
        writer.writeheader()
        for row in reader:
            row["LONGITUDE_DD"] = f"{dms_to_dd(float(row[lon_cols[0]]), float(row[lon_cols[1]]), float(row[lon_cols[2]]), row[lon_cols[3]]):.10f}"
            row["LATITUDE_DD"] = f"{dms_to_dd(float(row[lat_cols[0]]), float(row[lat_cols[1]]), float(row[lat_cols[2]]), row[lat_cols[3]]):.10f}"
            writer.writerow(row)
            n += 1
    return n


def rtk_to_points(csv_path: str, out_gdb: str, center_lon: float, profile: dict,
                  lon_field: str = "LONGITUDE_DD", lat_field: str = "LATITUDE_DD",
                  z_field: str | None = None, out_name: str = "RTK_Points") -> tuple[str, str]:
    import arcpy
    csv_path = os.path.abspath(csv_path)
    out_gdb = os.path.abspath(out_gdb)
    epsg = choose_gk_epsg(center_lon, profile)
    wgs84 = arcpy.SpatialReference(4326)
    fds = os.path.join(out_gdb, "LAND_SURVEY")
    stage = os.path.join(fds, out_name + "_WGS84")
    arcpy.management.XYTableToPoint(csv_path, stage, lon_field, lat_field, z_field or None, wgs84)
    target = os.path.join(fds, f"{out_name}_CGCS2000_GK")
    arcpy.management.Project(stage, target, arcpy.SpatialReference(epsg))
    return stage, target


def cad_to_gdb(cad_path: str, out_gdb: str, out_name: str = "CAD_Converted") -> str:
    import arcpy
    cad_path = os.path.abspath(cad_path)
    out_gdb = os.path.abspath(out_gdb)
    out = os.path.join(out_gdb, out_name)
    arcpy.management.CADToGeodatabase(cad_path, out_gdb, out_name)
    return out


def write_manifest(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("dms-csv", "rtk", "cad"), required=False)
    ap.add_argument("--src")
    ap.add_argument("--dst")
    ap.add_argument("--out-gdb")
    ap.add_argument("--center-lon", type=float, default=109.8)
    ap.add_argument("--lon-field", default="LONGITUDE_DD")
    ap.add_argument("--lat-field", default="LATITUDE_DD")
    ap.add_argument("--z-field", default=None)
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "standard_profile.json"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    profile = load_profile(a.profile)
    if a.selftest:
        assert abs(dms_to_dd(109, 47, 54.672, "E") - 109.79852) < 1e-6
        assert abs(dms_to_dd(23, 59, 51, "N") - 23.9975) < 1e-6
        assert choose_gk_epsg(a.center_lon, profile) == 4525
        print("CAD_RTK_SELFTEST PASS")
        return 0
    if not a.mode or not a.src:
        ap.error("--mode and --src are required unless --selftest")
    if a.mode == "dms-csv":
        if not a.dst:
            ap.error("dms-csv requires --dst")
        n = convert_dms_csv(a.src, a.dst)
        print(f"DMS_CONVERTED {n} -> {a.dst}")
        return 0
    if not a.out_gdb:
        ap.error(f"{a.mode} requires --out-gdb")
    try:
        import arcpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit("实际转换必须使用 ArcGIS Pro Python 环境") from exc
    if a.mode == "rtk":
        stage, target = rtk_to_points(a.src, a.out_gdb, a.center_lon, profile, a.lon_field, a.lat_field, a.z_field)
        print(f"RTK_STAGE {stage}")
        print(f"RTK_TARGET {target}")
        return 0
    if a.mode == "cad":
        out = cad_to_gdb(a.src, a.out_gdb)
        print(f"CAD_CONVERTED {out}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())