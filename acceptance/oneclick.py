"""一键流程：图斑 → 评价单元 → 网格四套成果 → 影像 → 出图 → 交付清单。

对外只有一个入口 run()，参数全部有默认值，用户通常只需给"图斑图层"和"输出文件夹"。
技术检查与业务审核分别记录，不代替专业复核。
"""
from __future__ import annotations

import json
import inspect
import os
import sys
import time

from . import task_state as T
from . import workflow
from .sampling import _projected_meters_per_unit
from .data_intake import field_mapping, FACTOR_FIELDS

_PIPELINE_SIGNATURE = inspect.signature(workflow.execute)
MU_M2 = 666.6666667
ID_CANDIDATES = ("PE_ID", "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME", "OID")
UNIT_CANDIDATES = ("PJDYBH", "PJDYBH_M", "DYBH", "EVAL_UNIT")


def _pick_field(names, candidates):
    lower = {n.lower(): n for n in names}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def detect_fields(source):
    import arcpy
    names = [f.name for f in arcpy.ListFields(source)]
    roles = field_mapping(names)
    return {
        "id": _pick_field(names, ID_CANDIDATES),
        "unit": _pick_field(names, UNIT_CANDIDATES),
        "factors": [roles[role] for role in FACTOR_FIELDS if role in roles] + ([roles["project"]] if "project" in roles else []),
        "factor_mapping": roles,
        "missing_factors": [role for role in FACTOR_FIELDS if role not in roles],
        "all": names,
    }


def _selection_where(arcpy, source, selection_oids):
    if not selection_oids:
        return None, 0
    raw = selection_oids.split(",") if isinstance(selection_oids, str) else list(selection_oids)
    ids = sorted({int(str(value).strip()) for value in raw if str(value).strip()})
    if not ids or any(value < 0 for value in ids):
        raise ValueError("当前选择范围没有有效的对象 ID。")
    if len(ids) > 10000:
        raise ValueError("当前选择超过 10,000 个要素；请先导出所选图斑为临时数据副本再运行。")
    oid_field = getattr(arcpy.Describe(source), "OIDFieldName", "OBJECTID")
    delimiter = arcpy.AddFieldDelimiters(source, oid_field)
    clauses = ["{} IN ({})".format(delimiter, ",".join(str(v) for v in ids[i:i + 500]))
               for i in range(0, len(ids), 500)]
    return " OR ".join("({})".format(clause) for clause in clauses), len(ids)


def _worker_python():
    homes = [os.environ.get("ARCGIS_PRO_HOME")]
    try:
        import arcpy
        homes.append(arcpy.GetInstallInfo().get("InstallDir"))
    except ImportError:
        pass
    candidates = [sys.executable] if os.path.basename(sys.executable).lower().startswith("python") else []
    candidates += [os.path.join(home, "bin", "Python", "envs", "arcgispro-py3", "python.exe")
                   for home in homes if home]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise RuntimeError("无法定位 ArcGIS Pro Python 解释器；请检查 Pro 安装或 ARCGIS_PRO_HOME。")


def _quality_checks(arcpy, parcel_fc, id_field, all_fields):
    """数据质量：编号空值/重复、空几何。

    课件 p26 要求核对图斑矢量数据的合规性与一致性；此前 preflight 只查坐标系、
    编号字段是否存在和范围，重复编号与空几何要到生成单元阶段才暴露。
    """
    checks = []
    has_id = bool(id_field) and id_field in all_fields
    empty_ids = null_geom = 0
    seen = {}
    columns = ["SHAPE@"] + ([id_field] if has_id else [])
    with arcpy.da.SearchCursor(str(parcel_fc), columns) as cursor:
        for row in cursor:
            shape = row[0]
            if shape is None or shape.area <= 0:
                null_geom += 1
            if has_id:
                text = "" if row[1] is None else str(row[1]).strip()
                if not text:
                    empty_ids += 1
                else:
                    seen[text] = seen.get(text, 0) + 1
    if has_id:
        duplicates = sum(1 for count in seen.values() if count > 1)
        if empty_ids:
            checks.append({"项目": "编号空值", "状态": "FAIL", "说明": "{} 个图斑编号为空".format(empty_ids)})
        if duplicates:
            checks.append({"项目": "编号重复", "状态": "FAIL", "说明": "{} 个编号重复".format(duplicates)})
        if not empty_ids and not duplicates:
            checks.append({"项目": "编号质量", "状态": "PASS", "说明": "编号无空值、无重复"})
    if null_geom:
        checks.append({"项目": "空几何", "状态": "FAIL", "说明": "{} 个图斑无有效几何".format(null_geom)})
    else:
        checks.append({"项目": "空几何", "状态": "PASS", "说明": "所有图斑几何有效"})
    return checks


