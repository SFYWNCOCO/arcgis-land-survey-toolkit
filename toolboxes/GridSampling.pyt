# -*- coding: utf-8 -*-
"""一键网格采样与出图：一个入口完成评价单元、四套网格成果、影像底图与图件。

面向没接触过 ArcGIS Pro 的使用者：只填"图斑图层"和"成果文件夹"，其余参数都有默认值。
"""
import datetime
import os
import sys

import arcpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ["GRID_SAMPLING_ROOT"] = ROOT
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _core():
    root = os.environ.get("GRID_SAMPLING_ROOT", "")
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from acceptance import oneclick
    return oneclick


def _p(name, label, datatype, required=True, direction="Input", default=None, multi=False):
    p = arcpy.Parameter(name=name, displayName=label, datatype=datatype, direction=direction,
                        parameterType="Required" if required else "Optional", multiValue=multi)
    if default is not None:
        p.value = default
    return p


def _load_layout_profile(path):
    if not path:
        return None
    root = os.environ.get('GRID_SAMPLING_ROOT', '')
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from acceptance.map_profile import load_profile
    return load_profile(path)


def _folder(prefix):
    return os.path.join(os.path.expanduser("~"), "Documents", "ArcGIS",
                        prefix + datetime.datetime.now().strftime("_%Y%m%d_%H%M%S"))


class Toolbox:
    def __init__(self):
        self.label = "一键网格采样与出图"
        self.alias = "gridsampling"
        self.tools = [GridSamplingOneClick, RunAcceptanceDefault, ExportTiandituSnapshots, DiscoverProjectData,
                      AdjustDesignPoint, PreviewOnlineImagery, ExtractDemTerrain, ExportSamplingCoordinates,
                      RefreshMapVisualReviews, CheckWorkbenchEnvironment, RedrawAcceptanceMaps]


class CheckWorkbenchEnvironment:
    label = '工作台环境检查'
    description = '在实际GP运行环境检查依赖、版本和输出权限；不生成业务成果。'
    canRunInBackground = False

    def getParameterInfo(self):
        return [_p('output_folder', '拟用成果目录', 'GPString', False),
                _p('environment_json', '环境诊断', 'GPString', False, direction='Output')]

    def isLicensed(self):
        return True

    def execute(self, parameters, messages):
        _core()
        import json
        import tempfile
        target = parameters[0].valueAsText
        parent = os.path.abspath(target) if target else tempfile.gettempdir()
        while not os.path.isdir(parent):
            upper = os.path.dirname(parent)
            if upper == parent:
                raise ValueError('无法找到可用的输出父目录')
            parent = upper
        with tempfile.TemporaryDirectory(prefix='.workbench-check-', dir=parent):
            pass
        info = {'python_exe': sys.executable, 'python_version': sys.version.split()[0],
                'pro_version': arcpy.GetInstallInfo()['Version'], 'arcpy': True,
                'output_parent': parent, 'output_writable': True}
        arcpy.SetParameterAsText(1, json.dumps(info, ensure_ascii=False))
        messages.addMessage('环境检查通过：ArcPy可用，输出目录可写。')


class RedrawAcceptanceMaps:
    label = '重绘已有图件'
    description = '按当前任务计划重绘选中图幅或整套图；保留地块、网格和设计点。'
    canRunInBackground = False

    def getParameterInfo(self):
        return [_p('task_root', '任务成果目录', 'DEFolder'),
                _p('target_name', '图幅名称（留空为整套）', 'GPString', False),
                _p('map_scale', '比例尺分母（auto恢复自动，留空不变）', 'GPString', False),
                _p('service_key', '影像服务密钥', 'GPStringHidden', False),
                _p('result_json', '重绘成果', 'GPString', False, direction='Output')]

    def isLicensed(self):
        return True

    def execute(self, parameters, messages):
        _core()
        import json
        from acceptance.map_redraw import redraw
        result = redraw(parameters[0].valueAsText, parameters[1].valueAsText, log=messages.addMessage,
                        scale=parameters[2].valueAsText, service_key=parameters[3].valueAsText)
        arcpy.SetParameterAsText(4, json.dumps(result, ensure_ascii=False))


