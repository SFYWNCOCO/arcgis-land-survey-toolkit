"""已有单元的独立数据复核；原则值与资料未知保留复核状态。"""
from __future__ import annotations
import math
import hashlib
import json
from .data_intake import FACTOR_FIELDS, field_mapping
from .rules import unit_area_status
from .sampling import _projected_meters_per_unit
from .task_state import _atomic_json

MU_M2 = 666.6666667


def _text(value):
    return '' if value is None else str(value).strip()


def check_existing_records(records, max_mu=50.0, meters_per_unit=1.0):
    if not math.isfinite(float(max_mu)) or float(max_mu) <= 0:
        raise ValueError('单元面积原则值必须是正数')
    if not math.isfinite(float(meters_per_unit)) or float(meters_per_unit) <= 0:
        raise ValueError('坐标线性单位换算无效')
    issues, groups, spatial = [], {}, []
    def issue(code, status, row, detail, **extra):
        issues.append(dict(code=code, status=status, unit_id=_text(row.get('unit_id')),
            parcel_id=_text(row.get('parcel_id')), detail=detail, **extra))
    for record in records:
        row = dict(record)
        uid, pid, geometry = _text(row.get('unit_id')), _text(row.get('parcel_id')), row.get('geometry')
        if not uid or not pid:
            issue('empty_identity', 'FAIL', row, '单元编号或图斑编号为空')
            continue
        if geometry is None:
            issue('empty_geometry', 'FAIL', row, '图斑为空几何')
            continue
        try:
            area_m2 = float(geometry.getArea('GEODESIC', 'SQUAREMETERS'))
            if not math.isfinite(area_m2) or area_m2 <= 0:
                raise ValueError()
        except Exception:
            issue('invalid_area', 'FAIL', row, '无法取得有限正数的椭球面积')
            continue
        if getattr(geometry, 'partCount', 1) > 1:
            issue('multipart_parcel', 'FAIL', row, '源图斑为多部件，须修复后再设计')
        row.update(unit_id=uid, parcel_id=pid, area_m2=area_m2)
        groups.setdefault(uid, []).append(row)
        spatial.append(row)

    # X范围扫描先排除不相交候选，再计算真实面相交，不以包围盒相交判失败。
    try:
        ordered = sorted(spatial, key=lambda row: row['geometry'].extent.XMin)
        active = []
        for row in ordered:
            geometry, box = row['geometry'], row['geometry'].extent
            active = [other for other in active if other['geometry'].extent.XMax > box.XMin]
            for other in active:
                old = other['geometry'].extent
                if old.YMax <= box.YMin or box.YMax <= old.YMin:
                    continue
                try:
                    overlap_m2 = float(geometry.intersect(other['geometry'], 4).area) * meters_per_unit ** 2
                    if not math.isfinite(overlap_m2) or overlap_m2 < 0:
                        raise ValueError('相交面积不是有限非负数')
                    tolerance = max(.001, min(row['area_m2'], other['area_m2']) * 1e-7)
                    if overlap_m2 > tolerance:
                        issue('parcel_overlap', 'FAIL', row, '图斑面存在实际重叠',
                            other_unit_id=other['unit_id'], other_parcel_id=other['parcel_id'], overlap_m2=overlap_m2)
                except Exception:
                    issue('overlap_unverified', 'NEEDS_REVIEW', row, '图斑相交关系无法核验', other_parcel_id=other['parcel_id'])
            active.append(row)
    except Exception:
        issues.append(dict(code='extent_unverified', status='NEEDS_REVIEW', detail='空间候选范围无法读取，重叠检查未完成'))

    # 图斑与单元是多对多：同一源图斑编号可对应多个不重叠的单元面。
    # 几何重叠或同一单元内重复才是 FAIL；不重叠的跨单元重复是合法的拆分候选。
    parcel_groups = {}
    for row in spatial:
        parcel_groups.setdefault(row['parcel_id'], []).append(row)
    for _pid, rows in parcel_groups.items():
        if len(rows) < 2:
            continue
        units = sorted({row['unit_id'] for row in rows})
        if len(units) == 1:
            issue('duplicate_parcel_id', 'FAIL', rows[1], '同一单元内图斑编号重复', unit_ids=units)
            continue
        overlapping = False
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if first['unit_id'] == second['unit_id']:
                    continue
                try:
                    overlap_m2 = float(first['geometry'].intersect(second['geometry'], 4).area) * meters_per_unit ** 2
                except Exception:
                    continue
                if not math.isfinite(overlap_m2) or overlap_m2 < 0:
                    continue
                tolerance = max(.001, min(first['area_m2'], second['area_m2']) * 1e-7)
                if overlap_m2 > tolerance:
                    overlapping = True
                    break
            if overlapping:
                break
        if overlapping:
            issue('duplicate_parcel_id', 'FAIL', rows[0], '同一图斑编号出现在多个单元且几何重叠', unit_ids=units)
        else:
            issue('split_parcel_across_units', 'NEEDS_REVIEW', rows[0],
                '同一源图斑按面积份额拆分到多个单元；须人工确认回汇关系', unit_ids=units)

    units = []
    for uid, rows in groups.items():
        projects = {_text(row.get('project')) for row in rows}
        known = projects - {'', '未填'}
        if len(known) > 1:
            issue('cross_project_unit', 'FAIL', rows[0], '同一单元跨多个项目', projects=sorted(known))
        if not known or projects & {'', '未填'}:
            issue('missing_project', 'NEEDS_REVIEW', rows[0], '项目归属缺失，不能推定为同一项目')
        for role in FACTOR_FIELDS:
            values = {_text(row.get('factors', {}).get(role)) for row in rows}
            if values & {'', '未填'}:
                issue('missing_factor', 'NEEDS_REVIEW', rows[0], '划分因素资料缺失', factor=role)
            known_values = values - {'', '未填'}
            if len(known_values) > 1:
                issue('factor_conflict', 'FAIL', rows[0], '同一单元内四因素不一致', factor=role, values=sorted(known_values))
            if role == 'supplement_source' and known_values - {'恢复', '垦造'}:
                issue('source_value_unverified', 'NEEDS_REVIEW', rows[0], '补充耕地来源取值需核对', values=sorted(known_values))
        area_m2 = sum(row['area_m2'] for row in rows)
        area_status = unit_area_status(area_m2 / MU_M2, max_mu, context="review")
        if area_status != 'PASS':
            issue('area_principle', area_status, rows[0], '已有单元面积超过原则值，须复核划分或说明依据',
                area_mu=area_m2 / MU_M2, principle_mu=float(max_mu))
        related = [item for item in issues if item.get('unit_id') == uid
                   or item.get('other_unit_id') == uid or uid in (item.get('unit_ids') or [])]
        statuses = {item['status'] for item in related}
        units.append(dict(unit=uid, project=next(iter(known)) if len(known) == 1 and not projects & {'', '未填'} else '未填',
            parcels=[row['parcel_id'] for row in rows], area_m2=area_m2, mu=round(area_m2 / MU_M2, 2), landtype='',
            review_status='FAIL' if 'FAIL' in statuses else 'NEEDS_REVIEW' if statuses else 'PASS'))
    if not groups and not issues:
        issues.append(dict(code='empty_input', status='FAIL', detail='没有有效图斑'))
    statuses = {item['status'] for item in issues}
    return dict(status='FAIL' if 'FAIL' in statuses else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in statuses else 'PASS',
        units=units, issues=issues, unit_checks=[dict(unit_id=row['unit'], status=row['review_status']) for row in units],
        area_method='GEODESIC SQUAREMETERS；累计后才换算/显示亩',
        manual_review_required=['相对集中性、资料真实性和单元业务确认仍需专业复核'])


