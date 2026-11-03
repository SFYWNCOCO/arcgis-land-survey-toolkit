"""地形部位归并表的纯 Python 用例。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import terrain as T  # noqa: E402
from acceptance import workflow as W  # noqa: E402


def test_mapping_accepts_only_the_twelve_classes(tmp_path):
    path = tmp_path / "地貌归并.json"
    path.write_text(json.dumps({"河漫滩": "平原低阶", "一级阶地": "平原低阶"},
                               ensure_ascii=False), encoding="utf-8")
    mapping = T.load_mapping(path)
    assert mapping["河漫滩"] == "平原低阶"
    assert T.normalize("河漫滩", mapping) == "平原低阶"
    assert T.normalize("未知单元", mapping) == "未知单元"


def test_mapping_rejects_target_outside_guide_classes(tmp_path):
    path = tmp_path / "地貌归并.json"
    path.write_text(json.dumps({"河漫滩": "河滩地"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="12 类"):
        T.load_mapping(path)


def test_workflow_finds_mapping_next_to_source(tmp_path):
    source = tmp_path / "图斑.shp"
    source.write_text("", encoding="utf-8")
    assert W._load_terrain_mapping(str(source), None) == ({}, None)
    (tmp_path / "地貌归并.json").write_text(json.dumps({"塬": "塬"}, ensure_ascii=False),
                                             encoding="utf-8")
    mapping, path = W._load_terrain_mapping(str(source), None)
    assert mapping == {"塬": "塬"} and path.endswith("地貌归并.json")
    explicit = tmp_path / "自定义.json"
    explicit.write_text(json.dumps({"丘顶": "丘陵上部"}, ensure_ascii=False), encoding="utf-8")
    mapping, path = W._load_terrain_mapping(str(source), {"landform": {"mapping": str(explicit)}})
    assert mapping == {"丘顶": "丘陵上部"} and path == str(explicit)
