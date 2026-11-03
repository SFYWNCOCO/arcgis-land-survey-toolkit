"""Standard stdio MCP facade for the workbench's live ArcGIS Pro component."""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

import anyio
from jsonschema import Draft202012Validator, ValidationError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

ROOT = Path(__file__).resolve().parent
PIPE_PREFIX = "pro-ai-bridge-v1-"
MAX_BYTES = 1_048_576
TIMEOUT = 90.0
_CATALOG = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))
_SCHEMAS = {
    tool["name"]: Draft202012Validator(tool["inputSchema"]) for tool in _CATALOG
}
_MAP_TOOLS = {t["name"] for t in _CATALOG if t.get("_meta", {}).get("pro-ai-bridge/context") != "project"}
app = Server("pro-ai-bridge")
_SESSION = None
_STATE_LOCK = threading.Lock()


def catalog() -> list[Tool]:
    return [Tool.model_validate(tool) for tool in _CATALOG]


def decode_response(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Pro 桥接响应为空或超过 1 MB。")
    # A connection carries exactly one JSON line, never a partial or appended message.
    text = raw.decode("utf-8").strip()
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ValueError("Pro 桥接响应不符合协议。")
    return result


def choose_pipe(names, requested: str) -> str:
    candidates = sorted(
        n for n in names if re.fullmatch(re.escape(PIPE_PREFIX) + r"[1-9][0-9]*", n)
    )
    if requested:
        if (
            not re.fullmatch(r"[1-9][0-9]*", requested)
            or PIPE_PREFIX + requested not in candidates
        ):
            raise ValueError(
                "所选 Pro 进程不可用；请调用 list_pro_sessions 后重新选择，或核对 PRO_AI_PID。"
            )
        return "\\\\.\\pipe\\" + PIPE_PREFIX + requested
    if len(candidates) > 1:
        raise ValueError(
            "发现多个 ArcGIS Pro 实例；请调用 list_pro_sessions、select_pro_session 选择工程，或设置 PRO_AI_PID。"
        )
    if not candidates:
        raise OSError(
            "Pro AI Bridge 未连接。请打开 ArcGIS Pro，安装插件后重启，并打开 AI 助手侧栏。"
        )
    return "\\\\.\\pipe\\" + candidates[0]


def available_pipe_names() -> list[str]:
    return os.listdir("\\\\.\\pipe\\") if os.name == "nt" else []


def inspect_dsh(home: Path | None = None) -> dict:
    home = home or Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
    profile = home / "profiles" / "desktop" / "cordis.patch.yml"
    text = profile.read_text(encoding="utf-8-sig") if profile.is_file() else ""
    registered = bool(
        re.search(
            r"(?m)^[ \t]*(?:-[ \t]*)?id[ \t]*:[ \t]*(['\"]?)pro-ai-bridge-mcp\1[ \t]*(?:#.*)?$",
            text,
        )
    )
    return {
        "profile_exists": profile.is_file(),
        "configuration": "saved" if registered else "missing",
        "connection": "not_verified",
        "hint": "配置已保存不等于桌面已连接；请在 DSH 中调用 get_status 验证。",
    }


def discover_sessions() -> dict:
    names = sorted(
        n
        for n in available_pipe_names()
        if re.fullmatch(re.escape(PIPE_PREFIX) + r"[1-9][0-9]*", n)
    )

    def probe(n):
        pid = int(n[len(PIPE_PREFIX) :])
        try:
            reply = exchange_at("\\\\.\\pipe\\" + n, "get_status", {}, timeout=3.0)
            if not reply.get("ok"):
                raise ValueError(reply.get("error", "Pro 状态查询失败。"))
            status = reply["result"]
            if status.get("process_id") != pid:
                raise ValueError("Pro 进程身份不一致。")
            return {"session": status}
        except (OSError, ValueError) as exc:
            return {"error": {"process_id": pid, "message": str(exc)}}

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(probe, names[:16]))
    return {
        "sessions": [r["session"] for r in replies if "session" in r],
        "errors": [r["error"] for r in replies if "error" in r],
        "omitted": max(0, len(names) - 16),
    }


def _probe_session(pid: int) -> dict:
    pipe = choose_pipe(available_pipe_names(), str(pid))
    reply = exchange_at(pipe, "get_status", {}, timeout=5.0)
    if not reply.get("ok"):
        raise ValueError(reply.get("error", "无法选择该工程。"))
    status = reply["result"]
    if status.get("process_id") != pid or not status.get("supports_project_guard") or not status.get("supports_map_guard"):
        raise ValueError(
            "工程和地图选择需要 Pro AI Bridge 0.3 或更高版本，请升级并重启 Pro。"
        )
    if not status.get("project"):
        raise ValueError("目标 Pro 尚未打开工程。")
    return {"process_id": pid, "project": status["project"], "map_uri": status.get("map_uri", ""), "pipe": pipe}


def select_session(pid: int) -> dict:
    global _SESSION
    selected = _probe_session(pid)
    with _STATE_LOCK:
        _SESSION = selected
    return {"selected": True, **selected}


