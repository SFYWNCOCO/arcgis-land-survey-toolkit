# -*- coding: utf-8 -*-
"""Parcel processing commands shipped with the ArcGIS Pro Add-In."""
import os
import sys
import datetime
import arcpy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ["PARCEL_TOOLS_ROOT"] = ROOT


def _core():
    root = os.environ.get("PARCEL_TOOLS_ROOT", "")
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from acceptance import parcel_tools
    return parcel_tools


def _poi_core():
    root = os.environ.get("PARCEL_TOOLS_ROOT", "")
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from acceptance import poi_reference
    return poi_reference


def _p(name, label, datatype, required=True, direction="Input", default=None, multi=False):
    p = arcpy.Parameter(name=name, displayName=label, datatype=datatype, direction=direction,
                        parameterType="Required" if required else "Optional", multiValue=multi)
    if default is not None:
        p.value = default
    return p


def _id(name="id_field"):
    p = _p(name, "地块编号字段", "Field")
    p.parameterDependencies = ["source"]
    p.filter.list = ["Text", "Short", "Long", "OID"]
    return p


def _out(name, label, datatype="DEFeatureClass"):
    p = _p(name, label, datatype, direction="Output")
    p.parameterType = "Derived"
    return p


def _folder(prefix):
    return os.path.join(os.path.expanduser("~"), "Documents", "ArcGIS", prefix + datetime.datetime.now().strftime("_%Y%m%d_%H%M%S"))


def _default_id(parameters):
    if parameters[0].valueAsText and not parameters[1].altered:
        try:
            names = {f.name.upper(): f.name for f in arcpy.ListFields(parameters[0].valueAsText)}
            for candidate in ("PE_ID", "DIKUAI_NO", "DKBH", "BSM", "PL_NAME"):
                if candidate in names:
                    parameters[1].value = names[candidate]
                    break
        except (ValueError, OSError, RuntimeError):
            pass  # GP validates the source independently; defaults are optional.


class Toolbox:
    def __init__(self):
        self.label = "地块定位与处理"
        self.alias = "parceltools"
        self.tools = [ParcelAudit, ParcelPrepare, ParcelMaps, POIReferenceSearch, ParcelEditPreview, ParcelEditResolve]


class ParcelEditPreview:
    label = "图斑合并或分割预览"
    description = "在新工作副本上生成候选；源数据不变，确认前不作为采样来源。"
    canRunInBackground = False

    def getParameterInfo(self):
        operation = _p('operation', '操作', 'GPString')
        operation.filter.list = ['merge', 'split']
        return [_p('source', '完整源面数据集路径', 'DEFeatureClass'), _id(),
                _p('selected_oids', '选中对象ID的JSON数组', 'GPString'), operation,
                _p('out_folder', '新编辑版本目录', 'GPString'),
                _p('split_json', '分割线Esri JSON', 'GPString', False), _out('result', '候选回执JSON', 'GPString')]

    def execute(self, parameters, messages):
        import json
        root = os.environ.get('PARCEL_TOOLS_ROOT', '')
        if root and root not in sys.path: sys.path.insert(0, root)
        from acceptance.parcel_edit import preview
        result = preview(*(p.valueAsText for p in parameters[:5]), split_json=parameters[5].valueAsText)
        arcpy.SetParameterAsText(6, json.dumps(result, ensure_ascii=False))
        messages.addMessage('几何检查通过；请在地图确认候选，业务状态仍为待复核。')


class ParcelEditResolve:
    label = "确认、取消或撤销图斑编辑"
    canRunInBackground = False

    def getParameterInfo(self):
        action = _p('action', '操作', 'GPString')
        action.filter.list = ['confirm', 'cancel', 'undo', 'verify']
        return [_p('manifest', '编辑回执', 'DEFile'), _p('token', '当前回执内容指纹', 'GPString'),
                action, _p('operator', '操作人', 'GPString'), _out('result', '操作回执JSON', 'GPString')]

    def execute(self, parameters, messages):
        import json
        root = os.environ.get('PARCEL_TOOLS_ROOT', '')
        if root and root not in sys.path: sys.path.insert(0, root)
        from acceptance.parcel_edit import resolve
        result = resolve(*(p.valueAsText for p in parameters[:4]))
        arcpy.SetParameterAsText(4, json.dumps(result, ensure_ascii=False))
        messages.addMessage('编辑版本状态：' + result['status'])


