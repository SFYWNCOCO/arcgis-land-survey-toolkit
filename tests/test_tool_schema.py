"""ai/tools.json 工具目录 schema 校验的契约测试。

正向：真实工具目录全量通过，FAIL/WARN 均为 0。
负向：逐项破坏一条规则，断言失败原因（Finding.code）与工具名，而不只断言布尔。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_tool_schema as vts  # noqa: E402


@pytest.fixture()
def catalog():
    return vts.load_catalog(vts.DEFAULT_CATALOG)


def _failure(report, code: str):
    matches = [item for item in report.failures if item.code == code]
    assert matches, (
        f"缺少 {code}，实际失败为：{[item.code for item in report.failures]}"
    )
    return matches[0]


def _warning(report, code: str):
    matches = [item for item in report.warnings if item.code == code]
    assert matches, (
        f"缺少 {code}，实际警告为：{[item.code for item in report.warnings]}"
    )
    return matches[0]


def _writable_confirm_tool(data):
    for tool in data:
        if (tool.get("annotations") or {}).get("readOnlyHint") is not False:
            continue
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        if "confirm" in properties:
            return tool
    raise AssertionError("真实工具目录里找不到带 confirm 的写工具")


# ---------------------------------------------------------------- 正向


def test_real_catalog_passes_all_rules(catalog):
    report = vts.validate_file(vts.DEFAULT_CATALOG)
    assert report.total == len(catalog)
    assert report.failures == []
    assert report.ok is True
    assert report.warnings == []


def test_mcp_target_tools_also_require_context_and_readonly(catalog):
    mcp_tools = [
        tool
        for tool in catalog
        if (tool.get("_meta") or {}).get(vts.TARGET_KEY) == "mcp"
    ]
    assert {tool["name"] for tool in mcp_tools} == {
        "list_pro_sessions",
        "select_pro_session",
    }
    report = vts.validate_tools(mcp_tools)
    assert report.failures == []
    assert report.warnings == []


# ---------------------------------------------------------------- 负向：FAIL


def test_duplicate_name_fails_with_name(catalog):
    data = copy.deepcopy(catalog)
    data[1]["name"] = data[0]["name"]
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["name_duplicate"]
    finding = _failure(report, "name_duplicate")
    assert finding.severity == vts.FAIL
    assert data[0]["name"] in finding.message


def test_missing_context_fails_with_tool_name(catalog):
    data = copy.deepcopy(catalog)
    data[0]["_meta"].pop(vts.CONTEXT_KEY)
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["context_missing"]
    finding = _failure(report, "context_missing")
    assert data[0]["name"] in finding.message
    assert vts.CONTEXT_KEY in finding.message


def test_non_object_input_schema_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["inputSchema"]["type"] = "array"
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["schema_not_object"]
    finding = _failure(report, "schema_not_object")
    assert data[0]["name"] in finding.message
    assert "object" in finding.message


def test_required_field_missing_from_properties_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["inputSchema"]["required"] = ["page", "not_a_property"]
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["required_missing_property"]
    finding = _failure(report, "required_missing_property")
    assert "not_a_property" in finding.message
    assert data[0]["name"] in finding.message


def test_non_bool_readonly_hint_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["annotations"]["readOnlyHint"] = "true"
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["readonly_not_bool"]
    finding = _failure(report, "readonly_not_bool")
    assert data[0]["name"] in finding.message
    assert "bool" in finding.message


def test_invalid_draft_schema_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["inputSchema"] = {
        "type": "object",
        "properties": {"x": {"type": "not-a-type"}},
    }
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["schema_invalid"]
    finding = _failure(report, "schema_invalid")
    assert data[0]["name"] in finding.message
    assert "Draft 2020-12" in finding.message


def test_invalid_name_pattern_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["name"] = "Bad-Name"
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["name_invalid"]
    finding = _failure(report, "name_invalid")
    assert "Bad-Name" in finding.message
    assert "^[a-z][a-z0-9_]*$" in finding.message


def test_empty_description_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["description"] = "   "
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["description_missing"]
    finding = _failure(report, "description_missing")
    assert data[0]["name"] in finding.message


def test_enum_element_type_mismatch_fails(catalog):
    data = copy.deepcopy(catalog)
    data[0]["inputSchema"]["properties"]["page"] = {
        "type": "string",
        "enum": ["assistant", 1],
    }
    report = vts.validate_tools(data)
    assert [item.code for item in report.failures] == ["enum_type_mismatch"]
    finding = _failure(report, "enum_type_mismatch")
    assert data[0]["name"] in finding.message
    assert "'page'" in finding.message
    assert "1" in finding.message


def test_non_array_catalog_fails():
    report = vts.validate_tools({"name": "open_acceptance_workspace"})
    assert [item.code for item in report.failures] == ["catalog_not_array"]
    assert report.total == 0
    assert "<catalog>" in _failure(report, "catalog_not_array").tool


# ---------------------------------------------------------------- 负向：WARN（confirm 约定）


def test_write_tool_confirm_enum_not_true_is_warn(catalog):
    data = copy.deepcopy(catalog)
    tool = _writable_confirm_tool(data)
    tool["inputSchema"]["properties"]["confirm"] = {"type": "boolean", "enum": [False]}
    report = vts.validate_tools(data)
    assert report.failures == []
    warning = _warning(report, "confirm_enum_not_true")
    assert warning.severity == vts.WARN
    assert tool["name"] in warning.message
    assert "confirm.enum" in warning.message


def test_write_tool_confirm_type_not_bool_is_warn(catalog):
    data = copy.deepcopy(catalog)
    tool = _writable_confirm_tool(data)
    tool["inputSchema"]["properties"]["confirm"] = {"type": "string", "enum": [True]}
    report = vts.validate_tools(data)
    assert report.failures == []
    warning = _warning(report, "confirm_type_not_boolean")
    assert warning.severity == vts.WARN
    assert tool["name"] in warning.message
    assert "boolean" in warning.message


# ---------------------------------------------------------------- CLI 契约


def test_check_mode_passes_real_catalog(capsys):
    assert vts.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "结论：PASS" in output
    assert "FAIL 0" in output


def test_check_mode_returns_one_on_failures(tmp_path, capsys, catalog):
    data = copy.deepcopy(catalog)
    data[1]["name"] = data[0]["name"]
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert vts.main(["--check", "--catalog", str(catalog_path)]) == 1
    output = capsys.readouterr().out
    assert "name_duplicate" in output


def test_json_mode_is_machine_readable(capsys):
    assert vts.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["failure_count"] == 0
    assert payload["total"] > 0
    assert payload["failures"] == []
    assert payload["warnings"] == []
