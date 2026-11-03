"""外业表格与标签生成：数量、字段读回、未填项与模板缺口。"""
from __future__ import annotations

from pathlib import Path

import pytest
from acceptance import forms


def _report(tmp_path, with_meta=True):
    report = {
        "单元明细": [
            {"unit": "F99000220261461E0001", "mu": 35.4, "parcels": ["1"], "landtype": "旱地",
             "area_m2": 23600.0},
            {"unit": "F99000220261461E0002", "mu": 17.9, "parcels": ["111"], "landtype": "旱地"},
        ],
        "网格单元明细": [
            {"评价单元": "F99000220261461E0001", "设计点数": 5},
            {"评价单元": "F99000220261461E0002", "设计点数": 3},
        ],
        "点位清单": None,
    }
    if with_meta:
        report["项目信息"] = {"project_name": "示例县2025年度第一批耕地恢复",
                              "county": "扶绥县", "town": "示例镇", "village": "某某村"}
    return report


def _docx():
    return forms._docx()


def _value(table, label):
    cell = forms._value_after_label(table, label)
    assert cell is not None, label
    return cell.text


def test_builds_two_units_and_reads_back_prefilled_fields(tmp_path):
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out", meta=None)
    assert manifest["状态"] == "PASS"
    assert manifest["单元数"] == 2
    assert len(manifest["踏勘表"]) == 2
    assert len(manifest["采样记录表"]) == 2
    assert len(manifest["鉴定意见表"]) == 2
    assert manifest["标签"]["count"] == 8

    doc = _docx().Document(manifest["踏勘表"][0])
    table = doc.tables[0]
    assert "F99000220261461E0001" in table.cell(0, 1).text
    assert "示例县2025年度第一批耕地恢复" in table.cell(1, 1).text
    assert _value(table, "图斑编号").strip() == "1"
    assert "23600.00" in _value(table, "面积（m2）")
    assert "示例镇" in _value(table, "评价单元地理位置")
    assert "旱地" in _value(table, "利用类型")
    assert "☑粤西桂南农林区" in _value(table, "农业分区")
    assert not _value(table, "变更前土地利用类型").strip()

    sample = _docx().Document(manifest["采样记录表"][0]).tables[0]
    assert sample.cell(0, 1).text.strip() == "F99000220261461E0001"
    assert _value(sample, "土壤容重样品编号").strip() == "RZF99000220261461E0001"
    assert _value(sample, "分样点数量（个）").strip() == "5"
    labels = [row.cells[0].text for row in sample.rows
              if row.cells[0].text.startswith("分样点") and "地理坐标" in row.cells[0].text]
    assert len(labels) >= 5, "五个设计点应扩展出五个分样点行"
    assert [label[:4] for label in labels[:5]] == ["分样点1", "分样点2", "分样点3", "分样点4", "分样点5"]

    opinion = _docx().Document(manifest["鉴定意见表"][0])
    assert "F99000220261461E0001" in _value(opinion.tables[0], "评价单元编号")
    assert len(opinion.tables) == 1
    assert "23600.00" in _value(opinion.tables[0], "评价单元面积（m2）")
    assert not _value(opinion.tables[0], "农业生产符合性评价").strip(), "原表说明不能成为实际鉴定结论"


def test_missing_meta_is_flagged_not_invented(tmp_path):
    manifest = forms.build_forms(_report(tmp_path, with_meta=False), tmp_path / "out", meta=None)
    assert manifest["状态"] == "NEEDS_REVIEW"
    missing = manifest["未填项"]["F99000220261461E0001"]
    assert "项目名称" in missing
    assert "评价单元地理位置" in missing
    assert "农业分区" in missing
    doc = _docx().Document(manifest["踏勘表"][0])
    assert forms.PLACEHOLDER in doc.tables[0].cell(1, 1).text


