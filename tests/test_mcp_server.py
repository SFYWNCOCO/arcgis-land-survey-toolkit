"""arcgis-pro MCP：工具边界、路径守卫、报告摘要与只读/写分离。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "arcgis_mcp"))

import server  # noqa: E402
import worker  # noqa: E402


def test_acceptance_render_plan_requires_write_permission(tmp_path, monkeypatch):
    with pytest.raises(server.ToolError):
        server.render_acceptance_maps(str(tmp_path/'plan.json'))


def test_render_plan_worker_validates_all_destinations_before_render(tmp_path, monkeypatch):
    root=tmp_path/'allowed';root.mkdir()
    monkeypatch.setenv('ARCGIS_MCP_READ_ROOTS',str(root))
    monkeypatch.setenv('ARCGIS_MCP_WRITE_ROOTS',str(root))
    plan=root/'plan.json'
    (root/'a.aprx').write_bytes(b'fixture')
    plan.write_text(json.dumps({'project':str(root/'a.aprx'),'targets':[{'jpg':str(tmp_path/'outside.jpg'),'png':str(root/'a.png'),'pdf':str(root/'a.pdf')}]}),encoding='utf-8')
    with pytest.raises((ValueError, PermissionError, FileNotFoundError)):
        worker.action_render_acceptance_maps({'plan_path':str(plan)})


@pytest.mark.parametrize('unsafe', ['name','imagery_alias'])
def test_render_plan_guards_thumbnail_and_imagery_alias(tmp_path,monkeypatch,unsafe):
    root=tmp_path/'allowed';root.mkdir()
    monkeypatch.setenv('ARCGIS_MCP_READ_ROOTS',str(root));monkeypatch.setenv('ARCGIS_MCP_WRITE_ROOTS',str(root))
    (root/'a.aprx').write_bytes(b'fixture');outside=tmp_path/'outside.tif';outside.write_bytes(b'fixture')
    target={'name':'safe','jpg':str(root/'a.jpg'),'png':str(root/'a.png'),'pdf':str(root/'a.pdf')}
    if unsafe=='name':target['name']='../../outside'
    else:target['imagery']={'output':str(outside)}
    plan=root/'plan.json';plan.write_text(json.dumps({'project':str(root/'a.aprx'),'targets':[target]}),encoding='utf-8')
    with pytest.raises((ValueError,PermissionError)):
        worker.action_render_acceptance_maps({'plan_path':str(plan)})


def test_server_and_worker_paths_exist():
    assert server.WORKER.is_file()
    if not server.PRO_PY.is_file():
        pytest.skip("当前机器未安装 ArcGIS Pro；不能验证 Pro Python 路径")
    assert server.PRO_PY.is_file()
    assert worker.ROOT == ROOT


def test_workspace_status_reports_required_artifacts():
    status = server.workspace_status()
    assert status["status"] in ("PASS", "NEEDS_REVIEW")
    assert status["checks"]["templates"]["表单_附录D_实地踏勘表.docx"] is True
    assert status["checks"]["configs"]["grading_parameters.json"] is True
    assert status["checks"]["vendor_docx"] is True


def test_write_tools_require_explicit_allow_write():
    with pytest.raises(server.ToolError, match="allow_write"):
        server.run_oneclick("x", "y")
    with pytest.raises(server.ToolError, match="allow_write"):
        server.export_layout("x.aprx", "L", "out.pdf")
    with pytest.raises(server.ToolError, match="allow_write"):
        server.run_tests()


def test_path_guard_rejects_outside_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(tmp_path / "out"))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    with pytest.raises(ValueError, match="只读范围"):
        worker._within("C:/Windows/System32/notepad.exe")
    assert worker._within(tmp_path) == tmp_path.resolve()


def test_mcp_path_policy_separates_read_and_write_roots(tmp_path, monkeypatch):
    read_root = tmp_path / "input"
    write_root = tmp_path / "output"
    read_root.mkdir()
    write_root.mkdir()
    read_path = read_root / "parcel.shp"
    write_path = write_root / "result.pdf"
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(read_root))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(write_root))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)

    assert server._read_within(read_path) == read_path.resolve()
    assert server._read_within(write_path) == write_path.resolve()
    assert server._write_within(write_path) == write_path.resolve()
    assert worker._read_within(read_path) == read_path.resolve()
    assert worker._read_within(write_path) == write_path.resolve()
    assert worker._write_within(write_path) == write_path.resolve()
    with pytest.raises(server.ToolError, match="可写"):
        server._write_within(read_path)
    with pytest.raises(ValueError, match="可写"):
        worker._write_within(read_path)


def test_legacy_mcp_roots_remain_read_only(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    path = legacy_root / "existing.aprx"
    monkeypatch.delenv("ARCGIS_MCP_READ_ROOTS", raising=False)
    monkeypatch.delenv("ARCGIS_MCP_WRITE_ROOTS", raising=False)
    monkeypatch.setenv("ARCGIS_MCP_ROOTS", str(legacy_root))

    assert server._read_within(path) == path.resolve()
    assert worker._read_within(path) == path.resolve()
    with pytest.raises(server.ToolError, match="可写"):
        server._write_within(path)
    with pytest.raises(ValueError, match="可写"):
        worker._write_within(path)


def test_server_oneclick_uses_read_scope_for_source_and_write_scope_for_output(tmp_path, monkeypatch):
    read_root = tmp_path / "input"
    write_root = tmp_path / "output"
    read_root.mkdir()
    write_root.mkdir()
    source = read_root / "parcels.shp"
    out_dir = write_root / "run-001"
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(read_root))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(write_root))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    calls = []

    def call_worker(action, params, **kwargs):
        calls.append((action, params, kwargs))
        return {"status": "PASS"}

    monkeypatch.setattr(server, "_call_worker", call_worker)
    assert server.run_oneclick(str(source), str(out_dir), allow_write=True)["status"] == "PASS"
    assert calls[0][1]["source"] == str(source.resolve())
    assert calls[0][1]["out_dir"] == str(out_dir.resolve())
    with pytest.raises(server.ToolError, match="可写"):
        server.run_oneclick(str(source), str(read_root / "not-an-output"), allow_write=True)


def test_server_export_layout_rejects_output_in_read_only_root(tmp_path, monkeypatch):
    read_root = tmp_path / "input"
    write_root = tmp_path / "output"
    read_root.mkdir()
    write_root.mkdir()
    aprx = read_root / "project.aprx"
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(read_root))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(write_root))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    calls = []
    monkeypatch.setattr(server, "_call_worker",
                        lambda action, params, **kwargs: calls.append((action, params)) or {"status": "PASS"})

    with pytest.raises(server.ToolError, match="可写"):
        server.export_layout(str(aprx), "Map", str(read_root / "export.pdf"), allow_write=True)
    server.export_layout(str(aprx), "Map", str(write_root / "export.pdf"), allow_write=True)
    assert calls[0][1]["aprx"] == str(aprx.resolve())
    assert calls[0][1]["out_path"] == str((write_root / "export.pdf").resolve())


def test_worker_oneclick_uses_write_scope_for_output(tmp_path, monkeypatch):
    from acceptance import oneclick

    read_root = tmp_path / "input"
    write_root = tmp_path / "output"
    read_root.mkdir()
    write_root.mkdir()
    source = read_root / "parcels.shp"
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(read_root))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(write_root))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    monkeypatch.setattr(oneclick, "run", lambda *_args, **_kwargs: {"技术状态": "PASS"})

    result = worker.action_run_oneclick({"source": str(source), "out_dir": str(write_root / "run"),
                                         "options": {}})
    assert result["status"] == "PASS"
    with pytest.raises(ValueError, match="可写"):
        worker.action_run_oneclick({"source": str(source), "out_dir": str(read_root / "run"),
                                    "options": {}})


def test_default_mcp_write_scope_does_not_cover_workspace_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("ARCGIS_MCP_READ_ROOTS", raising=False)
    monkeypatch.delenv("ARCGIS_MCP_WRITE_ROOTS", raising=False)
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    parent_path = server.TOOLKIT.parent / "unrelated-project" / "result.pdf"

    with pytest.raises(server.ToolError, match="可写"):
        server._write_within(parent_path)
    with pytest.raises(server.ToolError, match="只读范围"):
        server._read_within(server.TOOLKIT.parents[1] / "unrelated-project")
    status = server.workspace_status()
    assert status["write_roots"]
    assert str(server.TOOLKIT.parent.resolve()) not in status["write_roots"]


def test_worker_rejects_unknown_oneclick_options(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(tmp_path / "out"))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    with pytest.raises(ValueError, match="不支持的 oneclick 参数"):
        worker.action_run_oneclick({"source": str(tmp_path / "a.shp"),
                                    "out_dir": str(tmp_path / "out"),
                                    "options": {"danger": True}})


def test_worker_rejects_tests_outside_tests_dir():
    with pytest.raises(ValueError, match="tests"):
        worker.action_run_tests({"paths": ["../README.md"]})


def test_report_summary_limits_fields():
    report = {"评价单元数": 9, "图斑数": 9, "技术状态": "PASS",
              "外业表格状态": "NEEDS_REVIEW",
              "外业表格": {"状态": "NEEDS_REVIEW", "单元数": 9, "标签": {"count": 36},
                       "未填项": {"U1": ["农业分区"]}, "清单": "x.json"},
              "单元明细": [{"unit": "U1"}], "超长字段": "x" * 10000}
    summary = worker._summarize_report(report)
    assert summary["评价单元数"] == 9
    assert summary["外业表格"]["标签"]["count"] == 36
    assert "单元明细" not in summary


def test_read_run_returns_summary_not_full_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(tmp_path / "out"))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    (tmp_path / "task_state.json").write_text(json.dumps({"status": "COMPLETED"}),
                                              encoding="utf-8")
    (tmp_path / "一键成果报告.json").write_text(
        json.dumps({"评价单元数": 2, "图斑数": 2, "技术状态": "PASS",
                    "单元明细": [{"unit": "U1"}]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "检查清单.json").write_text(json.dumps({"总体状态": "PASS"}), encoding="utf-8")
    result = worker.action_read_run({"out_dir": str(tmp_path)})
    assert result["status"] == "PASS"
    assert result["report"]["summary"]["评价单元数"] == 2
    assert "单元明细" not in result["report"]["summary"]
    assert result["checks"]["总体状态"] == "PASS"
    assert result["summary_html"]["exists"] is False


def test_read_run_full_returns_raw_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCGIS_MCP_READ_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARCGIS_MCP_WRITE_ROOTS", str(tmp_path / "out"))
    monkeypatch.delenv("ARCGIS_MCP_ROOTS", raising=False)
    (tmp_path / "一键成果报告.json").write_text(
        json.dumps({"评价单元数": 1, "单元明细": [{"unit": "U1"}]}, ensure_ascii=False),
        encoding="utf-8")
    result = worker.action_read_run({"out_dir": str(tmp_path), "full": True})
    assert result["report"]["单元明细"][0]["unit"] == "U1"


def test_server_raises_tool_error_when_worker_writes_fail_result(tmp_path, monkeypatch):
    pro_python = tmp_path / "python.exe"
    pro_python.write_bytes(b"stub")
    worker_script = tmp_path / "worker.py"
    worker_script.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(server, "_MCP_AVAILABLE", True)
    monkeypatch.setattr(server, "PRO_PY", pro_python)
    monkeypatch.setattr(server, "WORKER", worker_script)
    monkeypatch.setattr(server, "_log", lambda *_args, **_kwargs: None)

    def write_failed_result(command, **_kwargs):
        Path(command[-1]).write_text(json.dumps({"status": "FAIL", "error": "ValueError: invalid input"}),
                                     encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", write_failed_result)

    with pytest.raises(server.ToolError, match="invalid input"):
        server._call_worker("run_tests", {}, timeout=1)
