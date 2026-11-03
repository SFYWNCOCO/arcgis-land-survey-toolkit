# -*- coding: utf-8 -*-
"""ArcGIS Pro toolbox for supplementary cultivated-land quality acceptance."""
import os
import sys
import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ["SCQA_CORE_ROOT"] = ROOT
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Toolbox:
    def __init__(self):
        self.label = "补充耕地质量验收"
        self.alias = "scqa"
        self.tools = [CreateProject, InspectVector, ImportVector, BatchImportVector, InspectFieldSurveyBundle, ImportCAD, ImportRTK, CompositeSampling, CompositeSamplingQA, BulkDensitySampling, BulkDensitySamplingQA, Sentinel2CatalogSearch, PrepareSentinel2Pair, RemoteSensingScreening, QualityCheck, DeliveryCheck, RasterPackageAudit, ThematicRasterSummary, ImageryAccuracyCheck]


class _Base:
    canRunInBackground = False

    def getParameterInfo(self):
        return self.params()

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return


def _p(name, display, datatype, direction="Input", required="Required", default=None):
    p = arcpy.Parameter(displayName=display, name=name, datatype=datatype, parameterType=required, direction=direction)
    if default is not None:
        p.value = default
    return p


def _add_footprint_coverage(arcpy, groups, aoi_geometry):
    """Compute geometric footprint coverage; this is not valid-pixel coverage."""
    aoi_area = float(aoi_geometry.getArea("GEODESIC", "SQUAREMETERS"))
    if not aoi_area > 0:
        raise ValueError("AOI 面积无效，无法计算候选景足迹覆盖率。")
    for group in groups:
        footprint = None
        for item in group.get("items", []):
            geometry = item.get("geometry")
            if not geometry:
                continue
            candidate = arcpy.AsShape(geometry, False)
            footprint = candidate if footprint is None else footprint.union(candidate)
        if footprint is None:
            group["geometric_footprint_coverage_pct"] = 0.0
            continue
        overlap = aoi_geometry.intersect(footprint, 4)
        area = float(overlap.getArea("GEODESIC", "SQUAREMETERS")) if overlap and overlap.pointCount else 0.0
        group["geometric_footprint_coverage_pct"] = round(min(100.0, max(0.0, area / aoi_area * 100.0)), 4)
        group["coverage_interpretation"] = "仅为目录足迹几何面积覆盖；不证明影像有效像元、无云或SCL覆盖。"


def _write_intake_log(gdb, name, operation, result):
    import datetime
    import uuid
    from acceptance.ingest import write_intake_manifest
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(gdb), "{}_{}_{}_{}.json".format(name, operation, stamp, uuid.uuid4().hex[:8]))
    return write_intake_manifest(path, {"operation": operation, "result": result})


class CreateProject(_Base):
    label = "01 新建验收项目与数据库"
    description = "按附录I建立目录及附录G空库，并记录提交层级、项目类型、污染报告适用性和复评条件。选择依据须来自项目资料。"

    def params(self):
        level = _p("delivery_level", "成果提交层级", "GPString")
        level.filter.list = ["县级初鉴", "市级最终评价"]
        project_type = _p("project_type", "项目类型", "GPString")
        project_type.filter.list = ["项目类", "非项目类"]
        pollution = _p("pollution_report_required", "是否要求土壤污染状况检测报告", "GPString")
        pollution.filter.list = ["是", "否"]
        reevaluation = _p("reevaluation", "是否复评", "GPString")
        reevaluation.filter.list = ["是", "否"]
        return [
            _p("root", "输出根目录", "DEFolder", default=os.path.expanduser("~")),
            _p("county_code", "县级行政区划代码（6位）", "GPString"),
            _p("county_name", "县名称", "GPString"),
            _p("project_id", "项目编号", "GPString"),
            _p("wkid", "项目确认坐标系 WKID", "GPLong"),
            level, project_type, pollution, reevaluation,
        ]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.project import create_project
        level = {"县级初鉴": "county_initial", "市级最终评价": "city_final"}[parameters[5].valueAsText]
        project_type = {"项目类": "project", "非项目类": "non_project"}[parameters[6].valueAsText]
        pollution = parameters[7].valueAsText == "是"
        reevaluation = parameters[8].valueAsText == "是"
        result = create_project(
            parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, parameters[3].valueAsText,
            int(parameters[4].value), delivery_level=level,
            project_type=project_type, pollution_report_required=pollution,
            reevaluation=reevaluation,
        )
        messages.addMessage("已建立新工作项目：" + result["project"])
        messages.addMessage("交付清单：" + result["delivery_manifest"])
        messages.addMessage("成果层级/项目类型/污染报告适用性/复评条件已记录；交付人工复核项初始均为未完成。源图斑尚未导入，请核查 CAD/RTK 坐标基准和项目资料。")


class InspectVector(_Base):
    label = "02 检查矢量文件与坐标系"
    description = "读取矢量数据类型、要素数、几何和坐标系，不改写源数据。"

    def params(self):
        return [_p("source", "矢量数据/要素类", "DEFeatureClass")]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.ingest import inspect_vector
        info = inspect_vector(parameters[0].valueAsText)
        for key, value in info.items():
            messages.addMessage("{}: {}".format(key, value))


class InspectFieldSurveyBundle(_Base):
    label = "外业踏勘 ZIP 一致性检查"
    description = "只读检查踏勘 ZIP 中的 GeoJSON/Shapefile 点数据、坐标来源、跨格式属性冲突和照片引用；输出 JSON 检查报告，不修改或解压源包，也不代替业务验收。"

    def params(self):
        source = _p("source_zip", "外业踏勘 ZIP 包", "DEFile")
        source.filter.list = ["zip"]
        report = _p("report_json", "新建检查报告 JSON（不得覆盖已有文件）", "DEFile", direction="Output")
        return [source, report]

    def updateMessages(self, parameters):
        source = parameters[0].valueAsText
        report = parameters[1].valueAsText
        if source and report and os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(report)):
            parameters[1].setErrorMessage("检查报告必须另存为新 JSON 文件，不能指向源 ZIP。")
        if report and os.path.splitext(report)[1].casefold() != ".json":
            parameters[1].setErrorMessage("检查报告文件扩展名须为 .json。")

    def execute(self, parameters, messages):
        import arcpy
        from acceptance.field_survey_bundle import inspect_field_survey_bundle

        source = parameters[0].valueAsText
        report_path = parameters[1].valueAsText
        result = inspect_field_survey_bundle(source, report_path)
        counts = {}
        for finding in result.get("findings", []):
            severity = finding.get("severity")
            counts[severity] = counts.get(severity, 0) + 1
            message = "{}: {}".format(finding.get("code", "检查项"), finding.get("message", ""))
            if severity == "FAIL":
                messages.addErrorMessage(message)
            elif severity == "REVIEW":
                messages.addWarningMessage(message)
            else:
                messages.addMessage(message)
        messages.addMessage("结构检查状态：{}；业务验收状态：{}。".format(
            result.get("structural_status"), result.get("business_acceptance")))
        messages.addMessage("问题计数：{}；报告：{}".format(counts, result.get("report_path", report_path)))
        if result.get("status") == "FAIL":
            raise arcpy.ExecuteError


