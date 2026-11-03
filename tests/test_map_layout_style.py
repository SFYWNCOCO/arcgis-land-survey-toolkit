"""Readable spacing and type-size contract for the A4 acceptance maps."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.map_layout_style import (area_row_positions, display_title,
                                         visual_layout_spec)  # noqa: E402


def test_shared_text_writer_preserves_success_missing_and_failure_behavior():
    import types
    from acceptance.map_layout_style import set_layout_text
    element = types.SimpleNamespace(type='TEXT_ELEMENT', name='title', text='old')
    layout = types.SimpleNamespace(listElements=lambda: [element])
    assert set_layout_text(layout, 'title', 'new') and element.text == 'new'
    assert not set_layout_text(layout, 'missing', 'new')
    class ReadOnly:
        type, name = 'TEXT_ELEMENT', 'title'
        @property
        def text(self): return 'old'
    assert not set_layout_text(types.SimpleNamespace(listElements=lambda: [ReadOnly()]), 'title', 'new')


def test_nice_scale_bar_uses_clean_divisions_at_current_project_scales():
    from acceptance.map_layout_style import nice_scale_bar
    for scale, division in [(1000, 5), (1500, 10), (2000, 10), (2500, 20), (25000, 200)]:
        bar = nice_scale_bar(scale)
        assert bar['division_m'] == division and bar['divisions'] == 5
        assert bar['total_m'] == division * 5
        assert 2.5 <= bar['paper_cm'] <= 4
    import pytest
    for bad in [0, -1, float('inf'), float('nan')]:
        with pytest.raises(ValueError): nice_scale_bar(bad)


def test_native_scale_bars_keep_clean_values_in_nested_groups_and_convert_km():
    from types import SimpleNamespace as NS
    from acceptance.map_layout_style import style_layout_typography
    ScaleBar = type('CIMDoubleFillScaleBar', (NS,), {})
    Group = type('CIMGroupElement', (NS,), {})
    meter = ScaleBar(name='Graphic Scale 10 m', unitLabel='m', numberFormat=NS(roundingValue=0))
    kilometer = ScaleBar(name='Graphic Scale km', unitLabel='km', numberFormat=NS(roundingValue=1))
    definition = NS(elements=[Group(name='Scale bar', elements=[meter, kilometer])])
    style_layout_typography(None, definition=definition, commit=False, scale=1000)
    assert meter.fittingStrategy == 'AdjustFrame' and meter.division == 5 and meter.divisions == 5
    assert kilometer.fittingStrategy == 'AdjustFrame' and kilometer.division == .005
    assert kilometer.numberFormat.roundingValue >= 3


def test_source_parcel_labels_use_original_numbers_in_numeric_order():
    from acceptance.map_layout_style import source_parcel_labels
    assert source_parcel_labels([{'unit': 'TEMP1', 'parcels': ['111', '31', '1']}]) == {
        'TEMP1': '地块1、31、111'}


def test_template_frame_grids_are_hidden_without_removing_sampling_layers():
    from types import SimpleNamespace as NS
    from acceptance.map_layout_style import style_layout_typography
    Frame = type('CIMMapFrame', (NS,), {})
    grid = NS(isVisible=True)
    frame = Frame(name='主图框', grids=[grid], map='sampling-map')
    definition = NS(elements=[frame])
    style_layout_typography(None, definition=definition, commit=False)
    assert grid.isVisible is False and frame.map == 'sampling-map'
    style_layout_typography(None, definition=definition, commit=False, show_frame_grid=True)
    assert grid.isVisible is True


def test_project_presentation_controls_do_not_change_review_status():
    from acceptance.map_layout_style import map_presentation_options
    plan = {'meta': {'presentation': {'show_inset': False, 'show_coordinate_labels': False,
                                     'show_preview_label': False, 'grid_line_width': 0.9}}}
    target = {'title': '【设计预览】采样点位布置图', 'preview_only': True}
    options = map_presentation_options(plan, target)
    assert not options['show_inset'] and not options['show_coordinate_labels']
    assert options['grid_line_width'] == 0.9
    assert display_title(dict(target, presentation=options)) == '采样点位布置图'
    assert display_title(dict(target, presentation=options, preview_only=False)) == '采样点位布置图'
    assert target['preview_only'] is True
    assert map_presentation_options(plan, {'presentation': {'show_inset': True}})['show_inset']
    defaults = map_presentation_options({}, {})
    assert defaults['show_inset'] is True  # 2026-10-31 用户确认：区位小图默认开
    assert not any(defaults[name] for name in ('show_coordinate_labels', 'show_frame_grid',
                                               'show_grid_centers', 'show_preview_label'))
    assert defaults['grid_line_width'] <= .5
    assert defaults['grid_line_color'][3] <= 45


def test_hiding_design_label_preserves_missing_imagery_warning():
    target = {'title': '【无影像预览】采样图', 'presentation': {'show_preview_label': False}}
    assert display_title(target) == '【无影像预览】采样图'


def test_source_parcel_labels_reject_missing_source_numbers():
    import pytest
    from acceptance.map_layout_style import source_parcel_labels
    with pytest.raises(ValueError, match='原始地块号'):
        source_parcel_labels([{'unit': 'TEMP1', 'parcels': []}])


def test_map_metadata_uses_real_company_and_current_render_date():
    import datetime
    from acceptance.map_layout_style import map_metadata_values
    company = '某某检测有限公司'
    values = map_metadata_values({'meta': {'org': company, 'date': '2000-01-01'}}, {},
                                 today=datetime.date(2026, 10, 25))
    assert values['编制单位'] == '编制单位：' + company
    assert values['成图日期'] == '制图日期：2026年10月25日'
    assert '1985' not in values['高程基准']


def test_map_metadata_never_leaves_placeholder_wording():
    import datetime
    from acceptance.map_layout_style import map_metadata_values
    values = map_metadata_values({}, {}, today=datetime.date(2026, 10, 28))
    assert '待填' not in values['编制单位']
    assert '待核实' not in values['高程基准']
    assert values['高程基准'] == '高程基准：未提供（以项目资料为准）'
    assert '未提供' in values['制图审核']


def test_sample_footer_reserves_imagery_source_and_reviewer_slots():
    from acceptance.map_layout_style import visual_layout_spec
    positions = visual_layout_spec('sample')['metadata_positions']
    assert '影像来源' in positions and '制图审核' in positions
    assert positions['影像来源']['y'] <= positions['制图审核']['y'] - 0.5


def test_explicit_project_map_date_overrides_render_day_without_changing_default():
    import datetime
    import pytest
    from acceptance.map_layout_style import map_metadata_values
    render_day = datetime.date(2026, 10, 28)
    result = map_metadata_values({'meta': {'map_date': '2026-10-25'}}, {}, today=render_day)
    assert result['成图日期'] == '制图日期：2026年10月25日'
    assert map_metadata_values({}, {}, today=render_day)['成图日期'] == '制图日期：2026年10月28日'
    with pytest.raises(ValueError):
        map_metadata_values({'meta': {'map_date': '2026-02-30'}}, {}, today=render_day)


def test_legend_metadata_and_scale_have_separate_footer_columns():
    spec = visual_layout_spec("sample")
    legend = spec["legend"]
    columns = spec['metadata_positions']
    assert min(value['x'] for value in columns.values()) - (legend['x'] + legend['width']) >= .4
    assert max(value['x'] + value['width'] for value in columns.values()) < spec['scale_bar']['x']
    assert legend['y'] - legend['height'] >= 0


def test_area_text_boxes_fit_long_unit_code_without_shrinking():
    spec = visual_layout_spec()
    assert spec["area_row_widths"][0] >= 8.5
    assert spec["area_row_widths"][1] >= 5.0
    assert spec["area_row_height"] >= 0.32


def test_smallest_coordinate_and_area_text_are_print_readable():
    sizes = visual_layout_spec()["font_sizes"]
    assert sizes["coordinate"] >= 7.0
    assert sizes["area_row"] >= 8.0
    assert sizes["metadata"] >= 9.0


def test_inset_is_compact_and_keeps_title_inside_frame():
    inset = visual_layout_spec()["inset"]
    assert inset["width"] <= 5.2
    assert inset["height"] <= 3.8
    assert inset["title_x"] >= inset["x"]
    assert inset["title_y"] < inset["y"] + inset["height"]


def test_area_rows_fill_first_column_then_second_without_overlap():
    positions = area_row_positions(7)
    assert len(positions) == 7
    assert [x for x, _ in positions[:5]] == [10.4] * 5
    assert [x for x, _ in positions[5:]] == [15.55] * 2
    assert positions[0][1] > positions[1][1] > positions[2][1]
    assert positions[5][1] == positions[0][1]


def test_sampling_title_keeps_project_name_for_courseware_layout():
    title = display_title({
        "kind": "sample",
        "uid": "F99000120260000E0001",
        "title": "XX县2026年第一批补充耕地质量鉴定采样点位布置图",
    })
    assert title == "XX县2026年第一批补充耕地质量鉴定采样点位布置图"


def test_location_title_is_preserved():
    assert display_title({"kind": "geo", "title": "补充耕地质量鉴定地理位置图"}) == "补充耕地质量鉴定地理位置图"


def test_location_preview_is_marked_when_context_features_are_missing():
    from acceptance import map_layout_style
    title, missing = map_layout_style.location_map_title(
        "补充耕地质量鉴定地理位置图", [])
    assert title.endswith("（预览，待补地理要素）")
    assert missing == ["居民点", "道路", "水系", "境界线"]


def test_sampling_export_preserves_courseware_scale_from_plan():
    from acceptance import map_layout_style
    planned_scale = getattr(map_layout_style, "planned_scale", None)
    assert callable(planned_scale)
    assert planned_scale({"kind": "sample", "scale": 2000}) == 2000


def test_geographic_scale_steps_down_when_raster_cannot_cover_frame():
    from acceptance import map_layout_style
    fit_scale = getattr(map_layout_style, "fit_scale_to_imagery", None)
    assert callable(fit_scale)
    frame = map_layout_style.visual_layout_spec("geo")["map_frame"]
    assert fit_scale(15000, (2264, 2251), frame["width"], frame["height"]) == 8000
    assert fit_scale(8000, (2264, 2251), frame["width"], frame["height"]) == 8000


def test_courseware_maps_keep_footer_below_map_and_coordinates_between_neatlines():
    for kind in ("sample", "geo"):
        spec = visual_layout_spec(kind)
        frame = spec["map_frame"]
        page = spec["page_size"]
        legend = spec["legend"]
        assert page == {"width": 29.7, "height": 21.0}
        assert frame["width"] / frame["height"] >= 1.5
        assert frame["x"] >= 0 and frame["y"] >= 0
        assert frame["x"] + frame["width"] <= page["width"]
        assert frame["y"] + frame["height"] <= page["height"]
        outer = spec['outer_frame']
        assert 0 <= legend['y'] - legend['height'] < legend['y'] < outer['y']
        assert spec['scale_bar']['y'] + spec['scale_bar']['height'] < outer['y']
        assert outer['x'] < frame['x']
        assert outer['y'] < frame['y']
        assert outer['x'] + outer['width'] > frame['x'] + frame['width']
        assert outer['y'] + outer['height'] > frame['y'] + frame['height']
        assert spec["show_area_panel"] is False
        assert spec["show_coordinate_labels"] is True
        assert 'Scale bar' not in spec['hide_elements']
        assert spec['north_arrow']['y'] + spec['north_arrow']['height'] < outer['y']
        assert spec['north_arrow']['x'] + spec['north_arrow']['width'] <= page['width']


def test_preview_status_is_preserved_without_printing_a_title_prefix():
    from acceptance.map_layout_style import map_presentation_options
    target = {'title': '【设计预览】采样点位布置图（地块1）', 'preview_only': True}
    options = map_presentation_options({}, target)
    assert display_title(dict(target, presentation=options)) == '采样点位布置图（地块1）'
    explicit = {'title': '【设计预览】图', 'preview_only': True,
                'presentation': {'show_preview_label': False}}
    assert display_title(explicit) == '图'
    assert explicit['preview_only'] is True
    assert display_title(dict(explicit, presentation={'show_preview_label': True})) == '【设计预览】图'
    formal = {'title': '【设计预览】图', 'preview_only': False,
              'presentation': {'show_preview_label': False}}
    assert display_title(formal) == '图'