class ParcelAudit:
    label = "地块一键质检"
    description = "检查几何、重叠、编号、指定必填字段及指定口径面积；异常地块自动加到地图，CSV可逐条核对。"
    canRunInBackground = False

    def getParameterInfo(self):
        source = _p("source", "待检查面图层（遵循当前选择和定义查询）", "GPFeatureLayer")
        required = _p("required_fields", "必填字段（由项目要求指定；备注不自动判缺项）", "Field", False, multi=True)
        required.parameterDependencies = ["source"]
        area = _p("area_field", "待核对面积字段（可空）", "Field", False)
        area.parameterDependencies = ["source"]
        unit = _p("area_unit", "属性面积单位", "GPString", default="公顷")
        unit.filter.list = ["平方米", "公顷", "亩"]
        method = _p("method", "面积计算方法（与源字段口径一致）", "GPString", default="椭球面积")
        method.filter.list = ["椭球面积", "平面面积"]
        return [source, _id(), _p("out_folder", "新的质检成果目录（同名拒绝覆盖）", "DEFolder", direction="Output", default=_folder("地块质检")),
                required, area, unit, method, _p("tolerance", "面积比较容差（平方米）", "GPDouble", default=0.5),
                _p("overlap_tolerance", "重叠面积容差（平方米）", "GPDouble", default=0.01), _out("anomalies", "异常地块")]

    def updateParameters(self, parameters):
        _default_id(parameters)

    def execute(self, parameters, messages):
        result = _core().audit_parcels(parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, required_fields=[str(v) for v in (parameters[3].values or [])],
            area_field=parameters[4].valueAsText, area_unit=parameters[5].valueAsText,
            method=parameters[6].valueAsText, tolerance_m2=float(parameters[7].value),
            overlap_tolerance_m2=float(parameters[8].value))
        arcpy.SetParameterAsText(9, result["anomaly_fc"])
        messages.addMessage("检查{}块，{}条问题，涉及{}块。目录：{}".format(result["feature_count"], result["issue_count"], result["anomaly_count"], parameters[2].valueAsText))
        messages.addMessage(result["scope"])


class ParcelPrepare:
    label = "地块数据整理"
    description = "复制到新FileGDB，可选坐标转换、字段复制/补充；分别新增平面和椭球面积，原文件不修改。"
    canRunInBackground = False

    def getParameterInfo(self):
        rules = _p("rules_json", "字段规则JSON（可空；copy复制到新字段，set新增赋值字段）", "DEFile", False)
        rules.filter.list = ["json"]
        return [_p("source", "源面图层（遵循当前选择和定义查询）", "GPFeatureLayer"),
                _p("out_folder", "新的整理成果目录", "DEFolder", direction="Output", default=_folder("地块整理")),
                _p("target_sr", "目标坐标系（可空；默认源坐标系）", "GPCoordinateSystem", False),
                _p("transformation", "经项目资料确认的基准转换方法（跨基准必填）", "GPString", False),
                rules, _out("prepared", "整理地块")]

    def execute(self, parameters, messages):
        target_sr = None
        if parameters[2].valueAsText:
            target_sr = arcpy.SpatialReference()
            target_sr.loadFromString(parameters[2].valueAsText)
        result = _core().prepare_parcels(parameters[0].valueAsText, parameters[1].valueAsText,
            target_sr=target_sr, transformation=parameters[3].valueAsText, rules_path=parameters[4].valueAsText)
        arcpy.SetParameterAsText(5, result["output"])
        messages.addMessage("已整理{}块：{}".format(result["count"], result["output"]))
        messages.addMessage("原字段保留，面积新增字段：" + ", ".join(result["computed_fields"]))


