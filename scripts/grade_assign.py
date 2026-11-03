#!/usr/bin/env python
"""耕地质量监测指标分级赋值（ArcGIS Pro / Python 3 版命令行工具）。

与文章里 ArcMap 10.8 + Python 2.7 的脚本相比，这里的关键差别：
    1. 阈值在 `config/grade_thresholds.json`，不在代码里——原文没给全的表不能靠感觉补齐；
    2. 分级核心在 `acceptance/grading.py`（纯 Python，可脱机测试），本脚本只做 arcpy 读写；
    3. 输出字段重名默认报错（不自动加 _1），避免重跑后分不清哪份是本次结果。

用法：
    # 只体检阈值配置，不碰数据（不需要 arcpy 许可）
    python scripts/grade_assign.py --check

    # 真正的批量赋值
    python scripts/grade_assign.py -i <gdb/要素类或shp> -r 华南区 -o 分级报告.json
    python scripts/grade_assign.py -i <...> -r 西南区 --mapping mapping.json --overwrite

字段约定：
    输入字段名用指标缩写（OM、TN、AP……）或带后缀（OM_2023），先精确后前缀匹配；
    输出字段 `FJ_<缩写>`，别名 `<指标名>_等级`，值 1—5，未测为空。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance import grading  # noqa: E402


def _require_arcpy():
    try:
        import arcpy  # noqa: F401
    except ImportError:  # pragma: no cover - 取决于运行环境
        sys.exit("此操作需要 ArcGIS Pro 的 Python 环境（arcpy）。仅体检配置请用 --check。")
    import arcpy
    return arcpy


def _existing_fj_fields(arcpy, table: str) -> dict[str, str]:
    return {f.name.upper(): f.name for f in arcpy.ListFields(table)
            if f.name.upper().startswith(grading.OUT_PREFIX)}


def _read_records(arcpy, table: str, fields: list[str]) -> list[dict]:
    rows: list[dict] = []
    with arcpy.da.SearchCursor(table, fields) as cur:
        for values in cur:
            rows.append(dict(zip(fields, values)))
    return rows


def _ensure_fields(arcpy, table: str, mapping: dict[str, str], specs) -> list[str]:
    created: list[str] = []
    for abbr in mapping:
        name = grading.OUT_PREFIX + abbr
        if name.upper() in _existing_fj_fields(arcpy, table):
            continue
        arcpy.management.AddField(table, name, "SHORT", field_alias="{}_{}".format(
            specs[abbr].name, "等级"))
        created.append(name)
    return created


def _write_back(arcpy, table: str, key_field: str, rows: list[dict], mapping: dict[str, str]) -> int:
    out_fields = [grading.OUT_PREFIX + abbr for abbr in mapping]
    updated = 0
    with arcpy.da.UpdateCursor(table, [key_field] + out_fields) as cur:
        index = {row[key_field]: row for row in rows}
        for values in cur:
            row = index.get(values[0])
            if row is None:
                continue
            cur.updateRow([values[0]] + [row.get(f) for f in out_fields])
            updated += 1
    return updated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="耕地质量监测指标分级赋值（1—5 级）")
    ap.add_argument("-i", "--input", help="输入要素类或 shp")
    ap.add_argument("-r", "--region", help="区域：西南区 / 华南区")
    ap.add_argument("-c", "--config", default=str(grading.DEFAULT_CONFIG), help="阈值配置 JSON")
    ap.add_argument("-o", "--out", help="分级统计报告 JSON 输出路径")
    ap.add_argument("--mapping", help="显式字段映射 JSON：{指标缩写: 字段名}")
    ap.add_argument("--policy", choices=["strict", "first"], default="strict",
                    help="前缀命中多字段时的策略；strict 报歧义跳过（默认）")
    ap.add_argument("--key", default="BSM", help="回写用的唯一键字段，默认 BSM")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的 FJ_ 字段")
    ap.add_argument("--check", action="store_true", help="只体检阈值配置与缺失清单")
    ap.add_argument("--county", help="项目所在县（市、区）全称，用于校验区域选择是否正确")
    args = ap.parse_args(argv)

    config = grading.load_thresholds(args.config)
    if args.check:
        items = grading.checklist(config)
        print("阈值配置体检：{}".format(args.config))
        for line in items:
            print("  - " + line)
        missing_zones = grading.zones_without_thresholds(config)
        if missing_zones:
            print("无阈值的二级农业区（不能套用邻近区）：")
            for z in missing_zones:
                print("  - {}（{}）：{} 个县".format(z["zone"], z["first_level"], z["county_count"]))
        print("共 {} 项待处理。".format(len(items)))
        return 0

    if not (args.input and args.region):
        ap.error("需要 -i 与 -r（或用 --check）")

    if args.county:
        try:
            guard = grading.region_guard(args.county, args.region, config)
        except KeyError as exc:
            sys.exit("区域守卫未通过：{}".format(exc.args[0]))
        if not guard["ok"]:
            sys.exit("区域守卫未通过：{}".format(guard["problem"]))
        print("区域守卫通过：{} → {}（{}），阈值区 {}。".format(
            guard["county"], guard["zone"], guard["first_level"], guard["expected"]))
    else:
        print("[提示] 未传 --county，跳过“县所属区 vs 所选区域”校验；"
              "选错区会让阈值、权重全套错位，建议补上。")

    specs = grading.specs_for(config, args.region)
    arcpy = _require_arcpy()

    desc = arcpy.Describe(args.input)
    field_names = [f.name for f in arcpy.ListFields(args.input)]
    if args.mapping:
        mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
        missing = [f for f in mapping.values() if f not in field_names]
        if missing:
            sys.exit("mapping 指向不存在的字段：{}".format("、".join(missing)))
        unsupported = sorted(set(mapping) - set(specs))
        if unsupported:
            sys.exit("以下指标在 {} 未配置阈值，无法分级：{}；请先在 {} 补齐或从 mapping 移除。".format(
                args.region, "、".join(unsupported), args.config))
    else:
        mapping, issues = grading.match_fields(field_names, specs, policy=args.policy)
        for issue in issues:
            print("[字段匹配] " + issue)
    if not mapping:
        sys.exit("没有匹配到任何指标字段，未做任何写入。")

    existing = _existing_fj_fields(arcpy, args.input)
    clash = [grading.OUT_PREFIX + a for a in mapping if (grading.OUT_PREFIX + a).upper() in existing]
    if clash and not args.overwrite:
        sys.exit("输出字段已存在：{}；确认要覆盖请加 --overwrite".format("、".join(clash)))

    key_field = args.key if args.key in field_names else None
    if key_field is None:
        sys.exit("找不到键字段 {}，无法安全回写。".format(args.key))

    created = _ensure_fields(arcpy, args.input, mapping, specs)
    read_fields = [key_field] + sorted({mapping[a] for a in mapping})
    records = _read_records(arcpy, args.input, read_fields)
    result = grading.assign(records, specs, mapping, args.region)
    updated = _write_back(arcpy, args.input, key_field, result["rows"], mapping)

    # 回读校验：写入字段非空数应等于 已分级数
    check = {abbr: 0 for abbr in mapping}
    out_fields = [grading.OUT_PREFIX + a for a in mapping]
    with arcpy.da.SearchCursor(args.input, out_fields) as cur:
        for values in cur:
            for abbr, value in zip(mapping, values):
                if value is not None:
                    check[abbr] += 1
    mismatched = {a: (result["stats"][a]["graded"], check[a]) for a in mapping
                  if result["stats"][a]["graded"] != check[a]}

    report = {
        "input": args.input,
        "region": args.region,
        "spatial_reference": getattr(desc.spatialReference, "name", ""),
        "created_fields": created,
        "mapping": mapping,
        "updated_rows": updated,
        "readback": check,
        "readback_mismatch": mismatched,
        "stats": result["stats"],
        "unverified": result["unverified"],
        "source": config.get("source", {}),
        "usage_boundary": config.get("source", {}).get("usage_boundary", ""),
        "issues": result["issues"][:200],
        "issue_count": len(result["issues"]),
    }
    print(grading.render_stats(result))
    print("\n写入字段 {} 个，更新记录 {} 条。".format(len(created), updated))
    if mismatched:
        print("[回读不一致] {}".format(mismatched))
    if result["unverified"]:
        print("\n注意：{} 的阈值未经原文核对，本结果标 UNVERIFIED，不得直接用于正式成果。".format(
            "、".join(result["unverified"])))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告：{}".format(args.out))
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