def test_labels_are_design_only_and_bulk_ids_use_rz_prefix(tmp_path):
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out", meta=None)
    doc = _docx().Document(manifest["标签"]["path"])
    texts = []
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.append("\n".join(paragraph.text for paragraph in cell.paragraphs))
    joined = "\n".join(texts)
    assert "RZF99000220261461E0001" in joined
    assert "不是实采记录" in joined
    assert len(doc.tables) == 8
    assert sum("混合样" in _value(t, "编号") for t in doc.tables) == 2
    assert sum("容重平行样" in _value(t, "编号") for t in doc.tables) == 6
    for table in doc.tables:
        for label in ("采样地点", "采样日期", "采样人", "采样深度", "土壤名称"):
            assert not _value(table, label).strip(), "设计信息不能冒充现场记录"
    assert all("签" in p.text for p in doc.paragraphs if "土壤样品标" in p.text)


def test_region_lookup_and_unknown_county(tmp_path):
    lookup = forms._region_lookup()
    assert forms.region_for_county("扶绥县", lookup) == "粤西桂南农林区"
    assert forms.region_for_county("不存在县", lookup) == ""


def test_missing_templates_fail_loudly(tmp_path):
    with pytest.raises(FileNotFoundError, match="表单模板缺失"):
        forms.ensure_templates(tmp_path / "empty", source_docx=None)


def test_manifest_html_keeps_css_braces(tmp_path):
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out", meta=None)
    path = forms.write_manifest_html(manifest, tmp_path / "清单.html")
    text = (tmp_path / "清单.html").read_text(encoding="utf-8")
    assert path.endswith("清单.html")
    assert "border-collapse" in text
    assert "实地踏勘表" in text


def test_area_falls_back_to_mu_when_report_lacks_square_meters(tmp_path):
    report = _report(tmp_path)
    report["单元明细"][1].pop("area_m2", None)
    manifest = forms.build_forms(report, tmp_path / "out", meta=None)
    doc = _docx().Document(manifest["踏勘表"][1])
    expected = 17.9 * 666.6666667
    assert f"{expected:.2f}" in _value(doc.tables[0], "面积（m2）")


def test_template_label_drift_is_reported_as_missing(tmp_path):
    """金丝雀：模板改坏标签后，必须报未填项，不能静默当作已填。"""
    import shutil
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    for path in forms.ensure_templates().values():
        shutil.copyfile(path, template_dir / Path(path).name)
    shutil.copyfile(Path(forms.__file__).parents[1] / "templates" / forms.LABEL_TEMPLATE_NAME,
                    template_dir / forms.LABEL_TEMPLATE_NAME)
    path = template_dir / forms.TEMPLATE_NAMES["D"]
    doc = _docx().Document(path)
    for row in doc.tables[0].rows:
        for cell in row.cells:
            if forms._norm(cell.text) == "项目名称":
                forms._set_cell(cell, "项目名")
    doc.save(path)
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out", template_dir=template_dir, meta=None)
    assert "项目名称" in manifest["未填项"]["F99000220261461E0001"]


def test_supplied_layout_and_in_table_notes_are_used(tmp_path):
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out")
    expected_rows = {"踏勘表": 19, "采样记录表": 13, "鉴定意见表": 57}
    for kind, count in expected_rows.items():
        doc = _docx().Document(manifest[kind][0])
        assert len(doc.tables) == 1
        assert len(doc.tables[0].rows) == count
        assert not any("预填" in p.text or "提示：" in p.text for p in doc.paragraphs)
    field = _docx().Document(manifest["踏勘表"][0]).tables[0]
    assert "不是实采记录" in _value(field, "其他备注说明")