def check_existing_units(arcpy, source, unit_field, id_field, max_mu=50.0, output=None):
    fields = [field.name for field in arcpy.ListFields(source)]
    if unit_field not in fields or id_field not in fields:
        raise ValueError('已有单元或图斑编号字段不存在')
    mapping = field_mapping(fields)
    columns = list(dict.fromkeys(['OID@', unit_field, id_field, 'SHAPE@'] +
        [mapping[role] for role in FACTOR_FIELDS if role in mapping] + ([mapping['project']] if mapping.get('project') else [])))
    records = []
    with arcpy.da.SearchCursor(source, columns) as cursor:
        for values in cursor:
            row = dict(zip(columns, values))
            records.append(dict(oid=row['OID@'], unit_id=row[unit_field], parcel_id=row[id_field],
                geometry=row['SHAPE@'], project=row.get(mapping.get('project')),
                factors={role: row.get(mapping.get(role)) for role in FACTOR_FIELDS}))
    result = check_existing_records(records, max_mu=max_mu,
        meters_per_unit=_projected_meters_per_unit(arcpy.Describe(source).spatialReference))
    snapshot = [{key: value for key, value in row.items() if key != 'geometry'} | {
        'geometry_wkb': bytes(row['geometry'].WKB).hex() if row.get('geometry') is not None else None} for row in records]
    result['content_fingerprint'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    result.update(source=str(source), fields=mapping, unit_field=unit_field, id_field=id_field)
    if output:
        result['path'] = str(output)
        _atomic_json(str(output), result)
    return result
