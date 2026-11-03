# -*- coding: utf-8 -*-
"""附录C建库工具单元测试（系统 Python + pytest，不需要 arcpy）。

覆盖：字段状态分级、缺源即 NULL、BSM 注1后缀、面积重算舍入、缺口清单条数、
值域校验（BCGDLY 仅恢复/垦造）、占位文本禁止、字段结构与源哈希。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from acceptance import appendix_c as ac
from acceptance import qa
from acceptance.qa import APPENDIX_C, APPENDIX_C_DECIMALS, APPENDIX_C_VALUE_DOMAINS

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCE = Path(r"D:\work\.openclaw\高标准\示例区2025年度第一批耕地恢复.shp")
REAL_XMBH = "F99000120260000"
SOURCE_HASH_BASELINE = {
    ".shp": "E035B8D74C4521E6",
    ".dbf": "C56DC7C377A63954",
    ".prj": "9509514628E51854",
    ".shx": "FFBF6DB2099DACF9",
    ".cpg": "3AD3031F5503A440",
    ".shp.xml": "D50F505B6FA8A620",
}


def options(**overrides):
    base = dict(
        county_code="990001",
        county_name="示例区",
        project_name="示例市示例区2026年第一批次补充耕地",
    )
    base.update(overrides)
    return ac.BuildOptions(**base)


def source_row(**overrides):
    row = {
        "id_field": "DIKUAI_NO",
        "feature_id": "39",
        "landtype": "旱地",
        "purpose": "开垦",
        "project_no": REAL_XMBH,
        "patchtype": "新增",
        "note": None,
        "area_m2": 4533.4349,
        "gross_area_m2": 4533.4349,
        "hole_area_m2": 0.0,
        "net_area_m2": 4533.4349,
        "part_count": 1,
    }
    row.update(overrides)
    return row


def build_rows(**overrides):
    return ac.build_field_rows(source_row(**overrides), options())


def get_row(rows, name):
    return ac._row(rows, name)


def status_of(rows, name):
    return get_row(rows, name)["status"]


def value_of(rows, name):
    return get_row(rows, name)["value"]


EXPECTED_FIELD_STATUS = {
    "BSM": "MISSING",
    "XDM": "DERIVED",
    "XMC": "CONFIRMED",
    "XZDM": "MISSING",
    "XZMC": "MISSING",
    "BGQDLBM": "MISSING",
    "BGQDLMC": "MISSING",
    "DLBM": "DERIVED",
    "DLMC": "CONFIRMED",
    "ZLDWDM": "MISSING",
    "ZLDWMC": "MISSING",
    "TBMJ": "DERIVED",
    "KCDLBM": "MISSING",
    "KCXS": "MISSING",
    "KCMJ": "DERIVED",
    "TBDLMJ": "DERIVED",
    "GDLX": "MISSING",
    "GDPDJB": "MISSING",
    "GXSJ": "MISSING",
    "XMMC": "ASSUMED",
    "XMBH": "CONFIRMED",
    "DKBH": "CONFIRMED",
    "JZGDMJ": "ASSUMED",
    "SFYKT": "MISSING",
    "SFFK": "MISSING",
    "SFZPJ": "ASSUMED",
    "BCFS": "DERIVED",
    "BCGDLY": "DERIVED",
    "BZ": "MISSING",
}


# ---------------------------------------------------------------------------
# 契约来源与字段结构
# ---------------------------------------------------------------------------
def test_contract_comes_from_qa_not_copied():
    assert ac.APPENDIX_C is qa.APPENDIX_C
    assert ac.FIELD_ORDER == tuple(row[0] for row in APPENDIX_C)
    assert len(ac.FIELD_ORDER) == 29
    assert ac.APPENDIX_C_DECIMALS is APPENDIX_C_DECIMALS or ac.APPENDIX_C_DECIMALS == APPENDIX_C_DECIMALS


def test_field_descriptions_match_appendix_c():
    descriptions = ac.field_descriptions()
    assert len(descriptions) == 29
    by_name = {row[0]: row for row in descriptions}
    assert by_name["BSM"][1:4] == ["TEXT", "标识码", 30]
    assert by_name["TBMJ"][1:3] == ["DOUBLE", "图斑面积"]
    assert by_name["TBMJ"][3] == ""
    assert by_name["GXSJ"][1] == "DATE"
    assert by_name["KCXS"][1] == "FLOAT"
    assert by_name["BCGDLY"][3] == 10


class FakeField:
    def __init__(self, name, field_type, length):
        self.name = name
        self.type = field_type
        self.length = length


def test_compare_schema_passes_and_detects_mismatch():
    fields = [FakeField(n, t, l) for n, _a, t, l, _r, _d in APPENDIX_C]
    assert ac.compare_schema(fields)["status"] == "PASS"
    missing = [f for f in fields if f.name != "XDM"]
    result = ac.compare_schema(missing)
    assert result["status"] == "FAIL"
    assert result["missing"] == ["XDM"]
    wrong = [FakeField("XMC", "String", 10) if f.name == "XMC" else f for f in fields]
    result = ac.compare_schema(wrong)
    assert result["mismatched"][0]["field"] == "XMC"
    # 数值型长度是存储属性，不比长度。
    numeric = [FakeField("TBMJ", "Double", 8) if f.name == "TBMJ" else f for f in fields]
    assert ac.compare_schema(numeric)["status"] == "PASS"


# ---------------------------------------------------------------------------
# 状态分级
# ---------------------------------------------------------------------------
def test_all_29_fields_present_in_contract_order():
    rows = build_rows()
    assert [row["field"] for row in rows] == list(ac.FIELD_ORDER)
    assert len(rows) == 29


def test_status_classification_matches_rules():
    rows = build_rows()
    actual = {row["field"]: row["status"] for row in rows}
    assert actual == EXPECTED_FIELD_STATUS
    counts = ac.status_counts([rows])["per_field"]
    assert counts == {"CONFIRMED": 4, "DERIVED": 7, "ASSUMED": 3, "MISSING": 15}


def test_status_counts_multiple_features():
    rows = [build_rows(feature_id=str(i)) for i in (39, 30, 31)]
    counts = ac.status_counts(rows)
    assert counts["per_field"] == {"CONFIRMED": 4, "DERIVED": 7, "ASSUMED": 3, "MISSING": 15}
    assert counts["per_feature_row"] == {
        "CONFIRMED": 12, "DERIVED": 21, "ASSUMED": 9, "MISSING": 45,
    }


# ---------------------------------------------------------------------------
# 缺源即 NULL、缺口清单
# ---------------------------------------------------------------------------
def test_missing_source_writes_null_not_placeholder():
    rows = build_rows()
    for row in rows:
        if row["status"] == "MISSING":
            assert row["value"] is None, row["field"]
    for row in rows:
        if row["value"] is not None:
            assert not ac.is_placeholder(row["value"])
            assert str(row["value"]).strip() != ""


def test_placeholder_source_text_is_nulled_with_note():
    rows = build_rows(note="待填")
    bz = get_row(rows, "BZ")
    assert bz["value"] is None
    assert bz["status"] == "MISSING"
    assert "占位" in (bz["note"] or "")


def test_gap_list_has_15_rows_and_10_required():
    rows = build_rows()
    gap = ac.gap_table([rows], ["39"])
    assert len(gap) == 15
    required = [item["field"] for item in gap if item["required"] == "M"]
    conditional = [item["field"] for item in gap if item["required"] == "C"]
    assert required == ["BSM", "XZDM", "XZMC", "BGQDLBM", "BGQDLMC",
                        "ZLDWDM", "ZLDWMC", "GXSJ", "SFYKT", "SFFK"]
    assert conditional == ["KCDLBM", "KCXS", "GDLX", "GDPDJB", "BZ"]


def test_missing_required_does_not_include_conditional_fields():
    rows = build_rows()
    missing_required = {item["field"] for item in ac.missing_fields([rows], "M")}
    assert len(missing_required) == 10
    assert "GDLX" not in missing_required and "GDPDJB" not in missing_required
    assert get_row(rows, "GDLX")["required"] == "C"


def test_gap_rows_trace_each_feature():
    rows = [build_rows(feature_id=str(i)) for i in (39, 30)]
    gap = ac.gap_table(rows, ["39", "30"])
    assert {item["field"]: item["affected_features"] for item in gap}["BSM"] == ["39", "30"]


# ---------------------------------------------------------------------------
# BSM 注1后缀与长度
# ---------------------------------------------------------------------------
def test_bsm_suffixes_applied_across_features():
    base = "990001202600000001"
    rows_by_feature = [
        ac.build_field_rows(source_row(feature_id=str(i)), options(),
                            {"BSM": base})
        for i in (1, 2, 3)
    ]
    normalized, details = ac.apply_bsm_suffixes(rows_by_feature, ["1", "2", "3"])
    values = [ac.row_value(rows, "BSM") for rows in normalized]
    assert values == [base, base + "-1", base + "-2"]
    assert [detail["action"] for detail in details] == ["keep", "suffix", "suffix"]
    assert all(detail["feature_id"] for detail in details)
    assert "附录C注1" in (details[1]["note"] or "")


def test_bsm_without_source_stays_null():
    rows_by_feature, details = ac.apply_bsm_suffixes([build_rows()], ["39"])
    row = get_row(rows_by_feature[0], "BSM")
    assert row["value"] is None
    assert row["status"] == "MISSING"
    assert details[0]["action"] == "missing"


def test_bsm_overflow_is_nulled_not_truncated():
    base = "1" * 30
    rows_by_feature = [
        ac.build_field_rows(source_row(feature_id=str(i)), options(), {"BSM": base})
        for i in (1, 2)
    ]
    normalized, details = ac.apply_bsm_suffixes(rows_by_feature, ["1", "2"])
    assert ac.row_value(normalized[0], "BSM") == base
    assert ac.row_value(normalized[1], "BSM") is None
    assert details[1]["action"] == "overflow_null"
    assert get_row(normalized[1], "BSM")["status"] == "MISSING"


# ---------------------------------------------------------------------------
# 审核修复轮（E-1）：BSM 注1后缀必须全局唯一，不得撞已有值
# ---------------------------------------------------------------------------
def test_bsm_suffix_avoids_collision_with_existing_suffixed_value():
    # 审核 FAIL 用例：["A","A-1","A"] 旧实现给出 ["A","A-1","A-1"]（重复）
    normalized, details = ac.plan_bsm_suffixes(["A", "A-1", "A"])
    assert normalized == ["A", "A-1", "A-2"]
    assert len(set(normalized)) == 3
    assert details[2]["action"] == "suffix"
    assert details[2]["result"] == "A-2"
    # 第一次出现的值不得改动
    assert details[0]["result"] == "A" and details[0]["action"] == "keep"
    assert details[1]["result"] == "A-1" and details[1]["action"] == "keep"


def test_bsm_suffix_three_duplicates_stay_unique():
    normalized, _ = ac.plan_bsm_suffixes(["A", "A", "A"])
    assert normalized == ["A", "A-1", "A-2"]
    assert len(set(normalized)) == 3


def test_bsm_suffix_skips_preexisting_and_generated_candidates():
    # A-1 源数据已存在；A 重复两次 → 依次跳过 A-1、A-2，用 A-2、A-3
    normalized, details = ac.plan_bsm_suffixes(["A", "A-1", "A", "A"])
    assert normalized == ["A", "A-1", "A-2", "A-3"]
    assert len(set(normalized)) == 4
    assert details[2]["result"] == "A-2"
    assert details[3]["result"] == "A-3"


def test_bsm_suffix_length_overflow_and_fit():
    base30 = "1" * 30
    normalized, details = ac.plan_bsm_suffixes([base30, base30])
    assert normalized == [base30, None]
    assert details[1]["action"] == "overflow_null"
    base29 = "2" * 29
    normalized, details = ac.plan_bsm_suffixes([base29, base29])
    assert normalized == [base29, None]  # base29 + "-1" = 31 > 30
    assert details[1]["action"] == "overflow_null"
    base28 = "3" * 28
    normalized, details = ac.plan_bsm_suffixes([base28, base28])
    assert normalized == [base28, base28 + "-1"]  # 30 位刚好可容纳
    assert details[1]["action"] == "suffix"


def test_bsm_suffix_result_is_unique_for_mixed_input():
    values = ["A", "A-1", "A", "A-2", "A", "A"]
    normalized, _ = ac.plan_bsm_suffixes(values)
    assert len(normalized) == len(values)
    assert len(set(normalized)) == len(values)  # 无重复
    assert normalized[0] == "A" and normalized[1] == "A-1" and normalized[3] == "A-2"
    assert normalized[2] == "A-3" and normalized[4] == "A-4" and normalized[5] == "A-5"


def test_bsm_suffix_status_is_derived_not_confirmed():
    base = "990001202600000001"
    rows_by_feature = [
        ac.build_field_rows(source_row(feature_id=str(i)), options(), {"BSM": base})
        for i in (1, 2)
    ]
    normalized, _ = ac.apply_bsm_suffixes(rows_by_feature, ["1", "2"])
    first = get_row(normalized[0], "BSM")
    second = get_row(normalized[1], "BSM")
    assert first["status"] == "CONFIRMED"  # 经办原值
    assert second["status"] == "DERIVED"   # 注1后缀属推导，不得记 CONFIRMED
    assert "注1" in (second["note"] or "")


# ---------------------------------------------------------------------------
# 审核修复轮（E-2）：占位值判定（整值占位才判，正文含占位词不误杀）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("token", [
    "待确认", "待核实", "待补", "待定", "待填", "未知",
    "—", "-", "--", "－", "/", "//", "…", "××", "×",
    "N/A", "n/a", "NA", "null", "None", "TBD", "xx",
])
def test_provided_placeholder_value_is_null_and_gap(token):
    rows = ac.build_field_rows(source_row(), options(), {"BZ": token})
    row = get_row(rows, "BZ")
    assert row["value"] is None, token
    assert row["status"] == "MISSING", token
    assert token.strip() in (row["note"] or ""), token  # 记命中占位词
    assert ac.row_errors([rows]) == []
    assert ac.domain_violations([rows]) == []
    gap = ac.gap_table([rows], ["39"])
    assert "BZ" in [item["field"] for item in gap]


@pytest.mark.parametrize("text", [
    "无",
    "待确认后补",
    "暂无资料，见附件",
    "-1",
    "A-1",
    "--按批复调整",
    "该地块待核实后再定，暂记",
])
def test_placeholder_like_text_is_not_killed(text):
    rows = ac.build_field_rows(source_row(), options(), {"BZ": text})
    row = get_row(rows, "BZ")
    assert row["value"] == text
    assert row["status"] == "CONFIRMED"
    assert ac.is_placeholder(text) is False


def test_empty_provided_value_is_treated_as_not_provided():
    rows = ac.build_field_rows(source_row(), options(), {"BZ": ""})
    row = get_row(rows, "BZ")
    assert row["value"] is None
    assert row["status"] == "MISSING"
    # 空串不应产生“占位命中”备注
    assert "命中占位词" not in (row["note"] or "")


def test_placeholder_rule_on_source_values_too():
    rows = build_rows(note="待核实")
    row = get_row(rows, "BZ")
    assert row["value"] is None and row["status"] == "MISSING"
    assert "待核实" in (row["note"] or "")


# ---------------------------------------------------------------------------
# 面积重算与舍入
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    (4533.4349, 4533.43),
    (2.345, 2.35),
    (1.005, 1.01),
    (0.014, 0.01),
    (None, None),
])
def test_round_area_half_up(raw, expected):
    assert ac.round_area(raw) == expected


def test_area_fields_and_relation():
    # 无孔洞：毛=净，KCMJ 按几何孔洞合计取 0
    rows = build_rows(area_m2=4533.4349, gross_area_m2=4533.4349,
                      hole_area_m2=0.0, net_area_m2=4533.4349)
    assert value_of(rows, "TBMJ") == 4533.43
    assert value_of(rows, "KCMJ") == 0.0
    assert value_of(rows, "TBDLMJ") == 4533.43
    assert value_of(rows, "JZGDMJ") == 4533.43
    assert status_of(rows, "TBMJ") == "DERIVED"
    assert status_of(rows, "KCMJ") == "DERIVED"
    assert status_of(rows, "TBDLMJ") == "DERIVED"
    assert status_of(rows, "JZGDMJ") == "ASSUMED"


def test_area_gross_hole_net_semantics_with_holes():
    # 22 号实测：外环 11738.344、孔洞 441.766、净 11296.574
    rows = build_rows(area_m2=11296.574, gross_area_m2=11738.344,
                      hole_area_m2=441.766, net_area_m2=11296.574)
    assert value_of(rows, "TBMJ") == 11738.34
    assert value_of(rows, "KCMJ") == 441.77
    assert value_of(rows, "TBDLMJ") == 11296.57
    assert round(value_of(rows, "TBDLMJ") - (value_of(rows, "TBMJ") - value_of(rows, "KCMJ")), 2) == 0.0
    assert value_of(rows, "JZGDMJ") == 11296.57
    assert "外环面积合计" in (get_row(rows, "TBMJ")["source"])
    assert "孔洞" in (get_row(rows, "KCMJ")["note"] or "")


def test_tbdmj_subtracts_provided_kcmj():
    rows = ac.build_field_rows(
        source_row(area_m2=1000.0, gross_area_m2=1000.0,
                   hole_area_m2=None, net_area_m2=1000.0),
        options(), {"KCMJ": 300.0})
    assert value_of(rows, "KCMJ") == 300.0
    assert value_of(rows, "TBDLMJ") == 700.0
    assert status_of(rows, "JZGDMJ") == "MISSING"


def test_invalid_geometry_area_is_missing():
    rows = build_rows(area_m2=None, gross_area_m2=None,
                      hole_area_m2=None, net_area_m2=None)
    assert value_of(rows, "TBMJ") is None
    assert status_of(rows, "TBMJ") == "MISSING"
    assert value_of(rows, "KCMJ") is None
    assert status_of(rows, "KCMJ") == "MISSING"
    assert value_of(rows, "TBDLMJ") is None


# ---------------------------------------------------------------------------
# XDM / BCFS / BCGDLY / DLBM 推导与值域
# ---------------------------------------------------------------------------
def test_xdm_derived_from_xmbh():
    rows = build_rows()
    assert value_of(rows, "XDM") == "990001"
    assert status_of(rows, "XDM") == "DERIVED"


def test_xdm_rejects_conflicting_county_code():
    with pytest.raises(ValueError):
        ac.build_field_rows(source_row(), options(county_code="990002"))
    # 一致时不报错
    rows = ac.build_field_rows(source_row(), options(county_code="990001"))
    assert value_of(rows, "XDM") == "990001"


def test_bcfs_derived_from_first_char_of_xmbh():
    rows = build_rows()
    assert value_of(rows, "BCFS") == "F"
    assert status_of(rows, "BCFS") == "DERIVED"
    note = get_row(rows, "BCFS")["note"]
    assert "15位编号规则首字符推导" in note
    assert "广西耕地占补平衡动态监管系统验收阶段备案编号核对" in note


def test_bcfs_invalid_first_char_is_null_and_gap():
    rows = build_rows(project_no="X99000120261841")
    assert value_of(rows, "BCFS") is None
    assert status_of(rows, "BCFS") == "MISSING"
    assert "值域" in (get_row(rows, "BCFS")["note"] or "")


def test_bcfs_bad_length_is_missing():
    rows = build_rows(project_no="F9900012026")
    assert value_of(rows, "BCFS") is None
    assert status_of(rows, "BCFS") == "MISSING"
    assert value_of(rows, "XMBH") == "F9900012026"


def test_bcgdly_maps_purpose_within_domain():
    assert value_of(build_rows(purpose="开垦"), "BCGDLY") == "垦造"
    assert value_of(build_rows(purpose="恢复"), "BCGDLY") == "恢复"
    rows = build_rows(purpose="未知用途")
    assert value_of(rows, "BCGDLY") is None
    assert status_of(rows, "BCGDLY") == "MISSING"
    for value in ("垦造", "恢复"):
        assert value in APPENDIX_C_VALUE_DOMAINS["BCGDLY"]


def test_domain_violation_detected_for_provided_values():
    rows = ac.build_field_rows(source_row(), options(), {"BCGDLY": "其他"})
    issues = ac.domain_violations([rows])
    assert any(issue["field"] == "BCGDLY" for issue in issues)
    rows = ac.build_field_rows(source_row(), options(), {"SFYKT": "Y"})
    issues = ac.domain_violations([rows])
    assert any(issue["field"] == "SFYKT" for issue in issues)
    # 合法值不应报错
    assert ac.domain_violations([build_rows()]) == []


def test_dlbm_mapping_gb21010():
    assert value_of(build_rows(landtype="水田"), "DLBM") == "0101"
    assert value_of(build_rows(landtype="水浇地"), "DLBM") == "0102"
    assert value_of(build_rows(landtype="旱地"), "DLBM") == "0103"
    rows = build_rows(landtype="果园")
    assert value_of(rows, "DLBM") is None
    assert status_of(rows, "DLBM") == "MISSING"
    assert value_of(rows, "DLMC") == "果园"
    assert status_of(rows, "DLMC") == "CONFIRMED"


# ---------------------------------------------------------------------------
# 显式提供值的可追溯性
# ---------------------------------------------------------------------------
def test_provided_value_traceable_and_not_claimed_as_source():
    rows = ac.build_field_rows(source_row(), options(), {"XZDM": "990001001"})
    row = get_row(rows, "XZDM")
    assert row["value"] == "990001001"
    assert row["status"] == "CONFIRMED"
    assert "--attrs-json" in row["source"]
    assert "须核对" in (row["note"] or "")


def test_assumed_fields_are_traceable():
    rows = build_rows()
    for name in ("XMMC", "JZGDMJ", "SFZPJ"):
        row = get_row(rows, name)
        assert row["status"] == "ASSUMED"
        assert row["note"], name
    assert get_row(rows, "SFZPJ")["value"] == "否"


# ---------------------------------------------------------------------------
# 结构与长度校验
# ---------------------------------------------------------------------------
def test_row_errors_detect_overlong_and_placeholder():
    rows = ac.build_field_rows(source_row(), options(), {"DKBH": "9" * 255})
    errors = ac.row_errors([rows])
    assert any(error["field"] == "DKBH" for error in errors)
    rows = ac.build_field_rows(source_row(), options(), {"BZ": "待填"})
    assert ac.row_errors([rows]) == []  # 占位被置空，不进入写入值


def test_numeric_provided_value_must_parse():
    rows = ac.build_field_rows(source_row(), options(), {"TBMJ": "1000.5"})
    assert value_of(rows, "TBMJ") == 1000.5
    with pytest.raises(ValueError):
        ac.build_field_rows(source_row(), options(), {"TBMJ": "一千"})


def test_parse_date_rules():
    assert ac.parse_date("20260817") == dt.datetime(2026, 8, 17)
    assert ac.parse_date("2026-08-17") == dt.datetime(2026, 8, 17)
    assert ac.parse_date(None) is None
    with pytest.raises(ValueError):
        ac.parse_date("2026/08/17")


# ---------------------------------------------------------------------------
# 多部件 vs 孔洞（附录C注6）
# ---------------------------------------------------------------------------
def test_polygon_ring_stats_distinguishes_holes_from_multipart():
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
    other_outer = [[20, 0], [20, 10], [30, 10], [30, 0], [20, 0]]

    single = ac.polygon_ring_stats({"rings": [outer]})
    assert single["ring_count"] == 1
    assert single["exterior_rings"] == 1
    assert single["hole_rings"] == 0
    assert single["multipart"] is False

    with_hole = ac.polygon_ring_stats({"rings": [outer, hole]})
    assert with_hole["exterior_rings"] == 1
    assert with_hole["hole_rings"] == 1
    assert with_hole["multipart"] is False
    assert round(with_hole["gross_area"], 2) == 100.0
    assert round(with_hole["hole_area"], 2) == 4.0
    assert round(with_hole["net_area"], 2) == 96.0

    two_parts = ac.polygon_ring_stats({"rings": [outer, other_outer]})
    assert two_parts["exterior_rings"] == 2
    assert two_parts["multipart"] is True

    # 接受 Esri JSON 字符串（与 geometry.JSON 一致）
    assert ac.polygon_ring_stats(json.dumps({"rings": [outer, hole]}))["hole_rings"] == 1
    assert ac.polygon_ring_stats({"rings": []})["multipart"] is False


# ---------------------------------------------------------------------------
# 源文件哈希、项目名冲突（依赖真实源数据时跳过）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="真实源数据不在本机")
def test_source_hashes_match_baseline():
    hashes = ac.source_family_hashes(REAL_SOURCE)
    for suffix, expected in SOURCE_HASH_BASELINE.items():
        assert hashes[suffix]["sha256_16"] == expected, suffix


@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="真实源数据不在本机")
def test_project_name_conflict_records_both_sources():
    conflicts = ac.project_name_conflicts(REAL_SOURCE, "示例市示例区2026年第一批次补充耕地")
    assert conflicts
    first = conflicts[0]
    assert first["status"] == "待核"
    assert "2025" in json.dumps(first["values"], ensure_ascii=False)
    assert "2026" in json.dumps(first["values"], ensure_ascii=False)
    assert first["year_conflict"] is True


def test_source_metadata_missing_xml_is_safe(tmp_path):
    shp = tmp_path / "demo.shp"
    shp.write_bytes(b"")
    assert ac.read_source_metadata_names(shp)["xmbh_source_name"] is None


# ---------------------------------------------------------------------------
# CSV/JSON 落盘
# ---------------------------------------------------------------------------
def test_write_csv_and_json_refuse_overwrite(tmp_path):
    csv_path = tmp_path / "out.csv"
    ac.write_csv(csv_path, ["字段名", "状态"], [{"字段名": "BSM", "状态": "MISSING"}])
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0] == "字段名,状态"
    with pytest.raises(FileExistsError):
        ac.write_csv(csv_path, ["字段名"], [])
    json_path = tmp_path / "out.json"
    ac.write_json(json_path, {"原因": "缺源"})
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"原因": "缺源"}
    with pytest.raises(FileExistsError):
        ac.write_json(json_path, {})


def test_field_source_rows_have_trace_columns():
    rows_by_feature = [build_rows()]
    table = ac.field_source_rows(rows_by_feature, ["39"])
    assert len(table) == 29
    assert set(table[0]) == {"地块编号", "序号", "字段名", "字段别名", "必填性", "状态", "取值", "来源依据", "备注"}
    assert table[0]["地块编号"] == "39"
    assert {row["状态"] for row in table} <= set(ac.STATUSES)


def test_summarize_counts_and_lists():
    summary = ac.summarize([build_rows()], ["39"])
    assert summary["field_count"] == 29
    assert summary["feature_count"] == 1
    assert len(summary["missing_required_fields"]) == 10
    assert len(summary["missing_conditional_fields"]) == 5
    assert summary["value_domain_violations"] == []
    assert summary["row_errors"] == []


# ---------------------------------------------------------------------------
# 终验前遗留 NEEDS_REVIEW 修复（task-6）
# ---------------------------------------------------------------------------
def _load_builder_module():
    """加载 scripts/build_appendix_c.py（不执行 main，不依赖 arcpy）。"""
    name = "build_appendix_c_under_test"
    if name in sys.modules:
        return sys.modules[name]
    path = TOOLKIT_ROOT / "scripts" / "build_appendix_c.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _FakeGeometry:
    """极简 arcpy Polygon 替身：支持 area/partCount/isMultipart/JSON。"""

    def __init__(self, area=4533.4302, part_count=1, hole=False):
        self.area = area
        self.partCount = part_count
        self.isMultipart = bool(part_count > 1 or hole)
        outer = [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0], [0.0, 0.0]]
        hole_ring = [[2.0, 2.0], [4.0, 2.0], [4.0, 4.0], [2.0, 4.0], [2.0, 2.0]]
        self.JSON = json.dumps({"rings": [outer] + ([hole_ring] if hole else [])})


class _FakeCursor:
    """arcpy.da.SearchCursor 的最小替身（支持 with 上下文）。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def __enter__(self):
        return iter(self._rows)

    def __exit__(self, *_exc):
        return False