class RefreshMapVisualReviews:
    label = '更新图件视觉复核结果'
    description = '核对当前图像与渲染指纹，读回真实审图回执；不改变地块、点位或规范审核结论。'
    canRunInBackground = False

    def getParameterInfo(self):
        return [_p('report_path','本次一键成果报告','DEFile')]

    def isLicensed(self):
        return True

    def execute(self, parameters, messages):
        _core()
        from acceptance.workflow import refresh_visual_reviews
        from acceptance.task_state import _atomic_json
        import json
        path = parameters[0].valueAsText
        with open(path,encoding='utf-8') as stream:
            report=json.load(stream)
        report['视觉复核']=refresh_visual_reviews(report.get('出图') or {})
        from acceptance.triage import write_summary
        report.update(write_summary(report,os.path.dirname(os.path.abspath(path))))
        _atomic_json(path,report)
        messages.addMessage('视觉复核：'+report['视觉复核']['status'])


class ExportTiandituSnapshots:
    label = "按地块批量天地图快照"
    description = ("按面图层的每个图斑分别生成天地图本地快照：每块用自己的范围和 z18，"
                   "叠加地名注记并用 Lanczos 重采样；输出 TIFF 与批量快照清单。"
                   "不自动把全部快照加载到地图；正式报件仍须项目 DOM/无人机影像。")
    canRunInBackground = False

    def getParameterInfo(self):
        source = _p("source", "图斑面图层", "GPFeatureLayer")
        out_dir = _p("out_dir", "快照输出文件夹", "DEFolder", direction="Output", default=_folder("天地图快照"))
        id_field = _p("id_field", "图斑编号字段（留空自动识别）", "Field", False)
        id_field.parameterDependencies = ["source"]
        id_field.filter.list = ["Text", "Short", "Long", "OID"]
        labels = _p("labels", "叠加天地图地名注记", "GPBoolean", required=False, default="true")
        max_zoom = _p("max_zoom", "最大层级（天地图影像最高 18）", "GPLong", default=18)
        pad = _p("pad_m", "每块外扩米数", "GPDouble", default=40.0)
        manifest = _p("manifest", "批量快照清单 JSON", "DEFile", direction="Output")
        manifest.parameterType = "Derived"
        return [source, out_dir, id_field, labels, max_zoom, pad, manifest]

    def execute(self, parameters, messages):
        _core()
        from acceptance.tianditu_batch_snapshot import run
        source = parameters[0].valueAsText
        out_dir = parameters[1].valueAsText
        id_field = parameters[2].valueAsText or ""
        labels = str(parameters[3].valueAsText).lower() in ("true", "1", "yes")
        max_zoom = int(parameters[4].value or 18)
        pad = float(parameters[5].value or 40.0)
        result = run(source, out_dir, id_field=id_field, labels=labels,
                     max_zoom=min(18, max_zoom), pad_m=pad)
        parameters[6].value = result["manifest"]
        messages.addMessage("批量快照：" + str(result["count"]) + " 个；清单：" + result["manifest"])


