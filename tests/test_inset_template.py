"""区位小图模板生成：正常追加、重复追加、压整饰元素、越界四种情形都要有确定行为。

只用纯 Python 读 .pagx，不依赖 arcpy，因此可以在任意解释器里跑（出图的 arcpy 段另测）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent / "demo_map" / "compliant" / "补充耕地鉴定图_A4.pagx"


def _load_module():
    path = ROOT / "scripts" / "make_inset_template.py"
    spec = importlib.util.spec_from_file_location("make_inset_template", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load_module()
pytestmark = pytest.mark.skipif(not BASE.exists(), reason="缺基础模板 补充耕地鉴定图_A4.pagx")


def _frames(doc):
    return {e.get("name"): e for e in doc["layoutDefinition"]["elements"] if e.get("type") == "CIMMapFrame"}


def test_add_inset_frame_and_title(tmp_path):
    out = tmp_path / "inset.pagx"
    info = M.build(str(BASE), str(out))
    doc = M.load_pagx(str(out))

    frames = _frames(doc)
    assert M.INSET_FRAME_NAME in frames
    assert M.frame_bbox(frames[M.INSET_FRAME_NAME]) == pytest.approx(M.DEFAULT_RECT)

    # 主图框不能被改动，否则影像与方里网的范围会跟着漂
    base_frames = _frames(M.load_pagx(str(BASE)))
    assert M.frame_bbox(frames["WEBMAP_MAP_FRAME"]) == pytest.approx(
        M.frame_bbox(base_frames["WEBMAP_MAP_FRAME"]))

    titles = {e.get("name"): e for e in doc["layoutDefinition"]["elements"]
              if e.get("graphic", {}).get("text")}
    assert titles[M.INSET_TITLE_NAME]["graphic"]["text"] == "区位示意图"
    assert info["rect_cm"] == list(M.DEFAULT_RECT)

    inset = frames[M.INSET_FRAME_NAME]
    assert inset["graphicFrame"]["backgroundSymbol"]["symbol"]["symbolLayers"][0]["type"] == "CIMSolidFill"


def test_duplicate_inset_is_refused(tmp_path):
    out = tmp_path / "inset.pagx"
    M.build(str(BASE), str(out))
    doc = M.load_pagx(str(out))
    with pytest.raises(ValueError, match="不重复添加"):
        M.add_inset(doc)


def test_rect_over_north_arrow_is_refused():
    """指北针占 x 17.9–19.5 / y 25.1–26.7；压上去箭头会画在区位框上（实测）。"""
    doc = M.load_pagx(str(BASE))
    with pytest.raises(ValueError, match="重叠"):
        M.add_inset(doc, rect=(15.2, 23.0, 19.1, 26.6))


def test_rect_outside_main_frame_is_refused():
    doc = M.load_pagx(str(BASE))
    with pytest.raises(ValueError, match="必须落在主图框"):
        M.add_inset(doc, rect=(1.6, 10.0, 7.8, 14.0))


def test_rect_too_small_is_refused():
    doc = M.load_pagx(str(BASE))
    with pytest.raises(ValueError, match="太小"):
        M.add_inset(doc, rect=(2.0, 24.0, 3.0, 25.0))


def test_collision_check_ignores_margin_annotations():
    """坐标注记贴在框外（N左 x=1.1 右对齐），不得把它们误判成障碍。"""
    doc = M.load_pagx(str(BASE))
    M.add_inset(doc, rect=M.DEFAULT_RECT)   # 不抛异常即通过
