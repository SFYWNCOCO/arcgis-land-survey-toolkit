"""Editable sampling candidates; generated locations are never field observations."""
from __future__ import annotations

import math
import os


def _projected_meters_per_unit(sr):
    """Return the linear unit conversion only for a defined projected CRS."""
    if not sr or getattr(sr, "name", "Unknown") == "Unknown":
        raise ValueError("采样输入必须具有已确认的投影坐标系")
    if str(getattr(sr, "type", "")).casefold() != "projected":
        raise ValueError("采样距离以米计；输入必须使用投影坐标系，不能直接用经纬度计算")
    unit = str(getattr(sr, "linearUnitName", "") or "").strip()
    try:
        meters_per_unit = float(getattr(sr, "metersPerUnit", 0) or 0)
    except (TypeError, ValueError):
        meters_per_unit = 0
    if not unit or not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise ValueError("无法读取有效的投影线性单位换算；请指定已确认的投影坐标系")
    return meters_per_unit


def _reject_filtered_layer(description, label, selection_state=None):
    """Refuse selections and definition queries that hide source features."""
    where_clause = str(getattr(description, "whereClause", "") or "").strip()
    if where_clause:
        raise ValueError("{}存在定义查询；请移除查询后运行完整设计。".format(label))
    if selection_state is not None:
        raise ValueError("{}存在选择集；请清除选择后运行完整设计。".format(label))
    selected = getattr(description, "FIDSet", None)
    if isinstance(selected, (list, tuple, set)):
        has_selection = bool(selected)
    else:
        has_selection = bool(str(selected or "").strip())
    if has_selection:
        raise ValueError("{}存在选择集；请清除选择后运行完整设计。".format(label))


def _create_output(gdb, name, sr, fields, mark_created=None, shape_type="POINT"):
    import arcpy
    out = os.path.join(gdb, name)
    if arcpy.Exists(out):
        raise FileExistsError("设计点输出已存在，不覆盖：" + out)
    arcpy.management.CreateFeatureclass(gdb, name, shape_type, spatial_reference=sr)
    if mark_created:
        mark_created()
    for field, kind, length in fields:
        arcpy.management.AddField(out, field, kind, field_length=length or None)
    return out


def _with_output_rollback(arcpy, output_path, operation):
    """Delete only an output this operation created if a later ArcPy step fails."""
    created = False

    def mark_created():
        nonlocal created
        created = True

    try:
        return operation(mark_created)
    except Exception as exc:
        if created and arcpy.Exists(output_path):
            try:
                arcpy.management.Delete(output_path)
            except Exception as cleanup_exc:
                raise RuntimeError("采样设计失败：{}；部分输出回滚失败：{}: {}".format(
                    exc, type(cleanup_exc).__name__, cleanup_exc
                )) from exc
        raise


