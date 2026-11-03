"""Conservative spatial QA for editable composite-sample design points."""
from __future__ import annotations

import math
import datetime as dt
import json
from pathlib import Path

from .remote_sensing import _crs_signature
from .rules import RULES
from .sampling import _projected_meters_per_unit, _reject_filtered_layer


def _key(unit_id, parcel_id):
    return str(unit_id), str(parcel_id)


def _resolve_fields(arcpy, dataset, requested):
    available = {str(field.name).casefold(): str(field.name) for field in arcpy.ListFields(dataset)}
    resolved = []
    for name in requested:
        field_name = available.get(str(name).casefold())
        if field_name is None:
            raise ValueError("输入图层缺少字段：{}".format(name))
        resolved.append(field_name)
    return resolved


def _write_json_new(path, payload):
    """Create a new JSON report and only remove the file if this call owns it."""
    created = False
    try:
        with Path(path).open("x", encoding="utf-8") as stream:
            created = True
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except Exception:
        if created and Path(path).exists():
            Path(path).unlink()
        raise


def _spacing_exception_review_map(reviews):
    """Validate human review records keyed by unit and the exact point pair."""
    result = {}
    for row in reviews or []:
        if not isinstance(row, dict):
            raise ValueError("间距例外审核记录必须是JSON对象列表。")
        required = ("unit_id", "point_id_a", "point_id_b", "parcel_id_a", "parcel_id_b")
        if any(row.get(key) in (None, "") for key in required):
            raise ValueError("间距例外审核记录缺少评价单元编号或点位对编号。")
        key = (
            str(row["unit_id"]),
            tuple(sorted(((str(row["point_id_a"]), str(row["parcel_id_a"])),
                          (str(row["point_id_b"]), str(row["parcel_id_b"]))))),
        )
        if key in result:
            raise ValueError("间距例外审核记录的评价单元/点位对重复。")
        result[key] = row
    return result


