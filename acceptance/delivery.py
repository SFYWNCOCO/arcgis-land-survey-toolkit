"""Appendix I package preparation and readiness checklist."""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path


FOLDERS = ("数据库成果", "图件成果", "文字成果", "附件", "附件/相关举证照片")
_DELIVERABLE_CATEGORIES = {
    "10.1 数据库成果": ("数据库成果", {".gdb"}),
    "10.2 图件成果": ("图件成果", {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}),
    "10.3 文字成果": ("文字成果", {".doc", ".docx", ".pdf", ".odt"}),
    "10.4 附件成果": ("附件", {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".xlsx", ".xls", ".dwg", ".dxf"}),
}
PHOTO_CATEGORIES = (
    "样点四周景观照：每评价单元全景至少4张，覆盖东、南、西、北；每评价单元近景至少2张",
    "现场工作照：每评价单元至少1张",
    "剖面照或土钻断面照：每个分样点至少1张",
    "土壤混合样品采集照：每个分样点至少1张",
    "四分法工作照：每评价单元至少1张",
    "土壤容重样品采集照：每个容重样点至少1张",
    "土壤侵入体及砾石含量采集照：每个分样点至少1张",
)
MANUAL_REVIEW_KEYS = (
    "source_change_parcels_unchanged",
    "quality_identification_parcels_unchanged",
    "appendix_g_fields_units_and_values_reviewed",
    "appendix_g_precision_and_scale_reviewed",
    "appendix_c_topology_and_values_reviewed",
    "county_or_city_report_content_reviewed",
    "opinion_form_content_reviewed",
    "location_map_boundaries_roads_water_settlements_and_layout_reviewed",
    "sampling_map_scale_points_and_layout_reviewed",
    "three_party_signatures_reviewed",
    "cma_certificate_and_scope_reviewed",
    "photo_categories_reviewed",
    "pollution_report_applicability_reviewed",
    "field_records_and_lab_results_consistency_reviewed",
    "lab_institution_independence_reviewed",
)
PROJECT_REVIEW_KEYS = ("project_attachments_reviewed",)
REEVALUATION_REVIEW_KEYS = ("reevaluation_comparison_reviewed",)
MANUAL_REVIEW_LABELS = {
    "source_change_parcels_unchanged": "数据库：国土变更图斑来源及未改动",
    "quality_identification_parcels_unchanged": "数据库：质量鉴定图斑来源及未改动",
    "appendix_g_fields_units_and_values_reviewed": "数据库：附录G字段、计量单位和值域",
    "appendix_g_precision_and_scale_reviewed": "数据库：附录G精度和比例尺",
    "appendix_c_topology_and_values_reviewed": "数据库：附录C拓扑和属性值",
    "county_or_city_report_content_reviewed": "文字：县级/市级报告内容",
    "opinion_form_content_reviewed": "文字：鉴定意见表/意见内容",
    "location_map_boundaries_roads_water_settlements_and_layout_reviewed": "图件：地理位置图边界、道路、水系、居民点和版式",
    "sampling_map_scale_points_and_layout_reviewed": "图件：采样点位图底图、比例尺、点位和版式",
    "three_party_signatures_reviewed": "附件/文字：三方签字盖章",
    "cma_certificate_and_scope_reviewed": "附件：CMA资质和检测能力范围",
    "photo_categories_reviewed": "附件：举证照片类别、数量和真实性",
    "pollution_report_applicability_reviewed": "附件：污染检测报告适用性/结果",
    "field_records_and_lab_results_consistency_reviewed": "附件：外业记录与实验室结果一致性",
    "lab_institution_independence_reviewed": "附件：县级初鉴与市级鉴定检测机构分别委托核验",
    "project_attachments_reviewed": "项目类附件：设计、批复、竣工验收和图件",
    "reevaluation_comparison_reviewed": "复评附件：等级变化前后对比分析表",
}


