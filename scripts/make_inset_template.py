"""在成果图模板上加一幅「区位小图」图框（纯 Python 改 .pagx，不需要 arcpy）。

为什么这样做：ArcGIS Pro 3.1.5 的 `Layout.setDefinition()` 混入新元素会静默硬崩（退出码
4294967295），`layout.createMapFrame()` 在这套安装里也不可用。可复现的做法是**在 import
之前**把 .pagx 的 JSON 改好——复制一个已有的 CIMMapFrame 元素，改名字与框位，再加一条图名
文本，然后交给 `aprx.importDocument()`；运行时再用 `frame.map = inset_map` 把它绑到第二幅
地图上。

用法：
    python make_inset_template.py --out temp/inset.pagx
    python make_inset_template.py --base ...\\补充耕地鉴定图_A4.pagx --out ... --rect 13.2,21.8,19.4,26.4

约定：rect 用**页面厘米**、原点在左下角，口径与 .pagx/layout API 一致。
"""
from __future__ import annotations

import argparse
import copy
import json
import os

MAIN_FRAME_NAMES = ("WEBMAP_MAP_FRAME", "主图框")
INSET_FRAME_NAME = "区位图框"
INSET_TITLE_NAME = "区位图名"
PAGE_CM = (21.0, 29.7)          # A4 纵向
# 左上角：右上角被指北针（x 17.9–19.5 / y 25.1–26.7）占着，区块会压上去。
DEFAULT_RECT = (1.6, 21.4, 7.8, 26.0)
OUTER_FRAME_NAME = "外图廓"


