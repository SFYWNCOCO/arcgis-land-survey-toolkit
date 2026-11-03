"""占用/补充耕地质量核算：三口径面积加权平均等级。

纯 Python。占用等级取整数，补充等级取两位小数且只用市级鉴定意见；
空集或零面积直接报错，不返回 0。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _rows(items) -> list[Mapping[str, Any]]:
    if isinstance(items, Mapping):
        items = items.get("rows") or []
    return list(items or [])


def weighted_average(items: Iterable[Mapping[str, Any]], *,
                     grade_field: str = "grade", area_field: str = "area_m2",
                     require_integer: bool = False, require_two_decimals: bool = False) -> dict:
    rows = _rows(items)
    if not rows:
        raise ValueError("核算明细为空，不能计算平均等级")
    total_area = 0.0
    weighted = 0.0
    detail = []
    for index, row in enumerate(rows, start=1):
        try:
            grade = float(row[grade_field])
            area = float(row[area_field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 行缺少有效等级/面积：{row!r}") from exc
        if area <= 0:
            raise ValueError(f"第 {index} 行面积必须大于 0：{area!r}")
        if require_integer and not float(grade).is_integer():
            raise ValueError(f"占用耕地等级应为整数：第 {index} 行 {grade!r}")
        if require_two_decimals and round(grade, 2) != grade:
            raise ValueError(f"补充耕地等级应为两位小数：第 {index} 行 {grade!r}")
        total_area += area
        weighted += grade * area
        detail.append({"grade": grade, "area_m2": round(area, 2), "weighted": round(grade * area, 4)})
    return {"average": round(weighted / total_area, 2),
            "area_m2": round(total_area, 2), "count": len(rows), "detail": detail}


def account(occupied_all, occupied_nonfarm, supplementary,
            supplementary_source: str = "市级鉴定意见") -> dict:
    """三口径分开核算，并给出规范处置判据。"""
    if supplementary_source != "市级鉴定意见":
        raise ValueError("补充耕地等级必须使用市级鉴定意见，不能使用县级初鉴值")
    all_result = weighted_average(occupied_all, require_integer=True)
    nonfarm_result = weighted_average(occupied_nonfarm, require_integer=True)
    supplement_result = weighted_average(supplementary, require_two_decimals=True)
    conditions = []
    if supplement_result["average"] >= nonfarm_result["average"]:
        conditions.append("补充耕地平均等级不低于上一年度非农建设占用耕地平均等级，"
                          "可按程序纳入非农建设补充耕地报备（仍需规划、生态、集中连片、稳定利用等条件）")
    if supplement_result["average"] < all_result["average"]:
        conditions.append("补充耕地平均等级低于各类占用耕地平均等级，须培肥改良，"
                          "5 年内达到目标后按程序再评价（原则上只开展一次）")
    if not conditions:
        conditions.append("补充耕地平均等级低于非农建设占用但不低于各类占用，"
                          "按规范进一步核对报备条件")
    return {"各类占用": all_result, "非农建设占用": nonfarm_result,
            "补充耕地": supplement_result, "结论": conditions,
            "口径": "平均等级=Σ(等级×面积)/Σ面积；面积 m²、等级 2 位"}
