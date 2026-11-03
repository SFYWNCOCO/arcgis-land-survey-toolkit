"""评估集：用固定样例跑资料检查（可选准备），比较期望与实际并输出评估报告。

样例现场生成，不依赖外部数据；真实项目可用 --real 追加一个样例。
覆盖：正常图斑、重复编号、空几何、缺编号字段、超 50 亩单元（需 --prepare）。

用法（必须 ArcGIS Pro 自带 Python，需要 arcpy）：
  "<Pro>\\bin\\Python\\envs\\arcgispro-py3\\python.exe" scripts/eval_suite.py
  ... scripts/eval_suite.py --prepare --real "<某项目>.shp"
  ... scripts/eval_suite.py --out out\\eval

输出：<out>/eval-<时间>.json 与 .md（通过率、逐样例耗时、期望/实际差异）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WKID = 4524          # CGCS2000 3 度带 36 带，和示例区项目一致
ORIGIN_X = 36500000  # 假东，落到带内即可
ORIGIN_Y = 2620000


def build_case(work: Path, name: str, spec) -> str:
    """按 spec 生成一个临时 shp：spec = {"parcels": [(编号, 边长米, 有几何)], "fields": (...)}。"""
    import arcpy
    folder = work / name
    folder.mkdir(parents=True, exist_ok=True)
    fc = str(folder / "图斑.shp")
    sr = arcpy.SpatialReference(WKID)
    arcpy.management.CreateFeatureclass(str(folder), "图斑.shp", "POLYGON", spatial_reference=sr)
    fields = tuple(spec.get("fields") or ("DKBH", "LANDTYPE"))
    for field in fields:
        arcpy.management.AddField(fc, field, "TEXT", field_length=30)
    offset = 0
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + list(fields)) as cursor:
        for parcel_id, side, has_geometry in spec["parcels"]:
            geometry = None
            if has_geometry:
                x = ORIGIN_X + offset
                y = ORIGIN_Y
                offset += side + 10
                geometry = arcpy.Polygon(arcpy.Array([
                    arcpy.Point(x, y), arcpy.Point(x + side, y),
                    arcpy.Point(x + side, y + side), arcpy.Point(x, y + side)]), sr)
            values = [parcel_id if field == "DKBH" else "旱地" for field in fields]
            cursor.insertRow([geometry] + values)
    return fc


def cases(prepare: bool):
    """(样例名, spec, 动作, 期望状态)。期望值来自课件口径：重复编号/空几何/缺字段必须被拦。"""
    items = [
        ("clean_3parcels", {"parcels": [("P1", 100, True), ("P2", 100, True), ("P3", 100, True)]},
         "check", "PASS"),
        ("duplicate_ids", {"parcels": [("P1", 100, True), ("P1", 100, True)]}, "check", "FAIL"),
        ("null_geometry", {"parcels": [("P1", 100, True), ("P2", 100, False)]}, "check", "FAIL"),
        ("missing_id_field", {"parcels": [("P1", 100, True)], "fields": ("AAA",)},
         "check", "FAIL"),
    ]
    if prepare:
        items.append(("oversize_unit", {"parcels": [("BIG", 400, True)]}, "prepare", "FAIL"))
    return items


def run_case(fc: str, action: str, work: Path) -> dict:
    from acceptance import oneclick
    out_dir = str(work / "out")
    started = time.time()
    try:
        report = oneclick.run(fc, out_dir, task_action=action, make_maps=False)
        status = report.get("状态") or report.get("技术状态")
        return {"status": status, "elapsed_s": round(time.time() - started, 1),
                "units": report.get("评价单元数"), "error": ""}
    except Exception as exc:  # noqa: BLE001 - 评估要记录失败而不是中断整批
        return {"status": "EXCEPTION", "elapsed_s": round(time.time() - started, 1),
                "units": None, "error": "{}: {}".format(type(exc).__name__, str(exc)[:200])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="补充耕地验收评估集")
    parser.add_argument("--out", default=str(ROOT / "out" / "eval"), help="评估报告目录")
    parser.add_argument("--prepare", action="store_true", help="追加超 50 亩单元的 prepare 样例")
    parser.add_argument("--real", default="", help="可选：真实项目 shp，只跑 check")
    parser.add_argument("--keep", action="store_true", help="保留临时样例数据（默认删除）")
    args = parser.parse_args(argv)

    try:
        import arcpy  # noqa: F401
    except ImportError:
        print("需要在 ArcGIS Pro 自带的 Python 下运行（arcpy 不可用）")
        return 2

    import shutil
    work = Path(args.out) / "_cases"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, spec, action, expected in cases(args.prepare):
        try:
            fc = build_case(work, name, spec)
        except Exception as exc:  # noqa: BLE001 - 生成失败也要计入评估
            rows.append({"case": name, "action": action, "expected": expected,
                         "actual": "BUILD_FAILED", "passed": False,
                         "error": "{}: {}".format(type(exc).__name__, str(exc)[:200])})
            continue
        result = run_case(fc, action, work / name)
        passed = result["status"] == expected
        rows.append({"case": name, "action": action, "expected": expected,
                     "actual": result["status"], "passed": passed, "elapsed_s": result["elapsed_s"],
                     "units": result["units"], "error": result["error"]})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}（{action}）期望 {expected} 实际 {result['status']}"
              f" {result['elapsed_s']}s")

    if args.real:
        result = run_case(args.real, "check", work / "real")
        passed = result["status"] == "PASS"
        rows.append({"case": "real_project", "action": "check", "expected": "PASS",
                     "actual": result["status"], "passed": passed,
                     "elapsed_s": result["elapsed_s"], "units": result["units"],
                     "error": result["error"]})
        print(f"[{'PASS' if passed else 'FAIL'}] real_project（check）期望 PASS 实际 {result['status']}")

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)

    total = len(rows)
    passed_count = sum(1 for row in rows if row["passed"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": stamp, "total": total, "passed": passed_count,
               "pass_rate": round(passed_count / total, 3) if total else 0, "cases": rows}
    (out / f"eval-{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    lines = [f"# 评估报告 {stamp}", "",
             f"- 样例 {total}，通过 {passed_count}，通过率 {payload['pass_rate']}", "",
             "| 样例 | 动作 | 期望 | 实际 | 耗时s | 结果 |", "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            row["case"], row["action"], row["expected"], row["actual"],
            row.get("elapsed_s", ""), "PASS" if row["passed"] else "FAIL"))
    (out / f"eval-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n通过 {passed_count}/{total}；报告：{out / ('eval-' + stamp + '.md')}")
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
