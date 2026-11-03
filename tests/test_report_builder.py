"""初鉴报告底稿：表 1/表 2 填表、平均等级与不编造结论。"""
from __future__ import annotations

import pytest
from acceptance import report_builder as R


def _units():
    return [
        {"unit": "U1", "parcels": ["A"], "area_mu": 10.0, "land_use": "旱地"},
        {"unit": "U2", "parcels": ["B"], "area_mu": 20.0, "land_use": "水田"},
    ]


def _results():
    return {
        "U1": {"status": "PASS", "result": "合格", "failures": [],
               "checks": [{"指标": name, "实测值": value, "判定": "PASS"}
                          for name, value in (("有效土层厚度", 50), ("土壤有机质含量", 15),
                                              ("土壤pH", 6.5), ("水资源保障条件", "满足"),
                                              ("地形坡度", 5), ("田面平整程度", "平整"),
                                              ("土壤侵入体及砾石", {"10_100mm_percent": 5, "over_100mm": False}),
                                              ("道路通行条件", "可通达"),
                                              ("土壤污染状况", {"ph": 6.5, "镉": 0.1}))]},
        "U2": {"status": "FAIL", "result": "不合格", "failures": ["土壤pH"],
               "checks": [{"指标": "土壤pH", "实测值": 9.0, "判定": "FAIL"}]},
    }


def _grades():
    return {"U1": {"status": "PASS", "ZLDJ": 3, "JQZLDJ": 2.5},
            "U2": {"status": "PASS", "ZLDJ": 4, "JQZLDJ": 3.5}}


def test_builds_tables_for_all_units_and_weighted_average(tmp_path):
    output = tmp_path / "初鉴报告.docx"
    manifest = R.build_report(_units(), _results(), _grades(), output,
                             meta={"project_name": "示例区测试项目",
                                   "project_number": "F99000220261461"})
    assert output.is_file()
    assert manifest["units"] == 2
    doc = R._docx().Document(output)
    table1, table2 = doc.tables[0], doc.tables[1]
    assert len(table1.rows) == 3
    assert table1.cell(1, 0).text.strip() == "U1"
    assert table1.cell(2, 0).text.strip() == "U2"
    assert "不合格" in table1.cell(2, 11).text
    assert len(table2.rows) == 4
    assert table2.cell(1, 0).text.strip() == "U1"
    assert table2.cell(1, 3).text.strip() == "3"
    assert table2.cell(1, 4).text.strip() == "2.50"
    assert table2.cell(3, 3).text.strip() == "3.67"
    assert table2.cell(3, 4).text.strip() == "3.17"
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "生成依据与待补清单" in text
    assert "不得作为正式报告" not in text or "底稿" in text


def test_missing_precise_grade_marks_average_unverified(tmp_path):
    grades = _grades()
    grades["U2"]["JQZLDJ"] = None
    output = tmp_path / "报告.docx"
    R.build_report(_units(), _results(), grades, output, meta={"project_name": "P"})
    doc = R._docx().Document(output)
    assert doc.tables[1].cell(3, 4).text.strip() == "UNVERIFIED"


def test_missing_template_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError, match="模板不存在"):
        R.build_report(_units(), _results(), _grades(), tmp_path / "x.docx",
                       template_path=tmp_path / "missing.docx")


def test_missing_result_writes_pending_not_fabricated(tmp_path):
    output = tmp_path / "报告.docx"
    R.build_report(_units(), {}, {}, output, meta={"project_name": "P"})
    doc = R._docx().Document(output)
    assert doc.tables[0].cell(1, 2).text.strip() == "待补"
    assert doc.tables[1].cell(1, 3).text.strip() == "待补"
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    assert "待补/待核" in text