def preflight(parcel_fc, out_dir, id_field=None, unit_field=None, factor_fields=(), selection_oids=None, source_where_clause=None):
    """只读检查运行条件，返回可以直接显示给新手的检查项。"""
    import arcpy
    checks = []
    if not arcpy.Exists(str(parcel_fc)):
        return {"状态": "FAIL", "检查": [{"项目": "图斑输入", "状态": "FAIL",
                                           "说明": "图斑数据源不存在：{}".format(parcel_fc)}]}
    desc = arcpy.Describe(str(parcel_fc))
    try:
        _projected_meters_per_unit(desc.spatialReference)
        checks.append({"项目": "坐标系", "状态": "PASS", "说明": getattr(desc.spatialReference, "name", "")})
    except Exception as exc:
        checks.append({"项目": "坐标系", "状态": "FAIL", "说明": str(exc)})
    fields = detect_fields(str(parcel_fc))
    picked_id = id_field or fields["id"]
    checks.append({"项目": "图斑编号", "状态": "PASS" if picked_id in fields["all"] else "FAIL",
                   "说明": picked_id or "未发现常见编号字段"})
    checks.extend(_quality_checks(arcpy, str(parcel_fc),
                                  picked_id if picked_id in fields["all"] else "", fields["all"]))
    selected_where, selected_count = _selection_where(arcpy, str(parcel_fc), selection_oids)
    if source_where_clause:
        selected_where = "({}) AND ({})".format(selected_where, source_where_clause) if selected_where else source_where_clause
    try:
        with arcpy.da.SearchCursor(str(parcel_fc), ["OID@"], where_clause=selected_where) as cursor:
            scope_count = sum(1 for _ in cursor)
        checks.append({"项目": "处理范围", "状态": "PASS" if scope_count else "FAIL",
                       "说明": "本次 {} 个图斑；{}".format(scope_count, "定义查询范围" if source_where_clause else "当前选择" if selected_count else "整个数据源")})
    except Exception as exc:
        checks.append({"项目": "处理范围", "状态": "FAIL", "说明": "查询无法应用到所选数据源：{}".format(exc)})
    output = os.path.abspath(os.fspath(out_dir))
    if os.path.exists(output) and os.listdir(output):
        has_task = os.path.isfile(os.path.join(output, T.STATE_NAME))
        checks.append({"项目": "成果目录", "状态": "NEEDS_REVIEW" if has_task else "FAIL",
                       "说明": "检测到可继续的任务" if has_task else "目录非空，请选择新文件夹"})
    else:
        checks.append({"项目": "成果目录", "状态": "PASS", "说明": "可用于新任务"})
    try:
        from . import forms
        templates = forms.ensure_templates()
        checks.append({"项目": "外业表格模板", "状态": "PASS",
                       "说明": "附录 D/E/F 单表模板齐全：" + "、".join(sorted(os.path.basename(path) for path in templates.values()))})
    except Exception as exc:  # noqa: BLE001 - 缺模板不阻断出图，但必须提示
        checks.append({"项目": "外业表格模板", "状态": "NEEDS_REVIEW",
                       "说明": "模板缺失或不可读：{}".format(exc)})
    status = "FAIL" if any(item["状态"] == "FAIL" for item in checks) else "NEEDS_REVIEW" if any(
        item["状态"] == "NEEDS_REVIEW" for item in checks) else "PASS"
    return {"状态": status, "检查": checks, "源图斑数": int(arcpy.management.GetCount(str(parcel_fc))[0]),
            "候选编号字段": picked_id, "候选单元字段": unit_field or fields["unit"],
            "候选划分因素": list(factor_fields or fields["factors"])}


