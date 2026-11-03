"""图斑几何编辑候选：复制、拓扑核验、内容绑定确认和撤销；不写源数据。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from .data_intake import field_mapping, FACTOR_FIELDS, OTHER_FIELDS
from .profile import source_folder, default_path, load as load_profile, project_rules
from .sampling import _projected_meters_per_unit
from .task_state import _atomic_json

ID_FIELD = 'PE_ID'
ORIGIN_FIELD = 'PE_ORIG'
EDIT_FIELDS = {ID_FIELD, ORIGIN_FIELD, 'PE_EDIT', 'PE_M2'}


def origin_id(value, fallback):
    value = '' if value is None else str(value).strip()
    return value or fallback


def selection_ids(values, operation):
    if isinstance(values, str):
        values = json.loads(values)
    if not isinstance(values, list) or not values or any(type(v) is not int or v < 0 for v in values):
        raise ValueError('请选择有效图斑；对象ID必须是非负整数数组')
    if len(set(values)) != len(values) or len(values) > 1000:
        raise ValueError('图斑选择重复或超过1000块')
    if operation not in ('merge', 'split') or (operation == 'merge' and len(values) < 2) or (operation == 'split' and len(values) != 1):
        raise ValueError('合并至少选择两块；分割只能选择一块')
    return set(values)


def revised_profile(saved, changed_ids, replacement_ids):
    """保留制图和技术参数，显式撤销依赖旧几何/旧单元的设计规则。"""
    result, invalidated = copy.deepcopy(saved), []
    changed_ids, replacement_ids = {str(v).strip() for v in changed_ids}, [str(v).strip() for v in replacement_ids]
    groups = result.get('sampling_groups', [])
    result['sampling_groups'] = [g for g in groups if not changed_ids.intersection(str(v).strip() for v in g['parcels'])]
    if len(result['sampling_groups']) != len(groups): invalidated.append('受影响的人工采样组')
    meta = result.setdefault('meta', {})
    rules = meta['project_rules'] = copy.deepcopy(project_rules(meta))
    excluded = set(rules.get('excluded_parcels', []))
    if changed_ids.intersection(excluded) and not changed_ids.issubset(excluded):
        raise ValueError('不能合并已排除与未排除的地块')
    rules['excluded_parcels'] = sorted((excluded - changed_ids) | (set(replacement_ids) if changed_ids.issubset(excluded) else set()))
    groups = rules.get('combine_parcels', [])
    rules['combine_parcels'] = [g for g in groups if not changed_ids.intersection(g)]
    if len(groups) != len(rules['combine_parcels']): invalidated.append('受影响的合并图幅')
    for name in ('point_counts_by_unit', 'map_scales'):
        if rules.pop(name, None): invalidated.append(name)
    result.pop('_path', None)
    return result, invalidated


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dataset_digest(api, source):
    fields = [f.name for f in api.ListFields(source) if f.type not in ('OID', 'Geometry', 'Blob', 'Raster')]
    digest = hashlib.sha256(api.Describe(source).spatialReference.exportToString().encode())
    digest.update(json.dumps(fields).encode())
    with api.da.SearchCursor(source, ['OID@'] + fields + ['SHAPE@WKB']) as cursor:
        rows = sorted((list(row[:-1]) + [bytes(row[-1]).hex() if row[-1] else None] for row in cursor), key=lambda r: r[0])
    for row in rows: digest.update(json.dumps(row, ensure_ascii=False, default=str).encode('utf-8') + b'\0')
    return digest.hexdigest()


def _source_signature(api, source):
    folder = Path(source_folder(source))
    return {'dataset': _dataset_digest(api, source), 'sidecars': {
        name: _digest(folder / name) for name in ('项目参数.json', '地理要素.json', '地貌归并.json', '采样加密.json')
        if (folder / name).is_file()}}


def _reply(path, manifest):
    return dict(manifest, manifest=str(path), token=_digest(path))


def _copy_sidecars(source, directory, old_ids, new_ids, all_ids):
    folder, directory = Path(source_folder(source)), Path(directory)
    profile = default_path(source)
    saved, invalidated = revised_profile(load_profile(profile) if profile else {}, old_ids, new_ids)
    _atomic_json(str(directory / '项目参数.json'), saved)
    for name in ('地貌归并.json',):
        if (folder / name).is_file(): (directory / name).write_bytes((folder / name).read_bytes())
    if (folder / '地理要素.json').is_file():
        context = json.loads((folder / '地理要素.json').read_text(encoding='utf-8-sig'))
        if not isinstance(context, dict) or any(not isinstance(v, str) for v in context.values()):
            raise ValueError('地理要素配置须为角色到路径的映射')
        _atomic_json(str(directory / '地理要素.json'), {k: os.path.abspath(folder / v) for k, v in context.items()})
    if (folder / '采样加密.json').is_file():
        density = json.loads((folder / '采样加密.json').read_text(encoding='utf-8-sig'))
        if not isinstance(density, dict) or {str(k).strip() for k in density} - set(all_ids):
            raise ValueError('采样加密包含非地块编号，编辑前请将加密要求关联到地块编号')
        if len({str(k).strip() for k in density}) != len(density) or any(
                isinstance(v, bool) or not re.fullmatch(r'[0-9]+', str(v).strip()) or int(v) < 1 for v in density.values()):
            raise ValueError('采样加密点数必须为正整数，地块编号不能重复')
        density = {str(k).strip(): int(v) for k, v in density.items()}
        needed = max([density[p] for p in old_ids if p in density] or [0])
        density = {k: v for k, v in density.items() if k not in old_ids}
        if needed: density.update({p: needed for p in new_ids})
        _atomic_json(str(directory / '采样加密.json'), density)
    return invalidated


def preview(source, id_field, selected_oids, operation, out_folder, split_json=None):
    import arcpy as api
    desc = api.Describe(source)
    if desc.dataType not in ('ShapeFile', 'FeatureClass') or desc.shapeType != 'Polygon':
        raise ValueError('编辑须使用本机面数据集的完整路径')
    source = str(desc.catalogPath)
    meters = _projected_meters_per_unit(desc.spatialReference)
    selected = selection_ids(selected_oids, operation)
    fields = [f for f in api.ListFields(source) if f.editable and f.type not in ('OID', 'Geometry', 'Blob', 'Raster') and f.name not in EDIT_FIELDS]
    names = [f.name for f in fields]
    available = {f.name for f in api.ListFields(source)}
    if id_field not in available: raise ValueError('图斑编号字段不存在')
    if next(f for f in api.ListFields(source) if f.name == id_field).type == 'OID':
        raise ValueError('请指定稳定的图斑编号字段，不能使用对象ID')
    if available.intersection(EDIT_FIELDS) and not EDIT_FIELDS.issubset(available):
        raise ValueError('编辑关联字段不完整或与源字段重名')
    signature = _source_signature(api, source)
    read_fields = list(dict.fromkeys([id_field] + names + ([ORIGIN_FIELD] if ORIGIN_FIELD in available else [])))
    records, ids = [], set()
    with api.da.SearchCursor(source, ['OID@'] + read_fields + ['SHAPE@']) as cursor:
        for row in cursor:
            attrs = dict(zip(read_fields, row[1:-1]))
            if ORIGIN_FIELD in available:
                origins = json.loads(attrs.get(ORIGIN_FIELD) or 'null')
                if not isinstance(origins, list) or not origins or any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in origins) or len(set(origins)) != len(origins):
                    raise ValueError('原始地块关联不是有效编号数组，请先核对来源')
            pid = '' if attrs[id_field] is None else str(attrs[id_field]).strip()
            if not pid or pid in ids: raise ValueError('图斑编号为空或重复，请先整理')
            ids.add(pid)
            if row[0] in selected:
                geometry = row[-1]
                if geometry is None or geometry.area <= 0 or geometry.partCount != 1:
                    raise ValueError('所选图斑为空、零面积或多部件，不能编辑')
                records.append({'id': pid, 'oid': row[0], 'attrs': attrs, 'shape': geometry})
    if {r['oid'] for r in records} != selected: raise ValueError('所选图斑已变化，请重新选择')
    roles = field_mapping(names)
    conflicts = []
    for name in names:
        values = [r['attrs'][name] for r in records]
        if any(v != values[0] for v in values[1:]): conflicts.append(name)
    guarded = [roles[r] for r in tuple(FACTOR_FIELDS) + ('project',) if r in roles]
    if operation == 'merge' and set(conflicts).intersection(guarded):
        raise ValueError('所选图斑的项目或因素属性不一致，不能直接合并：' + '、'.join(set(conflicts).intersection(guarded)))
    old = records[0]['shape']
    for record in records[1:]:
        if old.intersect(record['shape'], 4).area * meters ** 2 > .01:
            raise ValueError('所选图斑存在面积重叠，请先处理')
        old = old.union(record['shape'])
    if operation == 'merge':
        if old.partCount != 1: raise ValueError('只能合并相邻图斑；不相邻图斑请使用采样合组')
        pieces = [old]
    else:
        if not split_json: raise ValueError('请绘制一条穿过图斑的分割线')
        line = api.AsShape(json.loads(split_json), True)
        if line.type != 'polyline' or line.spatialReference.factoryCode <= 0 or line.spatialReference.factoryCode != desc.spatialReference.factoryCode:
            raise ValueError('请将地图坐标系设置为图斑坐标系后画线分割')
        pieces = [g for g in old.cut(line) if g is not None and g.area > 0]
        if len(pieces) != 2 or any(g.partCount != 1 for g in pieces):
            raise ValueError('分割线须穿过边界，把一块图斑分为两块单部件图斑')
    after = pieces[0]
    for piece in pieces[1:]: after = after.union(piece)
    area_before = old.area * meters ** 2
    tolerance = max(.01, area_before * 1e-8)
    overlap = (sum(g.area for g in pieces) - after.area) * meters ** 2
    difference = old.symmetricDifference(after).area * meters ** 2
    if not all(math.isfinite(v) for v in (overlap, difference)) or overlap > tolerance or difference > tolerance:
        raise ValueError('编辑前后覆盖不一致或出现重叠，未生成候选')
    directory = Path(out_folder).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / '图斑编辑.json'
    try:
        api.management.CreateFileGDB(str(directory), 'work.gdb')
        output = str(directory / 'work.gdb' / '编辑图斑')
        api.management.CopyFeatures(source, output)
        if not available.intersection(EDIT_FIELDS):
            for name, kind, length in ((ID_FIELD, 'TEXT', 254), (ORIGIN_FIELD, 'TEXT', 4000), ('PE_EDIT', 'TEXT', 32), ('PE_M2', 'DOUBLE', None)):
                api.management.AddField(output, name, kind, field_length=length)
        # 所有单元须基于新边界重建，不能沿用部分旧正式单元。
        unit_names = {f for aliases in [OTHER_FIELDS['unit_id']] for f in aliases} | {'SAMP_GRP'}
        clear_units = [name for name in names if name.upper() in {v.upper() for v in unit_names}]
        source_link = ['SRC_DKBH'] if 'SRC_DKBH' in available else []
        cursor_fields = list(dict.fromkeys([id_field, ID_FIELD, ORIGIN_FIELD, 'PE_EDIT', 'PE_M2'] + clear_units + source_link))
        with api.da.UpdateCursor(output, cursor_fields + ['SHAPE@']) as cursor:
            for row in cursor:
                pid = str(row[cursor_fields.index(id_field)]).strip()
                row[cursor_fields.index(ID_FIELD)] = pid
                origin = row[cursor_fields.index('SRC_DKBH')] if source_link else None
                row[cursor_fields.index(ORIGIN_FIELD)] = row[cursor_fields.index(ORIGIN_FIELD)] or json.dumps([origin_id(origin, pid)], ensure_ascii=False)
                if source_link: row[cursor_fields.index('SRC_DKBH')] = pid
                row[cursor_fields.index('PE_M2')] = row[-1].getArea('GEODESIC', 'SQUAREMETERS')
                for name in clear_units: row[cursor_fields.index(name)] = None
                if pid in {r['id'] for r in records}: cursor.deleteRow()
                else: cursor.updateRow(row)
        prefix = 'M_' if operation == 'merge' else 'S_'
        edit_id = uuid.uuid4().hex
        new_ids = [prefix + edit_id[:12] + ('_' + str(i + 1) if len(pieces) > 1 else '') for i in range(len(pieces))]
        if set(new_ids).intersection(ids): raise ValueError('临时图斑编号冲突，请重试')
        origins = sorted({p for r in records for p in json.loads(r['attrs'].get(ORIGIN_FIELD) or
            json.dumps([origin_id(r['attrs'].get('SRC_DKBH'), r['id'])]))})
        origin_json = json.dumps(origins, ensure_ascii=False)
        if len(origin_json) > 4000: raise ValueError('原始地块关联过长，请分批编辑')
        area_aliases = {'AREA', 'AREA_M2', 'AREA_HA', 'AREA_MU', 'TBMJ', 'DKMJ', '面积', '平面面积', '椭球面积'}
        clear = set(conflicts) | set(clear_units) | set(OTHER_FIELDS['parcel_id']) | area_aliases
        insert_fields = names + [ID_FIELD, ORIGIN_FIELD, 'PE_EDIT', 'PE_M2', 'SHAPE@']
        with api.da.InsertCursor(output, insert_fields) as cursor:
            for pid, geometry in zip(new_ids, pieces):
                attrs = [None if name.upper() in {v.upper() for v in clear} else records[0]['attrs'][name] for name in names]
                if source_link: attrs[names.index('SRC_DKBH')] = pid
                cursor.insertRow(attrs + [pid, origin_json, edit_id, geometry.getArea('GEODESIC', 'SQUAREMETERS'), geometry])
        expected = len(ids) - len(records) + len(pieces)
        if int(api.management.GetCount(output)[0]) != expected: raise ValueError('编辑后的图斑数量不一致')
        check = str(directory / 'work.gdb' / '几何检查')
        api.management.CheckGeometry(output, check)
        if int(api.management.GetCount(check)[0]): raise ValueError('候选几何检查未通过')
        with api.da.SearchCursor(output, ['PE_EDIT', 'SHAPE@']) as cursor:
            written = [g for tag, g in cursor if tag == edit_id]
        if len(written) != len(pieces) or any(g is None or g.partCount != 1 for g in written):
            raise ValueError('候选写入后的几何不完整')
        measured = written[0]
        for geometry in written[1:]: measured = measured.union(geometry)
        difference = old.symmetricDifference(measured).area * meters ** 2
        overlap = (sum(g.area for g in written) - measured.area) * meters ** 2
        if difference > tolerance or overlap > tolerance: raise ValueError('候选读回出现覆盖或重叠差异')
        invalidated = _copy_sidecars(source, directory, [r['id'] for r in records], new_ids, ids)
        if _source_signature(api, source) != signature: raise ValueError('源资料在预览期间变化，请重新执行')
        manifest = {'status': 'PREVIEW', 'operation': operation, 'source': source, 'source_id_field': id_field,
            'source_signature': signature, 'output': output, 'id_field': ID_FIELD, 'new_ids': new_ids,
            'selected_ids': [r['id'] for r in records], 'original_ids': origins, 'count': expected,
            'candidate_signature': _source_signature(api, output), 'invalidated_rules': invalidated,
            'cleared_fields': sorted(set(names).intersection(clear) - set(source_link)),
            'relinked_fields': source_link, 'business_status': 'NEEDS_REVIEW',
            'qa': {'status': 'PASS', 'area_before_m2': area_before, 'area_after_m2': sum(g.area for g in written) * meters ** 2,
                   'coverage_difference_m2': difference, 'overlap_m2': overlap, 'numeric_tolerance_m2': tolerance}}
        _atomic_json(str(path), manifest)
        return _reply(path, manifest)
    except Exception as error:
        _atomic_json(str(path), {'status': 'FAILED', 'error': str(error)})
        raise


@contextmanager
def _locked(path):
    lock = str(path) + '.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try: yield
    finally:
        os.close(descriptor)
        os.unlink(lock)


def resolve(manifest_path, token, action, operator):
    import arcpy as api
    path = Path(manifest_path).resolve()
    if action not in ('confirm', 'cancel', 'undo', 'verify') or not isinstance(operator, str) or (action != 'verify' and not operator.strip()):
        raise ValueError('请选择确认、取消、撤销或只读核验，并填写操作人')
    if len(operator.strip()) > 100: raise ValueError('操作人不能超过100字')
    with _locked(path):
        if not token or token != _digest(path): raise ValueError('编辑候选已变化，请重新读取')
        data = json.loads(path.read_text(encoding='utf-8'))
        expected = {'PREVIEW', 'COMMITTED', 'CANCELLED', 'UNDONE'} if action == 'verify' else {'COMMITTED' if action == 'undo' else 'PREVIEW'}
        if data.get('status') not in expected: raise ValueError('当前编辑状态不支持该操作')
        if os.path.normcase(data['output']) != os.path.normcase(str(path.parent / 'work.gdb' / '编辑图斑')):
            raise ValueError('候选路径不属于当前编辑目录')
        if action != 'cancel' and data['status'] != 'CANCELLED':
            if _source_signature(api, data['source']) != data['source_signature']:
                raise ValueError('上一来源已变化，不能确认或撤销此版本')
            if _source_signature(api, data['output']) != data['candidate_signature']:
                raise ValueError('候选或项目参数已变化，请重新预览')
        if action == 'verify': return _reply(path, data)
        data['status'] = {'confirm': 'COMMITTED', 'cancel': 'CANCELLED', 'undo': 'UNDONE'}[action]
        data['operator'] = operator.strip()
        _atomic_json(str(path), data)
        return _reply(path, data)
