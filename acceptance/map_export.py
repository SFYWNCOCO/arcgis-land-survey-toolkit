"""出图导出进程：在新进程里重新打开工程，逐图幅改视图/换影像/写文字后导出。

为什么要独立进程：同一个进程里改过图例 CIM 后再导出，图例会按旧状态重绘（实测仍带
Band_1/2/3 并压到其它元素上）；重新打开工程后导出才稳定。工程与导出拆成两步。
"""
from __future__ import annotations

import json
import math
import os
import sys
import copy
import hashlib
import re
import subprocess
import tempfile

import arcpy
import arcpy.mp as mp
from .map_validation import (create_thumbnail, verify_export_artifact, collect_layout_snapshot,
                             audit_layout_snapshot, layout_export_contract, audit_map_scale, validate_visual_review)
from .map_layout_style import (set_layout_text as _set_text, apply_page_geometry, area_row_positions, display_title, map_metadata_values,
                               map_presentation_options,
                               planned_scale,
                               style_inset_frame, style_layout_typography,
                               style_legend_background, style_legend_typography,
                               visual_layout_spec)
from . import imagery as imagery_module
from .task_state import _atomic_json


def _target_query(layer, target, plan):
    """Filter point/parcel rows by the planned original-parcel association, not only sampling group."""
    if target.get("parcel_ids") and layer.name in ("补充耕地图斑", "设计采样点", "网格中心点"):
        field = plan.get("parcel_id_field", "DKBH") if layer.name == "补充耕地图斑" else "DKBH"
        values = target["parcel_ids"]
    elif target.get("unit_ids"):
        field = (plan.get("filter_fields") or {}).get(layer.name, "PJDYBH")
        values = target["unit_ids"]
    elif target.get("uid") is not None:
        field = (plan.get("filter_fields") or {}).get(layer.name, "PJDYBH")
        values = [target["uid"]]
    else:
        return "1=1"
    fields = {f.name.upper(): f for f in arcpy.ListFields(layer.dataSource)}
    definition = fields.get(field.upper())
    if definition is None:
        raise ValueError("制图关联字段不存在：" + field)
    values = sorted({str(v) for v in values})
    if not values or len(values) > 10000:
        raise ValueError("制图过滤范围为空或过大")
    numeric = definition.type in ("OID", "Integer", "SmallInteger", "Double", "Single")
    if numeric:
        if any(not math.isfinite(float(v)) for v in values):
            raise ValueError("制图编号不是有限数值")
        literals = [str(float(v)) for v in values]
    else:
        literals = ["'{}'".format(v.replace("'", "''")) for v in values]
    name = arcpy.AddFieldDelimiters(layer.dataSource, definition.name)
    clauses = ["{} IN ({})".format(name, ",".join(literals[i:i + 400]))
               for i in range(0, len(literals), 400)]
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def _grid_query_for_target_parcels(layer, target, plan, where, parcel_layer):
    """Keep whole squares intersecting the displayed parcels; never clip grid geometry."""
    if layer.name != '方格网' or target.get('kind') != 'sample' or not target.get('parcel_ids'):
        return where
    if parcel_layer is None:
        raise RuntimeError('采样图缺少原始图斑图层，无法限定方格网范围')
    geometry = None
    with arcpy.da.SearchCursor(parcel_layer.dataSource, ['SHAPE@'],
                               where_clause=_target_query(parcel_layer, target, plan)) as cursor:
        for row in cursor:
            if row[0] is not None:
                geometry = row[0] if geometry is None else geometry.union(row[0])
    if geometry is None:
        raise RuntimeError('未找到当前采样图的原始地块几何')
    selected = []
    with arcpy.da.SearchCursor(layer.dataSource, ['OID@', 'SHAPE@'], where_clause=where) as cursor:
        for oid, square in cursor:
            if square is not None and not geometry.disjoint(square):
                selected.append(int(oid))
    if not selected:
        return where + ' AND 1=0'
    selected = sorted(set(selected))
    field = arcpy.AddFieldDelimiters(layer.dataSource, arcpy.Describe(layer.dataSource).OIDFieldName)
    groups = ['{} IN ({})'.format(field, ','.join(str(oid) for oid in selected[start:start + 900]))
              for start in range(0, len(selected), 900)]
    return where + ' AND (' + ' OR '.join(groups) + ')'


def _mark_target_preview_only(target, imagery_visible):
    target = dict(target or {})
    if target.get("kind") not in ("sample", "sheet", "index") or imagery_visible:
        return target
    title = str(target.get("title") or "采样点位布置图")
    if not title.startswith("【无影像预览】"):
        target["title"] = "【无影像预览】" + title
    target["imagery_text"] = "影像底图未提供；本图仅供内部预览，不可作为报件图"
    target["preview_only"] = True
    return target


def _record_missing_imagery_failure(output, target, imagery_visible):
    output = dict(output or {})
    if target.get("kind") not in ("sample", "sheet", "index") or imagery_visible:
        return output
    output["preview_only"] = True
    output["deliverable_status"] = "PREVIEW_ONLY"
    checks = [row for row in output.get("checks", []) if row.get("item") != "采样图影像底图"]
    checks.append({"item": "采样图影像底图", "status": "FAIL",
                   "detail": "课件要求采样点位图以影像为底图；保留的文件仅供内部定位预览，不可作为报件图"})
    output["checks"] = checks
    output["status"] = "FAIL"
    return output


def _refresh_imagery_evidence(output, target):
    """Replace cached imagery evidence checks with checks against current proof files."""
    output = dict(output or {})
    evidence_items = {"原始GSD", "影像位置精度", "视觉复核", "底图缺瓦片", "像元与纸面采样"}
    checks = [row for row in output.get("checks", []) if row.get("item") not in evidence_items]
    checks.extend(imagery_module.export_imagery_evidence_checks(target.get("imagery_quality") or {}))
    if output.get('sampling_diagnostics') and target.get('imagery_path'):
        measured = imagery_module.raster_sampling_diagnostics(target['imagery_path'], target['scale'],
            output['sampling_diagnostics']['dpi'], arcpy_module=arcpy)
        output['sampling_diagnostics'] = measured
        checks.append({'item': '像元与纸面采样', 'status': 'PASS' if measured['status'] == 'MEASURED' else 'NEEDS_REVIEW',
                       'detail': measured})
    tiles = (target.get('imagery_quality') or {}).get('tiles')
    if isinstance(tiles, dict) and int(tiles.get('fail') or 0) > 0:
        checks.append({'item': '底图缺瓦片', 'status': 'FAIL',
                       'detail': '底图有{}张下载失败瓦片；保留预览，须补齐后重新出图。'.format(tiles['fail'])})
    visual=validate_visual_review(output.get('jpg') or '',output.get('render_key'))
    output['visual_review']=visual
    checks.append({'item':'视觉复核','status':visual['status'],'detail':visual['detail']})
    output["checks"] = checks
    statuses = {row.get("status", "FAIL") for row in checks}
    if output.get("status") == "FAIL":
        statuses.add("FAIL")
    output["status"] = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS"
    imagery_visible = output.get("imagery_visible")
    if imagery_visible is None:
        imagery_visible = bool(target.get("imagery_path") or target.get("imagery"))
    return _record_missing_imagery_failure(output, target, imagery_visible)


