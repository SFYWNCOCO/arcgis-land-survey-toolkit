"""Readable A4 layout spacing and typography for acceptance map exports."""
import math
import datetime

CHINESE_FONT = "Microsoft YaHei"
LATIN_FONT = "Arial"


def set_layout_text(layout, name, text):
    """Write a named text element; report missing or unwritable elements to the caller."""
    for element in layout.listElements():
        if element.type == "TEXT_ELEMENT" and element.name == name:
            try:
                element.text = text
                return True
            except Exception:  # noqa: BLE001 - retain the existing caller-visible result
                return False
    return False


def nice_scale_bar(scale):
    """Choose five clean 1/2/5 divisions without changing the map scale."""
    scale = float(scale)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('比例尺必须为有限正数')
    exponent = math.floor(math.log10(scale / 150))
    choices = []
    for power in range(exponent - 2, exponent + 3):
        for factor in (1, 2, 5):
            division = factor * 10.0 ** power
            paper = division * 5 * 100 / scale
            choices.append({'division_m': division, 'divisions': 5, 'total_m': division * 5, 'paper_cm': paper})
    fitting = [item for item in choices if 2.5 - 1e-9 <= item['paper_cm'] <= 4 + 1e-9]
    return min(fitting or choices, key=lambda item: (abs(item['paper_cm'] - 3.25), item['paper_cm']))


def map_presentation_options(plan, target):
    """Resolve project and sheet appearance independently from business review status."""
    # 默认口径（2026-10-31 用户确认）：区位小图默认开；预览图不显示【设计预览】前缀
    options = {'show_inset': True, 'show_coordinate_labels': False,
               'show_frame_grid': False, 'show_grid_centers': False,
               'show_preview_label': False, 'grid_line_width': 0.45,
               'grid_line_color': [190, 195, 175, 45]}
    options.update(((plan or {}).get('meta') or {}).get('presentation') or {})
    options.update((target or {}).get('presentation') or {})
    return options


def map_metadata_values(plan, target, today=None):
    meta = dict((plan or {}).get("meta") or {})
    meta.update((target or {}).get("meta") or {})
    day = datetime.date.fromisoformat(str(meta['map_date'])) if meta.get('map_date') else today or datetime.date.today()
    return {
        "编制单位": "编制单位：" + str(meta.get("org") or "（未提供，不得报件）"),
        "成图日期": "制图日期：{}年{:02d}月{:02d}日".format(day.year, day.month, day.day),
        "高程基准": "高程基准：" + str(meta.get("elevation_datum") or "未提供（以项目资料为准）"),
        "投影": "投影：高斯—克吕格（3°分带）",
        "制图审核": "制  图：{}    审  核：{}".format(meta.get("drawn_by") or "未提供", meta.get("checked_by") or "未提供"),
    }


def source_parcel_labels(unit_rows):
    """Display source parcel IDs without presenting generated grouping keys as official IDs."""
    labels = {}
    for row in unit_rows:
        values = row.get("parcels") or []
        if not values or any(value is None or not str(value).strip() for value in values):
            raise ValueError("缺少原始地块号，不能用临时单元号替代")
        values = sorted({str(value).strip() for value in values},
                        key=lambda value: (0, int(value), value) if value.isdigit() else (1, value))
        labels[str(row["unit"])] = "地块" + "、".join(values)
    return labels


