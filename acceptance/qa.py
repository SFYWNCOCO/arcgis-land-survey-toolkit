"""Conservative, traceable structural checks for acceptance project data."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .rules import RULES


# 广西补充耕地质量验收作业指导书 v1.1 附录C，PDF第49页（印刷页43）。
# 表中 Char 对应 ArcGIS String，Float 对应 Single，Data 对应 ArcGIS Date。
# 数值字段的“字段长度/小数位数”按源表保存为证据；FileGDB 的 ArcPy
# Field.length 是存储实现属性，不能直接等同于源表的十进制精度/小数位。
APPENDIX_C = (
    ("BSM", "标识码", "String", 30, "M", None),
    ("XDM", "县级行政区代码", "String", 6, "M", None),
    ("XMC", "县级行政区名称", "String", 30, "M", None),
    ("XZDM", "乡镇代码", "String", 9, "M", None),
    ("XZMC", "乡镇名称", "String", 30, "M", None),
    ("BGQDLBM", "变更前地类编码", "String", 5, "M", None),
    ("BGQDLMC", "变更前地类名称", "String", 60, "M", None),
    ("DLBM", "地类编码", "String", 5, "M", None),
    ("DLMC", "地类名称", "String", 60, "M", None),
    ("ZLDWDM", "坐落单位代码", "String", 19, "M", None),
    ("ZLDWMC", "坐落单位名称", "String", 254, "M", None),
    ("TBMJ", "图斑面积", "Double", 15, "M", ">0"),
    ("KCDLBM", "扣除地类编码", "String", 5, "C", None),
    ("KCXS", "扣除地类系数", "Single", 6, "C", "[0,1)"),
    ("KCMJ", "扣除地类面积", "Double", 15, "C", ">=0"),
    ("TBDLMJ", "图斑地类面积", "Double", 15, "M", ">0"),
    ("GDLX", "耕地类型", "String", 2, "C", None),
    ("GDPDJB", "耕地坡度级别", "String", 2, "C", None),
    ("GXSJ", "更新时间", "Date", 8, "M", "YYYYMMDD"),
    ("XMMC", "项目名称/批次", "String", 254, "M", None),
    ("XMBH", "项目编号", "String", 100, "M", None),
    ("DKBH", "地块编号", "String", 254, "M", None),
    ("JZGDMJ", "净增耕地面积", "Double", 15, "M", ">0"),
    ("SFYKT", "是否有客土或物料填埋", "String", 3, "M", None),
    ("SFFK", "是否是未利用地、复垦", "String", 3, "M", None),
    ("SFZPJ", "是否再评价", "String", 3, "M", None),
    ("BCFS", "补充方式", "String", 3, "M", None),
    ("BCGDLY", "补充耕地来源", "String", 10, "M", None),
    ("BZ", "备注", "String", 254, "C", None),
)
APPENDIX_C_SOURCE = {
    "guide": "广西补充耕地质量验收作业指导书 v1.1（2026-08-17）",
    "pdf_page": 49,
    "printed_page": 43,
    "appendix": "附录C 补充耕地质量鉴定图斑属性结构表",
}
APPENDIX_C_GEOMETRY_SOURCE = {
    "guide": APPENDIX_C_SOURCE["guide"],
    "pdf_page": 50,
    "printed_page": 44,
    "clause": "附录C注6",
}
APPENDIX_C_CODE_FIELDS = (
    "BGQDLBM", "DLBM", "KCDLBM", "GDLX", "GDPDJB", "SFYKT",
    "SFFK", "SFZPJ", "BCFS", "BCGDLY",
)
APPENDIX_C_DECIMALS = {"TBMJ": 2, "KCXS": 4, "KCMJ": 2, "TBDLMJ": 2, "JZGDMJ": 2}
APPENDIX_C_VALUE_DOMAINS = {
    "SFYKT": {"是", "否"},
    "SFFK": {"是", "否"},
    "SFZPJ": {"是", "否"},
    "BCFS": {"Z", "G", "J", "K", "Q", "F"},
    "BCGDLY": {"恢复", "垦造"},
}


def _check_appendix_c(dataset):
    """Check the transferred identification parcel layer without editing it."""
    import math
    import arcpy

    layer_name = "补充耕地质量鉴定图斑"
    source = dict(APPENDIX_C_SOURCE)
    if not arcpy.Exists(dataset):
        return {
            "item": layer_name,
            "status": "FAIL",
            "detail": {"reason": "缺少附录C要求的移交图层", "source": source},
        }

    desc = arcpy.Describe(dataset)
    shape_type = str(getattr(desc, "shapeType", "") or "")
    fields = {str(field.name).upper(): field for field in arcpy.ListFields(dataset)}
    missing = [row[0] for row in APPENDIX_C if row[0] not in fields]
    wrong = []
    for name, _alias, expected_type, expected_length, _required, _domain in APPENDIX_C:
        field = fields.get(name)
        if field is None:
            continue
        actual_type = str(getattr(field, "type", "") or "")
        actual_length = getattr(field, "length", None)
        # ArcGIS geodatabase numeric length is a storage property; compare the
        # table's declared length only for Char/String fields.
        length_mismatch = expected_type == "String" and actual_length != expected_length
        if actual_type.lower() != expected_type.lower() or length_mismatch:
            wrong.append({
                "field": name,
                "expected": {"type": expected_type, "length": expected_length},
                "actual": {"type": actual_type, "length": actual_length},
            })

    if shape_type.lower() != "polygon":
        wrong.append({"geometry": {"expected": "Polygon", "actual": shape_type}})

    count = int(arcpy.management.GetCount(dataset)[0])
    missing_required = []
    invalid_values = []
    duplicate_ids = []
    coded_values_review = {name: set() for name in APPENDIX_C_CODE_FIELDS}
    multipart_count = 0
    multipart_ids = []
    hole_ring_parcels = []
    values_by_field = [
        row for row in APPENDIX_C
        if row[4] == "M" or row[5] or row[0] in APPENDIX_C_CODE_FIELDS
    ]
    cursor_fields = [row[0] for row in values_by_field] + ["SHAPE@"]
    seen_ids = set()
    if not missing:
        for values in arcpy.da.SearchCursor(dataset, cursor_fields):
            attrs = dict(zip(cursor_fields, values))
            for name, alias, _type, _length, required, domain in values_by_field:
                value = attrs.get(name)
                empty = value is None or (isinstance(value, str) and not value.strip())
                if required == "M" and empty:
                    missing_required.append({"field": name, "alias": alias})
                    continue
                if not empty and name in APPENDIX_C_VALUE_DOMAINS:
                    normalized = str(value).strip().upper() if name == "BCFS" else str(value).strip()
                    if normalized not in APPENDIX_C_VALUE_DOMAINS[name]:
                        invalid_values.append({
                            "field": name, "value": str(value),
                            "domain": sorted(APPENDIX_C_VALUE_DOMAINS[name]),
                        })
                    continue
                if empty or domain is None:
                    if not empty and name in coded_values_review:
                        coded_values_review[name].add(str(value))
                    continue
                if name == "GXSJ":
                    valid = (hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"))
                    if isinstance(value, str):
                        valid = len(value) == 8 and value.isdigit()
                        if valid:
                            try:
                                import datetime as dt
                                dt.datetime.strptime(value, "%Y%m%d")
                            except ValueError:
                                valid = False
                    if not valid:
                        invalid_values.append({"field": name, "value": str(value), "domain": domain})
                else:
                    try:
                        number = float(value)
                        valid_number = math.isfinite(number)
                    except (TypeError, ValueError, OverflowError):
                        number, valid_number = None, False
                    if name in {"TBMJ", "TBDLMJ", "JZGDMJ"}:
                        valid = valid_number and number > 0
                    elif name == "KCMJ":
                        valid = valid_number and number >= 0
                    else:  # KCXS: [0, 1)
                        valid = valid_number and 0 <= number < 1
                    if not valid:
                        invalid_values.append({"field": name, "value": str(value), "domain": domain})

            bsm = attrs.get("BSM")
            if bsm is not None and str(bsm).strip():
                key = str(bsm).strip()
                if key in seen_ids:
                    duplicate_ids.append(key)
                seen_ids.add(key)
            geometry = attrs.get("SHAPE@")
            if geometry is None:
                invalid_values.append({"field": "SHAPE@", "value": None, "domain": "图斑几何不能为空"})
            else:
                # Appendix C note 6 prohibits genuinely multipart parcels. ArcPy
                # reports isMultipart=True for a single-part polygon that merely
                # has interior rings (holes), so partCount is the reliable signal;
                # holes stay a manual-review item, not a FAIL.
                part_count = int(getattr(geometry, "partCount", 1) or 1)
                if part_count > 1:
                    multipart_count += 1
                    if bsm is not None and str(bsm).strip():
                        multipart_ids.append(str(bsm).strip())
                elif bool(getattr(geometry, "isMultipart", False)):
                    ring_count = None
                    try:
                        import json as _json
                        ring_count = len(_json.loads(geometry.JSON).get("rings") or [])
                    except Exception:
                        ring_count = None
                    hole_ring_parcels.append({
                        "DKBH": None if attrs.get("DKBH") is None or not str(attrs.get("DKBH")).strip()
                        else str(attrs.get("DKBH")).strip(),
                        "BSM": None if bsm is None or not str(bsm).strip() else str(bsm).strip(),
                        "partCount": part_count,
                        "ring_count": ring_count,
                        "hole_rings": None if ring_count is None else max(ring_count - part_count, 0),
                    })

    field_status = "FAIL" if missing or wrong else "PASS"
    coded_values_review = {
        name: sorted(values) for name, values in coded_values_review.items() if values
    }
    value_status = "FAIL" if missing_required or invalid_values or duplicate_ids else (
        "NEEDS_REVIEW" if coded_values_review else "PASS"
    )
    # Appendix C note 6 expressly prohibits multipart parcels. It gives no
    # numeric connector-width threshold, so slit connections stay a mandatory
    # visual/topology review instead of borrowing an unrelated cutoff.
    geometry_status = "FAIL" if multipart_count else "NEEDS_REVIEW"
    return {
        "item": layer_name,
        "status": "FAIL" if "FAIL" in (field_status, value_status, geometry_status) else "NEEDS_REVIEW",
        "detail": {
            "count": count,
            "source": source,
            "fields": {
                "status": field_status,
                "expected_count": len(APPENDIX_C),
                "expected_schema": [
                    {"field": name, "alias": alias, "type": field_type,
                     "source_length": length, "source_decimals": APPENDIX_C_DECIMALS.get(name),
                     "required": required, "value_domain": domain}
                    for name, alias, field_type, length, required, domain in APPENDIX_C
                ],
                "numeric_storage_note": "数值长度/小数位依附录C原表报告；FileGDB的ArcPy Field.length/precision/scale不直接按Shapefile十进制宽度判定。",
                "missing": missing,
                "mismatched": wrong,
            },
            "values": {
                "status": value_status,
                "empty_required": missing_required,
                "invalid_domain": invalid_values,
                "duplicate_BSM": sorted(set(duplicate_ids)),
                "duplicate_BSM_rule": "附录C注1要求标识码不唯一时依次增加“-1、-2……”后缀；发现重复值须核对源数据，按注1处理后重新质检。",
                "coded_values_needing_review": coded_values_review,
            },
            "geometry_review": {
                "status": geometry_status,
                "multipart_count": multipart_count,
                "multipart_BSM": sorted(multipart_ids),
                "source": dict(APPENDIX_C_GEOMETRY_SOURCE),
                "reason": "附录C注6明确禁止多部件图斑；存在多部件时本项不通过。",
                "multipart_rule": "多部件判据为 partCount>1；ArcPy 的 isMultipart 对含孔洞单部件图斑也为 True，不能直接作注6判据。",
                "slit_connector_review": {
                    "status": "NEEDS_REVIEW",
                    "action": "逐图斑检查是否利用细缝将多个图斑连接；发现此类图斑须拆分并复核边界后重新质检。",
                    "threshold_note": "规范未给出细缝宽度数值阈值，工具不自行设限；此项必须人工核查。",
                    "source": dict(APPENDIX_C_GEOMETRY_SOURCE),
                },
                "hole_ring_review": {
                    "status": "NEEDS_REVIEW",
                    "count": len(hole_ring_parcels),
                    "parcels": hole_ring_parcels,
                    "reason": "ArcPy 对含内环（孔洞）的单部件 Polygon 也返回 isMultipart=True；孔洞多为扣除地类，不属附录C注6禁止的多部件，须人工核对扣除地类与面积口径。",
                    "rule": "多部件按 partCount>1 判定；isMultipart=True 且 partCount=1 时记为孔洞信息，不判 FAIL。",
                },
            },
            "undocumented_coded_domains": list(APPENDIX_C_CODE_FIELDS),
            "checked_code_domains": {
                name: sorted(values) for name, values in APPENDIX_C_VALUE_DOMAINS.items()
            },
        },
    }


def _appendix_g_fields():
    """Load the Appendix G field contract from the existing delivery source."""
    import sys

    script_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    inserted = script_dir not in sys.path
    if inserted:
        sys.path.insert(0, script_dir)
    try:
        from delivery_package import APPENDIX_G
    finally:
        if inserted:
            sys.path.remove(script_dir)

    type_map = {
        "String": "String",
        "Double": "Double",
        "Single": "Single",
        "Integer": "Integer",
        "SmallInteger": "SmallInteger",
    }
    return {
        name: {"type": type_map[source_type], "length": length if source_type == "String" else None}
        for name, _alias, source_type, length in APPENDIX_G
    }


def _check_evaluation_unit_area(dataset):
    """Enforce the guide's 50-mu maximum for each evaluation unit."""
    import math
    import arcpy

    source_rule = dict(RULES["evaluation_unit"])
    max_area_m2 = float(source_rule["max_mu"]) * (10000.0 / 15.0)
    desc = arcpy.Describe(dataset)
    sr = getattr(desc, "spatialReference", None)
    if not sr or getattr(sr, "name", "Unknown") == "Unknown":
        return {
            "item": "评价单元面积（≤50亩）",
            "status": "UNVERIFIED",
            "detail": {"source_rule": source_rule, "reason": "坐标参考未知，无法可靠量算面积。"},
        }
    field_names = {field.name.upper() for field in arcpy.ListFields(dataset)}
    if "PJDYBH" not in field_names:
        return {
            "item": "评价单元面积（≤50亩）",
            "status": "UNVERIFIED",
            "detail": {"source_rule": source_rule, "reason": "缺少评价单元编号字段 PJDYBH。"},
        }
    try:
        count = int(arcpy.management.GetCount(dataset)[0])
        if count == 0:
            return {
                "item": "评价单元面积（≤50亩）",
                "status": "UNVERIFIED",
                "detail": {"source_rule": source_rule, "reason": "评价单元图层为空，尚无面积可核。"},
            }
        over_limit = []
        for unit_id, geometry in arcpy.da.SearchCursor(dataset, ["PJDYBH", "SHAPE@"]):
            if unit_id is None or not str(unit_id).strip() or geometry is None:
                return {
                    "item": "评价单元面积（≤50亩）",
                    "status": "FAIL",
                    "detail": {"source_rule": source_rule, "reason": "存在空评价单元编号或空几何。"},
                }
            area_m2 = float(geometry.getArea("GEODESIC", "SQUAREMETERS"))
            if not math.isfinite(area_m2) or area_m2 <= 0:
                return {
                    "item": "评价单元面积（≤50亩）",
                    "status": "FAIL",
                    "detail": {"source_rule": source_rule, "evaluation_unit_id": str(unit_id), "area_m2": area_m2},
                }
            if area_m2 > max_area_m2:
                over_limit.append({
                    "evaluation_unit_id": str(unit_id),
                    "area_m2": area_m2,
                    "area_mu": area_m2 / (10000.0 / 15.0),
                })
        return {
            "item": "评价单元面积（≤50亩）",
            "status": "NEEDS_REVIEW" if over_limit else "PASS",
            "detail": {
                "source_rule": source_rule,
                "measurement": "ArcPy Geometry.getArea('GEODESIC', 'SQUAREMETERS')",
                "max_area_m2": max_area_m2,
                "checked_count": count,
                "over_limit": over_limit,
                "review_note": "指导书6.1.3③为“原则上不超过50亩”；超限单元须说明理由并复核，不得直接当合格。" if over_limit else None,
            },
        }
    except Exception as exc:
        return {
            "item": "评价单元面积（≤50亩）",
            "status": "UNVERIFIED",
            "detail": {"source_rule": source_rule, "reason": "面积核算未完成：{}: {}".format(type(exc).__name__, exc)},
        }


