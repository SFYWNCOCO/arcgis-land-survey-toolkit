"""隔离解包后只从 Install 导入插件，验证离线 DOM 与 UNC 图斑。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

import arcpy
import arcpy.mp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dom", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("验证目录非空，拒绝覆盖")
    root.mkdir(parents=True, exist_ok=True)
    unpacked = root / "unpacked"
    unpacked.mkdir()
    with zipfile.ZipFile(args.package) as archive:
        for entry in archive.infolist():
            destination = (unpacked / entry.filename).resolve()
            if os.path.commonpath((str(unpacked), str(destination))) != str(unpacked):
                raise ValueError("安装包包含越界路径")
        archive.extractall(unpacked)
    installed = str(unpacked / "Install")
    source_toolkit = str(Path(__file__).resolve().parents[1]).lower()
    sys.path[:] = [path for path in sys.path if source_toolkit not in str(path).lower()]
    sys.path.insert(0, installed)
    os.chdir(installed)
    from acceptance import oneclick as O
    from acceptance import task_state as T
    from acceptance.map_validation import verify_export_artifact
    if os.path.commonpath((installed, os.path.abspath(O.__file__))) != installed:
        raise AssertionError("插件并非从解包目录加载")
    before = T._fingerprint_path(args.source)
    with arcpy.da.SearchCursor(args.source, ["OID@"]) as cursor:
        first_oid = next(cursor)[0]
    report = O.run(args.source, str(root / "results"), selection_oids=str(first_oid),
                   imagery_raster=args.dom, dpi=300, meta={"org": "隔离安装包离线验证（合成DOM）"})
    if before != T._fingerprint_path(args.source):
        raise AssertionError("源图斑被修改")
    state = T.read_task(str(root / "results"))
    project_path = state["phases"]["maps_built"]["detail"]["project"]
    project = arcpy.mp.ArcGISProject(project_path)
    broken = [item.name for item in project.listBrokenDataSources()]
    del project
    checks = [verify_export_artifact(row.get(ext, ""), ext)
              for row in report["出图"]["outputs"] for ext in ("jpg", "png", "pdf")]
    package_sha = hashlib.sha256(Path(args.package).read_bytes()).hexdigest()
    receipt = {"package_sha256": package_sha, "core_module": O.__file__, "source": args.source,
               "dom": args.dom, "source_unchanged": True, "broken_layers": broken,
               "report_path": report["成果报告路径"], "map_count": len(report["出图"]["outputs"]),
               "artifact_checks": checks, "arcgis_version": arcpy.GetInstallInfo()["Version"]}
    T._atomic_json(str(root / "packaged_offline_receipt.json"), receipt)
    if broken or any(item["status"] != "PASS" for item in checks) or report["技术状态"] == "FAIL":
        raise AssertionError("隔离包验证存在失败项，见 packaged_offline_receipt.json")
    print("PACKAGED_OFFLINE_OK", len(checks), "artifacts", flush=True)


if __name__ == "__main__":
    main()
