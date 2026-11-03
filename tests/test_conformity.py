"""符合性评价：阈值边界、一票否决、直接判不合格与缺值处理。"""
from __future__ import annotations

from acceptance import conformity as C


def _indicators(**overrides):
    values = {
        "有效土层厚度": 50,
        "土壤有机质含量": 12,
        "土壤pH": 6.5,
        "地形坡度": 10,
        "田面平整程度": "基本平整",
        "道路通行条件": "基本可通达",
        "水资源保障条件": "基本满足",
        "土壤侵入体及砾石": {"10_100mm_percent": 10, "over_100mm": False},
        "土壤污染状况": {"ph": 6.5, "镉": 0.3, "汞": 0.2, "砷": 20, "铅": 30, "铬": 60},
    }
    values.update(overrides)
    return values


def _evaluate(land_use="水田", **overrides):
    return C.evaluate({"unit": "U1", "utilization": land_use,
                       "indicators": _indicators(**overrides)})


def test_all_pass_is_pass():
    result = _evaluate()
    assert result["status"] == "PASS"
    assert result["result"] == "合格"


def test_thickness_threshold_depends_on_land_use():
    assert _evaluate("旱地", 有效土层厚度=40)["status"] == "PASS"
    assert _evaluate("旱地", 有效土层厚度=39)["status"] == "FAIL"
    assert _evaluate("水田", 有效土层厚度=49)["status"] == "FAIL"
    assert _evaluate("水浇地", 有效土层厚度=50)["status"] == "PASS"


def test_organic_matter_and_ph_boundaries():
    assert _evaluate("旱地", 土壤有机质含量=8)["status"] == "PASS"
    assert _evaluate("旱地", 土壤有机质含量=7.9)["status"] == "FAIL"
    assert _evaluate(土壤pH=4.2)["status"] == "PASS"
    assert _evaluate(土壤pH=4.1)["status"] == "FAIL"
    assert _evaluate(土壤pH=8.5)["status"] == "PASS"
    assert _evaluate(土壤pH=8.6)["status"] == "FAIL"


def test_slope_and_ordinal_boundaries():
    assert _evaluate(地形坡度=25)["status"] == "PASS"
    assert _evaluate(地形坡度=25.1)["status"] == "FAIL"
    assert _evaluate(田面平整程度="基本平整")["status"] == "PASS"
    assert _evaluate(田面平整程度="不平整")["status"] == "FAIL"
    assert _evaluate(道路通行条件="基本可通达")["status"] == "PASS"
    assert _evaluate(道路通行条件="不可通达")["status"] == "FAIL"


def test_intrusion_two_conditions():
    assert _evaluate(土壤侵入体及砾石={"10_100mm_percent": 20, "over_100mm": False})["status"] == "PASS"
    assert _evaluate(土壤侵入体及砾石={"10_100mm_percent": 20.1, "over_100mm": False})["status"] == "FAIL"
    assert _evaluate(土壤侵入体及砾石={"10_100mm_percent": 1, "over_100mm": True})["status"] == "FAIL"


def test_pollution_limits_follow_ph_bands():
    assert _evaluate(土壤污染状况={"ph": 5.0, "镉": 1.5, "汞": 2.0, "砷": 200, "铅": 400, "铬": 800})["status"] == "PASS"
    assert _evaluate(土壤污染状况={"ph": 5.0, "镉": 1.6, "汞": 2.0, "砷": 200, "铅": 400, "铬": 800})["status"] == "FAIL"
    assert _evaluate(土壤污染状况={"ph": 8.0, "镉": 4.0, "汞": 6.0, "砷": 100, "铅": 1000, "铬": 1300})["status"] == "PASS"
    assert _evaluate(土壤污染状况={"ph": 8.0, "镉": 4.1, "汞": 6.0, "砷": 100, "铅": 1000, "铬": 1300})["status"] == "FAIL"


def test_missing_values_are_unverified_not_pass():
    result = _evaluate(土壤pH=None)
    assert result["status"] == "UNVERIFIED"
    assert "土壤pH" in result["unverified"]


def test_one_failure_vetoes_everything():
    result = _evaluate(土壤pH=8.6)
    assert result["status"] == "FAIL"
    assert "土壤pH" in result["failures"]


def test_direct_fail_flag_is_enough():
    result = C.evaluate({"unit": "U1", "utilization": "水田",
                         "indicators": _indicators(),
                         "direct_fail": {"river_floodwater_protected": True}})
    assert result["status"] == "FAIL"
    assert result["direct_failures"]


def test_unknown_land_use_cannot_pick_threshold():
    result = C.evaluate({"unit": "U1", "utilization": "", "indicators": _indicators()})
    assert result["status"] == "UNVERIFIED"


def test_pollution_missing_ph_is_unverified():
    result = _evaluate(土壤污染状况={"镉": 0.3})
    assert result["status"] == "UNVERIFIED"


def test_config_is_loadable():
    assert "indicators" in C.load_config()
