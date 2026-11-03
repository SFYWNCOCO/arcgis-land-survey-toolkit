"""Local-only MCP bridge to the ArcGIS Pro add-in's allowlisted named pipe."""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from typing import Any

PIPE_NAME = r"\\.\pipe\arcgis-pro-live"
MAX_QUERY = 128
MAX_RESPONSE_BYTES = 1_000_000
PIPE_NOWAIT = 0x00000001
ERROR_NO_DATA = 232
ERROR_MORE_DATA = 234
MAX_TIMEOUT_SECONDS = 60.0

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
except Exception:  # pragma: no cover - keeps pure request validation importable
    class ToolError(Exception):
        pass

    class _NoMcp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def tool(self, *args: Any, **kwargs: Any):
            return lambda fn: fn

        def run(self, *args: Any, **kwargs: Any) -> None:
            raise ToolError('未安装 mcp 包；请安装兼容版本 `mcp<2`。')

    FastMCP = _NoMcp  # type: ignore[misc,assignment]

try:
    mcp = FastMCP("arcgis-pro-live", mask_error_details=True)
except TypeError:
    mcp = FastMCP("arcgis-pro-live")


def normalize_request(action: str, **kwargs: Any) -> dict[str, Any]:
    """Build a small, allowlisted request. Unknown actions never reach Pro."""
    if action in {"status", "list_layers"}:
        return {"action": action}
    if action not in {"search_parcel", "zoom_to_parcel"}:
        raise ValueError("不支持的操作；仅允许 status、list_layers、search_parcel、zoom_to_parcel。")
    query = str(kwargs.get("query", "")).strip()
    if not query:
        raise ValueError("请输入地块编号。")
    if len(query) > MAX_QUERY:
        raise ValueError(f"地块编号长度不能超过 {MAX_QUERY} 个字符。")
    if action == "zoom_to_parcel" and kwargs.get("confirm") is not True:
        raise ValueError("缩放会改变当前地图范围和选择状态；明确传入 confirm=True 后再操作。")
    layer_uri = str(kwargs.get("layer_uri", "")).strip()
    field = str(kwargs.get("field", "")).strip()
    if len(layer_uri) > 1024 or len(field) > 128:
        raise ValueError("图层或字段名称过长。")
    request = {"action": action, "query": query, "layer_uri": layer_uri, "field": field}
    if action == "zoom_to_parcel":
        request["confirm"] = True
    return request


def _read_pipe_response(read_file, get_last_error, handle, deadline: float,
                        timeout_seconds: float, now=None, pause=None) -> dict[str, Any]:
    now = now or time.monotonic
    pause = pause or time.sleep
    chunks = bytearray()
    while len(chunks) <= MAX_RESPONSE_BYTES:
        remaining = deadline - now()
        if remaining <= 0:
            raise ToolError(f"ArcGIS Pro 实时桥接超过 {timeout_seconds:g} 秒未返回响应。")
        buffer = ctypes.create_string_buffer(4096)
        received = wintypes.DWORD()
        ok = read_file(handle, buffer, len(buffer), ctypes.byref(received), None)
        code = get_last_error() if not ok else 0
        if not ok and code == ERROR_NO_DATA:
            pause(min(0.01, max(0.0, remaining)))
            continue
        if not ok and code != ERROR_MORE_DATA:
            raise ToolError(f"读取实时 Pro 响应失败（Win32 {code}）。")
        if received.value == 0:
            break
        chunks.extend(buffer.raw[:received.value])
        if b"\n" in chunks:
            break
    if len(chunks) > MAX_RESPONSE_BYTES:
        raise ToolError("实时 Pro 响应超过 1 MB 限制。")
    line = bytes(chunks).split(b"\n", 1)[0].rstrip(b"\r")
    if not line:
        raise ToolError("ArcGIS Pro 实时桥接返回空响应。")
    try:
        result = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError(f"ArcGIS Pro 实时桥接响应格式错误：{exc}") from exc
    if not isinstance(result, dict):
        raise ToolError("ArcGIS Pro 实时桥接响应不是 JSON 对象。")
    return result