def check_composite_design_records(parcels, points, meters_per_unit, evaluation_units=None,
                                   spacing_exception_reviews=None):
    """Check point counts, parcel linkage and general 15 m spacing.

    Records are dictionaries with ``unit_id``, ``parcel_id``, ``point_id``
    and ArcPy-like ``geometry`` objects. A spacing shortfall is advisory
    because the guide describes 15 m as general and allows small-parcel
    exceptions. The overall result never claims field representativeness.
    """
    try:
        meters_per_unit = float(meters_per_unit)
    except (TypeError, ValueError):
        raise ValueError("投影线性单位换算无效。")
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise ValueError("投影线性单位换算必须为有限正数。")

    parcel_by_key = {}
    duplicate_parcels = []
    invalid_parcels = []
    parcel_input_count = 0
    for row in parcels:
        parcel_input_count += 1
        unit_id, parcel_id = row.get("unit_id"), row.get("parcel_id")
        if unit_id in (None, "") or parcel_id in (None, "") or row.get("geometry") is None:
            invalid_parcels.append({"unit_id": unit_id, "parcel_id": parcel_id})
            continue
        key = _key(unit_id, parcel_id)
        if key in parcel_by_key:
            duplicate_parcels.append({"unit_id": key[0], "parcel_id": key[1]})
        else:
            parcel_by_key[key] = row["geometry"]

    evaluation_unit_by_id, invalid_evaluation_units = {}, []
    for row in evaluation_units or []:
        unit_id, geometry = row.get("unit_id"), row.get("geometry")
        if unit_id in (None, "") or geometry is None:
            invalid_evaluation_units.append({"unit_id": unit_id})
            continue
        unit_id = str(unit_id)
        if unit_id in evaluation_unit_by_id:
            invalid_evaluation_units.append({"unit_id": unit_id, "reason": "编号重复"})
        else:
            evaluation_unit_by_id[unit_id] = geometry

    parcel_unit_mismatches = []
    if evaluation_units is not None:
        for (unit_id, parcel_id), geometry in parcel_by_key.items():
            unit_geometry = evaluation_unit_by_id.get(unit_id)
            if unit_geometry is None:
                parcel_unit_mismatches.append({"unit_id": unit_id, "parcel_id": parcel_id,
                                              "reason": "评价单元面缺失"})
                continue
            try:
                if not unit_geometry.contains(geometry):
                    parcel_unit_mismatches.append({"unit_id": unit_id, "parcel_id": parcel_id,
                                                  "reason": "图斑不完全位于声明的评价单元内"})
            except Exception:
                parcel_unit_mismatches.append({"unit_id": unit_id, "parcel_id": parcel_id,
                                              "reason": "无法计算评价单元包含关系"})

    duplicate_point_ids = []
    point_id_counts = {}
    for row in points:
        point_id = row.get("point_id")
        if point_id not in (None, ""):
            point_id_counts[str(point_id)] = point_id_counts.get(str(point_id), 0) + 1
    duplicate_point_id_set = {point_id for point_id, count in point_id_counts.items() if count > 1}
    invalid_points = []
    unlinked_points = []
    spatial_relation_failures = []
    unit_containment_failures = []
    sample_id_violations = []
    sample_ids_by_unit = {}
    point_counts_by_parcel = {}
    point_counts_by_unit = {}
    spatially_linked_count = 0
    fully_valid_count = 0
    valid_points_by_unit = {}
    point_input_count = 0
    for row in points:
        point_input_count += 1
        unit_id, parcel_id, point_id, sample_id, geometry = (
            row.get("unit_id"), row.get("parcel_id"), row.get("point_id"),
            row.get("sample_id"), row.get("geometry")
        )
        if (unit_id in (None, "") or parcel_id in (None, "") or point_id in (None, "")
                or sample_id in (None, "") or geometry is None):
            invalid_points.append({"unit_id": unit_id, "parcel_id": parcel_id,
                                   "point_id": point_id, "sample_id": sample_id})
            continue
        unit_key, parcel_key, point_key, sample_key = str(unit_id), _key(unit_id, parcel_id), str(point_id), str(sample_id)
        is_duplicate = point_key in duplicate_point_id_set
        if is_duplicate:
            duplicate_point_ids.append(point_key)
        if sample_key != unit_key:
            sample_id_violations.append({"point_id": point_key, "unit_id": unit_key,
                                         "actual": sample_key, "required": unit_key})
        sample_ids_by_unit.setdefault(unit_key, set()).add(sample_key)
        if parcel_key not in parcel_by_key:
            unlinked_points.append({"point_id": point_key, "unit_id": unit_key, "parcel_id": parcel_key[1]})
            continue
        try:
            inside = bool(parcel_by_key[parcel_key].contains(geometry))
        except Exception:
            inside = None
        if inside is False:
            spatial_relation_failures.append({"point_id": point_key, "unit_id": unit_key, "parcel_id": parcel_key[1]})
            continue
        elif inside is None:
            invalid_points.append({"unit_id": unit_key, "parcel_id": parcel_key[1], "point_id": point_key,
                                   "reason": "无法计算点与图斑空间关系"})
            continue
        else:
            if evaluation_units is not None:
                unit_geometry = evaluation_unit_by_id.get(unit_key)
                if unit_geometry is None:
                    unit_containment_failures.append({"point_id": point_key, "unit_id": unit_key,
                                                      "reason": "评价单元面缺失"})
                    continue
                try:
                    if not unit_geometry.contains(geometry):
                        unit_containment_failures.append({"point_id": point_key, "unit_id": unit_key,
                                                          "reason": "点不在声明的评价单元内"})
                        continue
                except Exception:
                    unit_containment_failures.append({"point_id": point_key, "unit_id": unit_key,
                                                      "reason": "无法计算评价单元包含关系"})
                    continue
            spatially_linked_count += 1
            if not is_duplicate and sample_key == unit_key:
                point_counts_by_unit[unit_key] = point_counts_by_unit.get(unit_key, 0) + 1
                point_counts_by_parcel[parcel_key] = point_counts_by_parcel.get(parcel_key, 0) + 1
                valid_points_by_unit.setdefault(unit_key, []).append((point_key, parcel_key[1], geometry))
                fully_valid_count += 1

    unit_ids = sorted(
        {str(row.get("unit_id")) for row in parcels if row.get("unit_id") not in (None, "")}
        | set(evaluation_unit_by_id)
    )
    unit_checks = []
    for unit_id in unit_ids:
        generated = point_counts_by_unit.get(unit_id, 0)
        unit_checks.append({
            "unit_id": unit_id,
            "required": int(RULES["composite_sampling_design"]["min_subsites"]),
            "generated": generated,
            "status": "PASS" if generated >= int(RULES["composite_sampling_design"]["min_subsites"]) else "FAIL",
        })

    parcel_checks = []
    for unit_id, parcel_id in sorted(parcel_by_key):
        generated = point_counts_by_parcel.get((unit_id, parcel_id), 0)
        parcel_checks.append({
            "unit_id": unit_id,
            "parcel_id": parcel_id,
            "required": int(RULES["composite_sampling_design"]["per_parcel"]),
            "generated": generated,
            "status": "PASS" if generated >= int(RULES["composite_sampling_design"]["per_parcel"]) else "FAIL",
        })

    min_spacing_m = float(RULES["composite_sample_collection"]["min_spacing_m"])
    spacing_violations = []
    unverified_spacing = []
    for unit_id, unit_points in sorted(valid_points_by_unit.items()):
        for index, (point_id_a, parcel_id_a, geometry_a) in enumerate(unit_points):
            for point_id_b, parcel_id_b, geometry_b in unit_points[index + 1:]:
                try:
                    distance_m = float(geometry_a.distanceTo(geometry_b)) * meters_per_unit
                    if not math.isfinite(distance_m) or distance_m < 0:
                        raise ValueError("距离无效")
                except Exception:
                    unverified_spacing.append({"unit_id": unit_id, "point_id_a": point_id_a,
                                              "point_id_b": point_id_b,
                                              "parcel_id_a": parcel_id_a, "parcel_id_b": parcel_id_b})
                    continue
                if distance_m < min_spacing_m - 1e-8:
                    spacing_violations.append({
                        "unit_id": unit_id,
                        "point_id_a": point_id_a,
                        "point_id_b": point_id_b,
                        "parcel_id_a": parcel_id_a,
                        "parcel_id_b": parcel_id_b,
                        "distance_m": round(distance_m, 4),
                        "required_m": min_spacing_m,
                    })

    review_map = _spacing_exception_review_map(spacing_exception_reviews)
    spacing_exception_review = []
    matched_review_keys = set()
    for violation in spacing_violations:
        pair = tuple(sorted(((violation["point_id_a"], violation["parcel_id_a"]),
                             (violation["point_id_b"], violation["parcel_id_b"]))))
        review_key = (violation["unit_id"], pair)
        review = review_map.get(review_key, {})
        if review:
            matched_review_keys.add(review_key)
        try:
            review_date = dt.date.fromisoformat(str(review.get("review_date", "")))
            valid_date = review_date.isoformat() == str(review.get("review_date"))
        except (TypeError, ValueError):
            valid_date = False
        complete = bool(
            str(review.get("small_parcel_evidence", "")).strip()
            and review.get("whole_parcel_coverage_confirmed") is True
            and review.get("edge_avoidance_confirmed") is True
            and str(review.get("reviewer", "")).strip()
            and valid_date
            and str(review.get("evidence_reference", "")).strip()
        )
        spacing_exception_review.append({
            **violation,
            "status": "DOCUMENTED_PENDING_JUDGMENT" if complete else "NEEDS_REVIEW",
            "small_parcel_evidence": review.get("small_parcel_evidence"),
            "whole_parcel_coverage_confirmed": review.get("whole_parcel_coverage_confirmed"),
            "edge_avoidance_confirmed": review.get("edge_avoidance_confirmed"),
            "reviewer": review.get("reviewer"),
            "review_date": review.get("review_date"),
            "evidence_reference": review.get("evidence_reference"),
            "note": "已提供完整记录仍需专业人员确认例外适用性；工具不自动批准间距例外。" if complete
                    else "须补充小图斑现场依据、全田覆盖与避边核查、审核人日期及证据索引。",
        })
    unmatched_spacing_exception_reviews = [
        {"unit_id": key[0], "point_id_a": key[1][0][0], "parcel_id_a": key[1][0][1],
         "point_id_b": key[1][1][0], "parcel_id_b": key[1][1][1]}
        for key in sorted(set(review_map) - matched_review_keys)
    ]

    empty_inputs = []
    if parcel_input_count == 0:
        empty_inputs.append("parcels")
    if point_input_count == 0:
        empty_inputs.append("design_points")
    hard_failures = bool(
        empty_inputs
        or
        duplicate_parcels or invalid_parcels or duplicate_point_ids or invalid_points
        or unlinked_points or spatial_relation_failures or sample_id_violations
        or unmatched_spacing_exception_reviews
        or any(len(values) > 1 for values in sample_ids_by_unit.values())
        or parcel_unit_mismatches or unit_containment_failures or invalid_evaluation_units
        or any(row["status"] == "FAIL" for row in unit_checks + parcel_checks)
    )
    return {
        "status": "FAIL" if hard_failures else "NEEDS_REVIEW",
        "source_rules": {
            "minimum_points_per_unit": RULES["composite_sampling_design"],
            "minimum_points_per_parcel": RULES["composite_sampling_design"],
            "general_spacing": RULES["composite_sample_collection"],
        },
        "unit_checks": unit_checks,
        "parcel_checks": parcel_checks,
        "spacing_status": "NEEDS_REVIEW" if spacing_violations or unverified_spacing else "PASS",
        "spacing_violations": spacing_violations,
        "spacing_exception_review": spacing_exception_review,
        "unmatched_spacing_exception_reviews": unmatched_spacing_exception_reviews,
        "unverified_spacing_pairs": unverified_spacing,
        "duplicate_parcels": sorted(duplicate_parcels, key=lambda row: (str(row["unit_id"]), str(row["parcel_id"]))),
        "invalid_parcels": invalid_parcels,
        "duplicate_point_ids": sorted(set(duplicate_point_ids)),
        "invalid_points": invalid_points,
        "unlinked_points": unlinked_points,
        "spatial_relation_failures": spatial_relation_failures,
        "sample_id_violations": sample_id_violations,
        "duplicate_sample_units": [unit for unit, values in sorted(sample_ids_by_unit.items()) if len(values) > 1],
        "parcel_unit_mismatches": parcel_unit_mismatches,
        "unit_containment_failures": unit_containment_failures,
        "invalid_evaluation_units": invalid_evaluation_units,
        "raw_point_count": point_input_count,
        "spatially_linked_point_count": spatially_linked_count,
        "valid_linked_point_count": fully_valid_count,
        "empty_inputs": empty_inputs,
        "manual_review_required": [
            "均匀网格与空间代表性（指南未规定可机器判定的偏差阈值）",
            "田边避让、田块障碍和现场可达性",
            "15米间距不足是否属于小图斑例外及其书面依据",
            "设计点是否为实际采样位置；本工具不核验实采事实",
            "投影尺度变形及项目控制资料适用性",
        ],
        "scope": "仅复核混合样设计点的结构和空间候选条件；不验证现场采样事实或质量验收结论。",
    }