def _record_ledger(report, action, started, status="", error=""):
    """写运行账本；账本失败不能拖死任务。"""
    try:
        from .run_ledger import record
        record(report, action=action, elapsed_s=time.time() - started,
               status=status, error=str(error))
    except Exception:  # noqa: BLE001 - 运行账本是旁路，任何异常都不影响主流程
        pass


def run(parcel_fc, out_dir, *args, **kwargs):
    """任务入口：只读检查、执行、按已完成阶段继续、阶段间取消。"""
    started = time.time()
    task_action = kwargs.pop("task_action", "run")
    if task_action == 'cancel':
        return T.request_cancel(out_dir)
    review = kwargs.pop("review_confirmation", None)
    profile_path = kwargs.pop("profile", None)
    explicit_cell = kwargs.get('cell_size_m')
    values = {}
    if profile_path is None:
        from .profile import default_path
        profile_path = default_path(str(parcel_fc))
    if profile_path:
        from .profile import load as load_profile, merge_defaults
        values = load_profile(profile_path)
        kwargs = merge_defaults(values, kwargs)
    kwargs["profile_path"] = profile_path
    if task_action not in ("check", "run", "resume", "prepare", "preview", "export"):
        raise ValueError("未知任务动作")
    # draft_maps 必须显式给出：仅预览动作为真，其余动作强制为假，
    # 防止上一次预览的参数被带入正式导出（Pro 实测：正式图沿用了预览标题）
    kwargs["draft_maps"] = task_action == "preview"
    if task_action == "prepare":
        kwargs["prepare_only"] = True
    elif task_action == "export":
        kwargs["prepare_only"] = False
    bound = _PIPELINE_SIGNATURE.bind(parcel_fc, out_dir, *args, **kwargs)
    bound.apply_defaults()
    options = {key: value for key, value in bound.arguments.items()
               if key not in ("parcel_fc", "out_dir", "log", "checkpoint", "resume_state", "prepare_only", "draft_maps")}
    if task_action == "check":
        checked = preflight(parcel_fc, out_dir, **{key: options[key] for key in (
            "id_field", "unit_field", "factor_fields", "selection_oids", "source_where_clause")})
        _record_ledger(checked, "check", started)
        return checked
    if task_action == "export":
        prior = T.read_task(out_dir)
        digest = T.input_fingerprint(parcel_fc, options)[0]
        if not isinstance(review, dict) or not str(review.get("operator") or "").strip():
            raise ValueError("请先在地图预览中确认单元与点位，并填写设计确认人")
        if prior["input_fingerprint"] != digest or review.get("fingerprint") != digest:
            raise ValueError("资料、参数或程序已改变，请重新自动准备并确认预览")
        if prior.get("status") not in ("PREPARED", "COMPLETED"):
            raise ValueError("请先完成自动准备，再确认导出")
        if prior.get("report_path"):
            with open(prior["report_path"], encoding="utf-8") as stream:
                preview = json.load(stream)
                if preview.get("技术状态") == "FAIL":
                    raise ValueError("预览存在技术失败项，请修正后重新准备和确认")
            import arcpy
            from .point_adjustment import design_signature
            if preview.get("设计修订号", 0) != prior.get("design_revision", 0) or preview.get("设计指纹") != design_signature(
                prior["phases"]["data_prepared"]["detail"], prior["phases"]["sampling_prepared"]["detail"], arcpy):
                raise ValueError("单元或点位已修改，请重新自动准备并确认预览后导出")
    action = task_action
    if task_action in ("prepare", "preview", "export"):
        action = "resume" if os.path.isfile(os.path.join(out_dir, T.STATE_NAME)) else "run"
    state = T.begin_task(out_dir, parcel_fc, options, action=action)
    state_root = os.path.abspath(os.fspath(out_dir))
    execution_dir = state["latest_run_dir"]

    def checkpoint(phase, detail=None):
        T.record_phase(state_root, state, phase, detail)

    try:
        checkpoint("preflight", {"source": os.path.abspath(os.fspath(parcel_fc))})
        pipeline_options = dict(bound.arguments, out_dir=execution_dir,
                                checkpoint=checkpoint, resume_state=state)
        report = workflow.execute(**pipeline_options)
        from .profile import source_catalog_path
        adaptive = options['cell_size_m'] is None or (isinstance(options['cell_size_m'], str)
            and options['cell_size_m'].strip().lower() in ('', 'auto', '自适应'))
        report['参数追溯'] = {
            '输入图层': str(parcel_fc), '实际数据源': source_catalog_path(parcel_fc),
            '项目参数文件': profile_path, '网格模式': 'adaptive' if adaptive else 'fixed',
            '网格尺寸_m': options['cell_size_m'],
            '网格参数来源': '显式调用参数' if explicit_cell is not None else '项目参数文件' if profile_path and 'cell_size_m' in values else '程序默认',
            '最低点数': options['min_points'], '最小间距_m': options['min_spacing_m'],
            '避边距离_m': options['inset_m'], '输出DPI': options['dpi'],
            '执行模块': os.path.abspath(__file__),
            '程序指纹': state['input_details']['engine'],
        }
        from .workflow import _attach_summary
        _attach_summary(report, execution_dir, kwargs.get('log') or (lambda _: None))
        report_path = os.path.join(execution_dir, "一键成果报告.json")
        report["任务状态文件"] = os.path.join(state_root, T.STATE_NAME)
        report["任务运行ID"] = state["run_id"]
        report["成果报告路径"] = report_path
        report["输入指纹"] = state["input_fingerprint"]
        if review:
            report["设计确认"] = dict(review, confirmed_at=__import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                                   scope="确认采样设计；不代表影像精度核验或现场实采完成")
        T._atomic_json(report_path, report)
        if kwargs.get("prepare_only") or kwargs.get("draft_maps"):
            T.finish_task(state_root, state, "PREPARED", report_path=report_path)
        elif report.get("技术状态") == "FAIL":
            T.finish_task(state_root, state, "NEEDS_CORRECTION", report_path=report_path)
        else:
            checkpoint("completed", {"report_path": report_path})
            T.finish_task(state_root, state, "COMPLETED", report_path=report_path)
        _record_ledger(report, task_action, started)
        return report
    except T.TaskCancelled:
        raise
    except Exception as exc:
        T.finish_task(state_root, state, "FAILED", error=exc)
        _record_ledger(None, task_action, started, status="FAILED", error=exc)
        raise


