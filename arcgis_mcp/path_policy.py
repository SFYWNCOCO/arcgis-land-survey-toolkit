"""Shared read/write path policy for the MCP server and Pro worker."""
from __future__ import annotations

import os
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
# 默认只放行工具包目录；项目数据目录由使用方通过环境变量显式配置。
DEFAULT_READ_ROOTS = (TOOLKIT_ROOT,)
DEFAULT_WRITE_ROOTS = (TOOLKIT_ROOT / "out",)


def _parse_roots(raw: str | None, defaults: tuple[Path, ...]) -> list[Path]:
    values = [item.strip() for item in (raw or "").replace(";", os.pathsep).split(os.pathsep)
              if item.strip()]
    paths = [Path(item).expanduser().resolve() for item in values] if values else [
        path.resolve() for path in defaults]
    result = []
    for path in paths:
        if path not in result:
            result.append(path)
    return result


def write_roots(environ=None) -> list[Path]:
    env = os.environ if environ is None else environ
    return _parse_roots(env.get("ARCGIS_MCP_WRITE_ROOTS"), DEFAULT_WRITE_ROOTS)


def read_roots(environ=None) -> list[Path]:
    env = os.environ if environ is None else environ
    configured = env.get("ARCGIS_MCP_READ_ROOTS")
    if not (configured or "").strip():
        configured = env.get("ARCGIS_MCP_ROOTS")  # legacy setting: read access only
    roots = _parse_roots(configured, DEFAULT_READ_ROOTS)
    for root in write_roots(env):
        if root not in roots:
            roots.append(root)
    return roots


def within(path, roots, scope: str, error_type=ValueError) -> Path:
    target = Path(path).expanduser().resolve()
    for root in roots:
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    setting = "ARCGIS_MCP_WRITE_ROOTS" if scope == "可写" else "ARCGIS_MCP_READ_ROOTS"
    raise error_type("路径不在 MCP {}范围内：{}；请检查 {}".format(scope, target, setting))
