"""可恢复的一键采样阶段执行器。每阶段保存可读回的产物上下文。"""
from __future__ import annotations

import copy
import csv
import json
import os
import subprocess
import time

from . import grid_sampling as G
from . import imagery as I
from . import task_state as T
from . import unit_builder as U
from . import sampling_qa as QA
from .map_validation import verify_export_artifact, validate_visual_review
from . import point_adjustment as E
from .profile import source_folder


def elevation_gap(meta):
    """数据缺口里的高程基准：有值就如实写出，未核实的注明状态。

    此前固定写「未提供」，与项目参数 meta.elevation_datum 及图面标注矛盾；
    没有资料时仍返回「未提供」，不臆造值。
    """
    meta = meta or {}
    value = str(meta.get("elevation_datum") or "").strip()
    if not value:
        return "未提供"
    status = str(meta.get("elevation_datum_status") or "").strip()
    if not status:
        return value
    if "UNVERIFIED" in status.upper():
        return "{}（已填但未核实）".format(value)
    return "{}（{}）".format(value, status)


def refresh_visual_reviews(exported):
    """Re-read sidecar evidence even when the completed render phase is reused."""
    states, visual_states, technical_states, rows = [], [], [], []
    for output in exported.get('outputs', []):
        review = validate_visual_review(output.get('jpg') or '', output.get('render_key'))
        output['visual_review'] = review
        checks = [row for row in output.get('checks',[]) if row.get('item') != '视觉复核']
        technical = {row.get('status', 'FAIL') for row in checks}
        technical_states.append('FAIL' if 'FAIL' in technical else 'NEEDS_REVIEW' if not technical or 'NEEDS_REVIEW' in technical else 'PASS')
        checks.append({'item':'视觉复核','status':review['status'],'detail':review['detail']})
        output['checks'] = checks
        statuses = {row.get('status','FAIL') for row in checks}
        output['status'] = 'FAIL' if 'FAIL' in statuses else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in statuses else 'PASS'
        states.append(output['status'])
        visual_states.append(review['status'])
        rows.append({'target':output.get('target'),'status':review['status']})
    status = 'FAIL' if 'FAIL' in states else 'NEEDS_REVIEW' if not states or 'NEEDS_REVIEW' in states else 'PASS'
    exported['status'] = status
    exported['technical_status'] = 'FAIL' if 'FAIL' in technical_states else 'NEEDS_REVIEW' if not technical_states or 'NEEDS_REVIEW' in technical_states else 'PASS'
    visual_status = 'FAIL' if 'FAIL' in visual_states else 'NEEDS_REVIEW' if not visual_states or 'NEEDS_REVIEW' in visual_states else 'PASS'
    return {'status':visual_status,'maps':rows,'scope':'视觉复核状态独立于规范验收；图像或渲染改变后须重审'}


def refresh_report_technical_status(report):
    """Recompute map failures without clearing sampling, forms, grid or source-image failures."""
    required = ('网格单元明细', '容重候选', '外业表格', '影像逐单元', '项目自定义设计核对')
    if not all(key in report for key in required):
        base = 'FAIL' if report.get('技术状态') == 'FAIL' else 'NEEDS_REVIEW'
    else:
        overrides = ((report.get('项目设计规则') or {}).get('point_counts_by_unit') or {})
        failed = any(not row['达标'] and str(row['评价单元']) not in overrides for row in report['网格单元明细'])
        failed |= report['项目自定义设计核对'].get('technical_status') == 'FAIL'
        failed |= (report['容重候选'].get('qa') or {}).get('status') == 'FAIL'
        failed |= report['外业表格'].get('状态') == 'FAIL'
        failed |= any(record.get('coverage_status') == 'FAIL' for record in report['影像逐单元'].values())
        failed |= (report.get('网格几何核验') or {}).get('status') == 'FAIL'
        base = 'FAIL' if failed else 'PASS'
    maps = (report.get('出图') or {}).get('technical_status', 'NEEDS_REVIEW')
    report['非图件技术状态'] = base
    report['技术状态'] = 'FAIL' if 'FAIL' in (base, maps) else 'NEEDS_REVIEW' if 'NEEDS_REVIEW' in (base, maps) else 'PASS'
    report['状态'] = 'FAIL' if report['技术状态'] == 'FAIL' else 'NEEDS_REVIEW'
    return report['技术状态']


def _fresh_directory(parent, stem):
    candidate = os.path.join(parent, stem)
    suffix = 1
    while os.path.exists(candidate):
        suffix += 1
        candidate = os.path.join(parent, "{}_{:02d}".format(stem, suffix))
    os.makedirs(candidate)
    return candidate


def _pad_bounds(bounds, meters=200.0):
    """影像获取范围外扩：保证 1:2000 图面四周仍有影像覆盖，不留白边。"""
    xmin, ymin, xmax, ymax = bounds
    return [xmin - meters, ymin - meters, xmax + meters, ymax + meters]


CONTEXT_ROLES = ("居民点", "道路", "水系", "境界线")
INSET_TEMPLATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates",
                                              "补充耕地鉴定图_A4_区位.pagx"))
COUNTY_GEOJSON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                              "location_map", "data", "gx_county.geojson"))


def _load_context_layers(parcel_fc, say):
    """读取源图层同目录的“地理要素.json”，把必要地物加入图面；缺项不臆造。"""
    folder = source_folder(parcel_fc)
    path = os.path.join(folder, "地理要素.json")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig") as stream:
        spec = json.load(stream)
    layers = []
    for role, value in (spec or {}).items():
        if role not in CONTEXT_ROLES:
            say("地理要素.json 忽略未知角色：{}".format(role))
            continue
        target = value if os.path.isabs(value) else os.path.join(folder, value)
        if not os.path.exists(target):
            raise FileNotFoundError("地理要素 {} 不存在：{}".format(role, target))
        layers.append({"role": role, "path": os.path.abspath(target), "name": role})
    return layers