class _FakeArcPyBuild(types.ModuleType):
    """build() 所需的最小假 arcpy：Describe/ListFields/SearchCursor/Delete。"""

    def __init__(self):
        super().__init__("arcpy")
        self.deleted = []
        self.management = types.SimpleNamespace(
            GetCount=lambda _path: ["1"], Delete=self._delete,
        )
        self.da = types.SimpleNamespace(SearchCursor=self._search_cursor)

    def Exists(self, path):
        return Path(path).exists()

    def Describe(self, _path):
        return types.SimpleNamespace(
            shapeType="Polygon",
            spatialReference=types.SimpleNamespace(
                factoryCode=4524, name="CGCS2000_3_Degree_GK_Zone_36"),
        )

    def ListFields(self, _path):
        return [types.SimpleNamespace(name="DIKUAI_NO", type="String", length=50)]

    def _search_cursor(self, _path, names):
        row = {"OID@": 1, "DIKUAI_NO": "39", "SHAPE@": _FakeGeometry()}
        return _FakeCursor([tuple(row.get(name) for name in names)])

    def _delete(self, path):
        self.deleted.append(str(path))
        target = Path(path)
        if target.exists():
            shutil.rmtree(target)


def _fake_qa_result(fields, features):
    empty = [{"field": field, "alias": field} for field in fields for _ in range(features)]
    return {
        "item": "补充耕地质量鉴定图斑",
        "status": "FAIL",
        "detail": {
            "count": features,
            "fields": {"status": "PASS", "missing": [], "mismatched": []},
            "values": {
                "status": "FAIL",
                "empty_required": empty,
                "invalid_domain": [],
                "duplicate_BSM": [],
                "coded_values_needing_review": {},
            },
            "geometry_review": {
                "status": "NEEDS_REVIEW",
                "multipart_count": 0,
                "multipart_BSM": [],
                "multipart_rule": "partCount>1",
                "slit_connector_review": {"status": "NEEDS_REVIEW"},
                "hole_ring_review": {"status": "NEEDS_REVIEW", "count": 0, "parcels": []},
            },
        },
    }


