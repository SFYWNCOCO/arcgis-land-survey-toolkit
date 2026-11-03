from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import oneclick


def test_visual_failure_is_separate_from_gis_technical_checks(monkeypatch):
    from acceptance import workflow
    monkeypatch.setattr(workflow, 'validate_visual_review', lambda *_: {'status': 'FAIL', 'detail': 'visual issue'})
    exported = {'outputs': [{'jpg': 'test.jpg', 'checks': [{'item': '真实导出', 'status': 'PASS'}]}]}
    review = workflow.refresh_visual_reviews(exported)
    assert review['status'] == 'FAIL' and exported['status'] == 'FAIL'
    assert exported['technical_status'] == 'PASS'


def test_technical_failure_is_not_hidden_by_a_passing_visual_review(monkeypatch):
    from acceptance import workflow
    monkeypatch.setattr(workflow, 'validate_visual_review', lambda *_: {'status': 'PASS', 'detail': 'visual pass'})
    exported = {'outputs': [{'jpg': 'test.jpg', 'checks': [{'item': '影像', 'status': 'FAIL'}]}]}
    workflow.refresh_visual_reviews(exported)
    assert exported['technical_status'] == 'FAIL'


class FakeArcPy:
    class DescribeResult:
        OIDFieldName = "FID"

    @staticmethod
    def Describe(_source):
        return FakeArcPy.DescribeResult()

    @staticmethod
    def AddFieldDelimiters(_source, field):
        return '"{}"'.format(field)


def test_selection_ids_build_bounded_oid_filter_and_deduplicate():
    where, count = oneclick._selection_where(FakeArcPy, "parcel", "8,2,8,4")
    assert where == '("FID" IN (2,4,8))'
    assert count == 3


def test_cancel_does_not_require_a_still_available_source_layer(tmp_path, monkeypatch):
    import types
    def unavailable(_source):
        raise RuntimeError('source layer has been removed')
    monkeypatch.setitem(sys.modules, 'arcpy', types.SimpleNamespace(Describe=unavailable))
    monkeypatch.setattr(oneclick.T, 'request_cancel', lambda _out: {'status': 'CANCEL_REQUESTED'})
    assert oneclick.run('removed layer', tmp_path, task_action='cancel')['status'] == 'CANCEL_REQUESTED'


def test_large_selection_is_rejected_with_an_actionable_message():
    ids = ",".join(str(i) for i in range(10001))
    with pytest.raises(ValueError, match="10,000"):
        oneclick._selection_where(FakeArcPy, "parcel", ids)


def test_context_layers_are_optional_and_relative_to_source(tmp_path):
    import json
    import os
    from acceptance import workflow
    source = tmp_path / "图斑.shp"
    source.write_text("", encoding="utf-8")
    assert workflow._load_context_layers(str(source), lambda _m: None) == []
    (tmp_path / "道路.shp").write_text("", encoding="utf-8")
    (tmp_path / "地理要素.json").write_text(
        json.dumps({"道路": "道路.shp", "无关角色": "x.shp"}, ensure_ascii=False), encoding="utf-8")
    layers = workflow._load_context_layers(str(source), lambda _m: None)
    assert layers == [{"role": "道路", "path": os.path.abspath(str(tmp_path / "道路.shp")), "name": "道路"}]


def test_context_layers_reject_missing_file(tmp_path):
    import json
    from acceptance import workflow
    source = tmp_path / "图斑.shp"
    source.write_text("", encoding="utf-8")
    (tmp_path / "地理要素.json").write_text(
        json.dumps({"水系": "不存在.shp"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        workflow._load_context_layers(str(source), lambda _m: None)


def test_full_map_preview_stays_unconfirmed_and_shares_design_fingerprint(tmp_path, monkeypatch):
    from acceptance import workflow, task_state
    source = tmp_path / "图斑.geojson"
    source.write_text("{}", encoding="utf-8")
    calls = []

    def execute(**options):
        calls.append(options)
        return {"技术状态": "PASS", "出图": {"status": "NEEDS_REVIEW", "outputs": []}}

    monkeypatch.setattr(workflow, "execute", execute)
    output = tmp_path / "成果"
    result = oneclick.run(source, output, task_action="preview", meta={})
    state = task_state.read_task(output)
    assert calls[0]["draft_maps"] is True and calls[0]["prepare_only"] is False
    assert state["status"] == "PREPARED" and "设计确认" not in result
    assert "draft_maps" not in state["input_details"]["options"]
    assert state["phases"]["completed"]["status"] == "PENDING"


def test_preview_plan_marks_every_sheet_and_keeps_formal_paths_untouched(tmp_path):
    from acceptance import workflow
    plan = {"meta": {"org": ""}, "targets": [{"name": "地块39", "title": "采样点位布置图",
            "png": "正式/39.png", "jpg": "正式/39.jpg", "pdf": "正式/39.pdf"}]}
    draft = workflow._preview_render_plan(plan, str(tmp_path))
    assert draft["targets"][0]["title"].startswith("【设计预览】")
    assert draft["targets"][0]["preview_only"] is True
    assert all(str(tmp_path) in draft["targets"][0][ext] for ext in ("png", "jpg", "pdf"))
    assert plan["targets"][0]["png"] == "正式/39.png" and not plan["targets"][0].get("preview_only")


def test_atlas_retains_one_original_parcel_after_area_limit_splits_it():
    import types
    from acceptance import workflow
    geometry = types.SimpleNamespace(extent=types.SimpleNamespace(XMin=0, YMin=0, XMax=400, YMax=200),
                                     getArea=lambda *_args: 80000)
    records = {"assign": [{"SRC_DKBH": "39", "DKBH": "39#1", "PJDYBH": "U1"},
                          {"SRC_DKBH": "39", "DKBH": "39#2", "PJDYBH": "U2"}],
               "parcels": [{"DKBH": "39", "SHAPE@": geometry}]}

    class Cursor:
        def __init__(self, path, fields): self.rows = [tuple(row[f] for f in fields) for row in records[path]]
        def __enter__(self): return iter(self.rows)
        def __exit__(self, *_args): pass

    api = types.SimpleNamespace(da=types.SimpleNamespace(SearchCursor=Cursor))
    data = {"assign": "assign", "parcels": "parcels", "assign_id": "DKBH", "unit_field": "PJDYBH",
            "source_id_field": "SRC_DKBH"}
    targets = workflow._atlas_plan(api, data, "DKBH", 1)
    sampling = [t for t in targets if t["kind"] == "sample"]
    assert len(sampling) == 1 and sampling[0]["parcel"] == "39"
    assert sampling[0]["parcel_ids"] == ["39#1", "39#2"]
