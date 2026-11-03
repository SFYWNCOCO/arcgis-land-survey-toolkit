"""出图版式配置回归；系统 Python 3.12 可运行，不依赖 arcpy。"""
from __future__ import annotations

import json

import pytest

from acceptance import map_profile as M


def _profile(tmp_path, layout):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"layout": layout}, ensure_ascii=False), encoding="utf-8")
    return M.load_profile(path)


def test_missing_profile_returns_empty_and_default_pagx_is_resolved(tmp_path):
    assert M.load_profile(None) == {}
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "补充耕地鉴定图_A4.pagx").write_text("x", encoding="utf-8")
    assert M.resolve_pagx({}, False, str(templates)).endswith("补充耕地鉴定图_A4.pagx")
    with pytest.raises(FileNotFoundError, match="缺少课件版式模板"):
        M.resolve_pagx({}, True, str(templates))


def test_custom_pagx_must_exist_and_be_pagx(tmp_path):
    profile = _profile(tmp_path, {"pagx": str(tmp_path / "nope.pagx")})
    with pytest.raises(FileNotFoundError, match="自定义版式不存在"):
        M.resolve_pagx(profile, False, str(tmp_path))
    wrong = tmp_path / "x.pag"
    wrong.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="必须是 .pagx"):
        M.resolve_pagx(_profile(tmp_path, {"pagx": str(wrong)}), False, str(tmp_path))
    good = tmp_path / "单位版式.pagx"
    good.write_text("x", encoding="utf-8")
    assert M.resolve_pagx(_profile(tmp_path, {"pagx": str(good)}), True, str(tmp_path)) == str(good)


def test_unknown_or_wrong_typed_fields_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="未知字段：titel"):
        _profile(tmp_path, {"titel": "x"})
    with pytest.raises(ValueError, match="必须是布尔值"):
        _profile(tmp_path, {"title_with_unit": "yes"})
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="不是有效 JSON"):
        M.load_profile(bad)


def test_unit_title_inserts_unit_before_trailing_parenthesis():
    profile = {"layout": {"unit_title": "补充耕地质量鉴定采样点位图（实测）"}}
    assert M.title_for(profile, "unit", "U1") == "补充耕地质量鉴定采样点位图（U1）"
    plain = {"layout": {"unit_title": "采样点位图"}}
    assert M.title_for(plain, "unit", "U1") == "采样点位图（U1）"


def test_unit_title_can_drop_unit_number_and_falls_back():
    profile = {"layout": {"unit_title": "采样点位图", "title_with_unit": False}}
    assert M.title_for(profile, "unit", "U1") == "采样点位图"
    assert M.title_for({}, "overview", fallback="默认总览图") == "默认总览图"
    with pytest.raises(ValueError, match="单元号为空白"):
        M.title_for({"layout": {"unit_title": "图"}}, "unit", " ")
    with pytest.raises(ValueError, match="overview 或 unit"):
        M.title_for({}, "sheet")
