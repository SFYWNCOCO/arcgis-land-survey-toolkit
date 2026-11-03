"""Prepare and audit the Appendix I delivery folder for supplementary farmland QA.

This tool only creates empty folders/a manifest and reads submitted files/GDBs.
It never edits either of the two transferred parcel layers. The evaluation-unit
feature class is a derived deliverable and must be created from reviewed data.

Use --init once to create a package skeleton, then fill it and run --check with
ArcGIS Pro Python. PDF content, signatures, CMA scope, map composition, and
photo categories remain explicit manual review items.

Example:
  python delivery_package.py --init --root D:/deliveries --county-code 990002 \
    --county-name XX县 --project-id F99000220261461 --epsg 4525
  python delivery_package.py --check --root D:/deliveries/990002XX县/F99000220261461
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "delivery_manifest.json"
REPORT_NAME = "delivery_check_report.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from acceptance.delivery import (  # noqa: E402 - 需先把工具包根加入 sys.path
    MANUAL_REVIEW_KEYS,
    PROJECT_REVIEW_KEYS,
    REEVALUATION_REVIEW_KEYS,
)

# Appendix G (作业指导书 v1.1, pages 50-51). ArcGIS field types map Char->String,
# Float->Single, Double->Double, and Int->SmallInteger/Integer.
APPENDIX_G = [
    ("BSM", "标识码", "String", 30), ("XMMC", "项目名称/批次", "String", 254),
    ("XMBH", "项目编号", "String", 100), ("PJDYBH", "评价单元编号", "String", 30),
    ("DKBH", "地块编号", "String", 30), ("YJNYQ", "一级农业区", "String", 30),
    ("EJNYQ", "二级农业区", "String", 30), ("PXZQMC", "省名称", "String", 50),
    ("CXZQMC", "市名称", "String", 50), ("XZQMC", "县名称", "String", 50),
    ("TXZQMC", "乡镇名称", "String", 100), ("XZCMC", "村名称", "String", 100),
    ("TL", "土类", "String", 60), ("YL", "亚类", "String", 60),
    ("TS", "土属", "String", 60), ("TZ", "土种", "String", 60),
    ("DLBM", "地类编码", "String", 10), ("DLMC", "地类名称", "String", 10),
    ("JZGDMJ", "净增耕地面积", "Double", 15), ("DXBW", "地形部位", "String", 30),
    ("YXTCHD", "有效土层厚度", "Integer", 0), ("ZDGX", "质地构型", "String", 10),
    ("GCZD", "耕层质地", "String", 100), ("TRRZ", "土壤容重", "Single", 8),
    ("OM", "有机质", "Single", 8), ("AP", "有效磷", "Single", 8),
    ("AK", "速效钾", "Single", 8), ("SZYTJ", "水资源条件", "String", 60),
    ("PSNL", "排水能力", "String", 60), ("GCHD", "耕层厚度", "Single", 5),
    ("PH", "酸碱度", "Single", 4), ("HBGD", "海拔", "Integer", 0),
    ("YZHCD", "盐渍化程度", "String", 10), ("NTLWH", "农田林网化程度", "String", 10),
    ("FDXBW", "F 地形部位", "Single", 8), ("FYXTCHD", "F 有效土层厚度", "Single", 8),
    ("FZDGX", "F 质地构型", "Single", 8), ("FGCZD", "F 耕层质地", "Single", 8),
    ("FTRRZ", "F 土壤容重", "Single", 8), ("FOM", "F 有机质", "Single", 8),
    ("FAP", "F 有效磷", "Single", 8), ("FAK", "F 速效钾", "Single", 8),
    ("FSZYTJ", "F 水资源条件", "Single", 8), ("FPSNL", "F 排水能力", "Single", 8),
    ("FGCHD", "F 耕层厚度", "Single", 8), ("FPH", "F 酸碱度", "Single", 8),
    ("FHBGD", "F 海拔", "Single", 8), ("FYZHCD", "F 盐渍化程度", "Single", 8),
    ("FNTLWH", "F 农田林网化程度", "Single", 8),
    ("PJZHZS", "评价综合指数", "Single", 8), ("ZLDJ", "质量等级", "SmallInteger", 2),
    ("GDZLDJ", "耕地质量等级", "Single", 8),
    ("GDNDLSSCQLSP", "耕地年度粮食生产潜力水平", "Single", 8),
    ("DJLSSCQLSP", "单季粮食生产潜力水平", "Single", 8),
]

FOLDERS = (
    "数据库成果", "图件成果", "文字成果", "附件", "附件/相关举证照片",
)
PROTECTED_INPUT_FEATURES = (
    "补充耕地国土变更图斑", "补充耕地质量鉴定图斑",
)
EVALUATION_UNIT_FEATURE = "耕地质量等级评价评价单元图"
REQUIRED_FEATURES = (*PROTECTED_INPUT_FEATURES, EVALUATION_UNIT_FEATURE)
SUPPLEMENTARY_MATERIALS = (
    "CMA资质证书.pdf",
    "CMA资质认定证书附表.pdf",
    "样品交接记录表.pdf",
    "检测原始记录表.pdf",
)
PHOTO_CATEGORIES = (
    "样点四周景观照（每评价单元全景至少4张，覆盖东、南、西、北）",
    "样点近景照（每评价单元至少2张）",
    "现场工作照（每单元至少1张）",
    "剖面照或土钻断面照（每个分样点至少1张）",
    "土壤混合样品采集照（每个分样点至少1张）",
    "四分法工作照（每单元至少1张）",
    "土壤容重样品采集照（每个容重样点至少1张）",
    "土壤侵入体及砾石含量采集照（每个分样点至少1张）",
)
def _safe_segment(value: str, label: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or re.search(r'[\\/:*?"<>|]', value):
        raise ValueError(f"{label} 为空或含路径非法字符：{value!r}")
    return value


def initialize(args: argparse.Namespace) -> Path:
    if not re.fullmatch(r"\d{6}", args.county_code):
        raise ValueError("county-code 必须是 6 位行政区划代码")
    if not args.epsg or args.epsg <= 0:
        raise ValueError("必须按项目资料明确提供 --epsg，不能猜测坐标系")
    county_name = _safe_segment(args.county_name, "county-name")
    project_id = _safe_segment(args.project_id, "project-id")
    root = Path(args.root).expanduser().resolve() / f"{args.county_code}{county_name}" / project_id
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists():
        raise FileExistsError(f"汇交清单已存在，不覆盖：{manifest_path}")
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    reviews = dict.fromkeys(MANUAL_REVIEW_KEYS, False)
    if args.project_type == "project":
        reviews.update(dict.fromkeys(PROJECT_REVIEW_KEYS, False))
    if args.reevaluation:
        reviews.update(dict.fromkeys(REEVALUATION_REVIEW_KEYS, False))
    manifest = {
        "schema_version": 1,
        "source": "广西补充耕地质量验收作业指导书 v1.1 (2026-08-17), section 10 and Appendix I",
        "county_code": args.county_code,
        "county_name": county_name,
        "project_id": project_id,
        "delivery_level": args.level,
        "project_type": args.project_type,
        "pollution_report_required": bool(args.pollution_report_required),
        "reevaluation": bool(args.reevaluation),
        "epsg": args.epsg,
        "manual_reviews": reviews,
    }
    _write_json(manifest_path, manifest)
    print(f"PACKAGE_READY {root}")
    print(f"MANIFEST {manifest_path}")
    return root


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _file_candidates(root: Path, relative: str, extensions: tuple[str, ...] = ()) -> list[Path]:
    exact = root / relative
    allowed_extensions = {ext.lower() for ext in extensions}
    if exact.is_file() and (not extensions or exact.suffix.lower() in allowed_extensions):
        return [exact]
    parent = exact.parent
    if not extensions or not parent.is_dir():
        return []
    stem = exact.name
    matches = [p for p in parent.iterdir() if p.is_file()
               and p.stem == stem and p.suffix.lower() in allowed_extensions]
    priority = {ext.lower(): index for index, ext in enumerate(extensions)}
    return sorted(matches, key=lambda path: (priority[path.suffix.lower()], path.name.casefold()))


def _lab_chain_check(root: Path, manifest: dict) -> dict:
    """Check the v1.1 §6.3.1 different-institution rule without guessing."""
    if manifest.get("delivery_level") != "city_final":
        return {"status": "PENDING_CITY_STAGE", "detail": "县级初鉴阶段仅记录本级检测机构；市级阶段再核对不同机构要求。"}
    chain = manifest.get("lab_chain")
    if not isinstance(chain, dict):
        return {"status": "NEEDS_REVIEW", "detail": "缺少县级初鉴/市级鉴定检测机构及实际检测情况记录。"}
    county, city = chain.get("county_initial"), chain.get("city_final")
    if not isinstance(county, dict) or not isinstance(city, dict):
        return {"status": "NEEDS_REVIEW", "detail": "县级初鉴和市级鉴定两阶段信息均需明确记录。"}
    if type(county.get("performed")) is not bool or type(city.get("performed")) is not bool:
        return {"status": "NEEDS_REVIEW", "detail": "两阶段是否委托检测必须明确填写 true/false。"}
    if not county["performed"] or not city["performed"]:
        missing_evidence = []
        for label, phase in (("县级初鉴", county), ("市级鉴定", city)):
            if phase["performed"] is False:
                evidence = phase.get("non_testing_evidence")
                if not isinstance(evidence, str) or not evidence.strip() or not (root / evidence).is_file():
                    missing_evidence.append(label)
        if missing_evidence:
            return {"status": "NEEDS_REVIEW", "detail": "检测未开展的阶段须提供项目内书面依据：" + "、".join(missing_evidence)}
        return {"status": "NOT_APPLICABLE_WITH_EVIDENCE", "detail": "至少一个阶段未开展检测且有书面依据；不作不同机构比较，也不将其称为规范例外。"}
    county_id, city_id = county.get("institution_id"), city.get("institution_id")
    if not isinstance(county_id, str) or not county_id.strip() or not isinstance(city_id, str) or not city_id.strip():
        return {"status": "NEEDS_REVIEW", "detail": "两阶段均检测时须提供可核验的检测机构统一社会信用代码/唯一登记标识。"}
    county_id, city_id = county_id.strip().upper(), city_id.strip().upper()
    if county_id == city_id:
        return {"status": "FAIL", "detail": "两阶段记录为同一检测机构；不符合指导书 v1.1 §6.3.1 分别委托不同检测机构的要求。"}
    return {
        "status": "NEEDS_REVIEW",
        "detail": "两阶段填写的机构登记标识不同；须对照检测报告、CMA证书/附表或官方登记资料确认法人确为不同机构后人工签认。自动字符串比较不判定机构独立。",
    }


def _filespec(root: Path, relative: str, extensions: tuple[str, ...] = ()) -> Path | None:
    candidates = _file_candidates(root, relative, extensions)
    return candidates[0] if candidates else None


def _check_gdb(gdb: Path, epsg: int) -> list[str]:
    import arcpy
    issues = []
    if not arcpy.Exists(str(gdb)):
        return [f"缺少 FileGDB: {gdb}"]
    with arcpy.EnvManager(workspace=str(gdb)):
        feature_paths = {name: os.path.join(str(gdb), name)
                         for name in (arcpy.ListFeatureClasses() or [])}
        for dataset in arcpy.ListDatasets("", "Feature") or []:
            ds_path = os.path.join(str(gdb), dataset)
            for name in arcpy.ListFeatureClasses(feature_dataset=dataset) or []:
                feature_paths[name] = os.path.join(ds_path, name)
    present = set(feature_paths)
    for name in REQUIRED_FEATURES:
        if name not in present:
            issues.append(f"FileGDB 缺少要素类：{name}")
    unit_name = EVALUATION_UNIT_FEATURE
    if unit_name not in present:
        return issues
    fc = feature_paths[unit_name]
    desc = arcpy.Describe(fc)
    if str(getattr(desc, "shapeType", "")).lower() != "polygon":
        issues.append(f"评价单元图几何类型应为 Polygon，实际为 {getattr(desc, 'shapeType', None)}")
    sr = getattr(desc, "spatialReference", None)
    if epsg and getattr(sr, "factoryCode", None) != epsg:
        issues.append(f"评价单元图坐标系 EPSG:{getattr(sr, 'factoryCode', None)}，预期 EPSG:{epsg}")
    fields = {f.name.upper(): f for f in arcpy.ListFields(fc)}
    type_aliases = {
        "String": {"string"}, "Double": {"double"}, "Single": {"single"},
        "Integer": {"integer"}, "SmallInteger": {"smallinteger", "integer"},
    }
    for name, alias, expected_type, length in APPENDIX_G:
        field = fields.get(name.upper())
        if field is None:
            issues.append(f"评价单元图缺少附录G字段：{name}")
            continue
        actual_type = str(field.type).lower()
        if actual_type not in type_aliases[expected_type]:
            issues.append(f"字段 {name} 类型为 {field.type}，附录G要求 {expected_type}")
        if expected_type == "String" and int(field.length) < length:
            issues.append(f"字段 {name} 长度 {field.length}，附录G要求至少 {length}")
        if str(field.aliasName or "").strip() != alias:
            issues.append(f"字段 {name} 别名为 {field.aliasName!r}，附录G要求 {alias!r}")
    return issues


def check_package(root: Path, manual_reviewed=None, reviewer=None, review_evidence=None) -> dict:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少汇交清单：{manifest_path}；先运行 --init")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pid = manifest["project_id"]
    lab_chain_check = _lab_chain_check(root, manifest)
    failures: list[str] = []
    if lab_chain_check["status"] == "FAIL":
        failures.append(lab_chain_check["detail"])
    checked: list[dict] = []
    version_resolution_reviews: list[str] = []

    def require(relative: str, extensions: tuple[str, ...] = ()) -> None:
        found = _filespec(root, relative, extensions)
        ok = bool(found and found.stat().st_size > 0)
        checked.append({"item": relative, "status": "PRESENT" if ok else "MISSING",
                        "path": str(found) if found else None})
        if not ok:
            failures.append(f"缺少或为空：{relative}")

    def require_directory(relative: str) -> None:
        found = root / relative
        ok = found.is_dir() and any(found.iterdir())
        checked.append({"item": relative, "status": "PRESENT" if ok else "MISSING",
                        "path": str(found) if ok else None})
        if not ok:
            failures.append(f"缺少或为空：{relative}")

    gdb_rel = f"数据库成果/{pid}.gdb"
    gdb = root / gdb_rel
    require_directory(gdb_rel)
    require("图件成果/地理位置图.jpg")
    require("图件成果/采样点位布置图.jpg")

    delivery_level = manifest.get("delivery_level")
    # 作业指导书 v1.1 §10.3 and Appendix I require the opinion form at both
    # county and city submission levels; the city-only exception concerns the
    # report/opinion narrative, not this common form.
    require("文字成果/补充耕地质量鉴定意见表.pdf")
    if delivery_level == "county_initial":
        report_stem = "文字成果/县级补充耕地质量初鉴报告"
        require(report_stem, (".docx", ".doc"))
        report_candidates = _file_candidates(root, report_stem, (".docx", ".doc"))
        extensionless_report = root / report_stem
        if extensionless_report.is_file():
            checked.append({
                "item": "县级报告无扩展名同名文件",
                "status": "NEEDS_REVIEW",
                "path": str(extensionless_report),
                "detail": "该文件不属于允许的 DOCX/DOC 格式；请识别其内容并移出或规范命名。",
            })
            version_resolution_reviews.append(
                "县级补充耕地质量初鉴报告目录存在无扩展名同名文件；请确认内容并移出或规范命名"
            )
        if {path.suffix.lower() for path in report_candidates} >= {".docx", ".doc"}:
            selected_report = report_candidates[0]
            other_reports = report_candidates[1:]
            checked.append({
                "item": "县级报告新旧格式并存",
                "status": "NEEDS_REVIEW",
                "selected_path": str(selected_report),
                "other_candidates": [str(path) for path in other_reports],
                "detail": "优先检查 DOCX；DOC 与 DOCX 同名并存时，须人工核对版本并归档或更新旧稿。自动检查不判定内容一致性。",
            })
            version_resolution_reviews.append(
                "县级补充耕地质量初鉴报告存在 DOC/DOCX 同名版本；已优先选 DOCX，请核对并归档或更新旧稿"
            )
    elif delivery_level == "city_final":
        require("文字成果/市级补充耕地质量鉴定报告", (".doc", ".docx", ".pdf"))
        require("文字成果/市级补充耕地质量鉴定意见", (".doc", ".docx", ".pdf"))

    for name in ("补充耕地质量鉴定函.pdf", "补充耕地质量鉴定申请表.pdf",
                 "实地踏勘表.pdf", "土壤样品采集记录表.pdf",
                 "土壤样品检测报告.pdf", "专家意见.pdf"):
        require(f"附件/{name}")
    for name in SUPPLEMENTARY_MATERIALS:
        found = _filespec(root, f"附件/{name}")
        checked.append({
            "item": f"附件/{name}",
            "status": "PRESENT" if found and found.stat().st_size > 0 else "NOT_SUBMITTED",
            "path": str(found) if found else None,
            "requirement": "supplementary_or_applicability_review",
            "detail": "附录I第10.4(2)列为其他资料；按项目要求、CMA能力范围和样品检测链核查，不作为所有项目的统一硬性缺项。",
        })
    if manifest.get("pollution_report_required") is True:
        require("附件/土壤污染状况检测报告.pdf")
    if manifest.get("project_type") == "project":
        for name, exts in (
            ("项目设计报告", (".pdf",)), ("项目设计批复", (".pdf",)),
            ("竣工验收报告", (".pdf",)), ("竣工验收批复", (".pdf",)),
            ("项目规划图", (".dwg", ".pdf")), ("项目竣工图", (".dwg", ".pdf")),
        ):
            require(f"附件/{name}", exts)
    if manifest.get("reevaluation") is True:
        require("附件/耕地质量等级评价评价单元等级变更前后对比分析表",
                (".xlsx", ".xls", ".pdf"))

    photo_dir = root / "附件" / "相关举证照片"
    photos = [p for p in photo_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".jpg"] if photo_dir.is_dir() else []
    checked.append({"item": "附件/相关举证照片/*.jpg",
                    "status": "PRESENT" if photos else "MISSING", "count": len(photos)})
    if not photos:
        failures.append("相关举证照片目录没有 JPG 照片")
    checked.append({
        "item": "附件/相关举证照片类别与数量",
        "status": "NEEDS_REVIEW",
        "required_categories": list(PHOTO_CATEGORIES),
        "detail": "依据指导书6.2.5及10.4(1)⑤；数量依评价单元和实际样点确定，须人工逐类核对照片内容与真实性。",
    })

    gdb_issues = []
    appendix_c_check = None
    if gdb.is_dir():
        try:
            gdb_issues = _check_gdb(gdb, int(manifest.get("epsg") or 0))
            from acceptance.qa import _check_appendix_c
            appendix_c_check = _check_appendix_c(str(gdb / "补充耕地质量鉴定图斑"))
            if appendix_c_check.get("status") == "FAIL":
                gdb_issues.append("附录C图层结构、属性值或几何自动质检失败")
        except ImportError:
            gdb_issues = ["数据库结构检查需要 ArcGIS Pro Python（arcpy）"]
        except Exception as exc:
            gdb_issues = [f"数据库检查异常：{type(exc).__name__}: {exc}"]
    failures.extend(gdb_issues)
    reviews = manifest.get("manual_reviews", {})
    if not isinstance(reviews, dict):
        reviews = {}
    required_reviews = list(MANUAL_REVIEW_KEYS)
    lab_review_key = "lab_institution_independence_reviewed"
    if manifest.get("delivery_level") != "city_final":
        required_reviews = [key for key in required_reviews if key != lab_review_key]
    if manifest.get("project_type") == "project":
        required_reviews.extend(PROJECT_REVIEW_KEYS)
    if manifest.get("reevaluation") is True:
        required_reviews.extend(REEVALUATION_REVIEW_KEYS)
    if isinstance(manual_reviewed, str):
        manual_reviewed = [part for part in manual_reviewed.split(";") if part]
    selected_reviews = set(manual_reviewed or ())
    invalid_selections = sorted(selected_reviews - set(required_reviews))
    if invalid_selections:
        failures.append("选择了不适用于本项目的人工核验项：" + ", ".join(invalid_selections))
    manual_record_errors = []
    if selected_reviews and (not str(reviewer or "").strip() or not str(review_evidence or "").strip()):
        manual_record_errors.append("人工核验勾选必须填写审核人和审核依据/底稿编号")
        accepted_reviews = set()
    else:
        accepted_reviews = selected_reviews - set(invalid_selections)
    pending_reviews = [
        key for key in required_reviews
        if reviews.get(key) is not True and key not in accepted_reviews
    ]
    if (manifest.get("delivery_level") == "city_final"
            and lab_chain_check["status"] not in {"PASS", "NOT_APPLICABLE_WITH_EVIDENCE"}
            and lab_review_key not in accepted_reviews
            and lab_review_key not in pending_reviews):
        pending_reviews.append(lab_review_key)
    pending_reviews.extend(version_resolution_reviews)
    if delivery_level not in {"county_initial", "city_final"}:
        pending_reviews.append("delivery_level_selected")
    if manifest.get("project_type") not in {"project", "non_project"}:
        pending_reviews.append("project_type_selected")
    if type(manifest.get("pollution_report_required")) is not bool:
        pending_reviews.append("pollution_report_applicability_selected")
    if type(manifest.get("reevaluation")) is not bool:
        pending_reviews.append("reevaluation_applicability_selected")
    known_review_keys = set(MANUAL_REVIEW_KEYS) | set(PROJECT_REVIEW_KEYS) | set(REEVALUATION_REVIEW_KEYS)
    pending_reviews.extend(
        key for key, done in reviews.items()
        if key not in known_review_keys and done is not True
    )
    status = "FAIL" if failures else ("REVIEW_REQUIRED" if pending_reviews or manual_record_errors else "PASS")
    return {
        "status": status,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "package_root": str(root),
        "project_id": pid,
        "checked_files": checked,
        "failures": failures,
        "pending_manual_reviews": pending_reviews,
        "manual_review_record_errors": manual_record_errors,
        "manual_reviews_confirmed_this_run": sorted(accepted_reviews),
        "manual_review_attestation": {
            "reviewer": str(reviewer).strip(), "evidence": str(review_evidence).strip(),
        } if accepted_reviews else None,
        "database_issues": gdb_issues,
        "appendix_c_check": appendix_c_check,
        "lab_institution_independence": lab_chain_check,
        "checker_modified_input_feature_classes": False,
        "protected_transferred_feature_classes": list(PROTECTED_INPUT_FEATURES),
        "derived_evaluation_unit_feature_class": EVALUATION_UNIT_FEATURE,
    }


def write_html(report: dict, path: Path) -> Path:
    """人读交付清单；只汇总机器检查结果，不替代内容审核。"""
    import html
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(item.get("item"))), html.escape(str(item.get("status"))),
            html.escape(str(item.get("path") or "")))
        for item in report.get("checked_files") or [])
    failures = "".join(f"<li>{html.escape(str(item))}</li>"
                       for item in report.get("failures") or []) or "<li>无</li>"
    pending = "".join(f"<li>{html.escape(str(item))}</li>"
                      for item in report.get("pending_manual_reviews") or []) or "<li>无</li>"
    document = """<!doctype html><meta charset='utf-8'><title>交付清单</title>
