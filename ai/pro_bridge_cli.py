"""Pro AI Bridge 命令行助手。

把「发现 Pro 实例 -> 调用工具 -> 轮询地理处理任务 -> 读报告摘要」合成一次命令行调用，
减少 Agent 与 Pro 之间每轮只能发一个工具调用的往返成本。

用法（系统 Python 3.12 / Pro Python 3.9 均可，只用标准库）：
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py list
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py status
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py call list_layers
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py call acceptance_check @args.json
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py callwait acceptance_preview @args.json
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py wait <job_id> --timeout 1800
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py report <一键成果报告.json|资料检查.json>
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py run check --layer 示例区2025年度第一批耕地恢复 --all
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py run preview --layer 示例区2025年度第一批耕地恢复 --all
  python ARCGIS/land_survey_toolkit/ai/pro_bridge_cli.py run delivery --out-dir <成果目录>

安全约定：
- 只读工具不重试写请求；写工具（confirm=true）不自动重试，超时只提示核对任务，不重发。
- map 上下文工具会自动带上 get_status 返回的工程/地图身份；activate_map 后刷新。
- --pid 放在子命令之后（如 `list --pid 6916`）；多实例时必须指定。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path

PIPE_PREFIX = "pro-ai-bridge-v1-"
MAX_BYTES = 1_048_576
# 允许环境变量覆盖：脚本被复制到别处时仍能找到工具目录
BRIDGE_DIR = Path(os.environ.get("PRO_AI_BRIDGE_DIR") or Path(__file__).resolve().parent)
_KERNEL = None


def kernel():
    """惰性加载，保证非 Windows 上 report 等纯本地功能仍可用。"""
    global _KERNEL
    if _KERNEL is not None:
        return _KERNEL
    if os.name != "nt":
        raise OSError("ArcGIS Pro 桥接只支持 Windows 本机。")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    k.WaitNamedPipeW.restype = wintypes.BOOL
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                          wintypes.LPVOID, wintypes.LPVOID]
    k.SetNamedPipeHandleState.restype = wintypes.BOOL
    k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k.WriteFile.restype = wintypes.BOOL
    k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k.ReadFile.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    _KERNEL = k
    return k


def pipe_names() -> list[str]:
    try:
        return sorted(n for n in os.listdir("\\\\.\\pipe\\")
                      if re.fullmatch(re.escape(PIPE_PREFIX) + r"[1-9][0-9]*", n))
    except OSError:
        return []


def map_tools() -> set[str]:
    try:
        catalog = json.loads((BRIDGE_DIR / "tools.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        print(f"警告：读不到工具目录 {BRIDGE_DIR / 'tools.json'}；"
              f"地图上下文守卫参数将无法附加，请设置 PRO_AI_BRIDGE_DIR。", file=sys.stderr)
        return set()
    return {t["name"] for t in catalog
            if t.get("_meta", {}).get("pro-ai-bridge/context") != "project"}


MAP_TOOLS = map_tools()


def exchange(pipe: str, name: str, arguments: dict, timeout: float = 90.0,
             expected_project: str = "", expected_map: str = "") -> dict:
    k = kernel()
    pipe_path = "\\\\.\\pipe\\" + pipe
    if not k.WaitNamedPipeW(pipe_path, max(1, min(3000, int(timeout * 1000)))):
        raise OSError("Pro AI Bridge 未连接。请启动 ArcGIS Pro、打开插件工作台后再试。")
    handle = k.CreateFileW(pipe_path, 0xC0000000, 0, None, 3, 0x80, None)
    if not handle or handle in (-1, 0xFFFFFFFFFFFFFFFF):
        raise OSError(f"打开命名管道失败（Win32 {ctypes.get_last_error()}）。")
    try:
        mode = wintypes.DWORD(1)  # PIPE_NOWAIT：读写都不阻塞，由循环控制时限
        if not k.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            raise OSError(f"设置管道失败（Win32 {ctypes.get_last_error()}）。")
        payload = (json.dumps({"name": name, "arguments": arguments,
                               "expected_project": expected_project,
                               "expected_map": expected_map}, ensure_ascii=False)
                   + "\n").encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise ValueError("请求超过 1 MB。")
        sent = wintypes.DWORD()
        out = ctypes.create_string_buffer(payload)
        if not k.WriteFile(handle, out, len(payload), ctypes.byref(sent), None) \
                or sent.value != len(payload):
            raise OSError("管道未完整接收请求；不自动重试，以免重复执行。")
        data = bytearray()
        deadline = time.monotonic() + timeout
        while len(data) <= MAX_BYTES:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name} 超过 {timeout}s 未返回；操作可能已执行，"
                                   f"先查任务状态再决定是否重跑。")
            chunk = ctypes.create_string_buffer(8192)
            size = wintypes.DWORD()
            ok = k.ReadFile(handle, chunk, len(chunk), ctypes.byref(size), None)
            code = ctypes.get_last_error() if not ok else 0
            if code == 232:  # ERROR_NO_DATA：Pro 还没写好
                time.sleep(0.015)
                continue
            if not ok and code != 234:
                raise OSError(f"读取管道失败（Win32 {code}）。")
            if not size.value:
                raise ValueError("Pro 在响应完成前关闭了管道。")
            data.extend(chunk.raw[: size.value])
            if b"\n" in data:
                break
        if len(data) > MAX_BYTES:
            raise ValueError("响应超过 1 MB。")
        text = bytes(data).decode("utf-8").strip()
        if not text:
            raise ValueError("桥接响应为空。")
        result = json.loads(text)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("桥接响应不符合协议。")
        return result
    finally:
        k.CloseHandle(handle)


def only_pid(pid: int | None = None) -> str:
    names = pipe_names()
    if pid:
        target = PIPE_PREFIX + str(pid)
        if target not in names:
            raise SystemExit(f"指定 Pro 进程 {pid} 未在运行实例中。当前：{names}")
        return target
    if not names:
        raise SystemExit("未发现 Pro 实例：请先启动 ArcGIS Pro 并打开插件工作台。")
    if len(names) > 1:
        raise SystemExit(f"发现多个 Pro 实例：{names}；请用 --pid <进程号> 指定。")
    return names[0]


class Bridge:
    """一次会话：缓存工程/地图身份，给 map 上下文工具自动带上守卫参数。"""

    def __init__(self, pid: int | None = None):
        self.pipe = only_pid(pid)
        self.project = ""
        self.map_uri = ""
        self.map_name = ""
        self._loaded = False

    def load(self) -> dict:
        reply = exchange(self.pipe, "get_status", {}, 10)
        status = unwrap(reply, "get_status")
        self.project = status.get("project") or ""
        self.map_uri = status.get("map_uri") or ""
        self.map_name = status.get("map") or ""
        self._loaded = True
        return status

    def call(self, name: str, arguments: dict, timeout: float = 90.0) -> dict:
        if not self._loaded:
            self.load()
        if not MAP_TOOLS:
            raise SystemExit("工具目录不可用（MAP_TOOLS 为空），无法判断地图上下文守卫；"
                             "请设置 PRO_AI_BRIDGE_DIR 指向含 tools.json 的目录。")
        expected_map = self.map_uri if name in MAP_TOOLS else ""
        reply = exchange(self.pipe, name, arguments, timeout,
                         expected_project=self.project, expected_map=expected_map)
        if reply.get("ok") and name == "activate_map":
            result = reply.get("result") or {}
            self.map_uri = result.get("map_uri") or self.map_uri
            self.map_name = result.get("map") or self.map_name
        return reply


def unwrap(reply: dict, label: str = "") -> dict:
    if not reply.get("ok"):
        raise SystemExit(f"{label} 失败：{reply.get('error', '未知错误')}")
    return reply.get("result")


def parse_args_json(text: str) -> dict:
    """支持内联 JSON 或 @文件：Windows 命令行会吃掉引号，落文件更稳。"""
    if not text:
        return {}
    if text.startswith("@"):
        # Windows 记事本 / PowerShell 写出的 JSON 常带 BOM
        return json.loads(Path(text[1:]).read_text(encoding="utf-8-sig"))
    return json.loads(text)


def poll_job(bridge: Bridge, job_id: str, timeout: float, interval: float) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    retries = 0
    while time.monotonic() < deadline:
        try:
            job = unwrap(bridge.call("get_job", {"job_id": job_id}, 60), "get_job")
            retries = 0
        except (OSError, TimeoutError) as exc:  # 只读探查询：限次重试
            retries += 1
            if retries > 3:
                raise SystemExit(f"连续 {retries} 次读取任务失败：{exc}")
            time.sleep(interval)
            continue
        state = job.get("status")
        if state != last:
            print(f"[{time.strftime('%H:%M:%S')}] status={state} "
                  f"elapsed={job.get('elapsed_ms')}ms", flush=True)
            last = state
        if state in {"succeeded", "failed", "canceled", "cancelled"}:
            return job
        time.sleep(interval)
    raise SystemExit(f"等待 {job_id} 超时（{timeout}s），最后状态：{last}；"
                     f"任务可能仍在跑，先用 wait 复查再重跑。")


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _level_counts(items: list[dict], field: str) -> dict:
    counts: dict = {}
    for item in items:
        level = item.get(field, "?")
        counts[level] = counts.get(level, 0) + 1
    return counts


def summarize_report(path: Path) -> dict:
    """一键成果报告 / 资料检查 的结构化摘要，供 Agent 少读整份 JSON。"""
    data = _read_json(path)
    if data is None:
        return {"报告": str(path), "状态": "无法读取", "提示": "文件不存在或不是合法 JSON"}
    summary: dict = {"报告": str(path)}

    # 资料检查.json（acceptance_check 的输出）：检查项字段是「状态」；
    # 检查清单.json 的项字段是「级别」，必须落到下面的清单分支。
    items = data.get("检查")
    if isinstance(items, list) and data.get("技术状态") is None \
            and all("状态" in i for i in items) and any("级别" not in i for i in items):
        summary["状态"] = data.get("状态")
        summary["源图斑数"] = data.get("源图斑数")
        summary["检查统计"] = _level_counts(items, "状态")
        summary["检查明细"] = [
            {"项目": i.get("项目"), "状态": i.get("状态"), "说明": i.get("说明")}
            for i in items if i.get("状态") != "PASS"
        ]
        return summary

    # 检查清单.json 本体（11 项含「级别」）：直接按清单摘要
    if isinstance(items, list) and data.get("总体状态") and all("级别" in i for i in items):
        summary["检查清单总体状态"] = data.get("总体状态")
        summary["检查统计"] = _level_counts(items, "级别")
        summary["未通过项"] = [
            {"级别": i.get("级别"), "项目": i.get("项目"),
             "证据": i.get("证据"), "下一步": i.get("下一步")}
            for i in items if i.get("级别") in {"FAIL", "NEEDS_REVIEW"}
        ]
        return summary

    for key in ("技术状态", "业务审核状态", "状态", "评价单元数", "图斑数", "报备影像状态"):
        if key in data:
            summary[key] = data[key]
    grid = data.get("网格")
    if isinstance(grid, dict):
        summary["设计点数"] = grid.get("设计点")
    for section in ("出图", "影像", "区位小图", "独立采样质检"):
        block = data.get(section)
        if isinstance(block, dict):
            summary[section] = block.get("status") or block.get("状态")
            detail = block.get("detail")
            if detail:
                summary[section + "_说明"] = detail
    gaps = data.get("数据缺口")
    if isinstance(gaps, dict):
        summary["数据缺口"] = {k: v for k, v in gaps.items() if v}
    elif isinstance(gaps, list) and gaps:
        summary["数据缺口数"] = len(gaps)
        summary["数据缺口前5"] = gaps[:5]
    pending = data.get("待人工确认")
    if isinstance(pending, list) and pending:
        summary["待人工确认数"] = len(pending)
        summary["待人工确认前3"] = pending[:3]

    checklist = data.get("检查清单")
    checklist_path = Path(checklist) if isinstance(checklist, str) else None
    if checklist_path is not None and not checklist_path.is_file():
        sibling = path.parent / "检查清单.json"  # 报告被搬动过时，清单通常在报告同目录
        if sibling.is_file():
            checklist_path = sibling
    if checklist_path is not None and checklist_path.is_file():
        report = _read_json(checklist_path) or {}
        items = report.get("检查", [])
        summary["检查清单"] = str(checklist_path)
        summary["检查清单总体状态"] = report.get("总体状态")
        summary["检查统计"] = _level_counts(items, "级别")
        summary["未通过项"] = [
            {"级别": i.get("级别"), "项目": i.get("项目"),
             "证据": i.get("证据"), "下一步": i.get("下一步")}
            for i in items if i.get("级别") in {"FAIL", "NEEDS_REVIEW"}
        ]
    else:
        summary["检查清单"] = "未找到（不能当作通过）"
    return summary


def cmd_list(args) -> int:
    names = pipe_names()
    if args.pid:
        target = PIPE_PREFIX + str(args.pid)
        if target not in names:
            print(f"指定 Pro 进程 {args.pid} 未在运行实例中；当前：{names}")
            return 1
        names = [target]
    if not names:
        print("未发现 Pro 实例（请启动 ArcGIS Pro 并打开插件工作台）")
        return 1
    failed = False
    for n in names:
        try:
            r = unwrap(exchange(n, "get_status", {}, 5), n)
            print(f"{n}\tpid={r.get('process_id')}\t"
                  f"project={r.get('project') or '(未打开工程)'}\t"
                  f"map={r.get('map') or '-'}\tversion={r.get('version')}")
        except (SystemExit, OSError, TimeoutError) as exc:
            failed = True
            print(f"{n}\t错误：{exc}")
    return 1 if failed else 0


def cmd_status(args) -> int:
    status = Bridge(args.pid).load()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_call(args) -> int:
    reply = Bridge(args.pid).call(args.tool, parse_args_json(args.json), args.timeout)
    unwrap(reply, args.tool)
    print(json.dumps(reply.get("result"), ensure_ascii=False, indent=2))
    return 0


def cmd_wait(args) -> int:
    bridge = Bridge(args.pid)
    job = poll_job(bridge, args.job_id, args.timeout, args.interval)
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0 if job.get("status") == "succeeded" else 1


def cmd_callwait(args) -> int:
    bridge = Bridge(args.pid)
    reply = unwrap(bridge.call(args.tool, parse_args_json(args.json), args.timeout), args.tool)
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    job_id = reply.get("job_id")
    if not job_id:
        return 0
    job = poll_job(bridge, job_id, args.timeout, args.interval)
    print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0 if job.get("status") == "succeeded" else 1


def cmd_report(args) -> int:
    summary = summarize_report(Path(args.path))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("状态") != "无法读取" else 1


def cmd_run(args) -> int:
    """一键执行：预检 / 预览 / 交付自检，并直接给摘要。"""
    bridge = Bridge(args.pid)
    if args.action == "delivery":
        arguments = {"out_dir": args.out_dir} if args.out_dir else {}
        tool = "acceptance_delivery"
    else:
        if not args.layer:
            raise SystemExit("check/preview 需要 --layer")
        arguments = {"layer": args.layer}
        if args.use_selection:
            arguments["use_selection"] = True
        elif args.all:
            arguments["use_selection"] = False
        if args.out_dir:
            arguments["out_dir"] = args.out_dir
        if args.action == "check":
            tool = "acceptance_check"
        else:
            tool = "acceptance_preview"
            arguments["confirm"] = True
    reply = unwrap(bridge.call(tool, arguments, args.timeout), tool)
    print(json.dumps(reply, ensure_ascii=False, indent=2))
    job_id = reply.get("job_id")
    if job_id:
        job = poll_job(bridge, job_id, args.timeout, args.interval)
        print(json.dumps(job, ensure_ascii=False, indent=2))
        if job.get("status") != "succeeded":
            return 1
    # 报告只按动作限定候选，避免复用目录时把上一次的旧报告当成本次结果
    report_path = reply.get("report_path")
    names = {"check": ("资料检查.json",), "preview": ("一键成果报告.json",)}.get(args.action, ())
    candidates = []
    if report_path:
        candidates.append(Path(report_path))
    for base in (reply.get("out_dir"), args.out_dir):
        if base:
            candidates.extend(Path(base) / name for name in names)
    summary = None
    for candidate in candidates:
        if candidate.is_file():
            summary = summarize_report(candidate)
            break
    if summary is None and names:
        print(f"警告：{args.action} 未找到 {'/'.join(names)}，不能当作通过。", file=sys.stderr)
        return 1
    if summary is not None:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    status = ((summary or {}).get("检查清单总体状态")
              or (summary or {}).get("状态")
              or reply.get("status"))
    return 1 if status == "FAIL" else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pro AI Bridge 命令行助手")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--pid", type=int, default=None, help="目标 Pro 进程号（可放在子命令后）")
        sp.add_argument("--timeout", type=float, default=900.0)
        sp.add_argument("--interval", type=float, default=2.0)

    s = sub.add_parser("list"); s.set_defaults(func=cmd_list); common(s)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status); common(s)
    s = sub.add_parser("call")
    s.add_argument("tool"); s.add_argument("json", nargs="?", default="",
                                           help="内联 JSON 或 @文件")
    s.set_defaults(func=cmd_call); common(s)
    s = sub.add_parser("wait")
    s.add_argument("job_id")
    s.set_defaults(func=cmd_wait); common(s)
    s = sub.add_parser("callwait")
    s.add_argument("tool"); s.add_argument("json", nargs="?", default="")
    s.set_defaults(func=cmd_callwait); common(s)
    s = sub.add_parser("report")
    s.add_argument("path")
    s.set_defaults(func=cmd_report); common(s)
    s = sub.add_parser("run")
    s.add_argument("action", choices=["check", "preview", "delivery"])
    s.add_argument("--layer", default="")
    group = s.add_mutually_exclusive_group()
    group.add_argument("--use-selection", action="store_true",
                       help="只处理当前选择（工具缺省行为；不传则用工具缺省）")
    group.add_argument("--all", action="store_true",
                       help="处理全部要素并按定义查询（关闭选择范围）")
    s.add_argument("--out-dir", default="", help="成果目录；空则由工具自动新建")
    s.set_defaults(func=cmd_run); common(s)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