def _load_density(parcel_fc, say):
    """读取源目录“采样加密.json”：{单元编号或图斑编号: 最低点数}。

    规范只要求“点数按面积、地块数量、地形、客土来源确定”，没有密度表；
    这里只接受人工给定的点数，不自造系数。
    """
    folder = source_folder(parcel_fc)
    path = os.path.join(folder, "采样加密.json")
    if not os.path.isfile(path):
        return {}, None
    with open(path, encoding="utf-8-sig") as stream:
        spec = json.load(stream)
    if not isinstance(spec, dict):
        raise ValueError("采样加密.json 必须是对象：{单元或图斑编号: 点数}")
    result = {}
    for key, value in spec.items():
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("采样加密 {} 的点数不是整数：{}".format(key, value)) from None
        if number < 1:
            raise ValueError("采样加密 {} 的点数必须为正：{}".format(key, value))
        result[str(key)] = number
    say("采样加密：{} 项（{}）".format(len(result), path))
    return result, path


def _load_terrain_mapping(parcel_fc, factor_sources):
    """地貌二级单元 → 12 类归并表：优先因素映射里指定，其次源目录 地貌归并.json。"""
    from .terrain import load_mapping
    path = ((factor_sources or {}).get("landform") or {}).get("mapping")
    if not path:
        folder = source_folder(parcel_fc)
        candidate = os.path.join(folder, "地貌归并.json")
        path = candidate if os.path.isfile(candidate) else None
    if not path:
        return {}, None
    return load_mapping(path), os.path.abspath(path)


def _field_has_values(arcpy, source, field):
    """判断字段是否有任一非空值；全空视为“没有已有单元”。"""
    with arcpy.da.SearchCursor(str(source), [field]) as cursor:
        for (value,) in cursor:
            if value is not None and str(value).strip():
                return True
    return False


def _attach_summary(report, out_dir, say):
    """生成人读摘要；失败只记录，不阻断主流程。"""
    from . import triage
    try:
        report.update(triage.write_summary(report, out_dir))
    except Exception as exc:  # noqa: BLE001 - 摘要失败不应拖死出图
        report["运行摘要状态"] = "FAIL"
        report["运行摘要错误"] = "{}: {}".format(type(exc).__name__, exc)
        say("运行摘要生成失败：{}".format(report["运行摘要错误"]))


def _atlas_plan(arcpy, data, id_field, meters_per_unit, project_rules=None):
    from .sheet_plan import plan_atlas
    from .map_layout_style import visual_layout_spec
    source_field = data.get("source_id_field") or data["assign_id"]
    links = {}
    with arcpy.da.SearchCursor(data["assign"], [source_field, data["assign_id"], data["unit_field"]]) as cursor:
        for source, part, unit in cursor:
            row = links.setdefault(str(source), {"parcel_ids": set(), "unit_ids": set()})
            row["parcel_ids"].add(str(part))
            row["unit_ids"].add(str(unit))
    rows = []
    with arcpy.da.SearchCursor(data["parcels"], [id_field, "SHAPE@"]) as cursor:
        for pid, geometry in cursor:
            if str(pid) not in links:
                raise ValueError("原始地块缺少采样关联：" + str(pid))
            rows.append(dict(links[str(pid)], parcel=str(pid),
                             bbox_native=[geometry.extent.XMin, geometry.extent.YMin,
                                          geometry.extent.XMax, geometry.extent.YMax],
                             mu=geometry.getArea("GEODESIC", "SQUAREMETERS") / (2000.0 / 3)))
    frame = visual_layout_spec("sample")["map_frame"]
    return plan_atlas(rows, frame["width"], frame["height"], meters_per_unit, project_rules=project_rules)


def _preview_render_plan(plan, directory):
    preview = copy.deepcopy(plan)
    for target in preview["targets"]:
        target["title"] = "【设计预览】" + target["title"]
        target["preview_only"] = True
        for ext in ("png", "jpg", "pdf"):
            target[ext] = os.path.join(directory, os.path.basename(target[ext]))
    return preview


