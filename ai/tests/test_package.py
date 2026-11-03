import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockpane_is_registered_in_pro_daml_collection():
    tree = ET.parse(ROOT.parent / "addin/AcceptanceAddin/Config.daml")
    ns = {"d": "http://schemas.esri.com/DADF/Registry"}
    panes = tree.findall(".//d:insertModule/d:dockPanes/d:dockPane", ns)
    assert [p.get("id") for p in panes] == ["AcceptanceAddin_Workspace"]
    assert not tree.findall(".//d:controls/d:dockPane", ns)


def test_one_native_product_links_shared_ai_sources_and_catalog():
    import importlib.util
    toolkit = ROOT.parent
    spec = importlib.util.spec_from_file_location('unified_package', toolkit/'addin/package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = {name: path for path, name in module.entries()}
    assert entries['Install/tools.json'] == ROOT/'tools.json'
    assert not any(name.endswith('ProAiBridge.dll') for name in entries)
    assert not (ROOT/'addin/Config.daml').exists()
    assert not (ROOT/'addin/BridgeModule.cs').exists()
    csproj = (toolkit/'addin/AcceptanceAddin/AcceptanceAddin.csproj').read_text(encoding='utf-8')
    assert 'ai\\addin\\*.cs' in csproj and 'ArcGIS.Desktop.Layouts' in csproj
