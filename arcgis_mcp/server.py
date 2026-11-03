"""arcgis-pro MCP server (stdio).

Talks to ArcGIS Pro's Python/arcpy through a whitelisted worker. It does NOT
attach to the live Pro UI; use it for project inspection, dataset describe,
one-click batch runs, report readback, layout export and test runs.

Register with Claude Code:
    claude mcp add arcgis-pro -- <system python> <this file>

Environment:
    ARCGIS_PRO_PY   Pro Python path (default: standard Pro 3.1 install)
    ARCGIS_MCP_READ_ROOTS   allowed input/read roots, separated by ;
    ARCGIS_MCP_WRITE_ROOTS  allowed output/write roots, separated by ;
    ARCGIS_MCP_ROOTS        legacy read-only root setting
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

TOOLKIT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "worker.py"
if str(TOOLKIT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT))
from arcgis_mcp.path_policy import read_roots as _policy_read_roots
from arcgis_mcp.path_policy import within as _path_within
from arcgis_mcp.path_policy import write_roots as _policy_write_roots

DEFAULT_PRO_PY = Path(r"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe")
PRO_PY = Path(os.environ.get("ARCGIS_PRO_PY") or DEFAULT_PRO_PY)
LOG_DIR = TOOLKIT.parents[1] / "logs"
LOG_PATH = LOG_DIR / "arcgis_mcp.jsonl"

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    _MCP_AVAILABLE = True
except Exception:  # pragma: no cover - mcp 未安装时的降级

    class ToolError(Exception):  # type: ignore[no-redef]
        pass

    class _NoMcp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.name = args[0] if args else "arcgis-pro"

        def tool(self, *args: Any, **kwargs: Any) -> Callable:
            def _deco(fn: Callable) -> Callable:
                return fn
            return _deco

        def run(self, *args: Any, **kwargs: Any) -> None:
            raise ToolError("未安装 mcp 包；请 `pip install \"mcp<2\"`")

    FastMCP = _NoMcp  # type: ignore[assignment]
    _MCP_AVAILABLE = False


def _make_fastmcp() -> Any:
    try:
        return FastMCP("arcgis-pro", mask_error_details=True)
    except TypeError:
        return FastMCP("arcgis-pro")


mcp = _make_fastmcp()


def _tool(read_only: bool, **kwargs: Any) -> Callable:
    annotations = {"readOnlyHint": read_only}
    try:
        return mcp.tool(annotations=annotations, **kwargs)
    except TypeError:
        return mcp.tool(**kwargs)


def _read_roots() -> list[Path]:
    return _policy_read_roots()


def _write_roots() -> list[Path]:
    return _policy_write_roots()


def _roots() -> list[Path]:
    """Compatibility alias for the read scope; it never grants write access."""
    return _read_roots()


def _read_within(path) -> Path:
    return _path_within(path, _read_roots(), "只读", ToolError)


def _write_within(path) -> Path:
    return _path_within(path, _write_roots(), "可写", ToolError)


def _within(path) -> Path:
    """Compatibility alias for read-only path validation."""
    return _read_within(path)


def _log(action: str, status: str, seconds: float, detail: dict | None = None) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {"time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "action": action, "status": status, "seconds": round(seconds, 2)}
        if detail:
            record["detail"] = detail
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _call_worker(action: str, params: dict, timeout: float = 600.0) -> dict:
    if not _MCP_AVAILABLE:
        raise ToolError("mcp 包未安装，无法启动 server")
    if not PRO_PY.is_file():
        raise ToolError(f"找不到 ArcGIS Pro Python：{PRO_PY}；用 ARCGIS_PRO_PY 指定")
    if not WORKER.is_file():
        raise ToolError("找不到 worker：" + str(WORKER))
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="arcgis_mcp_") as folder:
        request_path = Path(folder) / "request.json"
        result_path = Path(folder) / "result.json"
        request_path.write_text(json.dumps({"action": action, "params": params},
                                           ensure_ascii=False), encoding="utf-8")
        process = subprocess.run(
            [str(PRO_PY), "-I", str(WORKER), str(request_path), str(result_path)],
            cwd=str(TOOLKIT), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if not result_path.is_file():
            _log(action, "FAIL", time.time() - started, {"returncode": process.returncode})
            tail = (process.stdout or "") + (process.stderr or "")
            raise ToolError(f"worker 未产出结果：returncode={process.returncode}；尾部：{tail[-2000:]}")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log(action, "FAIL", time.time() - started,
                 {"returncode": process.returncode, "error": str(exc)[:500]})
            raise ToolError("worker 结果文件无法读取：{}".format(exc)) from exc
        if not isinstance(result, dict):
            _log(action, "FAIL", time.time() - started,
                 {"returncode": process.returncode, "error": "result_not_object"})
            raise ToolError("worker 结果必须是 JSON 对象。")
    status = str(result.get("status", "UNKNOWN")).upper()
    failed = process.returncode != 0 or status == "FAIL"
    if failed:
        detail = str(result.get("error") or result.get("stderr") or result.get("stdout") or
                     "worker 状态={}，退出码={}".format(status, process.returncode))[-1200:]
        location = result.get("report_path") or result.get("out_dir") or result.get("path")
        _log(action, "FAIL", time.time() - started,
             {"returncode": process.returncode, "error": detail[-500:], "location": location})
        suffix = "；成果目录：{}".format(location) if location else ""
        raise ToolError("ArcGIS Pro worker 执行失败：{}{}".format(detail, suffix))
    _log(action, status, time.time() - started)
    return result


@_tool(read_only=True)
def workspace_status() -> dict:
    """检查 MCP 依赖、Pro Python、模板、配置、vendor 与最新安装包。"""
    import importlib.util
    templates = TOOLKIT / "templates"
    configs = TOOLKIT / "config"
    dist = sorted((TOOLKIT / "addin" / "dist").glob("*.esriAddInX")) if (TOOLKIT / "addin" / "dist").is_dir() else []
    latest = dist[-1] if dist else None
    checks = {
        "mcp_installed": importlib.util.find_spec("mcp") is not None,
        "pro_python": {"path": str(PRO_PY), "exists": PRO_PY.is_file()},
        "worker": {"path": str(WORKER), "exists": WORKER.is_file()},
        "templates": {name: (templates / name).is_file() for name in (
            "表单_附录D_实地踏勘表.docx", "表单_附录E_土壤样品采集记录表.docx",
            "表单_附录F_补充耕地质量鉴定意见表.docx", "初鉴报告_附录L_模板.docx")},
        "configs": {name: (configs / name).is_file() for name in (
            "conformity_thresholds.json", "grading_parameters.json", "region_counties.json")},
        "vendor_docx": (TOOLKIT / "vendor" / "docx" / "__init__.py").is_file(),
        "latest_package": None,
    }
    if latest:
        checks["latest_package"] = {"path": str(latest), "bytes": latest.stat().st_size,
                                    "sha256": hashlib.sha256(latest.read_bytes()).hexdigest().upper()}
    ok = (checks["mcp_installed"] and checks["pro_python"]["exists"] and checks["worker"]["exists"]
          and all(checks["templates"].values()) and all(checks["configs"].values()))
    return {"status": "PASS" if ok else "NEEDS_REVIEW", "toolkit": str(TOOLKIT),
            "roots": [str(item) for item in _read_roots()],
            "read_roots": [str(item) for item in _read_roots()],
            "write_roots": [str(item) for item in _write_roots()],
            "legacy_roots_read_only": bool(os.environ.get("ARCGIS_MCP_ROOTS") and
                                           not os.environ.get("ARCGIS_MCP_READ_ROOTS")),
            "checks": checks}


@_tool(read_only=True)
def pro_info() -> dict:
    """返回 ArcGIS Pro / arcpy / Python 版本与安装信息。"""
    return _call_worker("pro_info", {}, timeout=120)


@_tool(read_only=True)
def describe_dataset(path: str) -> dict:
    """描述数据集：类型、坐标系、范围、要素数、字段；栅格返回像元与波段。"""
    return _call_worker("describe_dataset", {"path": str(_read_within(path))}, timeout=120)


@_tool(read_only=True)
def list_project(aprx_path: str) -> dict:
    """列出 .aprx 的地图、图层、布局与定义查询。"""
    return _call_worker("list_project", {"aprx": str(_read_within(aprx_path))}, timeout=180)


@_tool(read_only=False)
def run_oneclick(source: str, out_dir: str, options: dict | None = None,
                 allow_write: bool = False) -> dict:
    """运行补充耕地一键流程（写输出目录）。options 走白名单；写操作需 allow_write=True。"""
    if not allow_write:
        raise ToolError("这是写操作：确认输出目录后传 allow_write=True 再调用")
    params = {"source": str(_read_within(source)), "out_dir": str(_write_within(out_dir)),
              "options": dict(options or {})}
    return _call_worker("run_oneclick", params, timeout=3600)


@_tool(read_only=True)
def read_run(out_dir: str, full: bool = False) -> dict:
    """读取一次运行的状态、报告摘要、检查清单与外业表格清单。"""
    return _call_worker("read_run", {"out_dir": str(_read_within(out_dir)), "full": bool(full)},
                        timeout=180)


@_tool(read_only=False)
def render_acceptance_maps(plan_path: str, allow_write: bool = False) -> dict:
    """使用插件相同的验收出图器运行渲染计划，检查真实比例、图层、导出和视觉回执。"""
    if not allow_write:
        raise ToolError('这是写操作：确认出图任务后传allow_write=True')
    return _call_worker('render_acceptance_maps', {'plan_path':str(_read_within(plan_path))},timeout=3600)


@_tool(read_only=False)
def export_layout(aprx_path: str, layout_name: str, out_path: str,
                  format: str = "PDF", dpi: int = 200, allow_write: bool = False) -> dict:
    """导出 .aprx 布局为 PDF/PNG/JPG；写操作需 allow_write=True。"""
    if not allow_write:
        raise ToolError("这是写操作：确认输出路径后传 allow_write=True 再调用")
    suffix = {"PDF": ".pdf", "PNG": ".png", "JPG": ".jpg", "JPEG": ".jpg"}.get(format.upper())
    if not suffix:
        raise ToolError("format 只支持 PDF/PNG/JPG/JPEG")
    target = Path(out_path)
    if target.suffix.lower() != suffix:
        target = target.with_suffix(suffix)
    return _call_worker("export_layout", {
        "aprx": str(_read_within(aprx_path)), "layout": layout_name,
        "out_path": str(_write_within(target)), "dpi": int(dpi)}, timeout=600)


@_tool(read_only=False)
def run_tests(paths: list[str] | None = None, allow_write: bool = False) -> dict:
    """在 Pro Python 下运行工具包 tests/ 的 pytest；会写 pytest 缓存。"""
    if not allow_write:
        raise ToolError("这是写操作（pytest 缓存）：传 allow_write=True 再调用")
    return _call_worker("run_tests", {"paths": list(paths or ["tests"])}, timeout=1800)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
