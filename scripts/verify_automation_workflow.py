"""ArcGIS Pro Python 离线流程实测。合成测试 DOM 明确标注，原始图斑只读。"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import arcpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import oneclick as O
from acceptance import task_state as T
from acceptance.map_validation import verify_export_artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--gp", action="store_true")
    parser.add_argument("--query-scope", action="store_true")
    options = parser.parse_args()
    root = Path(options.out).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("验证目录非空，拒绝覆盖：{}".format(root))
    root.mkdir(parents=True, exist_ok=True)
    source_before = T._fingerprint_path(options.source)
    desc = arcpy.Describe(options.source)
    extent = desc.extent
    width, height = extent.XMax - extent.XMin, extent.YMax - extent.YMin
    cx, cy = (extent.XMin + extent.XMax) / 2, (extent.YMin + extent.YMax) / 2
    pixel = max(width, height) * 3.5 / 1200
    xmin, ymin = cx - width * 1.75, cy - height * 1.75
    columns, rows = int(np.ceil(width * 3.5 / pixel)), int(np.ceil(height * 3.5 / pixel))
    yy, xx = np.indices((rows, columns))
    array = np.stack([55 + (xx // 30 % 5) * 25, 80 + (yy // 35 % 6) * 22,
                      35 + ((xx + yy) // 50 % 4) * 30]).astype("uint8")
    dom = str(root / "合成测试DOM_不得报件.tif")
    arcpy.NumPyArrayToRaster(array, arcpy.Point(xmin, ymin), pixel, pixel).save(dom)
    arcpy.management.DefineProjection(dom, desc.spatialReference)
    with open(dom + ".source.json", "w", encoding="utf-8") as stream:
        json.dump({"source_label": "合成测试DOM（非现场影像）", "demo_only": True,
                   "acquired": "无真实拍摄日期；仅软件验证", "license": "仅用于插件测试"}, stream, ensure_ascii=False)
    output = str(root / "results")
    metadata = {"org": "插件自动化验证（合成DOM）", "drawn_by": "自动测试",
                "date": datetime.date.today().isoformat()}
    if options.gp:
        toolbox_source = ROOT / "toolboxes" / "GridSampling.pyt"
        runtime = tempfile.TemporaryDirectory(prefix="grid_sampling_toolbox_", dir=str(ROOT / "temp"))
        toolbox_path = Path(runtime.name) / toolbox_source.name
        shutil.copy2(toolbox_source, toolbox_path)
        toolbox = arcpy.ImportToolbox(str(toolbox_path), "automationsmoke")
        os.environ["GRID_SAMPLING_ROOT"] = str(ROOT)
        first_oid = next(iter(arcpy.da.SearchCursor(options.source, ["OID@"])))[0]
        result = toolbox.GridSamplingOneClick(source=options.source, out_dir=output,
                    imagery_raster=dom, dpi=options.dpi, org=metadata["org"],
                    selection_oids="" if options.query_scope else str(first_oid), task_action="run",
                    source_where_clause="{} = {}".format(desc.OIDFieldName, first_oid) if options.query_scope else "")
        report_path = result.getOutput(result.outputCount - 1)
        if not os.path.isfile(report_path):
            raise AssertionError("GP 未返回可读取的成果报告：{}".format(report_path))
        with open(report_path, encoding="utf-8") as stream:
            report = json.load(stream)
    else:
        report = O.run(options.source, output, imagery_raster=dom, dpi=options.dpi, meta=metadata)
    source_after = T._fingerprint_path(options.source)
    if source_before != source_after:
        raise AssertionError("原始图斑文件指纹发生变化")
    artifact_checks = [verify_export_artifact(row.get(ext, ""), ext)
                       for row in report.get("出图", {}).get("outputs", []) for ext in ("jpg", "png", "pdf")]
    receipt = {"source_unchanged": True, "source_fingerprint": source_after,
               "arcgis_version": arcpy.GetInstallInfo()["Version"], "synthetic_dom": dom,
               "report": report["成果报告路径"], "parcel_count": report["图斑数"],
               "unit_count": report["评价单元数"], "sample_count": report["网格"]["设计点"],
               "map_status": report.get("出图", {}).get("status"),
               "map_count": len(report.get("出图", {}).get("outputs", [])),
               "artifact_checks": artifact_checks, "mode": "GP" if options.gp else "CORE"}
    T._atomic_json(str(root / "verification_receipt.json"), receipt)
    print(json.dumps({key: value for key, value in receipt.items() if key not in ("artifact_checks", "source_fingerprint")}, ensure_ascii=False))
    if not artifact_checks or any(row["status"] == "FAIL" for row in artifact_checks):
        raise AssertionError("存在导出文件复读失败，见 verification_receipt.json")
    if receipt["map_status"] == "FAIL":
        raise AssertionError("存在自动图件失败项，见一键成果报告")


if __name__ == "__main__":
    main()
