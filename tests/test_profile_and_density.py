"""项目参数与采样加密文件的纯 Python 用例。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import grid_sampling as G  # noqa: E402
from acceptance import profile as P  # noqa: E402
from acceptance import workflow as W  # noqa: E402


def test_profile_loads_whitelisted_keys(tmp_path):
    path = tmp_path / "项目参数.json"
    path.write_text(json.dumps({"max_mu": 50, "min_points": 4,
                                "meta": {"org": "某单位"}}, ensure_ascii=False), encoding="utf-8")
    data = P.load(path)
    assert data["max_mu"] == 50 and data["min_points"] == 4
    assert data["_path"] == str(path.resolve())


def test_profile_rejects_unknown_key(tmp_path):
    path = tmp_path / "项目参数.json"
    path.write_text(json.dumps({"threshold": 1}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="未知键"):
        P.load(path)


def test_blank_tool_fields_keep_project_grid_and_company_defaults():
    company = '某某检测有限公司'
    saved = dict(cell_size_m=20, meta=dict(org=company, label_mode='source_parcel'))
    values = P.merge_defaults(saved, dict(cell_size_m=None, meta=dict(org='', drawn_by='制图员')))
    assert values['cell_size_m'] == 20
    assert values['meta']['org'] == company and values['meta']['drawn_by'] == '制图员'
    explicit = P.merge_defaults(saved, dict(cell_size_m=40, meta=dict(org='明确填写的其他单位')))
    assert explicit['cell_size_m'] == 40 and explicit['meta']['org'] == '明确填写的其他单位'
    assert saved['meta']['org'] == company


def test_gdb_feature_uses_profile_beside_geodatabase(tmp_path):
    database = tmp_path / 'work.gdb'
    database.mkdir()
    profile = tmp_path / '项目参数.json'
    profile.write_text('{"cell_size_m":20}', encoding='utf-8')
    assert P.default_path(database / 'feature_dataset' / 'parcels') == str(profile)


def test_layer_name_uses_catalog_for_all_project_sidecars(tmp_path, monkeypatch):
    import types
    database = tmp_path / 'source.gdb'
    database.mkdir()
    catalog = str(database / 'parcels')
    monkeypatch.setitem(sys.modules, 'arcpy', types.SimpleNamespace(
        Describe=lambda source: types.SimpleNamespace(catalogPath=catalog)))
    profile = tmp_path / '项目参数.json'
    profile.write_text('{"cell_size_m":20}', encoding='utf-8')
    (tmp_path / '采样加密.json').write_text('{"U1":5}', encoding='utf-8')
    assert P.default_path('地图中的图斑') == str(profile)
    assert W._load_density('地图中的图斑', lambda _: None)[0] == {'U1': 5}


def test_sampling_fingerprint_includes_metric_implementation(tmp_path):
    from acceptance.task_state import input_fingerprint
    source = tmp_path / 'parcels.json'
    source.write_text('{}', encoding='utf-8')
    _, payload = input_fingerprint(str(source), {})
    assert payload['engine']['sampling_prepared']['sampling_metrics.py']


def test_density_file_is_optional_and_validated(tmp_path):
    source = tmp_path / "图斑.shp"
    source.write_text("", encoding="utf-8")
    assert W._load_density(str(source), lambda _m: None) == ({}, None)
    (tmp_path / "采样加密.json").write_text(json.dumps({"E0001": 5, "12": 4}), encoding="utf-8")
    density, path = W._load_density(str(source), lambda _m: None)
    assert density == {"E0001": 5, "12": 4} and path.endswith("采样加密.json")
    (tmp_path / "采样加密.json").write_text(json.dumps({"E0001": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="必须为正"):
        W._load_density(str(source), lambda _m: None)


def test_unit_min_points_uses_unit_or_parcel_key():
    items = [{"parcel": "12"}, {"parcel": "13"}]
    assert G.unit_min_points("E0001", items, {}, 3) == 3
    assert G.unit_min_points("E0001", items, {"E0001": 5}, 3) == 5
    assert G.unit_min_points("E0001", items, {"12": 4}, 3) == 4
    assert G.unit_min_points("E0001", items, {"E0001": {"min_points": 6}}, 3) == 6


def test_nested_project_rules_and_presentation_merge_without_losing_saved_values():
    saved={'meta':{'project_rules':{'index_scale':25000,'excluded_parcels':['124']},
                   'presentation':{'show_inset':False,'grid_line_width':0.9}}}
    value=P.merge_defaults(saved,{'meta':{'project_rules':{'sample_min_scale':1000},
                                        'presentation':{'show_preview_label':False}}})
    assert value['meta']['project_rules']=={'index_scale':25000,'excluded_parcels':['124'],'sample_min_scale':1000}
    assert value['meta']['presentation']['show_inset'] is False
    assert 'sample_min_scale' not in saved['meta']['project_rules']
    clearing=P.merge_defaults({'meta':{'project_rules':{'point_counts_by_unit':{'P_1':1}}}},
                             {'meta':{'project_rules':{'point_counts_by_unit':{}}}})
    assert clearing['meta']['project_rules']['point_counts_by_unit']=={}


def test_project_rules_are_validated_and_do_not_accept_ambiguous_values():
    assert P.project_rules({'project_rules':{'point_counts_by_unit':{'P_1':1},'combine_parcels':[['22','111']]}})['point_counts_by_unit']=={'P_1':1}
    for rules in ({'point_counts_by_unit':{'P':True}}, {'index_scale':float('inf')},
                  {'excluded_parcels':['1'],'combine_parcels':[['1','2']]}, {'unknown':1}, []):
        with pytest.raises(ValueError):P.project_rules({'project_rules':rules})


def test_custom_design_differences_do_not_hide_spatial_or_unapproved_coverage_failures():
    from acceptance.point_adjustment import custom_design_check
    qa={'status':'FAIL','unit_checks':[{'unit_id':'PAIR','generated':1,'required':3,'status':'FAIL'}],
        'parcel_checks':[{'unit_id':'PAIR','parcel_id':'111','generated':0,'status':'FAIL'}],
        'raw_point_count':1,'valid_linked_point_count':1}
    rules={'point_counts_by_unit':{'PAIR':1},'combine_parcels':[['22','111']]}
    rows=[{'unit':'PAIR','parcels':['22','111']}]
    result=custom_design_check(qa,rules,rows)
    assert result['technical_status']=='PASS' and result['normative_status']=='FAIL'
    assert qa['status']=='FAIL'
    assert custom_design_check(dict(qa,spatial_relation_failures=[{'point_id':'bad'}]),rules,rows)['technical_status']=='FAIL'
    assert custom_design_check(qa,{'point_counts_by_unit':{'PAIR':1}},rows)['technical_status']=='FAIL'


def test_workflow_revalidates_cached_visual_receipt_instead_of_trusting_old_pass(tmp_path,monkeypatch):
    def result(path,key):
        return {'status':'NEEDS_REVIEW','detail':'changed render','image_sha256':'new'}
    monkeypatch.setattr(W,'validate_visual_review',result)
    saved={'status':'PASS','outputs':[{'target':'T','jpg':str(tmp_path/'map.jpg'),'render_key':'new',
        'status':'PASS','visual_review':{'status':'PASS'},'checks':[{'item':'视觉复核','status':'PASS'}]}]}
    summary=W.refresh_visual_reviews(saved)
    assert summary['status']=='NEEDS_REVIEW' and saved['status']=='NEEDS_REVIEW'
    assert saved['outputs'][0]['visual_review']['status']=='NEEDS_REVIEW'
    assert saved['outputs'][0]['checks']==[{'item':'视觉复核','status':'NEEDS_REVIEW','detail':'changed render'}]


def test_visual_pass_is_separate_from_unverified_imagery(monkeypatch):
    monkeypatch.setattr(W,'validate_visual_review',lambda *_args:{'status':'PASS','detail':'current image reviewed'})
    exported={'outputs':[{'jpg':'map.jpg','render_key':'key','checks':[{'item':'原始GSD','status':'NEEDS_REVIEW'}]}]}
    assert W.refresh_visual_reviews(exported)['status']=='PASS'
    assert exported['status']=='NEEDS_REVIEW'
