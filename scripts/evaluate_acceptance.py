"""外业回传后的计算入口：符合性评价、等级评价、三口径核算与报告底稿。

用法：
    python scripts/evaluate_acceptance.py --input project.json --out results/
        [--report] [--accounting]

输入 JSON：
{
  "meta": {"project_name": "...", "project_number": "...", "county": "示例区"},
  "units": [{"unit": "...", "parcels": ["1"], "area_mu": 35.4, "land_use": "旱地",
             "region": "华南区", "indicators": {...}, "sources": {...}, "direct_fail": {...}}],
  "accounting": {"occupied_all": [...], "occupied_nonfarm": [...], "supplementary": [...]}
}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from acceptance import accounting, conformity, grade_eval, report_builder  # noqa: E402


def evaluate(payload: dict) -> dict:
    meta = dict(payload.get("meta") or {})
    county = meta.get("county") or meta.get("县") or ""
    results, grades, units = {}, {}, []
    for unit in payload.get("units") or []:
        uid = str(unit.get("unit") or "").strip()
        if not uid:
            raise ValueError("units 中存在空评价单元编号")
        region = grade_eval.region_guard(county, unit.get("region")) if county else unit.get("region")
        if not region:
            raise ValueError(f"单元 {uid} 未提供 region，且 meta.county 为空")
        conformity_result = conformity.evaluate(unit)
        grade_result = grade_eval.evaluate_unit(region, unit.get("indicators") or {})
        results[uid] = conformity_result
        grades[uid] = grade_result
        units.append({"unit": uid, "parcels": unit.get("parcels") or [],
                      "area_mu": unit.get("area_mu") or 0,
                      "land_use": unit.get("land_use") or "",
                      "region": region})
    output = {"meta": meta, "units": units, "符合性": results, "等级": grades}
    if payload.get("accounting"):
        item = payload["accounting"]
        output["核算"] = accounting.account(
            item.get("occupied_all"), item.get("occupied_nonfarm"), item.get("supplementary"),
            supplementary_source=item.get("supplementary_source") or "市级鉴定意见")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="补充耕地质量鉴定计算")
    parser.add_argument("--input", required=True, help="输入 JSON")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--report", action="store_true", help="生成县级初鉴报告底稿 DOCX")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    output = evaluate(payload)
    os.makedirs(args.out, exist_ok=True)
    result_path = os.path.join(args.out, "评价结果.json")
    with open(result_path, "w", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
    if args.report:
        report_path = os.path.join(args.out, "县级补充耕地质量初鉴报告_底稿.docx")
        output["报告"] = report_builder.build_report(
            output["units"], output["符合性"], output["等级"], report_path,
            meta=output["meta"])
        with open(result_path, "w", encoding="utf-8") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"结果": result_path, "报告": output.get("报告", {}).get("path"),
                      "符合性状态": {key: value["status"] for key, value in output["符合性"].items()},
                      "等级状态": {key: value["status"] for key, value in output["等级"].items()}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
