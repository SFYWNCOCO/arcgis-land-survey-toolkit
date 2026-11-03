"""Behavioral tests run the compiled production C# core over real local HTTP."""

import json
import subprocess
import threading
import time
from contextlib import suppress
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "build" / "CoreHarness.dll"


def run(*args, timeout=15):
    assert HARNESS.is_file(), "Compiled C# core implementation is missing"
    p = subprocess.run(
        ["dotnet", str(HARNESS), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    return p.returncode, json.loads(p.stdout)


def test_workbench_gate_is_shared_and_late_callbacks_cannot_release_another_job():
    assert all(run('workbench_gate')[1].values())


@pytest.mark.parametrize('context,expected', [('bridge', 'BRIDGE_UNAVAILABLE'),
    ('process', 'ENV_EXECUTABLE_MISSING'), ('result', 'RESULT_MISSING')])
def test_workbench_errors_distinguish_the_object_that_was_not_found(context, expected):
    assert run('workbench_error', context)[1]['code'] == expected


@pytest.mark.parametrize('status,expected', [('401', 'MAP_AUTH'), ('403', 'MAP_AUTH'), ('429', 'MAP_QUOTA')])
def test_provider_errors_have_actionable_categories(status, expected):
    assert run('workbench_http', status)[1]['code'] == expected


def test_workbench_diagnostic_redacts_credentials_even_inside_stack_details():
    value = run('workbench_redact', 'tk=abc123&key=xyz Authorization: Bearer abc.def RAW_SECRET')[1]['text']
    assert not any(secret in value for secret in ('abc123', 'xyz', 'abc.def', 'RAW_SECRET'))


@pytest.mark.parametrize('text', ['{"key":"JSON_SECRET","token":"TOKEN_SECRET"}', "{'password': 'PASSWORD_SECRET', 'api_key': 'API_SECRET'}",
    '{"password":"SECRET TWO WORDS"}', '{"token":"SECRET \\" TWO WORDS"}'])
def test_workbench_diagnostic_redacts_quoted_secret_fields(text):
    result = run('workbench_redact', text)[1]['text']
    assert '_SECRET' not in result and 'WORDS' not in result


@pytest.fixture
def workbench_receipt(tmp_path):
    image = tmp_path / 'map.jpg'
    image.write_bytes(b'actual artifact')
    fingerprint = 'a' * 64
    report = {'任务运行ID': 'current', '输入指纹': fingerprint, '设计修订号': 2,
              '技术状态': 'PASS', '业务审核状态': 'NEEDS_REVIEW',
              '出图': {'status': 'NEEDS_REVIEW', 'technical_status': 'PASS', 'outputs': [{'jpg': str(image)}]}}
    state = {'run_id': 'current', 'input_fingerprint': fingerprint, 'design_revision': 2,
             'status': 'PREPARED', 'report_path': str(tmp_path / 'report.json'),
             'latest_run_dir': str(tmp_path), 'pid': 2147483647}
    def write():
        (tmp_path / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        (tmp_path / 'task_state.json').write_text(json.dumps(state), encoding='utf-8')
    write()
    return tmp_path, state, report, image, write


def test_workbench_preview_is_viewable_without_claiming_business_acceptance(workbench_receipt):
    root, *_ = workbench_receipt
    result = run('workbench_receipt', str(root), 'current')[1]
    assert result['status'] == 'Prepared' and result['can_open_result']
    assert result['business_status'] == 'NEEDS_REVIEW'


def test_technical_failure_keeps_verified_preview_visible_but_not_exportable(workbench_receipt):
    root, _, report, _, write = workbench_receipt
    report['技术状态'] = 'FAIL'
    report['出图']['technical_status'] = 'FAIL'
    write()
    result = run('workbench_receipt', str(root), 'current')[1]
    assert result['status'] == 'Prepared' and result['can_open_preview']
    assert not result['can_open_result']


def test_workbench_prefers_current_atlas_gallery(workbench_receipt):
    root,_,report,_,write=workbench_receipt
    gallery=root/'图件目录.html'; gallery.write_text('<h1>atlas</h1>')
    report['出图']['bundle']={'html':str(gallery)}
    write()
    result=run('workbench_receipt',str(root),'current')[1]
    assert result['summary_path']==str(gallery)


def test_workbench_reports_current_sheet_progress(workbench_receipt):
    root,state,_,_,write=workbench_receipt
    state['current_map']={'name':'地块39','index':2,'total':11}
    write()
    result=run('workbench_receipt',str(root),'current')[1]
    assert result['map_progress']=='2/11 地块39'


def test_delivery_reads_current_attempt_instead_of_root_history(workbench_receipt):
    root, state, report, _, write = workbench_receipt
    attempt = root / 'attempt_02'
    attempt.mkdir()
    state['report_path'] = str(attempt / 'report.json')
    state['latest_run_dir'] = str(attempt)
    write()
    (root / '一键成果报告.json').write_text(json.dumps(report), encoding='utf-8')
    (attempt / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    (attempt / '检查清单.json').write_text(json.dumps({'检查': [{'级别': 'NEEDS_REVIEW', '项目': 'current'}]}), encoding='utf-8')
    (root / '检查清单.json').write_text(json.dumps({'检查': [{'级别': 'FAIL', '项目': 'previous'}]}), encoding='utf-8')
    result = run('workbench_delivery', str(root))[1]
    assert result['report'] == state['report_path']
    assert result['checklist'] == str(attempt / '检查清单.json')
    assert result['counts'] == {'fail': 0, 'review': 1, 'maps': 1}


@pytest.mark.parametrize('as_array', [False, True])
def test_delivery_preserves_checklist_failures_for_current_report(workbench_receipt, as_array):
    root, *_ = workbench_receipt
    rows = [{'级别': 'FAIL', '项目': 'current issue'}]
    (root / '检查清单.json').write_text(json.dumps(rows if as_array else {'检查': rows}), encoding='utf-8')
    result = run('workbench_delivery', str(root))[1]
    assert result['status'] == 'FAIL' and result['counts']['fail'] == 1


@pytest.mark.parametrize('fault', ['wrong_run', 'wrong_fingerprint', 'wrong_revision', 'outside', 'missing_state', 'bad_state', 'running'])
def test_delivery_rejects_unbound_results_without_using_root_history(workbench_receipt, fault):
    root, state, report, _, write = workbench_receipt
    if fault == 'wrong_run': report['任务运行ID'] = 'previous'
    if fault == 'wrong_fingerprint': report['输入指纹'] = 'previous'
    if fault == 'wrong_revision': report['设计修订号'] = 1
    if fault == 'outside': state['report_path'] = str(root.parent / 'unrelated.json')
    if fault == 'running': state['status'] = 'RUNNING'
    write()
    (root / '一键成果报告.json').write_text(json.dumps(report), encoding='utf-8')
    if fault == 'missing_state': (root / 'task_state.json').unlink()
    if fault == 'bad_state': (root / 'task_state.json').write_text('{')
    result = run('workbench_delivery', str(root))[1]
    assert result['status'] == 'UNVERIFIED' and not result.get('report')


def test_failed_ai_review_does_not_rewrite_successful_gis_processing(workbench_receipt):
    root, _, report, _, write = workbench_receipt
    report['出图']['status'] = 'FAIL'
    report['出图']['technical_status'] = 'PASS'
    report['视觉复核'] = {'status': 'FAIL'}
    write()
    result = run('workbench_receipt', str(root))[1]
    assert result['status'] == 'Prepared' and result['can_open_result']
    assert result['visual_status'] == 'FAIL'


@pytest.mark.parametrize('fault', ['old_run', 'fingerprint', 'missing_fingerprint', 'malformed_fingerprint',
                                   'revision', 'deleted', 'zero', 'corrupt', 'failed', 'no_outputs'])
def test_workbench_never_accepts_stale_or_invalid_results(workbench_receipt, fault):
    root, state, report, image, write = workbench_receipt
    if fault == 'old_run': report['任务运行ID'] = 'previous'
    if fault == 'fingerprint': report['输入指纹'] = 'previous'
    if fault == 'missing_fingerprint':
        state.pop('input_fingerprint')
        report.pop('输入指纹')
    if fault == 'malformed_fingerprint':
        state['input_fingerprint'] = report['输入指纹'] = 'inputs'
    if fault == 'revision': report['设计修订号'] = 1
    if fault == 'deleted': image.unlink()
    if fault == 'zero': image.write_bytes(b'')
    if fault == 'failed': state['status'] = 'FAILED'
    if fault == 'no_outputs': report['出图']['outputs'] = []
    write()
    if fault == 'corrupt': (root / 'report.json').write_text('{')
    result = run('workbench_receipt', str(root), 'current')[1]
    assert not result['can_open_result'] and result['status'] == 'Failed'


def test_workbench_crash_is_interrupted_and_does_not_automatically_restart(workbench_receipt):
    root, state, _, _, write = workbench_receipt
    state['status'] = 'RUNNING'
    write()
    result = run('workbench_receipt', str(root))[1]
    assert result['status'] == 'Interrupted' and result['can_resume']


@pytest.mark.parametrize('payload', ['null', '[]', '{}'])
def test_malformed_state_is_a_diagnostic_instead_of_an_unhandled_ui_exception(tmp_path, payload):
    (tmp_path / 'task_state.json').write_text(payload)
    code, result = run('workbench_receipt', str(tmp_path))
    assert code == 0 and result['status'] == 'Failed' and not result['can_open_result']


@pytest.mark.parametrize('field', ['attempt', 'pid', 'design_revision'])
def test_out_of_range_state_numbers_are_reported_as_invalid(workbench_receipt, field):
    root, state, _, _, write = workbench_receipt
    state[field] = 999999999999999
    if field == 'pid': state['status'] = 'RUNNING'
    write()
    code, result = run('workbench_receipt', str(root))
    assert code == 0 and result['status'] == 'Failed'


def test_late_invalid_report_fields_cannot_leave_result_buttons_enabled(workbench_receipt):
    root, _, report, _, write = workbench_receipt
    report['业务审核状态'] = {}
    write()
    code, result = run('workbench_receipt', str(root))
    assert code == 0 and result['status'] == 'Failed'
    assert not result['can_open_result'] and not result['can_open_preview']


def test_local_commands_do_not_become_model_prompts():
    for command in ("help", "map", "layers", "tasks", "sampling", "analysis", "chat", "basemap", "clear", "model", "stop"):
        assert run("command", " /" + command + " ")[1]["command"] == command
    assert run("command", "检查地块")[1]["command"] is None
    assert run("command", "/unknown")[0] != 0
    assert run("command", "/clear everything")[0] != 0


def test_changed_sampling_scope_is_refused_instead_of_using_old_snapshot():
    assert run('scope_snapshot','[1,2]','[2,1]','x=1','x=1','selection')[0] == 0
    assert run('scope_snapshot','[1,2]','[3]','x=1','x=1','selection')[0] != 0
    assert run('scope_snapshot','[1,2]','[1,2]','x=1','x=2','selection')[0] != 0


def test_scope_guard_ignores_only_changes_outside_the_chosen_scope():
    assert run('scope_snapshot','[]','[3]','','x=1','all')[0] == 0
    assert run('scope_snapshot','[]','[3]','x=1','x=1','query')[0] == 0
    assert run('scope_snapshot','[]','[3]','x=1','x=2','query')[0] != 0
    assert run('scope_snapshot','[]','[3]','','','invalid')[0] != 0


def test_file_uri_dataset_from_pro_is_sent_to_gp_as_a_native_path():
    assert run('dataset_path', 'file:///D:/工作/地块.shp')[1]['path'] == r'D:\工作\地块.shp'
    assert run('dataset_path', r'D:\工作\data.gdb\parcels')[1]['path'] == r'D:\工作\data.gdb\parcels'
    assert run('dataset_path', 'https://example.com/FeatureServer/0')[1]['path'] == 'https://example.com/FeatureServer/0'


def test_shared_basemap_saves_only_edited_fields(tmp_path):
    result = run('basemap_merge',str(tmp_path/'maps.json'))[1]
    assert result['preserved'] and result['explicit_clear']


BASEMAP_SOURCES = {
    "tianditu_image", "tianditu_vector", "tianditu_terrain",
    "amap_satellite", "amap_vector", "amap_district"}


def test_basemap_configuration_keeps_credentials_out_of_catalog_and_disk(tmp_path):
    receipt = run("basemap_settings", str(tmp_path / "maps.json"))[1]
    assert receipt["same"]
    assert "PRIVATE_" not in receipt["stored"]
    catalog = run("basemap_catalog")[1]
    assert {row["id"] for row in catalog["sources"]} == BASEMAP_SOURCES
    assert {row["id"]: row["status"] for row in catalog["sources"]} == {
        "tianditu_image": "NEEDS_CONFIGURATION", "tianditu_vector": "NEEDS_CONFIGURATION",
        "tianditu_terrain": "NEEDS_CONFIGURATION",
        "amap_satellite": "READY_REFERENCE_ONLY", "amap_vector": "READY_REFERENCE_ONLY",
        "amap_district": "NEEDS_CONFIGURATION"}
    # 高德瓦片无需 Key 即可加载为参考底图；天地图与区图分别缺自己的 Key。
    assert {row["id"] for row in catalog["sources"] if row["can_load_into_map"]} == {
        "amap_satellite", "amap_vector"}
    for provider in ("amap_vector", "tianditu_image"):
        assert run("basemap_missing", provider)[0] != 0


def test_basemap_catalog_reports_coordinate_system_correction_and_map_mode():
    rows = {row["id"]: row for row in run("basemap_catalog", "ready")[1]["sources"]}
    expected = {
        "tianditu_image": ("CGCS2000 / Web Mercator", "NONE", "raster_or_live"),
        "tianditu_vector": ("CGCS2000 / Web Mercator", "NONE", "raster_or_live"),
        "tianditu_terrain": ("CGCS2000 / Web Mercator", "NONE", "raster_or_live"),
        "amap_satellite": ("GCJ-02（纠偏后 CGCS2000）", "GCJ02_TO_CGCS2000", "raster_only_corrected"),
        "amap_vector": ("GCJ-02（纠偏后 CGCS2000）", "GCJ02_TO_CGCS2000", "raster_only_corrected"),
        "amap_district": ("GCJ-02（参考界线）", "NONE", "vector_layers"),
    }
    for source, (coordinate, correction, mode) in expected.items():
        row = rows[source]
        assert row["coordinate_system"] == coordinate
        assert row["correction"] == correction
        assert row["map_mode"] == mode
        assert row["positional_accuracy_status"] == "UNVERIFIED"
        assert row["source_gsd_status"] == "UNVERIFIED"
        assert row["label"] and row["map_layer_hint"]
        assert row["status"] == ("READY_REFERENCE_ONLY" if source.startswith("amap_")
                                 else "CONFIGURED_NOT_VERIFIED")
        assert row["can_load_into_map"] is True
    missing = {row["id"]: row for row in run("basemap_catalog")[1]["sources"]}
    assert not missing["tianditu_image"]["can_load_into_map"]
    assert not missing["amap_district"]["can_load_into_map"]
    assert missing["amap_satellite"]["can_load_into_map"]


def test_preview_sources_keep_district_out_of_browser_preview():
    value = run("basemap_preview_sources")[1]
    assert value["sources"] == ["tianditu_image", "tianditu_vector", "tianditu_terrain",
                                "amap_satellite", "amap_vector", "amap_district"]
    assert value["preview_sources"] == ["tianditu_image", "tianditu_vector", "tianditu_terrain",
                                        "amap_satellite", "amap_vector"]
    assert value["labels"] == ["天地图影像", "天地图矢量", "天地图地形晕渲",
                               "高德卫星影像", "高德地图", "高德行政区划区图"]
    assert value["corrections"] == ["NONE", "NONE", "NONE",
                                    "GCJ02_TO_CGCS2000", "GCJ02_TO_CGCS2000", "NONE"]
    assert value["gcj02"] == [False, False, False, True, True, True]
    assert value["tianditu"] == [True, True, True, False, False, False]


def test_amap_district_refuses_browser_preview():
    code, result = run("basemap_page", "amap_district", "106.9", "23.7")
    assert code != 0 and "行政区划" in result["error"]


@pytest.mark.parametrize("provider", ["amap_vector", "amap_satellite", "tianditu_image", "tianditu_vector", "tianditu_terrain"])
def test_basemap_preview_uses_private_loopback_page_and_explicit_coordinates(provider):
    code, page = run("basemap_page", provider, "106.9", "23.7")
    assert code == 0 and page["status"] == 200 and page["denied"] == 404
    assert page["origin_denied"] == 403
    assert "no-store" in page["cache"] and page["url"].startswith("http://127.0.0.1:")
    assert "TEST_" not in page["url"] and "UNVERIFIED" in page["html"]
    if provider.startswith("amap"):
        assert "AMap.convertFrom" in page["html"] and "'gps'" in page["html"]
        assert "TileLayer.Satellite" in page["html"] and "GCJ-02" in page["html"]
    else:
        token = page["url"].rsplit("/", 1)[1]
        assert "t0.tianditu.gov.cn" not in page["html"]
        assert "TEST_TDT_KEY" not in page["html"]
        assert token in page["html"] and "tilePath" in page["html"]
        if provider == "tianditu_terrain":
            assert "['ter','cta']" in page["html"] and "天地图地形晕渲" in page["html"]
    assert run("basemap_page", provider, "181", "23.7")[0] != 0


@pytest.mark.parametrize("provider,zoom,maximum", [
    ("amap_vector", 16, 20), ("amap_satellite", 18, 20),
    ("tianditu_vector", 16, 18), ("tianditu_image", 18, 18), ("tianditu_terrain", 16, 18),
])
def test_basemap_preview_opens_at_detail_appropriate_to_source(provider, zoom, maximum):
    html = run("basemap_page", provider, "106.9", "23.7")[1]["html"]
    config, _ = json.JSONDecoder().raw_decode(html.split("const cfg=", 1)[1])
    assert (config["zoom"], config["minZoom"], config["maxZoom"]) == (zoom, 3, maximum)
    assert (config["lon"], config["lat"]) == (106.9, 23.7)


def test_satellite_preview_exposes_named_navigation_and_optional_accuracy_details():
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = {}

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.elements[attrs["id"]] = (tag, attrs)

    page = Controls()
    page.feed(run("basemap_page", "amap_satellite", "106.9", "23.7")[1]["html"])
    for name, label in [("zoomIn", "放大"), ("zoomOut", "缩小"), ("reset", "回到打开位置")]:
        tag, attrs = page.elements[name]
        assert tag == "button" and attrs["aria-label"] == label
    assert page.elements["labels"][1]["type"] == "checkbox"
    assert page.elements["accuracy"][0] == "details"
    assert "open" not in page.elements["accuracy"][1]
    assert page.elements["level"][0] == "output"


def test_tianditu_tile_proxy_route_is_scoped_to_preview_source_and_tile_matrix():
    token = "A" * 48
    code, tile = run("basemap_tile_route", f"/{token}/tile/img/16/442/816", token, "tianditu_image")
    assert code == 0 and tile == {"matched": True, "layer": "img", "zoom": 16, "row": 442, "column": 816}
    for layer in ("ter", "cta"):
        code, tile = run("basemap_tile_route", f"/{token}/tile/{layer}/16/442/816", token, "tianditu_terrain")
        assert code == 0 and tile["matched"] is True and tile["layer"] == layer
    for path, expected_source in [
        (f"/{token}/tile/vec/16/442/816", "tianditu_image"),
        (f"/{token}/tile/img/19/442/816", "tianditu_image"),
        (f"/{token}/tile/img/16/65536/816", "tianditu_image"),
        (f"/{token}/tile/img/16/442/65536", "tianditu_image"),
        (f"/{'B' * 48}/tile/img/16/442/816", "tianditu_image"),
        (f"/{token}/tile/img/16/442/816?tk=leak", "tianditu_image"),
        (f"/{token}/tile/img/16/442/816", "amap_vector"),
        (f"/{token}/tile/img/16/442/816", "tianditu_terrain"),
        (f"/{token}/tile/vec/16/442/816", "tianditu_terrain"),
        (f"/{token}/tile/ter/16/442/816", "amap_district"),
    ]:
        assert run("basemap_tile_route", path, token, expected_source)[1]["matched"] is False


def test_tianditu_xyz_routes_are_scoped_and_accept_image_extensions():
    token = "A" * 48
    code, tile = run("basemap_tile_route", f"/{token}/xyz/img/16/442/816.jpg", token, "tianditu_image")
    assert code == 0 and tile == {"matched": True, "layer": "img", "zoom": 16, "row": 442, "column": 816}
    code, tile = run("basemap_tile_route", f"/{token}/tiles/vec/16/816/442.jpg", token, "tianditu_vector")
    assert code == 0 and tile == {"matched": True, "layer": "vec", "zoom": 16, "row": 442, "column": 816}
    code, tile = run("basemap_tile_route", f"/{token}/xyz/cia/18/442/816.png", token, "tianditu_image")
    assert code == 0 and tile["matched"] is True and tile["layer"] == "cia"
    code, tile = run("basemap_tile_route", f"/{token}/xyz/ter/16/442/816.jpg", token, "tianditu_terrain")
    assert code == 0 and tile["matched"] is True and tile["layer"] == "ter"
    code, tile = run("basemap_tile_route", f"/{token}/tiles/cta/16/816/442.jpg", token, "tianditu_terrain")
    assert code == 0 and tile["matched"] is True and tile["layer"] == "cta"
    for path, expected_source in [
        (f"/{token}/xyz/vec/16/442/816.jpg", "tianditu_image"),
        (f"/{token}/xyz/img/16/442/816.jpg", "tianditu_terrain"),
        (f"/{token}/xyz/cta/16/442/816.jpg", "tianditu_vector"),
        (f"/{token}/xyz/img/19/442/816.jpg", "tianditu_image"),
        (f"/{token}/xyz/img/16/65536/816.jpg", "tianditu_image"),
        (f"/{token}/xyz/img/16/442/65536.jpg", "tianditu_image"),
        (f"/{token}/tiles/img/16/65536/442.jpg", "tianditu_image"),
        (f"/{token}/xyz/img/16/442/816.jpg?tk=leak", "tianditu_image"),
        (f"/{'B' * 48}/xyz/img/16/442/816.jpg", "tianditu_image"),
    ]:
        assert run("basemap_tile_route", path, token, expected_source)[1]["matched"] is False


def test_snapshot_cache_key_rounds_extent_and_separates_key_and_labels():
    def key(xmin, keyhash="ABCD1234", labels="true"):
        return run("snapshot_cache_key", "4524", str(xmin), "2614930.78",
                   "36366396.85", "2615115.49", keyhash, labels)[1]["key"]
    assert key("36366142.52") == key("36366142.54")
    assert key("36366142.52") != key("36366143.60")
    assert key("36366142.52") != key("36366142.52", "FFFF0000")
    assert key("36366142.52") != key("36366142.52", "ABCD1234", "false")
    # 显式精度参数：同一函数既能保持米制默认，也能给度制范围用更高精度。
    def precise(xmin, decimals):
        return run("snapshot_cache_key", "4525", str(xmin), "23.3", "108.75", "23.55",
                   "ABCD1234", "false", str(decimals))[1]["key"]
    assert precise("108.4", "1") == precise("108.4001", "1")
    assert precise("108.4", "6") != precise("108.4001", "6")


def test_district_basemap_cache_key_keeps_degree_precision():
    def key(xmin, xmax, level="县乡", keywords=""):
        return run("basemap_district_cache_key", str(xmin), "23.3", str(xmax), "23.55",
                   level, keywords)[1]

    first = key(108.4, 108.75)
    # 区图范围是 WGS84 度，两位小数级差异必须换键（0.1°≈11 km 的旧行为会误命中旧范围）。
    assert first["decimals"] >= 5
    assert first["key"] != key(108.4001, 108.7501)["key"]
    # 默认 1 位小数精度会把这两个范围算成同一键——这正是 P1-3 的根因，用断言钉住修复方向。
    assert first["default_decimals"] == 1
    assert first["default_key"] == key(108.4001, 108.7501)["default_key"]
    # level/keywords 改变成果内容，必须进入缓存键。
    assert first["key"] != key(108.4, 108.75, "县")["key"]
    assert first["key"] != key(108.4, 108.75, "县乡", "上林县")["key"]
    assert key(108.4, 108.75)["key"] == key(108.4, 108.75)["key"]


def test_tool_profile_prunes_by_intent_and_expands_on_request():
    core = run("tool_profile", "列出图层")[1]["tools"]
    assert "list_layers" in core and "acceptance_preview" in core
    assert "buffer_features" not in core and "open_basemap_preview" not in core
    sampling = run("tool_profile", "按当前选择出图")[1]["tools"]
    assert "acceptance_preview" in sampling and "open_basemap_preview" in sampling
    assert "acceptance_snapshots" in run("tool_profile", "批量快照")[1]["tools"]
    analysis = run("tool_profile", "做缓冲分析并查字段")[1]["tools"]
    assert "buffer_features" in analysis and "spatial_join_features" in analysis
    full = run("tool_profile", "使用完整工具")[1]["tools"]
    assert len(full) >= 40 and "apply_symbology_from_layer" in full
    assert len(sampling) < len(full)


def test_acceptance_tools_require_confirm_for_preview_and_reject_unknown_fields():
    assert run("validate", "acceptance_check", '{"layer":"parcels"}')[0] == 0
    assert run("validate", "acceptance_preview", '{"layer":"parcels","confirm":true}')[0] == 0
    assert run("validate", "acceptance_preview", '{"layer":"parcels"}')[0] != 0
    assert run("validate", "acceptance_preview", '{"layer":"parcels","confirm":false}')[0] != 0
    assert run("validate", "acceptance_delivery", "{}")[0] == 0
    assert run("validate", "acceptance_delivery", '{"out_dir":"C:/work/out"}')[0] == 0
    assert run("validate", "acceptance_snapshots", '{"layer":"parcels","confirm":true}')[0] == 0
    assert run("validate", "acceptance_snapshots", '{"layer":"parcels"}')[0] != 0
    assert run("validate", "acceptance_check", '{"layer":"parcels","bogus":1}')[0] != 0


def test_tianditu_tile_type_sniffs_magic_not_content_type():
    code, result = run("tile_type")
    assert code == 0
    assert result == {"png": "image/png", "jpeg": "image/jpeg", "bad": None}


def test_tianditu_map_template_keeps_key_out_and_uses_xyz_placeholders():
    code, result = run("basemap_map_template", "tianditu_image")
    assert code == 0
    assert "TEST_TDT_KEY" not in json.dumps(result)
    assert result["template"].startswith("http://127.0.0.1:")
    assert "/xyz/img/" in result["template"] and "{z}/{y}/{x}" in result["template"]
    assert "/xyz/cia/" in result["labels"]
    terrain = run("basemap_map_template", "tianditu_terrain")[1]
    assert "TEST_TDT_KEY" not in json.dumps(terrain)
    assert "/xyz/ter/" in terrain["template"] and "/xyz/cta/" in terrain["labels"]
    assert run("basemap_map_missing", "tianditu_image")[0] != 0
    assert run("basemap_map_missing", "tianditu_terrain")[0] != 0
    assert run("basemap_map_missing", "amap_district")[0] != 0


def test_basemap_tool_schemas_cover_six_sources_and_reject_unknown():
    for source in sorted(BASEMAP_SOURCES):
        assert run("validate", "add_basemap", json.dumps({"source": source}))[0] == 0
    assert run("validate", "add_basemap", '{"source":"amap_district","mode":"live","labels":false}')[0] == 0
    assert run("validate", "add_basemap", '{"source":"amap_district","mode":"bad"}')[0] != 0
    for level in ("县", "乡", "县乡"):
        assert run("validate", "add_basemap",
                   json.dumps({"source": "amap_district", "level": level}))[0] == 0
    assert run("validate", "add_basemap", '{"source":"amap_district","level":"村"}')[0] != 0
    assert run("validate", "add_basemap", '{"source":"amap_district","keywords":"上林县"}')[0] == 0
    assert run("validate", "add_basemap", '{"source":"amap_district","keywords":""}')[0] != 0
    assert run("validate", "add_basemap",
               json.dumps({"source": "amap_district", "keywords": "x" * 101}))[0] != 0
    # schema 不按来源限制 level/keywords；非区图来源由 C# 运行时中文拒绝（见 add_basemap 分支）。
    assert run("validate", "add_basemap", '{"source":"tianditu_image","level":"乡"}')[0] == 0
    assert run("validate", "add_basemap", '{"source":"unknown"}')[0] != 0
    assert run("validate", "add_basemap", "{}")[0] != 0
    assert run("validate", "remove_basemap", "{}")[0] == 0
    assert run("validate", "remove_basemap", '{"layer":"区图-行政界线（高德参考）"}')[0] == 0
    assert run("validate", "calibrate_basemap_offset", "{}")[0] == 0
    assert run("validate", "calibrate_basemap_offset",
               '{"source":"amap_satellite","extent":[1,2,3,4]}')[0] == 0
    assert run("validate", "calibrate_basemap_offset", '{"source":"tianditu_image"}')[0] != 0
    assert run("validate", "calibrate_basemap_offset", '{"extent":[1,2,3]}')[0] != 0
    assert run("validate", "open_basemap_preview", '{"source":"tianditu_terrain","confirm":true}')[0] == 0
    assert run("validate", "open_basemap_preview", '{"source":"amap_district","confirm":true}')[0] != 0


def test_tianditu_map_layer_tools_validate_supported_sources():
    assert run("validate", "add_tianditu_basemap", '{"source":"tianditu_image"}')[0] == 0
    assert run("validate", "add_tianditu_basemap", '{"source":"tianditu_vector","labels":false}')[0] == 0
    assert run("validate", "add_tianditu_basemap", '{"source":"tianditu_image","mode":"raster"}')[0] == 0
    assert run("validate", "add_tianditu_basemap", '{"source":"tianditu_image","mode":"live"}')[0] == 0
    assert run("validate", "add_tianditu_basemap", '{"source":"tianditu_image","mode":"bad"}')[0] != 0
    assert run("validate", "add_tianditu_basemap", '{"source":"amap_vector"}')[0] != 0
    assert run("validate", "add_tianditu_basemap", "{}")[0] != 0
    assert run("validate", "remove_tianditu_basemap", "{}")[0] == 0


def test_tianditu_map_proxies_are_isolated_by_map():
    code, result = run("basemap_map_isolation")
    assert code == 0 and result == {"a": 200, "b": 200, "a_after_close_b": 200}


def test_catalog_validation_rejects_invalid_operations():
    for name, args in [
        ("execute_python", {}),
        ("remove_layer", {"layer": "a", "confirm": False}),
        ("get_layer_features", {"layer": "a", "limit": 0}),
        (
            "run_geoprocessing",
            {"tool": "management.Delete", "parameters": ["a"], "confirm": True},
        ),
        ("set_layer_visibility", {"layer": "a", "visible": "true"}),
        ("list_layers", {"code": "anything"}),
    ]:
        assert run("validate", name, json.dumps(args))[0] != 0
    assert run("validate", "list_layers", "{}")[0] == 0
    assert run("validate", "open_acceptance_workspace", '{"page":"sampling"}')[0] == 0
    assert run("validate", "open_acceptance_workspace", '{"page":"unknown"}')[0] != 0


def test_parcel_inspection_rejects_bad_fields_and_excessive_scan_bounds():
    assert run("validate", "inspect_parcels", json.dumps({"layer": "parcel", "id_field": "DKBH"}))[0] == 0
    for bad in [{"layer": "parcel", "id_field": ""},
                {"layer": "parcel", "id_field": "DKBH", "max_features": 10001},
                {"layer": "parcel", "id_field": "DKBH", "required_fields": "name"}]:
        assert run("validate", "inspect_parcels", json.dumps(bad))[0] != 0


@pytest.mark.parametrize("status,requested,project,expected", [
    ("queued", False, "D:/test.aprx", True),
    ("running", False, "d:/TEST.aprx", True),
    ("running", True, "D:/test.aprx", False),
    ("succeeded", False, "D:/test.aprx", False),
    ("failed", False, "D:/test.aprx", False),
    ("cancelled", False, "D:/test.aprx", False),
    ("running", False, "D:/other.aprx", False),
    ("running", False, "", False),
])
def test_cancel_action_only_for_pending_current_project_job(status, requested, project, expected):
    job = {"job_id": "selected", "project": "D:/test.aprx", "status": status, "cancel_requested": requested}
    code, result = run("job_cancel_available", json.dumps(job), project)
    assert code == 0, result
    assert result["available"] is expected


def test_cancel_action_requires_a_selected_job():
    code, result = run("job_cancel_available", "null", "D:/test.aprx")
    assert code == 0, result
    assert not result["available"]


def test_job_times_success_output_and_terminal_state():
    result = run("job", "analysis.Buffer", "D:/data.gdb/result", "succeeded")[1]
    queued, running, done, after = [result[k] for k in ["queued", "running", "completed", "after_cancel"]]
    assert queued["created_at"] and queued["started_at"] is None and queued["finished_at"] is None
    assert queued["output_path"] is None and not queued["can_load_output"]
    assert running["started_at"] and running["finished_at"] is None
    assert running["elapsed_ms"] >= 20
    assert done["finished_at"] and done["elapsed_ms"] >= running["elapsed_ms"]
    assert done["can_load_output"] and Path(result["output"]) == Path("D:/data.gdb/result")
    assert after["status"] == "succeeded" and after["cancel_requested"]
    assert after["finished_at"] == done["finished_at"] and after["elapsed_ms"] == done["elapsed_ms"]
    assert after["messages"] == "receipt"


def test_cancel_callback_can_complete_and_dispose_job_on_another_thread():
    code, result = run("job_cancel_callback", timeout=5)
    assert code == 0, result
    assert result["status"] == "cancelled" and result["cancel_requested"]


@pytest.mark.parametrize("tool,path,status", [
    ("management.GetCount", "D:/data.gdb/a", "succeeded"),
    ("analysis.Buffer", "D:/data.gdb/a", "failed"),
    ("analysis.Buffer", "D:/data.gdb/a", "cancelled"),
    ("conversion.FeaturesToJSON", "D:/result.json", "succeeded"),
    ("analysis.Buffer", "relative.shp", "succeeded"),
])
def test_job_load_rejects_non_feature_failed_and_cancelled(tool, path, status):
    value = run("job", tool, path, status)[1]
    assert value["output"] is None and value["error"]
    assert not value["completed"]["can_load_output"]


@pytest.mark.parametrize("tool",["analysis.Clip","analysis.SpatialJoin"])
def test_job_load_is_bound_to_original_project_and_output_index(tool):
    value = run("job", tool, "D:/data.gdb/cut", "succeeded", "D:/other.aprx")[1]
    assert value["output"] is None and "工程" in value["error"]
    assert Path(value["completed"]["output_path"]) == Path("D:/data.gdb/cut")


def test_job_records_actual_gp_output_after_automatic_shp_extension():
    result = run("job", "analysis.Buffer", "D:/outputs/result", "succeeded", "D:/test.aprx", "D:/outputs/result.shp")[1]
    assert result["completed"]["can_load_output"]
    assert Path(result["output"]) == Path("D:/outputs/result.shp")
    count = run("job", "management.GetCount", "D:/outputs/result", "succeeded", "D:/test.aprx", "D:/outputs/result.shp")[1]
    assert count["completed"]["output_path"] is None and not count["completed"]["can_load_output"]


@pytest.mark.parametrize("existing,output,matched", [
    ("D:/result", "D:/result.shp", True),
    ("d:/RESULT.SHP", "D:/result.shp", True),
    ("D:/a.gdb/result", "D:/a.gdb/result", True),
    ("D:/a.gdb/result", "D:/a.gdb/result.shp", False),
    ("D:/other/result", "D:/result.shp", False),
    ("D:/result", "D:/result.json", False),
    ("", "D:/result.shp", False),
])
def test_feature_source_identity_handles_sdk_shp_paths(existing, output, matched):
    assert run("source_match", existing, output)[1]["matched"] is matched


def test_load_job_tool_requires_confirmation():
    assert run("validate", "load_job_output", '{"job_id":"a","confirm":true}')[0] == 0
    assert run("validate", "load_job_output", '{"job_id":"a","confirm":false}')[0] != 0


def source(**kw):
    return dict(Path="D:/test.gdb/points", Geometry="Point", SpatialReference="WGS84", Geographic=True,
                Fields={"group_id": "String", "sample_id": "Integer", "Shape": "Geometry"}, **kw)


def analysis(name, args, data=None, clip=None, transformations=None):
    data = source() if data is None else data
    values = ["analysis", name, json.dumps(args), json.dumps(data)]
    if clip is not None:
        values.append(json.dumps(clip))
    if transformations is not None:
        if clip is None:
            values.append("null")
        values.append(json.dumps(transformations))
    return run(*values)


def test_projection_requires_valid_target_and_explicit_available_datum_transform():
    data = {**source(), "Datum": "WGS84"}
    target = {**source(), "SpatialReference": "WebMercator", "Datum": "WGS84", "Geographic": False}
    args = dict(output="D:/result.gdb/projected", target_wkid=3857)
    code, value = analysis("project_features", args, data, target, [])
    assert code == 0 and value["Tool"] == "management.Project"
    assert value["Parameters"][2:] == ["3857", "#"]
    other = {**target, "Datum": "other"}
    assert analysis("project_features", args, data, other, ["fixture_transform"])[0] != 0
    args["transformation"] = "fixture_transform"
    assert analysis("project_features", args, data, other, ["fixture_transform"])[1]["Parameters"][3] == "fixture_transform"
    assert analysis("project_features", args, data, other, ["different_transform"])[0] != 0
    assert analysis("project_features", {**args,"target_wkid":0}, data, target, ["fixture_transform"])[0] != 0


def test_spatial_join_has_explicit_matching_and_output_positions():
    join = {**source(), "Path": "D:/test.gdb/zones", "Geometry": "Polygon"}
    args = dict(output="D:/result.gdb/joined")
    code, value = analysis("spatial_join_features", args, clip=join)
    assert code == 0 and value["Tool"] == "analysis.SpatialJoin"
    assert Path(value["Parameters"][1]) == Path(join["Path"])
    assert Path(value["Parameters"][2]) == Path(args["output"])
    assert value["Parameters"][3:] == ["JOIN_ONE_TO_MANY", "KEEP_ALL", "#", "INTERSECT"]
    args.update(join_operation="ONE_TO_ONE", keep_all=False, match="WITHIN")
    assert analysis("spatial_join_features", args, clip=join)[1]["Parameters"][3:] == ["JOIN_ONE_TO_ONE", "KEEP_COMMON", "#", "WITHIN"]
    assert analysis("spatial_join_features", args, clip={**join,"SpatialReference":"other"})[0] != 0
    assert analysis("spatial_join_features", {**args,"match":"CLOSEST"}, clip=join)[0] != 0


def test_named_analysis_units_geometry_fields_and_crs():
    args = dict(output="D:/result.gdb/buffer", distance=12.5, unit="meters")
    code, result = analysis("buffer_features", args)
    assert code == 0
    assert result["Tool"] == "analysis.Buffer"
    assert Path(result["Parameters"][0]) == Path("D:/test.gdb/points")
    assert Path(result["Parameters"][1]) == Path(args["output"])
    assert result["Parameters"][2:] == ["12.5 Meters", "FULL", "ROUND", "NONE", "#", "GEODESIC"]
    for change in [dict(distance=0), dict(distance=-1), dict(unit="degrees"), dict(method="PLANAR")]:
        assert analysis("buffer_features", {**args, **change})[0] != 0
    assert analysis("buffer_features", args, {**source(), "SpatialReference": ""})[0] != 0
    assert analysis("buffer_features", {**args, "distance": -5}, {**source(), "Geometry": "Polygon"})[0] == 0
    polygon = {**source(), "Geometry": "Polygon", "Path": "D:/test.gdb/clip"}
    assert analysis("clip_features", dict(output="D:/test.gdb/cut"), clip=polygon)[0] == 0
    assert analysis("clip_features", dict(output="D:/test.gdb/cut"), clip=source())[0] != 0
    assert analysis("clip_features", dict(output="D:/test.gdb/cut"), clip={**polygon, "SpatialReference": "other"})[0] != 0
    dissolve = dict(output="D:/test.gdb/merged", fields=["GROUP_ID"], multipart=False)
    result = analysis("dissolve_features", dissolve)[1]
    assert result["Parameters"][2:5] == ["group_id", "#", "SINGLE_PART"]
    for fields in [["missing"], ["Shape"], ["group_id", "GROUP_ID"]]:
        assert analysis("dissolve_features", {**dissolve, "fields": fields})[0] != 0
    assert analysis("buffer_features", {**args, "output": "relative.shp"})[0] != 0


def test_map_identity_and_sibling_move_index():
    assert run("map", "map-one", "map-two")[0] != 0
    assert run("map", "map-one", "map-one")[0] == 0
    assert run("order", "a,b,c,d", "a", "c", "after")[1]["index"] == 2
    assert run("order", "a,b,c,d", "d", "b", "before")[1]["index"] == 1
    assert run("order", "a,b,c", "a", "a", "before")[0] != 0


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?key=a",
        "file:///a",
    ],
)
def test_endpoint_rejects_unsafe_or_misconfigured_url(url):
    assert run("endpoint", url)[0] != 0


def test_endpoint_preserves_provider_path_and_local_http():
    assert (
        run("endpoint", "https://example.com/compatible-mode/v1/")[1]["url"]
        == "https://example.com/compatible-mode/v1/chat/completions"
    )
    assert run("endpoint", "http://127.0.0.1:11434/v1")[0] == 0


def test_key_is_encrypted_and_roundtrips(tmp_path):
    target = tmp_path / "settings.json"
    result = run("settings", str(target))[1]
    assert result["roundtrip"] and result["timeout"] == 17
    assert "secret-test" not in target.read_text(encoding="utf-8")


def test_legacy_settings_keep_default_timeout(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(dict(baseUrl="http://127.0.0.1/v1", model="test", protectedKey="")), encoding="utf-8")
    assert run("load_settings", str(path))[1] == {"timeout":120,"models_url":"http://127.0.0.1/v1/models"}


def test_project_binding_rejects_stale_or_empty_current_project():
    assert run("project", "D:/one.aprx", "D:/two.aprx")[0] != 0
    assert run("project", "D:/one.aprx", "")[0] != 0
    assert run("project", "D:/one.aprx", "d:/ONE.aprx")[0] == 0


def test_gp_parameters_resolve_layer_inputs_and_reject_relative_outputs():
    code, value = run(
        "gpparams",
        "analysis.Clip",
        json.dumps(["test_layer", "test_layer", "D:/out.gdb/clipped"]),
    )
    assert code == 0 and value[:2] == ["D:/test.gdb/points"] * 2
    assert (
        run(
            "gpparams",
            "management.CopyFeatures",
            json.dumps(["test_layer", "relative_output"]),
        )[0]
        != 0
    )
    assert (
        run(
            "gpparams",
            "analysis.Buffer",
            json.dumps(["test_layer", "D:/out.gdb/buffer"]),
        )[0]
        != 0
    )


def test_only_existing_exported_documents_enable_open_button(tmp_path):
    image = tmp_path / "map.png"
    image.write_bytes(b"fixture")
    executable = tmp_path / "bad.exe"
    executable.write_bytes(b"fixture")
    assert run("output", "export_map", str(image))[1]["accepted"]
    assert not run("output", "get_layer_features", str(image))[1]["accepted"]
    assert not run("output", "export_map", str(executable))[1]["accepted"]
    assert not run("output", "export_map", str(tmp_path / "missing.png"))[1]["accepted"]


def test_job_polling_does_not_overlap_or_repeat_unchanged_results():
    code, value = run("monitor")
    assert code == 0
    assert value["fetches_during_overlap"] == 1 and not value["overlapped"]
    assert value["initial"] and not value["repeated"] and value["completed"]
    assert value["jobs"][0]["status"] == "succeeded"


def test_pending_job_refresh_cannot_restore_previous_project_tasks():
    assert run("monitor", "reset")[1]["count"] == 0


def test_background_mcp_export_becomes_available_even_without_new_jobs(tmp_path):
    image = tmp_path / "external.png"
    image.write_bytes(b"fixture")
    assert run("monitor_output", str(image))[1]["path"] == str(image)


def test_diagnostics_distinguish_saved_configuration_from_connection(tmp_path):
    profile = tmp_path / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text(
        "- insert:\n  - id: pro-ai-bridge-mcp\n    key: secret-diagnostic-test\n",
        encoding="utf-8",
    )
    code, value = run("diagnostics", str(tmp_path))
    assert code == 0 and value["pro_bridge"] == "ready"
    assert (
        value["dsh_configuration"] == "saved"
        and value["dsh_connection"] == "not_verified"
    )
    assert "secret-diagnostic-test" not in json.dumps(value)


@pytest.mark.parametrize(
    "text", ["# id: pro-ai-bridge-mcp\n", "- id: pro-ai-bridge-mcp-old\n"]
)
def test_native_diagnostics_require_exact_noncomment_plugin_id(tmp_path, text):
    profile = tmp_path / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text(text, encoding="utf-8")
    assert run("diagnostics", str(tmp_path))[1]["dsh_configuration"] == "missing"


@pytest.fixture
def api():
    requests = []
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path":self.path,"auth":self.headers.get("Authorization"),"method":"GET"})
            self.reply()

        def do_POST(self):
            requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            self.reply()

        def reply(self):
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
            time.sleep(reply.get("_delay", 0))
            data = json.dumps(reply.get("_payload", reply)).encode()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(reply.get("_status", 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    server.shutdown()
    server.server_close()


@pytest.fixture
def sse_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("连接", "成功"):
                chunk = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": piece},
                                      "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + chr(10) + chr(10)).encode("utf-8"))
                self.wfile.flush()
            done = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(("data: " + json.dumps(done) + chr(10) + chr(10) +
                              "data: [DONE]" + chr(10) + chr(10)).encode("utf-8"))
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()


@pytest.fixture
def stalled_sse_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"wait"}}]}\n\n')
            self.wfile.flush()
            time.sleep(3)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()


