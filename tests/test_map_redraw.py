import copy
import json
import shutil
import sys
import types
from pathlib import Path

import pytest
from PIL import Image
import fitz
from acceptance import map_redraw as R, task_state as T
from acceptance.map_validation import write_atlas_bundle


@pytest.fixture
def task(tmp_path, monkeypatch):
    rows, targets = [], []
    for name in ('A', 'B'):
        files = {ext: str(tmp_path / (name+'.'+ext)) for ext in ('jpg','png','pdf')}
        for ext in ('jpg','png'): Image.new('RGB',(64,64),'green').save(files[ext])
        with fitz.open() as pdf:
            pdf.new_page(); pdf.save(files['pdf'])
        rows.append(dict(target=name, scale=2000, status='NEEDS_REVIEW', checks=[{'item':'技术','status':'PASS'}], **files))
        targets.append(dict(name=name, kind='sample', scale=2000, automatic_scale=2000, parcel_ids=[name], **files))
    project=tmp_path/'project.aprx'; project.write_bytes(b'unchanged GIS project')
    plan=tmp_path/'plan.json'; plan.write_text(json.dumps({'project':str(project),'targets':targets}))
    report={'任务运行ID':'run','输入指纹':'a'*64,'设计修订号':0,
            '技术状态':'PASS','出图':{'result_path':str(tmp_path/'plan.result.json'),'outputs':rows}}
    path=tmp_path/'report.json'; path.write_text(json.dumps(report),encoding='utf-8')
    state={'run_id':'run','input_fingerprint':'a'*64,'design_revision':0,'status':'PREPARED','updated_at':'2026-11-02 08:00:00',
           'report_path':str(path),'history':[], 'input_details':{'options':{'meta':{}}},
           'phases':{'maps_built':{'detail':{'plan':str(plan)}},'maps_exported':{'detail':{}},
            'sampling_prepared':{'detail':{'data':'keep'}}}}
    T._atomic_json(tmp_path/T.STATE_NAME,state)
    monkeypatch.setattr(R,'_check_design',lambda *_:None)
    def export(path,checkpoint=None):
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        output=[]
        for i,target in enumerate(value['targets'],1):
            if checkpoint: checkpoint(target['name'],i,len(value['targets']))
            row=copy.deepcopy(next(row for row in rows if row['target']==target['name']))
            for ext in ('jpg','png','pdf'):
                shutil.copy2(row[ext],target[ext]); row[ext]=target[ext]
            output.append(row)
        return {'outputs':output,'status':'NEEDS_REVIEW'}
    monkeypatch.setitem(sys.modules,'acceptance.map_export',types.SimpleNamespace(export_all=export))
    return tmp_path,state,report,export


def test_selected_redraw_preserves_design_and_other_maps_and_publishes_full_bundle(task):
    root,state,report,_=task
    old=(root/'report.json').read_bytes()
    result=R.redraw(root,'A')
    saved=T.read_task(root)
    current=json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert result['redrawn']==1 and result['maps']==2
    assert saved['status']=='PREPARED' and saved['input_fingerprint']==state['input_fingerprint']
    assert saved['phases']['sampling_prepared']==state['phases']['sampling_prepared']
    assert (root/'report.json').read_bytes()==old
    assert current['出图']['outputs'][0]['jpg']!=report['出图']['outputs'][0]['jpg']
    assert current['出图']['outputs'][1]['jpg']==report['出图']['outputs'][1]['jpg']
    with fitz.open(current['出图']['bundle']['pdf']) as pdf: assert len(pdf)==2
    full=json.loads(Path(current['出图']['plan_path']).read_text(encoding='utf-8'))
    assert len(full['targets'])==2


def test_redraw_cancel_preserves_previous_report(task,monkeypatch):
    root,state,_,_=task
    def cancel(path,checkpoint=None):
        T.request_cancel(root); checkpoint('A',1,2)
    monkeypatch.setitem(sys.modules,'acceptance.map_export',types.SimpleNamespace(export_all=cancel))
    with pytest.raises(T.TaskCancelled): R.redraw(root)
    saved=T.read_task(root)
    assert saved['status']=='CANCELLED' and saved['report_path']==state['report_path']


