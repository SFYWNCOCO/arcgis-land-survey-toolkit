"""设计点吸附与即时空间复检。只编辑任务工作副本，保留修改收据。"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import time
import uuid

from . import task_state as T
from . import sampling_qa as QA
from .sampling import _projected_meters_per_unit


def custom_design_check(qa, rules, unit_rows):
    """Separate explicitly requested count differences from invalid geometry or association.

    The normative QA is deliberately kept intact, including its FAIL result.
    """
    overrides = (rules or {}).get('point_counts_by_unit') or {}
    unit_parcels = {str(row['unit']): {str(pid) for pid in row['parcels']} for row in unit_rows}
    coverage_groups = [set(group) for group in (rules or {}).get('combine_parcels', [])]
    accepted_counts, accepted_coverage, rejected = [], [], []
    for row in qa.get('unit_checks', []):
        uid = str(row['unit_id'])
        if row.get('status') == 'FAIL':
            (accepted_counts if uid in overrides and row.get('generated') == overrides[uid] else rejected).append(row)
    for row in qa.get('parcel_checks', []):
        if row.get('status') != 'FAIL':
            continue
        uid = str(row['unit_id'])
        allowed = uid in overrides and any(group == unit_parcels.get(uid) and str(row['parcel_id']) in group
            and overrides[uid] < len(group) for group in coverage_groups)
        (accepted_coverage if allowed else rejected).append(row)
    structural = ('empty_inputs','duplicate_parcels','invalid_parcels','duplicate_point_ids','invalid_points',
                  'unlinked_points','spatial_relation_failures','sample_id_violations','duplicate_sample_units',
                  'parcel_unit_mismatches','unit_containment_failures','invalid_evaluation_units',
                  'unmatched_spacing_exception_reviews')
    unsafe = bool(rejected) or any(qa.get(key) for key in structural)
    raw, valid = qa.get('raw_point_count'), qa.get('valid_linked_point_count')
    unsafe = unsafe or not isinstance(raw,int) or raw < 1 or raw != valid
    # A future/unknown QA failure must not be waived just because a project override exists.
    if qa.get('status') == 'FAIL' and not accepted_counts and not accepted_coverage:
        unsafe = True
    return {'technical_status': 'FAIL' if unsafe else 'PASS', 'normative_status': qa.get('status', 'FAIL'),
            'count_exceptions': accepted_counts, 'coverage_exceptions': accepted_coverage,
            'scope': '项目自定义设计数量；独立规范质检结果未改写'}


def apply_project_point_counts(arcpy, sample_fc, unit_fc, unit_field, counts):
    """Reduce only a new task work copy; retain original coordinates and an ID trace."""
    if not counts:
        return {'status':'NOT_REQUESTED','changes':[]}
    shapes = {}
    with arcpy.da.SearchCursor(unit_fc, [unit_field, 'SHAPE@']) as cursor:
        for uid, geometry in cursor:
            uid = str(uid)
            shapes[uid] = shapes[uid].union(geometry) if uid in shapes else geometry
    if set(counts) - set(shapes):
        raise ValueError('项目自定义点数含未知采样组：' + '、'.join(sorted(set(counts)-set(shapes))))
    records = list(arcpy.da.SearchCursor(sample_fc, ['OID@','POINT_ID','PJDYBH','SHAPE@XY']))
    if len({row[1] for row in records}) != len(records):
        raise ValueError('设计点编号重复，不能应用项目点数')
    kept, changes = {}, []
    for uid, count in counts.items():
        if isinstance(count,bool) or not isinstance(count,int) or count < 1:
            raise ValueError('项目点数必须为正整数')
        available = [row for row in records if str(row[2]) == uid]
        if len(available) < count:
            raise ValueError('采样组 {} 只有{}个原设计点，不能增加到{}'.format(uid,len(available),count))
        center = shapes[uid].trueCentroid
        available.sort(key=lambda row: ((row[3][0]-center.X)**2+(row[3][1]-center.Y)**2,str(row[1])))
        selected = [available.pop(0)]
        while len(selected) < count:
            chosen = max(available, key=lambda row: (min((row[3][0]-other[3][0])**2+
                         (row[3][1]-other[3][1])**2 for other in selected),str(row[1])))
            selected.append(chosen)
            available.remove(chosen)
        for seq, row in enumerate(selected, 1):
            kept[row[0]] = (uid + '-{:02d}'.format(seq),seq)
        changes.append({'unit':uid,'before':len(selected)+len(available),'after':count,
                        'retained_original_point_ids':[row[1] for row in selected]})
    if 'ORIG_POINT' not in {field.name for field in arcpy.ListFields(sample_fc)}:
        arcpy.management.AddField(sample_fc,'ORIG_POINT','TEXT',field_length=80)
    with arcpy.da.UpdateCursor(sample_fc,['OID@','POINT_ID','PJDYBH','SEQ','ORIG_POINT']) as cursor:
        for row in cursor:
            row[4] = row[4] or row[1]
            if str(row[2]) in counts:
                if row[0] not in kept:
                    cursor.deleteRow()
                    continue
                row[1], row[3] = kept[row[0]]
            cursor.updateRow(row)
    return {'status':'NEEDS_REVIEW','changes':changes,'note':'用户指定设计点数，不代表规范质检通过'}


def design_signature(data, sampling, arcpy):
    digest = hashlib.sha256()
    sources = [(sampling["grid"]["sample"], ["POINT_ID", "PJDYBH", "DKBH", "CELL_NO", "SAMPLE_ID", "DESIGN_ST", "SHAPE@JSON"]),
               (sampling["units_for_map"], [data["unit_field"], "SHAPE@JSON"]),
               (data["assign"], [data["unit_field"], data["assign_id"], "SHAPE@JSON"])]
    for key, fields in (("grid", ["PJDYBH", "CELL_NO", "SHAPE@JSON"]), ("node", ["PJDYBH", "SHAPE@JSON"]),
                        ("center", ["PJDYBH", "CELL_NO", "DKBH", "IN_UNIT", "SHAPE@JSON"])):
        sources.append((sampling["grid"][key], fields))
    bulk = sampling.get("bulk_design") or {}
    if bulk.get("profiles"):
        sources.append((bulk["profiles"], ["PJDYBH", "DESIGN_ST", "SHAPE@JSON"]))
    if bulk.get("output"):
        sources.append((bulk["output"], ["PJDYBH", "SAMPLE_ID", "REP_NO", "DESIGN_ST", "SHAPE@JSON"]))
    for source, fields in sources:
        with arcpy.da.SearchCursor(source, fields) as cursor:
            rows = sorted(json.dumps(list(row), ensure_ascii=False, default=str) for row in cursor)
        for row in rows:
            digest.update(row.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def format_dms(value, axis):
    value = float(value)
    limit = 180 if axis == "longitude" else 90
    if axis not in ("longitude", "latitude") or not math.isfinite(value) or abs(value) > limit:
        raise ValueError("经纬度值或坐标轴无效")
    degree, remainder = divmod(int(round(abs(value) * 360000)), 360000)
    minute, second = divmod(remainder, 6000)
    direction = ("W" if value < 0 else "E") if axis == "longitude" else ("S" if value < 0 else "N")
    return "{}°{:02d}′{:05.2f}″{}".format(degree, minute, second / 100, direction)


def write_point_csv(path, source, arcpy):
    sr = arcpy.Describe(source).spatialReference
    if str(getattr(sr, "name", "Unknown")) == "Unknown":
        raise ValueError("点图层坐标系未知，无法可靠导出经纬度")
    lonlat_sr = arcpy.SpatialReference(4490)
    wgs_sr = arcpy.SpatialReference(4326)
    available = {field.name for field in arcpy.ListFields(source)}
    required = ["PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID", "DEPTH_TOP", "DEPTH_BOT", "DESIGN_ST"]
    missing = sorted(set(required) - available)
    if missing:
        raise ValueError("设计点缺少字段：" + "、".join(missing))
    terrain_fields = [name for name in ("Z_DEM_M", "SLOPE_DEG") if name in available]
    fields = ["PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID", "SHAPE@", "DEPTH_TOP", "DEPTH_BOT", "DESIGN_ST"] + terrain_fields
    def natural(value):
        return tuple((0, int(token)) if token.isdigit() else (1, token.casefold())
                     for token in re.split(r"(\d+)", str(value or "")))
    with arcpy.da.SearchCursor(source, fields) as cursor:
        records = sorted(cursor, key=lambda row: (natural(row[1]), natural(row[2])))
    temporary = path + ".adjust.tmp"
    try:
        with open(temporary, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["评价单元", "图斑编号", "设计点编号", "混合样号", "X_成果坐标系", "Y_成果坐标系", "上界深度_cm", "下界深度_cm", "状态", "坐标系",
                             "经度_CGCS2000_度", "纬度_CGCS2000_度", "经度_度分秒", "纬度_度分秒", "经纬度坐标系", "海拔_DEM_m", "坡度_DEM_度",
                             "经度_WGS84_导航", "纬度_WGS84_导航", "LON", "LAT"])
            for row in records:
                uid, pid, point_id, sample_id, geometry, top, bottom, status = row[:8]
                if geometry is None:
                    raise ValueError("设计点 {} 几何为空".format(point_id))
                xy = geometry.firstPoint
                try:
                    lonlat = geometry.projectAs(lonlat_sr).firstPoint
                except Exception as exc:  # noqa: BLE001 - 投影失败要报出点号，不写半行
                    raise ValueError("设计点 {} 投影到 CGCS2000 经纬度失败：{}".format(point_id, exc)) from exc
                if not all(math.isfinite(value) for value in (lonlat.X, lonlat.Y)):
                    raise ValueError("设计点 {} 经纬度转换失败".format(point_id))
                terrain = dict(zip(terrain_fields, row[8:]))
                # 导航用 WGS84（手机/奥维直接可用），与 CGCS2000 经纬度分列；LON/LAT 供 photo_geotag.py 匹配
                try:
                    wgs = geometry.projectAs(wgs_sr).firstPoint
                except Exception as exc:  # noqa: BLE001 - 投影失败要报出点号，不写半行
                    raise ValueError("设计点 {} 投影到 WGS84 失败：{}".format(point_id, exc)) from exc
                if not all(math.isfinite(value) for value in (wgs.X, wgs.Y)):
                    raise ValueError("设计点 {} WGS84 坐标转换失败".format(point_id))
                writer.writerow([uid, pid, point_id, sample_id, xy.X, xy.Y, top, bottom, status, sr.name,
                                 "{:.6f}".format(lonlat.X), "{:.6f}".format(lonlat.Y),
                                 format_dms(lonlat.X, "longitude"), format_dms(lonlat.Y, "latitude"),
                                 "CGCS2000 (EPSG:4490)", terrain.get("Z_DEM_M"), terrain.get("SLOPE_DEG"),
                                 "{:.8f}".format(wgs.X), "{:.8f}".format(wgs.Y),
                                 "{:.8f}".format(wgs.X), "{:.8f}".format(wgs.Y)])
        os.replace(temporary, path)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)


def refresh_sampling(data, sampling, arcpy):
    from .grid_sampling import refresh_grid_statistics, verify_grid_geometry
    sampling['grid']['geometry_check'] = verify_grid_geometry(arcpy, sampling['grid'])
    if sampling['grid']['geometry_check']['status'] != 'PASS':
        raise ValueError('方格、格心或设计点几何不符；请重新生成设计')
    refresh_grid_statistics(arcpy, sampling['grid'], sampling['units_for_map'], data['unit_field'],
                            data['assign'], data['assign_id'])
    qa_dir = os.path.join(sampling["gdb"], "..", "复检记录")
    qa_dir = os.path.abspath(qa_dir)
    os.makedirs(qa_dir, exist_ok=True)
    qa_path = os.path.join(qa_dir, "采样质检_{}.json".format(uuid.uuid4().hex))
    qa = QA.check_composite_design(data["assign"], sampling["units_for_map"], sampling["grid"]["sample"],
        data["unit_field"], data["assign_id"], data["unit_field"], "PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID",
        "DESIGN_ST", qa_path, arcpy_module=arcpy)
    sampling["independent_qa"] = qa
    sampling["qa_path"] = qa_path
    bulk = sampling.get("bulk_design") or {}
    if bulk.get("output"):
        bulk_qa_path = os.path.join(qa_dir, "容重质检_{}.json".format(uuid.uuid4().hex))
        bulk["qa"] = QA.check_bulk_density_design(bulk["profiles"], sampling["units_for_map"], bulk["output"],
            "PJDYBH", data["unit_field"], "PJDYBH", "SAMPLE_ID", "REP_NO", "DESIGN_ST", bulk_qa_path)
        bulk["qa_path"] = bulk_qa_path
    write_point_csv(sampling["point_csv"], sampling["grid"]["sample"], arcpy)
    from .grid_sampling import export_shapefiles
    shp_dir = os.path.join(os.path.dirname(sampling["shp_dir"]), "shp_revision_" + uuid.uuid4().hex[:12])
    sampling["shapefiles"] = export_shapefiles(arcpy, sampling["grid"], shp_dir)
    sampling["shp_dir"] = shp_dir
    sampling["design_signature"] = design_signature(data, sampling, arcpy)
    return qa


def adjust_point(out_dir, point_id, x, y, request_wkid, operator, max_snap_m=200,
                 expected_signature=None, expected_revision=None):
    import arcpy
    if not str(operator or "").strip():
        raise ValueError("请填写点位修改人")
    if not all(math.isfinite(float(value)) for value in (x, y, max_snap_m)) or max_snap_m <= 0:
        raise ValueError("坐标和吸附距离必须为有效数值")
    state = T.read_task(out_dir)
    if state["status"] == "RUNNING":
        raise RuntimeError("任务正在执行，不能同时调整点位")
    data = state["phases"]["data_prepared"]["detail"]
    sampling = state["phases"]["sampling_prepared"]["detail"]
    if expected_signature is not None and expected_signature != design_signature(data, sampling, arcpy):
        raise ValueError("预览设计已改变，请重新准备后选择点位")
    if expected_revision is not None and int(expected_revision) != int(state.get("design_revision", 0)):
        raise ValueError("预览修订号已改变，请重新准备后选择点位")
    grid, sample_fc = sampling["grid"], sampling["grid"]["sample"]
    sr = arcpy.Describe(sample_fc).spatialReference
    meters = _projected_meters_per_unit(sr)
    requested = arcpy.PointGeometry(arcpy.Point(float(x), float(y)), arcpy.SpatialReference(int(request_wkid)))
    if requested.spatialReference.name == "Unknown":
        raise ValueError("点击坐标缺少已知坐标系")
    requested = requested.projectAs(sr)
    rows = list(arcpy.da.SearchCursor(sample_fc, ["POINT_ID", "PJDYBH", "DKBH", "CELL_NO", "SHAPE@"]))
    targets = [row for row in rows if str(row[0]) == str(point_id)]
    if len(targets) != 1:
        raise ValueError("设计点编号不存在或不唯一，请刷新预览后重选")
    _pid, uid, parcel, old_cell, old_geometry = targets[0]
    occupied = {int(row[3]) for row in rows if row[1] == uid and str(row[0]) != str(point_id)}
    parcel_geometry = None
    with arcpy.da.SearchCursor(data["assign"], [data["unit_field"], data["assign_id"], "SHAPE@"]) as cursor:
        for unit, pid, geometry in cursor:
            if str(unit) == str(uid) and str(pid) == str(parcel):
                parcel_geometry = geometry if parcel_geometry is None else parcel_geometry.union(geometry)
    if parcel_geometry is None:
        raise ValueError("设计点的图斑关联已失效")
    cells = []
    with arcpy.da.SearchCursor(grid["grid"], ["PJDYBH", "CELL_NO", "SHAPE@"]) as cursor:
        cells = [(int(cell), geometry) for unit, cell, geometry in cursor if str(unit) == str(uid) and int(cell) not in occupied]
    candidates = []
    with arcpy.da.SearchCursor(grid["center"], ["PJDYBH", "CELL_NO", "IN_UNIT", "SHAPE@"]) as cursor:
        for unit, cell, inside, geometry in cursor:
            if str(unit) == str(uid) and inside and int(cell) not in occupied and parcel_geometry.contains(geometry):
                candidates.append((geometry.distanceTo(requested), int(cell), "中心", geometry))
    with arcpy.da.SearchCursor(grid["node"], ["PJDYBH", "SHAPE@"]) as cursor:
        for unit, geometry in cursor:
            if str(unit) == str(uid) and parcel_geometry.contains(geometry):
                for cell, boundary in cells:
                    if not boundary.disjoint(geometry):
                        candidates.append((geometry.distanceTo(requested), cell, "节点", geometry))
    candidates = [item for item in candidates if item[0] * meters <= float(max_snap_m)]
    if not candidates:
        raise ValueError("附近没有属于同一图斑的空闲网格候选点；请点击更近的位置或调整网格")
    distance, cell, kind, geometry = min(candidates, key=lambda item: (item[0], item[1], item[2]))
    audit_path = os.path.join(out_dir, "点位修改记录.json")
    audit = []
    if os.path.isfile(audit_path):
        with open(audit_path, encoding="utf-8") as stream:
            audit = json.load(stream)
    editor = arcpy.da.Editor(sampling["gdb"])
    editor.startEditing(False, False)
    editor.startOperation()
    try:
        with arcpy.da.UpdateCursor(sample_fc, ["POINT_ID", "CELL_NO", "SHAPE@"]) as cursor:
            for row in cursor:
                if str(row[0]) == str(point_id):
                    cursor.updateRow([row[0], cell, geometry])
        qa = QA.check_composite_design(data["assign"], sampling["units_for_map"], sample_fc,
            data["unit_field"], data["assign_id"], data["unit_field"], "PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID",
            "DESIGN_ST", audit_path + ".{}.qa.json".format(uuid.uuid4().hex), arcpy_module=arcpy)
        if qa["status"] == "FAIL":
            raise ValueError("调整后的独立质检未通过，已撤销本次修改；请查看点位修改检查报告")
        editor.stopOperation()
        editor.stopEditing(True)
    except Exception:
        editor.abortOperation()
        editor.stopEditing(False)
        raise
    old_xy = [old_geometry.centroid.X, old_geometry.centroid.Y]
    new_xy = [geometry.centroid.X, geometry.centroid.Y]
    receipt = {"point_id": point_id, "unit": uid, "parcel": parcel, "old_xy": old_xy, "new_xy": new_xy,
               "old_cell": old_cell, "new_cell": cell, "snap_kind": kind, "snap_distance_m": distance * meters,
               "operator": str(operator).strip(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "qa": qa}
    audit.append(receipt)
    T._atomic_json(audit_path, audit)
    refresh_sampling(data, sampling, arcpy)
    state["design_revision"] = int(state.get("design_revision", 0)) + 1
    for phase in ("maps_built", "maps_exported", "completed"):
        state["phases"][phase] = {"status": "PENDING", "invalidated": "point_adjustment"}
    state["status"] = "PREPARED"
    state["history"].append({"event": "点位调整并即时复检", "point_id": point_id, "revision": state["design_revision"]})
    T._atomic_json(os.path.join(out_dir, T.STATE_NAME), state)
    return receipt