def completion(content=None, tool=None, arguments=None, ident="call_1"):
    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [
            {
                "id": ident,
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments or {})},
            }
        ]
    return {
        "choices": [
            {"message": message, "finish_reason": "tool_calls" if tool else "stop"}
        ]
    }


def test_visual_review_sends_resized_real_image_and_binds_original_hash(api, tmp_path):
    import base64
    import hashlib
    from io import BytesIO
    from PIL import Image
    image = tmp_path / '采点图.png'
    Image.new('RGB', (3200, 1200), '#126834').save(image)
    url, replies, requests = api
    verdict = {'status': 'PASS', 'findings': [{'item': '版面', 'status': 'PASS', 'detail': '地块边界、点号和底部图例清楚且无压盖'}]}
    replies.append(completion(json.dumps(verdict, ensure_ascii=False)))
    code, result = run('review_map', url, str(image))
    assert code == 0, result
    assert result['status'] == 'PASS' and result['findings'] == verdict['findings']
    assert result['image_sha256'] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert result['model'] == 'vision-test' and result['reviewer']['kind'] == 'vision_model'
    assert result['reviewed_at'] and result['image_input_verified'] is True
    request = requests[0]
    assert request['auth'] == 'Bearer secret-test' and request['path'] == '/v1/chat/completions'
    body = request['body']
    assert 'tools' not in body and body['stream'] is False
    content = body['messages'][-1]['content']
    assert content[0]['type'] == 'text' and content[1]['type'] == 'image_url'
    uri = content[1]['image_url']['url']
    assert uri.startswith('data:image/jpeg;base64,')
    blob = base64.b64decode(uri.split(',', 1)[1])
    with Image.open(BytesIO(blob)) as preview:
        assert preview.size == (1600, 600)
    assert result['preview_sha256'] == hashlib.sha256(blob).hexdigest()
    assert result['preview_width'] == 1600 and result['preview_height'] == 600


