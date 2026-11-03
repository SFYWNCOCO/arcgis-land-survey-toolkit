"""农业生产符合性评价：阈值逐项判定、一票否决、直接判不合格情形。

纯 Python。缺实测值返回 UNVERIFIED，不把未知写成合格。
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "conformity_thresholds.json"
LAND_USES = ("旱地", "水浇地", "水田")
ALIASES = {
    "水资源条件": "水资源保障条件", "水资源": "水资源保障条件",
    "有机质": "土壤有机质含量", "有机质含量": "土壤有机质含量",
    "pH": "土壤pH", "酸碱度": "土壤pH",
}


def load_config(path=None) -> dict:
    path = path or DEFAULT_PATH
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not data.get("indicators"):
        raise ValueError("符合性阈值配置无效：" + os.fspath(path))
    return data


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"实测值不是数值：{value!r}") from exc


def _pollution_limit(metal: Mapping[str, Any], ph: float) -> float:
    for band in metal.get("limits") or []:
        cap = band.get("max_ph")
        if cap is None or ph <= float(cap):
            return float(band["limit"])
    raise ValueError("污染管制值缺少 pH>7.5 档")


def _check_indicator(name: str, spec: Mapping[str, Any], value: Any,
                     land_use: str) -> dict:
    check = {"指标": name, "实测值": value, "阈值": "", "判定": "UNVERIFIED", "说明": ""}
    if value in (None, ""):
        check["说明"] = "缺实测值，不能判定"
        return check
    kind = spec.get("type")
    if kind == "min_by_land_use":
        threshold = (spec.get("thresholds") or {}).get(land_use)
        if threshold is None:
            check["说明"] = "未提供利用类型（旱地/水浇地/水田），无法选阈值"
            return check
        check["阈值"] = "≥{} {}".format(threshold, spec.get("unit", ""))
        check["判定"] = "PASS" if _num(value) >= float(threshold) else "FAIL"
    elif kind == "range":
        low, high = float(spec["min"]), float(spec["max"])
        check["阈值"] = f"{low}～{high}"
        check["判定"] = "PASS" if low <= _num(value) <= high else "FAIL"
    elif kind == "max":
        check["阈值"] = "≤{} {}".format(spec["max"], spec.get("unit", ""))
        check["判定"] = "PASS" if _num(value) <= float(spec["max"]) else "FAIL"
    elif kind == "ordinal":
        order = list(spec.get("order") or [])
        text = str(value).strip()
        if text not in order:
            check["说明"] = "取值不在规定档位：{}".format("、".join(order))
            return check
        minimum = order.index(spec["min"])
        check["阈值"] = "≥{}".format(spec["min"])
        check["判定"] = "PASS" if order.index(text) >= minimum else "FAIL"
    elif kind == "intrusion":
        if not isinstance(value, Mapping):
            check["说明"] = "需要 {10_100mm_percent, over_100mm} 两字段"
            return check
        percent = value.get("10_100mm_percent")
        over = value.get("over_100mm")
        if percent in (None, "") or over is None:
            check["说明"] = "侵入体百分含量或 >100 mm 是否存在缺项"
            return check
        check["阈值"] = "10–100 mm ≤{}%，且无 >100 mm".format(spec["max_percent"])
        check["判定"] = "PASS" if _num(percent) <= float(spec["max_percent"]) and not bool(over) else "FAIL"
    elif kind == "pollution":
        if not isinstance(value, Mapping):
            check["说明"] = "需要 {ph, 镉, 汞, 砷, 铅, 铬} 对象"
            return check
        ph = value.get("ph")
        if ph in (None, ""):
            check["说明"] = "缺土壤 pH，无法选污染管制值档位"
            return check
        failures, missing = [], []
        for metal, metal_spec in (spec.get("metals") or {}).items():
            measured = value.get(metal)
            if measured in (None, ""):
                missing.append(metal)
                continue
            limit = _pollution_limit(metal_spec, _num(ph))
            if _num(measured) > limit:
                failures.append(f"{metal} {_num(measured):.3f}>{limit:.3f}")
        check["阈值"] = f"GB 15618 管制值（pH {_num(ph):.1f} 分档）"
        if failures:
            check["判定"] = "FAIL"
            check["说明"] = "超管制值：" + "、".join(failures)
        elif missing:
            check["说明"] = "缺检测值：" + "、".join(missing)
        else:
            check["判定"] = "PASS"
    else:
        check["说明"] = f"未知判定类型：{kind}"
    return check


def evaluate(unit: Mapping[str, Any], config: dict | None = None) -> dict:
    """评价一个单元；unit 需含 utilization、indicators，可选 direct_fail/sources。"""
    config = config or load_config()
    land_use = str(unit.get("utilization") or unit.get("land_use") or "").strip()
    indicators = {ALIASES.get(name, name): value
                  for name, value in (unit.get("indicators") or {}).items()}
    sources = dict(unit.get("sources") or {})
    checks, direct_failures = [], []
    for item in config.get("direct_fail") or []:
        flags = unit.get("direct_fail") or {}
        if (isinstance(flags, Mapping) and flags.get(item["key"])) or (isinstance(flags, (list, tuple, set)) and item["key"] in flags):
            direct_failures.append(item["label"])
    for name, spec in (config.get("indicators") or {}).items():
        value = indicators.get(name)
        check = _check_indicator(name, spec, value, land_use)
        check["来源"] = sources.get(name, "待核")
        checks.append(check)
    failures = [check for check in checks if check["判定"] == "FAIL"]
    unverified = [check for check in checks if check["判定"] == "UNVERIFIED"]
    if direct_failures or failures:
        status, result = "FAIL", "不合格"
    elif unverified:
        status, result = "UNVERIFIED", "无法判定"
    else:
        status, result = "PASS", "合格"
    return {"status": status, "result": result, "unit": unit.get("unit"),
            "land_use": land_use, "checks": checks,
            "failures": [check["指标"] for check in failures],
            "unverified": [check["指标"] for check in unverified],
            "direct_failures": direct_failures,
            "note": "任一指标不达标即不合格；UNVERIFIED 不得写成合格"}