def test_failed_redraw_restores_original_task(task,monkeypatch):
    root,state,_,_=task
    def fail(*args,**kwargs): raise RuntimeError('failed renderer')
    monkeypatch.setitem(sys.modules,'acceptance.map_export',types.SimpleNamespace(export_all=fail))
    with pytest.raises(RuntimeError): R.redraw(root)
    saved=T.read_task(root)
    assert saved['status']=='PREPARED' and saved['report_path']==state['report_path']


def test_bundle_rejects_missing_file_without_publishing_pdf(task):
    root,_,report,_=task
    Path(report['出图']['outputs'][0]['png']).unlink()
    with pytest.raises(ValueError): write_atlas_bundle(report['出图'],root/'bundle')
    assert not (root/'bundle'/'整套图件.pdf').exists()


def test_scale_redraw_updates_bound_plan_without_changing_sampling(task,monkeypatch):
    root,original,report,_=task
    def resize(target,scale,*args):
        return dict(target,scale=float(scale),scale_key='sample:["A"]')
    monkeypatch.setattr(R,'_resize_target',resize)
    result=R.redraw(root,'A',scale=3000)
    state=T.read_task(root)
    current=json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert state['input_fingerprint']==current['输入指纹']!=original['input_fingerprint']
    assert state['phases']['sampling_prepared']==original['phases']['sampling_prepared']
    production=json.loads(Path(state['phases']['maps_built']['detail']['plan']).read_text(encoding='utf-8'))
    assert production['targets'][0]['scale']==3000 and production['targets'][1]['scale']==2000
    assert 'preview_only' not in production['targets'][0]
    assert current['项目设计规则']['map_scales']=={'sample:["A"]':3000}


def test_scale_failure_does_not_publish_changed_fingerprint(task,monkeypatch):
    root,original,_,_=task
    def resize(*args): raise ValueError('uncovered image')
    monkeypatch.setattr(R,'_resize_target',resize)
    with pytest.raises(ValueError,match='uncovered'): R.redraw(root,'A',scale=5000)
    saved=T.read_task(root)
    assert saved['status']==original['status'] and saved['input_fingerprint']==original['input_fingerprint']


def test_scale_cancel_restores_original_report_binding(task,monkeypatch):
    root,original,_,_=task
    monkeypatch.setattr(R,'_resize_target',lambda target,scale,*args:dict(target,scale=float(scale),scale_key='sample:["A"]'))
    def cancel(path,checkpoint=None):
        T.request_cancel(root); checkpoint('A',1,1)
    monkeypatch.setitem(sys.modules,'acceptance.map_export',types.SimpleNamespace(export_all=cancel))
    with pytest.raises(T.TaskCancelled): R.redraw(root,'A',scale=3000)
    saved=T.read_task(root)
    assert saved['status']=='CANCELLED' and saved['input_fingerprint']==original['input_fingerprint']
    assert saved['report_path']==original['report_path'] and saved['phases']==original['phases']


@pytest.mark.parametrize('failure',[None,'grid','samples','bulk','forms','imagery'])
def test_repaired_maps_clear_only_map_failure(failure):
    from acceptance.workflow import refresh_report_technical_status
    report={'技术状态':'FAIL','网格单元明细':[{'达标':True,'评价单元':'U'}],
            '项目自定义设计核对':{'technical_status':'PASS'},'容重候选':{'qa':{'status':'PASS'}},
            '外业表格':{'状态':'PASS'},'影像逐单元':{'U':{'coverage_status':'PASS'}},
            '出图':{'technical_status':'PASS'}}
    if failure=='grid': report['网格几何核验']={'status':'FAIL'}
    if failure=='samples': report['网格单元明细'][0]['达标']=False
    if failure=='bulk': report['容重候选']['qa']['status']='FAIL'
    if failure=='forms': report['外业表格']['状态']='FAIL'
    if failure=='imagery': report['影像逐单元']['U']['coverage_status']='FAIL'
    assert refresh_report_technical_status(report)==('PASS' if failure is None else 'FAIL')