@pytest.mark.parametrize('verdict', [
    '看起来没有问题，通过',
    {'status': 'PASS'},
    {'status': 'PASS', 'findings': []},
    {'status': 'OK', 'findings': [{'item': '版面', 'status': 'PASS', 'detail': '清楚'}]},
    {'status': 'PASS', 'findings': [{'item': '版面', 'status': 'FAIL', 'detail': '标题压盖'}]},
    {'status': 'FAIL', 'findings': [{'item': '版面', 'status': 'PASS', 'detail': '清楚'}]},
    {'status': 'PASS', 'findings': [{'item': '版面', 'status': 'PASS', 'detail': ''}]},
])
def test_visual_review_rejects_text_success_and_incomplete_or_conflicting_verdicts(api, tmp_path, verdict):
    from PIL import Image
    image = tmp_path / 'map.png'
    Image.new('RGB', (100, 80), 'white').save(image)
    url, replies, requests = api
    replies.append(completion(verdict if isinstance(verdict, str) else json.dumps(verdict, ensure_ascii=False)))
    code, result = run('review_map', url, str(image))
    assert code != 0 and result['type'] == 'InvalidOperationException', result
    assert len(requests) == 1 and 'status' not in result


@pytest.mark.parametrize('alteration', ['length', 'tool_call', 'service_error'])
def test_visual_review_refuses_truncation_tools_and_service_failures(api, tmp_path, alteration):
    from PIL import Image
    image = tmp_path / 'map.png'
    Image.new('RGB', (100, 80), 'white').save(image)
    url, replies, requests = api
    value = completion(json.dumps({'status': 'PASS', 'findings': [{'item': '版面', 'status': 'PASS', 'detail': '清楚'}]}))
    if alteration == 'length': value['choices'][0]['finish_reason'] = 'length'
    elif alteration == 'tool_call': value = completion(tool='list_layers')
    else: value = {'_status': 400, '_payload': {'error': {'message': 'image input unsupported'}}}
    replies.append(value)
    code, result = run('review_map', url, str(image))
    assert code != 0 and result['type'] == 'InvalidOperationException', result
    assert len(requests) == 1