def _set_polygon_outline(layer, color, width, dashed=False):
    """Set a polygon outline consistently and optionally add a dash effect."""
    cim = layer.getDefinition("V3")
    symbol = cim.renderer.symbol.symbol
    stroke = next((item for item in symbol.symbolLayers
                   if type(item).__name__ == "CIMSolidStroke"), None)
    if stroke is None:
        raise RuntimeError("图层缺少可整饰的轮廓线：{}".format(layer.name))
    stroke.color.values = list(color)
    stroke.width = float(width)
    if dashed:
        effect = arcpy.cim.CIMGeometricEffectDashes()
        effect.dashTemplate = [5.0, 3.0]
        effect.lineDashEnding = "FullGap"
        effect.controlPointEnding = "NoConstraint"
        stroke.effects = [effect]
    else:
        stroke.effects = []
    layer.setDefinition(cim)


def _style_sample_map_layers(mapping, inset=False, parcel_only=False, presentation=None):
    """Enforce hierarchy for boundaries, grid, and sampling points in each map."""
    found_points = False
    for layer in mapping.listLayers():
        if layer.name not in ("设计采样点", "补充耕地图斑", "评价单元", "方格网", "网格中心点"):
            continue
        symbology = layer.symbology
        symbol = symbology.renderer.symbol
        if layer.name == "设计采样点":
            symbol.applySymbolFromGallery("菱形 1")
            symbol.color = {"RGB": [214, 39, 40, 100]}
            symbol.outlineColor = {"RGB": [255, 255, 255, 100]}
            symbol.outlineWidth = 1.2 if not inset else 0.9
            symbol.size = 9.5 if not inset else 7.0
            found_points = True
        elif layer.name == "网格中心点":
            symbol.color = {"RGB": [70, 130, 110, 55]}
            symbol.outlineColor = {"RGB": [255, 255, 255, 60]}
            symbol.outlineWidth = 0.35
            symbol.size = 3.2
        elif layer.name == "补充耕地图斑":
            symbol.color = {"RGB": [255, 255, 255, 0]}
            color = [220, 48, 48, 100] if parcel_only else [133, 73, 155, 100]
            symbol.outlineColor = {"RGB": color}
            symbol.outlineWidth = 1.45 if inset or parcel_only else 1.15
        elif layer.name == "评价单元":
            symbol.color = {"RGB": [255, 255, 255, 0]}
            color = [220, 48, 48, 100]
            symbol.outlineColor = {"RGB": color}
            symbol.outlineWidth = 1.6 if inset else 1.45
        else:
            symbol.color = {"RGB": [255, 255, 255, 0]}
            grid_style = map_presentation_options({}, {'presentation': presentation or {}})
            color = list(grid_style['grid_line_color'])
            symbol.outlineColor = {"RGB": color}
            symbol.outlineWidth = float(grid_style['grid_line_width'])
        layer.symbology = symbology
        if layer.name == "补充耕地图斑":
            _set_polygon_outline(layer, color, symbol.outlineWidth, dashed=not parcel_only)
        elif layer.name in ("评价单元", "方格网"):
            _set_polygon_outline(layer, color, symbol.outlineWidth)
    return found_points


def _style_map_label_sizes(mapping, kind, target=None):
    """Apply legible feature-label sizes to both new and previously saved projects."""
    sizes = visual_layout_spec(kind)["font_sizes"]
    unit_size = sizes["unit_label"]
    layer_sizes = {"评价单元": unit_size,
                   "设计采样点": sizes["sample_label"],
                   "图幅索引": sizes["sheet_label"]}
    parcel_only = (target or {}).get("label_mode") == "source_parcel"
    if parcel_only:
        layer_sizes["补充耕地图斑"] = sizes["parcel_label"]
    for layer in mapping.listLayers():
        size = layer_sizes.get(layer.name)
        if size is None:
            continue
        try:
            cim = layer.getDefinition("V3")
            for label_class in cim.labelClasses or []:
                if parcel_only and layer.name == "补充耕地图斑":
                    source_field = (target or {}).get("parcel_label_field", "DKBH")
                    if source_field not in {field.name for field in arcpy.ListFields(layer.dataSource)}:
                        raise ValueError("缺少原始地块编号字段：" + source_field)
                    label_class.expression = "'地块' + Text($feature[{}])".format(json.dumps(source_field, ensure_ascii=False))
                    label_class.expressionEngine = "Arcade"
                    label_class.visible = True
                text_symbol = getattr(label_class, "textSymbol", None)
                symbol = getattr(text_symbol, "symbol", None) if text_symbol else None
                if symbol is None:
                    continue
                symbol.height = size
                symbol.fontFamilyName = "Microsoft YaHei"
                symbol.fontStyleName = "Bold"
                symbol.haloSize = max(float(getattr(symbol, "haloSize", 0) or 0), 2.0)
                for ink in getattr(getattr(symbol, "symbol", None), "symbolLayers", None) or []:
                    if type(ink).__name__ == "CIMSolidFill":
                        ink.color.values = [24, 32, 40, 100]
                if getattr(symbol, "haloSymbol", None) is None:
                    halo = arcpy.cim.CIMPolygonSymbol()
                    fill = arcpy.cim.CIMSolidFill()
                    fill.enable = True
                    fill.color = arcpy.cim.CIMRGBColor()
                    fill.color.values = [255, 255, 255, 100]
                    halo.symbolLayers = [fill]
                    symbol.haloSymbol = halo
                for halo_layer in getattr(symbol.haloSymbol, "symbolLayers", None) or []:
                    if type(halo_layer).__name__ == "CIMSolidFill":
                        halo_layer.color.values = [255, 255, 255, 100]
            layer.setDefinition(cim)
            if parcel_only and layer.name in ("评价单元", "补充耕地图斑"):
                layer.showLabels = layer.name == "补充耕地图斑" and kind not in ("geo", "index")
        except Exception as exc:  # noqa: BLE001 - record a layer-specific failure
            print("标注字号调整失败 {}: {}: {}".format(layer.name, type(exc).__name__, exc))


def _coord_labels(layout, frame, center_x, center_y, scale):
    """Place sparse CGCS2000 coordinates in the band between the two neatlines."""
    spatial_reference = frame.map.spatialReference
    meters_per_unit = _meters_per_unit(spatial_reference)
    units_per_cm = float(scale) * 0.01 / meters_per_unit
    left, bottom = frame.elementPositionX, frame.elementPositionY
    width, height = frame.elementWidth, frame.elementHeight
    x_min = float(center_x) - (width / 2.0) * units_per_cm
    y_min = float(center_y) - (height / 2.0) * units_per_cm
    x_max = float(center_x) + (width / 2.0) * units_per_cm
    y_max = float(center_y) + (height / 2.0) * units_per_cm
    lon_lat_sr = arcpy.SpatialReference(4490)
    source_code = int(getattr(spatial_reference, "factoryCode", 0) or 0)
    if source_code > 0:
        point_source_sr = arcpy.SpatialReference(source_code)
    else:
        point_source_sr = arcpy.SpatialReference()
        point_source_sr.loadFromString(spatial_reference.exportToString())

    def project(x, y):
        point = arcpy.PointGeometry(arcpy.Point(x, y), point_source_sr).projectAs(lon_lat_sr).firstPoint
        return float(point.X), float(point.Y)

    elements = layout.listElements("TEXT_ELEMENT")
    prefixes = ("E上", "E下", "N左", "N右")
    label_groups = {prefix: [
        next((element for element in elements
              if element.name == "地图{}_{:02d}".format(
                  {"E上": "经度_上", "E下": "经度_下", "N左": "纬度_左", "N右": "纬度_右"}[prefix],
                  index)), None)
        for index in range(1, 4)] for prefix in prefixes}
    for element in elements:
        if (element.name or "").startswith((*prefixes, "坐标注记说明")):
            element.visible = False
        if (element.name or "").startswith(("地图经度_", "地图纬度_")):
            element.visible = False
    if any(any(label is None for label in label_groups[prefix]) for prefix in prefixes):
        raise RuntimeError("图廓模板不足 3 组经纬度注记位置")
    spec = visual_layout_spec("geo")
    size = spec["font_sizes"]["coordinate"]
    outer = spec["outer_frame"]
    for index, fraction in enumerate((0.2, 0.5, 0.8), 1):
        map_x = x_min + (x_max - x_min) * fraction
        lon_top, _ = project(map_x, y_max)
        lon_bottom, _ = project(map_x, y_min)
        page_x = left + width * fraction
        for prefix, value, page_y in (
                ("E上", lon_top, (bottom + height + outer["y"] + outer["height"]) / 2.0),
                ("E下", lon_bottom, (outer["y"] + bottom) / 2.0)):
            label = label_groups[prefix][index - 1]
            label.text = "{:.4f}°E".format(value)
            label.textSize = size
            label.elementPositionX = page_x
            label.elementPositionY = page_y
            label.visible = True
    for index, fraction in enumerate((0.25, 0.5, 0.75), 1):
        map_y = y_min + (y_max - y_min) * fraction
        _, lat_left = project(x_min, map_y)
        _, lat_right = project(x_max, map_y)
        page_y = bottom + height * fraction
        left_center = outer["x"] + (left - outer["x"]) / 2.0
        right_center = left + width + (outer["x"] + outer["width"] - left - width) / 2.0
        for prefix, value, page_x in (
                ("N左", lat_left, left_center),
                ("N右", lat_right, right_center)):
            label = label_groups[prefix][index - 1]
            label.text = "{:.4f}°N".format(value)
            label.textSize = size
            label.elementPositionX = page_x
            label.elementPositionY = page_y
            label.visible = True
    return {"longitude_labels_per_edge": 3, "latitude_labels_per_edge": 3,
            "spatial_reference": spatial_reference.name}