def test_all_parcels_are_written_and_overflow_uses_matching_rows(tmp_path):
    report = _report(tmp_path)
    report["单元明细"] = [dict(report["单元明细"][0], parcels=[f"parcel-{n}" for n in range(1, 7)])]
    manifest = forms.build_forms(report, tmp_path / "out")
    assert manifest["状态"] == "PASS"
    table = _docx().Document(manifest["鉴定意见表"][0]).tables[0]
    rows = [row for row in table.rows if row.cells[0].text.startswith("地块编号")]
    assert len(rows) == 6
    assert [_value(table, row.cells[0].text) for row in rows] == [f"parcel-{n}" for n in range(1, 7)]
    from docx.table import _Cell
    assert all(not _Cell(row._tr.tc_lst[-1], table).text.strip() for row in rows)


def test_extra_sample_rows_keep_separate_longitude_latitude_depth_slots(tmp_path):
    report = _report(tmp_path)
    report["网格单元明细"][0]["设计点数"] = 9
    manifest = forms.build_forms(report, tmp_path / "out")
    table = _docx().Document(manifest["采样记录表"][0]).tables[0]
    rows = [r for r in table.rows if forms._norm(r.cells[0].text).startswith("分样点")
            and "地理坐标" in r.cells[0].text]
    assert len(rows) == 9
    for number, row in enumerate(rows, start=1):
        assert forms._norm(row.cells[0].text) == f"分样点{number}地理坐标及采样深度"
        assert [c.text.strip() for c in row.cells[1:]] == ["经度：", "纬度：", "深度："]


def test_split_accepts_final_single_table_f_and_has_no_trailing_page_break(tmp_path):
    from docx.oxml.ns import qn
    source = _docx().Document()
    for key in "DEF":
        source.add_paragraph("附录" + key)
        source.add_table(rows=1, cols=2)
    source.add_page_break()
    path = tmp_path / "source.docx"
    source.save(path)
    outputs = forms.split_templates(path, tmp_path / "templates")
    assert set(outputs) == set("DEF")
    for output in outputs.values():
        doc = _docx().Document(output)
        assert len(doc.tables) == 1
        assert not list(doc.element.body.iter(qn("w:br")))


def test_invalid_source_does_not_publish_partial_templates(tmp_path):
    source = _docx().Document()
    for key in "DE":
        source.add_paragraph("附录" + key)
        source.add_table(rows=1, cols=2)
    path = tmp_path / "bad.docx"
    source.save(path)
    output = tmp_path / "templates"
    with pytest.raises(ValueError):
        forms.split_templates(path, output)
    assert not list(output.glob("*.docx"))


def test_resume_rejects_missing_labels_and_corrupt_documents(tmp_path):
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "out")
    assert forms.outputs_available(manifest)
    labels = Path(manifest["标签"]["path"])
    labels.unlink()
    assert not forms.outputs_available(manifest)
    manifest = forms.build_forms(_report(tmp_path), tmp_path / "new")
    Path(manifest["踏勘表"][0]).write_text("not a Word document", encoding="utf-8")
    assert not forms.outputs_available(manifest)


def test_fill_preserves_opaque_template_parts_and_styles(tmp_path):
    from zipfile import ZipFile
    import shutil
    template = tmp_path / 'field.docx'
    shutil.copyfile(forms.ensure_templates()['D'], template)
    with ZipFile(template, 'a') as archive:
        archive.writestr('custom/preserve.bin', b'opaque retained template material')
    output = tmp_path / 'filled.docx'
    forms._fill_field_form(str(template), str(output), forms.context_from_report(_report(tmp_path))[0])
    with ZipFile(template) as before, ZipFile(output) as after:
        for name in before.namelist():
            if name != 'word/document.xml':
                assert name in after.namelist(), name
                assert before.read(name) == after.read(name), name


def test_fill_cannot_overwrite_the_reference_template(tmp_path):
    import shutil
    template = tmp_path / 'reference.docx'
    shutil.copyfile(forms.ensure_templates()['D'], template)
    original = template.read_bytes()
    with pytest.raises(ValueError, match='不能覆盖'):
        forms._fill_field_form(str(template), str(template), forms.context_from_report(_report(tmp_path))[0])
    assert template.read_bytes() == original
