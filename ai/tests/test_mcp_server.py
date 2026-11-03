"""Real MCP catalog/dispatch tests; GIS transport is an injectable boundary."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def load_server():
    path = ROOT / "mcp_server.py"
    assert path.is_file(), "MCP server implementation is missing"
    spec = importlib.util.spec_from_file_location("pro_ai_mcp", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_catalog_available_without_pro():
    server = load_server()
    names = [t.name for t in server.catalog()]
    assert len(names) == len(set(names)) >= 18
    assert {"get_status", "list_layers", "export_layout", "run_geoprocessing"} <= set(
        names
    )


@pytest.mark.anyio
async def test_unknown_tool_never_reaches_pipe():
    server = load_server()
    called = []
    server.exchange = lambda *a: called.append(a)
    result = await server.call_tool("execute_python", {"code": "pass"})
    assert result.isError and not called


@pytest.mark.anyio
async def test_transport_failure_is_mcp_error(monkeypatch):
    server = load_server()

    def fail(*args):
        raise OSError("bridge unavailable")

    monkeypatch.setattr(server, "exchange", fail)
    result = await server.call_tool("get_status", {})
    assert result.isError and "bridge unavailable" in result.content[0].text


@pytest.mark.anyio
async def test_valid_dispatch_and_structured_result(monkeypatch):
    server = load_server()
    seen = []

    def respond(name, arguments):
        seen.append((name, arguments))
        return {"ok": True, "result": {"layers": [{"name": "地块"}]}}

    monkeypatch.setattr(server, "exchange", respond)
    result = await server.call_tool("list_layers", {})
    assert seen == [("list_layers", {})]
    assert not result.isError
    assert json.loads(result.content[0].text)["layers"][0]["name"] == "地块"


@pytest.mark.anyio
async def test_bridge_error_preserves_failure(monkeypatch):
    server = load_server()
    monkeypatch.setattr(
        server, "exchange", lambda *a: {"ok": False, "error": "没有活动地图"}
    )
    result = await server.call_tool("list_layers", {})
    assert result.isError and "没有活动地图" in result.content[0].text


def test_message_reader_rejects_empty_invalid_and_oversize():
    server = load_server()
    for raw in (
        b"",
        b"[]\n",
        b"not-json\n",
        b'{"ok":true}\ntrailing',
        b"x" * (server.MAX_BYTES + 1),
    ):
        with pytest.raises((ValueError, UnicodeError)):
            server.decode_response(raw)


def test_multiple_pro_instances_require_explicit_selection():
    server = load_server()
    names = ["pro-ai-bridge-v1-100", "pro-ai-bridge-v1-200", "unrelated"]
    with pytest.raises(ValueError, match="PRO_AI_PID"):
        server.choose_pipe(names, "")
    assert server.choose_pipe(names, "200").endswith("-200")
    with pytest.raises(ValueError):
        server.choose_pipe(names, "999")
    assert server.choose_pipe(names[:1], "").endswith("-100")


def test_discovery_reports_project_and_individual_probe_failures(monkeypatch):
    server = load_server()
    monkeypatch.setattr(
        server,
        "available_pipe_names",
        lambda: ["pro-ai-bridge-v1-100", "pro-ai-bridge-v1-200"],
    )

    def probe(pipe, name, args, **kwargs):
        if pipe.endswith("-200"):
            raise TimeoutError("busy")
        return {
            "ok": True,
            "result": {
                "process_id": 100,
                "project": "D:/a.aprx",
                "supports_project_guard": True,
                "supports_map_guard": True, "map_uri": "map-b",
            },
        }

    monkeypatch.setattr(server, "exchange_at", probe)
    result = server.discover_sessions()
    assert result["sessions"][0]["project"] == "D:/a.aprx"
    assert result["errors"][0]["process_id"] == 200


def test_selected_session_is_bound_to_project_and_does_not_fall_back(monkeypatch):
    server = load_server()
    names = ["pro-ai-bridge-v1-100", "pro-ai-bridge-v1-200"]
    monkeypatch.setattr(server, "available_pipe_names", lambda: names)
    seen = []

    def probe(pipe, name, args, **kwargs):
        seen.append((pipe, name, kwargs))
        return {
            "ok": True,
            "result": {
                "process_id": 200,
                "project": "D:/b.aprx",
                "supports_project_guard": True,
                "supports_map_guard": True, "map_uri": "map-b",
            },
        }

    monkeypatch.setattr(server, "exchange_at", probe)
    server.select_session(200)
    server.exchange("list_layers", {})
    assert seen[-1][0].endswith("-200")
    assert seen[-1][2]["expected_project"] == "D:/b.aprx"
    names.remove("pro-ai-bridge-v1-200")
    with pytest.raises((ValueError, OSError)):
        server.exchange("set_layer_visibility", {"layer": "a", "visible": False})
    assert len(seen) == 2


def test_selection_rejects_old_addin_without_project_guard(monkeypatch):
    server = load_server()
    monkeypatch.setattr(
        server, "available_pipe_names", lambda: ["pro-ai-bridge-v1-100"]
    )
    monkeypatch.setattr(
        server,
        "exchange_at",
        lambda *a, **kw: {
            "ok": True,
            "result": {"process_id": 100, "project": "D:/a.aprx"},
        },
    )
    with pytest.raises(ValueError, match=r"0\.3"):
        server.select_session(100)


def test_map_binding_changes_only_on_successful_activation(monkeypatch):
    server = load_server()
    monkeypatch.setattr(server, "available_pipe_names", lambda: ["pro-ai-bridge-v1-100"])
    calls = []
    def probe(pipe, name, args, **kw):
        calls.append((name, kw))
        if name == "get_status":
            return {"ok": True, "result": dict(process_id=100, project="D:/a.aprx", map_uri="map-one", supports_project_guard=True, supports_map_guard=True)}
        if name == "activate_map":
            return {"ok": True, "result": {"map_uri": "map-two"}}
        return {"ok": True, "result": {}}
    monkeypatch.setattr(server, "exchange_at", probe)
    server.select_session(100)
    server.exchange("list_layers", {})
    assert calls[-1][1]["expected_map"] == "map-one"
    server.exchange("get_status", {})
    assert server._SESSION["map_uri"] == "map-one"
    server.exchange("activate_map", {"map": "two"})
    server.exchange("list_layers", {})
    assert calls[-1][1]["expected_map"] == "map-two"
    monkeypatch.setattr(server, "exchange_at", lambda *a, **kw: {"ok": False, "error": "map missing"})
    assert not server.exchange("activate_map", {"map": "missing"})["ok"]
    assert server._SESSION["map_uri"] == "map-two"
    server._SESSION = {**server._SESSION, "map_uri": ""}
    with pytest.raises(ValueError, match="activate_map"):
        server.exchange("list_layers", {})


def test_diagnosis_does_not_claim_saved_dsh_config_is_connected_or_expose_keys(
    tmp_path, monkeypatch
):
    server = load_server()
    profile = tmp_path / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text(
        "- insert:\n  - id: pro-ai-bridge-mcp\n    secret: private-test-key\n",
        encoding="utf-8",
    )
    result = server.inspect_dsh(tmp_path)
    assert result["configuration"] == "saved"
    assert result["connection"] == "not_verified"
    assert "private-test-key" not in json.dumps(result)


@pytest.mark.parametrize(
    "text", ["# id: pro-ai-bridge-mcp\n", "- id: pro-ai-bridge-mcp-old\n"]
)
def test_diagnostics_ignore_comments_and_other_plugin_ids(tmp_path, text):
    server = load_server()
    profile = tmp_path / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text(text, encoding="utf-8")
    assert server.inspect_dsh(tmp_path)["configuration"] == "missing"
