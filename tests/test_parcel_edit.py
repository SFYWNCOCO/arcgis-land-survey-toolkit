from pathlib import Path
import copy
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acceptance.parcel_edit import selection_ids, revised_profile, preview, resolve, _source_signature, origin_id


def test_source_identity_preserves_zero_and_rejects_blank_as_an_origin():
    assert origin_id(0, 'A') == '0'
    assert origin_id(' 0 ', 'A') == '0'
    assert origin_id('   ', 'A') == 'A'
    assert origin_id(None, 'A') == 'A'


@pytest.mark.parametrize('values,operation', [([], 'merge'), ([1], 'merge'), ([1, 2], 'split'),
    ([True, 2], 'merge'), ([1, 1], 'merge'), ([-1, 2], 'merge'), ([1, 2], 'unknown')])
def test_invalid_selection_is_rejected(values, operation):
    with pytest.raises(ValueError): selection_ids(values, operation)


def test_profile_rewrites_excluded_children_and_invalidates_only_geometry_bound_groups():
    before = {'sampling_groups': [{'parcels': ['A', 'B']}, {'parcels': ['C', 'D']}],
        'meta': {'presentation': {'show_frame_grid': False}, 'project_rules': {
        'excluded_parcels': ['A'], 'map_scales': {'old': 2000}, 'point_counts_by_unit': {'U': 3},
        'index_scale': 25000, 'combine_parcels': [['C', 'D']]}}}
    original = copy.deepcopy(before)
    after, changes = revised_profile(before, ['A'], ['S1', 'S2'])
    assert before == original
    assert after['sampling_groups'] == [{'parcels': ['C', 'D']}]
    assert after['meta']['project_rules']['excluded_parcels'] == ['S1', 'S2']
    assert after['meta']['project_rules']['combine_parcels'] == [['C', 'D']]
    assert after['meta']['project_rules']['index_scale'] == 25000
    assert after['meta']['presentation'] == before['meta']['presentation']
    assert 'map_scales' not in after['meta']['project_rules'] and changes
    other, _ = revised_profile(before, ['C'], ['SC1', 'SC2'])
    assert not other['meta']['project_rules']['combine_parcels']
    with pytest.raises(ValueError, match='排除'):
        revised_profile(before, ['A', 'B'], ['M'])


def test_profile_matches_integer_and_whitespace_ids():
    before = {'sampling_groups': [{'parcels': [' 1 ', '2']}], 'meta': {'project_rules': {
        'excluded_parcels': [1], 'combine_parcels': [['3', 4]]}}}
    after, _ = revised_profile(before, ['1'], ['S1', 'S2'])
    assert after['meta']['project_rules']['excluded_parcels'] == ['S1', 'S2']
    assert not after['sampling_groups']
    after, _ = revised_profile(before, ['3'], ['S1', 'S2'])
    assert not after['meta']['project_rules']['combine_parcels']


def test_density_keeps_numeric_maximum_when_values_are_strings(tmp_path):
    from acceptance.parcel_edit import _copy_sidecars
    (tmp_path / '采样加密.json').write_text(json.dumps({'A': '10', 'B': '2', 'C': 4}), encoding='utf-8')
    target = tmp_path / 'out'; target.mkdir()
    _copy_sidecars(str(tmp_path / 'parcels.shp'), target, ['A', 'B'], ['M'], {'A', 'B', 'C'})
    density = json.loads((target / '采样加密.json').read_text(encoding='utf-8'))
    assert density == {'M': 10, 'C': 4}


@pytest.fixture
def parcels(tmp_path):
    api = pytest.importorskip('arcpy')
    api.management.CreateFileGDB(str(tmp_path), 'source.gdb')
    fc = str(tmp_path / 'source.gdb' / 'parcels')
    sr = api.SpatialReference(4524)
    api.management.CreateFeatureclass(str(tmp_path / 'source.gdb'), 'parcels', 'POLYGON', spatial_reference=sr)
    for field in ('DIKUAI_NO', 'XMBH', 'DXBW', 'PJDYBH', 'SRC_DKBH'):
        api.management.AddField(fc, field, 'TEXT', field_length=80)
    x, y = 36500000, 2600000
    with api.da.InsertCursor(fc, ['DIKUAI_NO', 'XMBH', 'DXBW', 'PJDYBH', 'SRC_DKBH', 'SHAPE@']) as cursor:
        for index, start in enumerate((0, 20, 80)):
            polygon = api.Polygon(api.Array([api.Point(x + start, y), api.Point(x + start + 20, y),
                api.Point(x + start + 20, y + 20), api.Point(x + start, y + 20)]), sr)
            cursor.insertRow([chr(65 + index), 'PROJECT', '平地', 'OLD_UNIT', 'SRC_' + chr(65 + index), polygon])
    ids = {pid: oid for oid, pid in api.da.SearchCursor(fc, ['OID@', 'DIKUAI_NO'])}
    return api, fc, ids, tmp_path, sr


