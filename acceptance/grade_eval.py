"""耕地质量等级评价：指标→隶属度→综合指数→整数/两位小数等级。

纯 Python，不依赖 arcpy。参数来自 config/grading_parameters.json（指导书 v1.1 附录 K）；
缺参数、权重和不为 1、概念值查不到时 fail-closed，不猜值。
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "grading_parameters.json"
COUNTY_PATH = Path(__file__).resolve().parent.parent / "config" / "region_counties.json"

REGION_ALIASES = {
    "华南区": "华南区", "粤西桂南农林区": "华南区", "华南": "华南区",
    "西南区": "西南区", "黔桂高原山地林农牧区": "西南区", "黔桂高原": "西南区", "西南": "西南区",
    "南岭区": "南岭区", "南岭丘陵山地林农区": "南岭区", "南岭丘陵": "南岭区", "南岭": "南岭区",
}
INDICATOR_ALIASES = {
    "pH": "酸碱度", "酸碱度": "酸碱度",
    "有机质": "有机质", "有机质含量": "有机质",
    "有效磷": "有效磷", "速效钾": "速效钾",
    "容重": "土壤容重", "土壤容重": "土壤容重",
    "有效土层厚度": "有效土层厚度", "耕层厚度": "耕层厚度",
    "海拔": "海拔高度", "海拔高度": "海拔高度",
    "地形部位": "地形部位", "水资源": "水资源条件", "水资源条件": "水资源条件",
    "排水能力": "排水能力", "耕层质地": "耕层质地", "质地构型": "质地构型",
}


def _read_json(path) -> dict:
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("参数文件顶层必须是对象：" + os.fspath(path))
    return data


def load_parameters(path=None) -> dict:
    path = path or DEFAULT_PATH
    data = _read_json(path)
    if not data.get("regions"):
        raise ValueError("等级参数缺少 regions：" + os.fspath(path))
    return data


def normalize_region(region: str) -> str:
    key = REGION_ALIASES.get(str(region or "").strip())
    if not key:
        raise ValueError(f"未知农业分区：{region}（可用：华南区/西南区/南岭区）")
    return key


def validate_weights(region: str, parameters: dict | None = None) -> dict:
    parameters = parameters or load_parameters()
    key = normalize_region(region)
    weights = (parameters["regions"][key] or {}).get("weights") or {}
    if not weights:
        raise ValueError(f"{key} 没有权重配置")
    total = sum(float(value) for value in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"{key} 权重和为 {total:.6f}，不等于 1；先查指标漏选/错选，不得归一化掩盖")
    return weights


def membership(value, spec: Mapping[str, Any]) -> float:
    """数值型隶属度；边界规则按附录 K 注。"""
    try:
        u = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"指标值不是数值：{value!r}") from exc
    kind = spec.get("type")
    lower, upper = float(spec["lower"]), float(spec["upper"])
    if kind == "戒上型":
        if u <= lower:
            return 0.0
        if u >= upper:
            return 1.0
        result = 1.0 / (1.0 + float(spec["a"]) * (u - float(spec["c"])) ** 2)
    elif kind == "峰型":
        if u <= lower or u >= upper:
            return 0.0
        result = 1.0 / (1.0 + float(spec["a"]) * (u - float(spec["c"])) ** 2)
    elif kind == "负直线型":
        if u <= lower:
            return 1.0
        if u >= upper:
            return 0.0
        result = float(spec["b"]) - float(spec["a"]) * u
    else:
        raise ValueError(f"未知隶属函数类型：{kind}")
    return round(min(1.0, max(0.0, result)), 6)


def concept_membership(region: str, indicator: str, value: str,
                       parameters: dict | None = None) -> float:
    parameters = parameters or load_parameters()
    key = normalize_region(region)
    table = ((parameters["regions"][key].get("concept") or {}).get(indicator) or {})
    if not table:
        raise ValueError(f"{key} 没有概念型指标 {indicator} 的隶属度表")
    text = str(value or "").strip()
    if text not in table:
        raise ValueError("{} / {} 取值 {!r} 不在表中；可用值：{}".format(
            key, indicator, value, "、".join(sorted(table))))
    return float(table[text])


def _indicator_name(name: str) -> str:
    key = INDICATOR_ALIASES.get(str(name or "").strip())
    if not key:
        raise ValueError(f"未知评价指标：{name}")
    return key


def grade_for_index(index: float, region: str, parameters: dict | None = None) -> dict:
    """综合指数落位；一等/十等为开区间，不输出两位小数等级。"""
    parameters = parameters or load_parameters()
    key = normalize_region(region)
    bands = parameters["regions"][key].get("bands") or []
    value = float(index)
    for band in bands:
        grade = int(band["grade"])
        lower, upper = band.get("lower"), band.get("upper")
        if lower is None and upper is not None and value < float(upper):
            return {"ZLDJ": grade, "JQZLDJ": None,
                    "JQZLDJ状态": "UNVERIFIED", "说明": "十等为开区间，未定义两位小数插值"}
        if upper is None and lower is not None and value >= float(lower):
            return {"ZLDJ": grade, "JQZLDJ": None,
                    "JQZLDJ状态": "UNVERIFIED", "说明": "一等为开区间，未定义两位小数插值"}
        if lower is not None and upper is not None and float(lower) <= value < float(upper):
            width = float(upper) - float(lower)
            precise = grade - (value - float(lower)) / width
            return {"ZLDJ": grade, "JQZLDJ": round(precise, 2), "JQZLDJ状态": "UNVERIFIED",
                    "说明": "两位小数由区间线性插值反解，待与三普规范附录 J 原公式核对"}
    raise ValueError(f"综合指数 {value:.4f} 不在 {key} 等级区间内")


def evaluate_unit(region: str, indicators: Mapping[str, Any],
                  parameters: dict | None = None) -> dict:
    """计算一个评价单元；缺指标时返回 UNVERIFIED，不输出部分结论。"""
    parameters = parameters or load_parameters()
    key = normalize_region(region)
    weights = validate_weights(key, parameters)
    spec = parameters["regions"][key]
    provided = {}
    for name, value in (indicators or {}).items():
        try:
            canonical = _indicator_name(name)
        except ValueError:
            continue  # 符合性专用指标（如地形坡度）不参与等级评价
        if canonical in weights:
            provided[canonical] = value
    contributions, missing = [], []
    for indicator, weight in weights.items():
        if indicator not in provided or provided[indicator] in (None, ""):
            missing.append(indicator)
            continue
        value = provided[indicator]
        if indicator in (spec.get("numeric") or {}):
            score = membership(value, spec["numeric"][indicator])
        elif indicator in (spec.get("concept") or {}):
            score = concept_membership(key, indicator, value, parameters)
        else:
            raise ValueError(f"{key} 指标 {indicator} 没有隶属度配置")
        contributions.append({"指标": indicator, "实测值": value, "隶属度": score,
                              "权重": float(weight), "加权值": round(float(weight) * score, 6)})
    if missing:
        return {"status": "UNVERIFIED", "region": key, "missing": missing,
                "PJZHZS": None, "ZLDJ": None, "JQZLDJ": None,
                "contributions": contributions,
                "说明": "缺少指标实测值，未计算综合指数"}
    index = round(sum(item["加权值"] for item in contributions), 4)
    grade = grade_for_index(index, key, parameters)
    return {"status": "PASS", "region": key, "PJZHZS": index,
            "ZLDJ": grade["ZLDJ"], "JQZLDJ": grade["JQZLDJ"],
            "JQZLDJ状态": grade["JQZLDJ状态"], "说明": grade["说明"],
            "contributions": contributions, "missing": []}


def region_guard(county: str, region: str | None = None, config_path=None) -> str:
    """县名→二级农业区；县不在附录 J 或与所选区不符即停。"""
    data = _read_json(config_path or COUNTY_PATH)
    target = str(county or "").strip()
    if not target:
        raise ValueError("未提供县（市、区）名称，不能确定农业分区")
    import re
    def stem(name: str) -> str:
        return re.sub(r"(市|县|区|旗)$", "", str(name or "").strip())
    matched = None
    for zone, payload in (data.get("zones") or {}).items():
        counties = [str(item).strip() for item in payload.get("counties") or []]
        if target in counties or any(stem(target) == stem(item) for item in counties):
            matched = zone
            break
    if not matched:
        raise ValueError(f"县 {target} 不在附录 J 名单内；不得回退邻近区")
    if region:
        expected = normalize_region(region)
        if normalize_region(matched) != expected:
            raise ValueError(f"县 {target} 属于 {matched}，与所选 {region} 不符")
    return normalize_region(matched)