class RunAcceptanceDefault:
    label = "按默认参数预检/生成预览"
    description = ("AI/一键入口的薄包装：预检资料(check)、按当前选择生成预览(preview)、"
                   "继续上次任务(resume)或取消(cancel)。默认读取源目录项目参数.json；"
                   "正式导出仍须在采样页人工确认，不自动覆盖旧成果。")
    canRunInBackground = False

    def getParameterInfo(self):
        source = _p("source", "补充耕地图斑（面图层/要素类）", "GPFeatureLayer")
        out_dir = _p("out_dir", "成果文件夹（新任务用空目录，继续任务选原目录）", "DEFolder",
                     direction="Output", default=_folder("验收预览"))
        action = _p("action", "任务动作", "GPString", default="preview")
        action.filter.list = ["check", "preview", "resume", "cancel", "export"]
        id_field = _p("id_field", "图斑编号字段（留空自动识别）", "Field", False)
        id_field.parameterDependencies = ["source"]
        id_field.filter.list = ["Text", "Short", "Long", "OID"]
        selection_oids = _p("selection_oids", "当前选择对象ID（逗号分隔，可空=全部）", "GPString", False)
        source_query = _p("source_where_clause", "本次处理范围SQL（当前地图定义查询，可空）", "GPString", False)
        review = _p("review_confirmation", "设计确认记录JSON（仅export，人工确认时传递）", "GPString", False)
        report = _p("report", "预检/成果报告JSON", "DEFile", direction="Output")
        report.parameterType = "Derived"
        return [source, out_dir, action, id_field, selection_oids, source_query, review, report]

    def execute(self, parameters, messages):
        import json
        _core()
        from acceptance.task_state import _atomic_json
        source = parameters[0].valueAsText
        out_dir = parameters[1].valueAsText
        action = parameters[2].valueAsText or "preview"
        kwargs = {}
        for key, index in (("id_field", 3), ("selection_oids", 4), ("source_where_clause", 5)):
            value = parameters[index].valueAsText
            if value:
                kwargs[key] = value
        review = parameters[6].valueAsText
        if review:
            kwargs["review_confirmation"] = json.loads(review)
        try:
            from acceptance.tianditu_snapshot import _tianditu_key
            tdt_key = _tianditu_key()
            if tdt_key:
                kwargs.setdefault("imagery_provider", "tianditu")
                kwargs.setdefault("tianditu_key", tdt_key)
        except Exception:
            pass  # no configured key -> keep profile/default provider
        result = _core().run(source, out_dir, task_action=action, **kwargs)
        if action == "check":
            report_path = os.path.join(out_dir, "资料检查.json")
            _atomic_json(report_path, result)
        elif action == "cancel":
            report_path = os.path.join(out_dir, "取消结果.json")
            _atomic_json(report_path, result)
        else:
            report_path = result.get("成果报告路径") or os.path.join(out_dir, "一键成果报告.json")
            if not os.path.isfile(report_path):
                raise ValueError("一键流程未生成成果报告：" + report_path)
        parameters[7].value = report_path
        messages.addMessage("报告：" + report_path)