def test_visual_review_rejects_oversized_or_invalid_images_before_network(api, tmp_path):
    url, replies, requests = api
    replies.append(completion('unused'))
    for name, blob in [('large.png', b'x' * (8 * 1024 * 1024 + 1)), ('bad.png', b'not an image')]:
        image = tmp_path / name
        image.write_bytes(blob)
        code, result = run('review_map', url, str(image))
        assert code != 0 and result['type'] in ('ArgumentException', 'InvalidOperationException'), result
    assert requests == []


@pytest.mark.parametrize('level', ['FAIL', 'NEEDS_REVIEW'])
def test_visual_review_retains_real_failed_or_uncertain_observations(api, tmp_path, level):
    from PIL import Image
    image = tmp_path / 'map.png'
    Image.new('RGB', (80, 160), 'white').save(image)
    url, replies, _ = api
    findings = [{'item': '点号', 'status': level, 'detail': '图中字迹不足以确认全部点号'}]
    replies.append(completion(json.dumps({'status': level, 'findings': findings}, ensure_ascii=False)))
    code, result = run('review_map', url, str(image))
    assert code == 0 and result['status'] == level and result['findings'] == findings
    assert (result['preview_width'], result['preview_height']) == (80, 160)


def test_visual_review_invalidates_verdict_if_original_changes_during_request(api, tmp_path):
    from PIL import Image
    image = tmp_path / 'map.png'
    Image.new('RGB', (100, 80), 'white').save(image)
    url, replies, requests = api
    verdict = completion(json.dumps({'status': 'PASS', 'findings': [{'item': '图例', 'status': 'PASS', 'detail': '清楚'}]}))
    verdict['_delay'] = 1
    replies.append(verdict)
    result = []
    worker = threading.Thread(target=lambda: result.append(run('review_map', url, str(image))))
    worker.start()
    deadline = time.monotonic() + 8
    while not requests and time.monotonic() < deadline:
        time.sleep(.01)
    assert requests, 'review HTTP request did not arrive'
    Image.new('RGB', (100, 80), 'black').save(image)
    worker.join(timeout=8)
    assert not worker.is_alive() and result
    assert result[0][0] != 0 and '原图已变更' in result[0][1]['error']


