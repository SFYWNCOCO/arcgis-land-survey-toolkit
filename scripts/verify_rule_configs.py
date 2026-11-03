"""机械核验等级/符合性参数配置：权重和、区间连续、指标覆盖与原文对照。

用法：
    python scripts/verify_rule_configs.py [--strict]

退出码 1 表示存在硬错误；缺 references 原文时只做配置内部核验并给出提示。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


WORKSPACE = ROOT.parents[1]
GRADING = ROOT / "config" / "grading_parameters.json"
CONFORMITY = ROOT / "config" / "conformity_thresholds.json"
REF_GRADING = WORKSPACE / "skills" / "guangxi-cultivated-land-acceptance" / "references" / "03-grading-parameters.md"
REF_CONFORMITY = WORKSPACE / "skills" / "guangxi-cultivated-land-acceptance" / "references" / "02-conformity-thresholds.md"

EXPECTED_CONCEPT_COUNTS = {"地形部位": 11, "耕层质地": 12, "质地构型": 7, "水资源条件": 4, "排水能力": 4}


def _read_json(path):
    with open(path, encoding="utf-8-sig") as stream:
        return json.load(stream)


def verify_grading(errors: list[str], warnings: list[str]) -> dict:
    params = _read_json(GRADING)
    regions = params.get("regions") or {}
    if set(regions) != {"华南区", "西南区", "南岭区"}:
        errors.append("等级参数应恰有三个区，实际：{}".format("、".join(regions)))
    for key, spec in regions.items():
        weights = spec.get("weights") or {}
        total = sum(float(value) for value in weights.values())
        if abs(total - 1.0) > 1e-6:
            errors.append(f"{key} 权重和 {total:.6f} != 1")
        numeric = set((spec.get("numeric") or {}).keys())
        concept = set((spec.get("concept") or {}).keys())
        if numeric | concept != set(weights):
            errors.append(f"{key} 指标覆盖不一致：权重 {sorted(weights)} / 数值 {sorted(numeric)} / 概念 {sorted(concept)}")
        for indicator, table in (spec.get("concept") or {}).items():
            expected = EXPECTED_CONCEPT_COUNTS.get(indicator)
            if expected and len(table) != expected:
                errors.append(f"{key} / {indicator} 类别数 {len(table)} != 预期 {expected}")
        for indicator, item in (spec.get("numeric") or {}).items():
            for field in ("type", "lower", "upper"):
                if field not in item:
                    errors.append(f"{key} / {indicator} 缺字段 {field}")
            if item.get("type") == "负直线型" and "b" not in item:
                errors.append(f"{key} / {indicator} 负直线型缺 b")
            if item.get("type") != "负直线型" and not {"a", "c"} <= set(item):
                errors.append(f"{key} / {indicator} 缺 a/c")
        bands = spec.get("bands") or []
        if len(bands) != 10:
            errors.append(f"{key} 等级区间应为 10 段，实际 {len(bands)}")
        ordered = sorted(bands, key=lambda row: row["grade"])
        for index in range(len(ordered) - 1):
            left, right = ordered[index], ordered[index + 1]
            if left.get("lower") != right.get("upper"):
                errors.append("{} 等级 {}–{} 区间不连续：{} vs {}".format(
                    key, left["grade"], right["grade"], left.get("lower"), right.get("upper")))
        if ordered and ordered[0].get("upper") is not None:
            errors.append(f"{key} 一等应为开区间（upper=null）")
        if ordered and ordered[-1].get("lower") is not None:
            errors.append(f"{key} 十等应为开区间（lower=null）")
    return params


def verify_grading_against_reference(params: dict, errors: list[str], warnings: list[str]) -> None:
    if not REF_GRADING.is_file():
        warnings.append("缺少等级参数参考文件，跳过原文对照：" + str(REF_GRADING))
        return
    text = REF_GRADING.read_text(encoding="utf-8")
    column_region = {1: "西南区", 2: "华南区", 3: "南岭区"}
    seen = 0
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not re.match(r"^[一-鿿]", cells[0]):
            continue
        indicator = cells[0]
        matched = False
        for index, region in column_region.items():
            if not re.match(r"^[0-9.]+$", cells[index]):
                continue
            expected = float(cells[index])
            actual = (params["regions"][region]["weights"] or {}).get(indicator)
            if actual is None:
                errors.append(f"参考表有 {region} / {indicator}，配置缺失")
            elif abs(float(actual) - expected) > 1e-9:
                errors.append(f"{region} / {indicator} 权重 {actual} != 原文 {expected}")
            matched = True
        if matched:
            seen += 1
    if seen < 13:
        warnings.append(f"权重原文对照只匹配到 {seen} 行（预期 13 个指标），可能表格格式变化")


def verify_conformity(errors: list[str]) -> dict:
    config = _read_json(CONFORMITY)
    indicators = config.get("indicators") or {}
    required = {"有效土层厚度", "土壤有机质含量", "土壤pH", "地形坡度", "田面平整程度",
                "道路通行条件", "水资源保障条件", "土壤侵入体及砾石", "土壤污染状况"}
    if set(indicators) != required:
        errors.append(f"符合性指标集不一致：{sorted(set(indicators) ^ required)}")
    for name in ("有效土层厚度", "土壤有机质含量"):
        thresholds = indicators[name].get("thresholds") or {}
        if set(thresholds) != {"旱地", "水浇地", "水田"}:
            errors.append(f"{name} 缺旱地/水浇地/水田分档")
        if not (thresholds.get("旱地", 0) <= thresholds.get("水浇地", 0) <= thresholds.get("水田", 0)):
            errors.append(f"{name} 分档应满足旱地≤水浇地≤水田")
    pollution = indicators["土壤污染状况"].get("metals") or {}
    if set(pollution) != {"镉", "汞", "砷", "铅", "铬"}:
        errors.append("污染指标应为镉汞砷铅铬")
    for metal, spec in pollution.items():
        limits = spec.get("limits") or []
        if len(limits) != 4 or limits[-1].get("max_ph") is not None:
            errors.append(f"{metal} 管制值应有 4 个 pH 档且末档无上限")
    return config


def verify_conformity_against_reference(config: dict, errors: list[str], warnings: list[str]) -> None:
    if not REF_CONFORMITY.is_file():
        warnings.append("缺少符合性参考文件，跳过原文对照：" + str(REF_CONFORMITY))
        return
    text = REF_CONFORMITY.read_text(encoding="utf-8")
    patterns = {
        "有效土层厚度": r"^\|\s*有效土层厚度\s*\|\s*≥([0-9.]+)\s*cm\s*\|\s*≥([0-9.]+)\s*cm\s*\|\s*≥([0-9.]+)\s*cm",
        "土壤有机质含量": r"^\|\s*土壤有机质含量\s*\|\s*≥([0-9.]+)\s*g/kg\s*\|\s*≥([0-9.]+)\s*g/kg\s*\|\s*≥([0-9.]+)\s*g/kg",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            errors.append(f"参考文件找不到 {name} 阈值行")
            continue
        expected = [float(value) for value in match.groups()]
        thresholds = config["indicators"][name]["thresholds"]
        actual = [thresholds["旱地"], thresholds["水浇地"], thresholds["水田"]]
        if actual != expected:
            errors.append(f"{name} 阈值 {actual} != 原文 {expected}")
    ph = re.search(r"^\|\s*土壤 pH\s*\|\s*([0-9.]+)～([0-9.]+)", text, re.MULTILINE)
    if ph:
        actual = config["indicators"]["土壤pH"]
        if [actual["min"], actual["max"]] != [float(ph.group(1)), float(ph.group(2))]:
            errors.append("土壤 pH 区间与原文不一致")
    else:
        warnings.append("参考文件找不到 pH 阈值行")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="核验等级与符合性参数配置")
    parser.add_argument("--strict", action="store_true", help="把警告也视为失败")
    args = parser.parse_args(argv)
    errors: list[str] = []
    warnings: list[str] = []
    params = verify_grading(errors, warnings)
    verify_grading_against_reference(params, errors, warnings)
    config = verify_conformity(errors)
    verify_conformity_against_reference(config, errors, warnings)
    for item in warnings:
        print("WARN", item)
    for item in errors:
        print("FAIL", item)
    print("VERIFY", "PASS" if not errors and not (args.strict and warnings) else "FAIL",
          f"errors={len(errors)} warnings={len(warnings)}")
    return 0 if not errors and not (args.strict and warnings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
