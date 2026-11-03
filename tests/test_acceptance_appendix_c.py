"""Contract tests for Appendix C transferred parcel QA (no ArcPy installation)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from acceptance.qa import APPENDIX_C, _check_appendix_c, check_project


class Field:
    def __init__(self, name, field_type, length):
        self.name = name
        self.type = field_type
        self.length = length


class Geometry:
    """Fake ArcPy polygon geometry.

    ``multipart=True``：partCount=2（真多部件，附录C注6禁止）。
    ``hole=True``：partCount=1 但 isMultipart=True（含内环/孔洞，ArcPy 实测行为）。
    """

    def __init__(self, multipart=False, hole=False, ring_count=None):
        if multipart and hole:
            raise ValueError("multipart 与 hole 不能同时为 True")
        self.partCount = 2 if multipart else 1
        self.isMultipart = bool(multipart or hole)
        self._ring_count = ring_count if ring_count is not None else (2 if self.isMultipart else 1)
        ring = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        self.JSON = json.dumps({"rings": [ring] * self._ring_count})


class FakeArcPy(types.ModuleType):
    def __init__(self, exists=True, fields=None, rows=None, shape_type="Polygon"):
        super().__init__("arcpy")
        self._exists = exists
        self._fields = fields if fields is not None else [Field(n, t, length) for n, _, t, length, _, _ in APPENDIX_C]
        self._rows = rows if rows is not None else [valid_row()]
        self._shape_type = shape_type
        self.management = types.SimpleNamespace(GetCount=lambda _path: [str(len(self._rows))])
        self.da = types.SimpleNamespace(SearchCursor=self._cursor)
        self.EnvManager = lambda **_kwargs: nullcontext()

    def Exists(self, _path):
        return self._exists

    def Describe(self, _path):
        return types.SimpleNamespace(shapeType=self._shape_type)

    def ListFields(self, _path):
        return self._fields

    def ListFeatureClasses(self):
        return ["耕地质量等级评价评价单元图"]

    def _cursor(self, _path, names):
        return iter([tuple(row.get(name) for name in names) for row in self._rows])


def valid_row(**overrides):
    row = {
        "BSM": "990002000000000000000000000001",
        "XDM": "990002",
        "XMC": "示例县",
        "XZDM": "990002001",
        "XZMC": "示例镇",
        "BGQDLBM": "1202",
        "BGQDLMC": "农村宅基地",
        "DLBM": "0101",
        "DLMC": "水田",
        "ZLDWDM": "9900020010010000001",
        "ZLDWMC": "示例村一组",
        "TBMJ": 1000.25,
        "KCDLBM": None,
        "KCXS": None,
        "KCMJ": None,
        "TBDLMJ": 1000.25,
        "GDLX": "01",
        "GDPDJB": "01",
        "GXSJ": dt.datetime(2026, 8, 17),
        "XMMC": "样例批次",
        "XMBH": "F99000220260001",
        "DKBH": "DK001",
        "JZGDMJ": 1000.25,
        "SFYKT": "否",
        "SFFK": "否",
        "SFZPJ": "否",
        "BCFS": "Z",
        "BCGDLY": "恢复",
        "BZ": None,
        "SHAPE@": Geometry(),
    }
    row.update(overrides)
    return row


class AppendixCTest(unittest.TestCase):
    def run_check(self, fake):
        with patch.dict(sys.modules, {"arcpy": fake}):
            return _check_appendix_c("demo.gdb/补充耕地质量鉴定图斑")

    def test_contract_has_29_fields_and_pdf_page_provenance(self):
        from acceptance.qa import APPENDIX_C_SOURCE

        self.assertEqual(len(APPENDIX_C), 29)
        self.assertEqual(APPENDIX_C_SOURCE["pdf_page"], 49)
        self.assertEqual(APPENDIX_C_SOURCE["printed_page"], 43)
        self.assertEqual(next(row[3] for row in APPENDIX_C if row[0] == "TBMJ"), 15)
        self.assertEqual(next(row[3] for row in APPENDIX_C if row[0] == "BSM"), 30)

    def test_missing_layer_is_fail(self):
        result = self.run_check(FakeArcPy(exists=False))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("缺少", result["detail"]["reason"])

    def test_check_project_includes_appendix_c_result(self):
        with patch.dict(sys.modules, {"arcpy": FakeArcPy()}):
            report = check_project("demo.gdb")
        labels = {item["item"] for item in report["checks"]}
        self.assertIn("附录G字段结构", labels)
        self.assertIn("耕地质量等级评价评价单元图坐标系", labels)
        self.assertIn("移交国土变更图斑", labels)
        self.assertIn("外业采样与举证材料", labels)
        row = next(item for item in report["checks"] if item["item"] == "补充耕地质量鉴定图斑")
        self.assertEqual(row["detail"]["source"]["printed_page"], 43)
        self.assertEqual(len(row["detail"]["fields"]["expected_schema"]), 29)
        tbmj = next(item for item in row["detail"]["fields"]["expected_schema"] if item["field"] == "TBMJ")
        self.assertEqual((tbmj["source_length"], tbmj["source_decimals"]), (15, 2))

    def test_missing_field_is_fail(self):
        fields = [f for f in FakeArcPy()._fields if f.name != "XDM"]
        result = self.run_check(FakeArcPy(fields=fields))
        self.assertEqual(result["detail"]["fields"]["status"], "FAIL")
        self.assertIn("XDM", result["detail"]["fields"]["missing"])

    def test_wrong_type_and_char_length_are_fail(self):
        fields = [Field(f.name, "Double", f.length) if f.name == "XDM" else
                  Field(f.name, f.type, 10) if f.name == "XMC" else f
                  for f in FakeArcPy()._fields]
        result = self.run_check(FakeArcPy(fields=fields))
        mismatches = result["detail"]["fields"]["mismatched"]
        self.assertEqual({row["field"] for row in mismatches}, {"XDM", "XMC"})

    def test_empty_required_field_is_fail(self):
        result = self.run_check(FakeArcPy(rows=[valid_row(XDM="   ")]))
        self.assertEqual(result["detail"]["values"]["status"], "FAIL")
        self.assertEqual(result["detail"]["values"]["empty_required"][0]["field"], "XDM")

    def test_duplicate_identifier_is_fail(self):
        result = self.run_check(FakeArcPy(rows=[valid_row(), valid_row(DKBH="DK002")]))
        self.assertEqual(result["detail"]["values"]["status"], "FAIL")
        self.assertEqual(len(result["detail"]["values"]["duplicate_BSM"]), 1)
        rule = result["detail"]["values"]["duplicate_BSM_rule"]
        self.assertIn("附录C注1", rule)
        self.assertIn("-1、-2", rule)

    def test_explicit_numeric_domain_violation_is_fail(self):
        result = self.run_check(FakeArcPy(rows=[valid_row(TBMJ=0, KCXS=1.0)]))
        invalid = {row["field"] for row in result["detail"]["values"]["invalid_domain"]}
        self.assertEqual(invalid, {"TBMJ", "KCXS"})

    def test_explicit_coded_domain_violation_is_fail(self):
        result = self.run_check(FakeArcPy(rows=[valid_row(SFYKT="Y", BCGDLY="其他")] ))
        invalid = {row["field"] for row in result["detail"]["values"]["invalid_domain"]}
        self.assertEqual(invalid, {"SFYKT", "BCGDLY"})

    def test_valid_row_passes_explicit_checks_but_keeps_manual_review(self):
        result = self.run_check(FakeArcPy())
        self.assertEqual(result["detail"]["fields"]["status"], "PASS")
        self.assertEqual(result["detail"]["values"]["status"], "NEEDS_REVIEW")
        self.assertIn("GDLX", result["detail"]["values"]["coded_values_needing_review"])
        self.assertEqual(result["status"], "NEEDS_REVIEW")

    def test_multipart_is_fail_under_appendix_c_note_6(self):
        result = self.run_check(FakeArcPy(rows=[valid_row(**{"SHAPE@": Geometry(multipart=True)})]))
        review = result["detail"]["geometry_review"]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(review["status"], "FAIL")
        self.assertEqual(review["multipart_count"], 1)
        self.assertEqual(review["multipart_BSM"], ["990002000000000000000000000001"])
        self.assertIn("附录C注6", review["reason"])
        self.assertIn("partCount>1", review["multipart_rule"])

    def test_partcount_two_is_fail_even_if_is_multipart_flag_is_false(self):
        geometry = Geometry(multipart=True)
        geometry.isMultipart = False
        result = self.run_check(FakeArcPy(rows=[valid_row(**{"SHAPE@": geometry})]))
        review = result["detail"]["geometry_review"]
        self.assertEqual(review["status"], "FAIL")
        self.assertEqual(review["multipart_count"], 1)

    def test_hole_ring_single_part_is_not_multipart_fail(self):
        # 反向对照：ArcPy 对含孔洞的单部件多边形 isMultipart=True，不得误判注6 FAIL。
        result = self.run_check(FakeArcPy(rows=[valid_row(**{"SHAPE@": Geometry(hole=True)})]))
        review = result["detail"]["geometry_review"]
        self.assertEqual(review["status"], "NEEDS_REVIEW")
        self.assertNotEqual(result["status"], "FAIL")
        self.assertEqual(review["multipart_count"], 0)
        self.assertEqual(review["multipart_BSM"], [])
        hole = review["hole_ring_review"]
        self.assertEqual(hole["status"], "NEEDS_REVIEW")
        self.assertEqual(hole["count"], 1)
        self.assertEqual(hole["parcels"][0]["partCount"], 1)
        self.assertEqual(hole["parcels"][0]["ring_count"], 2)
        self.assertEqual(hole["parcels"][0]["hole_rings"], 1)

    def test_hole_ring_review_locates_parcel_by_dkbh(self):
        # 审核附加 2：孔洞记录须带 DKBH，能定位到具体图斑。
        result = self.run_check(FakeArcPy(rows=[
            valid_row(**{"SHAPE@": Geometry(hole=True)}),
            valid_row(DKBH="DK002"),
        ]))
        parcels = result["detail"]["geometry_review"]["hole_ring_review"]["parcels"]
        self.assertEqual(len(parcels), 1)
        self.assertEqual(parcels[0]["DKBH"], "DK001")
        self.assertIn("BSM", parcels[0])

    def test_slit_connector_requires_manual_review_without_invented_threshold(self):
        result = self.run_check(FakeArcPy(rows=[valid_row()]))
        review = result["detail"]["geometry_review"]
        self.assertEqual(review["status"], "NEEDS_REVIEW")
        self.assertEqual(review["slit_connector_review"]["status"], "NEEDS_REVIEW")
        self.assertIn("细缝", review["slit_connector_review"]["action"])
        self.assertIn("未给出", review["slit_connector_review"]["threshold_note"])


if __name__ == "__main__":
    unittest.main()
