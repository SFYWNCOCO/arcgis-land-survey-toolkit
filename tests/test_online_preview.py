from pathlib import Path
from types import SimpleNamespace as NS
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_online_preview_reads_selected_scope_and_keeps_network_failure_visible(tmp_path, monkeypatch):
    from acceptance import online_preview as P
    calls = []
    class Cursor:
        def __enter__(self): return iter([('P01', NS(extent='FIRST_EXTENT'))])
        def __exit__(self, *_a): pass
    api = NS(ListFields=lambda _: [NS(name='DKBH')],
        Describe=lambda _: NS(OIDFieldName='OBJECTID', spatialReference=NS(factoryCode=4525)),
        AddFieldDelimiters=lambda _s, f: f,
        da=NS(SearchCursor=lambda *a, **kw: calls.append(kw['where_clause']) or Cursor()))
    monkeypatch.setattr(P.I, 'prepare_imagery', lambda *a, **kw: dict(output=None, coverage_status='UNVERIFIED',
        online_diagnostics=dict(primary_reason='NETWORK_FAILED'), warning='网络错误'))
    result = P.preview_online('source.shp', str(tmp_path / 'preview'), provider='demo',
        selection_oids='2,3', source_where_clause="XMBH='A'", arcpy_module=api)
    assert 'OBJECTID IN (2,3)' in calls[0] and "XMBH='A'" in calls[0]
    assert result['parcel_id'] == 'P01' and result['preview_png'] is None
    assert result['imagery']['online_diagnostics']['primary_reason'] == 'NETWORK_FAILED'
    assert json.loads(Path(result['report_path']).read_text(encoding='utf-8'))['status'] == 'NEEDS_REVIEW'


def test_preview_validates_geometry_coverage_and_preserves_alpha(tmp_path,monkeypatch):
    import types
    import numpy as np
    from PIL import Image
    from acceptance import online_preview as P
    class Geometry:
        extent='extent'
        WKT='POLYGON ((0 0,1 0,1 1,0 1,0 0))'
        def __iter__(self):return iter([[NS(X=0,Y=0),NS(X=1,Y=0),NS(X=1,Y=1)]])
    class Cursor:
        def __enter__(self):return iter([('P01',Geometry())])
        def __exit__(self,*_a):pass
    api=NS(ListFields=lambda _:[NS(name='DKBH')],Describe=lambda _:NS(spatialReference=NS(factoryCode=4525)),
        da=NS(SearchCursor=lambda *a,**kw:Cursor()))
    pixels=np.zeros((4,64,64),dtype='uint8')
    pixels[:3]=120
    raster=NS(RasterXSize=64,RasterYSize=64,ReadAsArray=lambda **kw:pixels,
        GetGeoTransform=lambda:(0,1,0,64,0,-1))
    osgeo=types.ModuleType('osgeo');osgeo.gdal=NS(Open=lambda _:raster)
    monkeypatch.setitem(sys.modules,'osgeo',osgeo)
    monkeypatch.setattr(P.I,'prepare_imagery',lambda *a,**kw:dict(output='received.tif',coverage_status='PASS'))
    observed=[]
    monkeypatch.setattr(P.I,'inspect_raster_coverage',lambda path,wkt:observed.append(wkt) or
        dict(status='FAIL',coverage_status='FAIL',detail='NoData空洞'))
    result=P.preview_online('source.shp',str(tmp_path/'preview'),arcpy_module=api)
    assert observed==[Geometry.WKT]
    assert result['usable_for_parcel_preview'] is False
    assert result['imagery']['coverage_status']=='FAIL'
    with Image.open(result['preview_png']) as image:
        assert image.mode=='RGBA' and image.getpixel((30,30))[3]==0
