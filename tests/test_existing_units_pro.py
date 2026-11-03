from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
arcpy=pytest.importorskip('arcpy')
from acceptance import oneclick as O
from acceptance import grid_sampling as G

def polygon(x,width=80):
    sr=arcpy.SpatialReference(4525)
    return arcpy.Polygon(arcpy.Array([arcpy.Point(x,2550000),arcpy.Point(x+width,2550000),
        arcpy.Point(x+width,2550080),arcpy.Point(x,2550080)]),sr)

@pytest.fixture
def source(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path),'input.gdb')
    gdb=str(tmp_path/'input.gdb');fc=gdb+'/parcels'
    arcpy.management.CreateFeatureclass(gdb,'parcels','POLYGON',spatial_reference=arcpy.SpatialReference(4525))
    for name in ('PJDYBH','DKBH','DXBW','TZMC','BCGDLY','DLMC','XMBH'):
        arcpy.management.AddField(fc,name,'TEXT',field_length=255)
    return fc,gdb

def insert(source,records):
    with arcpy.da.InsertCursor(source,['SHAPE@','PJDYBH','DKBH','DXBW','TZMC','BCGDLY','DLMC','XMBH']) as cursor:
        for geometry,uid,pid,soil in records:cursor.insertRow([geometry,uid,pid,'平地',soil,'恢复','旱地','测试项目'])

def test_existing_units_do_not_bypass_factor_conflicts(source):
    fc,_=source
    insert(fc,[(polygon(36500000),'U1','P1','土种A'),(polygon(36500080),'U1','P2','土种B')])
    with pytest.raises(ValueError,match='已有单元检查'):
        O._existing_unit_rows(arcpy,fc,'PJDYBH','DKBH')

def test_existing_units_detect_real_overlap(source):
    fc,_=source
    insert(fc,[(polygon(36500000),'U1','P1','土种A'),(polygon(36500040),'U2','P2','土种A')])
    with pytest.raises(ValueError,match='已有单元检查'):
        O._existing_unit_rows(arcpy,fc,'PJDYBH','DKBH')

def test_long_parcel_id_survives_grid_and_shapefile_export(source,tmp_path):
    fc,gdb=source;pid='P'+'1234567890'*4
    insert(fc,[(polygon(36500000),'U1',pid,'土种A')])
    result=G.grid_design(fc,'PJDYBH','DKBH',gdb)
    assert {row[0] for row in arcpy.da.SearchCursor(result['sample'],['DKBH'])}=={pid}
    assert all(row[0] in (None,'',pid) for row in arcpy.da.SearchCursor(result['center'],['DKBH']))
    shp=G.export_shapefiles(arcpy,result,str(tmp_path/'shp'))
    paths=list(shp.values()) if isinstance(shp,dict) else shp
    sample=next(path for path in paths if '设计采样点' in str(path))
    assert {row[0] for row in arcpy.da.SearchCursor(sample,['DKBH'])}=={pid}

@pytest.mark.parametrize('pid',['P'+'A'*254,'地'*180],ids=['ascii255','multibyte'])
def test_shapefile_limit_cannot_silently_change_identifiers(source,tmp_path,pid):
    fc,gdb=source
    insert(fc,[(polygon(36500000),'U1',pid,'土种A')])
    result=G.grid_design(fc,'PJDYBH','DKBH',gdb)
    assert {row[0] for row in arcpy.da.SearchCursor(result['sample'],['DKBH'])}=={pid}
    with pytest.raises(ValueError,match='SHP.*编号'):
        G.export_shapefiles(arcpy,result,str(tmp_path/'shp'))

def test_all_blank_auto_unit_field_builds_candidates_instead_of_stopping(source,tmp_path):
    fc,_=source
    insert(fc,[(polygon(36500000),None,'P1','土种A'),(polygon(36500080),None,'P2','土种A')])
    result=O.run(fc,str(tmp_path/'automatic'),make_maps=False)
    assert result['评价单元数']==1 and result['图斑数']==2
    assert result['单元划分依据'] is not None
    assert {row[0] for row in arcpy.da.SearchCursor(fc,['PJDYBH'])}=={None}

def test_explicit_blank_existing_units_remain_failure(source,tmp_path):
    fc,_=source
    insert(fc,[(polygon(36500000),None,'P1','土种A')])
    with pytest.raises(ValueError,match='已有单元检查'):
        O.run(fc,str(tmp_path/'explicit'),unit_field='PJDYBH',make_maps=False)
