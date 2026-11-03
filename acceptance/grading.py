"""耕地质量监测指标分级赋值（实测值 → 1—5 级）。

依据：《全国九大农区及省级耕地质量监测指标分级标准（试行）》耕地监测函〔2019〕30号。
阈值真源：`config/grade_thresholds.json`（带文号、证据与核实状态），**不写在代码里**——
原文表格本次未取得，写死在代码里等于把未核数字固化成"官方值"。

口径边界（最重要的一条）：
    本模块产出的是**监测指标分级值**（1—5 级），不是耕地质量**等级**。
    补充耕地质量验收的等级走三普/GB-T 33469 的 权重×隶属度→综合指数→10 等级（见
    `references/03-grading-parameters.md`）。分级值不得直接当质量等级、不得代入等级评价，
    也不得把"分级占比"当"质量等级构成"写进验收报告。

三类分级逻辑（与隶属函数三型一一对应，但输出不同）：
    asc   正向（值越大越好）↔ 戒上型
    desc  负向（值越小越好）
    range 区间（适中最好）  ↔ 峰型

设计取舍（与常见脚本工具的差别，均有理由）：
    1. 阈值外置 + 逐项 verified 标记：未核阈值照算，但结果里带 UNVERIFIED，不冒充官方值。
    2. 空值/非数值不进分母：如果当 0 参与统计，会把"没测"算成"最低级"，直接污染面积占比。
    3. 字段重名不静默取第一个：默认报歧义并跳过，需显式传 mapping 或 policy="first" 才取。
    4. 输出字段重名不自动加 _1：重跑会产生"哪个才是本次结果"的歧义，默认拒绝，需显式 overwrite。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "grade_thresholds.json"
COUNTY_CONFIG = Path(__file__).resolve().parents[1] / "config" / "region_counties.json"

GRADES = (1, 2, 3, 4, 5)
OUT_PREFIX = "FJ_"

# 常见非数值写法：先尝试解析，解析不了记问题而不是当 0
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_ND_VALUES = {"未检出", "nd", "ND", "nd.", "—", "-", "", "/", "无", "未测", "null", "None"}


@dataclass
class GradeSpec:
    abbr: str
    name: str
    direction: str
    unit: str = ""
    bounds: list[float] = field(default_factory=list)
    ranges: list[tuple[float, float, int]] = field(default_factory=list)
    outside_grade: int | None = None
    verified: bool = False
    evidence: str = ""

    def grade_of(self, value: float) -> int:
        if self.direction == "asc":
            for i, bound in enumerate(self.bounds):
                if value >= bound:
                    return i + 1
            return 5
        if self.direction == "desc":
            for i, bound in enumerate(self.bounds):
                if value <= bound:
                    return i + 1
            return 5
        if self.direction == "range":
            for lo, hi, grade in self.ranges:
                if lo <= value <= hi:
                    return grade
            if self.outside_grade is not None:
                return self.outside_grade
            raise ValueError("区间型指标缺少 outside_grade：{}".format(self.abbr))
        raise ValueError("未知分级方向：{}".format(self.direction))


def load_thresholds(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_counties(path: str | Path = COUNTY_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def zone_for_county(county: str, counties: Mapping[str, Any] | None = None) -> dict:
    """县名 → 二级农业区。查不到就报错，绝不回退到邻近区。

    回退是这类工具最危险的静默错误：县名写错一个字，整批按错误权重算完，
    结果看起来一切正常。所以这里一律 fail-closed。
    """
    data = counties if counties is not None else load_counties()
    target = str(county).strip()
    for zone_name, body in data.get("zones", {}).items():
        if target in body.get("counties", []):
            return {"zone": zone_name, "first_level": body.get("first_level", ""),
                    "county": target}
    raise KeyError("县名不在附录J名单内：{}（请核对全称与后缀，如“横州市”“隆林各族自治县”）".format(target))


def threshold_key_for_zone(zone: str, counties: Mapping[str, Any] | None = None) -> str | None:
    """二级农业区 → grade_thresholds.json 的区域键；没有对应阈值返回 None。"""
    data = counties if counties is not None else load_counties()
    return data.get("grading_region_map", {}).get(zone)


def region_guard(county: str, region: str, thresholds: Mapping[str, Any],
                 counties: Mapping[str, Any] | None = None) -> dict:
    """选区域前的一致性守卫：县所属区必须与所选阈值区域相符。

    返回 {"ok", "zone", "first_level", "expected", "problem"}。ok=False 时调用方必须停，
    不得"跑完再解释"——区域选错会让权重、隶属函数、等级指数全套错位。
    """
    data = counties if counties is not None else load_counties()
    info = zone_for_county(county, data)
    expected = threshold_key_for_zone(info["zone"], data)
    base = {"county": info["county"], "zone": info["zone"],
            "first_level": info["first_level"], "expected": expected}
    # 先判"这个区到底有没有阈值"：有映射键但文件里没有参数，性质与大区缺表相同
    if expected is None or expected not in thresholds.get("regions", {}):
        base.update(ok=False, problem="{} 所属 {}（{}）在本阈值文件中没有对应参数（映射键 {}）；"
                                      "缺的是该大区阈值表，不能套用邻近区。".format(
                                          info["county"], info["zone"], info["first_level"],
                                          expected or "无"))
        return base
    if region != expected:
        base.update(ok=False, problem="区域选择与县所属区不一致：{} 属 {}（{}），应选 {}，实际选了 {}。".format(
            info["county"], info["zone"], info["first_level"], expected, region))
        return base
    base.update(ok=True, problem=None)
    return base


def zones_without_thresholds(thresholds: Mapping[str, Any],
                             counties: Mapping[str, Any] | None = None) -> list[dict]:
    """哪些二级农业区在本阈值文件里没有参数，涉及多少县。"""
    data = counties if counties is not None else load_counties()
    missing: list[dict] = []
    for zone_name, body in data.get("zones", {}).items():
        key = threshold_key_for_zone(zone_name, data)
        if key is None or key not in thresholds.get("regions", {}):
            missing.append({"zone": zone_name, "first_level": body.get("first_level", ""),
                            "county_count": len(body.get("counties", []))})
    return missing


def specs_for(config: Mapping[str, Any], region: str) -> dict[str, GradeSpec]:
    regions = config.get("regions", {})
    if region not in regions:
        raise KeyError("区域不存在：{}（可选：{}）".format(region, "/".join(regions)))
    raw = regions[region].get("indicators", {})
    specs: dict[str, GradeSpec] = {}
    for abbr, item in raw.items():
        specs[abbr] = GradeSpec(
            abbr=abbr,
            name=item.get("name", abbr),
            direction=item.get("direction", ""),
            unit=item.get("unit", ""),
            bounds=[float(x) for x in item.get("bounds", [])],
            ranges=[(float(a), float(b), int(g)) for a, b, g in item.get("ranges", [])],
            outside_grade=item.get("outside_grade"),
            verified=bool(item.get("verified", False)),
            evidence=item.get("evidence", ""),
        )
    return specs


def validate_spec(spec: GradeSpec) -> list[str]:
    """阈值自检：返回问题清单，空列表才算可用。"""
    problems: list[str] = []
    if spec.direction not in ("asc", "desc", "range"):
        problems.append("{}：direction 无效（{}）".format(spec.abbr, spec.direction))
        return problems
    if spec.direction in ("asc", "desc"):
        if len(spec.bounds) != 4:
            problems.append("{}：bounds 需 4 个界限才能覆盖 1—5 级，当前 {} 个".format(
                spec.abbr, len(spec.bounds)))
        # asc 要求严格递减（b4>b3>b2>b1），desc 要求严格递增（m1<m2<m3<m4）
        pairs = list(zip(spec.bounds, spec.bounds[1:]))
        if spec.direction == "asc" and not all(a > b for a, b in pairs):
            problems.append("{}：正向 bounds 必须严格递减（越大越好）".format(spec.abbr))
        if spec.direction == "desc" and not all(a < b for a, b in pairs):
            problems.append("{}：负向 bounds 必须严格递增".format(spec.abbr))
    else:
        if not spec.ranges:
            problems.append("{}：区间型缺少 ranges".format(spec.abbr))
        seen: list[int] = []
        for lo, hi, grade in spec.ranges:
            if lo > hi:
                problems.append("{}：区间 [{}, {}] 上下界颠倒".format(spec.abbr, lo, hi))
            if grade not in GRADES:
                problems.append("{}：等级 {} 不在 1—5".format(spec.abbr, grade))
            seen.append(grade)
        for lo, hi, _ in spec.ranges:
            for lo2, hi2, _ in spec.ranges:
                if (lo, hi) == (lo2, hi2):
                    continue
                if lo < hi2 and lo2 < hi:
                    problems.append("{}：区间 [{},{}] 与 [{},{}] 重叠".format(spec.abbr, lo, hi, lo2, hi2))
        if spec.outside_grade is None:
            problems.append("{}：区间型缺少 outside_grade".format(spec.abbr))
        else:
            reachable = {g for _, _, g in spec.ranges} | {spec.outside_grade}
            missing = sorted(set(GRADES) - reachable)
            if missing:
                problems.append("{}：分级未覆盖 {} 级（分级值不可达，占比统计会失真）".format(
                    spec.abbr, "/".join(str(g) for g in missing)))
    return problems


def parse_value(raw: Any) -> tuple[float | None, str | None]:
    """解析实测值：返回 (数值, 问题)。问题非空时数值为 None，且不得当 0 参与统计。"""
    if raw is None:
        return None, "空值"
    if isinstance(raw, (int, float)):
        return float(raw), None
    text = str(raw).strip()
    if text in _ND_VALUES:
        return None, "空值或未检出"
    # "<0.5" / ">100" / "≤1" 这类限值写法：取下限数值并留痕，不静默丢弃
    m = _NUM_RE.search(text)
    if not m:
        return None, "非数值：{}".format(text[:12])
    value = float(m.group(0))
    if text[:1] in "<≤>≥":
        return value, "限值写法：{}（按 {} 参与分级，需人工确认）".format(text, value)
    if text != m.group(0):
        return value, "含多余字符：{}".format(text[:12])
    return value, None


def match_fields(field_names: Sequence[str], specs: Mapping[str, GradeSpec],
                 policy: str = "strict") -> tuple[dict[str, str], list[str]]:
    """指标缩写 → 字段名。先精确匹配，再前缀匹配。

    policy="strict"（默认）：一个缩写命中多个字段时报歧义并跳过，不静默取第一个；
    policy="first"：按字段顺序取第一个，但记提示。文章默认行为即"取第一个"，此处改为可选。
    """
    mapping: dict[str, str] = {}
    issues: list[str] = []
    upper = {name: name.upper() for name in field_names}
    for abbr in specs:
        key = abbr.upper()
        exact = [name for name, up in upper.items() if up == key]
        if exact:
            mapping[abbr] = exact[0]
            continue
        prefixed = sorted(name for name, up in upper.items() if up.startswith(key))
        if not prefixed:
            issues.append("{}：未找到字段（试过精确与前缀匹配）".format(abbr))
            continue
        if len(prefixed) > 1 and policy != "first":
            issues.append("{}：命中多个字段 {}，已跳过；请在 mapping 中显式指定".format(
                abbr, "/".join(prefixed)))
            continue
        mapping[abbr] = prefixed[0]
        if len(prefixed) > 1:
            issues.append("{}：命中 {}，按 policy=first 取 {}".format(
                abbr, "/".join(prefixed), prefixed[0]))
    return mapping, issues


def assign(records: Iterable[Mapping[str, Any]], specs: Mapping[str, GradeSpec],
           mapping: Mapping[str, str], region: str) -> dict:
    """逐记录分级。返回 {rows, stats, issues, unverified}。

    rows：每条记录追加 FJ_<ABBR> 字段（值为 1—5，未测为 None）
    stats：每指标各级图斑数与占比，**分母只含已分级记录**；null 单独计数
    """
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    counts = {abbr: {g: 0 for g in GRADES} for abbr in mapping}
    nulls = {abbr: 0 for abbr in mapping}

    for idx, rec in enumerate(records):
        out = dict(rec)
        for abbr, fname in mapping.items():
            spec = specs[abbr]
            value, problem = parse_value(rec.get(fname))
            if problem:
                issues.append("第 {} 条 {}.{}：{}".format(idx + 1, fname, abbr, problem))
            if value is None:
                out[OUT_PREFIX + abbr] = None
                nulls[abbr] += 1
                continue
            grade = spec.grade_of(value)
            if grade not in GRADES:
                issues.append("第 {} 条 {}：分级结果 {} 越界".format(idx + 1, fname, grade))
                out[OUT_PREFIX + abbr] = None
                nulls[abbr] += 1
                continue
            out[OUT_PREFIX + abbr] = grade
            counts[abbr][grade] += 1
        rows.append(out)

    stats: dict[str, Any] = {}
    for abbr in mapping:
        total = sum(counts[abbr].values())
        stats[abbr] = {
            "name": specs[abbr].name,
            "graded": total,
            "null": nulls[abbr],
            "counts": dict(counts[abbr]),
            "share": {g: (round(counts[abbr][g] / total, 4) if total else None) for g in GRADES},
        }
    unverified = sorted(a for a in mapping if not specs[a].verified)
    return {"region": region, "rows": rows, "stats": stats, "issues": issues,
            "unverified": unverified}


def catalog_items(config: Mapping[str, Any]) -> dict[str, dict]:
    return dict(config.get("indicator_catalog", {}).get("items", {}))


def catalog_gaps(config: Mapping[str, Any]) -> list[str]:
    """指标层缺口：已确认方向的指标 vs 一个阈值都没配的指标。"""
    catalog = catalog_items(config)
    configured: set[str] = set()
    for body in config.get("regions", {}).values():
        configured.update(body.get("indicators", {}).keys())
    no_direction = [a for a, item in catalog.items() if not item.get("direction")]
    no_threshold = sorted(set(catalog) - configured)
    gaps: list[str] = []
    if no_threshold:
        gaps.append("阈值未配置 {} 项（方向已确认）：{}".format(
            len(no_threshold), "、".join(catalog[a]["name"] for a in no_threshold)))
    if no_direction:
        gaps.append("方向未确认 {} 项：{}".format(len(no_direction), "、".join(no_direction)))
    return gaps


def check_direction_consistency(config: Mapping[str, Any]) -> list[str]:
    """区域表里的方向必须与指标目录一致；不一致说明某一侧抄错。"""
    catalog = catalog_items(config)
    problems: list[str] = []
    for region, body in config.get("regions", {}).items():
        for abbr, item in body.get("indicators", {}).items():
            want = catalog.get(abbr, {}).get("direction")
            have = item.get("direction")
            if want and have and want != have:
                problems.append("[{}] {}：区域表方向 {} 与指标目录 {} 不一致".format(
                    region, abbr, have, want))
    return problems


def load_reference(path: str | Path | None = None) -> dict:
    """省级对照表（如福建省表）。仅用于结构自检，不得作为广西阈值。"""
    target = Path(path) if path else DEFAULT_CONFIG.parent / "grade_thresholds_reference.json"
    with open(target, encoding="utf-8") as fh:
        return json.load(fh)


def specs_from_reference(reference: Mapping[str, Any]) -> dict[str, GradeSpec]:
    """把对照表转成 GradeSpec，用于验证三门分级逻辑能覆盖官方表的全部写法。"""
    specs: dict[str, GradeSpec] = {}
    for abbr, item in reference.get("indicators", {}).items():
        direction = item.get("direction")
        if not direction:      # 重金属等不参与 1—5 级
            continue
        specs[abbr] = GradeSpec(
            abbr=abbr,
            name=item.get("name", abbr),
            direction=direction,
            unit=item.get("unit", ""),
            bounds=[float(x) for x in item.get("bounds", [])],
            ranges=[(float(a), float(b), int(g)) for a, b, g in item.get("ranges", [])],
            outside_grade=item.get("outside_grade"),
            verified=bool(reference.get("source", {}).get("verified", False)),
            evidence=reference.get("source", {}).get("title", ""),
        )
    return specs


def checklist(config: Mapping[str, Any]) -> list[str]:
    """还缺哪些阈值、哪些方向没定：给填表人一张清单，避免"看着能跑就当齐了"。"""
    missing: list[str] = []
    for region, body in config.get("regions", {}).items():
        for abbr, item in body.get("indicators", {}).items():
            spec = GradeSpec(abbr=abbr, name=item.get("name", abbr),
                             direction=item.get("direction", ""),
                             bounds=[float(x) for x in item.get("bounds", [])],
                             ranges=[(float(a), float(b), int(g)) for a, b, g in item.get("ranges", [])],
                             outside_grade=item.get("outside_grade"))
            for problem in validate_spec(spec):
                missing.append("[{}] {}".format(region, problem))
            if not item.get("verified", False):
                missing.append("[{}] {}：阈值未核（{}）".format(
                    region, abbr, item.get("evidence", "无证据说明")))
    missing.extend(catalog_gaps(config))
    missing.extend(check_direction_consistency(config))
    return missing


def render_stats(result: Mapping[str, Any]) -> str:
    lines = ["区域：{}".format(result["region"])]
    for abbr, st in result["stats"].items():
        cells = " ".join(
            "{}级{}".format(g, st["counts"][g]) for g in GRADES)
        share = st["share"]
        pct = " ".join(
            "{}%".format(round(share[g] * 100, 1)) if share[g] is not None else "-" for g in GRADES)
        lines.append("{}（{}）：已分级 {}，未分级 {}｜{}｜占比 {}".format(
            abbr, st["name"], st["graded"], st["null"], cells, pct))
    if result["unverified"]:
        lines.append("UNVERIFIED 阈值：{}（须对原文复核后方可用于正式成果）".format(
            "、".join(result["unverified"])))
    return "\n".join(lines)
