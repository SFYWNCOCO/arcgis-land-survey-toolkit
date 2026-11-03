"""三口径核算：面积加权、等级口径、示例复算与空集处理。"""
from __future__ import annotations

import pytest
from acceptance import accounting as A


def _rows(pairs):
    return [{"grade": grade, "area_m2": area} for grade, area in pairs]


OCCUPIED_ALL = _rows([(2, 1250.44), (5, 5810.55), (6, 4189.57), (8, 7255.39)])
OCCUPIED_NONFARM = _rows([(6, 4820.14), (4, 510.57), (7, 4287.33)])
SUPPLEMENT = _rows([(2.89, 3357.82), (4.86, 4144.27), (3.42, 2672.84), (7.52, 9085.39)])


def test_documented_three_way_example_recomputes():
    result = A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, SUPPLEMENT)
    assert result["各类占用"]["average"] == 6.20
    assert result["非农建设占用"]["average"] == 6.34
    assert result["补充耕地"]["average"] == 5.57


def test_printed_557_typo_would_change_the_answer():
    wrong = _rows([(2.89, 3357.82), (4.86, 4144.27), (3.42, 2672.84), (5.57, 9085.39)])
    result = A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, wrong)
    assert result["补充耕地"]["average"] != 5.57
    assert result["补充耕地"]["average"] == pytest.approx(4.65, abs=0.01)


def test_supplement_below_all_requires_fertilization():
    result = A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, SUPPLEMENT)
    assert any("培肥改良" in item for item in result["结论"])


def test_supplement_above_nonfarm_can_be_reported():
    better = _rows([(8.0, 1000), (9.0, 1000)])
    result = A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, better)
    assert any("报备" in item for item in result["结论"])


def test_empty_or_zero_area_fails_loudly():
    with pytest.raises(ValueError, match="为空"):
        A.weighted_average([])
    with pytest.raises(ValueError, match="大于 0"):
        A.weighted_average([{"grade": 5, "area_m2": 0}])


def test_occupied_grade_must_be_integer():
    with pytest.raises(ValueError, match="整数"):
        A.account(_rows([(5.5, 100)]), OCCUPIED_NONFARM, SUPPLEMENT)


def test_supplement_grade_must_be_two_decimals():
    with pytest.raises(ValueError, match="两位小数"):
        A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, _rows([(5.555, 100)]))


def test_supplement_must_come_from_city_level():
    with pytest.raises(ValueError, match="市级"):
        A.account(OCCUPIED_ALL, OCCUPIED_NONFARM, SUPPLEMENT, supplementary_source="县级初鉴")
