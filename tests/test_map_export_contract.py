from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_shared_sampling_group_grid_is_filtered_by_actual_parcels_without_clipping(monkeypatch):
    import importlib.util
    import types
    class Shape:
        def __init__(self, boxes): self.boxes = boxes
        def union(self, other): return Shape(self.boxes + other.boxes)
        def disjoint(self, other):
            return all(a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
                       for a in self.boxes for b in other.boxes)
    geometries = {'30': Shape([(0, 0, 10, 10)]), '31': Shape([(100, 0, 110, 10)])}
    grid = [(1, Shape([(0, 0, 20, 20)])), (2, Shape([(100, 0, 120, 20)])), (3, Shape([(50, 0, 70, 20)]))]
    class Cursor:
        def __init__(self, path, fields, where_clause=None):
            self.rows = grid if path == 'grid' else [(shape,) for pid, shape in geometries.items() if "'"+pid+"'" in where_clause]
        def __enter__(self): return iter(self.rows)
        def __exit__(self, *_args): pass
    arcpy = types.ModuleType('arcpy'); arcpy.mp = types.ModuleType('arcpy.mp')
    arcpy.AddFieldDelimiters = lambda _path, name: '"' + name + '"'
    arcpy.ListFields = lambda _path: [types.SimpleNamespace(name='DKBH', type='String')]
    arcpy.Describe = lambda _path: types.SimpleNamespace(OIDFieldName='OBJECTID')
    arcpy.da = types.SimpleNamespace(SearchCursor=Cursor)
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy); monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.parcel_grid_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    layer = types.SimpleNamespace(name='方格网', dataSource='grid')
    parcels = types.SimpleNamespace(name='补充耕地图斑', dataSource='parcels')
    base = 'PJDYBH = \'PAIR\' AND IN_UNIT = 1'
    for ids, wanted in [(['30'], '(1)'), (['31'], '(2)'), (['30', '31'], '(1,2)')]:
        result = exporter._grid_query_for_target_parcels(layer, {'kind': 'sample', 'parcel_ids': ids}, {}, base, parcels)
        assert result.startswith(base + ' AND ') and '"OBJECTID" IN ' + wanted in result
        assert '(1,2,3)' not in result
    assert exporter._grid_query_for_target_parcels(layer, {'kind': 'geo'}, {}, base, parcels) == base
    grid[:] = [(oid, Shape([(0, 0, 20, 20)])) for oid in range(1, 1002)]
    result = exporter._grid_query_for_target_parcels(layer, {'kind': 'sample', 'parcel_ids': ['30']}, {}, base, parcels)
    import re
    chunks = re.findall(r'"OBJECTID" IN \(([^)]+)\)', result)
    assert [len(chunk.split(',')) for chunk in chunks] == [900, 101]