def check_bulk_density_design_records(profiles, samples, meters_per_unit, evaluation_units=None):
    """Check bulk-density replicate counts and their profile-point geometry.

    Pair distances that differ from the 2 m design are reported for review;
    exceeding the guide's 2 m profile-point range is a hard failure.
    """
    try:
        meters_per_unit = float(meters_per_unit)
    except (TypeError, ValueError):
        raise ValueError("投影线性单位换算无效。")
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise ValueError("投影线性单位换算必须为有限正数。")

    required = int(RULES["bulk_density"]["cores_per_unit"])
    maximum_profile_distance = float(RULES["bulk_density"]["profile_buffer_m"])
    target_spacing = float(RULES["bulk_density"]["parallel_spacing_m"])
    spacing_numeric_tolerance_m = 1e-4
    sample_id_prefix = str(RULES["bulk_density"]["sample_id_prefix"])
    profile_by_unit = {}
    evaluation_unit_by_id = {}
    invalid_evaluation_units = []
    for row in evaluation_units or []:
        unit_id, geometry = row.get("unit_id"), row.get("geometry")
        if unit_id in (None, "") or geometry is None:
            invalid_evaluation_units.append({"unit_id": unit_id})
            continue
        unit_id = str(unit_id)
        if unit_id in evaluation_unit_by_id:
            invalid_evaluation_units.append({"unit_id": unit_id, "reason": "编号重复"})
        else:
            evaluation_unit_by_id[unit_id] = geometry
    unit_containment_failures = []
    duplicate_profiles = []
    invalid_profiles = []
    profile_input_count = 0
    for row in profiles:
        profile_input_count += 1
        unit_id, geometry = row.get("unit_id"), row.get("geometry")
        if unit_id in (None, "") or geometry is None:
            invalid_profiles.append({"unit_id": unit_id})
            continue
        unit_id = str(unit_id)
        if unit_id in profile_by_unit:
            duplicate_profiles.append(unit_id)
        else:
            profile_by_unit[unit_id] = geometry
        unit_geometry = evaluation_unit_by_id.get(unit_id)
        if evaluation_units is not None and unit_geometry is None:
            unit_containment_failures.append({"unit_id": unit_id, "feature": "profile", "reason": "评价单元面缺失"})
        elif unit_geometry is not None:
            try:
                if not unit_geometry.contains(geometry):
                    unit_containment_failures.append({"unit_id": unit_id, "feature": "profile", "reason": "调查点不在对应评价单元内"})
            except Exception:
                unit_containment_failures.append({"unit_id": unit_id, "feature": "profile", "reason": "评价单元包含关系无法核验"})

    samples_by_unit = {}
    invalid_samples = []
    unlinked_samples = []
    duplicate_replicates = []
    sample_id_violations = []
    seen_replicates = set()
    profile_distance_violations = []
    unverified_profile_distances = []
    sample_input_count = 0
    for row in samples:
        sample_input_count += 1
        unit_id = row.get("unit_id")
        sample_id = row.get("sample_id")
        replicate_no = row.get("replicate_no")
        geometry = row.get("geometry")
        if unit_id in (None, "") or sample_id in (None, "") or geometry is None:
            invalid_samples.append({"unit_id": unit_id, "sample_id": sample_id,
                                   "replicate_no": replicate_no})
            continue
        unit_id = str(unit_id)
        sample_id = str(sample_id)
        raw_replicate_no = replicate_no
        try:
            numeric_replicate_no = float(replicate_no)
            if isinstance(replicate_no, bool) or not math.isfinite(numeric_replicate_no) or not numeric_replicate_no.is_integer():
                raise ValueError("平行样序号不是整数")
            replicate_no = int(numeric_replicate_no)
        except (TypeError, ValueError, OverflowError):
            invalid_samples.append({"unit_id": unit_id, "sample_id": str(sample_id),
                                   "replicate_no": raw_replicate_no, "reason": "平行样序号不是整数"})
            continue
        if not 1 <= replicate_no <= required:
            invalid_samples.append({"unit_id": unit_id, "sample_id": str(sample_id),
                                   "replicate_no": replicate_no, "reason": "平行样序号超出规则范围"})
            continue
        replicate_key = (unit_id, replicate_no)
        if replicate_key in seen_replicates:
            duplicate_replicates.append({"unit_id": unit_id, "replicate_no": replicate_no})
        seen_replicates.add(replicate_key)
        samples_by_unit.setdefault(unit_id, []).append((replicate_no, sample_id, geometry))
        unit_geometry = evaluation_unit_by_id.get(unit_id)
        if evaluation_units is not None and unit_geometry is None:
            unit_containment_failures.append({"unit_id": unit_id, "feature": "design_point", "replicate_no": replicate_no, "reason": "评价单元面缺失"})
        elif unit_geometry is not None:
            try:
                if not unit_geometry.contains(geometry):
                    unit_containment_failures.append({"unit_id": unit_id, "feature": "design_point", "replicate_no": replicate_no, "reason": "候选点不在对应评价单元内"})
            except Exception:
                unit_containment_failures.append({"unit_id": unit_id, "feature": "design_point", "replicate_no": replicate_no, "reason": "评价单元包含关系无法核验"})
        expected_sample_id = sample_id_prefix + unit_id
        if sample_id != expected_sample_id:
            sample_id_violations.append({
                "unit_id": unit_id,
                "replicate_no": replicate_no,
                "actual": sample_id,
                "required": expected_sample_id,
            })
        profile = profile_by_unit.get(unit_id)
        if profile is None:
            unlinked_samples.append({"unit_id": unit_id, "sample_id": str(sample_id)})
            continue
        try:
            distance_m = float(profile.distanceTo(geometry)) * meters_per_unit
            if not math.isfinite(distance_m) or distance_m < 0:
                raise ValueError("距离无效")
        except Exception:
            unverified_profile_distances.append({"unit_id": unit_id, "sample_id": str(sample_id),
                                                 "replicate_no": replicate_no})
            continue
        if distance_m > maximum_profile_distance + 1e-8:
            profile_distance_violations.append({
                "unit_id": unit_id,
                "replicate_no": replicate_no,
                "distance_m": round(distance_m, 4),
                "maximum_m": maximum_profile_distance,
            })

    unit_ids = sorted(set(profile_by_unit) | set(samples_by_unit) | set(evaluation_unit_by_id))
    unit_checks = []
    for unit_id in unit_ids:
        generated = len(samples_by_unit.get(unit_id, []))
        unit_checks.append({
            "unit_id": unit_id,
            "required": required,
            "generated": generated,
            "status": "PASS" if generated == required else "FAIL",
        })

    spacing_deviations = []
    unverified_spacing = []
    for unit_id, unit_samples in sorted(samples_by_unit.items()):
        ordered = sorted(unit_samples, key=lambda row: row[0])
        for index, (rep_a, sample_a, geom_a) in enumerate(ordered):
            for rep_b, sample_b, geom_b in ordered[index + 1:]:
                try:
                    distance_m = float(geom_a.distanceTo(geom_b)) * meters_per_unit
                    if not math.isfinite(distance_m) or distance_m < 0:
                        raise ValueError("距离无效")
                except Exception:
                    unverified_spacing.append({"unit_id": unit_id, "replicate_no_a": rep_a,
                                               "replicate_no_b": rep_b})
                    continue
                if abs(distance_m - target_spacing) > spacing_numeric_tolerance_m:
                    spacing_deviations.append({
                        "unit_id": unit_id,
                        "replicate_no_a": rep_a,
                        "replicate_no_b": rep_b,
                        "sample_id": sample_a,
                        "sample_id_other": sample_b,
                        "distance_m": round(distance_m, 4),
                        "target_m": target_spacing,
                    })

    empty_inputs = []
    if profile_input_count == 0:
        empty_inputs.append("profiles")
    if sample_input_count == 0:
        empty_inputs.append("design_points")
    hard_failures = bool(
        empty_inputs
        or
        duplicate_profiles or invalid_profiles or invalid_samples or unlinked_samples
        or duplicate_replicates or profile_distance_violations or unverified_profile_distances
        or sample_id_violations
        or invalid_evaluation_units or unit_containment_failures
        or any(row["status"] == "FAIL" for row in unit_checks)
    )
    return {
        "status": "FAIL" if hard_failures else "NEEDS_REVIEW",
        "source_rules": RULES["bulk_density"],
        "spacing_numeric_tolerance_m": spacing_numeric_tolerance_m,
        "unit_checks": unit_checks,
        "spacing_status": "NEEDS_REVIEW" if spacing_deviations or unverified_spacing else "PASS",
        "spacing_deviations": spacing_deviations,
        "unverified_spacing_pairs": unverified_spacing,
        "profile_distance_violations": profile_distance_violations,
        "unit_containment_failures": unit_containment_failures,
        "invalid_evaluation_units": invalid_evaluation_units,
        "unverified_profile_distances": unverified_profile_distances,
        "duplicate_profiles": sorted(set(duplicate_profiles)),
        "invalid_profiles": invalid_profiles,
        "duplicate_replicates": sorted(duplicate_replicates,
                                        key=lambda row: (row["unit_id"], row["replicate_no"])),
        "sample_id_violations": sorted(sample_id_violations,
                                        key=lambda row: (row["unit_id"], row["replicate_no"])),
        "empty_inputs": empty_inputs,
        "invalid_samples": invalid_samples,
        "unlinked_samples": unlinked_samples,
        "manual_review_required": [
            "质地构型调查点和容重候选点是否位于适宜且同一田块位置",
            "平行点间距偏离2米时核查设计依据和现场可操作性",
            "土层、石砾/侵入体、环刀取样条件和实际采样位置",
            "候选点为设计位置，不证明已完成实地采样或样品流转",
            "坐标投影尺度变形及项目控制资料适用性",
        ],
        "scope": "仅检查容重设计点数量、编号结构和相对质地构型调查点的空间距离；不核验现场取样事实。",
    }