class ImportVector(_Base):
    label = "03 复制矢量到工作库"
    description = "接入点、线、面要素类或 GeoJSON/Esri JSON；混合几何按类型拆分，仅坐标系已确认时才投影。"

    def params(self):
        return [_p("source", "源矢量要素类（与 JSON 文件二选一）", "DEFeatureClass", required="Optional"), _p("json_source", "GeoJSON / Esri JSON 文件（与要素类二选一）", "DEFile", required="Optional"), _p("out_gdb", "目标 FileGDB", "DEWorkspace"), _p("name", "输出要素类名/前缀", "GPString"), _p("target_wkid", "目标 WKID（可空；须已确认）", "GPLong", required="Optional"), _p("source_wkid", "源元数据未知时填写源 WKID", "GPLong", required="Optional"), _p("transformation", "经资料确认的基准转换方法（可空）", "GPString", required="Optional")]

    def updateMessages(self, parameters):
        has_feature = bool(parameters[0].valueAsText)
        has_json = bool(parameters[1].valueAsText)
        if has_feature == has_json:
            parameters[0].setErrorMessage("矢量要素类和 GeoJSON/Esri JSON 文件必须且只能填写一个。")
            parameters[1].setErrorMessage("矢量要素类和 GeoJSON/Esri JSON 文件必须且只能填写一个。")

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.ingest import import_vector_copy
        source = parameters[0].valueAsText or parameters[1].valueAsText
        result = import_vector_copy(source, parameters[2].valueAsText, parameters[3].valueAsText, parameters[4].value, parameters[5].value, parameters[6].valueAsText)
        for output in result.get("outputs", [result["output"]]):
            messages.addMessage("已复制至：" + output)
        messages.addMessage("接入日志：" + _write_intake_log(parameters[2].valueAsText, parameters[3].valueAsText, "vector", result))
        messages.addMessage("源数据保持不变；检查转换参数和字段语义后再纳入正式成果。")


