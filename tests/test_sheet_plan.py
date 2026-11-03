import pytest
from acceptance import sheet_plan as S


def test_per_map_scale_is_bound_to_parcels_and_recomputes_imagery_frame():
    rows = [{'parcel':str(n), 'parcel_ids':[str(n)], 'unit_ids':['U'+str(n)],
             'bbox_native':[n*1000,0,n*1000+100,100]} for n in (1,2)]
    original=S.plan_atlas(rows)
    selected=next(t for t in original if t['kind']=='sample')
    changed=S.plan_atlas(rows,project_rules={'map_scales':{selected['scale_key']:3000}})
    actual=next(t for t in changed if t['scale_key']==selected['scale_key'])
    assert actual['scale']==3000 and actual['automatic_scale']==2000
    assert actual['frame_extent'][2]-actual['frame_extent'][0]==pytest.approx(826.2)
    assert all(a==b for a,b in zip(original,changed) if a['scale_key']!=selected['scale_key'])
    with pytest.raises(ValueError,match='裁切'):
        S.plan_atlas(rows,project_rules={'map_scales':{selected['scale_key']:100}})


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),True,'2000'])
def test_map_scale_rejects_invalid_denominator(value):
    with pytest.raises(ValueError): S.validate_map_scales({'sample:["1"]':value})


def test_scale_recommendation_tracks_the_new_scale_in_both_directions():
    target={'name':'采点图','kind':'sample','parcel_ids':['1'],'extent':[0,0,50,50],'scale':2000}
    key=S.map_scale_key(target)
    wide=S.apply_map_scales([target],{key:20000})[0]
    assert wide['scale_recommendation']=='NEEDS_REVIEW'
    close=S.apply_map_scales([wide],{key:2000})[0]
    assert close['scale_recommendation']=='PASS'


def test_small_project_needs_no_extra_main_sheets():
    rows = [{"unit": "U1", "bbox_native": [0, 0, 100, 100]}]
    assert S.plan_sheets(rows, 18, 16) == []


def test_sparse_large_project_gets_only_occupied_clear_sheets():
    rows = [{"unit": "U1", "bbox_native": [0, 0, 100, 100]},
            {"unit": "U2", "bbox_native": [10000, 10000, 10100, 10100]}]
    sheets = S.plan_sheets(rows, 18, 16)
    assert len(sheets) == 2
    assert {uid for sheet in sheets for uid in sheet["unit_ids"]} == {"U1", "U2"}
    assert len({sheet["sheet_id"] for sheet in sheets}) == 2
    assert all(sheet["scale"] <= 10000 for sheet in sheets)


def test_sheet_generation_is_bounded_and_handles_linear_units():
    rows = [{"unit": "U", "bbox_native": [0, 0, 10000000, 10000000]}]
    with pytest.raises(ValueError, match="分幅"):
        S.plan_sheets(rows, 18, 16, max_sheets=100)
    assert S.plan_sheets([{"unit": "U", "bbox_native": [0, 0, 3000, 3000]}], 18, 16, meters_per_unit=0.3048) == []


def test_overlapping_sheet_band_does_not_duplicate_units():
    rows = [{"unit": "U1", "bbox_native": [0, 0, 100, 100]},
            {"unit": "U2", "bbox_native": [0, 1300, 100, 1400]},
            {"unit": "U3", "bbox_native": [0, 4000, 100, 4100]}]
    sheets = S.plan_sheets(rows, 18, 16)
    counts = {}
    for sheet in sheets:
        for unit in sheet["unit_ids"]:
            counts[unit] = counts.get(unit, 0) + 1
    assert counts == {"U1": 1, "U2": 1, "U3": 1}


def test_atlas_uses_original_parcels_independently_of_sampling_groups():
    rows = [{"parcel": str(i + 1), "parcel_ids": [str(i + 1)],
             "unit_ids": ["SG_pair" if i < 2 else "U" + str(i)],
             "bbox_native": [i * 120, 0, i * 120 + 80, 80], "mu": 9.6}
            for i in range(9)]
    targets = S.plan_atlas(rows)
    samples = [t for t in targets if t["kind"] == "sample"]
    assert len(samples) == 9
    assert {t["parcel"] for t in samples} == {row["parcel"] for row in rows}
    assert all(t["scale"] == 2000 and len(t["parcel_ids"]) == 1 for t in samples)
    assert S.plan_atlas(list(reversed(rows))) == targets


