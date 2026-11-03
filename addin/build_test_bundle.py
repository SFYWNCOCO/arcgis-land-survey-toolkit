"""Build a portable Add-In + offline/live MCP test bundle without project data."""
from __future__ import annotations

import hashlib
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ADDIN = Path(__file__).resolve().parent
TOOLKIT = ADDIN.parent
DAML = ADDIN / "AcceptanceAddin" / "Config.daml"
SCRIPT_NAMES = {
    "setup_quality_gdb.py", "delivery_package.py", "build_field_forms.py",
    "evaluate_acceptance.py", "verify_rule_configs.py",
}
RUNTIME_DIRS = ("acceptance", "arcgis_mcp", "toolboxes", "config", "templates", "vendor")
TOOLBOX_METADATA = {"GridSampling.pyt.xml", "SupplementaryCultivatedLandAcceptance.pyt.xml"}


def _is_runtime_file(area: str, path: Path) -> bool:
    if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
        return False
    name = path.name
    if area == "acceptance":
        return path.suffix == ".py"
    if area == "arcgis_mcp":
        return path.suffix == ".py" or name == "README.md"
    if area == "toolboxes":
        # Toolbox-level metadata has real content. ArcGIS-generated per-tool
        # .pyt.xml stubs are created at import time and are not runtime assets.
        return name.endswith(".pyt") or name in TOOLBOX_METADATA
    if area == "config":
        return path.suffix in (".json", ".csv")
    if area == "templates":
        return path.is_file()
    if area == "vendor":
        return path.is_file()
    if area == "scripts":
        return name in SCRIPT_NAMES
    if area == "tests":
        return name in {"test_mcp_server.py", "test_live_mcp_server.py"}
    return False


def runtime_entries(toolkit: Path = TOOLKIT) -> list[tuple[Path, str]]:
    """Return only code/runtime assets; user data, outputs and caches are excluded."""
    entries = []
    for area in RUNTIME_DIRS + ("scripts", "tests"):
        root = toolkit / area
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and _is_runtime_file(area, path):
                rel = path.relative_to(toolkit).as_posix()
                entries.append((path, "MCPRuntime/" + rel))
    return entries


def _version() -> str:
    for node in ET.parse(DAML).iter():
        if node.tag.split("}")[-1] == "AddInInfo":
            value = node.get("version")
            if value:
                return value
    raise ValueError("Config.daml 缺少 AddInInfo.version")


def build_bundle(addin_path: Path, readme_path: Path, output_path: Path | None = None,
                 toolkit: Path = TOOLKIT) -> dict:
    addin_path = Path(addin_path).resolve()
    readme_path = Path(readme_path).resolve()
    if not addin_path.is_file() or addin_path.suffix.lower() != ".esriaddinX".lower():
        raise ValueError("必须提供现存的 .esriAddInX 文件")
    if not readme_path.is_file():
        raise ValueError("现场测试说明文件不存在：" + str(readme_path))
    source_entries = [(addin_path, "AddIn/" + addin_path.name),
                      (readme_path, "README-现场测试.md")]
    source_entries.extend(runtime_entries(Path(toolkit).resolve()))
    names = [arc for _, arc in source_entries]
    if len(names) != len(set(names)):
        raise ValueError("测试包包含重复归档路径")
    required = {
        "README-现场测试.md",
        "MCPRuntime/arcgis_mcp/server.py",
        "MCPRuntime/arcgis_mcp/worker.py",
        "MCPRuntime/arcgis_mcp/path_policy.py",
        "MCPRuntime/arcgis_mcp/live_server.py",
        "MCPRuntime/tests/test_mcp_server.py",
        "MCPRuntime/tests/test_live_mcp_server.py",
    }
    missing = sorted(required - set(names))
    if missing:
        raise ValueError("测试包缺少运行文件：" + "、".join(missing))

    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = ADDIN / "dist" / "采样助手_{}_完整MCP测试包_{}.zip".format(_version(), stamp)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checksums = []
    with zipfile.ZipFile(output_path, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arc in source_entries:
            payload = source.read_bytes()
            archive.writestr(arc, payload)
            checksums.append("{}  {}".format(hashlib.sha256(payload).hexdigest().upper(), arc))
        archive.writestr("SHA256SUMS.txt", "\n".join(checksums) + "\n")

    with zipfile.ZipFile(output_path) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError("测试包 CRC 校验失败：" + bad)
        if len(archive.namelist()) != len(source_entries) + 1:
            raise ValueError("测试包归档项数量不符")
        for source, arc in source_entries:
            if hashlib.sha256(archive.read(arc)).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise ValueError("测试包内容与源文件不一致：" + arc)
    return {"path": str(output_path), "entries": len(source_entries) + 1,
            "bytes": output_path.stat().st_size,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest().upper()}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addin", type=Path)
    parser.add_argument("readme", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build_bundle(args.addin, args.readme, args.output))
