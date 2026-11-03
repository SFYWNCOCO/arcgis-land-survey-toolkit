"""评价单元划分的纯规划测试：不依赖 ArcPy，可在系统 Python 下运行。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import unit_builder as U  # noqa: E402


def test_minimum_fragment_area_is_tied_to_sampling_feasibility():
    # 3 点、15 m 间距、5 m 避边 => 每点一格，格边 25 m。
    assert U.minimum_fragment_area_m2() == pytest.approx(3 * (15 + 2 * 5) ** 2)
    assert U.minimum_fragment_area_m2(
        min_points=1, min_spacing_m=10, inset_m=0
    ) == pytest.approx(100)
    assert U.minimum_fragment_area_m2() / U.MU_M2 == pytest.approx(2.8125, rel=1e-9)
    with pytest.raises(ValueError, match="最低点数"):
        U.minimum_fragment_area_m2(min_points=0)
    with pytest.raises(ValueError, match="最小间距"):
        U.minimum_fragment_area_m2(min_spacing_m=0)
    with pytest.raises(ValueError, match="避边"):
        U.minimum_fragment_area_m2(inset_m=-1)


def test_principal_axis_is_deterministic_and_follows_long_side():
    cx, cy, ux, uy = U._principal_axis([(0, 0), (200, 0), (200, 100), (0, 100)])
    assert (cx, cy) == pytest.approx((100, 50))
    assert (ux, uy) == pytest.approx((1, 0))
    # 旋转 45° 的长方形，主轴方向应随形状旋转，不应固定为 X 轴。
    angle = math.pi / 4
    axis = (math.cos(angle), math.sin(angle))
    normal = (-math.sin(angle), math.cos(angle))
    points = [
        (axis[0] * s + normal[0] * t, axis[1] * s + normal[1] * t)
        for s in (0, 200)
        for t in (0, 100)
    ]
    _, _, rx, ry = U._principal_axis(points)
    assert (rx, ry) == pytest.approx(axis, abs=1e-9)


def test_objective_orders_unit_count_then_cuts_then_split_sources_then_compactness():
    two = [
        {"area_m2": 100, "compactness": 0.8},
        {"area_m2": 100, "compactness": 0.8},
    ]
    three = [
        {"area_m2": 100, "compactness": 1.0},
        {"area_m2": 100, "compactness": 1.0},
        {"area_m2": 100, "compactness": 1.0},
    ]
    assert U.evaluate_partition(two)["score"] < U.evaluate_partition(three)["score"]
    assert (
        U.evaluate_partition(two, cut_count=0)["score"]
        < U.evaluate_partition(two, cut_count=1)["score"]
    )
    assert (
        U.evaluate_partition(two, split_source_count=0)["score"]
        < U.evaluate_partition(two, split_source_count=1)["score"]
    )
    compact = [
        {"area_m2": 100, "compactness": 0.9},
        {"area_m2": 100, "compactness": 0.9},
    ]
    loose = [
        {"area_m2": 100, "compactness": 0.3},
        {"area_m2": 100, "compactness": 0.3},
    ]
    assert U.evaluate_partition(compact)["score"] < U.evaluate_partition(loose)["score"]
    assert "单元数最少" in U.evaluate_partition(two)["policy"]


def test_traceability_keeps_many_to_many_with_area_shares():
    assignments = [
        {
            "pid": "A#1",
            "src_pid": "A",
            "unit_id": "U1",
            "area_m2": 60,
            "cut_type": "geometric",
        },
        {
            "pid": "A#2",
            "src_pid": "A",
            "unit_id": "U2",
            "area_m2": 40,
            "cut_type": "geometric",
        },
        {
            "pid": "B",
            "src_pid": "B",
            "unit_id": "U2",
            "area_m2": 100,
            "cut_type": "none",
        },
    ]
    trace = U.build_traceability(assignments)
    parcel_a = next(item for item in trace["parcels"] if item["source"] == "A")
    assert parcel_a["status"] == "SPLIT"
    assert parcel_a["area_m2"] == pytest.approx(100)
    assert {unit["unit"]: unit["share"] for unit in parcel_a["units"]} == pytest.approx(
        {"U1": 0.6, "U2": 0.4}
    )
    assert parcel_a["primary_unit"] == "U1"
    parcel_b = next(item for item in trace["parcels"] if item["source"] == "B")
    assert parcel_b["status"] == "WHOLE"
    unit_u2 = next(item for item in trace["units"] if item["unit"] == "U2")
    assert unit_u2["status"] == "MERGED"
    # 以 U2 总面积为分母：A=40/140，B=100/140。
    assert {
        source["source"]: source["share"] for source in unit_u2["sources"]
    } == pytest.approx({"A": 40 / 140, "B": 100 / 140})
    assert trace["checks"]["all_fragments_assigned"] is True
    assert trace["checks"]["source_shares_sum_to_one"] is True
    assert trace["checks"]["unit_shares_sum_to_one"] is True


def test_traceability_flags_duplicate_fragment_ids():
    trace = U.build_traceability(
        [
            {"pid": "A", "src_pid": "A", "unit_id": "U1", "area_m2": 100},
            {"pid": "A", "src_pid": "A", "unit_id": "U2", "area_m2": 100},
        ]
    )
    assert trace["checks"]["unique_fragment_ids"] is False
