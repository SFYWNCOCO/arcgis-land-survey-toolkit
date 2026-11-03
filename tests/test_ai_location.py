"""The complete AI component must be buildable from inside the toolkit."""
import importlib.util
import glob
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ai_sources_catalog_and_clients_are_owned_by_toolkit():
    for name in ("addin/MapActions.cs", "tools.json", "mcp_server.py", "pro_bridge_cli.py", "tests/CoreHarness.cs"):
        assert (ROOT / "ai" / name).is_file(), name


def test_native_build_references_resolve_within_toolkit():
    path = ROOT / "addin/AcceptanceAddin/AcceptanceAddin.csproj"
    tree = ET.parse(path)
    for item in tree.iter():
        include = item.get("Include", "")
        if "pro_ai_bridge" in include:
            raise AssertionError("Native build still depends on the sibling project")
        if item.tag in ("Compile", "Content") and "ai\\" in include:
            matches = glob.glob(str(path.parent / include.replace("\\", "/")))
            assert matches, include
            assert all(Path(candidate).resolve().is_relative_to(ROOT.resolve()) for candidate in matches)


def test_ai_harness_uses_the_same_native_project():
    path = ROOT / "build.py"
    spec = importlib.util.spec_from_file_location("ai_build_location", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.UNIFIED == ROOT / "addin"
