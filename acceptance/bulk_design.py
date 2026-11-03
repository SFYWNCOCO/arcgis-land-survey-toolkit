"""质地构型调查与容重的几何候选，不填造现场土层或检测值。"""
from __future__ import annotations

import os

from . import sampling as S
from . import sampling_qa as QA


def prepare_bulk_candidates(units, unit_field, centers, out_gdb, parcel_layer=None,
                            parcel_unit_field=None):
    """质地构型调查点候选。

    细则 8.1.3.5：在评价单元内选面积最大的图斑，在 7.2 布设点位中选最靠近该图斑中心的点。
    parcel_layer 缺省时退回“离边界最远”的旧口径，并在 REVIEW 里标明口径未落实。
    """
    import arcpy
    sr = arcpy.Describe(units).spatialReference
    largest = _largest_parcels(arcpy, parcel_layer, parcel_unit_field or unit_field, sr) if parcel_layer else {}
    profiles = S._create_output(out_gdb, "质地构型调查_候选", sr, [
        ("PJDYBH", "TEXT", 30), ("DESIGN_ST", "TEXT", 20), ("REVIEW", "TEXT", 120)])
    candidates = {}
    with arcpy.da.SearchCursor(centers, ["PJDYBH", "IN_UNIT", "SHAPE@"]) as cursor:
        for uid, inside, point in cursor:
            if inside:
                candidates.setdefault(str(uid), []).append(point)
    count = 0
    with arcpy.da.InsertCursor(profiles, ["SHAPE@", "PJDYBH", "DESIGN_ST", "REVIEW"]) as insert:
        with arcpy.da.SearchCursor(units, [unit_field, "SHAPE@"]) as cursor:
            for uid, geometry in cursor:
                label = arcpy.PointGeometry(geometry.labelPoint, sr)
                unit_points = [point for point in candidates.get(str(uid), []) if geometry.contains(point)]
                target = largest.get(str(uid))
                if target is not None:
                    # 8.1.3.5：最大图斑内、离该图斑中心最近的 7.2 点位
                    options = [point for point in unit_points if target.contains(point)] or [label]
                    centre = arcpy.PointGeometry(target.centroid, sr)
                    point = min(options, key=lambda option: option.distanceTo(centre))
                    review = "质地构型调查候选（8.1.3.5：最大图斑内离图斑中心最近）；现场确认土层与代表性"
                else:
                    options = unit_points + [label]
                    # 缺图斑层时退回旧口径：离边界较远的位置；8.1.3.5 口径未落实
                    point = max(options, key=lambda option: geometry.boundary().distanceTo(option))
                    review = "质地构型调查候选；缺图斑层，未按8.1.3.5选点，须人工确认"
                insert.insertRow([point, str(uid), "DESIGN_ONLY", review])
                count += 1
    result = {"profiles": profiles, "profile_count": count, "output": None, "count": 0,
              "status": "NEEDS_REVIEW", "qa": {"status": "NEEDS_REVIEW"}}
    try:
        bulk = S.bulk_density_candidates(profiles, "PJDYBH", out_gdb,
            evaluation_units=units, evaluation_unit_field=unit_field)
    except ValueError as exc:
        if "所有质地构型调查点周边均无法" not in str(exc):
            raise
        result["detail"] = str(exc)
        return result
    qa_path = os.path.join(os.path.dirname(out_gdb), "容重独立质检.json")
    qa = QA.check_bulk_density_design(profiles, units, bulk["output"], "PJDYBH", unit_field,
        "PJDYBH", "SAMPLE_ID", "REP_NO", "DESIGN_ST", qa_path)
    result.update(bulk)
    result.update({"qa": qa, "qa_path": qa_path})
    return result


def _largest_parcels(arcpy, parcel_layer, unit_field, sr):
    """每个评价单元内面积最大的图斑几何（8.1.3.5 选点依据）。"""
    best = {}
    with arcpy.da.SearchCursor(parcel_layer, [unit_field, "SHAPE@"]) as cursor:
        for uid, geometry in cursor:
            if geometry is None:
                continue
            key = str(uid)
            if key not in best or geometry.area > best[key].area:
                best[key] = geometry
    return best
