"""耕地质量监测指标分级赋值的契约测试（正负对照）。

覆盖：三类分级边界、区域切换、字段两级匹配、空值不进分母、阈值自检、未核标记。
"""
from __future__ import annotations

import json

import pytest

from acceptance import grading


@pytest.fixture(scope="module")
def cfg():
    return grading.load_thresholds()


# ---------- 三类分级逻辑 ----------

def test_asc_grades_higher_is_better():
    spec = grading.GradeSpec(abbr="OM", name="有机质", direction="asc", bounds=[35, 30, 25, 20])
    assert spec.grade_of(40) == 1
    assert spec.grade_of(35) == 1      # 边界含等号
    assert spec.grade_of(34.9) == 2
    assert spec.grade_of(30) == 2
    assert spec.grade_of(22) == 4
    assert spec.grade_of(19.9) == 5
    assert spec.grade_of(0) == 5


def test_desc_grades_lower_is_better():
    spec = grading.GradeSpec(abbr="SAL", name="盐渍化程度", direction="desc", bounds=[1, 3, 6, 9])
    assert spec.grade_of(0.5) == 1
    assert spec.grade_of(1) == 1
    assert spec.grade_of(2) == 2
    assert spec.grade_of(6) == 3
    assert spec.grade_of(8) == 4
    assert spec.grade_of(50) == 5


def test_range_grades_middle_is_best():
    spec = grading.GradeSpec(abbr="pH", name="酸碱度", direction="range",
                             ranges=[(6.0, 7.0, 1), (5.5, 6.0, 2)], outside_grade=5)
    assert spec.grade_of(6.5) == 1
    assert spec.grade_of(6.0) == 1      # 闭区间两端都算本级
    assert spec.grade_of(5.5) == 2
    assert spec.grade_of(4.0) == 5
    assert spec.grade_of(9.0) == 5


# ---------- 区域切换：同一指标两套阈值 ----------

def test_region_switch_changes_thresholds(cfg):
    sw = grading.specs_for(cfg, "西南区")
    hn = grading.specs_for(cfg, "华南区")
    assert sw["EXC_CA"].bounds == [1500, 1000, 500, 200]
    assert hn["EXC_CA"].bounds == [1000, 500, 200, 50]
    # 交换性钙 300 在西南区是 4 级，在华南区是 3 级 —— 换区必须换阈值
    assert sw["EXC_CA"].grade_of(300) == 4
    assert hn["EXC_CA"].grade_of(300) == 3


def test_unknown_region_raises(cfg):
    with pytest.raises(KeyError):
        grading.specs_for(cfg, "长江中下游区")


# ---------- 字段匹配 ----------

def test_field_exact_then_prefix(cfg):
    specs = grading.specs_for(cfg, "华南区")
    mapping, issues = grading.match_fields(["OM", "OM_2023", "pH值"], specs)
    # OM 精确命中 OM，不误取 OM_2023
    assert mapping["OM"] == "OM"
    assert mapping["pH"] == "pH值"


def test_ambiguous_prefix_is_skipped_by_default(cfg):
    specs = {"TN": grading.GradeSpec(abbr="TN", name="全氮", direction="asc", bounds=[2, 1.5, 1, 0.75])}
    mapping, issues = grading.match_fields(["TN_2023", "TN_2024"], specs)
    assert mapping == {}
    assert any("命中多个字段" in i for i in issues)


def test_ambiguous_prefix_first_policy_is_explicit(cfg):
    specs = {"TN": grading.GradeSpec(abbr="TN", name="全氮", direction="asc", bounds=[2, 1.5, 1, 0.75])}
    mapping, issues = grading.match_fields(["TN_2023", "TN_2024"], specs, policy="first")
    assert mapping == {"TN": "TN_2023"}
    assert any("policy=first" in i for i in issues)


# ---------- 空值与非数值 ----------

def test_parse_value_handles_limits_and_blanks():
    assert grading.parse_value(12.5) == (12.5, None)
    assert grading.parse_value("12.5") == (12.5, None)
    value, note = grading.parse_value("<0.5")
    assert value == 0.5 and "限值写法" in note
    assert grading.parse_value("") == (None, "空值或未检出")
    assert grading.parse_value("未检出") == (None, "空值或未检出")
    value, note = grading.parse_value("abc")
    assert value is None and note.startswith("非数值")


def test_null_is_excluded_from_denominator(cfg):
    specs = grading.specs_for(cfg, "华南区")
    mapping, _ = grading.match_fields(["OM", "pH"], specs)
    rows = [{"OM": "40", "pH": "6.5"}, {"OM": None, "pH": "6.4"}, {"OM": "", "pH": "x"}]
    result = grading.assign(rows, specs, mapping, "华南区")
    om = result["stats"]["OM"]
    assert om["graded"] == 1 and om["null"] == 2
    # 分母是已分级数 1，不是 3 —— 否则"没测"会被算成最低级
    assert om["share"][1] == 1.0
    assert om["share"][5] == 0.0


# ---------- 阈值自检 ----------

def test_validate_catches_incomplete_article_examples(cfg):
    specs = grading.specs_for(cfg, "西南区")
    problems = grading.validate_spec(specs["SALINITY"])
    assert any("bounds 需 4 个界限" in p for p in problems)
    # 文章只给了 pH 的 1 级区间与 5 级带外，2—4 级不可达 —— 校验必须抓出来
    ph_problems = grading.validate_spec(specs["pH"])
    assert any("未覆盖" in p and "2/3/4" in p for p in ph_problems)