def check_bulk_density_design(profile_fc, unit_fc, point_fc, profile_unit_field, unit_field,
                              point_unit_field, sample_id_field, replicate_field,
                              point_status_field, out_json, arcpy_module=None):
    """Read ArcPy layers and write a conservative bulk-density QA report."""
    if arcpy_module is None:
        import arcpy as arcpy_module
    arcpy = arcpy_module
    if not arcpy.Exists(profile_fc):
        raise ValueError("质地构型调查点不存在：{}".format(profile_fc))
    if not arcpy.Exists(unit_fc):
        raise ValueError("评价单元面不存在：{}".format(unit_fc))
    if not arcpy.Exists(point_fc):
        raise ValueError("容重设计点不存在：{}".format(point_fc))
    profile_desc = arcpy.Describe(profile_fc)
    unit_desc = arcpy.Describe(unit_fc)
    point_desc = arcpy.Describe(point_fc)
    _reject_filtered_layer(profile_desc, "质地构型调查点", arcpy.da.Describe(profile_fc).get("FIDSet"))
    _reject_filtered_layer(unit_desc, "评价单元面", arcpy.da.Describe(unit_fc).get("FIDSet"))
    _reject_filtered_layer(point_desc, "容重设计点", arcpy.da.Describe(point_fc).get("FIDSet"))
    if str(getattr(profile_desc, "shapeType", "")).casefold() != "point":
        raise ValueError("质地构型调查点输入必须为点要素类。")
    if str(getattr(unit_desc, "shapeType", "")).casefold() != "polygon":
        raise ValueError("评价单元输入必须为Polygon面要素类。")
    if str(getattr(point_desc, "shapeType", "")).casefold() != "point":
        raise ValueError("容重设计点输入必须为点要素类。")
    profile_sr = getattr(profile_desc, "spatialReference", None)
    unit_sr = getattr(unit_desc, "spatialReference", None)
    point_sr = getattr(point_desc, "spatialReference", None)
    profile_signature = _crs_signature(profile_sr)
    unit_signature = _crs_signature(unit_sr)
    point_signature = _crs_signature(point_sr)
    if profile_signature is None or unit_signature is None or point_signature is None:
        raise ValueError("调查点、评价单元面或容重设计点坐标系未知；已停止空间质检。")
    if profile_signature != unit_signature or profile_signature != point_signature:
        raise ValueError("调查点、评价单元面与容重设计点坐标系不一致；须按控制资料明确转换后再质检。")
    meters_per_unit = _projected_meters_per_unit(profile_sr)

    profile_unit_id_field, = _resolve_fields(arcpy, profile_fc, [profile_unit_field])
    evaluation_unit_id_field, = _resolve_fields(arcpy, unit_fc, [unit_field])
    point_unit, sample_id, replicate_no, point_status = _resolve_fields(
        arcpy,
        point_fc, [point_unit_field, sample_id_field, replicate_field, point_status_field]
    )
    profile_records = []
    with arcpy.da.SearchCursor(profile_fc, [profile_unit_id_field, "SHAPE@"] ) as cursor:
        for profile_unit_value, geometry in cursor:
            profile_records.append({"unit_id": profile_unit_value, "geometry": geometry})
    unit_records = []
    with arcpy.da.SearchCursor(unit_fc, [evaluation_unit_id_field, "SHAPE@"]) as cursor:
        for unit_value, geometry in cursor:
            unit_records.append({"unit_id": unit_value, "geometry": geometry})
    sample_records = []
    with arcpy.da.SearchCursor(point_fc, [point_unit, sample_id, replicate_no, point_status, "SHAPE@"] ) as cursor:
        for unit_id, sample_id_value, replicate_value, status, geometry in cursor:
            if str(status or "").strip().upper() != "DESIGN_ONLY":
                raise ValueError("容重设计质检只接受 DESIGN_ONLY 候选点；不得混入实采点。")
            sample_records.append({"unit_id": unit_id, "sample_id": sample_id_value,
                                   "replicate_no": replicate_value, "geometry": geometry})

    result = check_bulk_density_design_records(
        profile_records, sample_records, meters_per_unit, evaluation_units=unit_records,
    )
    report = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": result["status"],
        "source_profiles": str(profile_fc),
        "source_evaluation_units": str(unit_fc),
        "source_design_points": str(point_fc),
        "fields": {
            "profile_unit_id": profile_unit_id_field,
            "evaluation_unit_id": evaluation_unit_id_field,
            "point_unit_id": point_unit,
            "sample_id": sample_id,
            "replicate_no": replicate_no,
            "point_status": point_status,
        },
        "crs": {
            "name": str(getattr(profile_sr, "name", "Unknown")),
            "wkid": int(getattr(profile_sr, "factoryCode", 0) or 0),
            "signature": list(profile_signature),
            "meters_per_unit": meters_per_unit,
            "distance_method": "ArcPy planar geometry distance × projected metersPerUnit; projection scale distortion requires project-specific review",
        },
        **result,
    }
    path = Path(out_json).expanduser().resolve()
    if not path.parent.is_dir():
        raise ValueError("质检报告输出目录必须已存在：{}".format(path.parent))
    _write_json_new(path, report)
    report["output_json"] = str(path)
    return report


