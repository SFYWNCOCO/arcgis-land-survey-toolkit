"""网格法采样设计：方格网 / 网格交点 / 网格中心点 / 设计采样点 四套成果。

设计点仅为候选，不是实采记录。所有间距、避边、均匀性结论都须人工复核。
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from .task_state import _atomic_json

from .sampling import (_create_output, _projected_meters_per_unit,
                       _reject_filtered_layer, _spatial_reference_key,
                       _with_output_rollback)
from .sampling_metrics import boundary_probes, unit_quality


def _clean_id(value, label):
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError("{}存在空值；请先修复源数据".format(label))
    return text


def _load_parcels(arcpy, parcel_fc, unit_field, parcel_field):
    rows, seen = [], set()
    with arcpy.da.SearchCursor(parcel_fc, [unit_field, parcel_field, "SHAPE@"]) as cur:
        for unit_id, parcel_id, geom in cur:
            unit_id = _clean_id(unit_id, "评价单元编号")
            parcel_id = _clean_id(parcel_id, "图斑编号")
            if geom is None or geom.area <= 0:
                raise ValueError("输入含空几何或零面积图斑；请先修复源数据")
            if getattr(geom, "partCount", 1) > 1:
                raise ValueError("图斑 {} 为多部件；附录 C 注 6 禁止多部件图斑".format(parcel_id))
            key = parcel_id
            if key in seen:
                raise ValueError("图斑编号重复（含跨单元）：{} / {}".format(unit_id, parcel_id))
            seen.add(key)
            rows.append({"unit": unit_id, "parcel": parcel_id, "geom": geom})
    if not rows:
        raise ValueError("未找到带评价单元编号和图斑编号的有效面")
    return rows


def _group(rows):
    by_unit, by_parcel = {}, {}
    for row in rows:
        by_unit.setdefault(row["unit"], []).append(row)
        by_parcel[(row["unit"], row["parcel"])] = row["geom"]
    return by_unit, by_parcel


def _fishnet_cells(arcpy, arcpy_point, unit_geom, cell, sr, origin_key=None):
    """生成完整规则方格；相交面积单独保存，不改变正方形及其中心。

    网格线对齐到投影坐标的 cell 整数倍（origin_key 仅用于兼容旧调用），
    这样同一单元的方格、节点、中心点共用一套格网，不会出现半格错位。
    """
    ext = unit_geom.extent
    x0 = math.floor(ext.XMin / cell) * cell
    y0 = math.floor(ext.YMin / cell) * cell
    nx = int(math.ceil((ext.XMax - x0) / cell))
    ny = int(math.ceil((ext.YMax - y0) / cell))
    cells = []
    for i in range(max(nx, 1)):
        for j in range(max(ny, 1)):
            xa, ya = x0 + i * cell, y0 + j * cell
            ring = arcpy.Array([arcpy.Point(xa, ya), arcpy.Point(xa + cell, ya),
                                arcpy.Point(xa + cell, ya + cell), arcpy.Point(xa, ya + cell),
                                arcpy.Point(xa, ya)])
            poly = arcpy.Polygon(ring, sr)
            inter = poly.intersect(unit_geom, 4)
            area = inter.area if inter is not None else 0.0
            cells.append({"geom": poly, "intersection": inter, "inside_area": area,
                          "center_xy": (xa + cell / 2, ya + cell / 2),
                          "full": area >= cell * cell * 0.999, "col": i, "row": j})
    return cells, (x0, y0, max(nx, 1), max(ny, 1))


def _grid_nodes(arcpy, unit_geom, axes, cell, sr):
    """规则格网的节点（不取裁剪后多边形的顶点，避免边界上出现密集假节点）。"""
    x0, y0, nx, ny = axes
    nodes = []
    for i in range(nx + 1):
        for j in range(ny + 1):
            x, y = x0 + i * cell, y0 + j * cell
            # 几何用原始浮点，与方格角点同源；round 只用于去重键，
            # 否则 0.0005 边界会把同一节点读成 .724/.725 两种坐标。
            key = (round(x, 3), round(y, 3))
            pt = arcpy.PointGeometry(arcpy.Point(x, y), sr)
            nodes.append({"pt": pt, "key": key})
    return nodes


def _adaptive_cell(extent, area, required):
    """按布点数量反推方格尺寸：格数≈布点数，格网正方形。"""
    n = max(1, int(required))
    cell = math.sqrt(max(area, 1.0) / n)
    for _ in range(30):
        if area / (cell * cell) >= n:
            break
        cell *= 0.95
    return max(cell, 1.0)



def _node_key(pt):
    return (round(pt.X, 3), round(pt.Y, 3))


def _validate_sampling_crs(sr):
    """网格采样只接受可确认的 CGCS2000 三度高斯—克吕格米制坐标系。"""
    expected = "网格采样要求 CGCS2000 三度高斯—克吕格投影（米）；请先明确转换数据坐标系"
    name = str(getattr(sr, "name", "") or "")
    sr_type = str(getattr(sr, "type", "") or "").lower()
    if sr_type != "projected":
        raise ValueError("图斑必须使用投影坐标系；" + expected)

    projection = str(getattr(sr, "projectionName", "") or "").lower()
    if "gauss" not in projection or "kruger" not in projection.replace("-", ""):
        raise ValueError(expected)

    unit = str(getattr(sr, "linearUnitName", "") or "").lower()
    try:
        meters_per_unit = float(getattr(sr, "metersPerUnit"))
    except (TypeError, ValueError, AttributeError):
        meters_per_unit = float("nan")
    if unit not in ("meter", "metre", "meters", "metres") \
            or not math.isfinite(meters_per_unit) or abs(meters_per_unit - 1.0) > 1e-9:
        raise ValueError(expected)

    gcs = getattr(sr, "GCS", None)
    try:
        datum_code = int(getattr(gcs, "factoryCode", 0) or 0)
    except (TypeError, ValueError):
        datum_code = 0
    datum_name = str(getattr(gcs, "datumName", "") or "")
    datum_key = datum_name.lower().replace(" ", "_")
    if datum_code != 4490 and "china_2000" not in datum_key:
        raise ValueError(expected)

    try:
        central_meridian = float(getattr(sr, "centralMeridian"))
        false_easting = float(getattr(sr, "falseEasting"))
        wkid = int(getattr(sr, "factoryCode", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(expected)
    if not math.isfinite(central_meridian) or not math.isfinite(false_easting):
        raise ValueError(expected)
    zone_index = round(central_meridian / 3.0)
    if zone_index < 1 or abs(central_meridian - zone_index * 3.0) > 1e-7:
        raise ValueError(expected)

    name_key = name.lower().replace(" ", "_").replace("-", "_")
    says_six_degree = any(token in name_key for token in ("6_degree", "6degree", "6°", "6度"))
    says_three_degree = any(token in name_key for token in ("3_degree", "3degree", "3°", "3度"))
    if says_six_degree:
        raise ValueError(expected)

    encoded_false_easting = zone_index * 1_000_000.0 + 500_000.0
    uses_encoded_zone = abs(false_easting - encoded_false_easting) <= 1.0
    uses_cm_false_easting = abs(false_easting - 500_000.0) <= 1.0
    if not uses_encoded_zone and not (uses_cm_false_easting and says_three_degree):
        raise ValueError(expected)

    return {
        "wkid": wkid,
        "name": name,
        "projection": str(getattr(sr, "projectionName", "") or ""),
        "datum": datum_name or "CGCS2000",
        "central_meridian": central_meridian,
        "false_easting": false_easting,
        "zone_width_degrees": 3,
        "spacing_method": "GEODESIC",
    }


def _geodesic_distance_m(first, second):
    """ArcPy GEODESIC 距离返回米，用于间距阈值及候选点远近排序。"""
    method = getattr(first, "angleAndDistanceTo", None)
    if not callable(method):
        raise RuntimeError("ArcPy 几何不支持 GEODESIC 地面距离；停止采样设计")
    try:
        distance = float(method(second, "GEODESIC")[1])
    except Exception as exc:
        raise RuntimeError("无法计算采样点间的 GEODESIC 地面距离：{}".format(exc)) from exc
    if not math.isfinite(distance) or distance < 0:
        raise RuntimeError("ArcPy 返回无效的 GEODESIC 地面距离；停止采样设计")
    return distance


def _spacing_is_ok(chosen, min_spacing):
    return all(_geodesic_distance_m(a["pt"], b["pt"]) >= min_spacing - 1e-6
               for i, a in enumerate(chosen) for b in chosen[i + 1:])


def _boundary_distance_ok(arcpy, point, unit_geom, inset_m, sr):
    if inset_m <= 0:
        return True
    inset = unit_geom.buffer(-inset_m)
    if inset is None or inset.area <= 0:
        return False
    return bool(inset.contains(point))


def _boundary_rings(geom):
    """Keep parts and holes separate; never create probe lines between rings."""
    rings = []
    for i in range(int(getattr(geom, "partCount", 1))):
        ring = geom.getPart(i)
        if ring is None:
            continue
        # arcpy.Array 没有 isClosed 属性，只能取顶点；闭合与否交给 boundary_probes 判断
        # 带洞/多环面里 arcpy Array 会含空占位点（None），必须跳过，否则取 .X 直接崩
        part = []
        for point in ring:
            if point is None:
                if part:
                    rings.append(part)
                    part = []
            elif point.X is not None and point.Y is not None:
                part.append((point.X, point.Y))
        if part:
            rings.append(part)
    return rings


def _unit_quality(arcpy, uid, unit_geom, cells, chosen, meters_per_unit, min_spacing_m, inset_m,
                  parcel_geometries=None):
    """单元采样质量指标；算不出的项留 None，不猜。"""
    sr = unit_geom.spatialReference
    points = [{"pid": c.get('point_id') or "{}-{:02d}".format(uid, order), "xy": (c["pt"].firstPoint.X, c["pt"].firstPoint.Y)}
              for order, c in enumerate(chosen, 1)]
    owners = {_node_key(c['pt'].firstPoint): (parcel_geometries or {}).get((uid, c['parcel']), unit_geom)
              for c in chosen}
    cell_rows = [{"xy": c["center_xy"], "inside_area": c["inside_area"]} for c in cells]
    gaps = [math.hypot(a["xy"][0] - b["xy"][0], a["xy"][1] - b["xy"][1])
            for i, a in enumerate(cell_rows) for b in cell_rows[i + 1:]]
    # 只有一格时没有相邻格距参照：探针间距取点间距离的一半，避免整个单元退回“未计算”
    point_gaps = [math.hypot(a["xy"][0] - b["xy"][0], a["xy"][1] - b["xy"][1])
                  for i, a in enumerate(points) for b in points[i + 1:]]
    if gaps:
        spacing = min(gaps)
    elif point_gaps:
        spacing = min(point_gaps) / 2.0
    else:
        # 单点单元：没有任何间距参照，按包围盒短边的一半取探针，不退回未计算
        extent = unit_geom.extent
        spacing = min(extent.XMax - extent.XMin, extent.YMax - extent.YMin) / 2.0

    def distance_to_edge(xy):
        point = arcpy.PointGeometry(arcpy.Point(*xy), sr)
        return owners.get((round(xy[0], 3), round(xy[1], 3)), unit_geom).boundary().distanceTo(point) * meters_per_unit

    probes = [xy for ring in _boundary_rings(unit_geom) for xy in boundary_probes(ring, spacing)]
    for cell in cells:
        if cell['inside_area'] > 0:
            inter = cell['geom'].intersect(unit_geom, 4)
            if inter is not None and inter.area > 0:
                probes.append((inter.labelPoint.X, inter.labelPoint.Y))
    result = unit_quality(points, cells=cell_rows, probes=probes, unit_boundary=distance_to_edge,
                          edge_threshold=float(inset_m), min_spacing_m=float(min_spacing_m),
                          area_m2=unit_geom.getArea('GEODESIC', 'SQUAREMETERS'))
    result.update(覆盖半径估计方法='边界及内部探针', 覆盖探针数=len(probes),
                  覆盖半径性质='离散探针估计，不是全域最大距离证明', 距离口径='投影平面米')
    return result


def verify_grid_geometry(arcpy, report, expected_cell_size_m=None):
    """Read all four outputs; metadata alone is never evidence of a regular grid."""
    if isinstance(expected_cell_size_m, str) and expected_cell_size_m.strip().lower() in ('', 'auto', '自适应'):
        expected_cell_size_m = None
    sr = arcpy.Describe(report['grid']).spatialReference
    meters = _projected_meters_per_unit(sr)
    sizes = report.get('cell_sizes_m') or {}
    failures, cells, nodes_expected, widths = [], {}, set(), []
    def fail(message):
        if len(failures) < 20:
            failures.append(message)
    for kind in ('center', 'node', 'sample'):
        other = arcpy.Describe(report[kind]).spatialReference
        if _spatial_reference_key(sr) != _spatial_reference_key(other):
            fail('四套成果坐标系不一致：' + kind)
    if expected_cell_size_m is not None and (report.get('cell_mode') != 'fixed'
            or report.get('cell_size_m') != float(expected_cell_size_m)):
        fail('当前固定格距与成果模式/参数记录不一致')
    with arcpy.da.SearchCursor(report['grid'], ['PJDYBH', 'CELL_NO', 'SHAPE@']) as cursor:
        for uid, number, geometry in cursor:
            key = (str(uid), number)
            value = expected_cell_size_m if expected_cell_size_m is not None else sizes.get(str(uid))
            if geometry is None or value is None or not math.isfinite(float(value)) or float(value) <= 0:
                fail('网格缺少有效几何或精确格距：{}'.format(key))
                continue
            size = float(value) / meters
            e = geometry.extent
            tol = .002 / meters
            valid = (geometry.partCount == 1 and abs(e.width-size) <= tol and abs(e.height-size) <= tol
                     and abs(geometry.area-size*size) <= max(size * tol * 4, 1e-6)
                     and abs(e.XMin / size-round(e.XMin / size))*size <= tol
                     and abs(e.YMin / size-round(e.YMin / size))*size <= tol)
            if not valid or key in cells:
                fail('方格尺寸、面积、共同原点或编号异常：{}'.format(key))
            cells[key] = (e, size)
            widths.append(e.width * meters)
            for x in (e.XMin, e.XMax):
                for y in (e.YMin, e.YMax):
                    # 方格与节点经 ArcPy 几何引擎后毫米位可能分叉；集合匹配用厘米级，
                    # 尺寸/格心的 0.002 m 容差检查在下方独立保留。
                    nodes_expected.add((str(uid), round(x, 2), round(y, 2)))
    centers = set()
    with arcpy.da.SearchCursor(report['center'], ['PJDYBH', 'CELL_NO', 'SHAPE@XY']) as cursor:
        for uid, number, xy in cursor:
            key = (str(uid), number)
            if key not in cells or key in centers or xy is None:
                fail('格心关联或编号异常：{}'.format(key))
                continue
            e, _ = cells[key]
            if math.hypot(xy[0]-(e.XMin+e.XMax)/2, xy[1]-(e.YMin+e.YMax)/2)*meters > .002:
                fail('实际格心不在完整方格中心：{}'.format(key))
            centers.add(key)
    actual_nodes = []
    with arcpy.da.SearchCursor(report['node'], ['PJDYBH', 'SHAPE@XY']) as cursor:
        for uid, xy in cursor:
            actual_nodes.append((str(uid), round(xy[0], 2), round(xy[1], 2)))
    if not cells or centers != set(cells) or set(actual_nodes) != nodes_expected or len(actual_nodes) != len(set(actual_nodes)):
        fail('方格、中心点或节点集合不完整/重复')
    with arcpy.da.SearchCursor(report['sample'], ['PJDYBH', 'CELL_NO', 'SHAPE@XY']) as cursor:
        for uid, number, xy in cursor:
            key = (str(uid), number)
            if key not in cells or xy is None:
                fail('设计点缺少有效方格关联：{}'.format(key))
                continue
            e, _ = cells[key]
            allowed = [((e.XMin+e.XMax)/2, (e.YMin+e.YMax)/2)]
            allowed += [(x, y) for x in (e.XMin, e.XMax) for y in (e.YMin, e.YMax)]
            if min(math.hypot(xy[0]-x, xy[1]-y) for x, y in allowed)*meters > .002:
                fail('设计点不在关联格心或节点：{}'.format(key))
    return {'status': 'FAIL' if failures else 'PASS', 'failures': failures, 'cell_count': len(cells),
            'width_min_m': min(widths) if widths else None, 'width_max_m': max(widths) if widths else None,
            'scope': '四套实际几何读回；投影原点(0,0)，尺寸/格心/节点容差0.002米'}


def refresh_grid_statistics(arcpy, report, unit_fc, unit_field, parcel_fc=None, parcel_field=None):
    """Recompute from the final point layer after overrides or manual adjustment."""
    geometries, samples, cells, parcel_geometries = {}, {}, {}, {}
    with arcpy.da.SearchCursor(unit_fc, [unit_field, 'SHAPE@']) as cursor:
        for uid, geom in cursor:
            uid = str(uid)
            geometries[uid] = geometries[uid].union(geom) if uid in geometries else geom
    if parcel_fc:
        with arcpy.da.SearchCursor(parcel_fc, [unit_field, parcel_field, 'SHAPE@']) as cursor:
            for uid, pid, geom in cursor:
                key = (str(uid), str(pid))
                parcel_geometries[key] = parcel_geometries[key].union(geom) if key in parcel_geometries else geom
    with arcpy.da.SearchCursor(report['sample'], ['PJDYBH', 'DKBH', 'CELL_NO', 'POINT_ID', 'SHAPE@']) as cursor:
        for uid, parcel, cell, point_id, geom in cursor:
            samples.setdefault(str(uid), []).append({'parcel': str(parcel), 'cell_no': cell,
                'point_id': point_id, 'pt': geom})
    with arcpy.da.SearchCursor(report['grid'], ['PJDYBH', 'CELL_NO', 'CELL_M2', 'SHAPE@']) as cursor:
        for uid, number, area, geom in cursor:
            p = geom.trueCentroid
            cells.setdefault(str(uid), []).append({'geom': geom, 'inside_area': area,
                'center_xy': (p.X, p.Y), 'cell_no': number})
    meters = _projected_meters_per_unit(arcpy.Describe(unit_fc).spatialReference)
    for row in report['units']:
        uid = str(row['评价单元'])
        chosen, geom = samples.get(uid, []), geometries[uid]
        quality = _unit_quality(arcpy, uid, geom, cells[uid], chosen, meters,
                                report['min_spacing_m'], report['inset_m'], parcel_geometries)
        row['设计点数'] = len(chosen)
        row['采样质量'] = quality
        row['间距达标'] = _spacing_is_ok(chosen, report['min_spacing_m'])
        row['每格一点'] = len({p['cell_no'] for p in chosen}) == len(chosen)
        if parcel_fc:
            covered = {p['parcel'] for p in chosen}
            row['覆盖缺失图斑'] = [pid for unit, pid in parcel_geometries if unit == uid and pid not in covered]
        row['达标'] = len(chosen) >= max(row['最低点数'], row['图斑数']) and not row['覆盖缺失图斑']
        def owner(p):
            return parcel_geometries.get((uid, p['parcel']), geom)
        row['避边达标'] = all(_boundary_distance_ok(arcpy, p['pt'], owner(p), report['inset_m']/meters,
                                                 geom.spatialReference) for p in chosen)
        row['最小避边距离_m'] = min((owner(p).boundary().distanceTo(p['pt'])*meters for p in chosen), default=None)
    report['counts']['设计点'] = sum(len(rows) for rows in samples.values())
    return report


def _pick_points(arcpy, candidates, parcels, unit_geom, min_points,
                 min_spacing, inset_m, sr):
    """先筛避边候选、覆盖每图斑，再分散补点并向覆盖区域中心收敛。"""
    chosen, notes = [], []
    eligible = []
    for parcel in parcels:
        pool = [c for c in candidates if c.get("parcel") == parcel["parcel"]]
        if not pool:
            raise ValueError("图斑 {} 无可用格中心点；请减小网格尺寸".format(parcel["parcel"]))
        safe = [c for c in pool if _boundary_distance_ok(
            arcpy, c["pt"], parcel["geom"], inset_m, sr)]
        if not safe:
            notes.append({"parcel": parcel["parcel"], "issue": "无满足避边距离的格中心；保留候选供人工复核"})
        eligible.extend(safe or pool)
    for parcel in sorted(parcels, key=lambda p: p["geom"].area, reverse=True):
        pool = [c for c in eligible if c.get("parcel") == parcel["parcel"] and c not in chosen]
        spaced = [c for c in pool if all(_geodesic_distance_m(c["pt"], p["pt"]) >= min_spacing
                                        for p in chosen)]
        pool = spaced or pool
        centroid = arcpy.PointGeometry(parcel["geom"].labelPoint, sr)
        chosen.append(min(pool, key=lambda c: _geodesic_distance_m(c["pt"], centroid)))

    target = max(int(min_points), len(parcels))
    while len(chosen) < target:
        pool = [c for c in eligible if c not in chosen]
        if not pool:
            break
        spaced = [c for c in pool if all(_geodesic_distance_m(c["pt"], p["pt"]) >= min_spacing
                                        for p in chosen)]
        pool = spaced or pool
        chosen.append(max(pool, key=lambda c: min(
            _geodesic_distance_m(c["pt"], p["pt"]) for p in chosen)))

    chosen = _balance_points(eligible, chosen, min_spacing)
    spacing_ok = _spacing_is_ok(chosen, min_spacing)
    if inset_m > 0:
        near_edge = [c["cell_no"] for c in chosen
                     if not _boundary_distance_ok(arcpy, c["pt"], unit_geom, inset_m, sr)]
        if near_edge:
            notes.append({"issue": "部分格中心距图斑边界不足避让距离", "cells": near_edge[:10]})
    return chosen, spacing_ok, notes


def _balance_points(candidates, chosen, min_spacing):
    """在已有网格中心中做面积加权收敛，避免最远点策略把样点推向边角。"""
    if len(chosen) < 2:
        return chosen
    chosen = list(chosen)
    def xy(candidate):
        point = candidate["pt"].firstPoint
        return point.X, point.Y
    def distance2(first, second):
        return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
    for _ in range(12):
        groups = [[] for _ in chosen]
        for candidate in candidates:
            same_parcel = [i for i, point in enumerate(chosen)
                           if point["parcel"] == candidate["parcel"]]
            index = min(same_parcel, key=lambda i: distance2(xy(candidate), xy(chosen[i])))
            groups[index].append(candidate)
        changed = False
        for index, group in enumerate(groups):
            if not group:
                continue
            total = sum(c.get("weight", 1.0) for c in group)
            center = tuple(sum(xy(c)[axis] * c.get("weight", 1.0) for c in group) / total
                           for axis in (0, 1))
            others = chosen[:index] + chosen[index + 1:]
            pool = [c for c in candidates if c["parcel"] == chosen[index]["parcel"]
                    and c not in others and all(_geodesic_distance_m(c["pt"], p["pt"]) >= min_spacing
                                               for p in others)]
            if not pool:
                continue
            replacement = min(pool, key=lambda c: (distance2(xy(c), center), c["cell_no"]))
            if distance2(xy(replacement), center) + 1e-8 < distance2(xy(chosen[index]), center):
                chosen[index] = replacement
                changed = True
        if not changed:
            break
    return chosen


def _cell_centers(arcpy, cells, uid, geom, parcels, sr):
    centers, candidates = [], []
    for index, cellinfo in enumerate(cells, 1):
        cellinfo.update(unit=uid, cell_no=index)
        pt = arcpy.PointGeometry(arcpy.Point(*cellinfo["center_xy"]), sr)
        center = dict(unit=uid, cell_no=index, pt=pt, inside=bool(geom.contains(pt)),
                      full=cellinfo["full"], weight=max(cellinfo["inside_area"], 1e-8))
        centers.append(center)
        owner = next((row["parcel"] for row in parcels if row["geom"].contains(pt)), None)
        center["parcel"] = owner
        if center["inside"] and owner:
            candidates.append(center)
    return centers, candidates


def unit_min_points(uid, items, overrides, default):
    """单元最低点数：单元号或任一图斑号命中加密配置就取最大值。"""
    values = [int(default)]
    for key in [uid] + [str(item.get("parcel")) for item in items]:
        value = overrides.get(key)
        if isinstance(value, dict):
            value = value.get("min_points")
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(values)


def grid_design(parcel_fc, unit_field, parcel_field, out_gdb, cell_size_m=None,
                min_points=3, min_spacing_m=15.0, inset_m=5.0,
                min_points_by_unit=None,
                unit_layer=None, unit_layer_field=None):
    import arcpy
    desc = arcpy.Describe(parcel_fc)
    _reject_filtered_layer(desc, "图斑输入", arcpy.da.Describe(parcel_fc).get("FIDSet"))
    if str(getattr(desc, "shapeType", "")) != "Polygon":
        raise ValueError("网格法输入必须是面要素")
    sr = desc.spatialReference
    spatial_reference_check = _validate_sampling_crs(sr)
    meters_per_unit = _projected_meters_per_unit(sr)
    adaptive = cell_size_m is None or (isinstance(cell_size_m, str)
                                      and cell_size_m.strip().lower() in ("", "auto", "自适应"))
    checks = [(min_spacing_m, "最小间距", 0.0), (inset_m, "避边距离", 0.0)]
    if not adaptive:
        checks.insert(0, (cell_size_m, "网格尺寸", 0.5))
    for value, label, low in checks:
        if not math.isfinite(float(value)) or float(value) <= low - 1e-9:
            raise ValueError("{}必须大于 {} 米".format(label, low))
    if int(min_points) < 1:
        raise ValueError("每单元最低点数必须为正整数")
    overrides = {str(key): value for key, value in (min_points_by_unit or {}).items()}

    rows = _load_parcels(arcpy, parcel_fc, unit_field, parcel_field)
    by_unit, parcel_geom = _group(rows)

    unit_geom = {}
    if unit_layer:
        udesc = arcpy.Describe(unit_layer)
        _reject_filtered_layer(udesc, "评价单元面输入", arcpy.da.Describe(unit_layer).get("FIDSet"))
        if _spatial_reference_key(sr) != _spatial_reference_key(udesc.spatialReference):
            raise ValueError("图斑与评价单元面坐标系不一致；须按控制资料明确转换")
        with arcpy.da.SearchCursor(unit_layer, [unit_layer_field, "SHAPE@"]) as cur:
            for uid, geom in cur:
                unit_geom[_clean_id(uid, "评价单元编号")] = geom
    else:
        for uid, items in by_unit.items():
            merged = items[0]["geom"]
            for item in items[1:]:
                merged = merged.union(item["geom"])
            unit_geom[uid] = merged

    for uid, items in by_unit.items():
        if uid not in unit_geom:
            raise ValueError("评价单元 {} 在单元图层中不存在".format(uid))
        for item in items:
            if not unit_geom[uid].contains(item["geom"]):
                raise ValueError("图斑 {} 不完全位于评价单元 {} 内；须先复核边界/归属".format(
                    item["parcel"], uid))

    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在：" + str(out_gdb))
    names = {"grid": "方格网", "node": "网格交点", "center": "网格中心点", "sample": "设计采样点"}
    for name in names.values():
        if arcpy.Exists(os.path.join(out_gdb, name)):
            raise FileExistsError("网格成果已存在，不覆盖：" + os.path.join(out_gdb, name))

    adaptive = cell_size_m is None or (isinstance(cell_size_m, str)
                                      and cell_size_m.strip().lower() in ("", "auto", "自适应"))
    min_spacing = float(min_spacing_m) / meters_per_unit
    inset = float(inset_m) / meters_per_unit

    cells_all, nodes_all, centers_all, samples = [], [], [], []
    unit_report = []
    node_seen = set()
    for uid in sorted(unit_geom):
        geom = unit_geom[uid]
        unit_min = unit_min_points(uid, by_unit[uid], overrides, min_points)
        required = max(unit_min, len(by_unit[uid]))
        cell = _adaptive_cell(geom.extent, geom.area, required) if adaptive \
            else float(cell_size_m) / meters_per_unit
        interior_possible = set()
        for parcel in by_unit[uid]:
            interior = parcel["geom"].buffer(-inset) if inset > 0 else parcel["geom"]
            if interior is not None and interior.area > 0:
                interior_possible.add(parcel["parcel"])
        for attempt in range(21):
            cells, axes = _fishnet_cells(arcpy, arcpy.Point, geom, cell, sr)
            cells.sort(key=lambda c: (c["row"], c["col"]))
            centers, candidates = _cell_centers(arcpy, cells, uid, geom, by_unit[uid], sr)
            safe = [c for c in candidates if c["parcel"] not in interior_possible
                    or _boundary_distance_ok(arcpy, c["pt"], parcel_geom[(uid, c["parcel"])], inset, sr)]
            covered = {c["parcel"] for c in safe}
            if (not adaptive or (len(safe) >= required
                    and all(p["parcel"] in covered for p in by_unit[uid]))
                    or attempt == 20 or cell <= 0.5):
                break
            cell = max(cell * 0.85, 0.5)
        cells_all.extend(cells)
        centers_all.extend(centers)
        for node in _grid_nodes(arcpy, geom, axes, cell, sr):
            owner_key = (uid, node["key"])
            if owner_key in node_seen:
                continue
            node_seen.add(owner_key)
            nodes_all.append({"unit": uid, "pt": node["pt"]})
        if not candidates:
            raise ValueError("单元 {} 内没有可用格中心点；请减小网格尺寸".format(uid))
        chosen, spacing_ok, notes = _pick_points(arcpy, candidates, by_unit[uid], geom,
                                                 unit_min, min_spacing, inset, sr)
        counts = {}
        for order, pick in enumerate(chosen, 1):
            counts[pick["parcel"]] = counts.get(pick["parcel"], 0) + 1
            samples.append({"unit": uid, "parcel": pick["parcel"], "pt": pick["pt"],
                            "order": order, "cell_no": pick["cell_no"],
                            "spacing_ok": spacing_ok})
        missing = [p["parcel"] for p in by_unit[uid] if counts.get(p["parcel"], 0) < 1]
        center_cells = {c["cell_no"] for c in candidates}
        chosen_cells = {c["cell_no"] for c in chosen}
        quality = _unit_quality(arcpy, uid, geom, cells, chosen, meters_per_unit, min_spacing_m, inset_m)
        unit_report.append({
            "评价单元": uid,
            "图斑数": len(by_unit[uid]),
            "最低点数": unit_min,
            "网格尺寸": round(cell * meters_per_unit, 1),
            "网格尺寸精确_m": cell * meters_per_unit,
            "方格数": len(cells),
            "相交方格数": sum(c["inside_area"] > 0 for c in cells),
            "可布点格数": len(center_cells),
            "中心点数": len([c for c in centers_all if c["unit"] == uid]),
            "交点数": len([n for n in nodes_all if n["unit"] == uid]),
            "设计点数": len(chosen),
            "每格一点": chosen_cells.issubset(center_cells) and len(chosen_cells) == len(chosen),
            "达标": len(chosen) >= max(unit_min, len(by_unit[uid])) and not missing,
            "间距达标": spacing_ok,
            "避边达标": all(_boundary_distance_ok(arcpy, c["pt"], parcel_geom[(uid, c["parcel"])], inset, sr)
                            for c in chosen),
            "最小避边距离_m": round(min(parcel_geom[(uid, c["parcel"])].boundary().distanceTo(c["pt"])
                                          for c in chosen) * meters_per_unit, 3),
            "覆盖缺失图斑": missing,
            "采样质量": quality,
            "说明": notes,
        })

    def write(mark_created):
        grid_fc = _create_output(out_gdb, names["grid"], sr, [
            ("PJDYBH", "TEXT", 30), ("CELL_NO", "LONG", None),
            ("FULL_CELL", "SHORT", None), ("IN_UNIT", "SHORT", None),
            ("CELL_M2", "DOUBLE", None), ("CELL_MU", "DOUBLE", None)],
            mark_created, shape_type="POLYGON")
        with arcpy.da.InsertCursor(grid_fc, ["SHAPE@", "PJDYBH", "CELL_NO", "FULL_CELL", "IN_UNIT",
                                             "CELL_M2", "CELL_MU"]) as cur:
            for c in cells_all:
                area = c["intersection"].getArea("GEODESIC", "SQUAREMETERS") if c["inside_area"] > 0 else 0.0
                cur.insertRow([c["geom"], c["unit"], c["cell_no"],
                               1 if c["full"] else 0, int(c["inside_area"] > 0), area, area / 666.6666667])
        node_fc = _create_output(out_gdb, names["node"], sr, [("PJDYBH", "TEXT", 30)], mark_created)
        with arcpy.da.InsertCursor(node_fc, ["SHAPE@", "PJDYBH"]) as cur:
            for n in nodes_all:
                cur.insertRow([n["pt"], n["unit"]])
        center_fc = _create_output(out_gdb, names["center"], sr, [
            ("PJDYBH", "TEXT", 30), ("CELL_NO", "LONG", None), ("DKBH", "TEXT", 255),
            ("IN_UNIT", "SHORT", None)], mark_created)
        with arcpy.da.InsertCursor(center_fc, ["SHAPE@", "PJDYBH", "CELL_NO", "DKBH", "IN_UNIT"]) as cur:
            for c in centers_all:
                cur.insertRow([c["pt"], c["unit"], c["cell_no"], c.get("parcel"),
                               1 if c["inside"] else 0])
        sample_fc = _create_output(out_gdb, names["sample"], sr, [
            ("PJDYBH", "TEXT", 30), ("DKBH", "TEXT", 255), ("SAMPLE_ID", "TEXT", 30),
            ("POINT_ID", "TEXT", 80), ("CELL_NO", "LONG", None), ("SEQ", "SHORT", None),
            ("DESIGN_ST", "TEXT", 20), ("SRC_RULE", "TEXT", 40),
            ("DEPTH_TOP", "SHORT", None), ("DEPTH_BOT", "SHORT", None), ("REVIEW", "TEXT", 120)],
            mark_created)
        with arcpy.da.InsertCursor(sample_fc, ["SHAPE@", "PJDYBH", "DKBH", "SAMPLE_ID",
                                               "POINT_ID", "CELL_NO", "SEQ", "DESIGN_ST",
                                               "SRC_RULE", "DEPTH_TOP", "DEPTH_BOT", "REVIEW"]) as cur:
            for s in samples:
                review = "设计点；人工复核均匀性、田边避让与可达性"
                if not s["spacing_ok"]:
                    review += "；15米间距未满足，核实小图斑处理依据"
                cur.insertRow([s["pt"], s["unit"], s["parcel"], s["unit"],
                               "{}-{:02d}".format(s["unit"], s["order"]), s["cell_no"],
                               s["order"], "DESIGN_ONLY", "6.1.4;6.2.4.3(1)", 0, 20, review])
        report = {
            "grid": grid_fc, "node": node_fc, "center": center_fc, "sample": sample_fc,
            "cell_mode": "adaptive" if adaptive else "fixed",
            "cell_size_m": (None if adaptive else float(cell_size_m)),
            "cell_sizes_m": {u["评价单元"]: u["网格尺寸精确_m"] for u in unit_report},
            "min_points": int(min_points),
            "min_spacing_m": float(min_spacing_m), "inset_m": float(inset_m),
            "spatial_reference": getattr(sr, "name", ""),
            "spatial_reference_check": spatial_reference_check,
            "counts": {"方格": len(cells_all), "交点": len(nodes_all),
                       "中心点": len(centers_all), "设计点": len(samples),
                       "评价单元": len(unit_geom)},
            "units": unit_report,
            "selection_method": "regular_centers_inset_spacing_area_balanced",
            "status": "NEEDS_REVIEW",
            "rule": ("6.1.4 网格法：按布点数把评价单元划为不少于布点数的均匀网格，在格中心/节点布点；"
                     "每评价单元至少3个分样点、每图斑至少1点、一般间距不小于15米；"
                     "本工具只生成可编辑设计候选，均匀性、避边与代表性须人工复核"),
        }
        report['geometry_check'] = verify_grid_geometry(arcpy, report, None if adaptive else float(cell_size_m))
        if report['geometry_check']['status'] != 'PASS':
            raise ValueError('实际网格几何读回不符：' + '；'.join(report['geometry_check']['failures']))
        return report

    return _with_output_rollback(arcpy, os.path.join(out_gdb, names["grid"]), write)


def export_shapefiles(arcpy, report, out_folder):
    """把四套成果另存为 shp（按用户交付习惯分层）。"""
    os.makedirs(out_folder, exist_ok=True)
    paths = {}
    checks = []
    for key in ("grid", "node", "center", "sample"):
        src = report[key]
        name = os.path.basename(src)
        dst = os.path.join(out_folder, name + ".shp")
        if arcpy.Exists(dst):
            raise FileExistsError("shp 已存在，不覆盖：" + dst)
        arcpy.conversion.FeatureClassToFeatureClass(src, out_folder, name)
        check = verify_shapefile_identifiers(arcpy, src, dst)
        checks.append(check)
        _atomic_json(os.path.join(out_folder, 'shp_export_validation.json'), {
            'status': 'FAIL' if any(row['status'] == 'FAIL' for row in checks) else 'PASS', 'layers': checks})
        if check['status'] == 'FAIL':
            raise ValueError('SHP 编号读回不一致，可能超过字段字节长度；未判交付成功。见 ' + os.path.join(out_folder, 'shp_export_validation.json'))
        paths[key] = dst
    with open(os.path.join(out_folder, "grid_design.json"), "x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return paths


def verify_shapefile_identifiers(arcpy, source, destination):
    fields = [field.name for field in arcpy.ListFields(source)]
    ids = [name for name in ('PJDYBH', 'UNIT_KEY', 'DKBH', 'POINT_ID', 'SAMPLE_ID', 'DESIGN_GRP') if name in fields]
    result = dict(source=str(source), output=str(destination), fields=ids, status='FAIL')
    if not arcpy.Exists(destination):
        return dict(result, detail='SHP文件不存在')
    if _spatial_reference_key(arcpy.Describe(source).spatialReference) != _spatial_reference_key(
            arcpy.Describe(destination).spatialReference):
        return dict(result, detail='SHP坐标系与源成果不一致')
    output_fields = {field.name for field in arcpy.ListFields(destination)}
    if not ids or not set(ids) <= output_fields:
        return dict(result, detail='缺少可核对的编号字段')
    def values(path):
        with arcpy.da.SearchCursor(path, ids) as cursor:
            # DBF stores a nullable text value as spaces. Normalize only empty
            # values; preserve every character of nonempty identifiers.
            return Counter(tuple('' if value is None or not str(value).strip()
                                 else str(value) for value in row) for row in cursor)
    before, after = values(source), values(destination)
    result.update(source_count=sum(before.values()), output_count=sum(after.values()))
    if before != after:
        result.update(detail='编号或行数改变；SHP字段限制不得静默丢失编号',
            missing=[list(row) for row in list((before - after).keys())[:3]],
            unexpected=[list(row) for row in list((after - before).keys())[:3]])
    else:
        def geometries(path):
            grouped = {}
            with arcpy.da.SearchCursor(path, ids + ['SHAPE@']) as cursor:
                for row in cursor:
                    key = tuple('' if value is None or not str(value).strip() else str(value) for value in row[:-1])
                    grouped.setdefault(key, []).append(row[-1])
            return grouped
        expected, actual = geometries(source), geometries(destination)
        for key, shapes in expected.items():
            remaining = actual[key]
            for shape in shapes:
                match = next((i for i, candidate in enumerate(remaining)
                              if shape is not None and candidate is not None and shape.equals(candidate)), None)
                if match is None:
                    return dict(result, detail='编号相同但实际几何改变；SHP成果须重新导出', geometry_match=False)
                remaining.pop(match)
        result.update(status='PASS', detail='逐行编号组合、行数及实际几何读回一致', geometry_match=True)
    return result
