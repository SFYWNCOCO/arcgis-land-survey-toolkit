"""Tests for the allowlisted live ArcGIS Pro MCP bridge."""
from __future__ import annotations

import sys
import ctypes
from pathlib import Path
from ctypes import wintypes

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arcgis_mcp"))

import live_server  # noqa: E402


def test_request_rejects_unknown_action_and_arbitrary_code():
    with pytest.raises(ValueError, match="不支持的操作"):
        live_server.normalize_request("execute", code="print(1)")


def test_search_request_requires_bounded_exact_query():
    with pytest.raises(ValueError, match="请输入地块编号"):
        live_server.normalize_request("search_parcel", query="  ")
    with pytest.raises(ValueError, match="长度"):
        live_server.normalize_request("search_parcel", query="x" * 129)
    assert live_server.normalize_request(
        "search_parcel", query="39", layer_uri="layer://parcel", field="DIKUAI_NO"
    ) == {
        "action": "search_parcel",
        "query": "39",
        "layer_uri": "layer://parcel",
        "field": "DIKUAI_NO",
    }


def test_zoom_requires_explicit_confirmation_and_only_forwards_allowlisted_values():
    with pytest.raises(ValueError, match="confirm=True"):
        live_server.normalize_request("zoom_to_parcel", query="39")
    assert live_server.normalize_request(
        "zoom_to_parcel", query="39", confirm=True, ignored="value"
    ) == {"action": "zoom_to_parcel", "query": "39", "layer_uri": "", "field": "", "confirm": True}


def test_live_zoom_returns_bridge_result(monkeypatch):
    expected = {"status": "PASS", "selected": {"parcel_id": "39", "oid": 8}}
    seen = {}

    def fake_exchange(request):
        seen.update(request)
        return expected

    monkeypatch.setattr(live_server, "_exchange", fake_exchange)
    assert live_server.live_zoom_to_parcel("39", confirm=True) == expected
    assert seen == {"action": "zoom_to_parcel", "query": "39", "layer_uri": "", "field": "", "confirm": True}


def test_zoom_does_not_connect_without_confirmation(monkeypatch):
    def unexpected(_request):
        raise AssertionError("pipe must not be opened without confirmation")

    monkeypatch.setattr(live_server, "_exchange", unexpected)
    with pytest.raises(Exception, match="confirm=True"):
        live_server.live_zoom_to_parcel("39", confirm=False)


def test_pipe_response_reader_returns_after_temporary_no_data():
    calls = {"count": 0, "error": 0}
    payload = b'{"status":"PASS"}\n'

    def read_file(_handle, buffer, _size, received, _overlapped):
        calls["count"] += 1
        result = ctypes.cast(received, ctypes.POINTER(wintypes.DWORD)).contents
        if calls["count"] == 1:
            result.value = 0
            calls["error"] = live_server.ERROR_NO_DATA
            return False
        ctypes.memmove(buffer, payload, len(payload))
        result.value = len(payload)
        return True

    result = live_server._read_pipe_response(read_file, lambda: calls["error"], 1,
                                             deadline=1.0, timeout_seconds=1.0,
                                             now=lambda: 0.0, pause=lambda _duration: None)
    assert result == {"status": "PASS"}
    assert calls["count"] == 2


def test_pipe_response_reader_times_out_when_peer_never_sends():
    clock = {"now": 0.0}

    def read_file(_handle, _buffer, _size, received, _overlapped):
        ctypes.cast(received, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return False

    def sleep(duration):
        clock["now"] += duration

    with pytest.raises(live_server.ToolError, match="超过 0.025 秒"):
        live_server._read_pipe_response(read_file, lambda: live_server.ERROR_NO_DATA, 1,
                                        deadline=0.025, timeout_seconds=0.025,
                                        now=lambda: clock["now"], pause=sleep)


def test_missing_pipe_diagnostic_names_supported_addin_version_and_restart():
    message = live_server._pipe_connection_error_message(2)
    assert "实时桥接未启动" in message
    assert "1.36" in message
    assert "重启 ArcGIS Pro" in message


def test_access_denied_pipe_diagnostic_explains_same_user_requirement():
    message = live_server._pipe_connection_error_message(5)
    assert "同一 Windows 用户" in message