def check_composite_design(parcel_fc, unit_fc, point_fc, parcel_unit_field, parcel_id_field,
                           unit_id_field, point_unit_field, point_parcel_field, point_id_field,
                           sample_id_field, point_status_field, out_json, arcpy_module=None,
                           spacing_exception_reviews_json=None):
    """Read ArcPy layers, verify CRS/design semantics, and write a JSON report."""
    if arcpy_module is None:
        import arcpy as arcpy_module
    arcpy = arcpy_module
    if not arcpy.Exists(parcel_fc):
        raise ValueError("图斑面不存在：{}".format(parcel_fc))
    if not arcpy.Exists(point_fc):
        raise ValueError("混合样设计点不存在：{}".format(point_fc))
    if not arcpy.Exists(unit_fc):
        raise ValueError("评价单元面不存在：{}".format(unit_fc))
    parcel_desc = arcpy.Describe(parcel_fc)
    unit_desc = arcpy.Describe(unit_fc)
    point_desc = arcpy.Describe(point_fc)
    _reject_filtered_layer(parcel_desc, "图斑输入", arcpy.da.Describe(parcel_fc).get("FIDSet"))
    _reject_filtered_layer(unit_desc, "评价单元面输入", arcpy.da.Describe(unit_fc).get("FIDSet"))
    _reject_filtered_layer(point_desc, "混合样设计点输入", arcpy.da.Describe(point_fc).get("FIDSet"))
    if str(getattr(parcel_desc, "shapeType", "")).casefold() != "polygon":
        raise ValueError("图斑输入必须为面要素类。")
    if str(getattr(point_desc, "shapeType", "")).casefold() != "point":
        raise ValueError("设计点输入必须为点要素类。")
    if str(getattr(unit_desc, "shapeType", "")).casefold() != "polygon":
        raise ValueError("评价单元输入必须为面要素类。")
    parcel_sr = getattr(parcel_desc, "spatialReference", None)
    point_sr = getattr(point_desc, "spatialReference", None)
    parcel_signature = _crs_signature(parcel_sr)
    point_signature = _crs_signature(point_sr)
    unit_signature = _crs_signature(getattr(unit_desc, "spatialReference", None))
    if parcel_signature is None or point_signature is None:
        raise ValueError("图斑面或设计点坐标系未知；已停止空间质检。")
    if parcel_signature != point_signature or parcel_signature != unit_signature:
        raise ValueError("图斑面、评价单元面与设计点坐标系必须一致；须依据控制资料完成明确转换后再质检。")
    meters_per_unit = _projected_meters_per_unit(parcel_sr)

    parcel_unit, parcel_id = _resolve_fields(arcpy, parcel_fc, [parcel_unit_field, parcel_id_field])
    evaluation_unit_id_field = _resolve_fields(arcpy, unit_fc, [unit_id_field])[0]
    point_unit, point_parcel, point_id, sample_id, point_status = _resolve_fields(
        arcpy,
        point_fc, [point_unit_field, point_parcel_field, point_id_field, sample_id_field, point_status_field]
    )
    parcel_records = []
    with arcpy.da.SearchCursor(parcel_fc, [parcel_unit, parcel_id, "SHAPE@"] ) as cursor:
        for unit_id, parcel_id_value, geometry in cursor:
            parcel_records.append({"unit_id": unit_id, "parcel_id": parcel_id_value, "geometry": geometry})
    unit_records = []
    with arcpy.da.SearchCursor(unit_fc, [evaluation_unit_id_field, "SHAPE@"] ) as cursor:
        for unit_id_value, geometry in cursor:
            unit_records.append({"unit_id": unit_id_value, "geometry": geometry})
    point_records = []
    with arcpy.da.SearchCursor(point_fc, [point_unit, point_parcel, point_id, sample_id, point_status, "SHAPE@"] ) as cursor:
        for unit_id, parcel_id_value, point_id_value, sample_id_value, status, geometry in cursor:
            if str(status or "").strip().upper() != "DESIGN_ONLY":
                raise ValueError("设计质检只接受 DESIGN_ONLY 候选点；不得把实采点混入设计点检查。")
            point_records.append({"unit_id": unit_id, "parcel_id": parcel_id_value,
                                  "point_id": point_id_value, "sample_id": sample_id_value, "geometry": geometry})

    spacing_reviews = None
    if spacing_exception_reviews_json:
        review_path = Path(spacing_exception_reviews_json).expanduser().resolve()
        if not review_path.is_file():
            raise ValueError("间距例外审核JSON文件不存在：{}".format(review_path))
        try:
            with review_path.open("r", encoding="utf-8-sig") as stream:
                spacing_reviews = json.load(stream)
        except (OSError, ValueError) as exc:
            raise ValueError("无法读取间距例外审核JSON：{}".format(exc))
        if not isinstance(spacing_reviews, list):
            raise ValueError("间距例外审核JSON顶层必须是记录列表。")
    result = check_composite_design_records(
        parcel_records, point_records, meters_per_unit, unit_records,
        spacing_exception_reviews=spacing_reviews,
    )
    report = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": result["status"],
        "source_parcels": str(parcel_fc),
        "source_evaluation_units": str(unit_fc),
        "source_design_points": str(point_fc),
        "source_spacing_exception_reviews": str(spacing_exception_reviews_json or ""),
        "fields": {
            "parcel_unit_id": parcel_unit,
            "parcel_id": parcel_id,
            "evaluation_unit_id": evaluation_unit_id_field,
            "point_unit_id": point_unit,
            "point_parcel_id": point_parcel,
            "point_id": point_id,
            "sample_id": sample_id,
            "point_status": point_status,
        },
        "crs": {
            "name": str(getattr(parcel_sr, "name", "Unknown")),
            "wkid": int(getattr(parcel_sr, "factoryCode", 0) or 0),
            "signature": list(parcel_signature),
            "meters_per_unit": meters_per_unit,
            "distance_method": "ArcPy planar geometry distance × projected metersPerUnit; projection scale distortion requires project-specific review",
        },
        **result,
    }
    path = Path(out_json).expanduser().resolve()
    if not path.parent.is_dir():
        raise ValueError("质检报告输出目录必须已存在：{}".format(path.parent))
    _write_json_new(path, report)
    report["output_json"] = str(path)
    return report