def test_visual_review_cancel_is_never_recorded_as_success(api, tmp_path):
    from PIL import Image
    image = tmp_path / 'map.png'
    Image.new('RGB', (100, 80), 'white').save(image)
    url, replies, _ = api
    reply = completion(json.dumps({'status': 'PASS', 'findings': [{'item': '图例', 'status': 'PASS', 'detail': '清楚'}]}))
    reply['_delay'] = .5
    replies.append(reply)
    code, result = run('review_map', url, str(image), 'cancel')
    assert code != 0 and result['type'] in ('TaskCanceledException', 'OperationCanceledException')


def test_saved_visual_review_writes_bound_receipt_without_printing_credentials(api, tmp_path):
    import hashlib
    from PIL import Image
    image = tmp_path / 'map.jpg'
    Image.new('RGB', (100, 80), 'white').save(image)
    url, replies, requests = api
    profile = tmp_path / 'settings.json'
    profile.write_text(json.dumps({'baseUrl': url, 'model': 'saved-vision', 'protectedKey': ''}), encoding='utf-8')
    context = tmp_path / 'context.txt'
    context.write_text('预期地块39，1个设计点，比例尺1:1000。', encoding='utf-8')
    receipt = Path(str(image) + '.visual-review.json')
    replies.append(completion(json.dumps({'status': 'PASS', 'findings': [{'item': '点数', 'status': 'PASS', 'detail': '观察到1个设计点'}]}, ensure_ascii=False)))
    code, result = run('review_saved', str(image), 'bound-render-key', str(receipt), str(context), str(profile))
    assert code == 0 and result['status'] == 'PASS', result
    proof = json.loads(receipt.read_text(encoding='utf-8'))
    assert proof['image_sha256'] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert proof['render_key'] == 'bound-render-key' and proof['model'] == 'saved-vision'
    assert requests[0]['body']['model'] == 'saved-vision'
    assert 'protectedKey' not in json.dumps(result) and 'baseUrl' not in json.dumps(result)