def _pipe_connection_error_message(code: int) -> str:
    code = int(code)
    if code == 2:
        return (
            f"ArcGIS Pro 实时桥接未启动（Win32 {code}）。请确认已加载采样助手 1.36 或更高版本；"
            "安装或更新后请完全重启 ArcGIS Pro。"
        )
    if code == 5:
        return (
            f"无法访问 ArcGIS Pro 实时桥接（Win32 {code}）。"
            "请让 ArcGIS Pro 与 MCP 服务在同一 Windows 用户下运行。"
        )
    return (
        f"连接不到 ArcGIS Pro 实时桥接（Win32 {code}）。请确认 Pro 已打开并加载支持实时 MCP 的采样助手 "
        "（1.36及以上），然后稍后重试。"
    )


def _exchange(request: dict[str, Any], timeout_seconds: float = 20.0) -> dict[str, Any]:
    """Exchange one newline-delimited JSON message over the current-user pipe."""
    if os.name != "nt":
        raise ToolError("实时 Pro 桥接只支持 Windows 本机命名管道。")
    timeout_seconds = float(timeout_seconds)
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ToolError("实时 Pro 桥接超时须大于 0 且不超过 60 秒。")
    deadline = time.monotonic() + timeout_seconds
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                                  wintypes.LPVOID, wintypes.LPVOID]
    kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    wait_ms = max(1, min(int(timeout_seconds * 1000), 60000))
    if not kernel32.WaitNamedPipeW(PIPE_NAME, wait_ms):
        code = ctypes.get_last_error()
        raise ToolError(_pipe_connection_error_message(code))
    handle = kernel32.CreateFileW(PIPE_NAME, 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ToolError(f"打开 ArcGIS Pro 命名管道失败（Win32 {ctypes.get_last_error()}）。")
    try:
        payload = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        sent = wintypes.DWORD()
        source = ctypes.create_string_buffer(payload)
        if not kernel32.WriteFile(handle, source, len(payload), ctypes.byref(sent), None) or sent.value != len(payload):
            raise ToolError(f"发送实时 Pro 请求失败（Win32 {ctypes.get_last_error()}）。")
        mode = wintypes.DWORD(PIPE_NOWAIT)
        if not kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise ToolError(f"设置命名管道非阻塞读取失败（Win32 {ctypes.get_last_error()}）。")
        return _read_pipe_response(kernel32.ReadFile, ctypes.get_last_error, handle,
                                   deadline, timeout_seconds)
    finally:
        kernel32.CloseHandle(handle)


def _call(action: str, **kwargs: Any) -> dict[str, Any]:
    try:
        result = _exchange(normalize_request(action, **kwargs))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if result.get("status") == "ERROR":
        raise ToolError(str(result.get("message", "ArcGIS Pro 操作失败。")))
    return result


def _tool(read_only: bool):
    try:
        return mcp.tool(annotations={"readOnlyHint": read_only})
    except TypeError:
        return mcp.tool()


@_tool(read_only=True)
def live_status() -> dict[str, Any]:
    """读取 ArcGIS Pro 当前活动地图和实时桥接状态。"""
    return _call("status")


@_tool(read_only=True)
def live_list_layers() -> dict[str, Any]:
    """列出当前地图的图层名、类型、URI 和可见状态。"""
    return _call("list_layers")


@_tool(read_only=True)
def live_search_parcel(query: str, layer_uri: str = "", field: str = "") -> dict[str, Any]:
    """按地块编号精确查询；重复编号返回全部候选，不改变地图视图。"""
    return _call("search_parcel", query=query, layer_uri=layer_uri, field=field)


@_tool(read_only=False)
def live_zoom_to_parcel(query: str, layer_uri: str = "", field: str = "",
                        confirm: bool = False) -> dict[str, Any]:
    """唯一精确命中后选中并缩放到地块；须显式传 confirm=True。"""
    return _call("zoom_to_parcel", query=query, layer_uri=layer_uri, field=field, confirm=confirm)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
