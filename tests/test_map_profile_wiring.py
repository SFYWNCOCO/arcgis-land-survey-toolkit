"""版式装配接线回归：用桩 arcpy 跑真实函数，确认配置真的生效。

若 `_install_courseware_layout` 忽略配置（或图名不走配置），这里会红。
"""
from __future__ import annotations

import importlib.util
import sys
import types

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _load(name, path, monkeypatch):
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Mapping:
    def listLayers(self):
        return []


class _Layout:
    def listElements(self, _kind=None):
        return []


class _Aprx:
    def __init__(self):
        self.deleted, self.imported = [], []

    def listLayouts(self):
        return ["old"]

    def deleteItem(self, item):
        self.deleted.append(item)

    def importDocument(self, path, include_layout=True):
        self.imported.append((path, include_layout))
        frame = types.SimpleNamespace(name="主图框", map=None)
        return types.SimpleNamespace(
            listElements=lambda kind=None: [frame] if kind == "MAPFRAME_ELEMENT" else [],
            name="layout")


def test_configured_pagx_is_the_one_imported(tmp_path, monkeypatch):
    exporter = _load("acceptance.map_export_profile", ROOT / "acceptance" / "map_export.py", monkeypatch)
    custom = tmp_path / "单位版式.pagx"
    custom.write_text("x", encoding="utf-8")
    aprx = _Aprx()
    layout, inset = exporter._install_courseware_layout(
        aprx, _Mapping(), [], {"layout": {"pagx": str(custom)}})
    assert aprx.imported == [(str(custom), True)]
    assert aprx.deleted == ["old"] and inset is None


def test_missing_configured_pagx_stops_instead_of_silent_default(tmp_path, monkeypatch):
    exporter = _load("acceptance.map_export_profile_missing", ROOT / "acceptance" / "map_export.py", monkeypatch)
    aprx = _Aprx()
    with pytest.raises(FileNotFoundError, match="自定义版式不存在"):
        exporter._install_courseware_layout(
            aprx, _Mapping(), [], {"layout": {"pagx": str(tmp_path / "nope.pagx")}})
    assert aprx.deleted == [] and aprx.imported == []


def test_default_template_still_used_without_profile(monkeypatch):
    exporter = _load("acceptance.map_export_default", ROOT / "acceptance" / "map_export.py", monkeypatch)
    aprx = _Aprx()
    exporter._install_courseware_layout(aprx, _Mapping(), [], None)
    assert aprx.imported[0][0].endswith("补充耕地鉴定图_A4.pagx")