def _meters_per_unit(spatial_reference):
    """ArcGIS Pro 3.1 may not expose metersPerUnit for projected references."""
    code = int(getattr(spatial_reference, "linearUnitCode", 0) or 0)
    factors = {9001: 1.0, 9002: 0.3048, 9003: 1200.0 / 3937.0,
               9036: 1000.0}
    if code in factors:
        return factors[code]
    unit_name = str(getattr(spatial_reference, "linearUnitName", "") or "").lower()
    if "survey foot" in unit_name or "us foot" in unit_name:
        return 1200.0 / 3937.0
    if "foot" in unit_name or "feet" in unit_name:
        return 0.3048
    if "kilometer" in unit_name:
        return 1000.0
    if "centimeter" in unit_name:
        return 0.01
    if "millimeter" in unit_name:
        return 0.001
    if "meter" in unit_name:
        return 1.0
    raise RuntimeError("无法识别地图投影单位：{} ({})".format(unit_name, code))


def _remove_auto_layout_text(layout):
    for element in list(layout.listElements("TEXT_ELEMENT")):
        name = element.name or ""
        if name.startswith("map_auto_") or "_map_auto_" in name:
            element.delete()


def _coordinate_system_label(mapping):
    name = str(getattr(mapping.spatialReference, "name", "") or "未知坐标系")
    zone = re.search(r"3_Degree_GK_Zone_(\d+)", name)
    if zone:
        return "坐标系：CGCS2000 3°带第{}带".format(zone.group(1))
    return "坐标系：{}".format(name.replace("_", " "))


def _add_geo_unit_labels(layout, mapping, frame, target, plan, center_x, center_y, scale):
    """Label the planned original parcels; sampling-group geometry must not change parcel labels."""
    parcel_mode = target.get("label_mode") == "source_parcel"
    role = "补充耕地图斑" if parcel_mode else "评价单元"
    unit_layer = next((layer for layer in mapping.listLayers() if layer.name == role), None)
    if unit_layer is None:
        raise RuntimeError("地理位置图缺少评价单元图层，无法核对单元编号")
    unit_layer.showLabels = False
    field = target.get("parcel_label_field", plan.get("parcel_id_field", "DKBH")) if parcel_mode else (
        plan.get("filter_fields") or {}).get("评价单元", "PJDYBH")
    available_fields = {item.name.lower(): item.name for item in arcpy.ListFields(unit_layer.dataSource)}
    field = available_fields.get(field.lower())
    if not field:
        raise RuntimeError("评价单元图层缺少编号字段")
    spatial_reference = frame.map.spatialReference
    meters_per_unit = _meters_per_unit(spatial_reference)
    units_per_cm = float(scale) * 0.01 / meters_per_unit
    map_width, map_height = frame.elementWidth * units_per_cm, frame.elementHeight * units_per_cm
    x_min, y_min = float(center_x) - map_width / 2.0, float(center_y) - map_height / 2.0
    x_max, y_max = x_min + map_width, y_min + map_height
    expected = {str(value) for value in (target.get("source_parcels") if parcel_mode else target.get("unit_ids")) or []}
    positions = []
    geometries = {}
    with arcpy.da.SearchCursor(unit_layer.dataSource, [field, "SHAPE@"],
                               where_clause=_target_query(unit_layer, target, plan)) as cursor:
        for unit_id, geometry in cursor:
            unit_id = str(unit_id if unit_id is not None else "").strip()
            if expected and unit_id not in expected:
                continue
            geometries[unit_id] = geometries[unit_id].union(geometry) if unit_id in geometries else geometry
    for unit_id, geometry in geometries.items():
        centroid = geometry.trueCentroid
        x, y = float(centroid.X), float(centroid.Y)
        if x_min <= x <= x_max and y_min <= y <= y_max:
            positions.append((unit_id, frame.elementPositionX + (x - x_min) / units_per_cm,
                              frame.elementPositionY + (y - y_min) / units_per_cm))
    def unit_sort(row):
        return (0, int(row[0])) if row[0].isdigit() else (1, row[0])
    positions.sort(key=unit_sort)
    seen = {row[0] for row in positions}
    missing = sorted(expected - seen)
    if missing:
        raise RuntimeError("当前图幅内未找到计划地块编号：{}".format("、".join(missing)))
    if not positions:
        raise RuntimeError("当前地理位置图幅内没有可标注的评价单元")

    clusters = []
    remaining = list(positions)
    while remaining:
        cluster = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                if any(abs(candidate[1] - item[1]) <= 2.1 and abs(candidate[2] - item[2]) <= 0.65
                       for item in cluster):
                    cluster.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        clusters.append(sorted(cluster, key=unit_sort))
    labels = sorted((element for element in layout.listElements("TEXT_ELEMENT")
                     if (element.name or "").startswith("地块编号_")),
                    key=lambda element: element.name or "")
    if not labels:
        raise RuntimeError("缺少评价单元标注模板")
    if len(clusters) > len(labels):
        raise RuntimeError("当前图幅的地块簇超过模板标注容量")
    for label in labels:
        label.visible = False
    size = visual_layout_spec("geo")["font_sizes"]["unit_label"]
    frame_left, frame_bottom = frame.elementPositionX, frame.elementPositionY
    frame_right = frame_left + frame.elementWidth
    frame_top = frame_bottom + frame.elementHeight
    for cluster_index, cluster in enumerate(clusters):
        center_page_x = sum(row[1] for row in cluster) / len(cluster)
        center_page_y = sum(row[2] for row in cluster) / len(cluster)
        line_height = 0.47
        text_height = line_height * len(cluster)
        label = labels[cluster_index]
        label.text = "\n".join(row[0] if parcel_mode else (target.get("unit_labels") or {}).get(row[0], row[0]) for row in cluster)
        label.textSize = size
        label.elementWidth = 3.4
        label.elementHeight = text_height
        label.elementPositionX = min(max(center_page_x + 0.28, frame_left + 0.12), frame_right - 3.45)
        label.elementPositionY = min(max(center_page_y + 0.18, frame_bottom + 0.12), frame_top - text_height - 0.08)
        label.visible = True
    return {"expected": len(expected) if expected else len(positions),
            "labeled": len(positions), "clusters": [[row[0] for row in group] for group in clusters]}


