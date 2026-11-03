from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addin"))

from build_test_bundle import build_bundle, runtime_entries  # noqa: E402


def _minimal_runtime(root: Path) -> None:
    required = (
        "acceptance/oneclick.py",
        "arcgis_mcp/server.py",
        "arcgis_mcp/worker.py",
        "arcgis_mcp/path_policy.py",
        "arcgis_mcp/live_server.py",
        "arcgis_mcp/README.md",
        "toolboxes/GridSampling.pyt",
        "toolboxes/GridSampling.pyt.xml",
        "toolboxes/GridSampling.GridSamplingOneClick.pyt.xml",
        "config/grading_parameters.json",
        "templates/form.docx",
        "vendor/docx/__init__.py",
        "scripts/build_field_forms.py",
        "tests/test_mcp_server.py",
        "tests/test_live_mcp_server.py",
        "testdata/private_project.shp",
        "out/private_output.pdf",
    )
    for rel in required:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode("utf-8"))


def test_runtime_entries_include_mcp_and_exclude_project_data(tmp_path):
    _minimal_runtime(tmp_path)
    entries = {arc for _, arc in runtime_entries(tmp_path)}

    assert "MCPRuntime/arcgis_mcp/server.py" in entries
    assert "MCPRuntime/arcgis_mcp/live_server.py" in entries
    assert "MCPRuntime/acceptance/oneclick.py" in entries
    assert "MCPRuntime/tests/test_mcp_server.py" in entries
    assert "MCPRuntime/tests/test_live_mcp_server.py" in entries
    assert "MCPRuntime/toolboxes/GridSampling.pyt.xml" in entries
    assert "MCPRuntime/toolboxes/GridSampling.GridSamplingOneClick.pyt.xml" not in entries
    assert not any("testdata" in name or name.startswith("MCPRuntime/out/") for name in entries)


def test_build_bundle_verifies_every_included_file_and_checksum(tmp_path):
    runtime = tmp_path / "runtime"
    _minimal_runtime(runtime)
    addin = tmp_path / "candidate.esriAddInX"
    addin.write_bytes(b"addin-test-payload")
    readme = tmp_path / "README.md"
    readme.write_text("仅供隔离测试", encoding="utf-8")
    output = tmp_path / "package.zip"

    result = build_bundle(addin, readme, output, toolkit=runtime)

    assert result["path"] == str(output)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "AddIn/candidate.esriAddInX" in archive.namelist()
        assert "MCPRuntime/arcgis_mcp/path_policy.py" in archive.namelist()
        assert "MCPRuntime/tests/test_live_mcp_server.py" in archive.namelist()
        checksums = archive.read("SHA256SUMS.txt").decode("utf-8").splitlines()
        for line in checksums:
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest().upper() == digest