def test_atlas_clusters_location_maps_and_frames_cover_all_planned_parcels():
    rows = [{"parcel": str(i), "parcel_ids": [str(i)], "unit_ids": ["U" + str(i)],
             "bbox_native": [x, y, x + 80, y + 80], "mu": 9.6}
            for i, (x, y) in enumerate([(0, 0), (150, 0), (10000, 10000)], 1)]
    targets = S.plan_atlas(rows)
    assert len([t for t in targets if t["kind"] == "index"]) == 2
    locations = [t for t in targets if t["kind"] == "geo"]
    assert len(locations) == 2
    assert sorted(pid for t in locations for pid in t["parcel_ids"]) == ["1", "2", "3"]
    assert all(t["scale"] == 10000 for t in locations)
    boxes = {r["parcel"]: r["bbox_native"] for r in rows}
    for target in targets:
        x0, y0, x1, y1 = target["frame_extent"]
        for pid in target["parcel_ids"]:
            a, b, c, d = boxes[pid]
            assert x0 <= a <= c <= x1 and y0 <= b <= d <= y1


def test_atlas_single_selection_does_not_reintroduce_other_parcels():
    rows = [{"parcel": "39", "parcel_ids": ["F001", "F002"], "unit_ids": ["U1", "U2"],
             "bbox_native": [0, 0, 80, 80], "mu": 9.6}]
    targets = S.plan_atlas(rows)
    assert [t["kind"] for t in targets] == ["geo", "sample"]
    assert all(t["parcel_ids"] == ["F001", "F002"] for t in targets)
    assert [t["parcel"] for t in targets if t["kind"] == "sample"] == ["39"]
    with pytest.raises(ValueError, match="重复"):
        S.plan_atlas(rows + rows)
    with pytest.raises(ValueError, match="范围"):
        S.plan_atlas([dict(rows[0], bbox_native=[0, 0, float("nan"), 80])])


def test_overview_keeps_25000_and_splits_distant_parcels_instead_of_zooming_out():
    rows = [{'parcel': str(i), 'parcel_ids': [str(i)], 'unit_ids': ['U'+str(i)],
             'bbox_native': [x, 0, x+100, 100]} for i,x in enumerate((0,12000,24000),1)]
    targets = S.plan_atlas(rows)
    overview = [t for t in targets if t['kind']=='index']
    assert len(overview)==3 and all(t['scale']==25000 for t in overview)
    assert sorted(p for t in overview for p in t['parcel_ids'])==['1','2','3']


def test_project_sampling_scale_fits_small_parcels_and_combines_confirmed_pair():
    rows = [{'parcel': str(i), 'parcel_ids': [str(i)], 'unit_ids': ['PAIR'],
             'bbox_native': [x, 0, x+80, 80]} for i,x in ((22,0),(111,90))]
    targets=S.plan_atlas(rows, project_rules={'sample_min_scale':1000,'combine_parcels':[['22','111']]})
    samples=[t for t in targets if t['kind']=='sample']
    assert len(samples)==1 and set(samples[0]['parcel_ids'])=={'22','111'}
    assert samples[0]['scale']==1000


def test_oversized_overview_parcel_is_tiled_without_changing_locked_scale():
    rows=[{'parcel':'L','parcel_ids':['L'],'unit_ids':['L'],'bbox_native':[0,0,18000,8000]},
          {'parcel':'S','parcel_ids':['S'],'unit_ids':['S'],'bbox_native':[25000,0,25100,100]}]
    overview=[t for t in S.plan_atlas(rows) if t['kind']=='index']
    assert len(overview)>2 and all(t['scale']==25000 for t in overview)
    assert any(t.get('partial_coverage') for t in overview)