def test_actual_merge_confirm_and_undo_preserve_source_and_area(parcels):
    api, source, ids, directory, _sr = parcels
    original = _source_signature(api, source)
    result = preview(source, 'DIKUAI_NO', [ids['A'], ids['B']], 'merge', directory / 'merge')
    assert result['count'] == 2 and result['qa']['status'] == 'PASS'
    assert result['qa']['area_before_m2'] == pytest.approx(800)
    assert result['qa']['area_after_m2'] == pytest.approx(800)
    assert result['original_ids'] == ['SRC_A', 'SRC_B']
    from acceptance.oneclick import detect_fields
    assert detect_fields(result['output'])['id'] == 'PE_ID'
    assert not any(v for (v,) in api.da.SearchCursor(result['output'], ['PJDYBH']))
    committed = resolve(result['manifest'], result['token'], 'confirm', 'test operator')
    assert committed['status'] == 'COMMITTED'
    verified = resolve(committed['manifest'], committed['token'], 'verify', '')
    assert verified['token'] == committed['token'] and verified['status'] == 'COMMITTED'
    undone = resolve(committed['manifest'], committed['token'], 'undo', 'test operator')
    assert undone['status'] == 'UNDONE' and undone['source'] == source
    assert _source_signature(api, source) == original


def test_actual_split_conserves_coverage_and_assigns_unique_ids(parcels):
    api, source, ids, directory, sr = parcels
    line = {'paths': [[[36500010, 2599990], [36500010, 2600030]]], 'spatialReference': {'wkid': sr.factoryCode}}
    result = preview(source, 'DIKUAI_NO', [ids['A']], 'split', directory / 'split', json.dumps(line))
    assert result['count'] == 4 and len(set(result['new_ids'])) == 2
    assert result['qa']['coverage_difference_m2'] <= result['qa']['numeric_tolerance_m2']
    assert result['qa']['area_after_m2'] == pytest.approx(400)
    data = list(api.da.SearchCursor(result['output'], ['PE_ID', 'PE_ORIG']))
    assert len({row[0] for row in data}) == 4
    assert all(json.loads(origins) == ['SRC_A'] for pid, origins in data if pid in result['new_ids'])
    links = list(api.da.SearchCursor(result['output'], ['PE_ID', 'SRC_DKBH']))
    assert all(pid == link for pid, link in links)
    children = [oid for oid, pid in api.da.SearchCursor(result['output'], ['OID@', 'PE_ID']) if pid in result['new_ids']]
    second = preview(result['output'], 'PE_ID', children, 'merge', directory / 'remerge')
    assert second['original_ids'] == ['SRC_A']


def test_actual_nonadjacent_merge_and_tangent_split_are_refused(parcels):
    _api, source, ids, directory, sr = parcels
    with pytest.raises(ValueError, match='相邻'):
        preview(source, 'DIKUAI_NO', [ids['A'], ids['C']], 'merge', directory / 'disjoint')
    line = {'paths': [[[36500020, 2599990], [36500020, 2600030]]], 'spatialReference': {'wkid': sr.factoryCode}}
    with pytest.raises(ValueError, match='两块'):
        preview(source, 'DIKUAI_NO', [ids['A']], 'split', directory / 'tangent', json.dumps(line))


def test_actual_attribute_conflict_and_stale_candidate_cannot_be_confirmed(parcels):
    api, source, ids, directory, _sr = parcels
    with api.da.UpdateCursor(source, ['DIKUAI_NO', 'DXBW']) as cursor:
        for row in cursor:
            if row[0] == 'B': row[1] = '山地'; cursor.updateRow(row)
    with pytest.raises(ValueError, match='属性不一致'):
        preview(source, 'DIKUAI_NO', [ids['A'], ids['B']], 'merge', directory / 'conflict')
    with api.da.UpdateCursor(source, ['DXBW']) as cursor:
        for row in cursor: row[0] = '平地'; cursor.updateRow(row)
    result = preview(source, 'DIKUAI_NO', [ids['A'], ids['B']], 'merge', directory / 'changed')
    with api.da.UpdateCursor(result['output'], ['PE_M2']) as cursor:
        for row in cursor: row[0] = 1; cursor.updateRow(row)
    with pytest.raises(ValueError, match='候选'):
        resolve(result['manifest'], result['token'], 'confirm', 'operator')
    with pytest.raises(ValueError, match='候选'):
        resolve(result['manifest'], result['token'], 'verify', '')
    with api.da.UpdateCursor(result['output'], ['PE_ORIG']) as cursor:
        for row in cursor: row[0] = '"not-an-array"'; cursor.updateRow(row)
    all_oids = [oid for (oid,) in api.da.SearchCursor(result['output'], ['OID@'])]
    with pytest.raises(ValueError, match='原始地块关联'):
        preview(result['output'], 'PE_ID', all_oids, 'merge', directory / 'bad-lineage')


def test_actual_changed_source_blocks_confirmation_but_cancel_is_available(parcels):
    api, source, ids, directory, _sr = parcels
    result = preview(source, 'DIKUAI_NO', [ids['A'], ids['B']], 'merge', directory / 'stale-source')
    with api.da.UpdateCursor(source, ['DIKUAI_NO']) as cursor:
        for row in cursor:
            if row[0] == 'C': row[0] = 'C2'; cursor.updateRow(row)
    with pytest.raises(ValueError, match='上一来源'):
        resolve(result['manifest'], result['token'], 'confirm', 'operator')
    cancelled = resolve(result['manifest'], result['token'], 'cancel', 'operator')
    assert cancelled['status'] == 'CANCELLED'
    with pytest.raises(ValueError, match='状态'):
        resolve(cancelled['manifest'], cancelled['token'], 'confirm', 'operator')