def _add_scale_note(layout, target):
    """Add the ratio note and require a visible, scale-linked graphic scale."""
    spec = visual_layout_spec(target.get("kind"))
    label = next((element for element in layout.listElements("TEXT_ELEMENT")
                  if element.name == "比例尺注"), None)
    if label is None:
        raise RuntimeError("缺少比例尺文字样式模板")
    label.text = "比例尺  1:{:d}".format(int(round(float(target["scale"]))))
    label.textSize = spec["font_sizes"]["scale_note"]
    label.elementPositionX = spec["scale_note"]["x"]
    label.elementPositionY = spec["scale_note"]["y"]
    label.elementWidth = spec["scale_note"].get("width", 7.0)
    label.elementHeight = 0.5
    label.visible = True
    active_bar = next((element for element in layout.listElements("MAPSURROUND_ELEMENT")
                       if (element.name or "").startswith("Graphic Scale ")
                       and element.visible), None)
    if active_bar is None:
        raise RuntimeError("版面缺少与地图框关联的线段比例尺")
    return {"text": label.text, "size": label.textSize,
            "graphic_scale": active_bar.name}


def _area_panel(layout, rows, total_mu, anchor_name="面积统计标题"):
    area_elements = []
    for element in layout.listElements():
        if element.type != "TEXT_ELEMENT":
            continue
        name = element.name or ""
        if name == anchor_name:
            continue
        if name.startswith("面积"):
            area_elements.append(element)
    if not area_elements:
        return 0
    area_elements.sort(key=lambda element: (-element.elementPositionY, element.name or ""))
    lines = list(rows) + ["合计 {:.1f} 亩".format(total_mu)]
    positions = area_row_positions(len(lines))
    area_spec = visual_layout_spec()
    if len(lines) > len(positions):
        print("面积统计超过 10 行，其余单元见成果报告")
    while len(area_elements) < len(positions):
        try:
            area_elements.append(area_elements[0].clone("面积行{:02d}".format(len(area_elements) + 1)))
        except Exception as exc:  # noqa: BLE001
            print("创建面积行失败: {}: {}".format(type(exc).__name__, exc))
            break
    count = 0
    for index, element in enumerate(area_elements):
        if index >= len(positions):
            element.visible = False
            continue
        try:
            element.text = lines[index]
            element.elementPositionX, element.elementPositionY = positions[index]
            column = index // 5
            element.elementWidth = area_spec["area_row_widths"][min(column, 1)]
            element.elementHeight = area_spec["area_row_height"]
            element.visible = True
            count += 1
        except Exception as exc:  # noqa: BLE001
            print("设置面积行失败: {}: {}".format(type(exc).__name__, exc))
            break
    return count


def _apply_visual_layout(layout, target):
    """Apply the courseware's landscape map hierarchy without changing map data or CRS."""
    kind = target.get("kind")
    spec = apply_page_geometry(layout, kind)
    presentation = target.get('presentation') or {}
    scale = float(target.get("scale") or target.get("planned_scale") or 0)
    scale_bar_name = ("Graphic Scale km" if scale >= 100000 else
                      "Graphic Scale 100 m" if scale >= 5000 else
                      "Graphic Scale 10 m")
    for element in layout.listElements():
        name = element.name or ""
        if name == '区位图框' and not presentation.get('show_inset', True):
            element.visible = False
        if not presentation.get('show_coordinate_labels', True) and name.startswith(
                ('地图经度_', '地图纬度_', 'E上', 'E下', 'N左', 'N右', '坐标注记说明')):
            element.visible = False
        if name in spec["hide_elements"]:
            element.visible = False
            continue
        if name == "Scale bar":
            element.visible = True
        elif name in ("Graphic Scale 10 m", "Graphic Scale 100 m", "Graphic Scale km"):
            element.visible = name == scale_bar_name
            if element.visible:
                element.elementPositionX = 22.0
                element.elementPositionY = 1.25
        if not spec["show_area_panel"] and (name == "面积统计标题" or name.startswith("面积")):
            element.visible = False
        if kind in ("sample", "geo", "sheet", "index") and name in ("密级", "图号"):
            element.visible = False
        if element.type == "TEXT_ELEMENT":
            if name.startswith(("E上", "E下", "N左", "N右")):
                element.textSize = spec["font_sizes"]["coordinate"]
                element.visible = False
            elif name == "面积统计标题":
                element.textSize = spec["font_sizes"]["area_title"]
                element.elementWidth = spec["area_row_widths"][0]
                element.elementHeight = 0.42
            elif name.startswith("面积"):
                element.textSize = (spec["font_sizes"]["area_row"]
                                    if element.elementPositionX < 15
                                    else spec["font_sizes"]["area_row_secondary"])
            elif name == "影像来源":
                position = spec["metadata_positions"].get("影像来源")
                if position:
                    element.textSize = spec["font_sizes"].get("imagery_note", 6.0)
                    element.elementPositionX = position["x"]
                    element.elementPositionY = position["y"]
                    element.elementWidth = position["width"]
                    element.elementHeight = 0.42
                    element.visible = True
                else:
                    element.visible = False
            elif name in spec["metadata_positions"]:
                position = spec["metadata_positions"][name]
                element.textSize = spec["font_sizes"]["metadata"]
                element.elementPositionX = position["x"]
                element.elementPositionY = position["y"]
                element.elementWidth = position["width"]
                element.elementHeight = 0.5
            elif name in ("密级", "图号"):
                element.textSize = spec["font_sizes"]["page_header"]
            elif name == "图名":
                element.textSize = spec["font_sizes"]["title"]
                if spec["title"]:
                    element.elementPositionX = spec["title"]["x"]
                    element.elementPositionY = spec["title"]["y"]
                    element.elementWidth = spec["title"]["width"]
                    element.elementHeight = spec["title"]["height"]
            elif name == "区位图名":
                element.visible = False
            elif name == "比例尺注":
                element.visible = False
            elif name == "坐标注记说明":
                element.textSize = spec["font_sizes"]["coordinate"]
                element.elementPositionX = spec["coordinate_note"]["x"]
                element.elementPositionY = spec["coordinate_note"]["y"]
                element.visible = False
        elif name == "Legend":
            element.elementPositionX = spec["legend"]["x"]
            element.elementPositionY = spec["legend"]["y"]
            element.elementWidth = spec["legend"]["width"]
            element.elementHeight = spec["legend"]["height"]
        elif element.type == "MAPSURROUND_ELEMENT" and name == "North Arrow":
            arrow = spec.get("north_arrow")
            if arrow:
                element.elementPositionX = arrow["x"]
                element.elementPositionY = arrow["y"]
                element.elementWidth = arrow["width"]
                element.elementHeight = arrow["height"]


# 口径（2026-11-01 用户确认）：制图人/审核人不作必需项；图上仍显示“未提供”但不据此降级为预览图
REQUIRED_METADATA = (("org", "编制单位"), ("elevation_datum", "高程基准"))


def _missing_metadata(plan, target):
    meta = dict((plan or {}).get("meta") or {})
    meta.update((target or {}).get("meta") or {})
    return [label for key, label in REQUIRED_METADATA if not str(meta.get(key) or "").strip()]


