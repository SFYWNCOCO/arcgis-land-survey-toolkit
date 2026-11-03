"""Opt-in smoke against the real Pro process and a disposable synthetic project."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("PRO_AI_LIVE") != "1",
    reason="Requires installed add-in and disposable Pro project",
)


def test_real_workbench_rejects_parallel_gis_and_stops_before_state_exists():
    source = os.environ.get('WORKBENCH_SAMPLE')
    if not source:
        pytest.skip('Requires synthetic projected parcel fixture')
    spec = importlib.util.spec_from_file_location('workbench_host_proxy', ROOT / 'mcp_server.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    target = Path(os.environ['PRO_AI_TEST_DIR']).parent
    suffix = str(time.time_ns())
    check = module.exchange('acceptance_check', {'layer': source, 'out_dir': str(target / ('host-check-' + suffix))})
    assert check['ok'], check
    rejected = module.exchange('run_geoprocessing', {'tool': 'management.GetCount', 'parameters': [source], 'confirm': True})
    assert not rejected['ok'] and ('任务' in rejected['error'] or '操作' in rejected['error'])
    assert rejected['diagnostic']['code'] == 'JOB_BUSY'
    assert module.exchange('list_layers', {})['ok']
    def wait(job):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            reply = module.exchange('get_job', {'job_id': job['job_id']})
            assert reply['ok'], reply
            if reply['result']['status'] not in ('queued', 'running'):
                return reply['result']
            time.sleep(.3)
        raise AssertionError('Host task did not reach a terminal state')
    assert wait(check['result'])['status'] == 'succeeded'
    out = target / ('host-safe-stop-' + suffix)
    started = module.exchange('acceptance_preview', {'layer': source, 'out_dir': str(out), 'confirm': True})
    assert started['ok'], started
    state_existed = (out / 'task_state.json').exists()
    stop = module.exchange('cancel_job', {'job_id': started['result']['job_id']})
    assert stop['ok'] and stop['result']['cancel_requested']
    finished = wait(started['result'])
    assert finished['status'] == 'cancelled', finished
    saved = json.loads((out / 'task_state.json').read_text(encoding='utf-8'))
    assert saved['status'] == 'CANCELLED' and saved['run_id']
    assert not (out / 'cancel.request').exists()
    (target / 'host-workbench-receipt.json').write_text(json.dumps({
        'parallel_request': rejected, 'state_existed_when_stopped': state_existed,
        'job': finished, 'state': saved,
        'workspace': module.exchange('get_status', {})['result']['workspace'],
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def blue_clusters(path):
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    occupied_rows = []
    for y in range(image.height):
        if any(
            a > 128 and b > 80 and b > r * 1.2
            for r, g, b, a in image.crop((0, y, image.width, y + 1)).getdata()
        ):
            occupied_rows.append(y)
    return sum(
        i == 0 or row > occupied_rows[i - 1] + 1 for i, row in enumerate(occupied_rows)
    )


def test_real_basemap_catalog_and_unconfigured_preview():
    spec = importlib.util.spec_from_file_location("basemap_proxy", ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    status = module.exchange("get_status", {})
    import xml.etree.ElementTree as ET
    config = ROOT.parent / 'addin/AcceptanceAddin/Config.daml'
    expected_version = next(n.get('version') for n in ET.parse(config).getroot().iter() if n.tag.split('}')[-1] == 'AddInInfo')
    assert status['ok'] and status['result']['version'] == expected_version
    assert status['result']['unified_acceptance'] is True
    assert Path(status['result']['project']).resolve() == Path(os.environ['PRO_AI_TEST_DIR']) / 'BridgeSmoke.aprx'
    assert module.select_session(status['result']['process_id'])['selected']
    result = module.exchange('list_basemap_sources', {})
    assert result['ok'] and len(result['result']['sources']) == 4
    row = next(s for s in result['result']['sources'] if s['id'] == 'amap_satellite')
    assert row['coordinate_system'] == 'GCJ-02'
    if row['status'] == 'NEEDS_CONFIGURATION':
        preview = module.exchange('open_basemap_preview', {'source':'amap_satellite','confirm':True})
        assert not preview['ok'] and '密钥' in preview['error']
    assert not {'AmapKey','AmapSecurityCode','TiandituKey'} & set(result['result'])
    (Path(os.environ['PRO_AI_TEST_DIR']) / 'basemap-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


def test_real_unified_workspace_and_both_native_bridges():
    spec = importlib.util.spec_from_file_location('workspace_proxy', ROOT/'mcp_server.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    status = module.exchange('get_status', {})
    assert status['ok'] and status['result']['unified_acceptance']
    assert Path(status['result']['project']).resolve() == Path(os.environ['PRO_AI_TEST_DIR'])/'BridgeSmoke.aprx'
    assert module.select_session(status['result']['process_id'])['selected']
    source = str(Path(os.environ['PRO_AI_TEST_DIR'])/'smoke.gdb/join_zones')
    added = module.exchange('add_layer', {'path':source})
    assert added['ok'], added
    snapshots = []
    try:
        for page in ('sampling', 'assistant', 'sampling', 'assistant'):
            result = module.exchange('open_acceptance_workspace', {'page':page})
            assert result['ok'] and result['result']['page'] == page, result
            current = module.exchange('get_status', {})['result']['workspace']
            assert current['page'] == page
            assert current['requested_page'] == page
            assert current['assistant_draft_chars'] == 0
            assert current['sampling_project_name_chars'] == 0
            snapshots.append(current)
        assert snapshots[-1]['sampling_created'] and snapshots[-1]['assistant_created']
        assert module.exchange('open_assistant', {})['ok']
        sys.path.insert(0, str(ROOT.parent/'arcgis_mcp'))
        import live_server
        sampling_status = live_server.live_status()
        assert sampling_status.get('status') == 'PASS', sampling_status
        assert sampling_status['process_id'] == status['result']['process_id']
        assert sampling_status['map_uri'] == status['result']['map_uri']
    finally:
        assert module.exchange('remove_layer', {'layer':added['result']['uri'],'confirm':True})['ok']
    (Path(os.environ['PRO_AI_TEST_DIR'])/'workspace-receipt.json').write_text(
        json.dumps({'workspace':snapshots,'sampling_bridge':sampling_status},ensure_ascii=False,indent=2),encoding='utf-8')


def test_real_parcel_inspection_and_issue_location():
    spec = importlib.util.spec_from_file_location("qa_proxy", ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    status = module.exchange("get_status", {})
    assert status["ok"]
    assert Path(status["result"]["project"]).resolve() == Path(os.environ["PRO_AI_TEST_DIR"]) / "BridgeSmoke.aprx"
    assert module.select_session(status["result"]["process_id"])["selected"]
    path = str(Path(os.environ["PRO_AI_TEST_DIR"]) / "smoke.gdb" / "join_zones")
    added = module.exchange("add_layer", {"path": path})
    assert added["ok"], added
    layer = added["result"]["uri"]
    # Every polygon has a different zone_name: no duplicate IDs or missing fields.
    good = module.exchange("inspect_parcels", {"layer": layer, "id_field": "zone_name"})
    assert good["ok"], good
    assert good["result"]["feature_count"] == 2 and not good["result"]["issues"]
    bad = module.exchange("inspect_parcels", {"layer": layer, "id_field": "qa_id", "required_fields": ["qa_required"]})
    assert bad["ok"] and bad["result"]["issue_count"] == 3, bad
    issue = bad["result"]["issues"][0]
    located = module.exchange("select_by_attribute", {"layer": layer, "where": issue["where"]})
    assert located["ok"] and located["result"]["selected"] == 1, located
    assert module.exchange("zoom_to_selection", {})["ok"]
    filtered = module.exchange("inspect_parcels", {"layer": layer, "id_field": "qa_id"})
    assert not filtered["ok"] and "选择" in filtered["error"]
    assert module.exchange("clear_selection", {})["ok"]
    bounded = module.exchange("inspect_parcels", {"layer": layer, "id_field": "zone_name", "max_features": 1})
    assert not bounded["ok"] and "上限" in bounded["error"]
    # OBJECTID is deliberately required as a value that is always present; bad field rejects.
    invalid = module.exchange("inspect_parcels", {"layer": layer, "id_field": "not_a_field"})
    assert not invalid["ok"] and "字段" in invalid["error"]
    assert module.exchange("remove_layer", {"layer": layer, "confirm": True})["ok"]
    (Path(os.environ["PRO_AI_TEST_DIR"]) / "parcel-inspection-receipt.json").write_text(
        json.dumps({"healthy": good, "anomalies": bad, "located": located, "selected_scope_refused": filtered,
                    "scan_limit_refused": bounded, "missing_field_refused": invalid}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_real_pro_workflow():
    spec = importlib.util.spec_from_file_location("live_proxy", ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    records = []

    def call(name, **args):
        value = module.exchange(name, args)
        records.append({"name": name, "arguments": args, "response": value})
        assert value["ok"], value
        return value["result"]

    target = Path(os.environ["PRO_AI_TEST_DIR"]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        status = None
        for _ in range(60):
            try:
                status = module.exchange("get_status", {})["result"]
                if status["map"]:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        assert status and status["map"], "Pro test map did not finish opening"
        status = call("get_status")
        assert Path(status["project"]).resolve() == target / "BridgeSmoke.aprx"
        sessions = module.discover_sessions()
        assert any(
            s["process_id"] == status["process_id"] for s in sessions["sessions"]
        )
        assert module.select_session(status["process_id"])["selected"]
        maps = call("list_maps")
        assert maps["maps"]
        other = next(m for m in maps["maps"] if m["name"] == "Bridge Other Map")
        main = next(m for m in maps["maps"] if m["name"] == "Bridge Map")
        call("activate_map", map=other["uri"])
        assert call("get_status")["map"] == "Bridge Other Map"
        call("activate_map", map=main["uri"])
        assert call("get_status")["map"] == "Bridge Map"
        layers = call("list_layers")["layers"]
        layer = next(x["uri"] for x in layers if x["name"] == "bridge_points")
        refused_context = module.exchange_at(
            status["pipe"]
            if status["pipe"].startswith("\\\\")
            else "\\\\.\\pipe\\" + status["pipe"],
            "set_layer_visibility",
            {"layer": layer, "visible": False},
            expected_project=str(target / "Wrong.aprx"),
        )
        assert not refused_context["ok"] and "工程" in refused_context["error"]
        assert next(
            item for item in call("list_layers")["layers"] if item["uri"] == layer
        )["visible"]
        wrong_map = module.exchange_at(module._SESSION["pipe"], "set_layer_visibility", {"layer": layer, "visible": False},
            expected_project=status["project"], expected_map="wrong-map")
        assert not wrong_map["ok"] and "地图" in wrong_map["error"]
        unbound = module.exchange_at(module._SESSION["pipe"], "list_layers", {}, expected_project=status["project"])
        assert not unbound["ok"] and "目标地图" in unbound["error"]
        renamed = call("rename_layer", layer=layer, new_name="Bridge Renamed")
        assert renamed["uri"] == layer and renamed["name"] == "Bridge Renamed"
        call("rename_layer", layer=layer, new_name="bridge_points")
        assert call("list_fields", layer=layer)["fields"]
        assert call("get_feature_count", layer=layer)["count"] == 3
        assert len(call("get_layer_features", layer=layer, limit=2)["features"]) == 2
        call("set_layer_visibility", layer=layer, visible=False)
        assert not next(x for x in call("list_layers")["layers"] if x["uri"] == layer)[
            "visible"
        ]
        call("set_layer_visibility", layer=layer, visible=True)
        call("set_definition_query", layer=layer, where="sample_id >= 2")
        assert call("get_feature_count", layer=layer)["count"] == 2
        assert not module.exchange("describe_features", {"input": layer})["ok"]
        call("set_definition_query", layer=layer, where="")
        assert (
            call("select_by_attribute", layer=layer, where="sample_id = 2")["selected"]
            == 1
        )
        assert call("zoom_to_selection")["selected"] == 1
        selected_input = module.exchange("buffer_features", dict(input=layer, output=str(target / "smoke.gdb" / "selected_bad"), distance=1, unit="meters", confirm=True))
        assert not selected_input["ok"] and "选择" in selected_input["error"]
        call("clear_selection")
        assert not module.exchange("zoom_to_selection", {})["ok"]
        call("zoom_to_layer", layer=layer)
        call("set_simple_symbology", layer=layer, color="#3478AB", size=9)
        call(
            "apply_symbology_from_layer",
            layer=layer,
            path=str(target / "symbols.lyrx"),
            confirm=True,
        )
        call("set_simple_symbology", layer=layer, color="#3478AB", size=9)
        call("export_map", path=str(target / "map.png"), confirm=True)
        assert (target / "map.png").stat().st_size > 1000
        assert blue_clusters(target / "map.png") == 3
        call(
            "export_layout",
            layout="Bridge Layout",
            path=str(target / "layout.pdf"),
            confirm=True,
        )
        assert (target / "layout.pdf").read_bytes().startswith(b"%PDF")
        job = call(
            "run_geoprocessing",
            tool="management.GetCount",
            parameters=[str(target / "smoke.gdb" / "bridge_points")],
            confirm=True,
        )
        for _ in range(60):
            state = call("get_job", job_id=job["job_id"])
            if state["status"] not in ("queued", "running"):
                break
            time.sleep(0.5)
        assert state["status"] == "succeeded" and state["value"] == "3"
        assert state["started_at"] and state["finished_at"] and state["elapsed_ms"] >= 0
        assert state["output_path"] is None and not state["can_load_output"]
        no_output = module.exchange("load_job_output", {"job_id": job["job_id"], "confirm": True})
        records.append(dict(name="load_job_output", arguments={"job_id":job["job_id"], "confirm":True}, response=no_output))
        assert not no_output["ok"] and "要素结果" in no_output["error"]
        assert any(j["job_id"] == job["job_id"] for j in call("list_jobs")["jobs"])
        assert call("cancel_job", job_id=job["job_id"])["status"] == "succeeded"
        def wait_job(job):
            for _ in range(120):
                value = call("get_job", job_id=job["job_id"])
                if value["status"] not in ("queued", "running"):
                    return value
                time.sleep(0.5)
            raise AssertionError("GP timeout")

        source = str(target / "smoke.gdb" / "bridge_points")
        def output(n):
            return str(target / "smoke.gdb" / n)
        info = call("describe_features", input=source)
        assert info["Geometry"] == "Point" and info["Geographic"]
        assert call("describe_features", input=str(target / "points.shp"))["Geometry"] == "Point"
        for name, args in [
            ("buffer_features", dict(input=source, output=output("buffer_bad"), distance=1, unit="meters", method="PLANAR", confirm=True)),
            ("buffer_features", dict(input=output("unknown_points"), output=output("unknown_bad"), distance=1, unit="meters", confirm=True)),
            ("clip_features", dict(input=source, clip=output("clip_projected"), output=output("clip_bad"), confirm=True)),
            ("dissolve_features", dict(input=source, output=output("dissolve_bad"), fields=["missing"], confirm=True)),
        ]:
            response = module.exchange(name, args)
            records.append(dict(name=name, arguments=args, response=response))
            assert not response["ok"], response
        for name, args, count in [
            ("buffer_features", dict(input=source, output=output("buffer_result"), distance=120, unit="meters"), 3),
            ("clip_features", dict(input=source, clip=output("clip_area"), output=output("clip_result")), 2),
            ("dissolve_features", dict(input=source, output=output("dissolve_result"), fields=["group_id"]), 2),
            ("buffer_features", dict(input=source, output=str(target / "buffer_without_extension"), distance=120, unit="meters"), 3),
        ]:
            state = wait_job(call(name, **args, confirm=True))
            assert state["status"] == "succeeded", state
            expected_output = args["output"] if ".gdb" in args["output"] else args["output"] + ".shp"
            assert state["can_load_output"] and Path(state["output_path"]) == Path(expected_output)
            assert state["started_at"] and state["finished_at"] and state["elapsed_ms"] >= 0
            # Pro may temporarily release its active map during GP/UI activation.
            # Explicitly restore the known fixture map; never relax the guard or
            # retry a potentially completed write after a context failure.
            call("activate_map", map=main["uri"])
            before = len(call("list_layers")["layers"])
            loaded = call("load_job_output", job_id=state["job_id"], confirm=True)
            assert loaded["loaded"] and not loaded["already_loaded"]
            result_layer = loaded["layer"]
            repeated = call("load_job_output", job_id=state["job_id"], confirm=True)
            assert repeated["already_loaded"] and repeated["layer"] == result_layer
            assert len(call("list_layers")["layers"]) == before + 1
            wrong_target = module.exchange_at(module._SESSION["pipe"], "load_job_output", {"job_id":state["job_id"],"confirm":True},
                expected_project=status["project"], expected_map="wrong-map")
            assert not wrong_target["ok"] and "地图" in wrong_target["error"]
            assert call("get_feature_count", layer=result_layer)["count"] == count
            call("remove_layer", layer=result_layer, confirm=True)
        cancel = call("buffer_features", input=output("cancel_points"), output=output("cancel_result"), distance=1000, unit="meters", confirm=True)
        assert call("cancel_job", job_id=cancel["job_id"])["cancel_requested"]
        cancelled = wait_job(cancel)
        assert cancelled["status"] == "cancelled", cancelled
        refused_cancelled = module.exchange("load_job_output", {"job_id":cancel["job_id"],"confirm":True})
        records.append(dict(name="load_job_output", arguments={"job_id":cancel["job_id"],"confirm":True}, response=refused_cancelled))
        assert not refused_cancelled["ok"] and not cancelled["can_load_output"]
        # Existing GIS datasets must survive attempted output collisions.
        rejected_job = call(
            "run_geoprocessing",
            tool="management.CopyFeatures",
            parameters=[
                str(target / "smoke.gdb" / "bridge_points"),
                str(target / "smoke.gdb" / "bridge_points"),
            ],
            confirm=True,
        )
        for _ in range(60):
            rejected = call("get_job", job_id=rejected_job["job_id"])
            if rejected["status"] not in ("queued", "running"):
                break
            time.sleep(0.5)
        assert rejected["status"] == "failed"
        refused_failed = module.exchange("load_job_output", {"job_id":rejected_job["job_id"],"confirm":True})
        records.append(dict(name="load_job_output", arguments={"job_id":rejected_job["job_id"],"confirm":True}, response=refused_failed))
        assert not refused_failed["ok"] and not rejected["can_load_output"]
        assert call("get_feature_count", layer=layer)["count"] == 3
        call("add_layer", path=str(target / "smoke.gdb" / "bridge_points"))
        current = call("list_layers")["layers"]
        added = next(
            x["uri"]
            for x in current
            if x["uri"] != layer and x["name"] == "bridge_points"
        )
        call("move_layer", layer=layer, reference_layer=added, placement="before")
        order = [x["uri"] for x in call("list_layers")["layers"]]
        assert order.index(layer) < order.index(added)
        call("move_layer", layer=layer, reference_layer=added, placement="after")
        order = [x["uri"] for x in call("list_layers")["layers"]]
        assert order.index(layer) > order.index(added)
        refused = module.exchange("remove_layer", {"layer": added, "confirm": False})
        assert not refused["ok"]
        call("remove_layer", layer=added, confirm=True)
        call("save_project", confirm=True)
        call("open_assistant")
    finally:
        (target / "live-receipt.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def test_result_moved_then_restored():
    spec = importlib.util.spec_from_file_location("live_missing_proxy", ROOT / "mcp_server.py")
    proxy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = proxy
    spec.loader.exec_module(proxy)
    target = Path(os.environ["PRO_AI_TEST_DIR"]).resolve()
    status = proxy.exchange("get_status", {})["result"]
    assert Path(status["project"]).resolve() == target / "BridgeSmoke.aprx"
    proxy.select_session(status["process_id"])
    original = target / "moved_result.shp"
    moved = target / "moved_result_away.shp"
    assert not original.exists() and not moved.exists()
    records = []

    def call(name, **args):
        value = proxy.exchange(name, args)
        records.append(dict(name=name, arguments=args, response=value))
        return value

    pro = Path(os.environ.get("ARCGIS_PRO_HOME", "D:/Program Files/ArcGIS/Pro")) / "bin/Python/envs/arcgispro-py3/python.exe"
    def rename(source, destination):
        subprocess.run([str(pro), "-c", "import arcpy,sys; arcpy.management.Rename(sys.argv[1],sys.argv[2])", str(source), str(destination)],
                       capture_output=True, check=True, timeout=30)

    try:
        reply = call("run_geoprocessing", tool="management.CopyFeatures", parameters=[str(target / "smoke.gdb/bridge_points"), str(original)], confirm=True)
        assert reply["ok"], reply
        job_id = reply["result"]["job_id"]
        for _ in range(120):
            state = call("get_job", job_id=job_id)["result"]
            if state["status"] not in ("running", "queued"):
                break
            time.sleep(0.5)
        assert state["status"] == "succeeded" and original.is_file()
        rename(original, moved)
        assert moved.is_file() and not original.exists()
        missing = call("load_job_output", job_id=job_id, confirm=True)
        assert not missing["ok"] and "无法读取任务结果" in missing["error"]
        rename(moved, original)
        restored = call("load_job_output", job_id=job_id, confirm=True)
        assert restored["ok"], restored
        assert call("get_feature_count", layer=restored["result"]["layer"])["result"]["count"] == 3
        assert call("remove_layer", layer=restored["result"]["layer"], confirm=True)["ok"]
    finally:
        if moved.is_file() and not original.exists():
            rename(moved, original)
        (target / "moved-result-receipt.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def test_projection_and_spatial_join():
    spec = importlib.util.spec_from_file_location("live_gis_proxy", ROOT / "mcp_server.py")
    proxy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = proxy
    spec.loader.exec_module(proxy)
    target = Path(os.environ["PRO_AI_TEST_DIR"]).resolve()
    status = proxy.exchange("get_status", {})["result"]
    assert Path(status["project"]).resolve() == target / "BridgeSmoke.aprx"
    proxy.select_session(status["process_id"])
    records = []

    def call(name, **args):
        value = proxy.exchange(name, args)
        records.append(dict(name=name, arguments=args, response=value))
        assert value["ok"], value
        return value["result"]

    def wait(job):
        for _ in range(120):
            state = call("get_job", job_id=job["job_id"])
            if state["status"] not in ("running", "queued"):
                assert state["status"] == "succeeded", state
                return state
            time.sleep(0.5)
        raise AssertionError("GIS job timeout")

    source = str(target / "smoke.gdb/bridge_points")
    zones = str(target / "smoke.gdb/join_zones")
    try:
        call("activate_map", map="Bridge Map")
        transforms = call("list_transformations", input=source, target_wkid=3857)
        assert not transforms["transformation_required"] and transforms["transformations"] == []
        state = wait(call("project_features", input=source, output=str(target / "smoke.gdb/projected_result"), target_wkid=3857, confirm=True))
        layer = call("load_job_output", job_id=state["job_id"], confirm=True)["layer"]
        metadata = call("describe_features", input=layer)
        assert not metadata["Geographic"] and metadata["Datum"]
        assert call("get_feature_count", layer=layer)["count"] == 3
        call("remove_layer", layer=layer, confirm=True)
        for operation, name, expected in [("ONE_TO_MANY","joined_many",5),("ONE_TO_ONE","joined_one",3)]:
            state = wait(call("spatial_join_features", input=source, join=zones, output=str(target / ("smoke.gdb/"+name)), join_operation=operation, confirm=True))
            layer = call("load_job_output", job_id=state["job_id"], confirm=True)["layer"]
            assert call("get_feature_count", layer=layer)["count"] == expected
            rows = call("get_layer_features", layer=layer, limit=10)["features"]
            assert {r["sample_id"] for r in rows} == {1,2,3}
            call("remove_layer", layer=layer, confirm=True)
        bad = proxy.exchange("spatial_join_features", dict(input=source,join=str(target / "smoke.gdb/clip_projected"),output=str(target / "smoke.gdb/invalid_join"),confirm=True))
        assert not bad["ok"] and "坐标系" in bad["error"]
        invalid = proxy.exchange("project_features",dict(input=source,output=str(target / "smoke.gdb/invalid_project"),target_wkid=999999,confirm=True))
        assert not invalid["ok"]
    finally:
        (target / "gis-v05-receipt.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")


def test_cross_datum_projection():
    spec = importlib.util.spec_from_file_location("live_datum_proxy", ROOT / "mcp_server.py")
    proxy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = proxy
    spec.loader.exec_module(proxy)
    target = Path(os.environ["PRO_AI_TEST_DIR"]).resolve()
    status = proxy.exchange("get_status", {})["result"]
    assert Path(status["project"]).resolve() == target / "BridgeSmoke.aprx"
    proxy.select_session(status["process_id"])
    records = []

    def call(name, **args):
        reply = proxy.exchange(name,args)
        records.append(dict(name=name,arguments=args,response=reply))
        assert reply["ok"],reply
        return reply["result"]

    try:
        call("activate_map",map="Bridge Map")
        for source, output, wkid, transformation in [
            ("datum_wgs","datum_nad_result",4269,"~NAD_1983_To_WGS_1984_5"),
            ("datum_nad_result","datum_wgs_result",4326,"NAD_1983_To_WGS_1984_5"),
        ]:
            path = str(target / ("smoke.gdb/"+source))
            choices = call("list_transformations",input=path,target_wkid=wkid)
            assert choices["transformation_required"] and transformation in choices["transformations"]
            refused = proxy.exchange("project_features",dict(input=path,output=str(target / "smoke.gdb/no_transform"),target_wkid=wkid,confirm=True))
            assert not refused["ok"] and "基准面" in refused["error"]
            job = call("project_features",input=path,output=str(target / ("smoke.gdb/"+output)),target_wkid=wkid,transformation=transformation,confirm=True)
            for _ in range(120):
                state = call("get_job",job_id=job["job_id"])
                if state["status"] not in ("queued","running"):
                    break
                time.sleep(0.5)
            assert state["status"] == "succeeded",state
            layer = call("load_job_output",job_id=job["job_id"],confirm=True)["layer"]
            assert call("get_feature_count",layer=layer)["count"] == 1
            call("remove_layer",layer=layer,confirm=True)
    finally:
        (target / "datum-v05-receipt.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")
