"""出图工作进程（第一阶段）：建工程、配图层与符号、冻结图例、写文字，然后交出渲染计划。

实测要点：
- 只开一个 ArcGISProject 对象，避免同进程多次打开同一 .aprx 时 save 报 OSError。
- 图例必须在保存前把栅格项从 CIMLegend.items 里剔除并 autoAdd=False，否则每次出图都
  会重新生成 Band_1/2/3，并把图例撑到压住其它元素。
- 同一个进程里改完图例再导出会按旧状态重绘，所以导出交给第二个进程（map_export）。
"""
from __future__ import annotations

import json
import os
import shutil
import datetime
import math
import re
import hashlib
from . import imagery as I
from .map_profile import title_for
from .map_layout_style import (set_layout_text as _set_text, apply_page_geometry, location_map_title,
                               map_presentation_options,
                               source_parcel_labels,
                               style_inset_frame, style_layout_typography,
                               style_legend_background, style_legend_typography,
                               visual_layout_spec)

import arcpy
import arcpy.mp as mp

DEFAULT_TEMPLATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates",
                                                 "补充耕地鉴定图_A4.pagx"))
SCALES = (500, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 10000, 15000, 20000, 25000,
          30000, 50000, 100000, 200000)


def _blank_project():
    homes = [os.environ.get("ARCGIS_PRO_HOME"), arcpy.GetInstallInfo().get("InstallDir")]
    for home in homes:
        if not home:
            continue
        path = os.path.join(home, "Resources", "ArcToolBox", "Services", "routingservices", "data", "Blank.aprx")
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("ArcGIS Pro 安装目录中未找到 Blank.aprx，无法创建成果工程。")


def _safe_map_name(value):
    value = str(value)
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .") or "map"
    if clean != value or len(clean) > 80:
        clean = clean[:65] + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return clean


def overview_map_title(kind, meta):
    """总览/位置/索引/分幅图的图名；未配置时回退到旧默认值。"""
    if kind == "geo":
        return "补充耕地质量鉴定地理位置图"
    return title_for(meta.get("layout_profile"), "overview",
                     fallback=meta.get("title") or "补充耕地质量鉴定采样点位布置图")


def unit_map_title(meta, uid, label_mode, unit_labels):
    """逐单元图名与文件名。源地块号模式下编号用源地块号，否则用评价单元号。"""
    title = title_for(meta.get("layout_profile"), "unit", uid, fallback=meta.get("title_unit"))
    name = _safe_map_name("单元_" + str(uid))
    if label_mode == "source_parcel":
        title = (title_for(meta.get("layout_profile"), "unit", None, fallback=meta.get("title_unit"))
                 or "采样点位图")
        title = "{}（{}）".format(title, unit_labels[str(uid)])
    return title, name


def _imagery_quality(active):
    """Copy the evidence needed for export-time imagery revalidation into each target."""
    active = active if isinstance(active, dict) else {}
    accuracy = active.get("positional_accuracy") or {}
    return {
        "coverage_status": active.get("coverage_status", "NEEDS_REVIEW"),
        "pixel_qa": active.get("pixel_qa", {}),
        "frame_coverage": active.get("frame_coverage", {}),
        "content_role": active.get("content_role", "imagery"),
        "annotation": active.get("annotation"),
          "tiles": active.get("tiles"),
        "source_gsd_m": active.get("source_gsd_m"),
        "source_gsd_status": active.get("source_gsd_status", "UNVERIFIED"),
        "source_gsd_evidence": active.get("source_gsd_evidence"),
        "source_gsd_review": active.get("source_gsd_review"),
        "positional_accuracy": dict(accuracy) if isinstance(accuracy, dict) else {},
    }


def _pick_scale(extent, frame, margin=1.12, meters_per_unit=1):
    width_cm, height_cm = frame.elementWidth, frame.elementHeight
    need_x = extent.width * meters_per_unit / 10.0
    need_y = extent.height * meters_per_unit / 10.0
    ratio = max(need_x / (width_cm / margin), need_y / (height_cm / margin))
    for scale in SCALES:
        if scale >= ratio * 1000:
            return scale
    return SCALES[-1]