def _overall_status(checks):
    statuses = {row["status"] for row in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "NEEDS_REVIEW" in statuses or "UNVERIFIED" in statuses:
        return "NEEDS_REVIEW"
    return "PASS"


def inspect_dataset(dataset):
    import arcpy

    desc = arcpy.Describe(dataset)
    sr = getattr(desc, "spatialReference", None)
    return {"path": str(dataset), "shape_type": getattr(desc, "shapeType", None), "count": int(arcpy.management.GetCount(dataset)[0]), "crs": getattr(sr, "name", "Unknown"), "wkid": int(getattr(sr, "factoryCode", 0) or 0)}


def check_project(gdb, expected_wkid=None):
    import arcpy

    checks = []
    expected = "耕地质量等级评价评价单元图"
    if not arcpy.Exists(gdb):
        raise ValueError("FileGDB 不存在：" + str(gdb))
    with arcpy.EnvManager(workspace=gdb):
        available = set(arcpy.ListFeatureClasses() or [])
    if expected not in available:
        checks.append({"item": expected, "status": "FAIL", "detail": "缺少附录G评价单元图层"})
    else:
        dataset = str(Path(gdb) / expected)
        info = inspect_dataset(dataset)
        checks.append({
            "item": expected,
            "status": "PASS" if info["shape_type"] == "Polygon" else "FAIL",
            "detail": {"expected_shape_type": "Polygon", "actual_shape_type": info["shape_type"], "count": info["count"]},
        })

        actual_fields = {field.name: field for field in arcpy.ListFields(dataset)}
        contract = _appendix_g_fields()
        missing = sorted(set(contract) - set(actual_fields))
        wrong = []
        for field_name, expected_field in contract.items():
            field = actual_fields.get(field_name)
            if field is None:
                continue
            actual_type = getattr(field, "type", None)
            actual_length = getattr(field, "length", None)
            if actual_type != expected_field["type"] or (
                expected_field["length"] is not None and actual_length != expected_field["length"]
            ):
                wrong.append({
                    "field": field_name,
                    "expected": expected_field,
                    "actual": {"type": actual_type, "length": actual_length},
                })
        checks.append({
            "item": "附录G字段结构",
            "status": "FAIL" if missing or wrong else "PASS",
            "detail": {"expected_count": len(contract), "missing": missing, "mismatched": wrong},
        })

        crs_status = "NEEDS_REVIEW"
        if expected_wkid and info["wkid"]:
            crs_status = "PASS" if info["wkid"] == int(expected_wkid) else "FAIL"
        checks.append({
            "item": expected + "坐标系",
            "status": crs_status,
            "detail": {"expected_wkid": int(expected_wkid) if expected_wkid else None, **info},
        })
        checks.append(_check_evaluation_unit_area(dataset))
    checks.append(_check_appendix_c(str(Path(gdb) / "补充耕地质量鉴定图斑")))
    checks.append({"item": "移交国土变更图斑", "status": "NEEDS_REVIEW", "detail": "补充耕地国土变更图斑应与日常变更/国土变更调查数据保持一致；本项不改动原层，需核对正式源库。"})
    checks.append({"item": "外业采样与举证材料", "status": "NEEDS_REVIEW", "detail": "实采情况、照片真实性、签字、实验室/CMA材料需由人员核实。"})
    return {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "scope": "数据库结构初检；不代表完整验收通过",
        "source_rules": RULES,
        "status": _overall_status(checks),
        "checks": checks,
        "summary": {s: sum(1 for row in checks if row["status"] == s) for s in ("PASS", "FAIL", "UNVERIFIED", "NEEDS_REVIEW")},
    }


def write_report(gdb, out_json, expected_wkid=None):
    report = check_project(gdb, expected_wkid)
    path = Path(out_json)
    if path.exists():
        raise FileExistsError("质检报告已存在，不覆盖：" + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path), report