def test_qa_missing_rows_note_uses_measured_numbers(monkeypatch):
    """NEEDS_REVIEW-1：note 不得硬编码 10×9=90，须按实测行级/字段级数量拼接。"""
    builder = _load_builder_module()
    fields = ["XZDM", "XZMC", "BGQDLBM", "BGQDLMC", "ZLDWDM",
              "ZLDWMC", "GXSJ", "SFYKT", "SFFK"]  # BSM 已由 attrs 提供 → 9 字段
    qa_result = _fake_qa_result(fields, 9)  # 9 字段 × 9 要素 = 81 行
    fake = types.ModuleType("arcpy")
    fake.Exists = lambda _path: True
    fake.da = types.SimpleNamespace(
        SearchCursor=lambda _layer, _fields: _FakeCursor([
            ("39", 4533.43, 0.0, 4533.43, _FakeGeometry()),
        ]),
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake)
    monkeypatch.setattr(qa, "_check_appendix_c", lambda _layer: qa_result)

    output = builder.generate_qa_report(Path("demo.gdb/补充耕地质量鉴定图斑"))
    note = output["missing_required_rows_note"]
    assert output["missing_required_rows"] == 81
    assert output["missing_required_fields_count"] == 9
    assert "81" in note
    assert "9" in note
    assert "90" not in note  # 旧实现硬编码本批 10×9
    assert "口径" in note  # 保留行级/字段级口径不同的说明