def test_model_roundtrip_executes_tool_then_returns_answer(api):
    url, replies, requests = api
    replies += [completion(tool="list_layers"), completion("共有一个测试地块图层")]
    code, result = run("model", url)
    assert code == 0 and result["executed"] == ["list_layers"]
    assert result["text"] == "共有一个测试地块图层"
    assert requests[0]["path"] == "/v1/chat/completions"
    assert requests[0]["auth"] == "Bearer secret-test"
    assert requests[1]["body"]["messages"][-1]["role"] == "tool"
    assert [r["phase"] for r in result["receipts"]] == ["running", "completed"]
    assert json.loads(result["receipts"][0]["detail"]) == {}
    assert json.loads(result["receipts"][1]["detail"])["layers"] == ["测试地块"]


def test_model_system_prompt_advances_with_defaults_without_weakening_gis_safety(api):
    url, replies, requests = api
    replies += [completion("已完成")]
    code, result = run("model", url)
    assert code == 0, result

    instruction = requests[0]["body"]["messages"][0]["content"]
    assert "不要每次列出多个方案让用户选择" in instruction
    assert "默认采用合理的低风险方案并简要说明" in instruction
    assert "给出一个推荐方案并继续" in instruction
    assert "只有缺少会改变GIS结果的关键信息" in instruction
    assert "才提一个简短问题或走现有人类确认" in instruction
    assert "删除图层、保存、导出和地理处理需用户确认" in instruction
    assert "不要用模型生成的confirm代替人类确认" in instruction


