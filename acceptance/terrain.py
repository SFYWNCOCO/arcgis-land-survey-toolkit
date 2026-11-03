"""地形部位归并：把地貌图二级单元归到指导书 3.10 的 12 类。

只做机械映射，不推断：映射表由人工按地貌图图例逐项确认；未命中的原始值保留并标待核。
"""
from __future__ import annotations

import json

TERRAIN_CLASSES = (
    "山间盆地", "宽谷盆地", "平原低阶", "平原中阶", "平原高阶",
    "丘陵上部", "丘陵中部", "丘陵下部",
    "山地坡上", "山地坡中", "山地坡下", "塬",
)


def load_mapping(path):
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not data:
        raise ValueError("地貌归并表必须是 {原始二级单元: 12类之一} 的非空对象")
    mapping = {}
    for raw, target in data.items():
        raw, target = str(raw).strip(), str(target).strip()
        if not raw or not target:
            raise ValueError("地貌归并表存在空键或空值")
        if target not in TERRAIN_CLASSES:
            raise ValueError("地貌归并目标不在 3.10 的 12 类内：{} → {}".format(raw, target))
        mapping[raw] = target
    return mapping


def normalize(value, mapping):
    raw = "" if value is None else str(value).strip()
    return mapping.get(raw, raw)
