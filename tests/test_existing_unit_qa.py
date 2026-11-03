from pathlib import Path
from types import SimpleNamespace as NS
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class Rect:
    def __init__(self,x,y,w=20,h=20):
        self.extent=NS(XMin=x,YMin=y,XMax=x+w,YMax=y+h)
        self.area=w*h
        self.partCount=1
    def getArea(self,*_a):return self.area
    def intersect(self,other,_dimension):
        a,b=self.extent,other.extent
        return NS(area=max(0,min(a.XMax,b.XMax)-max(a.XMin,b.XMin))*max(0,min(a.YMax,b.YMax)-max(a.YMin,b.YMin)))

def row(pid,uid='U1',project='A',soil='土种A',geometry=None):
    return dict(oid=pid,parcel_id=pid,unit_id=uid,project=project,geometry=geometry or Rect(0,0),
        factors=dict(landform='平地',soil_species=soil,supplement_source='恢复',land_use='旱地'))

def check(rows,**kw):
    from acceptance.existing_unit_qa import check_existing_records
    return check_existing_records(rows,**kw)

def test_existing_unit_with_conflicting_four_factors_fails():
    result=check([row('A'),row('B',soil='土种B',geometry=Rect(20,0))])
    assert result['status']=='FAIL'
    assert any(c['code']=='factor_conflict' and c['unit_id']=='U1' for c in result['issues'])

def test_split_parcel_across_units_is_review_not_duplicate():
    # 图斑与单元多对多：同一源图斑拆到两个不重叠单元是合法候选，须人工确认回汇。
    result=check([row('A'),row('A','U2',geometry=Rect(20,0))])
    assert result['status']=='NEEDS_REVIEW'
    assert any(c['code']=='split_parcel_across_units' and c['status']=='NEEDS_REVIEW' for c in result['issues'])
    assert not any(c['code']=='duplicate_parcel_id' for c in result['issues'])

def test_identical_duplicate_parcel_across_units_fails():
    result=check([row('A'),row('A','U2')])
    assert result['status']=='FAIL'
    assert any(c['code']=='duplicate_parcel_id' and c['status']=='FAIL' for c in result['issues'])

def test_duplicate_parcel_across_units_and_cross_project_unit_fail():
    mixed=check([row('A'),row('B',project='B',geometry=Rect(20,0))])
    assert any(c['code']=='cross_project_unit' for c in mixed['issues'])

def test_area_principle_is_review_not_hard_failure_and_missing_factor_is_unknown():
    result=check([row('A',geometry=Rect(0,0,200,200))])
    assert result['status']=='NEEDS_REVIEW'
    assert any(c['code']=='area_principle' and c['status']=='NEEDS_REVIEW' for c in result['issues'])
    item=row('A');item['factors']['soil_species']=None
    result=check([item])
    assert result['status']=='NEEDS_REVIEW'
    assert any(c['code']=='missing_factor' for c in result['issues'])

def test_actual_overlap_fails_but_touching_boundary_passes():
    assert check([row('A'),row('B','U2',geometry=Rect(10,0))])['status']=='FAIL'
    assert check([row('A'),row('B','U2',geometry=Rect(20,0))])['status']=='PASS'

def test_raw_area_accumulates_before_rounding_and_long_id_survives():
    first=row('地块编号-'+'A'*35,geometry=Rect(0,0,1,1))
    second=row('B',geometry=Rect(1,0,1,1))
    result=check([first,second])
    assert result['units'][0]['area_m2']==2
    assert result['units'][0]['parcels'][0]==first['parcel_id']
    assert result['status']=='PASS'

@pytest.mark.parametrize('change',[dict(parcel_id=''),dict(unit_id=''),dict(geometry=None)])
def test_empty_identity_or_geometry_is_reported(change):
    item=row('A');item.update(change)
    result=check([item])
    assert result['status']=='FAIL' and result['issues']

def test_nonfinite_overlap_is_unknown_not_pass():
    a=row('A');b=row('B','U2',geometry=Rect(10,0))
    b['geometry'].intersect=lambda *_a:NS(area=float('nan'))
    result=check([a,b])
    assert result['status']=='NEEDS_REVIEW'
    assert any(item['code']=='overlap_unverified' for item in result['issues'])
