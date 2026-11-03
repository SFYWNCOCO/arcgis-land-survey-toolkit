"""出图模板（.pagx）与版式标题的可配置入口。

版式可配部分：版式文件路径、总览图名、单元图名（含是否带单元号）。
落位、图号/编制单位/制图人/审核人/高程基准已经由 GRID_SAMPLING 工具箱参数传入，这里不重复。
配置格式：
    {"layout": {"pagx": "D:/templates/某单位版式.pagx",
                "overview_title": "……采样点位布置图",
                "unit_title": "……采样点位图",
                "title_with_unit": true}}
单元标题带编号时，编号插在结尾括号前（"图名（U1）"），不带时只写单元图名。
"""
from __future__ import annotations

import json
import os

DEFAULT_PAGX = ("补充耕地鉴定图_A4_区位.pagx", "补充耕地鉴定图_A4.pagx")


def load_profile(path=None):
    """读版式配置；未给路径时返回空配置（走默认版式）。字段类型不对直接报错。"""
    if not path:
        return {}
    with open(path, encoding="utf-8-sig") as stream:
        try:
            data = json.load(stream)
        except json.JSONDecodeError as exc:
            raise ValueError("版式配置不是有效 JSON：{}".format(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("版式配置顶层必须是对象")
    layout = data.get("layout") or {}
    if not isinstance(layout, dict):
        raise ValueError("版式配置 layout 必须是对象")
    allowed = {"pagx", "overview_title", "unit_title", "title_with_unit"}
    unknown = sorted(set(layout) - allowed)
    if unknown:
        raise ValueError("版式配置含未知字段：{}（可用：{}）".format("、".join(unknown), "、".join(sorted(allowed))))
    for key in ("pagx", "overview_title", "unit_title"):
        if key in layout and not isinstance(layout[key], str):
            raise ValueError("版式配置 {} 必须是字符串".format(key))
    if "title_with_unit" in layout and not isinstance(layout["title_with_unit"], bool):
        raise ValueError("版式配置 title_with_unit 必须是布尔值")
    return {"layout": dict(layout)}


def resolve_pagx(profile, has_inset, templates_dir):
    """返回要导入的 .pagx 绝对路径；自定义版式必须存在，默认版式缺失也报错。"""
    custom = ((profile or {}).get("layout") or {}).get("pagx")
    if custom:
        path = os.path.abspath(os.path.expanduser(custom))
        if not os.path.isfile(path):
            raise FileNotFoundError("自定义版式不存在：" + path)
        if os.path.splitext(path)[1].lower() != ".pagx":
            raise ValueError("自定义版式必须是 .pagx 文件：" + path)
        return path
    name = DEFAULT_PAGX[0] if has_inset else DEFAULT_PAGX[1]
    path = os.path.abspath(os.path.join(templates_dir, name))
    if not os.path.isfile(path):
        raise FileNotFoundError("缺少课件版式模板：" + path)
    return path


def title_for(profile, kind, unit_id=None, fallback=None):
    """总览/单元图名。kind=overview 用 overview_title；kind=unit 用 unit_title。

    unit_id 只在 title_with_unit 为真（默认真）且给了单元号时插入括号。
    """
    layout = (profile or {}).get("layout") or {}
    if kind not in ("overview", "unit"):
        raise ValueError("kind 必须是 overview 或 unit")
    text = layout.get("overview_title" if kind == "overview" else "unit_title") or fallback or ""
    text = str(text).strip()
    if unit_id is None or not layout.get("title_with_unit", True):
        return text
    unit_text = str(unit_id).strip()
    if not unit_text:
        raise ValueError("单元号为空白，不能拼进图名")
    if text.endswith("）"):
        head, sep, _bracket = text[:-1].rpartition("（")
        if sep:
            return "{}（{}）".format(head, unit_text)
    return "{}（{}）".format(text, unit_text)
