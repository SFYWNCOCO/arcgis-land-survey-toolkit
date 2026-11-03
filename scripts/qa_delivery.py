# -*- coding: utf-8 -*-
"""Delivery QA for the land-survey toolkit.

Pure helpers can be tested with system Python.  The ArcGIS checks run with
ArcGIS Pro Python and produce a JSON report; ``--strict`` returns exit 1 on
any FAIL (UNVERIFIED is allowed unless ``--strict-unverified`` is used).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lsplan import load_profile, min_area_for_dlbm, read_rgb_contract  # noqa: E402


def required_layers(profile: dict) -> list[str]:
    return list(profile["layers"].keys())


def area_violation(profile: dict, dlbm: str | None, area_m2: float) -> dict | None:
    limit = min_area_for_dlbm(profile, dlbm)
    if area_m2 + 1e-9 < limit:
        return {"dlbm": dlbm, "area_m2": round(float(area_m2), 3), "limit_m2": limit}
    return None


def evaluate_color_contract(path: str) -> dict:
    rows = read_rgb_contract(path)
    return {"status": "PASS" if rows else "UNVERIFIED", "rows": len(rows), "path": path}


def _add(findings: list[dict], status: str, code: str, detail: str) -> None:
    findings.append({"status": status, "code": code, "detail": detail})


def run_arcpy_checks(gdb: str, profile: dict, epsg: int, color_contract: str) -> dict:
    import arcpy
    findings: list[dict] = []
    arcpy.env.workspace = gdb
    fds = os.path.join(gdb, "LAND_SURVEY")
    if not arcpy.Exists(fds):
        _add(findings, "FAIL", "missing_dataset", f"缺少要素数据集：{fds}")
        return {"status": "FAIL", "findings": findings}
    present = {os.path.basename(str(fc)) for fc in (arcpy.ListFeatureClasses("*", "", "LAND_SURVEY") or [])}
    missing = [name for name in required_layers(profile) if name not in present]
    if missing:
        _add(findings, "FAIL", "missing_layers", "缺少图层：" + ", ".join(missing))
    else:
        _add(findings, "PASS", "layer_inventory", f"图层齐全 {len(present)} 个")

    for name, spec in profile["layers"].items():
        fc = os.path.join(fds, name)
        if not arcpy.Exists(fc):
            continue
        desc = arcpy.Describe(fc)
        sr = getattr(desc.spatialReference, "factoryCode", None)
        if sr != epsg:
            _add(findings, "FAIL", "crs_mismatch", f"{name} EPSG:{sr} != {epsg}")
        if spec["geometry"] == "POLYGON" and spec.get("min_area_rule"):
            fields = ["SHAPE@AREA", "DLBM", "BSM"]
            bad = []
            with arcpy.da.SearchCursor(fc, fields) as cur:
                for area, dlbm, bsm in cur:
                    v = area_violation(profile, dlbm, float(area or 0.0))
                    if v:
                        bad.append({"BSM": bsm, **v})
            if bad:
                _add(findings, "FAIL", f"min_area:{name}", json.dumps(bad[:20], ensure_ascii=False))
            else:
                _add(findings, "PASS", f"min_area:{name}", "未发现低于配置阈值的图斑")

    topo = os.path.join(fds, profile["topology"]["name"])
    if arcpy.Exists(topo):
        try:
            arcpy.management.ValidateTopology(topo)
            arcpy.management.ExportTopologyErrors(topo, "in_memory", "TOPO_ERR")
            errs = arcpy.ListFeatureClasses("TOPO_ERR*", "", "in_memory") or []
            count = sum(int(arcpy.management.GetCount(fc)[0]) for fc in errs)
            if count:
                _add(findings, "FAIL", "topology", f"拓扑残留错误 {count} 个")
            else:
                _add(findings, "PASS", "topology", "拓扑验证无残留错误")
        except Exception as exc:  # pragma: no cover - depends on ArcGIS runtime
            _add(findings, "UNVERIFIED", "topology", f"拓扑验证未完成：{exc}")
    else:
        _add(findings, "FAIL", "missing_topology", f"缺少拓扑：{topo}")

    col = evaluate_color_contract(color_contract)
    _add(findings, col["status"], "style_color_contract",
         f"{col['status']} rows={col['rows']} path={col['path']}")
    status = "FAIL" if any(x["status"] == "FAIL" for x in findings) else ("UNVERIFIED" if any(x["status"] == "UNVERIFIED" for x in findings) else "PASS")
    return {"status": status, "findings": findings}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "standard_profile.json"))
    ap.add_argument("--gdb")
    ap.add_argument("--epsg", type=int, default=4525)
    ap.add_argument("--color-contract", default=os.path.join(ROOT, "config", "style_color_freeze.csv"))
    ap.add_argument("--report", default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--strict-unverified", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    profile = load_profile(a.profile)
    if a.selftest:
        assert required_layers(profile)[0] == "LC_Buildings"
        assert area_violation(profile, "1202", 199)["limit_m2"] == 200
        assert area_violation(profile, "0101", 399)["limit_m2"] == 400
        assert area_violation(profile, "0101", 400) is None
        print("DELIVERY_QA_SELFTEST PASS")
        return 0
    if not a.gdb:
        ap.error("--gdb is required unless --selftest")
    try:
        import arcpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit("交付 QA 必须使用 ArcGIS Pro Python 环境") from exc
    report = run_arcpy_checks(a.gdb, profile, a.epsg, a.color_contract)
    report["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    report["gdb"] = a.gdb
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.report:
        with open(a.report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if report["status"] == "FAIL":
        return 1
    if a.strict_unverified and report["status"] == "UNVERIFIED":
        return 1
    if a.strict and report["status"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())