def _extent_list(extent):
    return [extent.XMin, extent.YMin, extent.XMax, extent.YMax]


def _existing_unit_rows(arcpy, parcels, unit_field, id_field, max_mu=50.0, qa_path=None, return_qa=False):
    from .existing_unit_qa import check_existing_units
    review = check_existing_units(arcpy, parcels, unit_field, id_field, max_mu=max_mu, output=qa_path)
    if review['status'] == 'FAIL':
        failures = [item for item in review['issues'] if item['status'] == 'FAIL']
        raise ValueError('已有单元检查失败：{}；检查报告：{}'.format(
            '；'.join('{} / {}：{}'.format(item.get('unit_id', ''), item.get('parcel_id', ''), item['detail']) for item in failures[:5]),
            qa_path or '见数据检查明细'))
    return (review['units'], review) if return_qa else review['units']


def _landtype_lookup(arcpy, parcels, unit_field):
    names = [f.name for f in arcpy.ListFields(parcels)]
    field = next((n for n in ("LANDTYPE", "DLMC", "DL") if n in names), None)
    if not field:
        return {}
    result = {}
    with arcpy.da.SearchCursor(parcels, [unit_field, field]) as cur:
        for uid, value in cur:
            result.setdefault(str(uid), str(value or ""))
    return result


def _dissolve_units(arcpy, gdb, assign, unit_field):
    out = os.path.join(gdb, "评价单元")
    if arcpy.Exists(out):
        return out
    arcpy.management.Dissolve(assign, out, unit_field)
    return out


def _unit_extent(arcpy, units_fc, unit_field, uid):
    with arcpy.da.SearchCursor(units_fc, ["SHAPE@"], "{} = '{}'".format(
            unit_field, str(uid).replace("'", "''"))) as cur:
        for (geom,) in cur:
            return geom.extent
    raise ValueError("评价单元 {} 无几何".format(uid))


def _unit_area(arcpy, units_fc, unit_field, uid):
    with arcpy.da.SearchCursor(units_fc, ["SHAPE@"], "{} = '{}'".format(
            unit_field, str(uid).replace("'", "''"))) as cur:
        for (geom,) in cur:
            return round(geom.getArea("GEODESIC", "SQUAREMETERS") / MU_M2, 2)
    return 0.0