def _compact_imagery_text(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) <= 2:
        return "\n".join(lines)
    return lines[0].rstrip("；;") + "；" + "；".join(line.lstrip("；;") for line in lines[1:-1]) + "\n" + lines[-1]


def _export_target(plan, target, mapping, layout, frame, filter_layers, imagery_layer, net,
                   layout_checks, inset_mapping=None):
    target = dict(target)
    missing_metadata = _missing_metadata(plan, target)
    if missing_metadata:
        target["preview_only"] = True
        title = str(target.get("title") or "")
        if "【设计预览】" not in title:
            target["title"] = "【设计预览】" + title
    current_render_key = _render_key(plan, target)
    target['presentation'] = presentation = map_presentation_options(plan, target)
    target["planned_scale"] = planned_scale(target)
    target["scale"] = target["planned_scale"]
    _remove_auto_layout_text(layout)
    for layer in mapping.listLayers():
        if layer.name == "评价单元":
            layer.showLabels = True
    parcel_only = target.get("label_mode") == "source_parcel"
    found_points = _style_sample_map_layers(mapping, parcel_only=parcel_only, presentation=presentation)
    if inset_mapping is not None:
        _style_sample_map_layers(inset_mapping, inset=True, parcel_only=parcel_only)
    if target.get("kind") in ("sample", "sheet") and not found_points:
        raise RuntimeError("采样图缺少设计采样点图层，无法应用红色菱形符号")
    _style_map_label_sizes(mapping, target.get("kind"), target)
    from .map_worker import enforce_layer_order
    drawing_order = enforce_layer_order(mapping)
    layout_checks.append({'item': '图层绘制顺序', 'status': 'PASS', 'detail': drawing_order})
    uid = target.get("uid")
    for element in layout.listElements():
        if element.name == "区位图框":
            element.visible = target.get("kind") == "geo" and presentation['show_inset']
        elif element.name == "区位图名":
            element.visible = False
    target_extent = arcpy.Extent(*target["extent"])
    query_checks = []
    for layer in filter_layers:
        try:
            where = _target_query(layer, target, plan)
            if layer.name in ("网格中心点", "方格网") and any(
                    field.name.upper() == "IN_UNIT" for field in arcpy.ListFields(layer.dataSource)):
                where += " AND {} = 1".format(arcpy.AddFieldDelimiters(layer.dataSource, "IN_UNIT"))
            if layer.name == '方格网':
                parcel_layer = next((item for item in filter_layers if item.name == '补充耕地图斑'), None)
                where = _grid_query_for_target_parcels(layer, target, plan, where, parcel_layer)
            layer.definitionQuery = where
            if layer.definitionQuery != where:
                raise RuntimeError("定义查询回读不一致")
            with arcpy.da.SearchCursor(layer.dataSource, ["OID@"], where_clause=where) as cursor:
                count = sum(1 for _ in cursor)
            if layer.name in ("补充耕地图斑", "评价单元", "设计采样点") and count < 1:
                raise RuntimeError("查询后目标要素数为0")
            layer.visible = layer.name != "网格交点"
            if layer.name == '网格中心点' and not presentation['show_grid_centers']:
                layer.visible = False
            if parcel_only and layer.name == "评价单元":
                layer.visible = False
            if target.get("kind") in ("geo", "index") and layer.name in ("方格网", "网格中心点", "设计采样点", "容重候选点"):
                layer.visible = False
            query_checks.append({"layer": layer.name, "where": where, "count": count, "status": "PASS"})
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("定义查询失败 {}（单元 {}）：{}".format(
                layer.name, uid if uid is not None else "总览", exc))
    if "imagery_path" in target and not target.get("imagery_path"):
        if imagery_layer is not None:
            imagery_layer.visible = False
    elif target.get("imagery_path") or target.get("imagery"):
        if imagery_layer is None:
            raise RuntimeError("影像切换失败（单元 {}）：工程中没有影像底图图层".format(uid))
        image_target = target.get("imagery_path") or target["imagery"].get("output")
        if not image_target or not os.path.exists(image_target):
            raise RuntimeError("影像切换失败（单元 {}）：目标影像不存在 {}".format(uid, image_target))
        try:
            connection = imagery_layer.connectionProperties
            replacement = copy.deepcopy(connection)
            replacement["connection_info"]["database"] = os.path.dirname(image_target)
            replacement["dataset"] = os.path.basename(image_target)
            changed = imagery_layer.updateConnectionProperties(connection, replacement)
            if changed is False:
                raise RuntimeError("数据源连接未更新")
            if imagery_layer.isBroken or os.path.normcase(os.path.normpath(imagery_layer.dataSource)) != os.path.normcase(os.path.normpath(image_target)):
                raise RuntimeError("影像数据源回读不匹配")
            # updateConnectionProperties keeps the old raster colorizer statistics.
            # Probe the target raster once and copy its renderer before exporting.
            probe = mapping.addDataFromPath(image_target)
            try:
                probe_cim = probe.getDefinition("V3")
                target_cim = imagery_layer.getDefinition("V3")
                colorizer = getattr(probe_cim, "colorizer", None)
                if colorizer is None:
                    raise RuntimeError("目标栅格没有可用的色彩渲染器")
                # XYZ/WMTS tile mosaics are already rendered 8-bit RGB(A).
                # ArcGIS' default percentile+gamma stretch changes their cartographic colors.
                raster_source = arcpy.Raster(image_target)
                if (getattr(raster_source, "pixelType", None) == "U8"
                        and getattr(raster_source, "bandCount", 0) in (3, 4)
                        and hasattr(colorizer, "stretchType")
                        and hasattr(colorizer, "useGammaStretch")):
                    colorizer.stretchType = "None"
                    colorizer.useGammaStretch = False
                target_cim.colorizer = copy.deepcopy(colorizer)
                imagery_layer.setDefinition(target_cim)
            finally:
                mapping.removeLayer(probe)
            imagery_layer.visible = True
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("影像切换失败（单元 {}）：{}".format(uid, exc))
    imagery_visible = bool(imagery_layer and getattr(imagery_layer, "visible", False)
                           and not getattr(imagery_layer, "isBroken", False))
    target = _mark_target_preview_only(target, imagery_visible)
    scale_adjustment = None
    if net is not None:
        try:
            net.visible = bool(target.get("show_net", True) and presentation['show_frame_grid'])
            if net.visible != bool(target.get("show_net", True) and presentation['show_frame_grid']):
                raise RuntimeError("图层可见性回读不一致")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("方里网图层切换失败（单元 {}）：{}".format(uid, exc))

    camera = frame.camera
    camera.X = (target_extent.XMin + target_extent.XMax) / 2.0
    camera.Y = (target_extent.YMin + target_extent.YMax) / 2.0
    camera.scale = target["scale"]
    frame.camera = camera
    text_values = [
        ("图名", display_title(target)),
        ("坐标系", _coordinate_system_label(mapping)),
        ("影像来源", _compact_imagery_text(target.get("imagery_text")) or "影像底图：未提供；不得作为报件图"),
    ] + list(map_metadata_values(plan, target).items())
    for name, value in text_values:
        if not _set_text(layout, name, value):
            raise RuntimeError("版面要素写入失败：{}".format(name))
    show_area_panel = visual_layout_spec(target.get("kind"))["show_area_panel"]
    if show_area_panel:
        area_rows = _area_panel(layout, target["rows"], target["total_mu"])
    else:
        area_rows = 0
    _apply_visual_layout(layout, target)
    actual_extent=frame.camera.getExtent()
    scale_measurement=audit_map_scale(target['scale'],
        [actual_extent.XMin,actual_extent.YMin,actual_extent.XMax,actual_extent.YMax],
        frame.elementWidth,frame.elementHeight,target['extent'],_meters_per_unit(mapping.spatialReference))
    if scale_measurement['status']=='FAIL':
        raise RuntimeError('比例尺实测或计划地块覆盖检查失败：'+str(scale_measurement))
    inset_audit = None
    if target.get("kind") == "geo" and inset_mapping is not None and presentation['show_inset']:
        inset_frame = next((element for element in layout.listElements("MAPFRAME_ELEMENT")
                            if element.name == "区位图框"), None)
        extent_layer = next((layer for layer in inset_mapping.listLayers()
                             if layer.name in ("全部原始地块", "全部评价单元")), None)
        if inset_frame is not None and extent_layer is not None:
            inset_camera = inset_frame.camera
            inset_camera.setExtent(inset_frame.getLayerExtent(extent_layer, False, False))
            inset_camera.scale *= 1.2
            inset_frame.camera = inset_camera
            expected_extent = inset_frame.getLayerExtent(extent_layer, False, False)
            rendered_extent = inset_camera.getExtent()
            covers = (rendered_extent.XMin <= expected_extent.XMin and rendered_extent.YMin <= expected_extent.YMin
                      and rendered_extent.XMax >= expected_extent.XMax and rendered_extent.YMax >= expected_extent.YMax)
            inset_audit = {"covers_all": covers, "source_layer": extent_layer.name}
            if not covers:
                raise RuntimeError("项目索引小图未包含全部本次地块")
    coordinate_audit = None
    if visual_layout_spec(target.get("kind"))["show_coordinate_labels"] and presentation['show_coordinate_labels']:
        coordinate_audit = _coord_labels(layout, frame, camera.X, camera.Y, target["scale"])
    unit_label_audit = None
    if target.get("kind") in ("geo", "index"):
        unit_label_audit = _add_geo_unit_labels(
            layout, mapping, frame, target, plan, camera.X, camera.Y, target["scale"])
    scale_audit = _add_scale_note(layout, target)
    layout_audit = audit_layout_snapshot(collect_layout_snapshot(layout))
    export_contract = layout_export_contract(layout, plan.get('dpi', 300))

    png = target["png"]
    jpg = target.get("jpg") or os.path.splitext(png)[0] + ".jpg"
    pdf = target["pdf"]
    os.makedirs(os.path.dirname(png), exist_ok=True)
    staged = {ext: os.path.splitext(path)[0] + ".partial." + ext for ext, path in (("jpg", jpg), ("png", png), ("pdf", pdf))}
    layout.exportToJPEG(staged["jpg"], resolution=int(plan.get("dpi", 300)), jpeg_quality=95)
    layout.exportToPNG(staged["png"], resolution=int(plan.get("dpi", 300)))
    layout.exportToPDF(staged["pdf"], resolution=int(plan.get("dpi", 300)), image_quality="BEST")
    for ext, destination in (("jpg", jpg), ("png", png), ("pdf", pdf)):
        checked = verify_export_artifact(staged[ext], ext, expected=export_contract)
        if checked["status"] == "FAIL":
            raise RuntimeError("{} 导出文件复读失败：{}".format(ext, checked.get("detail")))
        os.replace(staged[ext], destination)
    artifacts = [verify_export_artifact(jpg, "jpg", expected=export_contract), verify_export_artifact(png, "png", expected=export_contract),
                 verify_export_artifact(pdf, "pdf", expected=export_contract)]
    thumbnail = os.path.join(os.path.dirname(png), "thumbnails", target["name"] + ".jpg")
    if all(row["status"] != "FAIL" for row in artifacts[:2]):
        create_thumbnail(png, thumbnail)
        thumbnail_check = verify_export_artifact(thumbnail, "jpg")
    else:
        thumbnail_check = {"status": "NEEDS_REVIEW", "detail": "源 PNG 未通过解码，未生成缩略图"}
    checks = list(layout_checks)
    checks.append({'item':'实际比例尺与地块覆盖','status':scale_measurement['status'],'detail':scale_measurement})
    checks.extend(layout_audit['checks'])
    if coordinate_audit:
        checks.append({"item": "图廓经纬度", "status": "PASS",
                       "detail": "CGCS2000 转经纬度；每条经纬边各 {} 个稀疏注记".format(
                           coordinate_audit["longitude_labels_per_edge"]),
                       "spatial_reference": coordinate_audit["spatial_reference"]})
    if unit_label_audit:
        label_status = "PASS" if unit_label_audit["expected"] == unit_label_audit["labeled"] else "FAIL"
        checks.append({"item": "原始地块编号与排序" if parcel_only else "评价单元编号与排序", "status": label_status,
                       "detail": "{}个数据编号均显示；近邻按编号升序分组".format(
                           unit_label_audit["labeled"]),
                       "clusters": unit_label_audit["clusters"]})
    if parcel_only:
        checks.append({"item": "正式评价单元编号", "status": "NEEDS_REVIEW",
                       "detail": "按原始DIKUAI_NO标注地块；15位正式评价单元号未提供，关联字段留空"})
    if scale_audit:
        checks.append({"item": "比例尺文字字号", "status": "PASS",
                       "detail": "比例尺文字使用正文样式重排，图面比例值保持不变",
                       "text": scale_audit["text"], "layout_size": scale_audit["size"]})
    if show_area_panel:
        area_status = "FAIL" if area_rows == 0 else "PASS" if area_rows == len(target["rows"]) + 1 else "NEEDS_REVIEW"
        area_detail = "已写 {} 行，应写 {} 行".format(area_rows, len(target["rows"]) + 1)
    else:
        area_status = "PASS"
        area_detail = "按课件样张不显示面积统计表"
    checks.append({"item": "面积表", "status": area_status, "detail": area_detail})
    if target.get("kind") == "geo":
        missing_roles = target.get("missing_context_roles")
        if missing_roles is None:
            checks.append({"item": "地理位置图地物", "status": "NEEDS_REVIEW",
                           "detail": "渲染计划未记录居民点、道路、水系、境界线角色核对结果"})
        elif missing_roles:
            checks.append({"item": "地理位置图地物", "status": "NEEDS_REVIEW",
                           "detail": "缺少：" + "、".join(missing_roles)})
        else:
            checks.append({"item": "地理位置图地物", "status": "PASS",
                           "detail": "已提供居民点、道路、水系和境界线图层"})
    if scale_adjustment:
        checks.append({"item": "地理图比例尺适配", "status": "PASS",
                       "detail": "按当前影像覆盖将 1:{:g} 调整为 1:{:g}，避免空白边框".format(
                           scale_adjustment["planned"], scale_adjustment["rendered"])})
    checks.append({"item": "图层查询", "status": "PASS", "layers": query_checks})
    checks.extend({"item": row["kind"], "status": row["status"], "detail": row.get("detail"),
                   "path": row["path"]} for row in artifacts)
    checks.append({"item": "复核缩略图", "status": thumbnail_check["status"],
                   "detail": thumbnail_check.get("detail"), "path": thumbnail})
    if not imagery_layer or not imagery_layer.visible:
        checks.append({"item": "影像覆盖", "status": "NEEDS_REVIEW",
                       "detail": "缺少可见影像图层；可输出预览，不能标记为正式底图通过"})
    quality = target.get("imagery_quality") or {}
    if quality.get("content_role") == "vector" and (quality.get("annotation") or {}).get("fail", 1):
        checks.append({"item": "地名道路注记", "status": "NEEDS_REVIEW", "detail": "矢量注记未完整取得，需检查地名和道路表达"})
    if quality.get("frame_coverage") and quality["frame_coverage"].get("coverage_status") != "PASS":
        checks.append({"item": "完整图框底图覆盖", "status": "NEEDS_REVIEW",
                       "detail": "底图未覆盖完整图框；保留规划比例尺与地块范围，需补齐底图，未裁掉地块适配影像"})
    if quality.get("pixel_qa"):
        checks.append({"item": "影像逐像元检查", "status": quality["pixel_qa"]["status"],
                       "detail": quality["pixel_qa"]})
    if quality.get("coverage_status") == "FAIL":
        checks.append({"item": "影像覆盖", "status": "FAIL", "detail": "未获取到有效影像覆盖"})
    elif quality.get("coverage_status") != "PASS":
        checks.append({"item": "影像覆盖", "status": "NEEDS_REVIEW", "detail": "有效覆盖范围尚未证明"})
    checks.extend(imagery_module.export_imagery_evidence_checks(quality))
    sampling_diagnostics = None
    if imagery_visible and imagery_layer:
        sampling_diagnostics = imagery_module.raster_sampling_diagnostics(
            imagery_layer.dataSource, frame.camera.scale, plan.get('dpi', 300), arcpy_module=arcpy)
        checks.append({'item': '像元与纸面采样',
            'status': 'PASS' if sampling_diagnostics['status'] == 'MEASURED' else 'NEEDS_REVIEW',
            'detail': sampling_diagnostics})
    if target.get("scale_recommendation") == "NEEDS_REVIEW":
        checks.append({"item": "比例尺建议", "status": "NEEDS_REVIEW",
                       "detail": "超过 1:10000 建议比例尺；这是建议项，不单独判不合格"})
    if missing_metadata:
        checks.append({"item": "图面元数据", "status": "NEEDS_REVIEW",
                       "detail": "缺少：" + "、".join(missing_metadata) + "；仅可作设计预览，不能作为报件图"})
    statuses = {row["status"] for row in checks}
    status = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS"
    output = {"target": target["name"], "title": display_title(target), "kind": target.get("kind"),
              "sampling_diagnostics": sampling_diagnostics,
            "parcel": target.get("parcel"), "unit": uid, "jpg": jpg, "png": png,
            "scale_key": target.get('scale_key'), "automatic_scale": target.get('automatic_scale', target['scale']),
            "pdf": pdf, "thumbnail": thumbnail, "scale": target["scale"],
            "planned_scale": target["planned_scale"], "scale_adjustment": scale_adjustment,
            "checks": checks, "status": status, "artifacts": artifacts, 'export_contract': export_contract,
            'layout_audit': layout_audit,
            'scale_measurement': scale_measurement, 'render_key': current_render_key,
            "inset_audit": inset_audit,
            "imagery_visible": imagery_visible,
            "ink": "【设计预览】" if "【设计预览】" in display_title(target) else "",
            "preview_only": bool(target.get("preview_only")),
            "bytes": {row["kind"]: row.get("bytes", 0) for row in artifacts}}
    return _refresh_imagery_evidence(output, target)