def test_qa_exception_rolls_back_created_gdb(monkeypatch, tmp_path):
    """NEEDS_REVIEW-2：质检抛错须纳入落盘 try/except，删除本次新建 gdb。"""
    builder = _load_builder_module()
    source = tmp_path / "src.shp"
    source.write_bytes(b"fake-shp-for-hash")
    out_dir = tmp_path / "out"
    gdb = out_dir / "附录C图斑.gdb"
    qa_json = out_dir / "附录C质检.json"
    fake = _FakeArcPyBuild()
    monkeypatch.setitem(sys.modules, "arcpy", fake)

    def fake_build_layer(out_gdb_arg, _source, feature_values, **_kwargs):
        Path(out_gdb_arg).mkdir(parents=True, exist_ok=True)
        return {
            "geodatabase": str(out_gdb_arg),
            "feature_class": str(Path(out_gdb_arg) / "补充耕地质量鉴定图斑"),
            "layer_name": "补充耕地质量鉴定图斑",
            "count": len(feature_values),
            "wkid": 4524,
            "crs_name": "CGCS2000_3_Degree_GK_Zone_36",
            "schema": {"status": "PASS", "missing": [], "mismatched": []},
            "fields": [],
            "area_check": {"status": "PASS"},
        }

    monkeypatch.setattr(builder.appendix_c, "build_layer", fake_build_layer)

    def boom(_feature_class):
        raise RuntimeError("模拟质检异常")

    monkeypatch.setattr(builder, "generate_qa_report", boom)

    with pytest.raises(RuntimeError, match="模拟质检异常"):
        builder.build(source=source, out_gdb=gdb,
                      layer_name="补充耕地质量鉴定图斑", qa_json=qa_json)
    assert not gdb.exists(), "质检异常后本次 gdb 必须被回滚删除"
    assert not qa_json.exists()
    assert not (out_dir / "附录C建库报告.json").exists()
    assert not (out_dir / "待补清单.csv").exists()
    assert fake.deleted == [str(gdb.resolve())]