@pytest.mark.parametrize("reply", [[], "unexpected", {"choices":[]}, {"choices":{}}, {"choices":[{}]}, {"choices":[{"message":"invalid"}]}])
def test_malformed_model_envelope_is_readable_and_never_executes_tools(api, reply):
    url, replies, requests = api
    replies += [{"_payload":reply}]
    code, result = run("model", url)
    assert code != 0 and result["type"] == "InvalidOperationException"
    assert "模型响应" in result["error"] and "choices" in result["error"]
    assert len(requests) == 1


def test_invalid_tool_calls_type_is_not_accepted_as_a_normal_answer(api):
    url, replies, requests = api
    replies += [completion("looks successful")]
    replies[0]["choices"][0]["message"]["tool_calls"] = {"id":"bad"}
    code, result = run("model", url)
    assert code != 0 and "tool_calls" in result["error"]
    assert len(requests) == 1


@pytest.mark.parametrize("bad_call", [42, {"id":1,"function":{"name":"list_layers"}}, {"id":"second","function":[]}, {"id":"second","function":{"name":"list_layers","arguments":{}}}, {"id":"second","type":"custom","function":{"name":"list_layers"}}])
def test_bad_call_shape_rejects_whole_batch_before_any_execution(api, bad_call):
    url, replies, requests = api
    response = completion(tool="clear_selection")
    response["choices"][0]["message"]["tool_calls"].append(bad_call)
    replies += [response, completion("done")]
    code, result = run("model",url)
    assert code != 0 and result["type"] == "InvalidOperationException"
    assert result["executed"] == [] and result["confirmations"] == []
    assert "工具调用" in result["error"] and len(requests)==1


def test_compatible_provider_can_omit_optional_call_type_and_arguments(api):
    url, replies, requests = api
    response=completion(tool="list_layers")
    call=response["choices"][0]["message"]["tool_calls"][0]
    call.pop("type")
    call["function"].pop("arguments")
    replies += [response,completion("done")]
    code,result=run("model",url)
    assert code==0 and result["executed"]==["list_layers"] and len(requests)==2


@pytest.mark.parametrize("reply", [[], "unsupported"])
def test_malformed_catalog_envelope_has_actionable_error(api, reply):
    url, replies, _requests = api
    replies += [{"_payload":reply}]
    code, result = run("models", url)
    assert code != 0 and result["type"] == "InvalidOperationException"
    assert "模型清单" in result["error"] and "手工" in result["error"]


def test_cancel_during_confirmation_never_enters_tool_executor(api):
    url, replies, requests = api
    replies += [completion(tool="remove_layer", arguments={"layer":"a","confirm":True}), completion("done")]
    code, result = run("cancel_boundary", url, "confirm")
    assert code == 0 and result == {"cancelled":True,"executed":0}
    assert len(requests) == 1


def test_cancel_when_final_response_arrives_never_reports_success(api):
    url, replies, requests = api
    replies += [{**completion("done"),"usage":{"total_tokens":1}}]
    code, result = run("cancel_boundary", url, "usage")
    assert code == 0 and result == {"cancelled":True,"executed":0}
    assert len(requests) == 1