def _grid_layer(gdb, extent, sr, step=100.0, name="方里网"):
    out = os.path.join(gdb, name)
    if arcpy.Exists(out):
        return out
    x0 = (int(extent.XMin // step) - 1) * step
    y0 = (int(extent.YMin // step) - 1) * step
    lines = []
    x = x0
    while x <= extent.XMax + step:
        lines.append(arcpy.Polyline(arcpy.Array([arcpy.Point(x, y0),
                                                 arcpy.Point(x, extent.YMax + step)]), sr))
        x += step
    y = y0
    while y <= extent.YMax + step:
        lines.append(arcpy.Polyline(arcpy.Array([arcpy.Point(x0, y),
                                                 arcpy.Point(extent.XMax + step, y)]), sr))
        y += step
    arcpy.management.CreateFeatureclass(gdb, name, "POLYLINE", spatial_reference=sr)
    with arcpy.da.InsertCursor(out, ["SHAPE@"]) as cur:
        for line in lines:
            cur.insertRow([line])
    return out


def _style(layer, name=None, fill=None, outline=None, width=None, size=None, marker_style=None):
    if name:
        layer.name = name
    if all(value is None for value in (fill, outline, width, size)):
        return layer
    try:
        sym = layer.symbology
        if marker_style is not None:
            sym.renderer.symbol.applySymbolFromGallery(marker_style)
        if fill is not None:
            sym.renderer.symbol.color = {"RGB": list(fill)}
        if outline is not None and hasattr(sym.renderer.symbol, "outlineColor"):
            sym.renderer.symbol.outlineColor = {"RGB": list(outline)}
        if width is not None and hasattr(sym.renderer.symbol, "outlineWidth"):
            sym.renderer.symbol.outlineWidth = width
        if size is not None and hasattr(sym.renderer.symbol, "size"):
            sym.renderer.symbol.size = size
        layer.symbology = sym
    except Exception as exc:  # noqa: BLE001 - 符号失败不阻断出图，但要留痕
        print("符号设置失败 {}: {}: {}".format(layer.name, type(exc).__name__, exc))
    return layer


def _configure_grid_display(layer, meta):
    """Keep complete squares, showing only those intersecting their owning unit."""
    fields = {field.name.upper(): field.name for field in arcpy.ListFields(layer.dataSource)}
    if 'IN_UNIT' not in fields:
        raise ValueError('方格网缺少IN_UNIT，不能确认地块内显示范围；请重新生成网格')
    predicate = '{} = 1'.format(arcpy.AddFieldDelimiters(layer.dataSource, fields['IN_UNIT']))
    previous = str(layer.definitionQuery or '').strip()
    layer.definitionQuery = '({}) AND ({})'.format(previous, predicate) if previous else predicate
    presentation = map_presentation_options({'meta': meta or {}}, {})
    return _style(layer, fill=(0, 0, 0, 0), outline=presentation['grid_line_color'],
                  width=float(presentation['grid_line_width']))


def _configure_source_parcel_labels(mapping, source_field):
    """Use the original parcel identifiers in Pro as well as exported maps."""
    parcels = next((layer for layer in mapping.listLayers() if layer.name == "补充耕地图斑"), None)
    if parcels is None or source_field not in {field.name for field in arcpy.ListFields(parcels.dataSource)}:
        raise ValueError("缺少原始地块编号字段：" + source_field)
    from .map_export import _style_map_label_sizes

    _style_map_label_sizes(mapping, "sample", {
        "label_mode": "source_parcel", "parcel_label_field": source_field,
    })


def _label(layer, expression, size=7, halo=1.1):
    try:
        layer.showLabels = True
        for lc in layer.listLabelClasses():
            lc.expression = expression
            lc.visible = True
        cim = layer.getDefinition("V3")
        for lc in cim.labelClasses:
            sym = lc.textSymbol.symbol
            sym.height = size
            sym.fontFamilyName = "Arial"
            sym.fontStyleName = "Bold"
            sym.haloSize = max(float(halo), 1.5)
            for ink in getattr(getattr(sym, "symbol", None), "symbolLayers", None) or []:
                if type(ink).__name__ == "CIMSolidFill":
                    ink.color.values = [24, 32, 40, 255]
            sym.haloSymbol = {"type": "CIMPolygonSymbol", "symbolLayers": [
                {"type": "CIMSolidFill", "enable": True,
                 "color": {"type": "CIMRGBColor", "values": [255, 255, 255, 225]}}]}
        layer.setDefinition(cim)
        return True
    except Exception as exc:  # noqa: BLE001
        print("标注设置失败 {}: {}: {}".format(layer.name, type(exc).__name__, exc))
        return False


def _freeze_legend(layout, hide_names=("影像底图",)):
    """剔除栅格图例项并冻结（见文件头说明），返回保留的项名。"""
    kept = []
    try:
        cim = layout.getDefinition("V3")
    except Exception as exc:  # noqa: BLE001
        print("读取布局 CIM 失败: {}".format(type(exc).__name__))
        return kept
    for element in cim.elements:
        if type(element).__name__ != "CIMLegend":
            continue
        items = list(element.items or [])
        keep = [item for item in items if getattr(item, "name", "") not in hide_names]
        element.items = keep
        element.autoAdd = False
        kept = [getattr(item, "name", "") for item in keep]
        cim = style_legend_background(layout, cim, commit=False)
        cim = style_legend_typography(layout, cim, commit=False)
    try:
        layout.setDefinition(cim)
    except Exception as exc:  # noqa: BLE001
        print("写回布局 CIM 失败: {}".format(type(exc).__name__))
    for element in layout.listElements():
        if element.type == "LEGEND_ELEMENT":
            try:
                legend = visual_layout_spec("sample")["legend"]
                element.syncNewLayer = False
                element.syncLayerVisibility = True
                element.elementPositionX = legend["x"]
                element.elementPositionY = legend["y"]
                element.elementWidth = legend["width"]
                element.elementHeight = legend["height"]
            except Exception as exc:  # noqa: BLE001
                print("调整图例框失败: {}".format(type(exc).__name__))
    return kept


def _place_neatline_labels(layout):
    """图廓坐标注记默认锚在内外图廓线上；固定另一轴，让注记整体落在图廓外侧。

    北坐标注记的 X 必须等导出时先定 Y 再设，否则 ArcGIS 会把锚点算成 NaN、整列消失。
    """
    fixed = {"E上": ("y", 27.22), "E下": ("y", 10.36)}
    for element in layout.listElements():
        if element.type != "TEXT_ELEMENT":
            continue
        for prefix, (axis, value) in fixed.items():
            if (element.name or "").startswith(prefix):
                if axis == "y":
                    element.elementPositionY = value
                else:
                    element.elementPositionX = value


def _move_text(layout, name, x, y):
    for element in layout.listElements():
        if element.type == "TEXT_ELEMENT" and element.name == name:
            try:
                element.elementPositionX = x
                element.elementPositionY = y
            except Exception:  # noqa: BLE001
                pass


def _move_element(layout, name, x, y):
    for element in layout.listElements():
        if element.name == name:
            element.elementPositionX = x
            element.elementPositionY = y


def _hide(layout, name):
    for element in layout.listElements():
        if element.name == name:
            try:
                element.visible = False
            except Exception:  # noqa: BLE001
                pass


DRAWING_ROLES = ('问题与待核项', '设计采样点', '容重候选点', '单元边界', '网格', '辅助资料', '影像')


def enforce_layer_order(mapping):
    """Keep business overlays above context and raster groups, including reopened projects."""
    def roots():
        return [layer for layer in mapping.listLayers() if '\\' not in getattr(layer, 'longName', layer.name)]
    def rank(layer):
        name = re.sub(r'^\d+\s+', '', layer.name)
        name = {'补充耕地图斑': '单元边界', '评价单元': '单元边界', '方格网': '网格',
                '网格交点': '网格', '网格中心点': '网格', '影像底图': '影像'}.get(name, name)
        return DRAWING_ROLES.index(name) if name in DRAWING_ROLES else 5
    layers = roots()
    if [rank(layer) for layer in layers] == sorted(rank(layer) for layer in layers):
        return [layer.name for layer in layers]
    ordered = sorted(layers, key=rank)
    if ordered and ordered[0] != layers[0]:
        mapping.moveLayer(layers[0], ordered[0], 'BEFORE')
    for before, after in zip(ordered, ordered[1:]):
        mapping.moveLayer(before, after, 'AFTER')
    actual = roots()
    if [rank(layer) for layer in actual] != sorted(rank(layer) for layer in actual):
        raise RuntimeError('图层绘制顺序回读不一致')
    return [layer.name for layer in actual]


def _group_project_layers(mapping, roles):
    """将生成图层按用途归组；节点/中心仅作辅助定位，默认隐藏。"""
    group_names = DRAWING_ROLES
    groups = {}
    # createGroupLayer inserts at the top, so create in reverse order to get the planned TOC order.
    for name in reversed(group_names):
        groups[name] = mapping.createGroupLayer("{:02d} {}".format(group_names.index(name) + 1, name), None)
    for name in group_names:
        group = groups[name]
        for layer in reversed(roles.get(name, [])):
            if layer is None:
                continue
            if layer.name in ("网格交点", "网格中心点", "方里网"):
                layer.visible = False
            mapping.addLayerToGroup(group, layer, "TOP")
            mapping.removeLayer(layer)
    enforce_layer_order(mapping)
    return groups


def _build_inset(aprx, layout, frame, spec, job):
    """区位小图：县界 + 项目点 + 所在县高亮；界线来源未核，仅作示意。"""
    render_dir = job.get("render_dir") or job["out_dir"]
    gdb = os.path.join(render_dir, "区位图.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(render_dir, "区位图.gdb")
    counties = os.path.join(gdb, "县界")
    if not arcpy.Exists(counties):
        arcpy.conversion.JSONToFeatures(spec["county_geojson"], counties)
    inset_map = aprx.createMap("区位图", "MAP")
    for layer in list(inset_map.listLayers()):
        inset_map.removeLayer(layer)
    _style(inset_map.addDataFromPath(counties), name="县界",
           fill=(232, 232, 232, 255), outline=(255, 255, 255, 255), width=0.4)
    point = spec.get("point")
    if point:
        point_fc = os.path.join(gdb, "项目位置")
        if not arcpy.Exists(point_fc):
            arcpy.management.CreateFeatureclass(gdb, "项目位置", "POINT",
                                                spatial_reference=arcpy.SpatialReference(4326))
            with arcpy.da.InsertCursor(point_fc, ["SHAPE@"]) as cursor:
                cursor.insertRow([arcpy.Point(point[0], point[1])])
        _style(inset_map.addDataFromPath(point_fc), name="项目位置",
               fill=(220, 30, 30, 255), size=6.0)
        arcpy.management.MakeFeatureLayer(counties, "inset_sel")
        arcpy.management.SelectLayerByLocation(
            "inset_sel", "INTERSECT",
            arcpy.PointGeometry(arcpy.Point(point[0], point[1]), arcpy.SpatialReference(4326)))
        hit = os.path.join(gdb, "项目县")
        if arcpy.Exists(hit):
            arcpy.management.Delete(hit)
        if int(arcpy.management.GetCount("inset_sel")[0]):
            arcpy.management.CopyFeatures("inset_sel", hit)
            _style(inset_map.addDataFromPath(hit), name="项目县",
                   fill=(255, 170, 60, 160), outline=(200, 100, 0, 200), width=0.8)
        arcpy.management.Delete("inset_sel")
    desc = arcpy.Describe(counties)
    extent = desc.extent
    lat = (extent.YMin + extent.YMax) / 2.0
    width_m = (extent.XMax - extent.XMin) * 111320.0 * math.cos(math.radians(lat))
    height_m = (extent.YMax - extent.YMin) * 110540.0
    scale = max(width_m / (max(0.1, frame.elementWidth) / 100.0),
                height_m / (max(0.1, frame.elementHeight) / 100.0)) * 1.08
    camera = frame.camera
    camera.X = (extent.XMin + extent.XMax) / 2.0
    camera.Y = (extent.YMin + extent.YMax) / 2.0
    camera.scale = scale
    frame.map = inset_map
    frame.camera = camera
    for element in layout.listElements():
        if element.type == "TEXT_ELEMENT" and element.name == "区位图名":
            element.text = "区位示意（界线待核）"


def _build_project_index(aprx, frame, job, spatial_reference):
    mapping = aprx.createMap("项目地块分布索引", "MAP")
    for layer in mapping.listLayers():
        mapping.removeLayer(layer)
    mapping.spatialReference = spatial_reference
    layer = _style(mapping.addDataFromPath(job["parcels"]), name="全部原始地块",
                   fill=(255, 255, 255, 0), outline=(220, 48, 48, 100), width=1.1)
    _label(layer, "$feature." + job.get("parcel_label_field", job.get("parcel_id_field", "DKBH")), size=7)
    frame.map = mapping
    camera = frame.camera
    camera.setExtent(arcpy.Describe(job["parcels"]).extent)
    camera.scale *= 1.15
    frame.camera = camera


def build(job):
    template = job.get("template") or DEFAULT_TEMPLATE
    if not os.path.exists(template):
        raise FileNotFoundError("出图模板不存在：" + template)
    project_path = job["project_path"]
    if os.path.exists(project_path):
        raise FileExistsError("工程文件已存在，不覆盖：" + project_path)
    os.makedirs(os.path.dirname(project_path), exist_ok=True)
    shutil.copy2(_blank_project(), project_path)

    aprx = mp.ArcGISProject(project_path)
    mapping = aprx.listMaps()[0] if aprx.listMaps() else aprx.createMap("成果图", "MAP")
    mapping.name = job.get("map_name", "采样点位图")
    working_sr = arcpy.SpatialReference(int(job["wkid"]))
    mapping.spatialReference = working_sr

    roles = {name: [] for name in ("问题与待核项", "设计采样点", "容重候选点", "单元边界", "网格", "辅助资料", "影像")}
    imagery = job.get("imagery") or {}
    image_candidates = [imagery, job.get("imagery_location") or {}] + list((job.get("imagery_by_unit") or {}).values())
    initial_imagery = next((item for item in image_candidates if item.get("output") and os.path.exists(item["output"])), {})
    if initial_imagery:
        roles["影像"].append(_style(mapping.addDataFromPath(initial_imagery["output"]), name="影像底图"))
    else:
        print("警告：未提供影像底图，图面只有矢量要素（不得作为报件图）")

    roles["单元边界"].append(_style(mapping.addDataFromPath(job["parcels"]), name="补充耕地图斑",
                                   fill=(255, 214, 90, 8), outline=(184, 36, 38, 255), width=1.2))
    units = _style(mapping.addDataFromPath(job["units"]), name="评价单元",
                   fill=(255, 255, 255, 0), outline=(220, 66, 58, 255), width=1.1)
    if (job.get("meta") or {}).get("label_mode") == "source_parcel":
        _configure_source_parcel_labels(mapping, job.get("parcel_label_field", job.get("parcel_id_field", "DKBH")))
    else:
        _label(units, "$feature." + job.get("filter_fields", {}).get("评价单元", "PJDYBH"), size=8.5)
    roles["单元边界"].append(units)
    grid_layer = _style(mapping.addDataFromPath(job["grid"]), name="方格网")
    roles["网格"].append(_configure_grid_display(grid_layer, job.get('meta') or {}))
    roles["网格"].append(_style(mapping.addDataFromPath(job["nodes"]), name="网格交点", fill=(0, 90, 200, 100), size=2.0))
    roles["网格"].append(_style(mapping.addDataFromPath(job["centers"]), name="网格中心点", fill=(0, 150, 90, 100), size=2.6))
    samples = _style(mapping.addDataFromPath(job["samples"]), name="设计采样点",
                     fill=(214, 39, 40, 255), outline=(255, 255, 255, 255),
                     width=0.6, size=8.0, marker_style="菱形 1")
    _label(samples, "$feature.SEQ", size=9.5)
    roles["设计采样点"].append(samples)

    if job.get("bulk_density_candidates") and os.path.exists(job["bulk_density_candidates"]):
        roles["容重候选点"].append(_style(
            mapping.addDataFromPath(job["bulk_density_candidates"]), name="容重候选点",
            fill=(153, 51, 204, 100), size=7.0))

    for item in job.get("context_layers") or []:
        role = str(item.get("role") or "其他")
        layer = mapping.addDataFromPath(item["path"])
        if role == "居民点":
            _style(layer, name=role, fill=(200, 30, 30, 100), size=5.0)
        elif role == "水系":
            _style(layer, name=role, fill=(30, 90, 200, 100), width=0.9)
        elif role in ("道路", "境界线"):
            _style(layer, name=role, fill=(120, 80, 40, 100), width=0.8)
        else:
            _style(layer, name=str(item.get("name") or role), fill=(90, 90, 90, 100))
        roles["辅助资料"].append(layer)

    if job.get("frame_grid", True) and map_presentation_options({'meta': job.get('meta') or {}}, {})['show_frame_grid']:
        desc = arcpy.Describe(job["parcels"])
        step = float(job.get("frame_grid_step", 100.0))
        pad = step * 2
        grid_extent = arcpy.Extent(desc.extent.XMin - pad, desc.extent.YMin - pad,
                                   desc.extent.XMax + pad, desc.extent.YMax + pad)
        net_path = _grid_layer(os.path.dirname(job["parcels"]), grid_extent,
                               desc.spatialReference, step)
        roles["辅助资料"].append(_style(mapping.addDataFromPath(net_path), name="方里网",
                                       fill=(0, 0, 0, 210), width=0.35))

    groups = _group_project_layers(mapping, roles)
    preview_dir = os.path.join(job.get("render_dir") or job["out_dir"], "preview_layers")
    os.makedirs(preview_dir, exist_ok=True)
    preview_layers = []

    aprx.importDocument(template, include_layout=True)
    layout = aprx.listLayouts()[0]
    # Normalize the frame and text CIM before changing any page element geometry.
    style_inset_frame(layout)
    style_layout_typography(layout, show_frame_grid=map_presentation_options({'meta': job.get('meta') or {}}, {})['show_frame_grid'])
    _place_neatline_labels(layout)
    frame = None
    for element in layout.listElements():
        if element.type == "MAPFRAME_ELEMENT" and element.name in ("WEBMAP_MAP_FRAME", "主图框"):
            frame = element
            break
    if frame is None:
        for element in layout.listElements():
            if element.type == "MAPFRAME_ELEMENT" and element.name not in ("外图廓", "区位图框"):
                frame = element
                break
    if frame is None:
        raise RuntimeError("模板中未找到地图框（MAPFRAME_ELEMENT）")
    frame.map = mapping
    apply_page_geometry(layout, "sample")
    inset_spec = job.get("inset") or {}
    inset_frame = next((e for e in layout.listElements() if e.name == "区位图框"), None)
    if inset_frame is not None:
        if job.get("project_index"):
            _build_project_index(aprx, inset_frame, job, working_sr)
        elif inset_spec.get("county_geojson") and os.path.exists(inset_spec["county_geojson"]):
            try:
                _build_inset(aprx, layout, inset_frame, inset_spec, job)
            except Exception as exc:  # noqa: BLE001 - 区位图失败不阻断主图
                print("区位小图失败：{}: {}".format(type(exc).__name__, exc))
                inset_frame.visible = False
        else:
            inset_frame.visible = False

    kept_items = _freeze_legend(layout, ("影像底图", "网格交点", "网格中心点", "方里网"))

    meta = job.get("meta", {})
    crs_name = job.get("crs_name") or getattr(mapping.spatialReference, "name", "")
    meta.setdefault("date", datetime.date.today().isoformat())
    meta.setdefault("crs_label", crs_name)
    meta.setdefault("projection", crs_name)
    _set_text(layout, "图号", "图号：{}".format(meta.get("sheet_no", "")))
    if meta.get("secret"):
        _set_text(layout, "密级", "密级：{}".format(meta["secret"]))
    else:
        _hide(layout, "密级")
    _set_text(layout, "编制单位", "编制单位：{}".format(meta.get("org") or "（未提供，不得报件）"))
    _set_text(layout, "制图审核", "制  图：{}    审  核：{}".format(meta.get("drawn_by") or "未提供",
                                                                    meta.get("checked_by") or "未提供"))
    _set_text(layout, "成图日期", "成图日期：{}".format(meta.get("date", "")))
    _set_text(layout, "坐标系", "坐标系：{}".format(meta.get("crs_label", "")))
    _set_text(layout, "高程基准", "高程基准：{}".format(meta.get("elevation_datum") or "未提供（以项目资料为准）"))
    _set_text(layout, "投影", "投  影：{}".format(meta.get("projection", "")))
    # 版面重排：比例尺注移出图例范围、页脚注记固定位置、隐藏重复的动态日期
    _move_text(layout, "比例尺注", 1.40, 4.55)
    # Pro 3.1.5 can save an unreadable layout when both this graphic group and its
    # nested map surround are hidden. Keep them visible and below the area table.
    _move_element(layout, "Scale bar", 13.73, 2.20)
    _move_element(layout, "Scale Line", 13.80, 2.20)
    _move_text(layout, "坐标注记说明", 1.40, 1.30)
    _hide(layout, "{Date}")

    unit_rows = job["unit_rows"]
    label_mode = meta.get("label_mode")
    unit_labels = source_parcel_labels(unit_rows) if label_mode == "source_parcel" else {}
    meters_per_unit = float(job.get("meters_per_unit") or working_sr.metersPerUnit)
    specs = list(job["targets"])
    sheets = [target for target in specs if target.get("kind") in ("sheet", "geo")]
    by_unit = job.get("imagery_by_unit") or {}
    current_imagery = imagery
    targets = []
    map_out_dir = job.get("map_out_dir") or os.path.join(job["out_dir"], "map")
    scale_override = job.get("scale")
    for target in specs:
        uid = target.get("unit")
        kind = target.get("kind", "sample")
        missing_context_roles = []
        extent = arcpy.Extent(*target["extent"])
        active = job.get("imagery_location") or imagery if kind == "geo" else imagery
        if target.get("name") in (job.get("imagery_by_target") or {}):
            active = job["imagery_by_target"][target["name"]]
        swap = None
        if uid is not None and str(uid) in by_unit:
            new_meta = by_unit[str(uid)]
            active = new_meta
            if new_meta.get("output") and imagery.get("output") \
                    and new_meta["output"] != imagery["output"]:
                active = new_meta
                swap = {"previous": current_imagery.get("output"), "output": new_meta["output"]}
                current_imagery = new_meta
        explicit_scale = target.get("scale") or scale_override
        scale = explicit_scale or _pick_scale(extent, frame, meters_per_unit=meters_per_unit)
        if not explicit_scale and kind in ("sample", "sheet"):
            scale = max(scale, 2000)  # 课件样图口径：采样点位图从 1:2000 起，避免过细丢图面环境
        imagery_text = I.imagery_caption(active)
        if uid is None:
            included = [row for row in unit_rows if not target.get("unit_ids") or str(row["unit"]) in target["unit_ids"]]
            rows = ["{} {} {:.1f}亩".format(unit_labels.get(str(r["unit"]), r["unit"]), r.get("landtype", ""), r["mu"]) for r in included]
            total = sum(r["mu"] for r in included)
            title = overview_map_title(kind, meta)
            if kind == "index":
                title += "（索引总览）"
            elif kind == "sheet":
                title += "（分幅 {}）".format(target["sheet_id"])
            annotation = active.get("annotation") or {}
            if kind == "geo" and (active.get("content_role") != "vector" or annotation.get("fail", 1)):
                title, missing_context_roles = location_map_title(title, job.get("context_layers") or [])
            name = "地理位置图" if kind == "geo" else "采样点位布置图"
            if kind == "sheet":
                name += "_" + target["sheet_id"]
        else:
            row = next((r for r in unit_rows if r["unit"] == uid), None)
            rows = ["{} {} {:.1f}亩".format(unit_labels.get(str(row["unit"]), row["unit"]), row.get("landtype", ""), row["mu"])] \
                if row else []
            total = row["mu"] if row else 0.0
            title, name = unit_map_title(meta, uid, label_mode, unit_labels)
            if label_mode == "source_parcel":
                name = _safe_map_name("采样图_" + unit_labels[str(uid)])
        if target.get("name"):
            name = _safe_map_name(target["name"])
        if kind == "index":
            title = "补充耕地质量鉴定项目地块总览图"
            suffix = re.search(r'_(\d+)$', name)
            if suffix:
                title += '（分幅{:02d}）'.format(int(suffix.group(1)))
        if target.get("parcel") is not None:
            parcel_title = title_for(meta.get("layout_profile"), "unit", None,
                                     fallback=meta.get("title")) or "补充耕地质量鉴定采样点位布置图"
            title = "{}（地块{}）".format(parcel_title, target["parcel"])
            rows = ["地块{}  {:.1f}亩".format(target["parcel"], target.get("area_mu", total))]
            total = target.get("area_mu", total)
        if target.get("source_parcels") and kind == "geo":
            rows = ["地块：" + "、".join(target["source_parcels"])]
        if active.get("content_role") == "vector":
            imagery_text = I.imagery_caption(active).replace("影像来源", "底图来源")
        else:
            imagery_text = I.imagery_caption(active)
        targets.append({
            "name": name, "uid": uid, "kind": kind,
            "label_mode": target.get("label_mode", label_mode), "unit_labels": unit_labels,
            "parcel": target.get("parcel"), "parcel_ids": target.get("parcel_ids"),
            "source_parcels": target.get("source_parcels"),
            "parcel_label_field": job.get("parcel_label_field", job.get("parcel_id_field", "DKBH")),
            "missing_context_roles": missing_context_roles,
            "sheet_id": target.get("sheet_id"), "unit_ids": target.get("unit_ids"),
            "extent": [extent.XMin, extent.YMin, extent.XMax, extent.YMax],
            "scale": scale, "scale_key": target.get('scale_key'),
            "automatic_scale": target.get('automatic_scale', scale), "title": title, "rows": rows, "total_mu": total,
            "imagery_text": imagery_text, "imagery": swap, "imagery_path": active.get("output"),
            "imagery_quality": _imagery_quality(active),
            "scale_recommendation": "NEEDS_REVIEW" if kind in ("sample", "sheet") and scale > 10000 else "PASS",
            "show_net": False,  # 方里网默认隐藏，图面只保留采样方格网
            "png": os.path.join(map_out_dir, "{}.png".format(name)),
            "jpg": os.path.join(map_out_dir, "{}.jpg".format(name)),
            "pdf": os.path.join(map_out_dir, "{}.pdf".format(name)),
        })

    for group in sorted(groups.values(), key=lambda layer: layer.name):
        path = os.path.join(preview_dir, _safe_map_name(group.name) + ".lyrx")
        group.saveACopy(path)
        preview_layers.append(path)
    aprx.save()
    plan = {"project": project_path, "map_name": mapping.name, "meta": meta,
            "dpi": int(job.get("dpi", 200)), "targets": targets,
            "parcel_id_field": job.get("parcel_id_field", "DKBH"),
            "filter_fields": job.get("filter_fields", {}), "sheets": sheets}
    plan_path = os.path.join(job.get("render_dir") or job["out_dir"], "render_plan.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=2)
    return {"project": project_path, "plan": plan_path, "legend_items": kept_items,
            "preview_layers": preview_layers,
            "sheets": sheets,
            "layers": [getattr(l, "name", "?") for l in mapping.listLayers()],
            "targets": len(targets), "status": "BUILT"}


def main(argv):
    job_path = argv[1]
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    result = build(job)
    out = job_path.replace(".json", ".result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print("MAP_BUILD_OK", out, result["targets"], "| 图例项:", "、".join(result["legend_items"]))


if __name__ == "__main__":
    import sys
    main(sys.argv)
