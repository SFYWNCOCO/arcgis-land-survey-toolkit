# -*- coding: utf-8 -*-
"""Pure-Python rules for the dual-scale land survey toolkit.

This module deliberately does not import arcpy. ArcGIS Pro scripts import it for
parameter validation and can run their ``--dry-run``/``--selftest`` paths with
the system Python interpreter.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = TOOLKIT_ROOT / "config" / "standard_profile.json"


def load_profile(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PROFILE
    with p.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def choose_gk_epsg(longitude: float, profile: dict) -> int:
    """Return the CGCS2000 3-degree Gauss-Kruger EPSG for a longitude."""
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude out of range: {longitude}")
    zone = int(math.floor(longitude / 3.0 + 0.5))
    table = {int(k): int(v) for k, v in profile["math_basis"]["gk_epsg_by_zone"].items()}
    if zone not in table:
        raise ValueError(f"no configured EPSG for 3-degree zone {zone}")
    return table[zone]


def dms_to_dd(degrees: float, minutes: float, seconds: float, hemisphere: str = "N") -> float:
    """Convert degrees/minutes/seconds to decimal degrees.

    ``hemisphere`` is one of N, S, E, W.  S/W produce negative values.
    """
    hemisphere = (hemisphere or "N").strip().upper()
    if hemisphere not in {"N", "S", "E", "W"}:
        raise ValueError(f"invalid hemisphere: {hemisphere!r}")
    if degrees < 0 or minutes < 0 or seconds < 0:
        raise ValueError("degrees, minutes and seconds must be non-negative")
    if minutes >= 60 or seconds >= 60:
        raise ValueError("minutes and seconds must be below 60")
    value = float(degrees) + float(minutes) / 60.0 + float(seconds) / 3600.0
    return -value if hemisphere in {"S", "W"} else value


def map_mm_to_ground_m(mm: float, scale_denominator: float) -> float:
    """Convert a map distance in millimetres to ground metres."""
    if mm < 0 or scale_denominator <= 0:
        raise ValueError("mm must be >= 0 and scale denominator > 0")
    return float(mm) / 1000.0 * float(scale_denominator)


def min_area_for_rule(profile: dict, rule: str) -> float:
    values = profile["thresholds"]["min_area_m2"]
    if rule not in values:
        raise KeyError(f"unknown minimum-area rule: {rule}")
    return float(values[rule])


def rule_for_dlbm(profile: dict, dlbm: str | None) -> str:
    """Map a GB/T 21010 DLBM code to the configured minimum-area rule."""
    mapping = profile["thresholds"]["dlbm_min_area_rule"]
    code = (dlbm or "").strip()
    if code in mapping:
        return mapping[code]
    if len(code) >= 2 and code[:2] in mapping:
        return mapping[code[:2]]
    return mapping.get("default", "other")


def min_area_for_dlbm(profile: dict, dlbm: str | None) -> float:
    return min_area_for_rule(profile, rule_for_dlbm(profile, dlbm))


def ring_is_closed(points: list[list[float]] | list[tuple[float, float]], tolerance: float = 1e-9) -> bool:
    if len(points) < 4:
        return False
    x0, y0 = points[0]
    x1, y1 = points[-1]
    return abs(float(x0) - float(x1)) <= tolerance and abs(float(y0) - float(y1)) <= tolerance


def read_rgb_contract(path: str | Path) -> list[dict]:
    """Read a style colour contract CSV and validate its basic shape."""
    out = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"DLBM", "DLMC", "R", "G", "B"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"colour contract is missing columns: {sorted(required - set(reader.fieldnames or []))}")
        for row in reader:
            rgb = [int(row[k]) for k in ("R", "G", "B")]
            if any(v < 0 or v > 255 for v in rgb):
                raise ValueError(f"RGB out of range for {row.get('DLBM')}: {rgb}")
            row["RGB"] = tuple(rgb)
            out.append(row)
    return out


def rgb_within_tolerance(actual: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 5) -> bool:
    return all(abs(int(a) - int(e)) <= tolerance for a, e in zip(actual, expected))


def validate_profile(profile: dict) -> None:
    """Fail fast on a profile that would make downstream scripts ambiguous."""
    for key in ("math_basis", "thresholds", "scales", "layers"):
        if key not in profile:
            raise KeyError(f"profile missing {key}")
    expected = {200, 400, 600, 1500}
    actual = set(profile["thresholds"]["min_area_m2"].values())
    if not expected.issubset(actual):
        raise ValueError(f"profile min-area values incomplete: {actual}")
    if not profile["thresholds"]["boundary_mm"].get("obvious") == 0.3:
        raise ValueError("expected obvious-boundary threshold 0.3 mm")
    if not profile["thresholds"]["boundary_mm"].get("non_obvious") == 1.0:
        raise ValueError("expected non-obvious-boundary threshold 1.0 mm")
    for layer, spec in profile["layers"].items():
        if spec["geometry"] not in {"POINT", "POLYLINE", "POLYGON"}:
            raise ValueError(f"unsupported geometry for {layer}: {spec['geometry']}")


def selftest() -> None:
    p = load_profile()
    validate_profile(p)
    assert choose_gk_epsg(109.79852, p) == 4525
    assert choose_gk_epsg(108.0, p) == 4524
    assert choose_gk_epsg(105.0, p) == 4523
    assert abs(dms_to_dd(109, 47, 54.672, "E") - 109.79852) < 1e-6
    assert dms_to_dd(23, 59, 51.0, "S") < 0
    assert abs(map_mm_to_ground_m(0.3, 10000) - 3.0) < 1e-9
    assert abs(map_mm_to_ground_m(0.3, 2000) - 0.6) < 1e-9
    assert min_area_for_rule(p, "construction") == 200
    assert min_area_for_rule(p, "agriculture") == 400
    assert min_area_for_rule(p, "other") == 600
    assert rule_for_dlbm(p, "0101") == "agriculture"
    assert rule_for_dlbm(p, "1202") == "construction"
    assert ring_is_closed([[0, 0], [1, 0], [1, 1], [0, 0]])
    assert not ring_is_closed([[0, 0], [1, 0], [1, 1]])
    print("LS_PLAN_SELFTEST PASS")


if __name__ == "__main__":
    selftest()