def test_native_scale_bar_keeps_natural_size_instead_of_resizing_its_parent_group(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy'); arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy); monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.scale_position_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    group = types.SimpleNamespace(name='Scale bar', type='GROUP_ELEMENT', elementWidth=9.9, elementHeight=2.2, visible=True)
    bar = types.SimpleNamespace(name='Graphic Scale 10 m', type='MAPSURROUND_ELEMENT', visible=False)
    layout = types.SimpleNamespace(listElements=lambda *_args: [group, bar])
    exporter._apply_visual_layout(layout, {'kind': 'sample', 'scale': 1000})
    assert (group.elementWidth, group.elementHeight) == (9.9, 2.2)
    assert bar.visible and (bar.elementPositionX, bar.elementPositionY) == (22.0, 1.25)


def test_failed_tiles_are_deterministic_failures_without_condemning_solid_vector_tiles(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy'); arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy); monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.tile_failure_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    monkeypatch.setattr(exporter.imagery_module, 'export_imagery_evidence_checks', lambda _quality: [])
    monkeypatch.setattr(exporter, 'validate_visual_review', lambda *_args: {'status': 'PASS', 'detail': 'reviewed'})
    for tiles, status in [({'fail': 2, 'ok': 20}, 'FAIL'), ({'fail': 0, 'ok': 22}, 'PASS'), (None, 'PASS')]:
        output = {'status': 'PASS', 'checks': [], 'imagery_visible': True}
        target = {'kind': 'geo', 'imagery_quality': {'tiles': tiles}}
        result = exporter._refresh_imagery_evidence(output, target)
        assert result['status'] == status
        assert any(x['item'] == '底图缺瓦片' and x['status'] == 'FAIL' for x in result['checks']) == (status == 'FAIL')


def test_scale_audit_measures_ground_extent_and_rejects_wrong_scale_or_cropped_parcels():
    from acceptance.map_validation import audit_map_scale
    ok = audit_map_scale(2000, [0, 0, 540, 308], 27, 15.4, [20, 20, 200, 200])
    assert ok['status'] == 'PASS' and abs(ok['measured_scale_x']-2000)<.001
    assert audit_map_scale(2500, [0, 0, 540, 308], 27, 15.4, [20,20,200,200])['status']=='FAIL'
    assert audit_map_scale(2000, [0, 0, 540, 308], 27, 15.4, [-1,20,200,200])['status']=='FAIL'


def test_visual_review_requires_current_actual_image_input_and_consistent_findings(tmp_path):
    import hashlib
    from acceptance.map_validation import validate_visual_review
    from PIL import Image
    p=tmp_path/'map.jpg';Image.new('RGB',(32,32),'white').save(p)
    assert validate_visual_review(p,'render-A')['status']=='NEEDS_REVIEW'
    proof={'status':'PASS','image_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
           'render_key':'render-A','image_input_verified':True,
           'reviewer':{'kind':'vision_model','name':'test-vision'},'reviewed_at':'2026-10-28T10:00:00+00:00',
           'findings':[{'item':'layout','status':'PASS','detail':'clear'}]}
    assert validate_visual_review(p,'render-A',proof)['status']=='PASS'
    assert validate_visual_review(p,'',dict(proof,render_key=''))['status']=='NEEDS_REVIEW'
    assert validate_visual_review(p,'render-A',dict(proof,reviewer={'kind':'vision_model','name':{}}))['status']=='NEEDS_REVIEW'
    assert validate_visual_review(p,'render-A',dict(proof,findings=[{'item':'layout','status':'PASS','detail':123}]))['status']=='NEEDS_REVIEW'
    assert validate_visual_review(p,'render-B',proof)['status']=='NEEDS_REVIEW'
    assert validate_visual_review(p,'render-A',dict(proof,image_input_verified=False))['status']=='NEEDS_REVIEW'
    bad=dict(proof,findings=[{'item':'layout','status':'FAIL','detail':'overlap'}])
    assert validate_visual_review(p,'render-A',bad)['status']!='PASS'
    p.write_bytes(b'changed-image')
    assert validate_visual_review(p,'render-A',proof)['status']=='NEEDS_REVIEW'


def test_hidden_inset_and_coordinates_survive_page_geometry_and_invalidate_cache(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.presentation_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    elements = [types.SimpleNamespace(name='区位图框', type='MAPFRAME_ELEMENT', visible=True),
                types.SimpleNamespace(name='地图纬度_左_01', type='TEXT_ELEMENT', visible=True)]
    layout = types.SimpleNamespace(listElements=lambda *_args: elements)
    options = {'show_inset': False, 'show_coordinate_labels': False}
    exporter._apply_visual_layout(layout, {'kind': 'geo', 'scale': 10000, 'presentation': options})
    assert all(not element.visible for element in elements)
    before = exporter._render_key(
        {'meta': {'presentation': {'show_inset': True, 'show_coordinate_labels': True}}}, {'kind': 'geo'})
    after = exporter._render_key({'meta': {'presentation': options}}, {'kind': 'geo'})
    assert before != after


def test_missing_map_metadata_is_reported_for_preview_only(monkeypatch):
    import importlib.util
    import types
    import sys
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    # 必须用 monkeypatch 注入并在用例结束时还原：直接写 sys.modules 会污染后续
    # 需要真实 arcpy 的测试（Pro 全量串跑实测 12 failed / 24 errors）
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.metadata_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    # 制图人/审核人不再作必需项（2026-11-01 用户确认）
    assert exporter._missing_metadata({}, {'kind': 'sample'}) == ['编制单位', '高程基准']
    complete = {'meta': {'org': '单位', 'elevation_datum': '1985国家高程基准'}}
    assert exporter._missing_metadata(complete, {'kind': 'sample'}) == []
    without_people = {'meta': {'org': '单位', 'elevation_datum': '1985国家高程基准',
                               'drawn_by': '', 'checked_by': ''}}
    assert exporter._missing_metadata(without_people, {'kind': 'sample'}) == []


def test_redboxed_footer_elements_stay_hidden_on_every_map_kind(monkeypatch):
    import importlib.util
    import types
    import sys
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.imagery_caption_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    for kind in ('sample', 'sheet', 'geo', 'index'):
        elements = [types.SimpleNamespace(name=name, type='TEXT_ELEMENT', visible=True,
                    textSize=0, elementPositionX=0, elementPositionY=0, elementWidth=0, elementHeight=0)
                    for name in ('影像来源', '制图审核')]
        layout = types.SimpleNamespace(listElements=lambda *_args: elements)
        exporter._apply_visual_layout(layout, {'kind': kind, 'scale': 2000, 'presentation': {}})
        assert all(not element.visible for element in elements)
    assert exporter._compact_imagery_text(
        '影像来源：天地图影像\n时相：未提供时相资料；精度待核\n输出像元：0.55 m（非原生 GSD）'
    ) == '影像来源：天地图影像；时相：未提供时相资料；精度待核\n输出像元：0.55 m（非原生 GSD）'


def test_atlas_queries_filter_original_parcel_points_even_when_sampling_group_is_shared(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    arcpy.AddFieldDelimiters = lambda _path, field: '"' + field + '"'
    arcpy.ListFields = lambda _path: [types.SimpleNamespace(name=n, type="String") for n in ("DKBH", "PJDYBH")]
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location("acceptance.atlas_query_contract", ROOT / "acceptance/map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    target = {"parcel_ids": ["22"], "unit_ids": ["SG_pair"]}
    layer = types.SimpleNamespace(name="设计采样点", dataSource="points")
    assert exporter._target_query(layer, target, {}) == '\"DKBH\" IN (\'22\')'
    layer.name = "方格网"
    assert exporter._target_query(layer, target, {}) == '\"PJDYBH\" IN (\'SG_pair\')'
    layer.name = "补充耕地图斑"
    assert exporter._target_query(layer, dict(target, parcel_ids=["O'Brien"]), {}) == '\"DKBH\" IN (\'O\'\'Brien\')'
    arcpy.ListFields = lambda _path: [types.SimpleNamespace(name="PJDYBH", type="String")]
    layer.name = "设计采样点"
    with pytest.raises(ValueError, match="DKBH"):
        exporter._target_query(layer, target, {})


@pytest.mark.parametrize("pid", ["22", 0])
def test_location_labels_follow_original_parcels_when_group_centroid_is_outside_region(monkeypatch, pid):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    arcpy.AddFieldDelimiters = lambda _path, name: name
    arcpy.ListFields = lambda path: [types.SimpleNamespace(name=n, type="String") for n in
                                    (("DKBH", "PJDYBH") if path == "parcels" else ("PJDYBH",))]

    class Cursor:
        def __init__(self, path, fields, **kwargs):
            x = 0 if path == "parcels" else 9000
            shape = types.SimpleNamespace(trueCentroid=types.SimpleNamespace(X=x, Y=0))
            row = {"DKBH": pid, "PJDYBH": "SG_far", "SHAPE@": shape, "SHAPE@TRUECENTROID": (x, 0)}
            self.rows = [tuple(row[f] for f in fields)]
        def __enter__(self): return iter(self.rows)
        def __exit__(self, *_args): pass

    arcpy.da = types.SimpleNamespace(SearchCursor=Cursor)
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location("acceptance.atlas_labels_contract", ROOT / "acceptance/map_export.py")
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    layers = [types.SimpleNamespace(name="补充耕地图斑", dataSource="parcels"),
              types.SimpleNamespace(name="评价单元", dataSource="units")]
    mapping = types.SimpleNamespace(listLayers=lambda: layers)
    frame = types.SimpleNamespace(map=types.SimpleNamespace(spatialReference=types.SimpleNamespace(linearUnitCode=9001)),
        elementWidth=27, elementHeight=15.4, elementPositionX=1, elementPositionY=3)
    label = types.SimpleNamespace(name="地块编号_1")
    layout = types.SimpleNamespace(listElements=lambda *_args: [label])
    audit = exporter._add_geo_unit_labels(layout, mapping, frame,
        {"label_mode": "source_parcel", "parcel_label_field": "DKBH", "parcel_ids": [str(pid)],
         "source_parcels": [str(pid)], "unit_ids": ["SG_far"]}, {}, 0, 0, 10000)
    assert label.text == str(pid) and audit["labeled"] == 1


def test_map_export_emits_jpeg_and_validates_each_rendered_artifact():
    exporter = (ROOT / "acceptance" / "map_export.py").read_text(encoding="utf-8")
    worker = (ROOT / "acceptance" / "map_worker.py").read_text(encoding="utf-8")
    assert "exportToJPEG" in exporter
    assert "jpeg" in worker.lower() or ".jpg" in worker.lower()
    assert "verify_export_artifact" in exporter


def test_layer_query_or_imagery_switch_failure_stops_export():
    exporter = (ROOT / "acceptance" / "map_export.py").read_text(encoding="utf-8")
    assert "raise RuntimeError" in exporter
    assert "定义查询失败" in exporter
    assert "影像切换失败" in exporter


def test_export_artifact_reader_detects_valid_and_corrupt_jpeg(tmp_path):
    sys.path.insert(0, str(ROOT))
    from acceptance.map_validation import verify_export_artifact
    Image = pytest.importorskip("PIL.Image")
    good = tmp_path / "good.jpg"
    Image.new("RGB", (32, 24), "green").save(good, format="JPEG")
    assert verify_export_artifact(good)["status"] == "PASS"
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8" + b"x" * 100 + b"\xff\xd9")
    assert verify_export_artifact(bad)["status"] == "FAIL"


def test_map_worker_has_managed_groups_and_hides_grid_helpers(monkeypatch):
    import importlib.util
    import types
    import copy
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location("acceptance.map_worker_contract", ROOT / "acceptance" / "map_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    class FakeMap:
        def __init__(self): self.groups = []
        def listLayers(self): return list(self.groups)
        def moveLayer(self, reference, layer, position):
            self.groups.remove(layer)
            self.groups.insert(self.groups.index(reference)+(position=='AFTER'),layer)
        def createGroupLayer(self, name, _parent):
            group = types.SimpleNamespace(name=name, layers=[])
            self.groups.insert(0, group)
            return group
        def addLayerToGroup(self, group, layer, _position): group.layers.insert(0, copy.copy(layer))
        def removeLayer(self, _layer): pass
    mapping = FakeMap()
    nodes = types.SimpleNamespace(name="网格交点", visible=True)
    samples = types.SimpleNamespace(name="设计采样点", visible=True)
    worker._group_project_layers(mapping, {"网格": [nodes], "设计采样点": [samples]})
    assert [group.name[3:] for group in mapping.groups] == ["问题与待核项", "设计采样点", "容重候选点", "单元边界", "网格", "辅助资料", "影像"]
    assert mapping.groups[4].layers[0].visible is False
    assert mapping.groups[1].layers[0].visible is True
    mapping.groups.reverse()
    worker.enforce_layer_order(mapping)
    assert [group.name[3:] for group in mapping.groups] == list(worker.DRAWING_ROLES)


def test_native_grid_display_excludes_outside_cells_and_preserves_scope(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    arcpy.ListFields = lambda _: [types.SimpleNamespace(name='IN_UNIT')]
    arcpy.AddFieldDelimiters = lambda _, field: '"' + field + '"'
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.grid_display_contract', ROOT / 'acceptance/map_worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    symbol = types.SimpleNamespace(color=None, outlineColor=None, outlineWidth=None)
    layer = types.SimpleNamespace(name='方格网', dataSource='grid', definitionQuery='PJDYBH = \'U1\'',
        symbology=types.SimpleNamespace(renderer=types.SimpleNamespace(symbol=symbol)))
    worker._configure_grid_display(layer, {})
    assert layer.definitionQuery == '(PJDYBH = \'U1\') AND ("IN_UNIT" = 1)'
    assert symbol.color == {'RGB': [0, 0, 0, 0]}
    assert symbol.outlineColor['RGB'][3] <= 45
    assert symbol.outlineWidth <= .5
    arcpy.ListFields = lambda _: []
    with pytest.raises(ValueError, match='IN_UNIT'):
        worker._configure_grid_display(layer, {})


def test_source_parcel_labels_replace_temporary_group_labels_and_are_larger(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    arcpy.ListFields = lambda _: [types.SimpleNamespace(name='DIKUAI_NO')]
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    for name in ('map_export', 'map_worker'):
        spec = importlib.util.spec_from_file_location('acceptance.' + name, ROOT / ('acceptance/' + name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules, 'acceptance.' + name, module)
    worker = sys.modules['acceptance.map_worker']
    class Layer:
        def __init__(self, name):
            self.name, self.dataSource, self.showLabels = name, name, True
            symbol = types.SimpleNamespace(height=8.5, haloSize=0, haloSymbol=types.SimpleNamespace(symbolLayers=[]))
            self.definition = types.SimpleNamespace(labelClasses=[types.SimpleNamespace(
                expression='$feature.SAMP_GRP', visible=True, textSymbol=types.SimpleNamespace(symbol=symbol))])
        def getDefinition(self, _version): return self.definition
        def setDefinition(self, definition): self.definition = definition
    parcels, units = Layer('补充耕地图斑'), Layer('评价单元')
    mapping = types.SimpleNamespace(listLayers=lambda: [parcels, units])
    worker._configure_source_parcel_labels(mapping, 'DIKUAI_NO')
    label = parcels.definition.labelClasses[0]
    assert label.expression == "'地块' + Text($feature[\"DIKUAI_NO\"])"
    assert label.textSymbol.symbol.height == 14
    assert label.textSymbol.symbol.fontStyleName == 'Bold'
    assert parcels.showLabels is True and units.showLabels is False
    arcpy.ListFields = lambda _: []
    with pytest.raises(ValueError, match='DIKUAI_NO'):
        worker._configure_source_parcel_labels(mapping, 'DIKUAI_NO')


def test_map_worker_applies_courseware_diamond_symbol_to_sample_points(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_worker_diamond_contract", ROOT / "acceptance" / "map_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    applied = []
    symbol = types.SimpleNamespace(
        color=None, outlineColor=None, outlineWidth=None, size=None,
        applySymbolFromGallery=lambda name: applied.append(name))
    layer = types.SimpleNamespace(
        name="", symbology=types.SimpleNamespace(renderer=types.SimpleNamespace(symbol=symbol)))
    worker._style(layer, name="设计采样点", marker_style="菱形 1",
                  fill=(214, 39, 40, 100), outline=(255, 255, 255, 100), size=8)
    assert applied == ["菱形 1"]
    assert symbol.color == {"RGB": [214, 39, 40, 100]}
    assert symbol.outlineColor == {"RGB": [255, 255, 255, 100]}


def test_export_restyles_legacy_sample_layers_before_rendering(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_export_legacy_symbol_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    applied = []
    def fake_layer(name):
        symbol = types.SimpleNamespace(
            color=None, outlineColor=None, outlineWidth=None, size=None,
            applySymbolFromGallery=lambda value: applied.append(value))
        return types.SimpleNamespace(
            name=name, symbology=types.SimpleNamespace(renderer=types.SimpleNamespace(symbol=symbol)))
    sample = fake_layer("设计采样点")
    parcel = fake_layer("补充耕地图斑")
    unit = fake_layer("评价单元")
    grid = fake_layer("方格网")
    centers = fake_layer("网格中心点")
    outlines = {}
    monkeypatch.setattr(exporter, "_set_polygon_outline", lambda layer, color, width, dashed=False:
                        outlines.update({layer.name: (color, width, dashed)}))
    mapping = types.SimpleNamespace(listLayers=lambda: [sample, parcel, unit, grid, centers])
    assert exporter._style_sample_map_layers(mapping) is True
    assert applied == ["菱形 1"]
    assert sample.symbology.renderer.symbol.size >= 9.0
    assert sample.symbology.renderer.symbol.color == {"RGB": [214, 39, 40, 100]}
    assert sample.symbology.renderer.symbol.outlineColor == {"RGB": [255, 255, 255, 100]}
    assert parcel.symbology.renderer.symbol.color["RGB"][3] == 0
    assert unit.symbology.renderer.symbol.color == {"RGB": [255, 255, 255, 0]}
    assert outlines['补充耕地图斑'][2] is True
    assert outlines['评价单元'][2] is False
    assert outlines['补充耕地图斑'][0] != outlines['评价单元'][0]
    assert outlines['方格网'][0][3] <= 50  # ArcGIS opacity is a percentage.
    assert outlines['方格网'][1] < outlines['评价单元'][1]
    assert centers.symbology.renderer.symbol.color['RGB'][3] < 100


def test_export_code_upgrade_changes_single_map_cache_key(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location("acceptance.map_export_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    monkeypatch.setattr(exporter, "_implementation_digest", lambda: "old-version")
    old = exporter._render_key({"dpi": 300}, {"name": "map"})
    monkeypatch.setattr(exporter, "_implementation_digest", lambda: "new-version")
    assert exporter._render_key({"dpi": 300}, {"name": "map"}) != old


def test_next_day_date_invalidates_cached_map(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy'); arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.map_export_date_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(exporter)
    monkeypatch.setattr(exporter, '_implementation_digest', lambda: 'same-source')
    monkeypatch.setattr(exporter, 'map_metadata_values', lambda *_: {'成图日期': '2026年10月25日'})
    first = exporter._render_key({}, {'name': 'map'})
    monkeypatch.setattr(exporter, 'map_metadata_values', lambda *_: {'成图日期': '2026年10月26日'})
    assert exporter._render_key({}, {'name': 'map'}) != first


def test_layout_template_change_invalidates_cached_maps(tmp_path, monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType('arcpy')
    arcpy.mp = types.ModuleType('arcpy.mp')
    monkeypatch.setitem(sys.modules, 'arcpy', arcpy)
    monkeypatch.setitem(sys.modules, 'arcpy.mp', arcpy.mp)
    spec = importlib.util.spec_from_file_location('acceptance.map_export_template_contract', ROOT / 'acceptance/map_export.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    core = tmp_path / 'acceptance'
    core.mkdir()
    for name in ('map_export.py', 'map_validation.py', 'map_layout_style.py', 'map_worker.py'):
        (core / name).write_text('source', encoding='utf-8')
    templates = tmp_path / 'templates'
    templates.mkdir()
    for name in ('补充耕地鉴定图_A4.pagx', '补充耕地鉴定图_A4_区位.pagx'):
        (templates / name).write_text('style one', encoding='utf-8')
    monkeypatch.setattr(exporter, '__file__', str(core / 'map_export.py'))
    before = exporter._implementation_digest()
    (templates / '补充耕地鉴定图_A4.pagx').write_text('style two', encoding='utf-8')
    assert exporter._implementation_digest() != before


def test_cached_export_rechecks_imagery_proof_after_it_changes(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_export_evidence_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    proof_file = tmp_path / "review.pdf"
    proof_file.write_bytes(b"reviewed evidence")
    proof = {"path": str(proof_file), "sha256": hashlib.sha256(proof_file.read_bytes()).hexdigest()}
    review = {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"}
    target = {"imagery_quality": {
        "source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
        "source_gsd_evidence": proof, "source_gsd_review": review,
        "positional_accuracy": {"value_m": 0.15, "status": "VERIFIED",
                                "evidence": proof, "review": review},
    }}
    output = {"status": "PASS", "checks": [
        {"item": "原始GSD", "status": "PASS"},
        {"item": "影像位置精度", "status": "PASS"},
        {"item": "布局", "status": "PASS"},
    ]}

    proof_file.write_bytes(b"changed after cached export")
    refreshed = exporter._refresh_imagery_evidence(output, target)

    assert refreshed["status"] == "NEEDS_REVIEW"
    assert {row["item"]: row["status"] for row in refreshed["checks"]} == {
        "原始GSD": "NEEDS_REVIEW", "影像位置精度": "NEEDS_REVIEW", "布局": "PASS", "视觉复核":"NEEDS_REVIEW"}

    proof_file.write_bytes(b"reviewed evidence")
    proof["sha256"] = hashlib.sha256(proof_file.read_bytes()).hexdigest()
    preserved_failure = exporter._refresh_imagery_evidence({"status": "FAIL", "checks": []}, target)
    assert preserved_failure["status"] == "FAIL"


def test_map_worker_passes_imagery_evidence_into_export_plan(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_worker_evidence_contract", ROOT / "acceptance" / "map_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    proof = {"path": "review.pdf", "sha256": "a" * 64}
    review = {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"}
    active = {"coverage_status": "PASS", "pixel_qa": {"status": "PASS"},
              "source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
              "source_gsd_evidence": proof, "source_gsd_review": review,
              "positional_accuracy": {"value_m": 0.15, "status": "VERIFIED",
                                      "evidence": proof, "review": review}}

    result = worker._imagery_quality(active)

    assert result["source_gsd_evidence"] == proof
    assert result["source_gsd_review"] == review
    assert result["positional_accuracy"] == active["positional_accuracy"]


def test_sampling_map_without_visible_imagery_is_a_failed_preview_not_a_formal_map(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_export_missing_imagery_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    target = {"kind": "sample", "title": "采样点位布置图", "imagery_text": "影像来源：未提供"}
    marked = exporter._mark_target_preview_only(target, imagery_visible=False)
    output = {"status": "PASS", "jpg": "preview.jpg", "png": "preview.png", "pdf": "preview.pdf",
              "checks": [{"item": "图层查询", "status": "PASS"}]}
    result = exporter._record_missing_imagery_failure(output, marked, imagery_visible=False)

    assert marked["preview_only"] is True
    assert "无影像预览" in marked["title"]
    assert "不可作为报件图" in marked["imagery_text"]
    assert result["preview_only"] is True
    assert result["deliverable_status"] == "PREVIEW_ONLY"
    assert result["status"] == "FAIL"
    assert next(row for row in result["checks"] if row["item"] == "采样图影像底图")["status"] == "FAIL"
    assert {key for key in ("jpg", "png", "pdf") if key in result} == {"jpg", "png", "pdf"}

    geo = exporter._mark_target_preview_only({"kind": "geo", "title": "地理位置图"}, imagery_visible=False)
    assert geo.get("preview_only") is not True


def test_sampling_map_with_visible_imagery_is_not_mislabeled_as_missing_image(monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_export_visible_imagery_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    target = exporter._mark_target_preview_only({"kind": "sample", "title": "采样图"}, imagery_visible=True)
    result = exporter._record_missing_imagery_failure({"status": "PASS", "checks": []}, target,
                                                       imagery_visible=True)

    assert target.get("preview_only") is not True
    assert "无影像预览" not in target["title"]
    assert result["status"] == "PASS"
    assert not any(row["item"] == "采样图影像底图" for row in result["checks"])


def test_layout_style_change_invalidates_cached_map_export(tmp_path, monkeypatch):
    import importlib.util
    import types
    arcpy = types.ModuleType("arcpy")
    arcpy.mp = types.ModuleType("arcpy.mp")
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)
    monkeypatch.setitem(sys.modules, "arcpy.mp", arcpy.mp)
    spec = importlib.util.spec_from_file_location(
        "acceptance.map_export_style_digest_contract", ROOT / "acceptance" / "map_export.py")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    monkeypatch.setattr(exporter, "__file__", str(tmp_path / "map_export.py"))
    for name in ("map_export.py", "map_validation.py", "map_layout_style.py", "map_worker.py"):
        (tmp_path / name).write_text("initial", encoding="utf-8")
    before = exporter._implementation_digest()
    (tmp_path / "map_layout_style.py").write_text("changed layout style", encoding="utf-8")
    assert exporter._implementation_digest() != before


@pytest.mark.parametrize('kind', ['jpg', 'png'])
def test_export_reader_rejects_wrong_pixel_size_or_dpi(tmp_path, kind):
    sys.path.insert(0, str(ROOT))
    from acceptance.map_validation import verify_export_artifact
    Image = pytest.importorskip('PIL.Image')
    image = tmp_path / ('paper.' + kind)
    Image.new('RGB', (300, 600), 'white').save(image, dpi=(300, 300))
    contract = dict(width_mm=25.4, height_mm=50.8, dpi=300, pages=1)
    assert verify_export_artifact(image, expected=contract)['status'] == 'PASS'
    assert verify_export_artifact(image, expected=dict(contract, dpi=150))['status'] == 'FAIL'
    assert verify_export_artifact(image, expected=dict(contract, width_mm=50.8))['status'] == 'FAIL'


def test_export_reader_retains_unknown_dpi_as_manual_check(tmp_path):
    sys.path.insert(0, str(ROOT))
    from acceptance.map_validation import verify_export_artifact
    Image = pytest.importorskip('PIL.Image')
    image = tmp_path / 'missing_dpi.png'
    Image.new('RGB', (300, 600), 'white').save(image)
    result = verify_export_artifact(image, expected=dict(width_mm=25.4, height_mm=50.8, dpi=300, pages=1))
    assert result['status'] == 'NEEDS_REVIEW'
    assert any(c['item'] == 'DPI元数据' and c['status'] == 'NEEDS_REVIEW' for c in result['checks'])


def test_pdf_reader_rejects_wrong_page_count_and_paper(tmp_path):
    sys.path.insert(0, str(ROOT))
    from acceptance.map_validation import verify_export_artifact
    fitz = pytest.importorskip('fitz')
    pdf = tmp_path / 'paper.pdf'
    document = fitz.open()
    document.new_page(width=595.276, height=841.89)
    document.save(pdf)
    document.close()
    contract = dict(width_mm=210, height_mm=297, pages=1)
    assert verify_export_artifact(pdf, expected=contract)['status'] == 'PASS'
    assert verify_export_artifact(pdf, expected=dict(contract, pages=2))['status'] == 'FAIL'
    assert verify_export_artifact(pdf, expected=dict(contract, width_mm=297))['status'] == 'FAIL'