<style>body{{font:14px/1.6 "Microsoft YaHei",sans-serif;margin:24px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #999;padding:5px 8px}}
.pass{{color:#2e7d32}}.fail{{color:#c62828}}.review{{color:#ef6c00}}</style>
<h1>交付清单</h1><p>状态：<b class='{cls}'>{status}</b></p>
<h2>缺项/失败</h2><ul>{failures}</ul>
<h2>待人工复核</h2><ul>{pending}</ul>
<h2>文件检查</h2><table><tr><th>项目</th><th>状态</th><th>路径</th></tr>{rows}</table>
<p>说明：文件存在不等于内容合格；签字、CMA 能力、照片真实性仍须人工核验。</p>""".format(
        cls={"PASS": "pass", "FAIL": "fail"}.get(report.get("status"), "review"),
        status=html.escape(str(report.get("status"))), failures=failures, pending=pending, rows=rows)
    path.write_text(document, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init", action="store_true", help="创建目录结构和汇交清单，不覆盖已有清单")
    mode.add_argument("--check", action="store_true", help="检查文件齐备度和评价单元图附录G字段")
    ap.add_argument("--html", action="store_true", help="同时写 交付清单.html")
    ap.add_argument("--root", required=True, help="行政区文件夹的父目录（init）或项目目录（check）")
    ap.add_argument("--county-code")
    ap.add_argument("--county-name")
    ap.add_argument("--project-id")
    ap.add_argument("--level", choices=("county_initial", "city_final"), default="county_initial")
    ap.add_argument("--project-type", choices=("project", "non_project"), default="non_project")
    ap.add_argument("--pollution-report-required", action="store_true")
    ap.add_argument("--reevaluation", action="store_true")
    ap.add_argument("--epsg", type=int, help="项目实际坐标系 EPSG，例如 4523/4524/4525")
    a = ap.parse_args()
    try:
        if a.init:
            for name in ("county_code", "county_name", "project_id"):
                if not getattr(a, name):
                    ap.error(f"--init 必须提供 --{name.replace('_', '-')}")
            if a.epsg is None:
                ap.error("--init 必须按项目资料提供 --epsg")
            initialize(a)
            return 0
        root = Path(a.root).expanduser().resolve()
        report = check_package(root)
        _write_json(root / REPORT_NAME, report)
        if a.html:
            print(f"HTML {write_html(report, root / '交付清单.html')}")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["status"] == "FAIL" else 0
    except (ValueError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
