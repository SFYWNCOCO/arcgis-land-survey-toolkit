"""重绘已绑定任务的图件；保留设计数据，逐幅取消，成功后原子发布报告。"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
import hashlib
import time
from . import task_state as T


def _inside(path, root):
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)


def _read(path):
    with open(path, encoding='utf-8') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError('任务文件必须是JSON对象')
    return value


def _check_design(state, report):
    import arcpy
    from .point_adjustment import design_signature
    phases = state['phases']
    signature = design_signature(phases['data_prepared']['detail'], phases['sampling_prepared']['detail'], arcpy)
    if report.get('设计指纹') != signature:
        raise ValueError('单元或点位已改变，请重新生成预览')


def _resize_target(target, denominator, options, directory, key, project_path, map_name):
    from .sheet_plan import apply_map_scales, map_scale_key
    from .map_layout_style import visual_layout_spec
    import arcpy
    from . import imagery as I
    target = copy.deepcopy(target)
    auto = target.get('automatic_scale', target['scale'])
    target['scale'] = auto
    scale_key = map_scale_key(target)
    frame = visual_layout_spec(target['kind'])['map_frame']
    raster = target.get('imagery_path')
    if not raster or not arcpy.Exists(raster):
        raise ValueError('当前图件缺少有效底图，请先生成预览')
    project = arcpy.mp.ArcGISProject(project_path)
    maps = project.listMaps()
    mapping = next((m for m in maps if m.name == map_name), maps[0])
    sr = arcpy.SpatialReference()
    sr.loadFromString(mapping.spatialReference.exportToString())
    del mapping, maps, project
    changed = apply_map_scales([target], {scale_key: auto if denominator == 'auto' else float(denominator)},
                               frame['width'], frame['height'], sr.metersPerUnit)[0]
    b = changed['frame_extent']
    polygon = arcpy.Polygon(arcpy.Array([arcpy.Point(b[0],b[1]),arcpy.Point(b[2],b[1]),
        arcpy.Point(b[2],b[3]),arcpy.Point(b[0],b[3])]),sr)
    image_sr = arcpy.Describe(raster).spatialReference
    footprint = polygon.projectAs(image_sr) if sr.exportToString()!=image_sr.exportToString() else polygon
    quality = I.inspect_raster_coverage(raster, footprint.WKT)
    if quality.get('coverage_status') != 'PASS':
        choices = options.get('unit_imagery_sources') or {}
        configured = [choices[uid] for uid in changed.get('unit_ids',[]) if uid in choices]
        if configured and any(choice != configured[0] for choice in configured):
            raise ValueError('此图幅使用多份影像且新图框覆盖不足，请先补充统一影像再生成预览')
        choice = configured[0] if configured else {}
        source = choice.get('raster') or options.get('imagery_raster')
        provider = choice.get('provider') or options.get('imagery_provider', 'demo')
        if changed['kind'] in ('geo','index') and not source and provider in ('tianditu','tianditu_vector'):
            provider = 'tianditu_vector'
        record = I.prepare_imagery(directory, sr.factoryCode, extent=arcpy.Extent(*b), raster=source,
            provider=provider, tk=key, max_tiles=600, max_zoom=19, arcpy=arcpy, label='rescaled',
            labels=provider == 'tianditu_vector')
        if not record.get('output'):
            raise ValueError('新比例尺所需底图未取得，请检查影像源或授权')
        raster = record['output']
        image_sr = arcpy.Describe(raster).spatialReference
        footprint = polygon.projectAs(image_sr) if sr.exportToString()!=image_sr.exportToString() else polygon
        quality = I.inspect_raster_coverage(raster, footprint.WKT)
        if quality.get('coverage_status') != 'PASS':
            raise ValueError('新比例尺下底图未覆盖完整图框，请补充影像或调大图面比例')
        from .map_worker import _imagery_quality
        changed['imagery_quality'] = _imagery_quality(record)
        changed['imagery_text'] = I.imagery_caption(record)
    changed['imagery_path'] = raster
    changed.setdefault('imagery_quality', {})['frame_coverage'] = quality
    return changed


def redraw(task_root, target_name=None, log=None, scale=None, service_key=None):
    root = os.path.abspath(os.fspath(task_root))
    state_path = os.path.join(root, T.STATE_NAME)
    prior = _read(state_path)
    original = copy.deepcopy(prior)
    if prior.get('status') not in ('PREPARED', 'COMPLETED', 'NEEDS_CORRECTION'):
        raise ValueError('任务正在运行或没有完整报告，不能重绘')
    report_path = prior.get('report_path')
    if not report_path or not _inside(report_path, root):
        raise ValueError('任务报告不属于当前成果目录')
    report = _read(report_path)
    digest = prior.get('input_fingerprint')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest) or not prior.get('run_id') or \
            report.get('任务运行ID') != prior['run_id'] or report.get('输入指纹') != digest or \
            report.get('设计修订号') != prior.get('design_revision'):
        raise ValueError('报告与任务的运行、输入或设计修订不一致')
    _check_design(prior, report)
    exported = report.get('出图') or {}
    result_path = exported.get('result_path') or ''
    plan_path = exported.get('plan_path')
    if not plan_path:
        if not result_path.endswith('.result.json'):
            raise ValueError('报告缺少有效渲染计划路径')
        plan_path = result_path[:-len('.result.json')] + '.json'
    if not _inside(plan_path, root):
        raise ValueError('渲染计划不属于当前成果目录')
    plan = _read(plan_path)
    if not plan.get('project') or not _inside(plan['project'], root) or not os.path.isfile(plan['project']):
        raise ValueError('任务制图工程缺失或不属于当前成果目录')
    targets = plan.get('targets') or []
    names = [t['name'] for t in targets]
    if len(set(names)) != len(names) or not names or (target_name and target_name not in names):
        raise ValueError('图幅名称无效或不属于当前计划')
    previous = {row['target']: row for row in exported.get('outputs', [])}
    if set(previous) != set(names):
        raise ValueError('图幅清单与计划不一致，请重新生成预览')
    selected = {target_name} if target_name else set(names)
    if scale is not None and not target_name:
        raise ValueError('请先选定单幅图再调整比例尺')
    directory = os.path.join(root, '重绘图件', uuid.uuid4().hex[:12])
    os.makedirs(directory, exist_ok=False)
    state = copy.deepcopy(prior)
    state.update(status='RUNNING', pid=os.getpid(), process_created=T._process_identity(os.getpid())[1],
                 current_phase='maps_exported', updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    marker = os.path.join(root, T.CANCEL_NAME)
    if os.path.isfile(marker): os.remove(marker)
    T._atomic_json(state_path, state)
    try:
        plan = copy.deepcopy(plan)
        if scale is not None:
            index = names.index(target_name)
            plan['targets'][index] = _resize_target(plan['targets'][index], scale,
                state['input_details']['options'], os.path.join(directory, 'imagery'), service_key, plan['project'], plan.get('map_name'))
            selected_target = plan['targets'][index]
            rules = state['input_details']['options'].setdefault('meta', {}).setdefault('project_rules', {})
            scales = rules.setdefault('map_scales', {})
            if scale == 'auto': scales.pop(selected_target['scale_key'], None)
            else: scales[selected_target['scale_key']] = selected_target['scale']
            digest = hashlib.sha256(json.dumps(state['input_details'], ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), default=str).encode('utf-8')).hexdigest()
            state['input_fingerprint'] = report['输入指纹'] = digest
            report['项目设计规则'] = copy.deepcopy(rules)
            report.setdefault('项目信息', {}).setdefault('project_rules', {})['map_scales'] = dict(scales)
            plan.setdefault('meta', {}).setdefault('project_rules', {})['map_scales'] = dict(scales)
            production_source = state['phases']['maps_built']['detail']['plan']
            if not _inside(production_source, root):
                raise ValueError('正式渲染计划不属于当前任务')
            production = _read(production_source)
            formal_dir = os.path.join(directory, '正式图件')
            os.makedirs(formal_dir)
            updates = {target['name']: target for target in plan['targets']}
            for target in production['targets']:
                resized = updates[target['name']]
                for field in ('scale','scale_key','automatic_scale','frame_extent','imagery_path','imagery_quality','imagery_text','scale_recommendation'):
                    if field in resized: target[field] = copy.deepcopy(resized[field])
                for ext in ('jpg','png','pdf'): target[ext] = os.path.join(formal_dir,os.path.basename(target[ext]))
            production.setdefault('meta', {}).setdefault('project_rules', {})['map_scales'] = dict(scales)
            production_path = os.path.join(directory,'production_plan.json')
            T._atomic_json(production_path,production)
            state['phases']['maps_built']['detail'].update(plan=production_path,map_dir=formal_dir)
        for target in plan['targets']:
            for ext in ('jpg', 'png', 'pdf'):
                target[ext] = (os.path.join(directory, os.path.basename(target[ext]))
                               if target['name'] in selected else previous[target['name']][ext])
        full_plan = os.path.join(directory, 'render_plan.json')
        T._atomic_json(full_plan, plan)
        work_plan = dict(plan, targets=[t for t in plan['targets'] if t['name'] in selected])
        work_path = os.path.join(directory, 'selected_plan.json')
        T._atomic_json(work_path, work_plan)
        from .map_export import export_all
        from .workflow import refresh_visual_reviews, refresh_report_technical_status
        from .map_validation import write_atlas_bundle
        def checkpoint(name, index, total):
            T.check_cancel(root, state)
            state['current_map'] = {'name': name, 'index': index, 'total': total}
            T._atomic_json(state_path, state)
            if log: log('重绘 {}/{}：{}'.format(index, total, name))
        if len(work_plan['targets']) == 1:
            checkpoint(work_plan['targets'][0]['name'], 1, 1)
        result = export_all(work_path, checkpoint=checkpoint)
        T.check_cancel(root, state)
        if len(result.get('outputs', [])) != len(selected) or any(row.get('status') == 'FAIL' for row in result['outputs']):
            raise RuntimeError('重绘存在失败图幅；原报告和设计数据保留')
        updated = {row['target']: row for row in result['outputs']}
        result.update(outputs=[updated.get(name, previous[name]) for name in names], draft=True,
                      plan_path=full_plan, result_path=os.path.join(directory, 'render_plan.result.json'))
        report['视觉复核'] = refresh_visual_reviews(result)
        result['bundle'] = write_atlas_bundle(result, directory)
        T.check_cancel(root, state)
        report['出图'] = result
        refresh_report_technical_status(report)
        current_targets = {target['name']: target for target in plan['targets']}
        for field in ('自动图册', '主分幅'):
            for row in report.get(field, []):
                target = current_targets.get(row.get('name'))
                if target:
                    for key in ('scale', 'scale_key', 'automatic_scale', 'frame_extent'):
                        if key in target: row[key] = copy.deepcopy(target[key])
        report.pop('设计确认', None)
        new_report = os.path.join(directory, '一键成果报告.json')
        report['成果报告路径'] = new_report
        report['图件重绘时间'] = time.strftime('%Y-%m-%d %H:%M:%S')
        from .triage import write_summary
        report.update(write_summary(report, directory))
        T._atomic_json(result['result_path'], result)
        T._atomic_json(new_report, report)
        state.pop('current_map', None)
        state['history'].append({'event': '重绘图件', 'targets': sorted(selected), 'report': new_report})
        state['phases']['maps_exported']['detail'] = result
        T.finish_task(root, state, 'PREPARED', report_path=new_report)
        return {'report_path': new_report, 'out_dir': directory, 'redrawn': len(selected), 'maps': len(names)}
    except T.TaskCancelled:
        cancelled = copy.deepcopy(original)
        cancelled.update(status='CANCELLED', current_phase='maps_exported',
                         updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        cancelled.setdefault('history', []).append({'event': '用户停止重绘，保留原报告与设计'})
        T._atomic_json(state_path, cancelled)
        raise
    except Exception as exc:
        original.setdefault('history', []).append({'event': '重绘失败，保留原报告', 'error': str(exc)})
        T._atomic_json(state_path, original)
        raise