class GridSamplingOneClick:
    label = "一键生成采样点与出图"
    description = ("输入补充耕地图斑，自动划分评价单元、生成方格网/网格交点/网格中心点/设计采样点，"
                   "自动规划总览、位置分区及逐原始地块采点图，准备底图并排版预览。所有结果标为设计候选，须人工复核。")
    canRunInBackground = False

    def getParameterInfo(self):
        source = _p("source", "补充耕地图斑（面图层）", "GPFeatureLayer")
        out_dir = _p("out_dir", "成果文件夹（新任务用空目录，继续任务选原目录）", "DEFolder",
                     direction="Output", default=_folder("采样成果"))
        id_field = _p("id_field", "图斑编号字段（留空自动识别）", "Field", False)
        id_field.parameterDependencies = ["source"]
        id_field.filter.list = ["Text", "Short", "Long", "OID"]
        unit_field = _p("unit_field", "评价单元字段（留空则自动划分单元）", "Field", False)
        unit_field.parameterDependencies = ["source"]
        unit_field.filter.list = ["Text"]
        factors = _p("factors", "单元划分附加因素字段（默认四因素与项目隔离；缺项逐图斑候选）", "Field", False, multi=True)
        factors.parameterDependencies = ["source"]
        factors.filter.list = ["Text"]
        max_mu = _p("max_mu", "单元面积上限（亩，规范原则值50）", "GPDouble", default=50.0)
        gap = _p("gap_m", "相邻图斑可合并的最大间距（米）", "GPDouble", default=30.0)
        multipart = _p("allow_multipart", "允许合并成多部件单元（默认否，避免不共边合并）",
                       "GPBoolean", required=False, default="false")
        cell = _p("cell_size", "网格尺寸（米；留空=按布点数自适应，推荐）", "GPDouble", required=False)
        min_points = _p("min_points", "每单元最低分样点数", "GPLong", default=3)
        spacing = _p("min_spacing", "点位最小间距（米）", "GPDouble", default=15.0)
        inset = _p("inset", "田边避让距离（米）", "GPDouble", default=5.0)
        imagery = _p("imagery_raster", "项目 DOM 文件或影像文件夹（可空则用在线影像）", "GPString", False)
        provider = _p("provider", "底图来源（天地图影像/矢量、授权 XYZ/WMTS 或 MapServer）", "GPString", default="demo")
        # 内置 demo / tianditu / tianditu_vector；阿里云或单位授权服务填配置 JSON 路径。
        key = _p("tianditu_key", "服务 key（天地图或配置要求时填写）", "GPStringHidden", False)
        dpi = _p("dpi", "输出DPI（软件默认值，不代表影像精度）", "GPLong", default=300)
        title = _p("title", "总览图图名", "GPString",
                   default="补充耕地质量鉴定采样点位布置图")
        sheet_no = _p("sheet_no", "图号", "GPString", False, default="")
        org = _p("org", "编制单位", "GPString", False, default="")
        drawn = _p("drawn_by", "制图人", "GPString", False, default="")
        checked = _p("checked_by", "审核人", "GPString", False, default="")
        keep_tiles = _p("keep_tiles", "保留影像瓦片缓存", "GPBoolean", required=False, default="false")
        selection_oids = _p("selection_oids", "当前选择对象ID（插件内部传递）", "GPString", False)
        action = _p("task_action", "任务动作", "GPString", default="run")
        action.filter.list = ["check", "run", "resume", "cancel", "prepare", "preview", "export"]
        source_query = _p("source_where_clause", "本次处理范围SQL（当前地图定义查询）", "GPString", False)
        factor_sources = _p("factor_sources", "四因素面资料角色映射JSON（可空）", "GPString", False)
        project_number = _p("project_number", "系统验收备案项目编号（15位，可空则保留候选）", "GPString", False)
        review = _p("review_confirmation", "设计确认记录JSON（导出时由插件传递）", "GPString", False)
        unit_images = _p("unit_imagery_sources", "逐单元影像选择JSON（可空）", "GPString", False)
        project_meta = _p("project_meta", "项目文字信息JSON（插件传递）", "GPString", False)
        profile = _p("layout_profile", "出图版式配置JSON（可空；版式pagx/图名/是否带单元号）", "DEFile", False)
        report = _p("report", "成果报告JSON", "DEFile", direction="Output")
        report.parameterType = "Derived"
        return [source, out_dir, id_field, unit_field, factors, max_mu, gap, multipart, cell,
                min_points, spacing, inset, imagery, provider, key, dpi, title, sheet_no, org,
                drawn, checked, keep_tiles, selection_oids, action, source_query, factor_sources,
                project_number, review, unit_images, project_meta, profile, report]

    def updateParameters(self, parameters):
        if parameters[0].valueAsText and not parameters[2].altered:
            try:
                names = {f.name.upper(): f.name for f in arcpy.ListFields(parameters[0].valueAsText)}
                for candidate in ("PE_ID", "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME"):
                    if candidate in names:
                        parameters[2].value = names[candidate]
                        break
            except (ValueError, OSError, RuntimeError):
                pass
        return

    def execute(self, parameters, messages):
        import json
        source = parameters[0].valueAsText
        out_dir = parameters[1].valueAsText
        profile_path = parameters[30].valueAsText if len(parameters) > 30 else None
        task_action = parameters[23].valueAsText or "run"
        if task_action == "cancel":
            result = _core().run(source, out_dir, task_action="cancel")
            messages.addMessage("取消请求：" + result["status"])
            return
        factors = [str(v) for v in (parameters[4].values or [])]
        meta = {
            "title": parameters[16].valueAsText or "补充耕地质量鉴定采样点位布置图",
            "title_unit": "补充耕地质量鉴定采样点位布置图",
            "layout_profile": _load_layout_profile(profile_path),
            "sheet_no": parameters[17].valueAsText or "",
            "org": parameters[18].valueAsText or "",
            "drawn_by": parameters[19].valueAsText or "",
            "checked_by": parameters[20].valueAsText or "",
            "date": datetime.datetime.now().strftime("%Y年%m月"),
        }
        if parameters[29].valueAsText:
            extra = json.loads(parameters[29].valueAsText)
            if not isinstance(extra, dict):
                raise ValueError("项目文字信息必须是JSON对象")
            for name in ("project_name", "county", "town", "village"):
                if name in extra:
                    meta[name] = str(extra[name] or "")
        sr = arcpy.Describe(source).spatialReference
        try:
            sr.loadFromString(sr.exportToString())
            meta["crs_label"] = "{}".format(sr.name)
        except Exception:  # noqa: BLE001
            meta["crs_label"] = "见项目资料"
        if "3_Degree" in (sr.name or "") or "3 Degree" in (sr.name or ""):
            meta["projection"] = "高斯—克吕格投影（3°分带）"

        core = _core()
        from acceptance.profile import default_path
        profile_path = default_path(arcpy.Describe(source).catalogPath)
        result = core.run(
            source, out_dir, profile=profile_path,
            id_field=parameters[2].valueAsText or None,
            unit_field=parameters[3].valueAsText or None,
            factor_fields=factors,
            max_mu=float(parameters[5].value), gap_m=float(parameters[6].value),
            allow_multipart=bool(parameters[7].value),
            cell_size_m=(float(parameters[8].value) if parameters[8].value not in (None, "") else None),
            min_points=int(parameters[9].value),
            min_spacing_m=float(parameters[10].value), inset_m=float(parameters[11].value),
            imagery_raster=parameters[12].valueAsText or None,
            imagery_provider=parameters[13].valueAsText or "demo",
            tianditu_key=parameters[14].valueAsText or None,
            dpi=int(parameters[15].value), meta=meta,
            keep_tiles=bool(parameters[21].value),
            selection_oids=parameters[22].valueAsText or None,
            task_action=task_action,
            source_where_clause=parameters[24].valueAsText or None,
            factor_sources=json.loads(parameters[25].valueAsText) if parameters[25].valueAsText else None,
            project_number=parameters[26].valueAsText or None,
            review_confirmation=json.loads(parameters[27].valueAsText) if parameters[27].valueAsText else None,
            unit_imagery_sources=json.loads(parameters[28].valueAsText) if parameters[28].valueAsText else None,
            log=lambda msg: messages.addMessage(msg))
        if task_action == "check":
            for item in result["检查"]:
                messages.addMessage("[{}] {}：{}".format(item["状态"], item["项目"], item["说明"]))
            messages.addMessage("资料检查结果：" + result["状态"])
            return
        report_path = result["成果报告路径"]
        arcpy.SetParameterAsText(31, report_path)
        messages.addMessage("评价单元 {} 个 / 图斑 {} 个 / 设计点 {} 个".format(
            result["评价单元数"], result["图斑数"], result["网格"]["设计点"]))
        messages.addMessage("成果目录：" + out_dir)
        try:
            with open(report_path, encoding="utf-8") as stream:
                summary = json.load(stream)
            if summary.get("运行摘要"):
                messages.addMessage("本次运行摘要：" + summary["运行摘要"])
            messages.addMessage("检查状态：{} / 外业表格：{}".format(
                summary.get("运行摘要状态") or "未生成", summary.get("外业表格状态") or "未生成"))
        except (OSError, ValueError) as exc:
            messages.addWarningMessage("摘要读取失败：{}".format(exc))
        for item in result.get("待人工确认", []):
            messages.addWarningMessage("待人工确认：" + item)