def area_row_positions(count, base_y=4.85, step=0.42):
    columns = (10.4, 15.55)
    return [
        (columns[index // 5], base_y - step * (index % 5))
        for index in range(min(max(int(count), 0), 10))
    ]


def planned_scale(target):
    """Keep the reviewed plan's denominator; never silently zoom past courseware scale."""
    scale = float(target.get("scale") or 0)
    if not scale > 0:
        raise ValueError("渲染计划必须提供大于 0 的明确比例尺")
    return scale


def fit_scale_to_imagery(planned, raster_size_m, frame_width_cm, frame_height_cm, step=500):
    """Keep a geographic raster inside its frame by choosing the nearest finer scale."""
    width_m, height_m = (float(value) for value in raster_size_m)
    width_cm, height_cm = float(frame_width_cm), float(frame_height_cm)
    scale = float(planned)
    if width_m <= 0 or height_m <= 0 or width_cm <= 0 or height_cm <= 0 or step <= 0:
        raise ValueError("影像范围和图框尺寸必须大于 0")
    maximum = min(width_m * 100.0 / width_cm, height_m * 100.0 / height_cm)
    if scale <= maximum:
        return scale
    fitted = math.floor(maximum / float(step)) * float(step)
    if fitted <= 0:
        raise ValueError("影像覆盖范围不足以支撑最小可用比例尺")
    return min(scale, fitted)


def display_title(target):
    """Render the requested title appearance independently of review status."""
    title = str(target.get("title") or "").strip()
    presentation = map_presentation_options({}, target)
    if presentation.get('show_preview_label') is False:
        title = title.replace('【设计预览】', '').strip()
    if title:
        return title
    if target.get("kind") == "sample":
        return "补充耕地质量鉴定采样点位布置图"
    if target.get("kind") == "geo":
        return "补充耕地质量鉴定地理位置图"
    return ""


def location_map_title(title, context_layers):
    """Mark a location sheet as a preview until all required context roles are present."""
    required = ("居民点", "道路", "水系", "境界线")
    present = {str(item.get("role") or "") for item in context_layers or []}
    missing = [role for role in required if role not in present]
    value = str(title or "补充耕地质量鉴定地理位置图")
    if missing:
        value += "（预览，待补地理要素）"
    return value, missing


def visual_layout_spec(kind=None):
    """Return page-space settings; acceptance map sheets follow the courseware's landscape panels."""
    map_frame = {"x": 1.2, "y": 8.2, "width": 18.6, "height": 18.5}
    outer_frame = None
    scale_bar = None
    hide_elements = ["影像来源", "制图审核"]
    legend = {"x": 1.2, "y": 6.8, "width": 8.2, "height": 2.09}
    scale_note = {"x": 1.4, "y": 5.2}
    coordinate_note = {"x": 1.4, "y": 2.7}
    metadata_y = {
        "坐标系": 9.7, "投影": 9.05, "高程基准": 8.4,
        "编制单位": 7.75, "成图日期": 7.1, "制图审核": 6.45,
    }
    show_area_panel = True
    show_coordinate_labels = True
    if kind in ("sample", "geo", "sheet", "index"):
        page_size = {"width": 29.7, "height": 21.0}
        map_frame = {"x": 1.35, "y": 3.3, "width": 27.0, "height": 15.4}
        outer_frame = {"x": 0.95, "y": 2.85, "width": 27.8, "height": 16.6}
        legend = {"x": 1.05, "y": 2.45, "width": 4.2, "height": 2.15}
        scale_bar = {"x": 20.15, "y": 1.25, "width": 6.4, "height": 1.0}
        scale_note = {"x": 20.25, "y": 0.9, "width": 6.7}
        coordinate_note = {"x": 1.55, "y": 0.7}
        metadata_positions = {
            "坐标系": {"x": 5.75, "y": 2.27, "width": 5.95},
            "投影": {"x": 5.75, "y": 1.74, "width": 5.95},
            "高程基准": {"x": 5.75, "y": 1.21, "width": 5.95},
            "编制单位": {"x": 12.0, "y": 2.27, "width": 7.6},
            "成图日期": {"x": 12.0, "y": 1.74, "width": 7.6},
            "制图审核": {"x": 12.0, "y": 1.21, "width": 7.6},
            "影像来源": {"x": 5.75, "y": 0.70, "width": 13.5},
        }
        title = {"x": 14.85, "y": 20.35, "width": 26.7, "height": 0.65}
        north_arrow = {"x": 27.55, "y": 0.72, "width": 0.82, "height": 0.82}
        inset = {
            "x": 1.7, "y": 14.6, "width": 5.8, "height": 3.45,
            "title_x": 1.7, "title_y": 18.1, "title_width": 5.8, "title_height": 0.38,
        }
        show_area_panel = False
        show_coordinate_labels = True
    else:
        page_size = None
        metadata_positions = {}
        title = None
        north_arrow = None
        inset = {
            "x": 1.6,
            "y": 22.0,
            "width": 5.2,
            "height": 3.8,
            "title_x": 1.8,
            "title_y": 25.68,
            "title_width": 4.8,
        }
    return {
        "hide_elements": tuple(hide_elements),
        "page_size": page_size,
        "map_frame": map_frame,
        "outer_frame": outer_frame,
        "scale_bar": scale_bar,
        "show_area_panel": show_area_panel,
        "show_coordinate_labels": show_coordinate_labels,
        "font_sizes": {
            "coordinate": 9.0,
            "area_row": 9.5,
            "area_row_secondary": 8.5,
            "area_title": 10.5,
            "metadata": 10.0,
            "scale_note": 12.0,
            "page_header": 10.0,
            "title": 18.0 if kind in ("sample", "geo", "sheet", "index") else 17.5,
            "inset_title": 10.5,
            "legend_title": 12.5,
            "legend_label": 11.5,
            "sample_label": 12.5,
            "unit_label": 11.0,
            "parcel_label": 14.0,
            "sheet_label": 10.0,
        },
        "scale_note": scale_note,
        "coordinate_note": coordinate_note,
        "legend": legend,
        "title": title,
        "north_arrow": north_arrow,
        "metadata_positions": metadata_positions,
        "area_row_widths": (8.8, 5.1),
        "area_row_height": 0.38,
        "metadata_y": metadata_y,
        "metadata_x": 10.4,
        "inset": inset,
    }


def apply_page_geometry(layout, kind):
    """Set the shared landscape page, main frame, and optional location inset."""
    spec = visual_layout_spec(kind)
    page = spec.get("page_size")
    if not page:
        return spec
    layout.pageWidth = page["width"]
    layout.pageHeight = page["height"]
    frames = [element for element in layout.listElements()
              if element.type == "MAPFRAME_ELEMENT"]
    main = next((element for element in frames
                 if element.name in ("WEBMAP_MAP_FRAME", "主图框")), None)
    if main is None:
        main = next((element for element in frames
                     if element.name not in ("外图廓", "区位图框")), None)
    if main is not None:
        frame = spec["map_frame"]
        main.elementPositionX = frame["x"]
        main.elementPositionY = frame["y"]
        main.elementWidth = frame["width"]
        main.elementHeight = frame["height"]
    outer = spec.get("outer_frame")
    if outer:
        for element in frames:
            if element.name == "外图廓":
                element.elementPositionX = outer["x"]
                element.elementPositionY = outer["y"]
                element.elementWidth = outer["width"]
                element.elementHeight = outer["height"]
                element.visible = True
    inset = spec["inset"]
    for element in layout.listElements():
        if element.name == "区位图框":
            element.visible = kind == "geo"
            element.elementPositionX = inset["x"]
            element.elementPositionY = inset["y"]
            element.elementWidth = inset["width"]
            element.elementHeight = inset["height"]
        elif element.name == "区位图名":
            element.visible = False
            element.elementPositionX = inset["title_x"]
            element.elementPositionY = inset["title_y"]
            element.elementWidth = inset["title_width"]
            element.elementHeight = inset["title_height"]
    return spec


def style_inset_frame(layout, definition=None, commit=True):
    """Remove the template's offset shadow so the locator map has one aligned frame."""
    cim_definition = definition if definition is not None else layout.getDefinition("V3")
    frames = [element for element in cim_definition.elements
              if type(element).__name__ == "CIMMapFrame" and element.name == "区位图框"]
    if not frames:
        return cim_definition
    graphic_frame = frames[0].graphicFrame
    graphic_frame.shadowOffsetX = 0
    graphic_frame.shadowOffsetY = 0
    shadow_ref = getattr(graphic_frame, "shadowSymbol", None)
    shadow_symbol = getattr(shadow_ref, "symbol", None) if shadow_ref else None
    for layer in getattr(shadow_symbol, "symbolLayers", None) or []:
        layer.enable = False
    border_ref = getattr(graphic_frame, "borderSymbol", None)
    border_symbol = getattr(border_ref, "symbol", None) if border_ref else None
    for layer in getattr(border_symbol, "symbolLayers", None) or []:
        if type(layer).__name__ == "CIMSolidStroke":
            layer.enable = True
            layer.width = 0.75
            layer.color.values = [60, 75, 85, 100]
    if commit:
        layout.setDefinition(cim_definition)
    return cim_definition


def style_layout_typography(layout, definition=None, commit=True, scale=None, show_frame_grid=False):
    """Use a readable sans-serif face for layout text while preserving size hierarchy."""
    cim_definition = definition if definition is not None else layout.getDefinition("V3")
    bold_names = {"图名", "区位图名", "密级", "图号", "面积统计标题", "面积合计"}
    def visit(elements):
        for element in elements:
            yield element
            yield from visit(getattr(element, 'elements', None) or [])
    for element in visit(cim_definition.elements):
        if type(element).__name__ == 'CIMMapFrame':
            for grid in getattr(element, 'grids', None) or []:
                grid.isVisible = bool(show_frame_grid)
            continue
        if type(element).__name__.endswith('ScaleBar'):
            element.fittingStrategy = 'AdjustFrame'
            if scale is not None:
                bar = nice_scale_bar(scale)
                kilometer = getattr(getattr(element, 'units', None), 'uwkid', None) == 9036 or getattr(element, 'unitLabel', '') == 'km'
                element.division = bar['division_m'] / (1000 if kilometer else 1)
                element.divisions = bar['divisions']
                element.divisionsBeforeZero = 0
                element.subdivisions = 0
                number_format = getattr(element, 'numberFormat', None)
                if number_format is not None:
                    number_format.roundingValue = max(0, -math.floor(math.log10(element.division)))
            continue
        if type(element).__name__ != "CIMGraphicElement":
            continue
        graphic = getattr(element, "graphic", None)
        symbol_ref = getattr(graphic, "symbol", None) if graphic else None
        symbol = getattr(symbol_ref, "symbol", None) if symbol_ref else None
        if type(symbol).__name__ != "CIMTextSymbol":
            continue
        if element.name.startswith(("E上", "E下", "N左", "N右", "地图经度_", "地图纬度_")):
            symbol.fontFamilyName = LATIN_FONT
        else:
            symbol.fontFamilyName = CHINESE_FONT
        symbol.fontStyleName = "Bold" if element.name in bold_names else "Regular"
        for ink in getattr(getattr(symbol, "symbol", None), "symbolLayers", None) or []:
            if type(ink).__name__ == "CIMSolidFill":
                ink.color.values = [24, 32, 40, 100]
    if commit:
        layout.setDefinition(cim_definition)
    return cim_definition


def style_legend_background(layout, definition=None, commit=True):
    """Add a near-opaque white fill to the existing legend graphic frame."""
    import arcpy.cim as cim_api

    cim_definition = definition if definition is not None else layout.getDefinition("V3")
    legends = [element for element in cim_definition.elements
               if type(element).__name__ == "CIMLegend" and element.name == "Legend"]
    if not legends:
        raise RuntimeError("布局中没有可整饰的 Legend 图例")
    legend = legends[0]
    graphic_frame = legend.graphicFrame
    background = graphic_frame.backgroundSymbol
    polygon = background.symbol
    layers = list(polygon.symbolLayers or [])
    fill = next((layer for layer in layers if type(layer).__name__ == "CIMSolidFill"), None)
    if fill is None:
        fill = cim_api.CIMSolidFill()
        layers.insert(0, fill)
    fill.enable = True
    fill.color = cim_api.CIMRGBColor()
    fill.color.values = [255, 255, 255, 93]
    polygon.symbolLayers = layers
    background.symbol = polygon
    graphic_frame.backgroundSymbol = background
    legend.graphicFrame = graphic_frame
    if commit:
        layout.setDefinition(cim_definition)
    return cim_definition


def style_legend_typography(layout, definition=None, commit=True):
    """Set a clear title/category hierarchy with print-readable legend text."""
    cim_definition = definition if definition is not None else layout.getDefinition("V3")
    sizes = visual_layout_spec("sample")["font_sizes"]
    legends = [element for element in cim_definition.elements
               if type(element).__name__ == "CIMLegend" and element.name == "Legend"]
    if not legends:
        raise RuntimeError("布局中没有可整饰的 Legend 图例")
    legend = legends[0]
    title_reference = getattr(legend, "titleSymbol", None)
    title_symbol = getattr(title_reference, "symbol", None) if title_reference else None
    if title_symbol is not None:
        title_symbol.height = sizes["legend_title"]
        title_symbol.fontFamilyName = CHINESE_FONT
        title_symbol.fontStyleName = "Bold"
        for ink in getattr(getattr(title_symbol, "symbol", None), "symbolLayers", None) or []:
            if type(ink).__name__ == "CIMSolidFill":
                ink.color.values = [24, 32, 40, 100]
    for item in legend.items or []:
        label_symbol = getattr(item, "labelSymbol", None)
        if label_symbol is None:
            continue
        label_symbol.height = sizes["legend_label"]
        label_symbol.fontFamilyName = CHINESE_FONT
        label_symbol.fontStyleName = "Regular"
        for ink in getattr(getattr(label_symbol, "symbol", None), "symbolLayers", None) or []:
            if type(ink).__name__ == "CIMSolidFill":
                ink.color.values = [24, 32, 40, 100]
    if commit:
        layout.setDefinition(cim_definition)
    return cim_definition