class ParcelMaps:
    label = "地块批量制图"
    description = "使用当前地图的工程副本，输出总览及逐地块PDF。默认A4横向；自定义模板必须含一个地图框及Title、PT_META文本元素。"
    canRunInBackground = False

    def getParameterInfo(self):
        template = _p("template", "自定义布局模板 .pagx（可空）", "DEFile", False)
        template.filter.list = ["pagx"]
        return [_p("source", "待制图面图层（遵循当前选择和定义查询）", "GPFeatureLayer"), _id(),
                _p("out_folder", "新的PDF成果目录", "DEFolder", direction="Output", default=_folder("地块图件")),
                _p("title", "图名", "GPString", default="地块核查图"), _p("author", "编制单位/人员", "GPString", False),
                template, _p("dpi", "导出分辨率DPI（150–600）", "GPLong", default=300), _out("manifest", "制图记录", "DEFile")]

    def updateParameters(self, parameters):
        _default_id(parameters)

    def execute(self, parameters, messages):
        project = arcpy.mp.ArcGISProject("CURRENT")
        if project.activeMap is None:
            raise ValueError("请先激活用于制图的地图视图。")
        result = _core().export_parcel_maps(parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, project, project.activeMap.name,
            template=parameters[5].valueAsText, title=parameters[3].valueAsText,
            author=parameters[4].valueAsText or "", dpi=int(parameters[6].value))
        arcpy.SetParameterAsText(7, os.path.join(parameters[2].valueAsText, "制图记录.json"))
        messages.addMessage("已输出总览及{}张地块图，共{}份PDF。".format(result["parcel_count"], result["pdf_count"]))


class POIReferenceSearch:
    label = "高德 POI 参考检索"
    description = "在线检索会把范围包络坐标、关键词/类型码和 Key 发给高德；原始项目面只在本机精确筛选。输出独立的 WGS84 参考点图层；GCJ-02 坐标经近似换算，不作为测绘、验收或报件依据。"
    canRunInBackground = False

    def getParameterInfo(self):
        aoi = _p("aoi", "项目范围面（范围包络将发送到高德；遵循当前选择）", "GPFeatureLayer")
        aoi.filter.list = ["Polygon"]
        keywords = _p("keywords", "POI关键词（可空；关键词与类型码至少填一项）", "GPString", False)
        types = _p("types", "高德POI类型码（可空；多个用 | 分隔）", "GPString", False)
        transformation = _p("transformation", "源坐标系到WGS84的基准转换（列表为空或坐标系相同则留空）", "GPString", False)
        limit = _p("max_results", "最多读取结果数（高德单次查询最多200条）", "GPLong", default=200)
        limit.filter.type = "Range"
        limit.filter.list = [1, 200]
        return [aoi, keywords, types, transformation, limit,
                _p("out_folder", "新建POI参考成果目录（拒绝覆盖）", "DEFolder", direction="Output", default=_folder("POI参考")),
                _out("output", "POI参考点图层")]

    def updateParameters(self, parameters):
        if not parameters[0].valueAsText or parameters[3].altered:
            return
        try:
            source_sr = arcpy.Describe(parameters[0].valueAsText).spatialReference
            if source_sr and source_sr.name != "Unknown":
                parameters[3].filter.list = arcpy.ListTransformations(source_sr, arcpy.SpatialReference(4326))
        except (AttributeError, OSError, RuntimeError, ValueError):
            parameters[3].filter.list = []

    def execute(self, parameters, messages):
        result = _poi_core().search_poi_reference(
            parameters[0].valueAsText,
            parameters[1].valueAsText or "",
            parameters[2].valueAsText or "",
            parameters[3].valueAsText or "",
            int(parameters[4].value),
            parameters[5].valueAsText)
        arcpy.SetParameterAsText(6, result["output"])
        messages.addMessage("高德 POI 请求 {} 条，范围内保留 {} 条，去重后输出 {} 条。".format(
            result["fetched_count"], result["inside_count"], result["output_count"]))
        if result["truncated"]:
            messages.addWarningMessage("结果达到查询上限，当前图层可能不完整；请缩小项目范围或调整关键词后分批检索。")
        messages.addWarningMessage("位置由 GCJ-02 近似反算到 WGS84，仅作位置参考，不能用于测绘、抽样定位、验收或报件。")
        messages.addMessage("成果目录：" + result["out_folder"])