def execute(parcel_fc, out_dir, id_field=None, unit_field=None, factor_fields=(),
            max_mu=50.0, gap_m=30.0, allow_multipart=False,
            cell_size_m=None, min_points=3, min_spacing_m=15.0, inset_m=5.0,
            imagery_raster=None, imagery_provider="demo", tianditu_key=None,
            dpi=300, meta=None, make_maps=True, template=None, keep_tiles=False,
            scale=None, log=None, selection_oids=None, source_where_clause=None, checkpoint=None, resume_state=None,
            factor_sources=None, project_number=None, prepare_only=False, unit_imagery_sources=None,
            profile_path=None, sampling_groups=(), draft_maps=False):
    import arcpy
    from . import oneclick as O
    started = time.time()
    say = log or (lambda message: print(message, flush=True))
    parcel_fc, out_dir = str(parcel_fc), os.path.abspath(out_dir)
    from .profile import project_rules as validate_project_rules
    project_rules = validate_project_rules(meta)
    context_layers = _load_context_layers(parcel_fc, say)
    density, density_path = _load_density(parcel_fc, say)
    desc = arcpy.Describe(parcel_fc)
    sr = desc.spatialReference
    O._projected_meters_per_unit(sr)
    fields = O.detect_fields(parcel_fc)
    id_field = id_field or fields["id"]
    explicit_unit_field = bool(unit_field)
    unit_field = unit_field or fields["unit"]
    factor_fields = list(factor_fields or fields["factors"])
    if not id_field:
        raise ValueError("无法识别图斑编号字段，请指定编号字段。")
    if unit_field and not explicit_unit_field and not _field_has_values(arcpy, parcel_fc, unit_field):
        say("自动识别的评价单元字段 {} 全为空，按无已有单元处理并生成候选。".format(unit_field))
        unit_field = None
    state = resume_state or {}
    phases = state.get("phases", {})

    def stage(name, operation, paths=(), accepted=None):
        prior = phases.get(name) or {}
        saved = prior.get("detail")
        reusable = prior.get("status") == "COMPLETED" and isinstance(saved, dict)
        if reusable:
            reusable = all(saved.get(key) and (arcpy.Exists(saved[key]) or os.path.exists(saved[key])) for key in paths)
        if reusable and accepted:
            reusable = bool(accepted(saved))
        if reusable:
            say("复用已完成阶段：{}".format(name))
            if checkpoint:
                checkpoint(name, saved)
            return saved
        if prior.get("status") == "COMPLETED":
            for downstream in T.PHASES[T.PHASES.index(name) + 1:]:
                phases[downstream] = {"status": "PENDING", "invalidated": "upstream artifact unavailable"}
        if state.get("state_root"):
            T.start_phase(state["state_root"], state, name)
        say("开始阶段：{}".format(name))
        result = operation()
        if checkpoint:
            checkpoint(name, result)
        return result

    def prepare_data():
        folder = _fresh_directory(os.path.join(out_dir, "db"), "data")
        arcpy.management.CreateFileGDB(folder, "资料.gdb")
        gdb = os.path.join(folder, "资料.gdb")
        parcels = os.path.join(gdb, "图斑")
        where, selected_count = O._selection_where(arcpy, parcel_fc, selection_oids)
        if source_where_clause:
            where = "({}) AND ({})".format(where, source_where_clause) if where else source_where_clause
        arcpy.conversion.FeatureClassToFeatureClass(parcel_fc, gdb, "图斑", where or "")
        count = int(arcpy.management.GetCount(parcels)[0])
        if count == 0 or (selected_count and not source_where_clause and count != selected_count):
            raise RuntimeError("图斑复制数量不匹配：选择 {}，实际 {}".format(selected_count, count))
        excluded = set(project_rules.get('excluded_parcels', []))
        removed = []
        if excluded:
            with arcpy.da.UpdateCursor(parcels, [id_field]) as cursor:
                for row in cursor:
                    if str(row[0]) in excluded:
                        removed.append(str(row[0]))
                        cursor.deleteRow()
            count = int(arcpy.management.GetCount(parcels)[0])
            if not count:
                raise ValueError('按项目排除规则处理后没有地块；请检查本次选择范围')
        overlay, working, working_id = None, parcels, id_field
        if factor_sources:
            from .factor_overlay import overlay_factors
            terrain_mapping, terrain_path = _load_terrain_mapping(parcel_fc, factor_sources)
            overlay = overlay_factors(parcels, id_field, gdb, factor_sources,
                                      terrain_mapping=terrain_mapping)
            overlay["terrain_mapping_file"] = terrain_path
            working, working_id = overlay["output"], overlay["id_field"]
        if sampling_groups:
            if factor_sources:
                raise ValueError("人工采样组使用原始地块关联，请先复核因素叠加结果再分组")
            from .unit_review import apply_sampling_groups
            # This is the newly-created work copy: recompute an old design association
            # when the user starts another task from a previously prepared layer.
            if "SAMP_GRP" in [f.name for f in arcpy.ListFields(parcels)]:
                arcpy.management.DeleteField(parcels, "SAMP_GRP")
            group_field, group_plan = apply_sampling_groups(arcpy, parcels, id_field, sampling_groups,
                                                           max_mu=max_mu, gap_m=gap_m, factor_fields=factor_fields)
            rows, review = O._existing_unit_rows(arcpy, parcels, group_field, id_field, max_mu=max_mu,
                qa_path=os.path.join(folder, '人工采样组检查.json'), return_qa=True)
            result = {"assign": parcels, "units_fc": None, "unit_field": group_field,
                      "assign_id": id_field, "unit_rows": rows, "unit_count": len(rows),
                      "existing_unit_qa": review, "sampling_group_plan": group_plan,
                      "unit_design": {"kind": "manual_sampling_groups", "status": "NEEDS_REVIEW",
                                      "missing_factors": sorted({item["factor"] for item in review["issues"] if item.get("code") == "missing_factor"})}}
        elif unit_field and not factor_sources:
            rows, review = O._existing_unit_rows(arcpy, parcels, unit_field, id_field, max_mu=max_mu,
                qa_path=os.path.join(folder, '已有单元检查.json'), return_qa=True)
            result = {"assign": parcels, "units_fc": None, "unit_field": unit_field,
                      "assign_id": id_field, "unit_rows": rows, "unit_count": len(rows), 'existing_unit_qa': review}
        else:
            built = U.build_units(working, working_id, gdb, factor_fields=factor_fields,
                                  max_mu=max_mu, gap_m=gap_m, allow_multipart=allow_multipart,
                                  min_points=min_points, min_spacing_m=min_spacing_m, inset_m=inset_m)
            result = {"assign": built["assign_fc"], "units_fc": built["unit_fc"],
                      "source_id_field": built["source_id_field"],
                      "unit_field": "PJDYBH", "assign_id": "DKBH", "unit_count": built["units"],
                      "unit_rows": [{"unit": row["单元"], "mu": row["面积亩"], "parcels": row["图斑"],
                                     "project": row["项目"], "area_m2": row.get("面积平方米")}
                                    for row in built["detail"]], "unit_design": built}
        result.update({"gdb": gdb, "parcels": parcels, "count": count, "selected_count": selected_count,
                       "source_count": int(arcpy.management.GetCount(parcel_fc)[0]),
                       "source_where_clause": source_where_clause, "factor_overlay": overlay,
                       "project_exclusions": {'requested':sorted(excluded),'removed':sorted(removed)}})
        from .unit_review import plan_unit_numbers, apply_unit_numbers
        numbering = plan_unit_numbers(result["unit_rows"], project_number)
        if sampling_groups:
            numbering = {"mapping": {}, "missing_project_units": [row["unit"] for row in result["unit_rows"]],
                         "sample_ids": {}, "status": "NEEDS_REVIEW", "note": "人工采样组不自动生成正式评价单元编号。"}
        apply_unit_numbers(result, numbering, arcpy)
        result["numbering"] = numbering
        if result.get('existing_unit_qa') and numbering['mapping']:
            from .existing_unit_qa import check_existing_units
            result['existing_unit_qa'] = check_existing_units(arcpy, result['assign'], result['unit_field'],
                result['assign_id'], max_mu=max_mu, output=result['existing_unit_qa']['path'])
        return result

    def accept_data(saved):
        if not saved.get('existing_unit_qa'):
            return True
        from .existing_unit_qa import check_existing_units
        review = saved['existing_unit_qa']
        if not os.path.isfile(review.get('path', '')):
            return False
        current = check_existing_units(arcpy, saved['assign'], saved['unit_field'], saved['assign_id'], max_mu=max_mu)
        return current['status'] != 'FAIL' and current['content_fingerprint'] == review.get('content_fingerprint')

    data = stage("data_prepared", prepare_data, paths=("gdb", "parcels", "assign"), accepted=accept_data)

    def prepare_sampling():
        folder = _fresh_directory(os.path.join(out_dir, "db"), "sampling")
        arcpy.management.CreateFileGDB(folder, "采样成果.gdb")
        gdb = os.path.join(folder, "采样成果.gdb")
        point_minimums = dict(density)
        for group in (data.get("sampling_group_plan") or {}).get("groups", []):
            gid = group["group_id"]
            point_minimums[gid] = max(group["min_points"], G.unit_min_points(
                gid, [{"parcel": pid} for pid in group["parcels"]], density, min_points))
        grid = G.grid_design(data["assign"], data["unit_field"], data["assign_id"], gdb,
                             cell_size_m=cell_size_m, min_points=min_points,
                             min_spacing_m=min_spacing_m, inset_m=inset_m,
                             min_points_by_unit=point_minimums)
        units = data["units_fc"] or O._dissolve_units(arcpy, gdb, data["assign"], data["unit_field"])
        custom_points = E.apply_project_point_counts(arcpy, grid['sample'], units, data['unit_field'],
                                                     project_rules.get('point_counts_by_unit', {}))
        if custom_points['status'] != 'NOT_REQUESTED':
            actual, parcel_points = {}, set()
            with arcpy.da.SearchCursor(grid['sample'], ['PJDYBH','DKBH']) as cursor:
                for uid,pid in cursor:
                    actual[str(uid)] = actual.get(str(uid),0)+1
                    parcel_points.add((str(uid),str(pid)))
            grid['counts']['设计点'] = sum(actual.values())
            for row in grid['units']:
                uid=str(row['评价单元'])
                row['设计点数']=actual.get(uid,0)
                row['覆盖缺失图斑']=[str(pid) for item in data['unit_rows'] if str(item['unit'])==uid
                                      for pid in item['parcels'] if (uid,str(pid)) not in parcel_points]
                row['达标']=row['设计点数']>=max(row['最低点数'],row['图斑数']) and not row['覆盖缺失图斑']
                if uid in project_rules.get('point_counts_by_unit',{}):
                    row['项目自定义点数']=project_rules['point_counts_by_unit'][uid]
        G.refresh_grid_statistics(arcpy, grid, units, data['unit_field'], data['assign'], data['assign_id'])
        shp_dir = _fresh_directory(out_dir, "shp")
        shapefiles = G.export_shapefiles(arcpy, grid, shp_dir)
        rows = copy.deepcopy(data["unit_rows"])
        landtypes = O._landtype_lookup(arcpy, data["parcels"], id_field)
        for row in rows:
            row["landtype"] = "、".join(sorted({landtypes.get(str(pid), "") for pid in row["parcels"]} - {""}))
            if not row.get("mu"):
                row["mu"] = O._unit_area(arcpy, units, data["unit_field"], row["unit"])
            row["bbox_native"] = O._extent_list(O._unit_extent(arcpy, units, data["unit_field"], row["unit"]))
        qa_path = os.path.join(folder, "独立采样质检.json")
        independent_qa = QA.check_composite_design(
            data["assign"], units, grid["sample"], data["unit_field"], data["assign_id"], data["unit_field"],
            "PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID", "DESIGN_ST", qa_path, arcpy_module=arcpy)
        from .bulk_design import prepare_bulk_candidates
        bulk_design = prepare_bulk_candidates(units, data["unit_field"], grid["center"], gdb,
                                              parcel_layer=data["assign"],
                                              parcel_unit_field=data["unit_field"])
        point_csv = os.path.join(folder, "采样点清单.csv")
        E.write_point_csv(point_csv, grid["sample"], arcpy)
        label_csv = os.path.join(folder, "样品标签预填.csv")
        with open(label_csv, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["候选评价单元", "混合样号预填", "容重样号预填", "采样日期", "采样人", "审核状态"])
            for row in rows:
                uid = str(row["unit"])
                writer.writerow([uid, uid, "RZ" + uid, "", "", "待确认；不是实采记录"])
        prepared = {"gdb": gdb, "grid": grid, "units_for_map": units, "unit_rows": rows, "shp_dir": shp_dir,
                "shapefiles": shapefiles,
                "bulk_design": bulk_design,
                "custom_point_design": custom_points,
                "independent_qa": independent_qa, "qa_path": qa_path, "point_csv": point_csv,
                "label_csv": label_csv}
        prepared["design_signature"] = E.design_signature(data, prepared, arcpy)
        return prepared

    def accept_sampling(saved):
        try:
            saved['grid']['geometry_check'] = G.verify_grid_geometry(arcpy, saved['grid'], cell_size_m)
        except Exception as exc:
            say('网格实际几何无法核实，重新生成：{}'.format(exc))
            return False
        if saved['grid']['geometry_check']['status'] != 'PASS':
            say('已有网格与当前格距、格心或节点不符，重新生成。')
            return False
        if not all(arcpy.Exists(saved["grid"].get(key, "")) for key in ("grid", "node", "center", "sample")):
            return False
        bulk = saved.get("bulk_design") or {}
        if not arcpy.Exists(bulk.get("profiles", "")) or (bulk.get("output") and not arcpy.Exists(bulk["output"])):
            return False
        if saved.get("design_signature") != E.design_signature(data, saved, arcpy):
            E.refresh_sampling(data, saved, arcpy)
            for downstream in ("maps_built", "maps_exported", "completed"):
                phases[downstream] = {"status": "PENDING", "invalidated": "design_changed"}
        try:
            valid_shp = all(G.verify_shapefile_identifiers(arcpy, saved['grid'][key],
                saved.get('shapefiles', {}).get(key, ''))['status'] == 'PASS' for key in ('grid', 'node', 'center', 'sample'))
        except Exception:
            valid_shp = False
        if not valid_shp:
            shp_dir = _fresh_directory(out_dir, 'shp')
            saved['shapefiles'] = G.export_shapefiles(arcpy, saved['grid'], shp_dir)
            saved['shp_dir'] = shp_dir
            say('SHP成果缺失、损坏或编号不一致，已从当前设计另存到新目录；原文件保留。')
        return True

    sampling = stage("sampling_prepared", prepare_sampling, paths=("gdb", "units_for_map", "shp_dir", "qa_path", "point_csv", "label_csv"),
                     accepted=accept_sampling)

    def prepare_forms():
        from . import forms
        context = {"单元明细": sampling["unit_rows"], "网格单元明细": sampling["grid"]["units"],
                   "点位清单": sampling["point_csv"], "项目信息": meta or {},
                   "成果报告路径": os.path.join(out_dir, "一键成果报告.json")}
        manifest = forms.build_forms(context, out_dir, meta=meta)
        if manifest.get("清单") and os.path.isfile(manifest["清单"]):
            manifest["HTML"] = forms.write_manifest_html(
                manifest, os.path.join(out_dir, "外业表格清单.html"))
        return manifest

    def accept_forms(saved):
        from .forms import outputs_available
        return outputs_available(saved)

    forms_result = stage("forms_prepared", prepare_forms, paths=("清单",), accepted=accept_forms)

    atlas_targets = _atlas_plan(arcpy, data, id_field, sr.metersPerUnit, project_rules=project_rules) if make_maps else []
    if atlas_targets:
        say("自动图册：{}张位置图、{}张逐地块采点图、{}张总览；比例尺和图框已规划。".format(
            sum(t["kind"] == "geo" for t in atlas_targets), sum(t["kind"] == "sample" for t in atlas_targets),
            sum(t["kind"] == "index" for t in atlas_targets)))

    def prepare_images():
        directory = _fresh_directory(out_dir, "imagery")
        cache_dir = (os.path.join(os.path.dirname(os.path.abspath(profile_path)), '影像缓存')
                     if profile_path else os.path.join(state.get("state_root") or out_dir, "tile_cache"))
        location_extent = arcpy.Extent(*_pad_bounds(O._extent_list(arcpy.Describe(sampling["units_for_map"]).extent), 800.0))
        choices = unit_imagery_sources or {}
        unit_ids = {str(row["unit"]) for row in sampling["unit_rows"]}
        if set(choices) - unit_ids:
            raise ValueError("逐单元影像选择含未知单元，请按当前预览编号重新选择：" + ",".join(sorted(set(choices) - unit_ids)))
        if unit_ids <= set(choices):
            overview = {"label": "overview", "output": None}
            location = {"label": "location", "output": None}
        else:
            overview = I.prepare_imagery(directory, sr.factoryCode,
                extent=location_extent if imagery_raster else arcpy.Extent(*_pad_bounds(
                    O._extent_list(arcpy.Describe(sampling["units_for_map"]).extent), 800.0)),
                raster=imagery_raster, provider=imagery_provider, tk=tianditu_key, max_tiles=1500, label="overview", cache_dir=cache_dir,
                labels=imagery_provider == "tianditu_vector")
            location = I._write_source_manifest(directory, dict(overview, label="location"))
        by_unit = {}
        for row in sampling["unit_rows"]:
            uid = str(row["unit"])
            frames = [t["frame_extent"] for t in atlas_targets if t["kind"] == "sample" and uid in t["unit_ids"]]
            image_bounds = [min(b[0] for b in frames), min(b[1] for b in frames),
                            max(b[2] for b in frames), max(b[3] for b in frames)] if frames else _pad_bounds(
                                O._extent_list(O._unit_extent(arcpy, sampling["units_for_map"], data["unit_field"], uid)))
            if uid in choices:
                choice = choices[uid]
                by_unit[uid] = I.prepare_imagery(directory, sr.factoryCode,
                    extent=arcpy.Extent(*image_bounds),
                    raster=choice.get("raster"), provider=choice.get("provider") or imagery_provider,
                    tk=tianditu_key, max_tiles=400, max_zoom=19, label="unit_" + uid, cache_dir=cache_dir,
                    labels=(choice.get("provider") or imagery_provider) == "tianditu_vector")
            elif imagery_raster:
                record = dict(overview, label="unit_" + uid)
                by_unit[uid] = I._write_source_manifest(directory, record)
            else:
                by_unit[uid] = I.prepare_imagery(
                    directory, sr.factoryCode,
                    extent=arcpy.Extent(*image_bounds),
                    provider=imagery_provider, tk=tianditu_key, max_tiles=400, max_zoom=19,
                    label="unit_" + uid, cache_dir=cache_dir,
                    labels=imagery_provider == "tianditu_vector")
        union = None
        with arcpy.da.SearchCursor(sampling["units_for_map"], [data["unit_field"], "SHAPE@"]) as cursor:
            for uid, geometry in cursor:
                record = by_unit[str(uid)]
                if record.get("output"):
                    image_sr = arcpy.Describe(record["output"]).spatialReference
                    projected = geometry.projectAs(image_sr) if geometry.spatialReference.exportToString() != image_sr.exportToString() else geometry
                    qa = I.inspect_raster_coverage(record["output"], projected.WKT)
                    record.update({"pixel_qa": qa, "coverage_status": qa["coverage_status"]})
                    I._write_source_manifest(directory, record)
                union = geometry if union is None else union.union(geometry)
        if choices:
            overview = I.combine_unit_sources(directory, by_unit, sr.factoryCode, O._extent_list(union.extent))
            location = I._write_source_manifest(directory, dict(overview, label="location"))
        for record in (overview, location):
            if record.get("output") and union is not None:
                image_sr = arcpy.Describe(record["output"]).spatialReference
                projected = union.projectAs(image_sr) if union.spatialReference.exportToString() != image_sr.exportToString() else union
                qa = I.inspect_raster_coverage(record["output"], projected.WKT)
                record.update({"pixel_qa": qa, "coverage_status": qa["coverage_status"]})
                I._write_source_manifest(directory, record)
        by_target = {}
        for index, target in enumerate(atlas_targets, 1):
            label = "atlas_{:03d}".format(index)
            if target["kind"] == "sample":
                records = {uid: by_unit[uid] for uid in target["unit_ids"]}
                if len({r.get("output") for r in records.values()}) == 1:
                    record = dict(next(iter(records.values())), label=label)
                elif len(records) == 1:
                    record = dict(next(iter(records.values())), label=label)
                else:
                    record = I.combine_unit_sources(directory, records, sr.factoryCode,
                        target["frame_extent"], label=label)
            else:
                vector = not imagery_raster and bool(tianditu_key) and imagery_provider in ("tianditu", "tianditu_vector")
                local_choices = unit_ids <= set(choices) and all(choices[uid].get("raster") for uid in unit_ids)
                if not vector and not imagery_raster and local_choices:
                    record = I.combine_unit_sources(directory, {uid: by_unit[uid] for uid in target["unit_ids"]},
                        sr.factoryCode, target["frame_extent"], label=label)
                else:
                    record = I.prepare_imagery(directory, sr.factoryCode, extent=arcpy.Extent(*target["frame_extent"]),
                        raster=None if vector else imagery_raster,
                        provider="tianditu_vector" if vector else imagery_provider, tk=tianditu_key,
                        max_tiles=512, max_zoom=18, label=label, cache_dir=cache_dir, labels=vector)
                record["content_role"] = "vector" if vector else "imagery"
            if record.get("output"):
                b = target["frame_extent"]
                record['sampling_diagnostics'] = I.raster_sampling_diagnostics(
                    record['output'], target['scale'], dpi, arcpy_module=arcpy)
                polygon = arcpy.Polygon(arcpy.Array([arcpy.Point(b[0], b[1]), arcpy.Point(b[2], b[1]),
                    arcpy.Point(b[2], b[3]), arcpy.Point(b[0], b[3])]), sr)
                image_sr = arcpy.Describe(record["output"]).spatialReference
                polygon = polygon.projectAs(image_sr) if sr.exportToString() != image_sr.exportToString() else polygon
                record["frame_coverage"] = I.inspect_raster_coverage(record["output"], polygon.WKT)
            by_target[target["name"]] = I._write_source_manifest(directory, record)
        request_dir = os.path.join(directory, "无人机补测需求")
        os.makedirs(request_dir)
        aoi = os.path.join(request_dir, "补测范围.geojson")
        arcpy.conversion.FeaturesToJSON(sampling["units_for_map"], aoi, "FORMATTED",
                                       "NO_Z_VALUES", "NO_M_VALUES", "GEOJSON", "WGS84")
        request = I.write_uav_resurvey_request(request_dir, sampling["unit_rows"],
                                               dict(by_unit, overview=overview), sr.factoryCode, aoi)
        return {"directory": directory, "overview": overview, "location": location, "by_unit": by_unit,
                "by_target": by_target,
                "resurvey": request, "request_dir": request_dir}

    images = stage("imagery_prepared", prepare_images, paths=("directory", "request_dir"),
                   accepted=lambda saved: bool(saved.get("by_target")) and all(item.get("output") and os.path.exists(item["output"])
                       for item in [saved["overview"], saved["location"]] + list(saved["by_unit"].values())
                       + list(saved["by_target"].values()))) if make_maps else {}
    grid = sampling["grid"]
    report = {"生成时间": time.strftime("%Y-%m-%d %H:%M:%S"), "源图层": parcel_fc, "输出目录": out_dir,
              "工作GDB": [data["gdb"], sampling["gdb"]],
              "SHP图层": sampling["shapefiles"],
              "坐标系": {"名称": sr.name, "wkid": sr.factoryCode},
              "字段": {"图斑编号": id_field, "评价单元": data["unit_field"], "划分因素": factor_fields},
              "处理范围": {"模式": "定义查询" if data.get("source_where_clause") else "当前选择" if data["selected_count"] else "整个数据源",
                           "定义查询": data.get("source_where_clause"),
                           "数据源总数": data["source_count"], "本次处理数": data["count"]},
              "评价单元数": data["unit_count"], "图斑数": data["count"], "网格": grid["counts"],
              "因素叠加": data.get("factor_overlay"), "单元划分依据": data.get("unit_design"),
              "人工采样分组": data.get("sampling_group_plan"),
              '已有单元质检': data.get('existing_unit_qa'),
              "编号计划": data.get("numbering"),
              "设计指纹": sampling["design_signature"], "设计修订号": int(state.get("design_revision", 0)),
              "数据缺口": {
                  "四因素缺项": (data.get("unit_design") or {}).get("missing_factors") or [],
                  "项目编号": project_number or "未提供（仅按结构生成，需核对系统备案号）",
                  "高程基准": elevation_gap(meta),
              },
              "项目参数": {
                  "来源": profile_path or "命令行默认值",
                  "面积上限亩": max_mu, "网格尺寸": cell_size_m, "最低点数": min_points,
                  "最小间距米": min_spacing_m, "避边米": inset_m, "dpi": dpi,
              },
              "采样加密": {"文件": density_path, "映射": density} if density_path else None,
              "单元明细": sampling["unit_rows"], "网格单元明细": grid["units"],
              "独立采样质检": {"path": sampling["qa_path"], "status": sampling["independent_qa"]["status"]},
              "容重候选": sampling["bulk_design"],
              "点位清单": sampling["point_csv"], "样品标签预填": (forms_result.get("标签") or {}).get("path"),
              "项目信息": meta or {},
              "外业表格": forms_result,
              "外业表格状态": forms_result.get("状态"),
              "影像": images.get("overview", {}), "影像逐单元": images.get("by_unit", {}),
              "图件像元诊断": {name: row.get('sampling_diagnostics') for name, row in images.get('by_target', {}).items()},
              "网格几何核验": grid.get('geometry_check'),
              "无人机补测需求包": images.get("request_dir"), "状态": "NEEDS_REVIEW",
              "待人工确认": ["设计点须现场复核代表性、均匀性、可达性和边界避让。",
                            "影像来源、时相、原始 GSD、位置精度证明与使用条件须核实。"],
              "技术状态": "PASS", "业务审核状态": "NEEDS_REVIEW",
              "自动图册": atlas_targets, "图件用途": "设计预览" if draft_maps else "采样设计资料"}
    for issue in (data.get('existing_unit_qa') or {}).get('issues', []):
        if issue['status'] != 'PASS':
            report['待人工确认'].append('已有单元 {} / {}：{}'.format(issue.get('unit_id', ''), issue.get('parcel_id', ''), issue['detail']))
    for group in (data.get("sampling_group_plan") or {}).get("groups", []):
        if len(group["parcels"]) > 1:
            report["待人工确认"].append("人工采样组 {}：{}".format(group["group_id"], "；".join(group["review_items"])))
    custom_check = E.custom_design_check(sampling['independent_qa'], project_rules, data['unit_rows'])
    report['项目设计规则'] = project_rules
    report['项目自定义设计核对'] = custom_check
    overridden_units = set(project_rules.get('point_counts_by_unit',{}))
    if any(not row["达标"] and str(row['评价单元']) not in overridden_units for row in grid["units"]) or custom_check['technical_status'] == 'FAIL':
        report["技术状态"] = "FAIL"
        report["待人工确认"].append("存在最低点数或图斑覆盖不足，见网格单元明细。")
    elif custom_check['count_exceptions'] or custom_check['coverage_exceptions']:
        report['待人工确认'].append('已按用户项目规则减少设计点；独立规范质检仍保留FAIL，本图仅为项目自定义设计资料。')
        report['图件用途'] = '项目自定义设计资料'
    if any(not row["间距达标"] for row in grid["units"]):
        report["待人工确认"].append("存在通常 15m 间距不足，需核实小图斑例外。")
    if (data.get("unit_design") or {}).get("missing_factors"):
        report["待人工确认"].append(
            "四因素缺少：{}；评价单元为临时候选，补齐后须重划并重出图。".format(
                "、".join((data["unit_design"] or {}).get("missing_factors") or [])))
    if density_path:
        report["待人工确认"].append(
            "采样加密来自项目给定（采样加密.json）；规范未给密度表，须在差异清单写明依据"
            "（图斑面积、地块数量、地形、客土来源）。")
    if sampling["bulk_design"].get("qa", {}).get("status") == "FAIL":
        report["技术状态"] = "FAIL"
        report["待人工确认"].append("容重独立质检存在硬失败，须修正后再导出设计成果。")
    if forms_result.get("状态") == "FAIL":
        report["技术状态"] = "FAIL"
        report["待人工确认"].append("外业表格生成失败：{}；可在成果目录补做。".format(
            forms_result.get("错误") or "见外业表格清单.json"))
    elif forms_result.get("状态") == "NEEDS_REVIEW":
        report["待人工确认"].append("外业表格存在未填项，见外业表格清单.json；空白项须现场/检测填写。")
    if any(record.get("coverage_status") == "FAIL" for record in images.get("by_unit", {}).values()):
        report["技术状态"] = "FAIL"
        report["待人工确认"].append("部分单元影像存在有效像元覆盖缺口，详见逐单元 pixel_qa。")
    if make_maps:
        imagery_records = [images.get("overview") or {}] + list((images.get("by_unit") or {}).values())
        gate_status, gate_detail = I.imagery_report_gate(imagery_records)
        report["报备影像状态"] = {"status": gate_status, "detail": gate_detail}
        if gate_status != "PASS":
            report["待人工确认"].append("报备影像：{}。".format(gate_detail))
        report["地理要素图层"] = [layer["role"] for layer in context_layers]
        if not context_layers:
            report["待人工确认"].append(
                "未提供居民点/道路/水系/境界线资料（源目录地理要素.json）；若项目区确无该类地物，"
                "须在图面或差异清单注明并附依据。")
        inset_ready = os.path.exists(INSET_TEMPLATE)
        report["区位小图"] = {
            "status": "INCLUDED" if inset_ready else "SKIPPED",
            "boundary_source": "本次选定原始图斑；项目地块分布索引，不代替行政区位资料",
        }

    if make_maps:
        def build_maps():
            render_dir = _fresh_directory(out_dir, "render")
            map_dir = _fresh_directory(out_dir, "map")
            inset = None
            map_template = template or (INSET_TEMPLATE if os.path.exists(INSET_TEMPLATE) else None)
            job = {"project_path": os.path.join(render_dir, "成果图.aprx"), "map_name": "采样点位布置图",
                   "wkid": sr.factoryCode, "crs_name": sr.name, "parcels": data["assign"],
                   "units": sampling["units_for_map"], "grid": grid["grid"], "nodes": grid["node"],
                   "centers": grid["center"], "samples": grid["sample"],
                   "bulk_density_candidates": sampling["bulk_design"].get("output"),
                   "filter_fields": {name: data["unit_field"] for name in ("补充耕地图斑", "评价单元")},
                   "parcel_id_field": data["assign_id"],
                   "parcel_label_field": data.get("source_id_field") or data["assign_id"],
                   "imagery": images["overview"], "imagery_location": images["location"],
                   "imagery_by_unit": images["by_unit"], "template": map_template, "inset": inset,
                   "imagery_by_target": images["by_target"], "project_index": True,
                   "out_dir": out_dir, "render_dir": render_dir, "map_out_dir": map_dir,
                   "dpi": dpi, "scale": scale, "frame_grid": True, "frame_grid_step": 100.0,
                   "label_interval": 200.0, "unit_rows": sampling["unit_rows"], "meta": meta or {},
                   "context_layers": context_layers,
                   "targets": atlas_targets}
            job_path = os.path.join(render_dir, "map_job.json")
            T._atomic_json(job_path, job)
            built = _worker(O._worker_python(), "acceptance.map_worker", job_path)
            return {"project": built["project"], "plan": built["plan"], "build_result": built, "map_dir": map_dir}

        maps = stage("maps_built", build_maps, paths=("project", "plan", "map_dir"),
                     accepted=lambda saved: bool(saved["build_result"].get("preview_layers")) and
                     all(os.path.isfile(path) for path in saved["build_result"]["preview_layers"]))
        report["准备工程"] = maps["project"]
        report["主分幅"] = maps["build_result"].get("sheets", [])
        report["预览图层文件"] = maps["build_result"]["preview_layers"]
        report["预览图层"] = {"parcels": data["assign"], "units": sampling["units_for_map"],
                              "samples": grid["sample"], "grid": grid["grid"],
                              "imagery": images["overview"].get("output")}
        if prepare_only:
            report["出图"] = {"status": "PREPARED", "outputs": [], "detail": "等待地图预览与设计确认，尚未导出图件"}
            _attach_summary(report, out_dir, say)
            T._atomic_json(os.path.join(out_dir, "一键成果报告.json"), report)
            return report

        def export_maps():
            plan_path = maps["plan"]
            if draft_maps:
                preview_dir = _fresh_directory(out_dir, "设计预览图")
                with open(plan_path, encoding="utf-8") as stream:
                    preview_plan = _preview_render_plan(json.load(stream), preview_dir)
                plan_path = os.path.join(preview_dir, "preview_plan.json")
                T._atomic_json(plan_path, preview_plan)
            result = _worker(O._worker_python(), "acceptance.map_export", plan_path)
            result["draft"] = bool(draft_maps)
            if draft_maps:
                for output in result.get("outputs", []):
                    output.update(preview_only=True, deliverable_status="PREVIEW_ONLY")
            result["legend_items"] = maps["build_result"].get("legend_items", [])
            result["result_path"] = os.path.splitext(plan_path)[0] + ".result.json"
            if result.get('outputs') and not any(row.get('status') == 'FAIL' for row in result['outputs']):
                from .map_validation import write_atlas_bundle
                result['bundle'] = write_atlas_bundle(result, os.path.dirname(plan_path))
            return result

        exported = stage("maps_exported", export_maps, paths=("result_path",),
                         accepted=lambda saved: bool(saved.get("draft")) == bool(draft_maps)
                         and saved.get("technical_status") in ("PASS", "NEEDS_REVIEW") and bool(saved.get("outputs"))
                         and all(all(verify_export_artifact(row.get(ext, ""), ext)["status"] != "FAIL" for ext in ("jpg", "png", "pdf"))
                                 for row in saved["outputs"]))
        report['视觉复核'] = refresh_visual_reviews(exported)
        T._atomic_json(exported['result_path'], exported)
        if checkpoint:
            checkpoint('maps_exported', exported)
        report["出图"] = exported
        refresh_report_technical_status(report)
        say("逐幅出图检查：{}，共 {} 幅".format(exported.get("status"), len(exported.get("outputs", []))))
    report["耗时秒"] = round(time.time() - started, 1)
    _attach_summary(report, out_dir, say)
    T._atomic_json(os.path.join(out_dir, "一键成果报告.json"), report)
    return report


def _worker(python, module, input_path):
    process = subprocess.run([python, "-m", module, input_path],
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                             env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    result_path = os.path.splitext(input_path)[0] + ".result.json"
    if process.returncode != 0 or not os.path.isfile(result_path):
        raise RuntimeError("{} 失败：{}\n{}".format(module, process.returncode,
                           (process.stdout or "")[-2000:] + (process.stderr or "")[-2000:]))
    with open(result_path, encoding="utf-8") as stream:
        return json.load(stream)
