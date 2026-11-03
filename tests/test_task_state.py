import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import task_state as T


def test_each_attempt_has_a_new_identity_and_cannot_reuse_an_old_cancel(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('{}')
    output = tmp_path / 'result'
    first = T.begin_task(output, source, {})
    T.finish_task(output, first, 'FAILED')
    (output / T.CANCEL_NAME).write_text(first.get('run_id', 'missing'))
    second = T.begin_task(output, source, {}, action='resume')
    assert second.get('run_id') and second['run_id'] != first.get('run_id')
    assert not (output / T.CANCEL_NAME).exists()


def test_stop_just_before_preview_finishes_is_not_reported_as_prepared(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('{}')
    output = tmp_path / 'result'
    state = T.begin_task(output, source, {})
    T.request_cancel(output)
    with pytest.raises(T.TaskCancelled):
        T.finish_task(output, state, 'PREPARED', report_path='old.json')
    assert T.read_task(output)['status'] == 'CANCELLED'


def test_late_completion_cannot_overwrite_a_terminal_failure(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text('{}')
    output = tmp_path / 'result'
    state = T.begin_task(output, source, {})
    T.finish_task(output, state, 'FAILED', error='original')
    T.finish_task(output, state, 'COMPLETED', report_path='stale.json')
    assert T.read_task(output)['status'] == 'FAILED'


@pytest.mark.parametrize('rule,phase', [('excluded_parcels','data_prepared'),
    ('point_counts_by_unit','sampling_prepared'),('combine_parcels','imagery_prepared'),('index_scale','imagery_prepared')])
def test_project_rule_changes_invalidate_the_correct_stage(rule,phase):
    assert T.invalidated_phase({'options':{'meta':{'project_rules':{}}}},
        {'options':{'meta':{'project_rules':{rule:['changed']}}}})==phase


def test_project_map_style_and_date_do_not_discard_prepared_design():
    assert T.invalidated_phase({'options':{'meta':{}}},
        {'options':{'meta':{'map_date':'2026-10-25','presentation':{'show_inset':False}}}})=='maps_built'


@pytest.mark.parametrize('key', ['project_name', 'county', 'town', 'village', 'city', '项目名称', '县'])
def test_form_metadata_changes_invalidate_forms_and_preserve_sampling(key):
    assert T.invalidated_phase({'options': {'meta': {key: 'old'}}},
                               {'options': {'meta': {key: 'new'}}}) == 'forms_prepared'


def test_forms_code_and_templates_are_bound_to_resume_fingerprint(tmp_path):
    source = tmp_path / 'source.shp'
    source.write_bytes(b'geometry')
    _, payload = T.input_fingerprint(source, {})
    assets = payload['engine'].get('forms_prepared', {})
    assert assets.get('forms.py')
    assert assets.get('../templates/表单_附录D_实地踏勘表.docx')
    assert assets.get('../templates/表单_样品标签.docx')
    prior = {'engine': {'forms_prepared': {'template': 'old'}}}
    current = {'engine': {'forms_prepared': {'template': 'new'}}}
    assert T.invalidated_phase(prior, current) == 'forms_prepared'


@pytest.mark.parametrize("before,after", [
    ([], [{"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认"}]),
    ([{"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认"}], []),
    ([{"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认"}],
     [{"group_id": "SG_pair", "parcels": ["30", "31"], "basis": "确认"}]),
])
def test_sampling_group_changes_invalidate_data_and_all_downstream(before, after):
    assert T.invalidated_phase({"options": {"sampling_groups": before}},
                               {"options": {"sampling_groups": after}}) == "data_prepared"


def test_run_records_phases_and_cancel_is_honored_at_boundary(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"source-a")
    output = tmp_path / "out"
    state = T.begin_task(output, source, {"cell_size": None})
    T.record_phase(output, state, "data_prepared", {"feature_count": 9})
    T.request_cancel(output)
    with pytest.raises(T.TaskCancelled):
        T.record_phase(output, state, "sampling_prepared")
    saved = T.read_task(output)
    assert saved["status"] == "CANCELLED"
    assert saved["phases"]["data_prepared"]["status"] == "COMPLETED"
    assert not (output / T.CANCEL_NAME).exists()


def test_resume_keeps_old_attempt_and_invalidates_when_source_changes(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"source-a")
    output = tmp_path / "out"
    first = T.begin_task(output, source, {"cell_size": None})
    T.finish_task(output, first, "FAILED", error="simulated interruption")
    source.write_bytes(b"source-b")
    resumed = T.begin_task(output, source, {"cell_size": None}, action="resume")
    assert resumed["input_changed_on_resume"] is True
    assert resumed["latest_run_dir"].endswith("attempt_02")
    assert Path(first["latest_run_dir"]).exists()
    assert json.loads((output / T.STATE_NAME).read_text(encoding="utf-8"))["attempt"] == 2


def test_new_run_refuses_unrelated_nonempty_output(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"data")
    output = tmp_path / "out"
    output.mkdir()
    (output / "user-file.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        T.begin_task(output, source, {})
    assert (output / "user-file.txt").read_text(encoding="utf-8") == "preserve"


def test_unchanged_resume_reuses_completed_stage_context(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"geometry")
    output = tmp_path / "out"
    first = T.begin_task(output, source, {"dpi": 300})
    T.record_phase(output, first, "data_prepared", {"gdb": "saved.gdb", "count": 9})
    T.finish_task(output, first, "FAILED", error="network interruption")
    resumed = T.begin_task(output, source, {"dpi": 300}, action="resume")
    assert resumed["latest_run_dir"] == first["latest_run_dir"]
    assert resumed["phases"]["data_prepared"]["detail"]["count"] == 9


def test_map_metadata_change_preserves_data_but_invalidates_maps(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"geometry")
    output = tmp_path / "out"
    first = T.begin_task(output, source, {"dpi": 150})
    for phase in T.PHASES:
        T.record_phase(output, first, phase, {"saved": phase})
    T.finish_task(output, first, "COMPLETED")
    resumed = T.begin_task(output, source, {"dpi": 300}, action="resume")
    assert resumed["invalidated_from"] == "maps_built"
    assert resumed["phases"]["sampling_prepared"]["status"] == "COMPLETED"
    assert resumed["phases"]["maps_built"]["status"] == "PENDING"


def test_shapefile_dbf_change_and_credentials_are_fingerprinted(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"geometry")
    dbf = source.with_suffix(".dbf")
    dbf.write_bytes(b"attribute-a")
    first, details = T.input_fingerprint(source, {"tianditu_key": "secret-token"})
    assert "secret-token" not in json.dumps(details)
    dbf.write_bytes(b"attribute-b")
    second, _ = T.input_fingerprint(source, {"tianditu_key": "secret-token"})
    assert first != second


def test_resume_checks_owner_identity_without_sending_signals(tmp_path, monkeypatch):
    source = tmp_path / "input.shp"
    source.write_bytes(b"data")
    monkeypatch.setattr(T, "_process_identity", lambda _pid: (True, "created-a"))
    monkeypatch.setattr(T.os, "kill", lambda *_args: pytest.fail("探活不得发送信号"))
    state = T.begin_task(tmp_path / "out", source, {})
    assert state["process_created"] == "created-a"
    with pytest.raises(RuntimeError, match="仍由进程"):
        T.begin_task(tmp_path / "out", source, {}, action="resume")
    monkeypatch.setattr(T, "_process_identity", lambda _pid: (True, "reused-pid"))
    assert T.begin_task(tmp_path / "out", source, {}, action="resume")["attempt"] == 2


def test_completed_task_can_recheck_and_repair_outputs(tmp_path):
    source = tmp_path / "input.shp"
    source.write_bytes(b"data")
    out = tmp_path / "out"
    state = T.begin_task(out, source, {})
    T.finish_task(out, state, "COMPLETED")
    resumed = T.begin_task(out, source, {}, action="resume")
    assert resumed["status"] == "RUNNING"
    assert resumed["latest_run_dir"] == state["latest_run_dir"]


def test_factor_source_edit_invalidates_data_stage(tmp_path):
    source = tmp_path / "parcels.shp"
    soil = tmp_path / "soil.shp"
    source.write_bytes(b"parcels")
    soil.write_bytes(b"soil-a")
    options = {"factor_sources": {"soil_species": {"path": str(soil), "field": "TZMC"}}}
    _hash, before = T.input_fingerprint(source, options)
    soil.write_bytes(b"soil-b")
    _hash, after = T.input_fingerprint(source, options)
    assert T.invalidated_phase(before, after) == "data_prepared"


def test_unconfigured_factor_sources_has_no_attachments(tmp_path):
    source = tmp_path / "parcels.shp"
    source.write_bytes(b"parcels")
    _hash, details = T.input_fingerprint(source, {"factor_sources": None})
    assert not details["attachments"]


def test_unit_imagery_change_invalidates_only_imagery_and_downstream(tmp_path):
    source = tmp_path / "source.shp"
    image = tmp_path / "unit_dom.tif"
    source.write_bytes(b"parcels")
    image.write_bytes(b"image-a")
    options = {"unit_imagery_sources": {"U1": {"raster": str(image)}}}
    _digest, before = T.input_fingerprint(source, options)
    image.write_bytes(b"image-b")
    _digest, after = T.input_fingerprint(source, options)
    assert T.invalidated_phase(before, after) == "imagery_prepared"