def _implementation_digest():
    digest = hashlib.sha256()
    for name in ("map_export.py", "map_validation.py", "map_layout_style.py", "map_worker.py"):
        with open(os.path.join(os.path.dirname(__file__), name), "rb") as stream:
            digest.update(stream.read())
    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    for name in ("补充耕地鉴定图_A4.pagx", "补充耕地鉴定图_A4_区位.pagx"):
        path = os.path.join(template_dir, name)
        if os.path.isfile(path):
            with open(path, "rb") as stream:
                digest.update(stream.read())
    return digest.hexdigest()


def _render_key(plan, target):
    # 键只描述“画什么”：预览身份与标题（预览前缀在图上，属输出标注而非内容）不参与，
    # 否则同一次执行里先加前缀再比对会把渲染键算成不同值，导致修复场景永远无法复用
    target = {key: value for key, value in (target or {}).items()
              if key not in ("preview_only", "title", "deliverable_status")}
    inputs = {"target": target, "dpi": plan.get("dpi"), "filters": plan.get("filter_fields"),
              "page_metadata": map_metadata_values(plan, target),
              "presentation": map_presentation_options(plan, target),
              "implementation": _implementation_digest()}
    for name, path in (("project", plan.get("project")), ("imagery", target.get("imagery_path"))):
        if path and os.path.isfile(path):
            stat = os.stat(path)
            inputs[name] = {"path": path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _reusable_export(row, key, target=None):
    """是否可复用上次渲染。预览图与正式图不能互相复用：图上是否带【设计预览】不同。"""
    if row.get("render_key") != key or row.get("status") == "FAIL":
        return False
    if target is not None:
        wanted = "【设计预览】" if "【设计预览】" in display_title(target) else ""
        if row.get("ink", "【设计预览】" if row.get("title", "").startswith("【设计预览】") else "") != wanted:
            return False
    prior_hashes = {item.get("kind"): item.get("sha256") for item in row.get("artifacts", [])}
    for ext in ("jpg", "png", "pdf"):
        checked = verify_export_artifact(row.get(ext, ""), ext, expected=row.get('export_contract'))
        if checked["status"] == "FAIL" or not prior_hashes.get(ext) or checked.get("sha256") != prior_hashes[ext]:
            return False
    return True


def _export_targets_isolated(plan_path, plan, checkpoint=None):
    """Render each target in a fresh Pro Python process to avoid layout state leaking between sheets."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    temp_root = os.path.join(root, "temp")
    os.makedirs(temp_root, exist_ok=True)
    result_path = os.path.splitext(plan_path)[0] + ".result.json"
    try:
        with open(result_path, encoding="utf-8") as stream:
            previous = {row["target"]: row for row in json.load(stream).get("outputs", [])}
    except (OSError, ValueError, KeyError):
        previous = {}

    outputs, layout_checks = [], []
    with tempfile.TemporaryDirectory(prefix="map_export_targets_", dir=temp_root) as work_dir:
        for index, target in enumerate(plan.get("targets", []), 1):
            if checkpoint:
                checkpoint(target['name'], index, len(plan['targets']))
            key = _render_key(plan, target)
            prior = previous.get(target.get("name"), {})
            if _reusable_export(prior, key, target):
                output = _refresh_imagery_evidence(dict(prior, reused=True), target)
                outputs.append(output)
                _atomic_json(result_path, {"project": plan.get("project"), "outputs": outputs,
                                           "status": "RUNNING", "layout_checks": layout_checks})
                continue

            child_plan = dict(plan, targets=[target])
            child_path = os.path.join(work_dir, "target_{:03d}.json".format(index))
            with open(child_path, "w", encoding="utf-8") as stream:
                json.dump(child_plan, stream, ensure_ascii=False, indent=2)
            child_result = os.path.splitext(child_path)[0] + ".result.json"
            env = dict(os.environ)
            existing_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = root + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "acceptance.map_export", child_path],
                    cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=1200, stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
                if not os.path.isfile(child_result):
                    raise RuntimeError("独立出图进程没有写出结果；{}".format(
                        ((process.stdout or "") + (process.stderr or ""))[-1200:]))
                with open(child_result, encoding="utf-8") as stream:
                    child = json.load(stream)
                child_outputs = child.get("outputs", [])
                if not child_outputs:
                    raise RuntimeError("独立出图进程结果为空，returncode={}".format(process.returncode))
                output = child_outputs[0]
                output = _refresh_imagery_evidence(output, target)
                if process.returncode != 0 and output.get("status") != "FAIL":
                    output["status"] = "FAIL"
                    output.setdefault("checks", []).append({"item": "独立出图进程",
                        "status": "FAIL", "detail": "returncode={}".format(process.returncode)})
                for check in child.get("layout_checks", []):
                    if check not in layout_checks:
                        layout_checks.append(check)
            except Exception as exc:  # noqa: BLE001 - record a per-target failure and continue
                output = {"target": target.get("name"), "unit": target.get("uid"), "status": "FAIL",
                          "checks": [{"item": "独立出图进程", "status": "FAIL", "detail": str(exc)}]}
            outputs.append(output)
            _atomic_json(result_path, {"project": plan.get("project"), "outputs": outputs,
                                       "status": "RUNNING", "layout_checks": layout_checks})

    statuses = {row.get("status", "FAIL") for row in outputs}
    status = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS"
    result = {"project": plan.get("project"), "outputs": outputs, "status": status,
              "layout_checks": layout_checks}
    _atomic_json(result_path, result)
    return result


def _install_courseware_layout(aprx, mapping, maps, profile=None):
    """Replace the project layout with the packaged courseware template or a configured .pagx."""
    from .map_profile import resolve_pagx
    map_by_name = {item.name: item for item in maps}
    inset_mapping = map_by_name.get("项目地块分布索引")
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
    template_path = resolve_pagx(profile, inset_mapping is not None, template_dir)
    for old_layout in list(aprx.listLayouts()):
        aprx.deleteItem(old_layout)
    layout = aprx.importDocument(template_path, include_layout=True)
    main_frame = next((item for item in layout.listElements("MAPFRAME_ELEMENT")
                       if item.name in ("WEBMAP_MAP_FRAME", "主图框")), None)
    if main_frame is None:
        raise RuntimeError("课件版式模板中没有主图框")
    main_frame.map = mapping
    inset_frame = next((item for item in layout.listElements("MAPFRAME_ELEMENT")
                        if item.name == "区位图框"), None)
    if inset_frame is not None:
        if inset_mapping is None:
            inset_frame.visible = False
        else:
            inset_frame.map = inset_mapping
    # Set side-coordinate text orientation while the imported page is still
    # untouched; changing textAngle after geometry edits can crash Pro 3.1.
    for element in layout.listElements("TEXT_ELEMENT"):
        if element.name.startswith("地图纬度_左_"):
            element.textAngle = 90
        elif element.name.startswith("地图纬度_右_"):
            element.textAngle = 270
    # Raster RGB bands are not useful legend categories; keep the raster on
    # the map but suppress its auto-generated legend entry.
    for layer in mapping.listLayers():
        if layer.name == "影像底图":
            definition = layer.getDefinition("V3")
            if hasattr(definition, "showLegends"):
                definition.showLegends = False
                layer.setDefinition(definition)
    return layout, inset_mapping


def export_all(plan_path, checkpoint=None):
    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)
    if len(plan.get("targets", [])) > 1 and os.environ.get("ARCGIS_MAP_EXPORT_SINGLE_TARGET") != "1":
        return _export_targets_isolated(plan_path, plan, checkpoint=checkpoint)
    aprx = mp.ArcGISProject(plan["project"])
    want = plan.get("map_name")
    maps = aprx.listMaps()
    mapping = next((m for m in maps if m.name == want), maps[0])
    layout, inset_mapping = _install_courseware_layout(
        aprx, mapping, maps, (plan.get("meta") or {}).get("layout_profile"))
    # CIM styling must precede page/text geometry edits; ArcGIS Pro cannot
    # reliably deserialize some updated text shapes afterward.
    style_inset_frame(layout)
    style_layout_typography(layout, scale=planned_scale(plan['targets'][0]),
                            show_frame_grid=map_presentation_options(plan, plan['targets'][0])['show_frame_grid'])
    style_legend_background(layout)
    style_legend_typography(layout)
    # Reuse the project legend freeze so raster probes during export cannot
    # re-add RGB bands after the last CIM layout update.
    from .map_worker import _freeze_legend
    excluded_legend = ['影像底图', '网格交点', '方里网']
    if not map_presentation_options(plan, {})['show_grid_centers']:
        excluded_legend.append('网格中心点')
    _freeze_legend(layout, excluded_legend)
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
        raise RuntimeError("工程中未找到地图框")
    frame.map = mapping

    layer_by_name = {}
    for layer in mapping.listLayers():
        try:
            layer_by_name[layer.name] = layer
        except Exception:  # noqa: BLE001
            continue
    filter_layers = [layer_by_name[n] for n in
                     ("补充耕地图斑", "评价单元", "方格网", "网格交点", "网格中心点", "设计采样点", "容重候选点")
                     if n in layer_by_name]
    imagery_layer = layer_by_name.get("影像底图")
    net = layer_by_name.get("方里网")
    sheet_index = layer_by_name.get("图幅索引")

    outputs = []
    result_path = os.path.splitext(plan_path)[0] + ".result.json"
    try:
        with open(result_path, encoding="utf-8") as stream:
            previous = {row["target"]: row for row in json.load(stream).get("outputs", [])}
    except (OSError, ValueError, KeyError):
        previous = {}
    expected_layers = ("补充耕地图斑", "评价单元", "方格网", "网格交点", "网格中心点", "设计采样点")
    missing_layers = [name for name in expected_layers if name not in layer_by_name]
    if missing_layers:
        raise RuntimeError("出图图层缺失：{}".format("、".join(missing_layers)))
    layout_elements = {element.name: element for element in layout.listElements()}
    layout_checks = []
    for name in ("图名", "编制单位", "成图日期", "投影", "比例尺注", "North Arrow", "Legend"):
        element = layout_elements.get(name)
        if element is None or not getattr(element, "visible", True):
            layout_checks.append({"item": name, "status": "FAIL", "detail": "版面要素缺失或隐藏"})
    for name in ("编制单位", "成图日期", "投影"):
        element = layout_elements.get(name)
        text = str(getattr(element, "text", "") or "") if element else ""
        if not text.partition("：")[2].strip():
            layout_checks.append({"item": name, "status": "FAIL", "detail": "内容为空"})
    for target in plan["targets"]:
        key = _render_key(plan, target)
        try:
            if sheet_index is not None:
                sheet_index.visible = target.get("kind") == "index"
            prior = previous.get(target["name"], {})
            if _reusable_export(prior, key, target):
                output = dict(prior, reused=True)
            else:
                output = _export_target(plan, target, mapping, layout, frame,
                                        filter_layers, imagery_layer, net, layout_checks,
                                        inset_mapping=inset_mapping)
                output["render_key"] = key
            output = _refresh_imagery_evidence(output, target)
            # 明确写出本轮是复用还是重渲染；此前从未写入，上层只能看到空值
            output["reused"] = bool(output.get("reused"))
            outputs.append(output)
        except Exception as exc:  # noqa: BLE001 - 单幅失败记账后继续其他图幅
            outputs.append({"target": target.get("name"), "unit": target.get("uid"), "status": "FAIL",
                            "checks": [{"item": "单幅出图", "status": "FAIL", "detail": str(exc)}]})
        _atomic_json(result_path, {"project": plan["project"], "outputs": outputs,
                                   "status": "RUNNING", "layout_checks": layout_checks})
    statuses = {row["status"] for row in outputs}
    status = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS"
    return {"project": plan["project"], "outputs": outputs, "status": status,
            "layout_checks": layout_checks}


def main(argv):
    plan_path = argv[1]
    result = export_all(plan_path)
    out = plan_path.replace(".json", ".result.json")
    _atomic_json(out, result)
    print("MAP_EXPORT_OK", out, len(result["outputs"]))


if __name__ == "__main__":
    main(sys.argv)