def test_validate_catches_non_monotonic(cfg):
    bad = grading.GradeSpec(abbr="OM", name="有机质", direction="asc", bounds=[20, 25, 30, 35])
    assert any("严格递减" in p for p in grading.validate_spec(bad))
    bad_desc = grading.GradeSpec(abbr="S", name="盐渍化", direction="desc", bounds=[9, 6, 3, 1])
    assert any("严格递增" in p for p in grading.validate_spec(bad_desc))


def test_validate_catches_overlapping_ranges():
    spec = grading.GradeSpec(abbr="pH", name="酸碱度", direction="range",
                             ranges=[(6.0, 7.0, 1), (6.5, 7.5, 2)], outside_grade=5)
    assert any("重叠" in p for p in grading.validate_spec(spec))


def test_valid_spec_passes():
    spec = grading.GradeSpec(abbr="OM", name="有机质", direction="asc", bounds=[35, 30, 25, 20])
    assert grading.validate_spec(spec) == []


# ---------- 未核标记与清单 ----------

def test_unverified_thresholds_are_flagged(cfg):
    specs = grading.specs_for(cfg, "西南区")
    mapping, _ = grading.match_fields(["OM"], specs)
    result = grading.assign([{"OM": "38"}], specs, mapping, "西南区")
    assert "OM" in result["unverified"]
    assert "UNVERIFIED" in grading.render_stats(result)


def test_config_marks_unverified_source(cfg):
    assert cfg["source"]["verified"] is False
    assert "不得" in cfg["source"]["usage_boundary"] or "不可" in cfg["source"]["usage_boundary"]
    items = grading.checklist(cfg)
    assert any("阈值未配置" in i for i in items)
    assert any("bounds 需 4 个界限" in i for i in items)
    # 原始检索失败记录必须留痕，否则下一个人会重走一遍
    assert len(cfg["source"]["access_attempts"]) >= 3


# ---------- 指标目录与省级对照表 ----------

def test_catalog_covers_26_indicators_with_directions(cfg):
    cat = grading.catalog_items(cfg)
    assert len(cat) == 26
    assert all(item["direction"] in ("asc", "desc", "range") for item in cat.values())
    assert cat["BD"]["direction"] == "range"       # 容重是区间型，最容易抄错
    assert cat["SALINITY"]["direction"] == "desc"  # 盐渍化负向
    assert cat["OM"]["direction"] == "asc"


def test_catalog_gaps_distinguish_direction_from_threshold(cfg):
    gaps = grading.catalog_gaps(cfg)
    assert any("阈值未配置" in g and "方向已确认" in g for g in gaps)
    assert not any("方向未确认" in g for g in gaps)


def test_direction_consistency_between_catalog_and_regions(cfg):
    assert grading.check_direction_consistency(cfg) == []
    broken = json.loads(json.dumps(cfg))
    broken["regions"]["华南区"]["indicators"]["OM"]["direction"] = "desc"
    assert any("不一致" in p for p in grading.check_direction_consistency(broken))


def test_official_reference_table_is_usable_and_marked_not_for_guangxi(cfg):
    """福建省级表是官方取得，可作结构与方向参考；但必须标明不得用于广西。"""
    ref = grading.load_reference()
    assert ref["source"]["verified"] is True
    assert "不得用于广西" in ref["source"]["usage_boundary"]
    assert cfg["reference_tables"]["福建省"]["usable_for_guangxi"] is False
    specs = grading.specs_from_reference(ref)
    assert len(specs) == 26          # 26 项参与 1—5 级，重金属不在此列
    # 官方表也必须能通过同一套阈值自检（除已标注需回原文复核的格子外应当无结构问题）
    structural = []
    for abbr, spec in specs.items():
        if ref["indicators"][abbr].get("cell_review_required"):
            continue
        structural.extend(grading.validate_spec(spec))
    assert structural == [], structural


# ---------- 县 → 二级农业区 → 大区 ----------

def test_county_list_totals_111_and_matches_appendix_j(cfg):
    counties = grading.load_counties()
    assert counties["count_total"] == 111
    total = sum(len(z["counties"]) for z in counties["zones"].values())
    assert total == 111
    assert counties["county_counts"] == {
        "南岭丘陵山地林农区": 42, "黔桂高原山地林农牧区": 15, "粤西桂南农林区": 54}


def test_zone_for_county_known_and_unknown():
    assert grading.zone_for_county("马山县")["zone"] == "黔桂高原山地林农牧区"
    assert grading.zone_for_county("兴宁区")["zone"] == "粤西桂南农林区"
    assert grading.zone_for_county("上林县")["zone"] == "南岭丘陵山地林农区"
    with pytest.raises(KeyError):
        grading.zone_for_county("南宁市")      # 市级不是县级单元
    with pytest.raises(KeyError):
        grading.zone_for_county("横县")        # 旧名，已改横州市


def test_region_guard_blocks_wrong_region(cfg):
    ok = grading.region_guard("马山县", "西南区", cfg)      # 黔桂 → 西南
    assert ok["ok"] is True and ok["expected"] == "西南区"
    bad = grading.region_guard("马山县", "华南区", cfg)     # 选错区必须拦
    assert bad["ok"] is False and "应选 西南区" in bad["problem"]


def test_region_guard_blocks_nanling_without_thresholds(cfg):
    """南岭 42 个县在只有西南/华南两套阈值时无参数，不得回退邻近区。"""
    guard = grading.region_guard("上林县", "华南区", cfg)
    assert guard["ok"] is False
    assert "没有对应参数" in guard["problem"]
    missing = grading.zones_without_thresholds(cfg)
    assert [m["zone"] for m in missing] == ["南岭丘陵山地林农区"]
    assert missing[0]["county_count"] == 42