class BatchImportVector(_Base):
    label = "03B 文件夹矢量批量接入"
    description = "批量复制文件夹内的 SHP、GeoJSON、Esri JSON 和 FileGDB 要素类到新工作库；逐项核对数量并保留各源坐标系，不修改源数据，也不自动重投影。"

    def params(self):
        folder = os.path.join(os.path.expanduser("~"), "Documents", "ArcGIS",
                              "矢量批量接入_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        outputs = _p("outputs", "成功接入的要素类", "DEFeatureClass", direction="Output", required="Derived")
        outputs.multiValue = True
        return [_p("source_folder", "矢量源文件夹（含 SHP/JSON 或 FileGDB）", "DEFolder"),
                _p("recursive", "递归扫描子文件夹", "GPBoolean", default=True),
                _p("out_folder", "新建成果目录（拒绝覆盖）", "DEFolder", direction="Output", default=folder),
                _p("output_gdb", "批量接入工作库", "DEWorkspace", direction="Output", required="Derived"),
                outputs,
                _p("manifest", "逐源接入清单 JSON", "DEFile", direction="Output", required="Derived")]

    def execute(self, parameters, messages):
        import arcpy
        from acceptance.ingest import import_vector_folder
        result = import_vector_folder(parameters[0].valueAsText, parameters[2].valueAsText,
                                      bool(parameters[1].value))
        arcpy.SetParameterAsText(3, result["output_gdb"])
        if result["outputs"]:
            arcpy.SetParameterAsText(4, ";".join(result["outputs"]))
        arcpy.SetParameterAsText(5, result["manifest"])
        messages.addMessage("发现 {} 个矢量源，成功 {} 个，失败 {} 个。".format(
            result["source_count"], result["success_count"], result["failure_count"]))
        if result["failure_count"]:
            failures = [item for item in result.get("items", []) if item.get("status") == "FAIL"]
            for item in failures[:8]:
                messages.addWarningMessage("接入失败：{}；{}".format(os.path.basename(item["source"]), item.get("error", "详见清单")))
            messages.addWarningMessage("失败项完整原因见清单：" + result["manifest"])
        messages.addMessage("工作库：" + result["output_gdb"])
        messages.addMessage("源数据保持不变；输出各层保留原坐标系，需转换时再按项目资料明确设置。")
        if result["success_count"] == 0:
            messages.addErrorMessage("没有矢量源成功接入；完整扫描和失败记录已写入清单。")
            raise arcpy.ExecuteError


class ImportCAD(_Base):
    label = "04 CAD 转入工作库"
    description = "支持 DWG/DXF/DGN。source WKID 只定义坐标参考，不代表完成配准；须检查 PRJ/WLD/world 配套材料，并在 Georeference CAD 中核验控制点与残差。"

    def params(self):
        return [_p("cad", "CAD 文件", "DEFile"), _p("out_gdb", "目标 FileGDB", "DEWorkspace"), _p("dataset", "输出要素数据集名", "GPString", default="CAD_Imported"), _p("source_wkid", "经资料核实的 CAD 源 WKID", "GPLong"), _p("scale", "参考比例尺", "GPLong", default=1000)]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.ingest import import_cad
        result = import_cad(parameters[0].valueAsText, parameters[1].valueAsText, parameters[2].valueAsText, int(parameters[3].value), int(parameters[4].value))
        messages.addMessage("CAD 转换状态：{}；输出数据集：{}；要素类数：{}；总要素数：{}".format(result["status"], result["output_dataset"], len(result["feature_classes"]), result["feature_count"]))
        messages.addMessage("源文件SHA-256：" + result["source_files"][0]["sha256"])
        for item in result["feature_classes"]:
            messages.addMessage("{} [{}]：{} 个要素".format(item["name"], item["shape_type"], item["count"]))
        if result["empty_reason"]:
            messages.addErrorMessage(result["empty_reason"])
        messages.addMessage("配准材料文件：" + ("、".join(result["registration_materials"]) if result["registration_materials"] else "未发现 PRJ/WLD/world 配套文件"))
        messages.addMessage("配准状态：" + result["registration_status"] + "；source WKID 不是配准证据")
        messages.addMessage("接入日志：" + _write_intake_log(parameters[1].valueAsText, parameters[2].valueAsText, "cad", result))
        messages.addWarningMessage(result["review"])


class ImportRTK(_Base):
    label = "05 RTK CSV 建点与投影"
    description = "逐行预检有限数值与地理坐标范围；必须选择 XY/YX 字段映射。原坐标点按源 WKID 创建，隔离输出坐标系环境后仅执行一次显式投影；结果仍需人工核验。"

    def params(self):
        params = [_p("csv", "RTK CSV 原始记录", "DEFile"), _p("out_gdb", "目标 FileGDB", "DEWorkspace"), _p("name", "输出名称前缀", "GPString", default="RTK"), _p("x_field", "字段1（XY时为X，YX时为Y）", "GPString"), _p("y_field", "字段2（XY时为Y，YX时为X）", "GPString"), _p("axis_order", "字段轴序映射（必须明确选择）", "GPString"), _p("source_wkid", "经测量资料确认的源 WKID", "GPLong"), _p("target_wkid", "项目目标 WKID", "GPLong"), _p("z_field", "Z 字段（可空）", "GPString", required="Optional"), _p("transformation", "经资料确认的基准转换方法（可空）", "GPString", required="Optional")]
        params[5].filter.type = "ValueList"
        params[5].filter.list = ["XY", "YX"]
        return params

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.ingest import import_rtk_csv
        result = import_rtk_csv(parameters[0].valueAsText, parameters[1].valueAsText, parameters[2].valueAsText, parameters[3].valueAsText, parameters[4].valueAsText, int(parameters[6].value), int(parameters[7].value), parameters[8].valueAsText or None, parameters[9].valueAsText, parameters[5].valueAsText)
        messages.addMessage("原坐标点：" + result["raw_points"])
        messages.addMessage("投影点：" + result["projected_points"])
        messages.addMessage("字段映射：X={}；Y={}；Z={}；轴序={}；预检行数={}".format(result["field_mapping"]["X"], result["field_mapping"]["Y"], result["field_mapping"]["Z"] or "未指定", result["axis_order"], result["row_count"]))
        messages.addMessage("CSV格式：编码={}；分隔符={}".format(result["csv_format"]["encoding"], result["csv_format"]["delimiter"]))
        messages.addMessage("坐标范围：{}；原坐标 WKID={}；投影 WKID={}；空几何={}".format(result["coordinates"], result["raw_output_wkid"], result["projected_output_wkid"], result["null_geometry_count"]))
        messages.addMessage("RTK源文件SHA-256：{}；Z字段={}；垂直基准状态={}".format(result["source_files"][0]["sha256"], result["z_field"] or "未提供", result["vertical_reference_status"]))
        if result.get("snapshot_cleanup_warning"):
            messages.addWarningMessage(result["snapshot_cleanup_warning"])
        messages.addMessage("接入日志：" + _write_intake_log(parameters[1].valueAsText, parameters[2].valueAsText, "rtk", result))
        messages.addWarningMessage(result["review"])


class CompositeSampling(_Base):
    label = "06 混合样分样点设计"
    description = "按指南6.1.4和6.2.4.3(1)生成可编辑的最低候选点；必须输入评价单元面核对图斑归属。附录E样品编号等于评价单元编号，POINT_ID另行区分，记录0–20cm设计深度；实际数量、网格均匀性、避边和现场位置由专业人员复核。"

    def params(self):
        params = [_p("parcels", "已复核的图斑面", "DEFeatureClass"),
                  _p("unit_field", "图斑评价单元编号字段", "Field"),
                  _p("parcel_field", "图斑编号字段", "Field"),
                  _p("units", "评价单元面图层", "DEFeatureClass"),
                  _p("units_field", "评价单元编号字段", "Field"),
                  _p("out_gdb", "输出 FileGDB", "DEWorkspace"),
                  _p("name", "输出要素类名", "GPString", default="混合样分样点_设计")]
        params[1].parameterDependencies = [params[0].name]
        params[2].parameterDependencies = [params[0].name]
        params[4].parameterDependencies = [params[3].name]
        return params

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.sampling import composite_candidates
        result = composite_candidates(parameters[0].valueAsText, parameters[1].valueAsText,
                                      parameters[2].valueAsText, parameters[5].valueAsText,
                                      parameters[6].valueAsText, parameters[3].valueAsText,
                                      parameters[4].valueAsText)
        messages.addMessage("生成最低布点候选 {} 个，涉及 {} 个评价单元：{}；SAMPLE_ID=评价单元编号，POINT_ID区分设计点，DEPTH_TOP/DEPTH_BOT=0/20cm。".format(result["count"], result["units"], result["output"]))
        for unit in result.get("unit_checks", []):
            if unit["generated"] < unit["required"]:
                messages.addWarningMessage("评价单元 {} 设计候选点不足：要求至少 {}，当前 {}。".format(unit["unit_id"], unit["required"], unit["generated"]))
        messages.addWarningMessage("此处点数分配只是最低候选，不代表实际布点数量或均匀网格已满足规范；须按图斑面积、地形、补充耕地来源及现场条件复核，并记录小图斑间距例外依据。")
        messages.addWarningMessage("运行状态为 NEEDS_REVIEW。所有点均是设计点，须人工复核图斑覆盖、田边避让、均匀性、代表性和现场可达性。")


class BulkDensitySampling(_Base):
    label = "07 容重平行样设计"
    description = "按指南6.2.4.2–6.2.4.3(2)生成每单元3个容重设计点；附录E编号为RZ+评价单元编号，平行样序号单独记录，100cm³环刀。"

    def params(self):
        params = [_p("profile", "质地构型调查点", "DEFeatureClass"),
                  _p("unit_field", "调查点评价单元编号字段", "Field"),
                  _p("units", "评价单元面图层", "DEFeatureClass"),
                  _p("units_field", "评价单元编号字段", "Field"),
                  _p("out_gdb", "输出 FileGDB", "DEWorkspace"),
                  _p("name", "输出要素类名", "GPString", default="容重平行样_设计")]
        params[1].parameterDependencies = [params[0].name]
        params[3].parameterDependencies = [params[2].name]
        return params

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.sampling import bulk_density_candidates
        result = bulk_density_candidates(
            parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[4].valueAsText, parameters[5].valueAsText,
            parameters[2].valueAsText, parameters[3].valueAsText,
        )
        messages.addMessage("候选容重样点 {} 个；样品编号RZ+评价单元编号，REP_NO区分平行样：{}".format(result["count"], result["output"]))
        for item in result.get("unit_checks", []):
            if item["generated"] != item["required"]:
                messages.addWarningMessage("评价单元 {} 无法生成完整容重候选点组，须现场重新选点。".format(item["unit_id"]))
        messages.addWarningMessage("输出是设计点；已检查候选点位于对应评价单元内，仍需现场核对土层、环刀取样及点位适用性。")


class BulkDensitySamplingQA(_Base):
    label = "容重平行样设计点空间质检"
    description = "核查每评价单元3个平行样、唯一序号、与质地构型调查点的2米范围及平行点2米间距；输出报告，现场适用性仍须人工确认。"

    def params(self):
        profile = _p("profile", "质地构型调查点（每评价单元1点）", "GPFeatureLayer")
        units = _p("units", "评价单元面（用于点位归属核查）", "GPFeatureLayer")
        points = _p("points", "容重平行样设计点（仅DESIGN_ONLY）", "GPFeatureLayer")
        profile_unit = _p("profile_unit_field", "调查点评价单元编号字段", "Field")
        unit_id = _p("unit_field", "评价单元编号字段", "Field")
        point_unit = _p("point_unit_field", "设计点评价单元编号字段", "Field")
        sample_id = _p("sample_id_field", "样品编号字段（RZ+评价单元编号）", "Field")
        replicate = _p("replicate_field", "平行样序号字段", "Field")
        point_status = _p("point_status_field", "设计状态字段（值必须为DESIGN_ONLY）", "Field")
        profile_unit.parameterDependencies = [profile.name]
        unit_id.parameterDependencies = [units.name]
        for parameter in (point_unit, sample_id, replicate, point_status):
            parameter.parameterDependencies = [points.name]
        return [profile, units, points, profile_unit, unit_id, point_unit, sample_id, replicate, point_status,
                _p("out_json", "新建质检报告JSON", "DEFile", "Output")]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.sampling_qa import check_bulk_density_design
        result = check_bulk_density_design(
            parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, parameters[3].valueAsText,
            parameters[4].valueAsText, parameters[5].valueAsText,
            parameters[6].valueAsText, parameters[7].valueAsText,
            parameters[8].valueAsText, parameters[9].valueAsText,
        )
        messages.addMessage("容重设计点空间质检报告：{}".format(result["output_json"]))
        messages.addMessage("总体状态：{}；平行点间距：{}。".format(
            result["status"], result["spacing_status"]))
        messages.addWarningMessage("土层适用性、田块位置、现场可操作性和真实采样事实须由采样人员复核。")


class CompositeSamplingQA(_Base):
    label = "07 混合样设计点空间质检"
    description = "复核混合样编号等于评价单元编号、评价单元/图斑/点位空间关联、有效设计点数量和一般15米间距；小图斑例外、网格均匀性及避边仍须人工审核。"

    def params(self):
        parcels = _p("parcels", "已复核的图斑面", "GPFeatureLayer")
        units = _p("units", "评价单元面（用于图斑和点位归属核查）", "GPFeatureLayer")
        point_fc = _p("points", "混合样设计点（仅DESIGN_ONLY）", "GPFeatureLayer")
        parcel_unit = _p("parcel_unit_field", "图斑评价单元编号字段", "Field")
        parcel_id = _p("parcel_id_field", "图斑编号字段", "Field")
        point_unit = _p("point_unit_field", "设计点评价单元编号字段", "Field")
        point_parcel = _p("point_parcel_field", "设计点图斑编号字段", "Field")
        point_id = _p("point_id_field", "设计点编号字段", "Field")
        sample_id = _p("sample_id_field", "混合样样品编号字段（须等于评价单元编号）", "Field")
        point_status = _p("point_status_field", "设计状态字段（值必须为DESIGN_ONLY）", "Field")
        for parameter in (parcel_unit, parcel_id):
            parameter.parameterDependencies = [parcels.name]
        units_field = _p("unit_field", "评价单元编号字段", "Field")
        units_field.parameterDependencies = [units.name]
        for parameter in (point_unit, point_parcel, point_id, sample_id, point_status):
            parameter.parameterDependencies = [point_fc.name]
        return [
            parcels, units, point_fc, parcel_unit, parcel_id, units_field, point_unit, point_parcel,
            point_id, sample_id, point_status,
            _p("out_json", "新建质检报告JSON", "DEFile", "Output"),
            _p("spacing_reviews_json", "间距不足例外审核记录JSON（可选；仍需人工判定）", "DEFile", required="Optional"),
        ]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.sampling_qa import check_composite_design
        result = check_composite_design(
            parameters[0].valueAsText, parameters[1].valueAsText, parameters[2].valueAsText,
            parameters[3].valueAsText, parameters[4].valueAsText, parameters[5].valueAsText,
            parameters[6].valueAsText, parameters[7].valueAsText, parameters[8].valueAsText,
            parameters[9].valueAsText, parameters[10].valueAsText, parameters[11].valueAsText,
            spacing_exception_reviews_json=parameters[12].valueAsText,
        )
        messages.addMessage("混合样设计点空间质检报告：{}".format(result["output_json"]))
        messages.addMessage("总体状态：{}；机械下限/空间关联已核对。".format(result["status"]))
        messages.addWarningMessage("均匀性、田边避让、现场可达性及15米例外须依据现场资料人工复核。")


class Sentinel2CatalogSearch(_Base):
    label = "08 Sentinel-2 目录检索（联网）"
    description = "显式向 Earth Search 提交评价区几何与日期范围，检索 Sentinel-2 C1 L2A 景并按日期/平台/处理基线分组；不自动选景或判定验收。"

    def params(self):
        params = [
            _p("aoi", "评价区/评价单元面（将转为 WGS84 提交 Earth Search）", "GPFeatureLayer"),
            _p("start_date", "起始日期 YYYY-MM-DD", "GPString"),
            _p("end_date", "结束日期 YYYY-MM-DD", "GPString"),
            _p("max_cloud", "候选景最大云量（%）", "GPDouble", default=80),
            _p("out_json", "候选结果 JSON（新文件）", "DEFile", "Output"),
            _p("send_aoi", "我已知评价区范围会发送到 Earth Search 公共目录", "GPBoolean", default=False),
        ]
        params[0].filter.list = ["Polygon"]
        return params

    def execute(self, parameters, messages):
        import csv
        import hashlib
        import json
        import os
        import sys
        import tempfile
        import arcpy
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        if not bool(parameters[5].value):
            raise ValueError("请确认 AOI 几何会发送至 Earth Search 后再运行目录检索。")
        aoi = parameters[0].valueAsText
        desc = arcpy.Describe(aoi)
        if str(getattr(desc, "shapeType", "")).casefold() != "polygon":
            raise ValueError("目录检索仅接受面要素。")
        source_sr = getattr(desc, "spatialReference", None)
        if source_sr is None or (not getattr(source_sr, "factoryCode", 0) and str(getattr(source_sr, "name", "Unknown")).casefold() == "unknown"):
            raise ValueError("评价区坐标系未知，不能生成可靠的 Earth Search 检索范围。")
        geometries = []
        with arcpy.da.SearchCursor(aoi, ["SHAPE@"] ) as rows:
            for (geometry,) in rows:
                if geometry and int(getattr(geometry, "pointCount", 0)) > 0:
                    geometries.append(geometry)
        if not geometries:
            raise ValueError("评价区图层没有有效面几何。")
        merged = geometries[0]
        for geometry in geometries[1:]:
            merged = merged.union(geometry)
        wgs84 = arcpy.SpatialReference(4326)
        projected = merged.densify("DISTANCE", 10.0, 0.0).projectAs(wgs84)
        geojson = projected.__geo_interface__
        from acceptance.stac import group_candidates, search_items
        result = search_items(
            geojson, parameters[1].valueAsText, parameters[2].valueAsText,
            max_cloud_cover=parameters[3].value,
        )
        aoi_json = json.dumps(geojson, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups = group_candidates(result["items"])
        _add_footprint_coverage(arcpy, groups, projected)
        result.update({
            "schema_version": "1.0",
            "aoi_sha256": hashlib.sha256(aoi_json.encode("utf-8")).hexdigest(),
            "aoi_source": os.path.abspath(aoi),
            "aoi_source_crs": {"name": source_sr.name, "wkid": int(getattr(source_sr, "factoryCode", 0) or 0), "wkt": source_sr.exportToString()},
            "aoi_wgs84": geojson,
            "groups": groups,
        })
        output = os.path.abspath(parameters[4].valueAsText)
        if os.path.exists(output):
            raise ValueError("输出 JSON 已存在；请选择新文件，工具不会覆盖已有结果。")
        parent = os.path.dirname(output)
        if not os.path.isdir(parent):
            raise ValueError("输出目录不存在：" + parent)
        fd, temporary = tempfile.mkstemp(prefix=".scqa-stac-", suffix=".tmp", dir=parent)
        os.close(fd)
        try:
            with open(temporary, "w", encoding="utf-8", newline="") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        if result["search_complete"]:
            messages.addMessage("目录检索完成：{} 页、{} 景、{} 个同日期组。".format(result["pages"], len(result["items"]), len(result["groups"])))
        else:
            messages.addWarningMessage("目录检索未完整：{}；当前结果不能用于影像准备。".format(result["page_error"]))
        messages.addMessage("候选清单：" + output)
        messages.addMessage("AOI SHA-256：" + result["aoi_sha256"])
        messages.addWarningMessage("景级云量和足迹相交不能替代 AOI 像元覆盖/云影检查；日期由项目人员选择。AOI 几何已发送至 Earth Search 公共目录。")


class PrepareSentinel2Pair(_Base):
    label = "09 选景、镶嵌并筛查 Sentinel-2 时相"
    description = "按人工选择的目录日期组镶嵌相邻瓦片、裁切评价区并检查网格/像元预算，然后运行 NDVI+SCL 单元筛查。结果只作现场复核线索。"

    def params(self):
        params = [
            _p("catalog_json", "完整 Sentinel-2 目录结果 JSON", "DEFile"),
            _p("aoi", "与目录检索相同的评价区面（用于裁切/指纹核对）", "GPFeatureLayer"),
            _p("before_group", "前期日期组（候选项）", "GPString"),
            _p("after_group", "后期日期组（候选项）", "GPString"),
            _p("units", "评价单元面（按单元统计）", "GPFeatureLayer"),
            _p("unit_field", "评价单元编号字段", "Field"),
            _p("output_root", "新结果保存目录", "DEFolder"),
            _p("allow_mixed", "确认前后期卫星平台或处理基线不一致", "GPBoolean", default=False),
            _p("max_pixels", "10米栅格像元预算（上限1500万）", "GPLong", default=15000000),
        ]
        params[1].filter.list = ["Polygon"]
        params[4].filter.list = ["Polygon"]
        params[5].parameterDependencies = [params[4].name]
        return params

    def updateParameters(self, parameters):
        import json
        import os
        catalog = parameters[0].valueAsText
        if not catalog or not os.path.isfile(catalog):
            return
        try:
            with open(catalog, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            if data.get("search_complete") is not True:
                parameters[2].filter.list = []
                parameters[3].filter.list = []
                return
            from acceptance.sentinel_prepare import candidate_key
            candidates = [candidate_key(group) for group in data.get("groups", [])]
            parameters[2].filter.list = candidates
            parameters[3].filter.list = candidates
        except Exception:
            parameters[2].filter.list = []
            parameters[3].filter.list = []

    def execute(self, parameters, messages):
        import hashlib
        import json
        import os
        import sys
        import shutil
        import arcpy
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        aoi, units = parameters[1].valueAsText, parameters[4].valueAsText
        desc = arcpy.Describe(aoi)
        unit_desc = arcpy.Describe(units)
        if str(getattr(desc, "shapeType", "")).casefold() != "polygon" or str(getattr(unit_desc, "shapeType", "")).casefold() != "polygon":
            raise ValueError("裁切 AOI 和评价单元都必须是面图层。")
        sr = getattr(desc, "spatialReference", None)
        if sr is None or str(getattr(sr, "type", "")).casefold() != "projected" or str(getattr(sr, "linearUnitName", "")).casefold() not in ("meter", "metre", "meters", "metres"):
            raise ValueError("AOI 必须使用以米为单位的已知投影坐标系；不会猜测或静默赋坐标。")
        geometries = []
        with arcpy.da.SearchCursor(aoi, ["SHAPE@"] ) as rows:
            for (geometry,) in rows:
                if geometry and int(getattr(geometry, "pointCount", 0)) > 0:
                    geometries.append(geometry)
        if not geometries:
            raise ValueError("AOI 图层没有有效面几何。")
        merged = geometries[0]
        for geometry in geometries[1:]:
            merged = merged.union(geometry)
        geojson = merged.densify("DISTANCE", 10.0, 0.0).projectAs(arcpy.SpatialReference(4326)).__geo_interface__
        canonical = json.dumps(geojson, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        aoi_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        extent = merged.extent
        from acceptance.sentinel_prepare import estimate_pixel_count, prepare_pair
        max_pixels = int(parameters[8].value)
        estimated = estimate_pixel_count((extent.XMin, extent.YMin, extent.XMax, extent.YMax))
        messages.addMessage("AOI 保守像元估算：{}；预算：{}。".format(estimated, max_pixels))
        last_reported = {"stage": None, "percent": -10}

        def report_warp_progress(stage, percent):
            if stage != last_reported["stage"] or percent >= 100 or percent - last_reported["percent"] >= 10:
                messages.addMessage("Sentinel 影像准备进度 {}：{}%。".format(stage, percent))
                last_reported.update({"stage": stage, "percent": percent})

        result = prepare_pair(
            parameters[0].valueAsText, parameters[2].valueAsText, parameters[3].valueAsText,
            aoi_hash, geojson, sr.exportToString(), parameters[6].valueAsText,
            allow_platform_mismatch=bool(parameters[7].value), max_pixels=max_pixels,
            estimated_pixels=estimated, progress=report_warp_progress,
        )
        products = result["products"]
        try:
            from acceptance.remote_sensing import run_ndvi_screening
            before, after = result["before"], result["after"]
            report = run_ndvi_screening(
                products["before"]["red"]["path"], products["before"]["nir"]["path"],
                products["after"]["red"]["path"], products["after"]["nir"]["path"],
                units, parameters[5].valueAsText, result["run_dir"],
                before["acquisition_date"], after["acquisition_date"],
                "Earth Search Sentinel-2 C1 L2A: " + before["platform"] + " " + ",".join(before["tiles"]),
                "Earth Search Sentinel-2 C1 L2A: " + after["platform"] + " " + ",".join(after["tiles"]),
                products["before"]["red"]["scale"], products["before"]["red"]["offset"],
                products["after"]["red"]["scale"], products["after"]["red"]["offset"],
                False, products["before"]["scl"]["path"], products["after"]["scl"]["path"],
            )
            report_path = report["outputs"]["json"]
            with open(report_path, "r", encoding="utf-8") as stream:
                evidence = json.load(stream)
            evidence["sentinel2_preparation_manifest"] = result["manifest"]
            with open(report_path, "w", encoding="utf-8") as stream:
                json.dump(evidence, stream, ensure_ascii=False, indent=2)
        except BaseException:
            shutil.rmtree(result["run_dir"], ignore_errors=True)
            raise
        messages.addMessage("本地影像镶嵌/裁切完成：{} 个10米像元。".format(result["pixel_count"]))
        messages.addMessage("NDVI筛查状态：NEEDS_FIELD_REVIEW（不是验收结论）。")
        messages.addMessage("结果目录：" + result["run_dir"])
        messages.addMessage("影像来源/网格/校验值：" + result["manifest"])
        messages.addMessage("按评价单元汇总：" + report["outputs"]["csv"])
        messages.addWarningMessage(report["review_note"])


class RemoteSensingScreening(_Base):
    label = "08 遥感时相筛查（NDVI）"
    description = "对前后期红光/NIR影像计算NDVI差值；可输入两期SCL自动排除云影等类别，或确认数据已完成云掩膜。按评价单元汇总，仅作变化线索。"

    def params(self):
        params = [
            _p("red_before", "前期红光单波段栅格", "GPRasterLayer"),
            _p("nir_before", "前期近红外单波段栅格", "GPRasterLayer"),
            _p("red_after", "后期红光单波段栅格", "GPRasterLayer"),
            _p("nir_after", "后期近红外单波段栅格", "GPRasterLayer"),
            _p("units", "评价单元面", "DEFeatureClass"),
            _p("unit_field", "评价单元编号字段", "Field"),
            _p("out_folder", "输出目录", "DEFolder"),
            _p("before_date", "前期影像日期 YYYY-MM-DD", "GPString"),
            _p("after_date", "后期影像日期 YYYY-MM-DD", "GPString"),
            _p("before_source", "前期数据源/产品/处理级别", "GPString"),
            _p("after_source", "后期数据源/产品/处理级别", "GPString"),
            _p("before_scale", "前期反射率比例因子（按元数据）", "GPDouble"),
            _p("before_offset", "前期反射率偏移量（按元数据）", "GPDouble"),
            _p("after_scale", "后期反射率比例因子（按元数据）", "GPDouble"),
            _p("after_offset", "后期反射率偏移量（按元数据）", "GPDouble"),
            _p("cloud_mask_confirmed", "无SCL时确认已完成两期云/阴影掩膜", "GPBoolean", default=False),
            _p("scl_before", "前期SCL场景分类栅格（可选，Sentinel-2推荐）", "GPRasterLayer", required="Optional"),
            _p("scl_after", "后期SCL场景分类栅格（可选，Sentinel-2推荐）", "GPRasterLayer", required="Optional"),
        ]
        params[5].parameterDependencies = [params[4].name]
        return params

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.remote_sensing import run_ndvi_screening
        result = run_ndvi_screening(
            parameters[0].valueAsText, parameters[1].valueAsText,
            parameters[2].valueAsText, parameters[3].valueAsText,
            parameters[4].valueAsText, parameters[5].valueAsText,
            parameters[6].valueAsText, parameters[7].valueAsText,
            parameters[8].valueAsText, parameters[9].valueAsText,
            parameters[10].valueAsText, parameters[11].value,
            parameters[12].value, parameters[13].value, parameters[14].value,
            bool(parameters[15].value), parameters[16].valueAsText,
            parameters[17].valueAsText,
        )
        messages.addMessage("遥感筛查状态：NEEDS_FIELD_REVIEW（不是验收结论）")
        messages.addMessage("NDVI变化栅格：" + result["outputs"]["delta"])
        messages.addMessage("评价单元汇总：" + result["outputs"]["csv"])
        messages.addMessage("来源与处理参数记录：" + result["outputs"]["json"])
        messages.addWarningMessage(result["review_note"])


class QualityCheck(_Base):
    label = "09 评价单元数据库质检"
    description = "检查附录G评价单元图层、字段合同、坐标系及每个评价单元50亩上限；超限列为FAIL，须按规范重新划分。"

    def params(self):
        return [_p("gdb", "项目 FileGDB", "DEWorkspace"), _p("wkid", "项目确认 WKID（可空）", "GPLong", required="Optional"), _p("report", "输出 JSON 质检报告", "DEFile", "Output")]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.qa import write_report
        output, report = write_report(parameters[0].valueAsText, parameters[2].valueAsText, parameters[1].value)
        messages.addMessage("评价单元结构初检报告：" + output)
        messages.addMessage("总状态：" + report["status"] + "（不等同完整验收通过）")
        for status, count in report["summary"].items():
            messages.addMessage("{}: {}".format(status, count))


class ImageryAccuracyCheck(_Base):
    label = "13 影像位置精度检核（RTK检查点残差）"
    description = "用检查点（RTK/控制点）与影像同名点计算平面残差，生成一页 HTML 精度记录与 JSON；结果只是计算记录，是否满足报备门槛由业务方确认。"

    def params(self):
        checks_layer = _p("checks_layer", "检查点图层（与影像点同一投影坐标系）", "GPFeatureLayer", required="Optional")
        images_layer = _p("images_layer", "影像同名点图层（在影像上采集）", "GPFeatureLayer", required="Optional")
        points = [_p("checks_csv", "检查点 CSV（替代图层；同坐标系米制）", "DEFile", required="Optional"),
                  _p("images_csv", "影像同名点 CSV（替代图层）", "DEFile", required="Optional"),
                  _p("checks_layer_id", "检查点图层编号字段（可空）", "Field", required="Optional"),
                  _p("images_layer_id", "影像点图层编号字段（可空）", "Field", required="Optional"),
                  _p("x_field", "CSV X 字段（米）", "GPString", required="Optional", default="X"),
                  _p("y_field", "CSV Y 字段（米）", "GPString", required="Optional", default="Y"),
                  _p("id_field", "CSV 编号字段", "GPString", required="Optional", default="ID"),
                  pairing := _p("pairing", "配对方式：按编号一一对应 或 按最近影像点", "GPString"),
                  _p("max_distance", "nearest 配对最大距离（米，可空）", "GPDouble", required="Optional"),
                  _p("imagery_name", "影像来源说明（写入记录）", "GPString", required="Optional"),
                  _p("imagery_gsd", "原始 GSD（米，可空）", "GPDouble", required="Optional"),
                  _p("out_html", "精度记录 HTML（新文件）", "DEFile", direction="Output"),
                  _p("out_json", "精度记录 JSON（新文件）", "DEFile", direction="Output")]
        pairing.filter.list = ["按编号一一对应", "按最近影像点"]
        pairing.value = "按编号一一对应"
        return [checks_layer, images_layer] + points

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if core_root and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.accuracy_check import build_record, load_points, read_csv
        checks_layer, images_layer = parameters[0].valueAsText, parameters[1].valueAsText
        checks_csv, images_csv = parameters[2].valueAsText, parameters[3].valueAsText
        x_field, y_field = parameters[6].valueAsText or "X", parameters[7].valueAsText or "Y"
        id_field = parameters[8].valueAsText or "ID"
        if checks_csv and images_csv:
            checks = read_csv(checks_csv, x_field, y_field, id_field)
            images = read_csv(images_csv, x_field, y_field, id_field)
            source = "CSV"
        elif checks_layer and images_layer and checks_layer.lower().endswith(".csv") and images_layer.lower().endswith(".csv"):
            # 图层控件里填 CSV 路径是常见误填；成对 CSV 直接按 CSV 读，但提示改用 CSV 参数
            import arcpy as _arcpy
            _arcpy.AddWarning("检测到在图层面输入了 CSV 路径，已按 CSV 读取；建议改用下方 CSV 参数。")
            checks = read_csv(checks_layer, x_field, y_field, id_field)
            images = read_csv(images_layer, x_field, y_field, id_field)
            source = "CSV（图层面误填）"
        elif checks_layer and images_layer:
            if not (parameters[4].valueAsText and parameters[5].valueAsText):
                raise ValueError("图层模式必须分别选择检查点与影像点的编号字段")
            checks = load_points(checks_layer, arcpy, parameters[4].valueAsText)
            images = load_points(images_layer, arcpy, parameters[5].valueAsText)
            source = "图层"
        else:
            raise ValueError("请成对提供检查点与影像点：CSV 两个文件，或两个图层及编号字段")
        max_distance = parameters[10].value
        pairing_label = parameters[9].valueAsText or "按编号一一对应"
        pairing = {"按编号一一对应": "id", "按最近影像点": "nearest"}.get(pairing_label)
        if pairing is None:
            raise ValueError("配对方式只能选“按编号一一对应”或“按最近影像点”：{}".format(pairing_label))
        context = {"影像": parameters[11].valueAsText or "未提供", "原始GSD_m": parameters[12].value,
                   "检查点文件": checks_csv or checks_layer, "影像点文件": images_csv or images_layer,
                   "配对方式": pairing_label, "检查点来源": source}
        payload = build_record(checks, images, context, parameters[13].valueAsText, parameters[14].valueAsText,
                               pairing=pairing, max_distance_m=max_distance)
        stats = payload["statistics"]
        messages.addMessage("精度记录：{}（{} 个检查点，RMSE={} m，状态 {}）".format(
            parameters[13].valueAsText, stats["检查点数"], stats["RMSE_m"], stats["状态"]))
        messages.addWarningMessage("同名点配对、影像时相与来源须人工复核；计算记录不自动证明满足报备精度门槛。")


class RasterPackageAudit(_Base):
    label = "11 遥感数据包预检"
    description = "只读扫描常见栅格文件、CRF目录和FileGDB栅格，列出逐波段NoData、像元、范围、坐标系/WKT及复核标记；不会猜测未知坐标系。"

    def params(self):
        return [_p("root", "遥感数据包目录（递归扫描）", "DEFolder"), _p("out_folder", "预检清单输出目录", "DEFolder")]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.raster_catalog import audit_raster_package
        report = audit_raster_package(parameters[0].valueAsText, parameters[1].valueAsText)
        messages.addMessage("栅格数：{}".format(report["summary"]["raster_count"]))
        for status, count in sorted(report["summary"]["status_counts"].items()):
            messages.addMessage("{}：{}".format(status, count))
        messages.addMessage("JSON：" + os.path.join(parameters[1].valueAsText, report["outputs"]["json"]))
        messages.addMessage("CSV：" + os.path.join(parameters[1].valueAsText, report["outputs"]["csv"]))
        messages.addWarningMessage("预检结果只帮助识别数据条件；不得将未知坐标系栅格直接用于采样/验收结论。")


class ThematicRasterSummary(_Base):
    label = "12 单期专题栅格评价单元摘要"
    description = "按评价单元统计同坐标系单波段连续专题栅格的有效像元覆盖、最小值、均值、中位数和标准差；不投影输入、不判质量等级。"

    def params(self):
        units = _p("units", "评价单元面", "GPFeatureLayer")
        unit_field = _p("unit_field", "唯一评价单元编号字段", "Field")
        unit_field.parameterDependencies = [units.name]
        max_cells = _p("max_cells", "最大处理像元数（内存保护）", "GPLong", default=5000000)
        return [
            _p("raster", "单波段连续专题栅格", "GPRasterLayer"),
            units,
            unit_field,
            _p("out_folder", "CSV/JSON输出目录（需预先存在）", "DEFolder"),
            max_cells,
        ]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.thematic_raster import summarize_continuous_raster
        result = summarize_continuous_raster(
            parameters[0].valueAsText,
            parameters[1].valueAsText,
            parameters[2].valueAsText,
            parameters[3].valueAsText,
            max_cells=parameters[4].value,
        )
        messages.addMessage("专题栅格 CRS 与评价单元已逐项核对一致；源栅格未改写。")
        messages.addMessage("处理单元数：{}；状态：NEEDS_FIELD_REVIEW（仅作核查线索）。".format(len(result["units"])))
        messages.addMessage("CSV：" + result["outputs"]["csv"])
        messages.addMessage("JSON：" + result["outputs"]["json"])
        if result["processing"]["raster_extent_may_not_cover_unit_extent"]:
            messages.addWarningMessage("栅格范围包络未完全覆盖评价单元范围；逐单元核实覆盖情况。")


class DeliveryCheck(_Base):
    label = "10 汇交成果逐项预检（非验收结论）"
    description = "依据项目交付清单检查10.1–10.4必交材料、FileGDB附录G结构和人工复核状态；图件/报告的内容、比例尺和签章须人工核验。"

    def params(self):
        from acceptance.delivery import MANUAL_REVIEW_LABELS
        reviewed = _p("manual_reviews", "本次已逐项人工核验的内容（可多选）", "GPString", required="Optional")
        reviewed.multiValue = True
        reviewed.filter.list = list(MANUAL_REVIEW_LABELS.values())
        return [
            _p("root", "汇交项目目录", "DEFolder"),
            _p("report", "输出 JSON 检查报告", "DEFile", "Output"),
            reviewed,
        ]

    def updateParameters(self, parameters):
        import json, os
        from acceptance.delivery import MANUAL_REVIEW_LABELS, MANUAL_REVIEW_KEYS, PROJECT_REVIEW_KEYS, REEVALUATION_REVIEW_KEYS
        root = parameters[0].valueAsText
        if not root:
            return
        path = os.path.join(root, "delivery_manifest.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except Exception:
            return
        keys = list(MANUAL_REVIEW_KEYS)
        if manifest.get("project_type") == "project":
            keys.extend(PROJECT_REVIEW_KEYS)
        if manifest.get("reevaluation") is True:
            keys.extend(REEVALUATION_REVIEW_KEYS)
        parameters[2].filter.list = [MANUAL_REVIEW_LABELS[key] for key in keys]

    def execute(self, parameters, messages):
        import os, sys
        core_root = os.environ.get("SCQA_CORE_ROOT", "")
        if os.path.isdir(os.path.join(core_root, "acceptance")) and core_root not in sys.path:
            sys.path.insert(0, core_root)
        from acceptance.delivery import write_checklist
        from acceptance.delivery import MANUAL_REVIEW_LABELS
        selected_labels = (parameters[2].valueAsText or "").split(";")
        selected_labels = {label.strip() for label in selected_labels if label.strip()}
        reviewed_keys = [key for key, label in MANUAL_REVIEW_LABELS.items() if label in selected_labels]
        output = write_checklist(parameters[0].valueAsText, parameters[1].valueAsText, manual_reviewed=reviewed_keys)
        messages.addMessage("汇交初筛报告：" + output)
        import json
        with open(output, "r", encoding="utf-8") as report_file:
            report = json.load(report_file)
        messages.addMessage("预检状态：" + report["status"])
        detailed = report.get("detailed_package_check") or {}
        if detailed.get("failures"):
            messages.addWarningMessage("成果预检发现未满足的必交项/数据库问题：{} 项；工具已完成检查，详见 JSON 报告。".format(len(detailed["failures"])))
        if detailed.get("pending_manual_reviews"):
            messages.addWarningMessage("尚有 {} 项人工核验未确认；报告和图件内容不由文件存在状态自动通过。".format(len(detailed["pending_manual_reviews"])))
        elif detailed.get("status") == "PASS":
            messages.addMessage("本次选定的人工核验项与自动文件/数据库检查均已通过；请保留报告和核验底稿。")
        if not detailed:
            messages.addWarningMessage("未找到项目 delivery_manifest.json；目前只能查看目录/文件类型候选，不能判定成果齐全。")


import arcpy