def _manifest_check(root, manual_reviewed=None, reviewer=None, review_evidence=None):
    manifest = root / "delivery_manifest.json"
    if not manifest.is_file():
        return None
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    scripts_text = str(scripts)
    inserted = scripts_text not in sys.path
    if inserted:
        sys.path.insert(0, scripts_text)
    try:
        from delivery_package import check_package
        return check_package(
            root, manual_reviewed=manual_reviewed,
            reviewer=reviewer, review_evidence=review_evidence,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {"status": "FAIL", "error": "汇交清单无效：{}: {}".format(type(exc).__name__, exc)}
    except Exception as exc:
        return {"status": "NEEDS_REVIEW", "error": "详细检查未完成：{}: {}".format(type(exc).__name__, exc)}
    finally:
        if inserted:
            sys.path.remove(scripts_text)


def initialize(root, county_code, county_name, project_id):
    if len(county_code) != 6 or not county_code.isdigit():
        raise ValueError("行政区划代码必须为 6 位数字")
    for label, value in (("县名称", county_name), ("项目编号", project_id)):
        if not value or value in (".", "..") or re.search(r'[\\/:*?"<>|]', value):
            raise ValueError(label + " 为空或含路径非法字符")
    target = Path(root).expanduser().resolve() / (county_code + county_name) / project_id
    if target.exists():
        raise FileExistsError("汇交项目已存在，不覆盖：" + str(target))
    for name in FOLDERS:
        (target / name).mkdir(parents=True, exist_ok=True)
    return str(target)


def checklist(root, manual_reviewed=None, reviewer=None, review_evidence=None):
    root = Path(root)
    detailed = _manifest_check(
        root, manual_reviewed=manual_reviewed,
        reviewer=reviewer, review_evidence=review_evidence,
    )
    detailed_pass = bool(detailed and detailed.get("status") == "PASS")
    required = ["数据库成果", "图件成果", "文字成果", "附件", "附件/相关举证照片"]
    entries = []
    for name in required:
        folder = root / name
        entries.append({"item": name, "status": "PASS" if folder.is_dir() else "FAIL", "path": str(folder)})

    for item, (relative_folder, accepted_extensions) in _DELIVERABLE_CATEGORIES.items():
        folder = root / relative_folder
        recognized = []
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_dir() and path.suffix.lower() == ".gdb" and ".gdb" in accepted_extensions:
                    recognized.append(str(path.relative_to(root)))
                elif path.is_file() and path.suffix.lower() in accepted_extensions and path.stat().st_size > 0:
                    recognized.append(str(path.relative_to(root)))
        recognized.sort()
        entries.append({
            "item": item,
            "presence_status": "CANDIDATES_FOUND" if recognized else "NO_RECOGNIZED_FILES",
            "content_status": "PASS" if detailed_pass else "NEEDS_REVIEW",
            "recognized_file_count": len(recognized),
            "candidate_files": recognized,
            "detail": "只按常见文件类型列候选，不验证成果内容、规范、比例尺、签章或版本；不得据此判定成果合格。",
        })
    photo_dir = root / "附件" / "相关举证照片"
    photos = sorted(
        str(path.relative_to(root))
        for path in photo_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".jpg"
    ) if photo_dir.is_dir() else []
    entries.append({
        "item": "举证照片文件",
        "status": "PRESENT" if photos else "FAIL",
        "count": len(photos),
        "candidate_files": photos,
        "detail": "指导书10.4(1)⑤要求提交JPG举证照片。",
    })
    entries.append({
        "item": "举证照片类别及数量",
        "status": "PASS" if detailed_pass else "NEEDS_REVIEW",
        "required_categories": list(PHOTO_CATEGORIES),
        "detail": "依据指导书6.2.5及10.4(1)⑤；须按评价单元和实际样点逐类核对，不因JPG文件存在而自动判定类别齐全。",
    })
    entries.append({
        "item": "外业证据与人工审核",
        "status": "PASS" if detailed_pass else "NEEDS_REVIEW",
        "detail": "需人工确认评价单元、实采记录、照片真实性、签字、检测结论及适用性。",
    })
    if detailed is None:
        entries.append({
            "item": "10.1–10.4规范逐项核验",
            "status": "NEEDS_REVIEW",
            "detail": "未发现 delivery_manifest.json；当前仅能做目录/扩展名初筛，不能据此判定交付齐全。",
        })
    else:
        entries.append({
            "item": "10.1–10.4规范逐项核验",
            "status": detailed.get("status", "NEEDS_REVIEW"),
            "failures": detailed.get("failures", []),
            "pending_manual_reviews": detailed.get("pending_manual_reviews", []),
            "database_issues": detailed.get("database_issues", []),
            "error": detailed.get("error"),
            "detail": "按项目适用性核查附录I必交材料、数据库结构和人工复核项。",
        })
    statuses = [row.get("status") for row in entries] + [row.get("content_status") for row in entries]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif detailed_pass and all(status in {"PASS", "PRESENT", None} for status in statuses):
        overall = "PASS"
    else:
        overall = "NEEDS_REVIEW"
    return {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "广西补充耕地质量验收作业指导书 v1.1，10.1–10.4、附录I",
        "scope": "无清单时为目录/类型初筛；存在交付清单时附加检查必交项、数据库结构和已声明的人工审核项",
        "status": overall,
        "root": str(root.resolve()),
        "detailed_package_check": detailed,
        "checks": entries,
    }


def write_checklist(root, output_json, manual_reviewed=None, reviewer=None, review_evidence=None):
    report = checklist(
        root, manual_reviewed=manual_reviewed,
        reviewer=reviewer, review_evidence=review_evidence,
    )
    p = Path(output_json)
    if p.exists():
        raise FileExistsError("汇交检查报告已存在，不覆盖：" + str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)