class PreviewOnlineImagery:
    label = '连接并预览在线影像（不需要本地影像文件）'
    canRunInBackground = False

    def getParameterInfo(self):
        report = _p('report', '在线影像预览报告', 'DEFile', direction='Output')
        report.parameterType = 'Derived'
        return [_p('source', '图斑图层', 'GPFeatureLayer'),
            _p('provider', '在线来源或服务地址', 'GPString', default='demo'),
            _p('key', '服务key（需要时填写）', 'GPStringHidden', False),
            _p('out_dir', '新建预览目录', 'DEFolder', direction='Output'),
            _p('selection_oids', '已选对象ID', 'GPString', False),
            _p('source_query', '本次范围查询', 'GPString', False), report]

    def execute(self, parameters, messages):
        import os
        import sys
        root = os.environ.get('GRID_SAMPLING_ROOT', '')
        if root and root not in sys.path: sys.path.insert(0, root)
        from acceptance.online_preview import preview_online
        messages.addMessage('正在连接在线影像并预览本次范围首块图斑；不需要本地影像文件。')
        result = preview_online(parameters[0].valueAsText, parameters[3].valueAsText,
            provider=parameters[1].valueAsText or 'demo', key=parameters[2].valueAsText or None,
            selection_oids=parameters[4].valueAsText or None, source_where_clause=parameters[5].valueAsText or None)
        arcpy.SetParameterAsText(6, result['report_path'])
        messages.addMessage('预览图斑：' + result['parcel_id'])
        messages.addMessage('影像预览已获取' if result['preview_png'] else result['imagery'].get('warning', '影像暂未取得'))


