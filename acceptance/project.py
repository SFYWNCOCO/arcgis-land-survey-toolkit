"""Project initialization and data access for the acceptance workflow."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from acceptance.delivery import MANUAL_REVIEW_KEYS, PROJECT_REVIEW_KEYS, REEVALUATION_REVIEW_KEYS
from acceptance.crs import parse_wkid


def create_project(
    root, county_code, county_name, project_id, epsg, delivery_level=None,
    project_type=None, pollution_report_required=None, reevaluation=None,
):
    epsg = parse_wkid(epsg, "项目坐标系 WKID", required=True)
    if delivery_level not in (None, "county_initial", "city_final"):
        raise ValueError("成果提交层级必须为县级初鉴或市级最终评价")
    if project_type not in (None, "project", "non_project"):
        raise ValueError("项目类型必须为项目类或非项目类")
    for label, value in (("污染报告是否适用", pollution_report_required), ("是否复评", reevaluation)):
        if value not in (None, True, False):
            raise ValueError(label + "必须明确选择是/否")
    if len(county_code) != 6 or not county_code.isdigit():
        raise ValueError("县级行政区划代码必须为 6 位数字")
    for label, value in (("县名称", county_name), ("项目编号", project_id)):
        if not value or value in (".", "..") or re.search(r'[\\/:*?"<>|]', value):
            raise ValueError(label + " 为空或含路径非法字符")
    county_root = Path(root).expanduser().resolve() / (county_code + county_name)
    project = county_root / project_id
    if project.exists():
        raise FileExistsError("项目目录已存在，不覆盖：" + str(project))
    county_root.mkdir(parents=True, exist_ok=True)
    staging = county_root / ("." + project_id + ".init-" + uuid.uuid4().hex[:12])
    staging.mkdir()
    try:
        for name in ("数据库成果", "图件成果", "文字成果", "附件", "附件/相关举证照片"):
            (staging / name).mkdir(parents=True, exist_ok=True)
        staged_gdb = staging / "数据库成果" / (project_id + ".gdb")
        final_gdb = project / "数据库成果" / (project_id + ".gdb")
        script_dir = str(Path(__file__).resolve().parents[1] / "scripts")
        import sys
        inserted = script_dir not in sys.path
        if inserted:
            sys.path.insert(0, script_dir)
        try:
            from setup_quality_gdb import create_quality_gdb
            setup = create_quality_gdb(staged_gdb, epsg)
        finally:
            if inserted:
                sys.path.remove(script_dir)

        if isinstance(setup, dict):
            setup["geodatabase"] = str(final_gdb)
            setup_manifest = staged_gdb.parent / (staged_gdb.stem + "_setup.json")
            if setup_manifest.exists():
                setup_manifest.write_text(json.dumps(setup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reviews = dict.fromkeys(MANUAL_REVIEW_KEYS, False)
        if project_type == "project":
            reviews.update(dict.fromkeys(PROJECT_REVIEW_KEYS, False))
        if reevaluation is True:
            reviews.update(dict.fromkeys(REEVALUATION_REVIEW_KEYS, False))
        manifest = {
            "schema_version": 1, "project_id": project_id, "county_code": county_code,
            "county_name": county_name, "epsg": epsg, "geodatabase": str(final_gdb),
            "delivery_level": delivery_level, "project_type": project_type,
            "pollution_report_required": pollution_report_required, "reevaluation": reevaluation,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source": "广西补充耕地质量验收作业指导书 v1.1，10.1–10.4、附录I",
            "state": "工作库已初始化；待导入经确认的源图斑/CAD/RTK并人工审核",
        }
        (staging / "project_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery_manifest = {
            "schema_version": 1, "source": manifest["source"],
            "county_code": county_code, "county_name": county_name,
            "project_id": project_id, "delivery_level": delivery_level,
            "project_type": project_type,
            "pollution_report_required": pollution_report_required,
            "reevaluation": reevaluation, "epsg": epsg,
            "manual_reviews": reviews,
        }
        delivery_manifest_path = staging / "delivery_manifest.json"
        delivery_manifest_path.write_text(json.dumps(delivery_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if project.exists():
            raise FileExistsError("项目目录在初始化过程中已被创建，不覆盖：" + str(project))
        staging.rename(project)
        return {
            "project": str(project), "gdb": str(final_gdb), "setup": setup,
            "manifest": str(project / "project_manifest.json"),
            "delivery_manifest": str(project / "delivery_manifest.json"),
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
