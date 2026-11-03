"""使用生成工程的副本验证失败检测和单幅续跑；不修改源工程。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import arcpy
import arcpy.mp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance.map_validation import verify_export_artifact
from acceptance.task_state import _atomic_json


def export_plan(plan, path):
    _atomic_json(str(path), plan)
    process = subprocess.run([sys.executable, "-m", "acceptance.map_export", str(path)],
                             cwd=str(ROOT), env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    if process.returncode:
        raise RuntimeError(process.stdout + process.stderr)
    with open(path.with_suffix(".result.json"), encoding="utf-8") as stream:
        return json.load(stream)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("验证目录非空，拒绝覆盖")
    root.mkdir(parents=True, exist_ok=True)
    base = json.load(open(args.plan, encoding="utf-8"))
    selected = next(row for row in base["targets"] if row.get("uid") is not None)
    results = []
    for scenario in ("hidden_north_arrow", "empty_organization", "invalid_query", "missing_imagery", "corrupt_and_resume"):
        folder = root / scenario
        folder.mkdir()
        project = folder / "test.aprx"
        source_project = arcpy.mp.ArcGISProject(base["project"])
        source_project.saveACopy(str(project))
        del source_project
        check_project = arcpy.mp.ArcGISProject(str(project))
        broken = check_project.listBrokenDataSources()
        if broken:
            raise AssertionError("测试工程副本连接未保持：{}".format([item.name for item in broken]))
        del check_project
        plan = dict(base, project=str(project), targets=[dict(selected)])
        target = plan["targets"][0]
        for ext in ("jpg", "png", "pdf"):
            target[ext] = str(folder / ("map." + ext))
        if scenario in ("hidden_north_arrow", "empty_organization"):
            aprx = arcpy.mp.ArcGISProject(str(project))
            layout = aprx.listLayouts()[0]
            for element in layout.listElements():
                if scenario == "hidden_north_arrow" and element.name == "North Arrow":
                    element.visible = False
                if scenario == "empty_organization" and element.name == "编制单位":
                    element.text = "编制单位："
            aprx.save()
            del layout, aprx
        if scenario == "invalid_query":
            plan["filter_fields"] = dict(plan.get("filter_fields", {}), **{"设计采样点": "MISSING_FIELD"})
        if scenario == "missing_imagery":
            target["imagery_path"] = str(folder / "missing.tif")
        path = folder / "plan.json"
        result = export_plan(plan, path)
        row = result["outputs"][0]
        if scenario != "corrupt_and_resume":
            if row["status"] != "FAIL":
                raise AssertionError("{} 未被识别为失败".format(scenario))
            required_cause = {"hidden_north_arrow": "North Arrow", "empty_organization": "编制单位",
                              "invalid_query": "定义查询失败", "missing_imagery": "影像切换失败"}[scenario]
            if not any(required_cause in str(check) and check.get("status") == "FAIL" for check in row["checks"]):
                raise AssertionError("{} 没有命中预期的缺陷原因：{}".format(scenario, row["checks"]))
            results.append({"scenario": scenario, "detected": True, "checks": row["checks"]})
        else:
            if row["status"] == "FAIL":
                raise AssertionError("正常基线出图失败：{}".format(row["checks"]))
            Path(row["jpg"]).write_bytes(b"corrupt-jpeg")
            restored = export_plan(plan, path)["outputs"][0]
            if verify_export_artifact(restored["jpg"], "jpg")["status"] != "PASS":
                raise AssertionError("损坏 JPG 未被重建")
            modified = os.stat(restored["jpg"]).st_mtime_ns
            reused = export_plan(plan, path)["outputs"][0]
            if not reused.get("reused") or os.stat(reused["jpg"]).st_mtime_ns != modified:
                raise AssertionError("有效的单幅成果没有按断点复用")
            results.append({"scenario": scenario, "corruption_repaired": True, "valid_artifacts_reused": True})
        _atomic_json(str(root / "failure_controls_receipt.json"), {"checks": results})
        print("VERIFIED", scenario, flush=True)
    print("FAILURE_CONTROLS_OK", len(results))


if __name__ == "__main__":
    main()