class DiscoverProjectData:
    label = "扫描资料文件夹或当前地图"
    description = "只读列出图斑、影像和辅助资料；同名及多个候选保留供选择。"
    canRunInBackground = False

    def getParameterInfo(self):
        source = _p("source", "资料文件夹（或 CURRENT 当前地图）", "GPString")
        output = _p("catalog", "资料清单JSON（新文件）", "DEFile", direction="Output")
        return [source, output]

    def execute(self, parameters, messages):
        import os
        import sys
        root = os.environ.get("GRID_SAMPLING_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from acceptance import data_intake as D
        from acceptance.task_state import _atomic_json
        source, output = parameters[0].valueAsText, parameters[1].valueAsText
        if os.path.exists(output):
            raise FileExistsError("资料清单已存在，不覆盖：" + output)
        if source == "CURRENT":
            project = arcpy.mp.ArcGISProject("CURRENT")
            mapping = project.activeMap
            if mapping is None:
                raise ValueError("请先激活地图视图")
            result = D.discover_map(mapping)
            _atomic_json(output, result)
        else:
            result = D.discover_folder(source, output=output)
        arcpy.SetParameterAsText(1, output)
        messages.addMessage("发现 {} 个资料候选；{} 个问题，选择后再执行。".format(
            len(result["candidates"]), len(result["issues"])))


class AdjustDesignPoint:
    label = "吸附调整设计点并立即复检"
    canRunInBackground = False

    def getParameterInfo(self):
        report = _p("report", "调整收据", "DEFile", direction="Output")
        report.parameterType = "Derived"
        return [_p("out_dir", "原任务目录", "DEFolder"), _p("point_id", "设计点编号", "GPString"),
                _p("x", "点击X坐标", "GPDouble"), _p("y", "点击Y坐标", "GPDouble"),
                _p("wkid", "点击坐标系WKID", "GPLong"), _p("operator", "修改人", "GPString"),
                _p("expected_signature", "预览设计指纹", "GPString", False),
                _p("expected_revision", "预览设计修订号", "GPLong", False), report]

    def execute(self, parameters, messages):
        import os
        import sys
        import uuid
        root = os.environ.get("GRID_SAMPLING_ROOT", "")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from acceptance.point_adjustment import adjust_point
        from acceptance.task_state import _atomic_json
        out = parameters[0].valueAsText
        result = adjust_point(out, parameters[1].valueAsText, float(parameters[2].value),
            float(parameters[3].value), int(parameters[4].value), parameters[5].valueAsText,
            expected_signature=parameters[6].valueAsText or None,
            expected_revision=int(parameters[7].value) if parameters[7].value is not None else None)
        receipt = os.path.join(out, "点位调整收据_{}.json".format(uuid.uuid4().hex))
        _atomic_json(receipt, result)
        arcpy.SetParameterAsText(8, receipt)
        messages.addMessage("设计点已吸附到{}，独立复检：{}；请重新预览后确认。".format(result["snap_kind"], result["qa"]["status"]))


def _suggest_stable_id(source, parameter, candidates):
    if not source or parameter.altered:
        return
    try:
        fields = {field.name.upper(): field.name for field in arcpy.ListFields(source)}
        for candidate in candidates:
            name = fields.get(candidate)
            if not name:
                continue
            with arcpy.da.SearchCursor(source, [name]) as cursor:
                if any(row[0] is not None and str(row[0]).strip() for row in cursor):
                    parameter.value = name
                    break
    except (ValueError, OSError, RuntimeError):
        return


class ExtractDemTerrain:
    label = 'DEM提取点位高程与坡度'
    description = ('选择单波段DEM和设计点，计算点位高程、坡度；可同时统计原始地块/已确认单元的平均高程和坡度。'
                   '按设计点米制投影重投影工作栅格；不依赖Spatial Analyst扩展。未知高程基准和NoData保留待核。')
    canRunInBackground = False

    def getParameterInfo(self):
        dem = _p('dem', 'DEM（单波段高程栅格）', 'GPRasterLayer')
        points = _p('points', '设计点图层（已确认的米制投影）', 'GPFeatureLayer')
        points.filter.list = ['Point']
        point_id = _p('point_id', '设计点编号字段', 'Field')
        point_id.parameterDependencies = ['points']
        point_id.filter.list = ['Text', 'Short', 'Long']
        units = _p('units', '原始地块或已确认单元面（可空）', 'GPFeatureLayer', False)
        units.filter.list = ['Polygon']
        unit_id = _p('unit_id', '面编号字段（提供面时填写）', 'Field', False)
        unit_id.parameterDependencies = ['units']
        unit_id.filter.list = ['Text', 'Short', 'Long']
        out_dir = _p('out_dir', 'DEM成果文件夹（空目录）', 'DEFolder', direction='Output', default=_folder('DEM地形成果'))
        vertical_unit = _p('vertical_unit', 'DEM高程值单位（按数据资料确认）', 'GPString', default='米')
        vertical_unit.filter.list = ['米', '英尺']
        datum = _p('vertical_reference', '源DEM高程基准（按资料填；此处不做基准转换）', 'GPString', False)
        report = _p('report', 'DEM计算报告', 'DEFile', direction='Output')
        report.parameterType = 'Derived'
        return [dem, points, point_id, units, unit_id, out_dir, vertical_unit, datum, report]

    def updateParameters(self, parameters):
        _suggest_stable_id(parameters[1].valueAsText, parameters[2], ('POINT_ID', 'SAMPLE_ID'))
        _suggest_stable_id(parameters[3].valueAsText, parameters[4], ('PJDYBH', 'UNIT_KEY', 'DKBH', 'DIKUAI_NO'))

    def updateMessages(self, parameters):
        if parameters[3].valueAsText and not parameters[4].valueAsText:
            parameters[4].setErrorMessage('请选择原始面编号字段；正式单元号为空时可选原始地块关联键。')

    def execute(self, parameters, messages):
        import os
        import sys
        root = os.environ.get('GRID_SAMPLING_ROOT', '')
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from acceptance.dem import extract_terrain
        result = extract_terrain(parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, parameters[5].valueAsText,
            unit_fc=parameters[3].valueAsText or None, unit_id_field=parameters[4].valueAsText or None,
            vertical_unit=parameters[6].valueAsText or '米', vertical_reference=parameters[7].valueAsText or '')
        arcpy.SetParameterAsText(8, result['report_path'])
        valid = sum(row['status'] == 'DEM_DERIVED' for row in result['points'])
        messages.addMessage('DEM计算完成：{}个点，{}个高程/坡度均有效；{}个面统计。'.format(
            len(result['points']), valid, len(result['units'])))
        messages.addMessage('点位高程坡度表：' + result['point_csv'])
        messages.addMessage('DEM点副本：' + result['point_output'])
        if valid < len(result['points']):
            messages.addWarningMessage('部分点无有效DEM邻域，高程或坡度已留空；请查看计算报告。')
        messages.addWarningMessage('DEM推算值与高程基准、分辨率和现场代表性须核实，结果状态：' + result['status'])


class ExportSamplingCoordinates:
    label = '导出设计点经纬度表'
    description = '从插件标准设计点图层导出CGCS2000十进制度经纬度、度分秒和成果XY坐标，用于核对和填表。'
    canRunInBackground = False

    def getParameterInfo(self):
        points = _p('points', '插件标准设计点图层', 'GPFeatureLayer')
        points.filter.list = ['Point']
        output = _p('out_csv', '点位经纬度CSV', 'DEFile', direction='Output',
                    default=os.path.join(_folder('点位经纬度'), '点位经纬度.csv'))
        output.filter.list = ['csv']
        return [points, output]

    def execute(self, parameters, messages):
        import os
        import sys
        root = os.environ.get('GRID_SAMPLING_ROOT', '')
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from acceptance.point_adjustment import write_point_csv
        source, output = parameters[0].valueAsText, parameters[1].valueAsText
        fields = {field.name for field in arcpy.ListFields(source)}
        missing = sorted({'PJDYBH', 'DKBH', 'POINT_ID', 'SAMPLE_ID', 'DEPTH_TOP', 'DEPTH_BOT', 'DESIGN_ST'} - fields)
        if missing:
            raise ValueError('请选择插件标准设计点图层；缺少字段：' + '、'.join(missing))
        if os.path.exists(output):
            raise FileExistsError('坐标表文件已存在，请选择新文件名：' + output)
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        write_point_csv(output, source, arcpy)
        arcpy.SetParameterAsText(1, output)
        messages.addMessage('点位经纬度表已导出：' + output)