def test_accuracy_tool_is_registered_in_main_toolbox():
    import ast
    path = ROOT / "toolboxes" / "SupplementaryCultivatedLandAcceptance.pyt"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    toolbox = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Toolbox")
    init = next(node for node in toolbox.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    tools = next(node for node in init.body if isinstance(node, ast.Assign) and node.targets[0].attr == "tools")
    names = [node.id for node in tools.value.elts]
    assert "ImageryAccuracyCheck" in names
    command = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ImageryAccuracyCheck")
    params = next(node for node in command.body if isinstance(node, ast.FunctionDef) and node.name == "params")
    outputs = [n for n in ast.walk(params) if isinstance(n, ast.keyword) and n.arg == "direction"]
    assert len(outputs) == 2
    assert "build_record" in source and "load_points" in source


def _worker(monkeypatch):
    """按既有桩方式加载 map_worker（名称带 acceptance. 前缀以支持相对导入）。"""
    return _load("acceptance.map_worker_profile_contract", ROOT / "acceptance" / "map_worker.py", monkeypatch)


def test_unit_title_uses_configured_layout_profile(monkeypatch):
    worker = _worker(monkeypatch)
    meta = {"layout_profile": {"layout": {"unit_title": "某单位采样图"}}, "title_unit": "旧图名"}
    title, name = worker.unit_map_title(meta, "U1", None, {})
    assert title == "某单位采样图（U1）"
    assert name.endswith("单元_U1")


def test_unit_title_without_profile_keeps_legacy_text(monkeypatch):
    worker = _worker(monkeypatch)
    title, _ = worker.unit_map_title({"title_unit": "补充耕地鉴定图"}, "U7", None, {})
    assert title == "补充耕地鉴定图（U7）"


def test_source_parcel_mode_uses_parcel_label_with_configured_title(monkeypatch):
    worker = _worker(monkeypatch)
    meta = {"layout_profile": {"layout": {"unit_title": "某单位采样图"}}}
    title, _ = worker.unit_map_title(meta, "U1", "source_parcel", {"U1": "地块31、32"})
    assert title == "某单位采样图（地块31、32）"


def test_overview_title_uses_profile_and_geo_keeps_fixed_name(monkeypatch):
    worker = _worker(monkeypatch)
    meta = {"layout_profile": {"layout": {"overview_title": "某单位总览图"}}, "title": "旧总览"}
    assert worker.overview_map_title("sample", meta) == "某单位总览图"
    assert worker.overview_map_title("geo", meta) == "补充耕地质量鉴定地理位置图"


def test_parcel_sheet_title_uses_configured_layout_profile():
    """Pro 实跑发现：逐图斑图名曾硬编码，配置被忽略。这里锁住源码走配置。"""
    source = (ROOT / "acceptance" / "map_worker.py").read_text(encoding="utf-8")
    assert 'title = "补充耕地质量鉴定采样点位布置图（地块{}）"' not in source
    assert 'title_for(meta.get("layout_profile"), "unit", None' in source


def test_preview_cache_is_not_reused_for_formal_export(monkeypatch):
    """缓存按**实际显示标题**判定；10-31 起预览图默认隐藏【设计预览】前缀。"""
    exporter = _load("acceptance.map_export_reuse_contract", ROOT / "acceptance" / "map_export.py", monkeypatch)
    monkeypatch.setattr(exporter, "verify_export_artifact",
                        lambda path, ext, expected=None: {"status": "PASS", "sha256": "h"})
    prior = {"title": "【设计预览】采样点位布置图（地块P1）", "preview_only": True, "status": "NEEDS_REVIEW",
             "jpg": "a.jpg", "png": "a.png", "pdf": "a.pdf",
             "artifacts": [{"kind": e, "sha256": "h"} for e in ("jpg", "png", "pdf")], "render_key": "K"}
    prior["ink"] = "【设计预览】"
    assert exporter._reusable_export(dict(prior), "K", {"title": "采样点位布置图（地块P1）"}) is False
    # 默认隐藏前缀：目标标题虽带前缀，图面实际不显示，旧图 ink 有前缀 → 不可复用
    assert exporter._reusable_export(dict(prior), "K", {"title": "【设计预览】采样点位布置图（地块P1）"}) is False
    # 显式要求显示前缀时，与实际显示一致才可复用
    assert exporter._reusable_export(
        dict(prior), "K",
        {"title": "【设计预览】采样点位布置图（地块P1）", "presentation": {"show_preview_label": True}},
    ) is True
    assert exporter._reusable_export(dict(prior), "K") is True  # 未给目标时保持旧行为


def test_draft_maps_is_forced_false_for_export_action():
    source = (ROOT / "acceptance" / "oneclick.py").read_text(encoding="utf-8")
    assert 'kwargs["draft_maps"] = task_action == "preview"' in source


def test_render_key_ignores_preview_identity_so_only_one_render_per_sheet(tmp_path, monkeypatch):
    """Pro 实测：refresh 后复用必须与首次渲染同键；键里若混入 preview 身份，修复场景永远无法复用。"""
    exporter = _load("acceptance.map_export_render_key", ROOT / "acceptance" / "map_export.py", monkeypatch)
    plan = {"meta": {"org": "单位"}, "dpi": 100, "filter_fields": {}}
    target = {"name": "图", "title": "采样点位布置图（地块1）", "kind": "sample", "extent": [0, 0, 1, 1]}
    key_a = exporter._render_key(plan, target)
    key_b = exporter._render_key(plan, dict(target, preview_only=True))
    assert key_a == key_b