def composite_candidates(parcel_fc, unit_field, parcel_field, out_gdb, out_name="混合样分样点_设计",
                         evaluation_units=None, evaluation_unit_field=None):
    """Create editable candidates and report every unmet spatial design constraint."""
    import arcpy
    desc = arcpy.Describe(parcel_fc)
    _reject_filtered_layer(desc, "图斑输入", arcpy.da.Describe(parcel_fc).get("FIDSet"))
    sr = desc.spatialReference
    if getattr(desc, "shapeType", None) != "Polygon":
        raise ValueError("混合样采样输入必须是面要素")
    meters_per_unit = _projected_meters_per_unit(sr)
    if not evaluation_units or not evaluation_unit_field:
        raise ValueError("必须提供评价单元面及其编号字段；混合样点不能只按图斑属性推定单元归属")
    if not arcpy.Exists(evaluation_units):
        raise ValueError("评价单元面图层不存在：" + str(evaluation_units))
    unit_desc = arcpy.Describe(evaluation_units)
    _reject_filtered_layer(unit_desc, "评价单元面输入", arcpy.da.Describe(evaluation_units).get("FIDSet"))
    if str(getattr(unit_desc, "shapeType", "")).casefold() != "polygon":
        raise ValueError("评价单元输入必须为 Polygon 面要素")
    if _spatial_reference_key(sr) != _spatial_reference_key(getattr(unit_desc, "spatialReference", None)):
        raise ValueError("图斑与评价单元面坐标系不一致；须按控制资料明确转换")
    unit_geometries = {}
    with arcpy.da.SearchCursor(evaluation_units, [evaluation_unit_field, "SHAPE@"]) as cursor:
        for unit_id, geometry in cursor:
            if unit_id in (None, "") or geometry is None or float(getattr(geometry, "area", 0) or 0) <= 0:
                raise ValueError("评价单元编号为空或面几何无效；请先修复评价单元数据")
            key = str(unit_id)
            if key in unit_geometries:
                raise ValueError("评价单元编号重复：{}；请先按评价单元合并为唯一面".format(key))
            unit_geometries[key] = geometry
    if not unit_geometries:
        raise ValueError("评价单元面图层为空")
    rows = []
    seen_pairs = set()
    with arcpy.da.SearchCursor(parcel_fc, [unit_field, parcel_field, "SHAPE@"]) as cur:
        for unit_id, parcel_id, geom in cur:
            if unit_id in (None, "") or parcel_id in (None, ""):
                raise ValueError("评价单元编号/图斑编号存在空值；请先修复源数据")
            if not geom or geom.area <= 0:
                raise ValueError("输入含空几何或零面积图斑；请先修复源数据")
            unit_geometry = unit_geometries.get(str(unit_id))
            if unit_geometry is None:
                raise ValueError("图斑评价单元编号 {} 在评价单元面图层中不存在".format(unit_id))
            if not unit_geometry.contains(geom):
                raise ValueError("图斑 {} 不完全位于其声明的评价单元 {} 内；须先复核边界/归属".format(parcel_id, unit_id))
            key = (str(unit_id), str(parcel_id))
            if key in seen_pairs:
                raise ValueError("同一评价单元内图斑编号重复：{} / {}".format(*key))
            seen_pairs.add(key)
            rows.append((str(unit_id), str(parcel_id), geom, float(geom.area)))
    if not rows:
        raise ValueError("未找到带评价单元编号和地块编号的有效面")
    by_unit = {}
    for row in rows:
        by_unit.setdefault(row[0], []).append(row)
    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在：" + str(out_gdb))
    output_path = os.path.join(out_gdb, out_name)
    if arcpy.Exists(output_path):
        raise FileExistsError("设计点输出已存在，不覆盖：" + output_path)

    records = []
    unit_checks = []
    total = 0
    for unit_id, parcels in sorted(by_unit.items()):
        target = max(3, len(parcels))
        counts = [1] * len(parcels)
        ranked = sorted(range(len(parcels)), key=lambda i: parcels[i][3], reverse=True)
        for extra in range(target - len(parcels)):
            counts[ranked[extra % len(ranked)]] += 1
        selected = []
        parcel_checks = []
        unit_start = total
        for i, parcel in enumerate(parcels):
            _, parcel_id, geom, area = parcel
            target_count = counts[i]
            min_spacing = 15.0 / meters_per_unit
            step = max(min_spacing, math.sqrt(area) / 20.0)
            ext = geom.extent
            candidates = []
            # First honor the ordinary 15 m grid. If that grid cannot meet
            # the minimum point count, refine it in bounded steps so small
            # parcels can still get coverage candidates; the spacing exception
            # remains explicit for human review.
            grid_steps = [step, step / 2, step / 4, step / 8]
            for grid_step in grid_steps:
                y = ext.YMin + grid_step / 2
                while y < ext.YMax:
                    x = ext.XMin + grid_step / 2
                    while x < ext.XMax:
                        pt = arcpy.PointGeometry(arcpy.Point(x, y), sr)
                        if geom.contains(pt):
                            candidates.append(pt)
                        x += grid_step
                    y += grid_step
                # The finer grids are needed only when the coarser candidate
                # pool is too small; skip them after securing enough options.
                if len(candidates) >= target_count:
                    break
            label = arcpy.PointGeometry(geom.labelPoint, sr)
            pool = []
            for point in sorted(candidates, key=lambda p: (p.centroid.X, p.centroid.Y)):
                if not any(point.distanceTo(other) < 1e-8 for other in pool):
                    pool.append(point)
            parcel_selected = []
            while pool and len(parcel_selected) < target_count:
                prior = selected + parcel_selected
                ranked_pool = sorted(
                    pool,
                    key=lambda point: min([point.distanceTo(q) for q in prior] or [float("inf")]),
                    reverse=True,
                )
                choice = next(
                    (point for point in ranked_pool if not prior or min(point.distanceTo(q) for q in prior) >= min_spacing),
                    ranked_pool[0],
                )
                if any(choice.distanceTo(other) < 1e-8 for other in prior):
                    pool.remove(choice)
                    continue
                parcel_selected.append(choice)
                pool.remove(choice)
            # Use the label point as one safe candidate when the grid missed a narrow parcel.
            if not parcel_selected:
                parcel_selected.append(label)
            spacing_violation = any(
                a.distanceTo(b) < min_spacing - 1e-8
                for index, a in enumerate(selected + parcel_selected)
                for b in (selected + parcel_selected)[index + 1:]
            )
            shortage = len(parcel_selected) < target_count
            parcel_checks.append({
                "parcel_id": parcel_id,
                "required": target_count,
                "generated": len(parcel_selected),
                "minimum_spacing_met": not spacing_violation,
                "status": "NEEDS_REVIEW",
                "detail": "点位代表性、田边避让、均匀性和现场可达性均须人工确认",
            })
            for j, point in enumerate(parcel_selected, 1):
                review = "设计点；人工复核均匀性、田边避让与可达性"
                if shortage:
                    review += "；未达到该图斑分配点数，须调整/补点"
                if spacing_violation:
                    review += "；15米间距未满足，核实小图斑处理依据"
                point_id = "{}-{}-{:02d}".format(unit_id, parcel_id, j)
                # Appendix E defines the mixed-soil sample number as the
                # evaluation-unit ID; subsite geometry gets its own point ID.
                records.append([point, unit_id, parcel_id, unit_id, point_id, "DESIGN_ONLY", "6.1.4;6.2.4.3(1)", 0, 20, review])
                total += 1
            selected.extend(parcel_selected)
        generated = total - unit_start
        unit_checks.append({
            "unit_id": unit_id,
            "required": target,
            "generated": generated,
            "parcel_checks": parcel_checks,
            "status": "NEEDS_REVIEW",
        })

    output_path = os.path.join(out_gdb, out_name)

    def write(mark_created):
        output = _create_output(out_gdb, out_name, sr, [("PJDYBH", "TEXT", 30), ("DKBH", "TEXT", 30), ("SAMPLE_ID", "TEXT", 30), ("POINT_ID", "TEXT", 80), ("DESIGN_ST", "TEXT", 20), ("SRC_RULE", "TEXT", 40), ("DEPTH_TOP", "SHORT", None), ("DEPTH_BOT", "SHORT", None), ("REVIEW", "TEXT", 100)], mark_created)
        with arcpy.da.InsertCursor(output, ["SHAPE@", "PJDYBH", "DKBH", "SAMPLE_ID", "POINT_ID", "DESIGN_ST", "SRC_RULE", "DEPTH_TOP", "DEPTH_BOT", "REVIEW"]) as insert:
            for row in records:
                insert.insertRow(row)
        return {
            "output": output,
            "count": total,
            "units": len(by_unit),
            "status": "NEEDS_REVIEW",
            "unit_checks": unit_checks,
            "rule": "自动分配仅为每图斑至少1点、每单元至少3点的最低候选；实际数量按图斑面积、地形、补充耕地来源及现场条件由专业人员复核；一般间距不小于15m；所有设计点均须人工复核",
        }

    return _with_output_rollback(arcpy, output_path, write)