def load_pagx(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8-sig"))


def save_pagx(doc, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    return path


def frame_bbox(element):
    """从 CIMMapFrame 的 frame.rings[0] 取 (x0, y0, x1, y1)。"""
    ring = (element.get("frame") or {}).get("rings") or []
    if not ring:
        raise ValueError("元素 {} 没有 frame.rings".format(element.get("name")))
    xs = [pt[0] for pt in ring[0]]
    ys = [pt[1] for pt in ring[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _set_bbox(element, rect):
    x0, y0, x1, y1 = rect
    element["frame"] = {"rings": [[[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]]}
    element["rotationCenter"] = {"x": (x0 + x1) / 2.0, "y": (y0 + y1) / 2.0}
    return element


def _solid(color, alpha=100):
    return {"type": "CIMRGBColor", "values": list(color)[:3] + [alpha]}


def _stroke_layer(width, color, offset=None):
    layer = {"type": "CIMSolidStroke", "enable": True, "capStyle": "Butt",
             "joinStyle": "Miter", "lineStyle3D": "Strip", "miterLimit": 10,
             "width": width, "color": _solid(color)}
    if offset is not None:
        layer["offset"] = offset
    return layer


def _restyle_inset_frame(element, rect, border_width=0.8):
    """白底 + 黑框：区位小图要盖住底图，否则县界与影像纹理混在一起读不出来。"""
    _set_bbox(element, rect)
    graphic = element.setdefault("graphicFrame", {"type": "CIMGraphicFrame"})
    graphic["backgroundSymbol"] = {"type": "CIMSymbolReference", "symbol": {
        "type": "CIMPolygonSymbol", "symbolLayers": [
            {"type": "CIMSolidFill", "enable": True, "color": _solid((255, 255, 255))}]}}
    graphic["borderSymbol"] = {"type": "CIMSymbolReference", "symbol": {
        "type": "CIMLineSymbol", "symbolLayers": [_stroke_layer(border_width, (0, 0, 0))]}}
    graphic["shadowSymbol"] = {"type": "CIMSymbolReference", "symbol": {
        "type": "CIMPolygonSymbol", "symbolLayers": [_stroke_layer(1, (0, 0, 0), 0)]}}
    view = element.get("view") or {}
    camera = view.get("camera") or {}
    camera.update({"scale": 15000000, "x": 108.0, "y": 23.8})
    view["camera"] = camera
    element["view"] = view
    return element


def text_element(name, text, x, y, size=7.0, font="SimHei", align="Left"):
    """照抄 .pagx 既有结构；anchor 与 horizontalAlignment 必须匹配，否则文字溢出被裁。"""
    anchor = {"Left": "TopLeftCorner", "Center": "TopMidPoint", "Right": "TopRightCorner"}[align]
    return {
        "type": "CIMGraphicElement", "anchor": anchor, "name": name, "visible": True,
        "rotationCenter": {"x": x, "y": y}, "lockedAspectRatio": False,
        "graphic": {"type": "CIMTextGraphic", "symbol": {"type": "CIMSymbolReference",
            "symbol": {"type": "CIMTextSymbol", "blockProgression": "TTB",
                       "compatibilityMode": True, "fontFamilyName": font,
                       "fontType": "Unspecified", "height": size, "hinting": "Default",
                       "horizontalAlignment": align, "kerning": True, "letterWidth": 100,
                       "textDirection": "LTR", "verticalAlignment": "Top",
                       "symbol": {"type": "CIMPolygonSymbol", "symbolLayers": [
                           {"type": "CIMSolidFill", "enable": True, "color": _solid((0, 0, 0))}]}},
            "blendingMode": "Alpha"},
            "text": text, "shape": {"x": x, "y": y}}}


def find_main_frame(elements):
    for element in elements:
        if element.get("type") == "CIMMapFrame" and element.get("name") in MAIN_FRAME_NAMES:
            return element
    raise ValueError("模板里找不到主图框（CIMMapFrame {}）".format("/".join(MAIN_FRAME_NAMES)))


def _element_bbox(element):
    """整饰元素的占位框：有 frame 用 frame（指北针/图例/比例尺），纯文本按锚点取 0.6cm 小框。

    文本用点式小框而不是 2cm 宽框：坐标注记（N左/E上）是右对齐贴在框外的单行文字，
    按宽度外推会误报"压住注记"，把区位框从唯一空位挤走。
    """
    ring = (element.get("frame") or {}).get("rings") or []
    if ring:
        xs = [pt[0] for pt in ring[0]]
        ys = [pt[1] for pt in ring[0]]
        return min(xs), min(ys), max(xs), max(ys)
    shape = (element.get("graphic") or {}).get("shape") or {}
    if "x" in shape and "y" in shape:
        return shape["x"] - 0.3, shape["y"] - 0.3, shape["x"] + 0.3, shape["y"] + 0.3
    return None


def _overlap(a, b, tol=1e-6):
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol or
                a[3] <= b[1] + tol or b[3] <= a[1] + tol)


def check_collisions(elements, rect, skip=(INSET_FRAME_NAME, INSET_TITLE_NAME,
                                           "WEBMAP_MAP_FRAME", OUTER_FRAME_NAME)):
    """区位框不能压到指北针/图例/比例尺/注记。实测右上角被指北针占着，压上去箭头会画在区块上。"""
    hits = []
    for element in elements:
        if element.get("name") in skip or element.get("visible") is False:
            continue
        box = _element_bbox(element)
        if box and _overlap(tuple(rect), box):
            hits.append((element.get("name"), [round(v, 2) for v in box]))
    if hits:
        raise ValueError("区位框 {} 与整饰元素重叠：{}".format(
            tuple(rect), "、".join("{} {}".format(n, b) for n, b in hits)))
    return []


def check_rect(rect, main_bbox, page=PAGE_CM, min_cm=3.0):
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        raise ValueError("区位框矩形无效：{}".format(rect))
    if min(x1 - x0, y1 - y0) < min_cm:
        raise ValueError("区位框太小（每边需≥{}cm）：{}".format(min_cm, rect))
    if x0 < 0 or y0 < 0 or x1 > page[0] or y1 > page[1]:
        raise ValueError("区位框超出页面 {}：{}".format(page, rect))
    mx0, my0, mx1, my1 = main_bbox
    if x0 < mx0 - 1e-6 or y0 < my0 - 1e-6 or x1 > mx1 + 1e-6 or y1 > my1 + 1e-6:
        raise ValueError("区位框必须落在主图框 {} 内：{}".format(main_bbox, rect))
    return rect


def add_inset(doc, rect=DEFAULT_RECT, title="区位示意图", frame_name=INSET_FRAME_NAME):
    """在 pagx dict 上追加区位图框与图名，返回 (inset_element, title_element)。"""
    elements = doc["layoutDefinition"]["elements"]
    if any(element.get("name") == frame_name for element in elements):
        raise ValueError("模板里已有 {}，不重复添加".format(frame_name))
    main = find_main_frame(elements)
    check_rect(tuple(rect), frame_bbox(main))
    check_collisions(elements, tuple(rect))

    inset = _restyle_inset_frame(copy.deepcopy(main), tuple(rect))
    inset["name"] = frame_name
    # clone 出来的 autoCamera 会跟着主图框走，区位图必须自己定相机
    inset["autoCamera"] = {"type": "CIMAutoCamera", "autoCameraType": "Extent",
                           "marginType": "Percent", "source": "None"}
    label = text_element(INSET_TITLE_NAME, title, rect[0] + 0.12, rect[3] - 0.08, size=7.0)

    # 图名放在区位框之后，保证压在白底上不被覆盖
    index = elements.index(main) + 1
    elements.insert(index, inset)
    elements.insert(index + 1, label)
    return inset, label


def build(base, out, rect=DEFAULT_RECT, title="区位示意图"):
    doc = load_pagx(base)
    add_inset(doc, rect=rect, title=title)
    save_pagx(doc, out)
    return {"template": os.path.abspath(out), "inset_frame": INSET_FRAME_NAME,
            "inset_title": INSET_TITLE_NAME, "rect_cm": list(rect), "title": title}


def main(argv=None):
    parser = argparse.ArgumentParser(description="在 A4 成果图模板上追加区位小图框")
    parser.add_argument("--base", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "demo_map", "compliant", "补充耕地鉴定图_A4.pagx"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--rect", default=",".join(str(v) for v in DEFAULT_RECT))
    parser.add_argument("--title", default="区位示意图")
    args = parser.parse_args(argv)
    rect = tuple(float(v) for v in args.rect.split(","))
    if len(rect) != 4:
        parser.error("--rect 需要 4 个厘米值：x0,y0,x1,y1")
    info = build(args.base, args.out, rect=rect, title=args.title)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return info


if __name__ == "__main__":
    main()
