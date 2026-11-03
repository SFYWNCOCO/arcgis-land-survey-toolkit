"""等级评价：权重、隶属函数、区间落位、两位小数与 fail-closed。"""
from __future__ import annotations

import pytest
from acceptance import grade_eval as G

INDICATORS = {
    "地形部位": "平原低阶",
    "水资源条件": "充分满足",
    "有效土层厚度": 100,
    "有机质": 30,
    "耕层质地": "壤土",
    "酸碱度": 6.8,
    "土壤容重": 1.2,
    "速效钾": 150,
    "排水能力": "充分满足",
    "有效磷": 30,
    "质地构型": "上松下紧型",
    "耕层厚度": 20,
}


def test_weights_sum_to_one_for_all_regions():
    params = G.load_parameters()
    for region in ("华南区", "西南区", "南岭区"):
        assert abs(sum(G.validate_weights(region, params).values()) - 1.0) < 1e-9


def test_documented_example_index_087_gives_grade_2_40():
    result = G.grade_for_index(0.8700, "南岭区")
    assert result["ZLDJ"] == 3
    assert result["JQZLDJ"] == 2.40


def test_open_ended_grades_do_not_invent_two_decimals():
    first = G.grade_for_index(0.99, "华南区")
    tenth = G.grade_for_index(0.1, "华南区")
    assert first["ZLDJ"] == 1 and first["JQZLDJ"] is None
    assert tenth["ZLDJ"] == 10 and tenth["JQZLDJ"] is None
    assert first["JQZLDJ状态"] == "UNVERIFIED"


def test_band_boundaries_belong_to_upper_grade():
    assert G.grade_for_index(0.8650, "华南区")["ZLDJ"] == 1
    assert G.grade_for_index(0.8400, "华南区")["ZLDJ"] == 2
    assert G.grade_for_index(0.6650, "华南区")["ZLDJ"] == 9
    assert G.grade_for_index(0.6649, "华南区")["ZLDJ"] == 10


def test_membership_boundary_rules():
    up = {"type": "戒上型", "a": 0.001, "c": 10, "lower": 5, "upper": 20}
    peak = {"type": "峰型", "a": 1.0, "c": 10, "lower": 5, "upper": 20}
    line = {"type": "负直线型", "a": 0.001, "b": 1.0, "lower": 100, "upper": 1000}
    assert G.membership(5, up) == 0
    assert G.membership(20, up) == 1
    assert G.membership(10, up) == pytest.approx(1.0)
    assert G.membership(5, peak) == 0
    assert G.membership(20, peak) == 0
    assert G.membership(10, peak) == pytest.approx(1.0)
    assert G.membership(100, line) == 1
    assert G.membership(1000, line) == 0


def test_unknown_concept_value_fails_closed():
    with pytest.raises(ValueError, match="不在表中"):
        G.concept_membership("华南区", "地形部位", "火山口")


def test_evaluate_unit_contributions_sum_to_index():
    result = G.evaluate_unit("南岭区", INDICATORS)
    assert result["status"] == "PASS"
    total = sum(item["加权值"] for item in result["contributions"])
    assert result["PJZHZS"] == pytest.approx(round(total, 4))
    assert 1 <= result["ZLDJ"] <= 10


def test_missing_indicator_is_unverified_not_partial():
    values = dict(INDICATORS)
    values.pop("有机质")
    result = G.evaluate_unit("南岭区", values)
    assert result["status"] == "UNVERIFIED"
    assert result["PJZHZS"] is None
    assert "有机质" in result["missing"]


def test_weight_tampering_stops_evaluation():
    params = G.load_parameters()
    params["regions"]["华南区"]["weights"]["地形部位"] += 0.01
    with pytest.raises(ValueError, match="权重和"):
        G.validate_weights("华南区", params)


def test_region_guard_uses_appendix_j_and_rejects_mismatch():
    assert G.region_guard("扶绥县") == "华南区"
    with pytest.raises(ValueError, match="不在附录 J"):
        G.region_guard("不存在县")
    with pytest.raises(ValueError, match="不符"):
        G.region_guard("扶绥县", "南岭区")
