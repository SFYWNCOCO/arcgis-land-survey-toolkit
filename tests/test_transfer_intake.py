# -*- coding: utf-8 -*-
"""移交包接入与套合纯逻辑单测（系统 Python，无需 arcpy）。

覆盖：图层识别/显式指定/缺图层报错、附录C契约比对（旧28字段版→重建）、
DLTB→附录C 与 旧版重建映射、套合匹配分类（1:1/1:N/N:1/无匹配/跨界）、
坐标系不一致报错、拒绝覆盖、gdb 指纹、区划名称表、CLI 纯辅助函数。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from acceptance import transfer_intake as ti
from acceptance.appendix_c import BuildOptions
from acceptance.qa import APPENDIX_C

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
def _spec(name, shape_type="Polygon", wkid=4524, count=1, fields=None):
    return ti.layer_spec(
        name=name,
        feature_count=count,
        shape_type=shape_type,
        wkid=wkid,
        crs_name="CGCS2000_3_Degree_GK_Zone_36",
        fields=fields if fields is not None else [],
    )


def _compliant_fields():
    return [
        {"name": name, "type": field_type, "length": length}
        for name, _alias, field_type, length, _required, _domain in APPENDIX_C
    ]


def _old_28_fields():
    """桂农厅发〔2026〕42号旧28字段版：无 BCGDLY、BSM 18、长度2、面积 Float。"""
    fields = []
    for name, _alias, field_type, length, _required, _domain in APPENDIX_C:
        if name == "BCGDLY":
            continue
        entry = {"name": name, "type": field_type, "length": length}
        if name == "BSM":
            entry["length"] = 18
        if name in ("SFYKT", "SFFK", "SFZPJ", "BCFS"):
            entry["length"] = 2
        if name in ("TBMJ", "KCMJ", "TBDLMJ", "JZGDMJ"):
            entry["type"] = "Single"
        fields.append(entry)
    return fields


def _load_transfer_script():
    name = "ingest_appendix_c_transfer_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, TOOLKIT_ROOT / "scripts" / "ingest_appendix_c_transfer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 图层识别与选择
# ---------------------------------------------------------------------------
def test_classify_layers_recognizes_dltb_and_appendix_c():
    layers = [
        _spec("补充耕地国土变更图斑", count=3),
        _spec("补充耕地质量鉴定图斑", count=2),
        _spec("方里网", shape_type="Polyline"),
    ]
    result = ti.classify_layers(layers)
    assert result["dltb"] == "补充耕地国土变更图斑"
    assert result["appendix_c"] == "补充耕地质量鉴定图斑"
    assert result["dltb_candidates"] == ["补充耕地国土变更图斑"]
    assert result["appendix_c_candidates"] == ["补充耕地质量鉴定图斑"]
    assert result["ambiguous"] is False
    assert [item["name"] for item in result["polygon_layers"]] == [
        "补充耕地国土变更图斑", "补充耕地质量鉴定图斑"]
    assert result["unrecognized_non_polygon"] == ["方里网"]


def test_classify_layers_multiple_candidates_is_ambiguous():
    layers = [
        _spec("补充耕地国土变更图斑A"),
        _spec("DLTB_补充耕地"),
        _spec("补充耕地质量鉴定图斑"),
    ]
    result = ti.classify_layers(layers)
    assert result["dltb"] is None
    assert len(result["dltb_candidates"]) == 2
    assert result["ambiguous"] is True


def test_resolve_layers_auto_unique_and_explicit():
    layers = [
        _spec("补充耕地国土变更图斑"),
        _spec("补充耕地质量鉴定图斑"),
    ]
    resolved = ti.resolve_layers(layers)
    assert resolved["dltb"] == "补充耕地国土变更图斑"
    assert resolved["appendix_c"] == "补充耕地质量鉴定图斑"
    assert resolved["explicit"] == {"dltb": False, "appendix_c": False}

    ambiguity = [_spec("补充耕地国土变更图斑A"), _spec("DLTB_补充耕地"),
                 _spec("补充耕地质量鉴定图斑")]
    with pytest.raises(ValueError, match="--dltb"):
        ti.resolve_layers(ambiguity)
    resolved = ti.resolve_layers(ambiguity, dltb="DLTB_补充耕地")
    assert resolved["dltb"] == "DLTB_补充耕地"
    assert resolved["explicit"]["dltb"] is True


def test_resolve_layers_missing_layer_raises():
    layers = [_spec("图斑"), _spec("评价单元")]
    with pytest.raises(ValueError, match="未能从移交 gdb 识别"):
        ti.resolve_layers(layers)
    with pytest.raises(ValueError, match="--dltb 指定的图层不存在"):
        ti.resolve_layers(layers, dltb="不存在的层")
    with pytest.raises(ValueError, match="--appendix-c 指定的图层不存在"):
        ti.resolve_layers(layers, appendix_c="不存在的层")


def test_resolve_layers_rejects_same_layer_for_both_roles():
    layers = [_spec("补充耕地质量鉴定图斑")]
    with pytest.raises(ValueError, match="不能是同一图层"):
        ti.resolve_layers(layers, dltb="补充耕地质量鉴定图斑",
                          appendix_c="补充耕地质量鉴定图斑")


# ---------------------------------------------------------------------------
# 附录C契约比对与接入路径
# ---------------------------------------------------------------------------
def test_contract_diff_accepts_exact_appendix_c():
    diff = ti.contract_diff(_compliant_fields())
    assert diff["compliant"] is True
    assert diff["missing"] == [] and diff["mismatched"] == []
    assert diff["expected_count"] == 29


def test_contract_diff_detects_old_28_field_version():
    diff = ti.contract_diff(_old_28_fields())
    assert diff["compliant"] is False
    assert "BCGDLY" in diff["missing"]
    mismatched = {item["field"] for item in diff["mismatched"]}
    assert {"BSM", "SFYKT", "SFFK", "SFZPJ", "BCFS",
            "TBMJ", "KCMJ", "TBDLMJ", "JZGDMJ"} <= mismatched


def test_contract_diff_missing_field_is_not_compliant():
    fields = [item for item in _compliant_fields() if item["name"] != "GXSJ"]
    diff = ti.contract_diff(fields)
    assert diff["compliant"] is False
    assert diff["missing"] == ["GXSJ"]


def test_decide_action_copy_vs_rebuild_vs_dltb():
    layers = [
        _spec("补充耕地质量鉴定图斑", fields=_compliant_fields()),
        _spec("补充耕地国土变更图斑", fields=[]),
    ]
    resolved = {"dltb": "补充耕地国土变更图斑",
                "appendix_c": "补充耕地质量鉴定图斑"}
    assert ti.decide_action(resolved, layers)["action"] == "copy"
    assert ti.decide_action(resolved, layers)["contract"]["compliant"] is True

    old_layer = _spec("补充耕地质量鉴定图斑", fields=_old_28_fields())
    rebuilt = ti.decide_action(resolved, [old_layer, layers[1]])
    assert rebuilt["action"] == "rebuild"
    assert rebuilt["contract"]["compliant"] is False
    assert "BCGDLY" in rebuilt["contract"]["missing"]

    dltb_only = ti.decide_action({"dltb": "补充耕地国土变更图斑"},
                                 [layers[1]])
    assert dltb_only["action"] == "dltb"


# ---------------------------------------------------------------------------
# DLTB → 附录C 映射
# ---------------------------------------------------------------------------
def test_map_dltb_rows_carries_fields_and_derives_xzdm():
    attrs = {
        "BSM": "990001001001001", "DLBM": "0103", "DLMC": "旱地",
        "ZLDWDM": "990001001001", "ZLDWMC": "示例村",
        "TBMJ": 1234.56, "KCDLBM": "0301", "KCXS": 0.5, "KCMJ": 12.0,
        "TBDLMJ": 1222.56, "GDLX": "01", "GDPDJB": "02",
        "GXSJ": "20260817", "BZ": "示例备注",
    }
    rows = ti.map_dltb_rows(
        attrs,
        BuildOptions(county_code="990001", county_name="示例区"),
        provided={"BCGDLY": "恢复"},
        region_table={"990001001": "示例镇"},
    )
    assert len(rows) == 29
    by_field = {row["field"]: row for row in rows}
    assert by_field["BSM"]["value"] == "990001001001001"
    assert by_field["BSM"]["status"] == "CONFIRMED"
    assert by_field["DLBM"]["value"] == "0103"
    assert by_field["ZLDWDM"]["value"] == "990001001001"
    assert by_field["XZDM"]["value"] == "990001001"
    assert by_field["XZDM"]["status"] == "DERIVED"
    assert by_field["XZMC"]["value"] == "示例镇"
    assert by_field["XZMC"]["status"] == "DERIVED"
    assert by_field["TBMJ"]["value"] == 1234.56
    assert by_field["KCXS"]["value"] == 0.5
    assert by_field["GXSJ"]["value"].year == 2026
    assert by_field["XDM"]["value"] == "990001"
    assert by_field["XMC"]["value"] == "示例区"
    assert by_field["BCGDLY"]["value"] == "恢复"
    assert by_field["BCGDLY"]["status"] == "CONFIRMED"  # 显式提供
    assert by_field["BGQDLBM"]["value"] is None
    assert by_field["BGQDLBM"]["status"] == "MISSING"


def test_map_dltb_rows_missing_everything_stays_null():
    rows = ti.map_dltb_rows({}, BuildOptions())
    assert len(rows) == 29
    by_field = {row["field"]: row for row in rows}
    for name in ("BSM", "XZDM", "XZMC", "BGQDLBM", "BGQDLMC", "ZLDWDM",
                 "GXSJ", "XMMC", "BCFS", "BCGDLY", "JZGDMJ"):
        assert by_field[name]["value"] is None, name
        assert by_field[name]["status"] == "MISSING", name
        assert by_field[name]["note"], name
    # 不得出现占位文本
    for row in rows:
        if row["value"] is not None:
            assert not ti.is_placeholder(row["value"]), row["field"]


def test_map_dltb_rows_placeholder_provided_is_nulled():
    rows = ti.map_dltb_rows({}, BuildOptions(), provided={"BCGDLY": "待确认"})
    by_field = {row["field"]: row for row in rows}
    assert by_field["BCGDLY"]["value"] is None
    assert by_field["BCGDLY"]["status"] == "MISSING"
    assert "待确认" in (by_field["BCGDLY"]["note"] or "")


def test_map_dltb_rows_project_fields_from_params():
    rows = ti.map_dltb_rows(
        {},
        BuildOptions(county_code="990001", county_name="示例区",
                     project_name="示例市示例区2026年第一批次补充耕地",
                     project_no="F99000120260000"),
    )
    by_field = {row["field"]: row for row in rows}
    assert by_field["XMMC"]["value"] == "示例市示例区2026年第一批次补充耕地"
    assert by_field["XMMC"]["status"] == "ASSUMED"
    assert by_field["XMBH"]["value"] == "F99000120260000"
    assert by_field["XDM"]["value"] == "990001"


# ---------------------------------------------------------------------------
# 旧版/缺字段 → 附录C 重建映射
# ---------------------------------------------------------------------------
def test_map_appendix_c_rows_carries_values_without_recomputing():
    attrs = {
        "BSM": "B1", "DLBM": "0103", "DLMC": "旱地",
        "ZLDWDM": "990001001002", "ZLDWMC": "某村",
        "TBMJ": 500.0, "TBDLMJ": 480.0, "JZGDMJ": 480.0,
        "XMBH": "F99000120260000", "BZ": "原备注",
    }
    rows = ti.map_appendix_c_rows(
        attrs, BuildOptions(county_code="990001", county_name="示例区"))
    by_field = {row["field"]: row for row in rows}
    assert by_field["TBMJ"]["value"] == 500.0  # 原值承接，不按几何重算
    assert by_field["TBMJ"]["status"] == "CONFIRMED"
    assert by_field["TBDLMJ"]["value"] == 480.0
    assert by_field["XZDM"]["value"] == "990001001"
    assert by_field["XMBH"]["value"] == "F99000120260000"
    assert by_field["BGQDLBM"]["value"] is None
    assert by_field["BCGDLY"]["value"] is None
    assert by_field["BZ"]["value"] == "原备注"


def test_map_appendix_c_rows_missing_fields_are_null_and_gap():
    rows = ti.map_appendix_c_rows({}, BuildOptions())
    missing_required = [
        row["field"] for row in rows
        if row["status"] == "MISSING" and row["required"] == "M"
    ]
    assert len(missing_required) == 23  # 附录C 29 字段中 M 为 23 个
    assert all(row["value"] is None for row in rows if row["status"] == "MISSING")
    gap = ti.gap_table([rows], ["1"])
    assert len(gap) == 29


# ---------------------------------------------------------------------------
# 审核修复轮 E-1：重建必须承接源层已有值；参数/提供值只填空白且冲突留痕
# ---------------------------------------------------------------------------
def _row_map(rows):
    return {row["field"]: row for row in rows}


def test_rebuild_carries_source_xmc_xmmc_without_params():
    attrs = {
        "XMC": "源层县名", "XMMC": "源层批次",
        "XZDM": "990001001", "XZMC": "源层镇名",
        "XMBH": "F99000120260000", "ZLDWDM": "990001001001",
    }
    rows = ti.map_appendix_c_rows(attrs, BuildOptions())
    by_field = _row_map(rows)
    assert by_field["XMC"]["value"] == "源层县名"
    assert "源层字段承接" in by_field["XMC"]["source"]
    assert by_field["XMMC"]["value"] == "源层批次"
    assert "源层字段承接" in by_field["XMMC"]["source"]
    assert by_field["XZDM"]["value"] == "990001001"
    assert by_field["XZMC"]["value"] == "源层镇名"
    assert by_field["XMBH"]["value"] == "F99000120260000"


def test_rebuild_params_do_not_override_existing_source_values():
    attrs = {"XMC": "源层县名", "XMMC": "源层批次", "XDM": "990001"}
    options = BuildOptions(county_code="990002", county_name="参数县名",
                           project_name="参数批次")
    rows = ti.map_appendix_c_rows(attrs, options)
    by_field = _row_map(rows)
    assert by_field["XMC"]["value"] == "源层县名"
    assert by_field["XMMC"]["value"] == "源层批次"
    assert by_field["XDM"]["value"] == "990001"
    conflicts = ti.collect_conflicts([rows])
    assert {item["field"] for item in conflicts} == {"XMC", "XMMC", "XDM"}
    assert all(item["kept"] == "源层值" for item in conflicts)
    assert "冲突" in (by_field["XMC"]["note"] or "")


def test_rebuild_provided_only_fills_empty_fields():
    attrs = {"XMC": "源层县名"}
    rows = ti.map_appendix_c_rows(attrs, BuildOptions(),
                                  provided={"XMC": "参数县名", "XMMC": "显式批次",
                                            "ZLDWDM": "990001001001"})
    by_field = _row_map(rows)
    assert by_field["XMC"]["value"] == "源层县名"  # 已有值不被覆盖
    assert by_field["XMMC"]["value"] == "显式批次"  # 空字段被填
    assert by_field["ZLDWDM"]["value"] == "990001001001"
    conflicts = ti.collect_conflicts([rows])
    assert [item["field"] for item in conflicts] == ["XMC"]
    assert conflicts[0]["kept"] == "源层值"


def test_rebuild_param_fills_empty_xmc_when_source_lacks_it():
    rows = ti.map_appendix_c_rows({}, BuildOptions(county_name="参数县名",
                                                  project_name="参数批次"))
    by_field = _row_map(rows)
    assert by_field["XMC"]["value"] == "参数县名"
    assert by_field["XMC"]["source"] == "--county-name 参数"
    assert by_field["XMMC"]["value"] == "参数批次"
    assert by_field["XMMC"]["status"] == "ASSUMED"  # 参数批次名属业务推定
    assert ti.collect_conflicts([rows]) == []


# ---------------------------------------------------------------------------
# 审核修复轮 E-2：DLTB 路径来源必须按真实来源标注，状态按事实给
# ---------------------------------------------------------------------------
def test_dltb_source_values_are_labeled_as_dltb_and_confirmed():
    attrs = {"XMMC": "DLTB批次", "XMBH": "F99000120260000",
             "XMC": "DLTB县名", "DLBM": "0103"}
    options = BuildOptions(project_name="参数批次", project_no="G000",
                           county_name="参数县名")
    rows = ti.map_dltb_rows(attrs, options)
    by_field = _row_map(rows)
    assert by_field["XMMC"]["value"] == "DLTB批次"
    assert "DLTB" in by_field["XMMC"]["source"]
    assert "--project-name 参数" not in by_field["XMMC"]["source"]
    assert by_field["XMMC"]["status"] == "CONFIRMED"
    assert by_field["XMBH"]["value"] == "F99000120260000"
    assert "DLTB" in by_field["XMBH"]["source"]
    assert by_field["XMC"]["value"] == "DLTB县名"
    assert "DLTB" in by_field["XMC"]["source"]
    conflicts = ti.collect_conflicts([rows])
    assert {item["field"] for item in conflicts} == {"XMMC", "XMBH", "XMC"}
    assert all(item["kept"] == "源层值" for item in conflicts)


def test_dltb_param_only_project_fields_are_fact_labeled():
    rows = ti.map_dltb_rows({}, BuildOptions(project_name="参数批次",
                                             project_no="F99000120260000"))
    by_field = _row_map(rows)
    assert by_field["XMMC"]["value"] == "参数批次"
    assert by_field["XMMC"]["source"] == "--project-name 参数"
    assert by_field["XMMC"]["status"] == "ASSUMED"
    assert by_field["XMBH"]["value"] == "F99000120260000"
    assert by_field["XMBH"]["source"] == "--project-no 参数"
    assert by_field["XMBH"]["status"] == "CONFIRMED"


def test_dltb_alias_fields_still_carried_after_generic_carry():
    rows = ti.map_dltb_rows({"DLBM_CODE": "0103", "TBMJ_M2": 66.6,
                             "GXSJ_TIME": "20251231"})
    by_field = _row_map(rows)
    assert by_field["DLBM"]["value"] == "0103"
    assert "DLTB" in by_field["DLBM"]["source"]
    assert by_field["TBMJ"]["value"] == 66.6
    assert by_field["GXSJ"]["value"].year == 2025


# ---------------------------------------------------------------------------
# 回归：copy 路径零修改（附录I 注2）
# ---------------------------------------------------------------------------
class _FakeCopyGeometry:
    def __init__(self, area):
        self.area = area


class _FakeCopyCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return iter(self._rows)

    def __exit__(self, *_exc):
        return False


class _FakeArcPyCopy(types.ModuleType):
    """copy_layer_unchanged 的最小假 arcpy：GetCount/CopyFeatures/ListFields/SearchCursor。"""

    def __init__(self, count=3, areas=(100.0, 200.0, 300.0), fields=None):
        super().__init__("arcpy")
        self.count = count
        self.areas = list(areas)
        self.fields = fields or []
        self.management = types.SimpleNamespace(
            GetCount=lambda _path: [str(self.count)],
            CreateFileGDB=self._create_gdb,
            CopyFeatures=self._copy_features,
            Delete=lambda _path: None,
        )
        self.da = types.SimpleNamespace(SearchCursor=self._cursor)

    def Exists(self, path):
        return Path(path).exists()

    def Describe(self, _path):
        return types.SimpleNamespace(
            shapeType="Polygon",
            spatialReference=types.SimpleNamespace(
                factoryCode=4524, name="CGCS2000_3_Degree_GK_Zone_36"))

    def ListFields(self, _path):
        return self.fields

    def _create_gdb(self, parent, name, _version):
        Path(parent, name).mkdir(parents=True, exist_ok=True)

    def _copy_features(self, source, target):
        Path(target).mkdir(parents=True, exist_ok=True)

    def _cursor(self, _path, names):
        rows = []
        for index, area in enumerate(self.areas):
            row = {}
            for name in names:
                row[name] = _FakeCopyGeometry(area) if name == "SHAPE@" else "V{}".format(index)
            rows.append(tuple(row[name] for name in names))
        return _FakeCopyCursor(rows)


def test_copy_layer_unchanged_zero_modification_regression(monkeypatch, tmp_path):
    fields = [types.SimpleNamespace(name=name, type=field_type, length=length)
              for name, _alias, field_type, length, _required, _domain in APPENDIX_C]
    fake = _FakeArcPyCopy(fields=fields)
    monkeypatch.setitem(sys.modules, "arcpy", fake)
    out_gdb = tmp_path / "out.gdb"
    result = ti.copy_layer_unchanged(
        out_gdb, str(tmp_path / "src.gdb" / "补充耕地质量鉴定图斑"))
    assert result["count_before"] == result["count_after"] == 3
    assert result["area_sum_before"] == result["area_sum_after"]
    assert result["attribute_digest_before"] == result["attribute_digest_after"]
    assert result["unchanged"] is True
    assert result["schema"]["status"] == "PASS"


# ---------------------------------------------------------------------------
# 套合匹配分类
# ---------------------------------------------------------------------------
def _match_case(parcel_areas, patch_areas, overlaps):
    parcel_meta = {key: {"DKBH": key, "area": area} for key, area in parcel_areas.items()}
    patch_meta = {key: {"OID": key, "BSM": "BSM-{}".format(key), "area": area}
                  for key, area in patch_areas.items()}
    return ti.classify_matches(parcel_meta, patch_meta, overlaps)


def test_classify_matches_one_to_one():
    result = _match_case({"D1": 100.0}, {1: 100.0}, [("D1", 1, 100.0)])
    assert result["rows"][0]["匹配类型"] == "1:1"
    assert result["summary"]["by_type"]["1:1"] == 1
    assert result["summary"]["by_type"]["无匹配"] == 0


def test_classify_matches_one_patch_to_many_parcels():
    result = _match_case({"D1": 60.0, "D2": 40.0}, {1: 100.0},
                         [("D1", 1, 60.0), ("D2", 1, 40.0)])
    assert {row["匹配类型"] for row in result["rows"]} == {"1:N"}
    assert result["summary"]["by_type"]["1:N"] == 2


def test_classify_matches_many_patches_to_one_parcel():
    result = _match_case({"D1": 100.0}, {1: 60.0, 2: 40.0},
                         [("D1", 1, 60.0), ("D1", 2, 40.0)])
    assert {row["匹配类型"] for row in result["rows"]} == {"N:1"}
    assert result["summary"]["by_type"]["N:1"] == 2


def test_classify_matches_cross_boundary():
    # 图斑 100 m²，只有 60 m² 落在地块内 → 跨界
    result = _match_case({"D1": 100.0}, {1: 100.0}, [("D1", 1, 60.0)])
    assert result["rows"][0]["匹配类型"] == "跨界"
    assert result["rows"][0]["占图斑面积比"] == 0.6
    assert result["summary"]["by_type"]["跨界"] == 1
    assert len(result["cross_boundary"]) == 1


def test_classify_matches_no_match_both_directions():
    result = _match_case({"D1": 100.0, "D2": 50.0}, {1: 100.0, 2: 30.0},
                         [("D1", 1, 100.0)])
    types = [row["匹配类型"] for row in result["rows"]]
    assert types.count("无匹配") == 2
    assert {row["DKBH"] for row in result["unmatched_parcels"]} == {"D2"}
    assert {row["特征OID"] for row in result["unmatched_patches"]} == {2}
    assert result["summary"]["by_type"]["1:1"] == 1


def test_classify_matches_tolerance_ignores_tiny_overlap():
    result = _match_case({"D1": 100.0}, {1: 100.0}, [("D1", 1, 0.005)])
    assert result["rows"][0]["匹配类型"] == "无匹配"
    assert result["summary"]["by_type"]["无匹配"] == 2


def test_classify_matches_both_multi_reports_note():
    result = _match_case({"D1": 60.0, "D2": 40.0}, {1: 50.0, 2: 50.0},
                         [("D1", 1, 50.0), ("D2", 2, 40.0), ("D1", 2, 10.0)])
    assert result["summary"]["both_multi_count"] >= 1
    assert "M:N" in result["note"] or "人工复核" in result["note"]


def test_extents_intersect():
    assert ti.extents_intersect((0, 0, 10, 10), (5, 5, 15, 15)) is True
    assert ti.extents_intersect((0, 0, 10, 10), (20, 20, 30, 30)) is False
    assert ti.extents_intersect(None, (0, 0, 1, 1)) is True


# ---------------------------------------------------------------------------
# CRS、拒绝覆盖、哈希指纹、区划表
# ---------------------------------------------------------------------------
def test_assert_same_crs_and_expect_wkid():
    ti.assert_same_crs(4524, 4524)
    with pytest.raises(ValueError, match="坐标系不一致"):
        ti.assert_same_crs(4524, 4546)
    with pytest.raises(ValueError, match="坐标系未知"):
        ti.assert_same_crs(0, 4524)
    ti.assert_expected_wkid(4524, 4524)
    with pytest.raises(ValueError, match="--expect-wkid"):
        ti.assert_expected_wkid(4524, 4525)


def test_ensure_outputs_absent_refuses_existing(tmp_path):
    missing = tmp_path / "new-dir"
    ti.ensure_outputs_absent([missing])
    missing.mkdir()
    with pytest.raises(FileExistsError, match="不覆盖"):
        ti.ensure_outputs_absent([missing])
    existing_file = tmp_path / "a.csv"
    existing_file.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ti.ensure_outputs_absent([missing, existing_file])


def test_gdb_fingerprint_detects_changes(tmp_path):
    gdb = tmp_path / "demo.gdb"
    gdb.mkdir()
    (gdb / "a.gdbtable").write_bytes(b"aaa")
    (gdb / "sub").mkdir()
    (gdb / "sub" / "b.gdbtable").write_bytes(b"bbb")
    (gdb / "_gdb.MM-20260109.1.sr.lock").write_bytes(b"lock")
    first = ti.gdb_fingerprint(gdb)
    second = ti.gdb_fingerprint(gdb)
    assert first == second
    assert first["file_count"] == 2  # 锁文件不参与指纹
    assert first["ignored_lock_files"] == ["_gdb.MM-20260109.1.sr.lock"]
    assert first["total_bytes"] == 6
    assert len(first["aggregate_sha256"]) == 64
    (gdb / "a.gdbtable").write_bytes(b"changed")
    third = ti.gdb_fingerprint(gdb)
    assert third["aggregate_sha256"] != first["aggregate_sha256"]


def test_gdb_fingerprint_missing_path_is_safe(tmp_path):
    result = ti.gdb_fingerprint(tmp_path / "absent.gdb")
    assert result["exists"] is False
    assert result["file_count"] == 0


def test_load_region_table_and_lookup(tmp_path):
    table_path = tmp_path / "region.csv"
    table_path.write_text(
        "行政区代码,行政区名称\n990001001,示例镇\n990001002,示例二镇\n",
        encoding="utf-8-sig")
    table = ti.load_region_table(table_path)
    assert table == {"990001001": "示例镇", "990001002": "示例二镇"}
    assert ti.lookup_region_name(table, "990001001") == "示例镇"
    assert ti.lookup_region_name(table, "990001001001") == "示例镇"
    assert ti.lookup_region_name(table, "990001009") is None
    assert ti.lookup_region_name(None, "990001001") is None
    with pytest.raises(FileNotFoundError):
        ti.load_region_table(tmp_path / "absent.csv")


# ---------------------------------------------------------------------------
# CLI 纯辅助函数
# ---------------------------------------------------------------------------
def test_cli_attrs_loader_rejects_unknown_field(tmp_path):
    script = _load_transfer_script()
    with pytest.raises(ValueError, match="附录C之外的字段"):
        script.load_attrs(_write_json(tmp_path / "attrs.json",
                                      {"values": {"UNKNOWN": "x"}}))
    attrs = script.load_attrs(_write_json(tmp_path / "ok.json",
                                          {"values": {"BCGDLY": "恢复"}}))
    assert attrs["values"] == {"BCGDLY": "恢复"}


def test_cli_gap_from_qa_builds_field_rows():
    script = _load_transfer_script()
    qa_output = {"missing_required_fields": ["BSM", "GXSJ"]}
    gap = script._gap_from_qa(qa_output)
    assert [item["field"] for item in gap] == ["BSM", "GXSJ"]
    assert all("附录I 注2" in (item["reason"] or "") for item in gap)
    csv_rows = script._gap_csv_rows(gap)
    assert csv_rows[0]["字段名"] == "BSM"
    assert csv_rows[0]["必填性"] == "M"


def test_cli_pick_id_field():
    script = _load_transfer_script()
    assert script._pick_id_field(["PL_NAME", "DIKUAI_NO"]) == "DIKUAI_NO"
    assert script._pick_id_field(["DKBH"]) == "DKBH"
    with pytest.raises(ValueError, match="--id-field"):
        script._pick_id_field(["X"], requested="DIKUAI_NO")
    with pytest.raises(ValueError, match="无可用编号字段"):
        script._pick_id_field(["X", "Y"])


def test_cli_summary_extracts_key_numbers():
    script = _load_transfer_script()
    report = {
        "dry_run": False,
        "action": {"action": "copy", "source_layer": "补充耕地质量鉴定图斑"},
        "artifacts": {"geodatabase": "x.gdb"},
        "output": {"count": 9, "wkid": 4524, "unchanged": True},
        "qa": {"qa_status": "FAIL", "missing_required_fields": ["BSM"],
               "missing_required_rows": 9},
        "gap_list_count": 1,
        "transfer": {"hash_status": "PASS"},
        "project": {"hash_status": "PASS"},
        "overlay": {"status": "PASS", "summary": {"by_type": {"1:1": 9}}},
    }
    summary = script._summary(report)
    assert summary["action"] == "copy"
    assert summary["feature_count"] == 9
    assert summary["copy_unchanged"] is True
    assert summary["transfer_hash_status"] == "PASS"
    assert summary["overlay_summary"] == {"by_type": {"1:1": 9}}


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