def exchange(name: str, arguments: dict) -> dict:
    global _SESSION
    with _STATE_LOCK:
        selected = _SESSION
    if selected is None:
        pipe = choose_pipe(available_pipe_names(), os.environ.get("PRO_AI_PID", ""))
        candidate = _probe_session(int(pipe.rsplit("-", 1)[1]))
        with _STATE_LOCK:
            if _SESSION is None:
                _SESSION = candidate
            selected = _SESSION
    # A missing selected process never silently falls back to another open project.
    choose_pipe(available_pipe_names(), str(selected["process_id"]))
    if name in _MAP_TOOLS and not selected["map_uri"]:
        raise ValueError("尚未选择地图，请调用 list_maps、activate_map。")
    reply = exchange_at(
        selected["pipe"], name, arguments, expected_project=selected["project"],
        expected_map=selected["map_uri"] if name in _MAP_TOOLS else "",
    )
    if name == "activate_map" and reply.get("ok"):
        map_uri = reply.get("result", {}).get("map_uri")
        if not isinstance(map_uri, str) or not map_uri:
            raise ValueError("地图激活未返回地图身份，请重新选择工程。")
        with _STATE_LOCK:
            if _SESSION is selected:
                _SESSION = {**selected, "map_uri": map_uri}
    return reply


def exchange_at(
    pipe_name: str, name: str, arguments: dict, *, timeout=TIMEOUT, expected_project="", expected_map=""
) -> dict:
    if os.name != "nt":
        raise OSError("ArcGIS Pro 桥接只支持 Windows 本机。")
    deadline = time.monotonic() + timeout
    payload = (
        json.dumps(
            {
                "name": name,
                "arguments": arguments,
                "expected_project": expected_project,
                "expected_map": expected_map,
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise ValueError("请求超过 1 MB。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetNamedPipeHandleState.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel.SetNamedPipeHandleState.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    if not kernel.WaitNamedPipeW(pipe_name, max(1, min(3000, int(timeout * 1000)))):
        raise OSError(
            "Pro AI Bridge 未连接。请打开 ArcGIS Pro，安装插件后重启，并打开 AI 助手侧栏。"
            f"（Win32 {ctypes.get_last_error()}）"
        )
    handle = kernel.CreateFileW(pipe_name, 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(f"无法打开 Pro 管道（Win32 {ctypes.get_last_error()}）。")
    try:
        mode = wintypes.DWORD(1)  # PIPE_NOWAIT bounds both write and read waits.
        if not kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise OSError(f"设置管道失败（Win32 {ctypes.get_last_error()}）。")
        sent = wintypes.DWORD()
        buf = ctypes.create_string_buffer(payload)
        if not kernel.WriteFile(
            handle, buf, len(payload), ctypes.byref(sent), None
        ) or sent.value != len(payload):
            raise OSError("管道未完整接收请求；没有自动重试，以免重复执行。")
        data = bytearray()
        while len(data) <= MAX_BYTES:
            if time.monotonic() >= deadline:
                raise TimeoutError("Pro 操作响应超时；请核对地图状态，避免重复写入。")
            chunk = ctypes.create_string_buffer(8192)
            size = wintypes.DWORD()
            ok = kernel.ReadFile(handle, chunk, len(chunk), ctypes.byref(size), None)
            code = ctypes.get_last_error() if not ok else 0
            if code == 232:  # ERROR_NO_DATA
                time.sleep(0.015)
                continue
            if not ok and code != 234:
                raise OSError(f"读取管道失败（Win32 {code}）。")
            if not size.value:
                raise ValueError("Pro 在响应完成前关闭了管道。")
            data.extend(chunk.raw[: size.value])
            if b"\n" in data:
                return decode_response(bytes(data))
        raise ValueError("Pro 响应超过 1 MB。")
    finally:
        kernel.CloseHandle(handle)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return catalog()


@app.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict | None) -> CallToolResult:
    try:
        if name not in _SCHEMAS:
            raise ValueError("未知工具：" + name)
        arguments = {} if arguments is None else arguments
        _SCHEMAS[name].validate(arguments)
        if name == "list_pro_sessions":
            value = await anyio.to_thread.run_sync(discover_sessions)
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(value, ensure_ascii=False))
                ]
            )
        if name == "select_pro_session":
            value = await anyio.to_thread.run_sync(
                select_session, arguments["process_id"]
            )
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(value, ensure_ascii=False))
                ]
            )
        reply = await anyio.to_thread.run_sync(exchange, name, arguments)
        if not reply["ok"]:
            raise ValueError(str(reply.get("error", "Pro 操作失败。")))
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(reply.get("result"), ensure_ascii=False),
                )
            ]
        )
    except (ValueError, OSError, ValidationError) as exc:
        # Input errors never include a provider key: model configuration is not part of MCP.
        return CallToolResult(
            isError=True, content=[TextContent(type="text", text=str(exc)[:3000])]
        )


async def main() -> None:
    async with stdio_server() as (reader, writer):
        await app.run(reader, writer, app.create_initialization_options())


if __name__ == "__main__":
    if "--diagnose" in sys.argv:
        print(
            json.dumps(
                {"pro": discover_sessions(), "dsh": inspect_dsh()},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif "--check" in sys.argv:
        print(
            json.dumps(
                {"server": "pro-ai-bridge", "tools": [t.name for t in catalog()]},
                ensure_ascii=False,
            )
        )
    else:
        anyio.run(main)