def _spatial_reference_key(sr):
    code = int(getattr(sr, "factoryCode", 0) or 0) if sr else 0
    if code > 0:
        return ("wkid", code)
    try:
        wkt = sr.exportToString() if sr else ""
    except Exception:
        wkt = ""
    return ("wkt", wkt) if wkt else None


def bulk_density_candidates(profile_points, unit_field, out_gdb, out_name="容重平行样_设计",
                            evaluation_units=None, evaluation_unit_field=None):
    """Create valid three-point triangles only when all candidates fit their unit."""
    import arcpy
    desc = arcpy.Describe(profile_points)
    _reject_filtered_layer(desc, "质地构型调查点", arcpy.da.Describe(profile_points).get("FIDSet"))
    sr = desc.spatialReference
    if getattr(desc, "shapeType", None) != "Point":
        raise ValueError("质地构型调查点输入必须是点要素")
    meters_per_unit = _projected_meters_per_unit(sr)
    if not evaluation_units or not evaluation_unit_field:
        raise ValueError("必须提供评价单元面图层及其评价单元编号字段；容重点不得生成到所属单元之外")
    if not arcpy.Exists(evaluation_units):
        raise ValueError("评价单元面图层不存在：" + str(evaluation_units))
    unit_desc = arcpy.Describe(evaluation_units)
    _reject_filtered_layer(unit_desc, "评价单元面输入", arcpy.da.Describe(evaluation_units).get("FIDSet"))
    if str(getattr(unit_desc, "shapeType", "")).casefold() != "polygon":
        raise ValueError("评价单元输入必须为 Polygon 面要素")
    unit_sr = getattr(unit_desc, "spatialReference", None)
    if _spatial_reference_key(sr) != _spatial_reference_key(unit_sr):
        raise ValueError("质地构型调查点与评价单元面坐标系不一致；须按控制资料明确转换后再设计")

    unit_geometries = {}
    with arcpy.da.SearchCursor(evaluation_units, [evaluation_unit_field, "SHAPE@"]) as cursor:
        for unit_id, geometry in cursor:
            if unit_id in (None, "") or geometry is None or float(getattr(geometry, "area", 0) or 0) <= 0:
                raise ValueError("评价单元编号为空或面几何无效；请先修复评价单元数据")
            key = str(unit_id)
            if key in unit_geometries:
                raise ValueError("评价单元编号重复：{}；容重设计要求编号唯一".format(key))
            unit_geometries[key] = geometry
    if not unit_geometries:
        raise ValueError("评价单元面图层为空")

    rows = list(arcpy.da.SearchCursor(profile_points, [unit_field, "SHAPE@"]))
    if not rows:
        raise ValueError("没有可用的质地构型调查点")
    if any(unit_id in (None, "") for unit_id, _geometry in rows):
        raise ValueError("评价单元编号存在空值；请先修复输入")
    if any(geometry is None for _unit_id, geometry in rows):
        raise ValueError("质地构型调查点含空坐标；请先修复输入")
    ids = [str(row[0]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("质地构型调查点必须每评价单元1个；请先人工消除重复编号")
    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在：" + str(out_gdb))
    output_path = os.path.join(out_gdb, out_name)
    if arcpy.Exists(output_path):
        raise FileExistsError("设计点输出已存在，不覆盖：" + output_path)
    radius = (2 / math.sqrt(3)) / meters_per_unit
    point_groups = []
    unit_checks = []
    for unit_id, profile_geometry in rows:
        key = str(unit_id)
        unit_geometry = unit_geometries.get(key)
        if unit_geometry is None:
            raise ValueError("质地构型调查点编号 {} 在评价单元面图层中不存在".format(key))
        if not unit_geometry.contains(profile_geometry):
            raise ValueError("质地构型调查点 {} 不在其编号对应的评价单元面内".format(key))
        center = profile_geometry.centroid
        candidate_points = None
        # Rotate the exact 2 m equilateral triangle to find a placement fully
        # inside the linked evaluation unit. Never publish points outside it.
        for rotation in range(0, 360, 5):
            points = []
            for idx in range(3):
                angle = math.radians(rotation + 90 + idx * 120)
                point = arcpy.PointGeometry(arcpy.Point(
                    center.X + radius * math.cos(angle),
                    center.Y + radius * math.sin(angle),
                ), sr)
                points.append(point)
            if all(unit_geometry.contains(point) for point in points):
                candidate_points = points
                break
        generated = len(candidate_points or [])
        unit_checks.append({
            "unit_id": key,
            "required": 3,
            "generated": generated,
            "status": "NEEDS_REVIEW" if generated == 3 else "NEEDS_FIELD_RELOCATION",
            "detail": "三个平行候选点均须落在对应评价单元内；边界空间不足时由采样人员现场选点并记录依据。",
        })
        if candidate_points:
            point_groups.append((key, candidate_points))
    profiled_units = {str(unit_id) for unit_id, _geometry in rows}
    for key in sorted(set(unit_geometries) - profiled_units):
        unit_checks.append({
            "unit_id": key,
            "required": 3,
            "generated": 0,
            "status": "NEEDS_PROFILE_POINT",
            "detail": "该评价单元没有质地构型调查点；须补充合规调查点后再布置容重平行样。",
        })
    if not point_groups:
        raise ValueError("所有质地构型调查点周边均无法在所属评价单元内布置完整容重三点组；未创建输出")

    def write(mark_created):
        output = _create_output(out_gdb, out_name, sr, [("PJDYBH", "TEXT", 30), ("SAMPLE_ID", "TEXT", 50), ("REP_NO", "SHORT", None), ("DESIGN_ST", "TEXT", 20), ("SRC_RULE", "TEXT", 40), ("RING_CM3", "LONG", None), ("REVIEW", "TEXT", 100)], mark_created)
        count = 0
        with arcpy.da.InsertCursor(output, ["SHAPE@", "PJDYBH", "SAMPLE_ID", "REP_NO", "DESIGN_ST", "SRC_RULE", "RING_CM3", "REVIEW"]) as dest:
            for unit_id, points in point_groups:
                for idx, point in enumerate(points):
                    dest.insertRow([point, unit_id, "RZ" + unit_id, idx + 1, "DESIGN_ONLY", "6.2.4.2;6.2.4.3(2)", 100, "现场确认质地构型点及环刀取样条件"])
                    count += 1
        return {"output": output, "count": count, "status": "NEEDS_REVIEW", "unit_checks": unit_checks,
                "rule": "每评价单元3个平行样；距质地构型调查点≤2m；平行点间距2m；100cm³环刀；每点位于对应评价单元内"}

    return _with_output_rollback(arcpy, output_path, write)