def test_model_catalog_works_without_model_and_normalizes_full_endpoint(api):
    url, replies, requests = api
    replies += [{"data":[{"id":"z-model"},{"id":"a-model"},{"id":"z-model"},{"id":""},{}]}]
    assert run("models", url + "/chat/completions")[1] == ["a-model", "z-model"]
    assert requests[0] == {"path":"/v1/models","auth":"Bearer secret-test","method":"GET"}


def test_model_catalog_reports_unsupported_response_and_cancels(api):
    url, replies, _requests = api
    replies += [{"unsupported":True}]
    assert run("models", url)[0] != 0
    replies[:] = [{"data":[{"id":"late"}],"_delay":1}]
    code, result = run("models", url, "cancel")
    assert code != 0 and result["type"] in ["TaskCanceledException","OperationCanceledException"]


@pytest.mark.parametrize("seconds,valid",[(4,False),(5,True),(600,True),(601,False)])
def test_configurable_timeout_is_bounded(seconds, valid):
    assert (run("timeout_setting",str(seconds))[0] == 0) is valid


def test_model_timeout_is_distinct_from_user_cancel_and_never_retries(api):
    url, replies, requests = api
    replies += [{**completion("late"),"_delay":5.4}]
    code, result = run("model",url,"allow","short_timeout",timeout=10)
    assert code != 0 and result["type"] == "TimeoutException"
    assert "5" in result["error"] and len(requests) == 1


def test_model_usage_and_actionable_rate_limit_errors(api):
    url, replies, _requests = api
    replies += [{**completion("ok"),"usage":{"total_tokens":123}}]
    assert any("123" in s and "tokens" in s for s in run("model",url)[1]["traces"])
    replies[:] = [{"_status":429,"error":{"message":"secret-test rate limit"}}]
    code,result = run("model",url)
    assert code != 0 and "429" in result["error"] and "限流" in result["error"]
    assert "secret-test" not in result["error"]


def test_model_cannot_self_authorize_destructive_tool(api):
    url, replies, requests = api
    replies += [
        completion(tool="remove_layer", arguments={"layer": "test", "confirm": True}),
        completion("已取消"),
    ]
    code, result = run("model", url, "deny")
    assert code == 0 and result["executed"] == []
    assert result["confirmations"] == ["remove_layer"]
    assert "取消" in requests[1]["body"]["messages"][-1]["content"]


def test_unknown_model_tool_is_returned_as_error(api):
    url, replies, requests = api
    replies += [completion(tool="execute_python"), completion("不支持该操作")]
    code, result = run("model", url)
    assert code == 0 and not result["executed"]
    assert "error" in requests[1]["body"]["messages"][-1]["content"]


def test_model_loop_and_empty_response_are_bounded(api):
    url, replies, requests = api
    replies += [completion(tool="list_layers")]
    code, result = run("model", url, timeout=30)
    assert code == 0 and len(requests) == 100, result
    assert "100" in result["text"] and "继续" in result["text"]
    replies[:] = [completion("")]
    code, result = run("model", url)
    assert code != 0 and "空" in result["error"]


def test_model_streams_sse_content(sse_api):
    url, requests = sse_api
    code, result = run("model", url)
    assert code == 0 and result["text"] == "连接成功"
    assert requests[0]["stream"] is True


def test_short_followup_keeps_result_loading_tools_available():
    tools = run("tool_profile", "继续")[1]["tools"]
    assert "load_job_output" in tools


def test_followup_tool_scope_accumulates_until_clear():
    tools = run("tool_scope", "缓冲分析", "继续")[1]["tools"]
    assert "buffer_features" in tools and "load_job_output" in tools


@pytest.mark.parametrize("mode,retained", [("continue", True), ("clear", False), ("switch", False)])
def test_tool_scope_persists_for_followup_and_resets_after_clear_or_model_switch(api, mode, retained):
    url, replies, requests = api
    replies += [completion("分析完成"), completion("继续完成")]
    code, result = run("scope_twice", url, mode)
    assert code == 0, result
    first = {tool["function"]["name"] for tool in requests[0]["body"]["tools"]}
    second = {tool["function"]["name"] for tool in requests[1]["body"]["tools"]}
    assert "buffer_features" in first
    assert ("buffer_features" in second) is retained
    assert "load_job_output" in second


def test_cancelled_tool_history_is_completed_without_overwriting_finished_receipt():
    messages = [
        {"role":"assistant","tool_calls":[{"id":"call_0","type":"function","function":{"name":"buffer_features","arguments":"{}"}}]},
        {"role":"tool","tool_call_id":"call_0","content":"{\"ok\":true}"},
        {"role":"assistant","tool_calls":[{"id":"call_0","type":"function","function":{"name":"load_job_output","arguments":"{}"}}]},
    ]
    code, result = run("complete_pending_tools", json.dumps(messages))
    assert code == 0
    rows = result if isinstance(result, list) else json.loads(result)
    assert [row.get("tool_call_id") for row in rows if row.get("role") == "tool"] == ["call_0", "call_0"]
    assert rows[-1]["tool_call_id"] == "call_0" and "已停止" in rows[-1]["content"]


def test_model_batch_above_limit_executes_first_sixteen_and_reports_the_rest(api):
    url, replies, requests = api
    calls = [{"id": "call_%02d" % index, "type": "function",
              "function": {"name": "list_layers", "arguments": "{}"}} for index in range(17)]
    replies += [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        completion("已处理"),
    ]
    code, result = run("model", url)
    assert code == 0 and len(result["executed"]) == 16
    tool_messages = [row for row in requests[1]["body"]["messages"] if row["role"] == "tool"]
    assert len(tool_messages) == 17
    assert "超过16" in json.loads(tool_messages[-1]["content"])["error"]


def test_model_round_limit_keeps_history_for_the_next_prompt(api):
    url, replies, requests = api
    replies += [completion(tool="list_layers")]
    code, result = run("twice", url, "keep", timeout=30)
    assert code == 0
    assert len(requests) == 200
    second = json.dumps(requests[100]["body"]["messages"], ensure_ascii=False)
    assert "第一条私有会话" in second


def test_model_history_is_trimmed_instead_of_overflowing(api):
    url, replies, requests = api
    replies += [completion(tool="list_layers")]
    code, result = run("model", url, "allow", "huge", timeout=30)
    assert code == 0
    assert len(requests) == 100
    body = requests[-1]["body"]
    assert len(json.dumps(body, ensure_ascii=False)) < 180000
    assert any(row["role"] == "user" and "列出图层" in row["content"] for row in body["messages"])
    assert any(json.loads(row["content"]).get("truncated") == True
               for row in body["messages"] if row["role"] == "tool")


@pytest.mark.parametrize("mode", ["switch", "clear"])
def test_model_history_cannot_cross_provider_or_reappear_after_clear(api, mode):
    url, replies, requests = api
    replies += [completion("第一条回答"), completion("第二条回答")]
    code, result = run("twice", url, mode)
    assert code == 0, result
    assert "第一条私有会话" not in json.dumps(
        requests[1]["body"]["messages"], ensure_ascii=False
    )


def test_connection_test_does_not_offer_or_execute_tools(api):
    url, replies, requests = api
    replies += [
        completion(
            tool="set_layer_visibility", arguments={"layer": "test", "visible": False}
        )
    ]
    code, result = run("connection", url)
    assert code != 0 and "工具" in result["error"]
    assert not requests[0]["body"].get("tools")


def test_model_retries_transient_429_then_succeeds(api):
    url, replies, requests = api
    replies += [{"_status": 429, "error": {"message": "rate limit"}}, completion("恢复")]
    code, result = run("model", url)
    assert code == 0 and "恢复" in result["text"]
    assert len(requests) == 2


def test_model_http_failure_redacts_provider_key(api):
    url, replies, requests = api
    replies += [{"_status": 401, "error": {"message": "secret-test invalid"}}]
    code, result = run("model", url)
    assert code != 0 and "401" in result["error"]
    assert "secret-test" not in result["error"] and len(requests) == 1


def test_model_cancellation_interrupts_pending_http_request(api):
    url, replies, _requests = api
    delayed = completion("不应等待此回答")
    delayed["_delay"] = 2
    replies += [delayed]
    started = time.monotonic()
    code, result = run("model", url, "allow", "cancel")
    assert code != 0 and result["type"] in {
        "TaskCanceledException",
        "OperationCanceledException",
    }
    assert time.monotonic() - started < 1.5


def test_sse_cancellation_interrupts_established_idle_stream(stalled_sse_api):
    url, _requests = stalled_sse_api
    started = time.monotonic()
    code, result = run("model", url, "allow", "cancel", timeout=3)
    assert code != 0 and result["type"] in {"TaskCanceledException", "OperationCanceledException"}
    assert time.monotonic() - started < 1.5
