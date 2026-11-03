# -*- coding: utf-8 -*-
"""Create the dual-scale land-survey FileGDB scaffold and topology.

Run with ArcGIS Pro Python for creation.  ``--dry-run`` and ``--selftest``
work with the system Python and do not require arcpy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lsplan import choose_gk_epsg, load_profile, validate_profile  # noqa: E402


def build_plan(profile: dict, epsg: int, out_gdb: str, scale: str = "engineering") -> list[str]:
    actions = [f"创建文件地理数据库：{out_gdb} (EPSG:{epsg}, scale={scale})"]
    actions.append("创建要素数据集：LAND_SURVEY")
    for name, spec in profile["layers"].items():
        actions.append(f"创建 {name}: {spec['geometry']} / {spec['role']}")
    actions.append(f"创建拓扑：{profile['topology']['name']}")
    for rule in profile["topology"]["rules"]:
        actions.append(f"拓扑规则：{rule['rule']} -> {rule['fc']}")
    actions.append("创建 QC_RUN_LOG / QC_THRESHOLDS 检查表")
    return actions


def add_common_fields(fc: str, profile: dict) -> None:
    import arcpy
    for f in profile["common_fields"]:
        kwargs = {
            "field_name": f["name"],
            "field_type": f["type"],
            "field_alias": f.get("alias", f["name"]),
            "field_is_nullable": "NULLABLE",
        }
        if f["type"] == "TEXT":
            kwargs["field_length"] = int(f.get("length", 255))
        arcpy.management.AddField(fc, **kwargs)
        if f.get("domain"):
            arcpy.management.AssignDomainToField(fc, f["name"], f["domain"])


def create_domains(gdb: str, profile: dict) -> None:
    import arcpy
    for name, values in profile["domains"].items():
        arcpy.management.CreateDomain(gdb, name, name, "TEXT", "CODED")
        for code, label in values:
            arcpy.management.AddCodedValueToDomain(gdb, name, code, label)


def create_qc_tables(gdb: str, profile: dict) -> None:
    import arcpy
    log = os.path.join(gdb, "QC_RUN_LOG")
    arcpy.management.CreateTable(gdb, "QC_RUN_LOG")
    for name, typ, length in (("RUN_ID", "TEXT", 32), ("RUN_DATE", "DATE", None),
                              ("ACTION", "TEXT", 64), ("STATUS", "TEXT", 16),
                              ("DETAIL", "TEXT", 512)):
        kw = {"field_name": name, "field_type": typ, "field_is_nullable": "NULLABLE"}
        if length:
            kw["field_length"] = length
        arcpy.management.AddField(log, **kw)
    th = os.path.join(gdb, "QC_THRESHOLDS")
    arcpy.management.CreateTable(gdb, "QC_THRESHOLDS")
    for name, typ, length in (("RULE", "TEXT", 64), ("VALUE", "TEXT", 64),
                              ("UNIT", "TEXT", 16), ("SOURCE", "TEXT", 255)):
        arcpy.management.AddField(th, name, typ, field_length=length)
    rows = [
        ("construction_min_area", str(profile["thresholds"]["min_area_m2"]["construction"]), "m2", "年度变更调查 4.5"),
        ("agriculture_min_area", str(profile["thresholds"]["min_area_m2"]["agriculture"]), "m2", "年度变更调查 4.5"),
        ("other_min_area", str(profile["thresholds"]["min_area_m2"]["other"]), "m2", "年度变更调查 4.5"),
        ("obvious_boundary", str(profile["thresholds"]["boundary_mm"]["obvious"]), "map_mm", "年度变更调查 9.3.4"),
        ("non_obvious_boundary", str(profile["thresholds"]["boundary_mm"]["non_obvious"]), "map_mm", "年度变更调查 9.3.4"),
        ("horizontal_crs", profile["math_basis"]["horizontal"], "text", "国家年度变更调查 4.6.1"),
        ("vertical_datum", profile["math_basis"]["vertical"], "text", "国家年度变更调查 4.6.2"),
    ]
    with arcpy.da.InsertCursor(th, ["RULE", "VALUE", "UNIT", "SOURCE"]) as cur:
        for row in rows:
            cur.insertRow(row)
    with arcpy.da.InsertCursor(log, ["RUN_ID", "RUN_DATE", "ACTION", "STATUS", "DETAIL"]) as cur:
        cur.insertRow([
            dt.datetime.now().strftime("%Y%m%d%H%M%S"),
            dt.datetime.now(),
            "bootstrap",
            "OK",
            json.dumps(profile["sources"], ensure_ascii=False),
        ])


def create_project(out_gdb: str, epsg: int, profile: dict, scale: str = "engineering", overwrite: bool = False) -> None:
    import arcpy
    if arcpy.Exists(out_gdb):
        if not overwrite:
            raise FileExistsError(f"输出已存在：{out_gdb}；如确需覆盖请显式传 --overwrite")
        arcpy.management.Delete(out_gdb)
    parent = os.path.dirname(os.path.abspath(out_gdb))
    name = os.path.basename(out_gdb)
    os.makedirs(parent, exist_ok=True)
    arcpy.management.CreateFileGDB(parent, name, "CURRENT")
    sr = arcpy.SpatialReference(int(epsg))
    fds = os.path.join(out_gdb, "LAND_SURVEY")
    arcpy.management.CreateFeatureDataset(out_gdb, "LAND_SURVEY", sr)
    create_domains(out_gdb, profile)
    for layer, spec in profile["layers"].items():
        fc = os.path.join(fds, layer)
        arcpy.management.CreateFeatureclass(fds, layer, spec["geometry"], None,
                                            "DISABLED", "DISABLED", sr)
        add_common_fields(fc, profile)
    topology = os.path.join(fds, profile["topology"]["name"])
    tol_key = "engineering_1_2000" if scale == "engineering" else "legal_1_10000"
    tol = float(profile["thresholds"]["geometry_tolerance_m"][tol_key])
    arcpy.management.CreateTopology(fds, profile["topology"]["name"], tol)
    for layer in profile["layers"]:
        arcpy.management.AddFeatureClassToTopology(topology, os.path.join(fds, layer), "1", "1")
    for rule in profile["topology"]["rules"]:
        arcpy.management.AddRuleToTopology(topology, rule["rule"], os.path.join(fds, rule["fc"]))
    create_qc_tables(out_gdb, profile)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "standard_profile.json"))
    ap.add_argument("--out-gdb", required=False, help="输出 .gdb 路径")
    ap.add_argument("--center-lon", type=float, default=109.8, help="项目中心经度，用于选择3度带 EPSG")
    ap.add_argument("--scale", choices=("engineering", "legal"), default="engineering", help="engineering=1:2000; legal=1:10000")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    profile = load_profile(a.profile)
    validate_profile(profile)
    if a.selftest:
        assert len(profile["layers"]) == 12
        assert choose_gk_epsg(a.center_lon, profile) in {4523, 4524, 4525}
        print("BOOTSTRAP_SELFTEST PASS")
        return 0
    if not a.out_gdb:
        ap.error("--out-gdb is required unless --selftest")
    epsg = choose_gk_epsg(a.center_lon, profile)
    if a.dry_run:
        print("\n".join(build_plan(profile, epsg, a.out_gdb, a.scale)))
        return 0
    try:
        import arcpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit("实际建库必须使用 ArcGIS Pro 的 Python 环境；非 ArcGIS 环境请使用 --dry-run") from exc
    create_project(a.out_gdb, epsg, profile, scale=a.scale, overwrite=a.overwrite)
    print(f"CREATED {a.out_gdb} EPSG:{epsg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())