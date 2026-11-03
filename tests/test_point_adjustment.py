import pytest
arcpy = pytest.importorskip("arcpy")
from acceptance import point_adjustment as P
from acceptance import oneclick as O
from acceptance import task_state as T


def test_project_rules_exclude_and_reduce_only_the_task_copy_with_true_normative_failure(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path),'rules.gdb')
    gdb=str(tmp_path/'rules.gdb');source=gdb+'/parcels';sr=arcpy.SpatialReference(4524)
    arcpy.management.CreateFeatureclass(gdb,'parcels','POLYGON',spatial_reference=sr)
    for name in ('DKBH','PJDYBH'):arcpy.management.AddField(source,name,'TEXT',field_length=40)
    with arcpy.da.InsertCursor(source,['SHAPE@','DKBH','PJDYBH']) as cursor:
        for pid,uid,x in [('22','PAIR',36355000),('111','PAIR',36355120),('171','OTHER',36356000)]:
            g=arcpy.Polygon(arcpy.Array([arcpy.Point(x,2621800),arcpy.Point(x+120,2621800),
                arcpy.Point(x+120,2621920),arcpy.Point(x,2621920)]),sr)
            cursor.insertRow([g,pid,uid])
    rules={'excluded_parcels':['171'],'point_counts_by_unit':{'PAIR':1},'combine_parcels':[['22','111']]}
    out=str(tmp_path/'task')
    report=O.run(source,out,task_action='prepare',make_maps=False,meta={'project_rules':rules})
    state=T.read_task(out);data=state['phases']['data_prepared']['detail'];sampling=state['phases']['sampling_prepared']['detail']
    assert int(arcpy.management.GetCount(source)[0])==3
    assert int(arcpy.management.GetCount(data['parcels'])[0])==2
    assert int(arcpy.management.GetCount(sampling['grid']['sample'])[0])==1
    assert sampling['grid']['counts']['设计点']==1
    assert int(arcpy.management.GetCount(sampling['shapefiles']['sample'])[0])==1
    assert sampling['independent_qa']['status']=='FAIL'
    assert report['项目自定义设计核对']['technical_status']=='PASS'
    assert report['项目自定义设计核对']['normative_status']=='FAIL'
    row=next(iter(arcpy.da.SearchCursor(sampling['grid']['sample'],['POINT_ID','ORIG_POINT'])))
    assert row[0]=='PAIR-01' and row[1]
    with pytest.raises(ValueError,match='未知采样组'):
        P.apply_project_point_counts(arcpy,sampling['grid']['sample'],sampling['units_for_map'],data['unit_field'],{'WRONG_PROJECT':1})
    excluded_oid=next(oid for oid,pid in arcpy.da.SearchCursor(source,['OID@','DKBH']) if pid=='171')
    with pytest.raises(ValueError,match='没有地块'):
        O.run(source,str(tmp_path/'empty_selection'),task_action='prepare',make_maps=False,
              selection_oids=[excluded_oid],meta={'project_rules':rules})
    assert int(arcpy.management.GetCount(source)[0])==3


def test_point_table_exports_projected_and_cgcs2000_coordinates(tmp_path):
    import csv
    gdb = str(tmp_path / 'coords.gdb')
    arcpy.management.CreateFileGDB(str(tmp_path), 'coords.gdb')
    source = gdb + '/points'
    sr = arcpy.SpatialReference(4524)
    arcpy.management.CreateFeatureclass(gdb, 'points', 'POINT', spatial_reference=sr)
    for name in ('PJDYBH', 'DKBH', 'POINT_ID', 'SAMPLE_ID', 'DESIGN_ST'):
        arcpy.management.AddField(source, name, 'TEXT', field_length=80)
    for name in ('DEPTH_TOP', 'DEPTH_BOT'):
        arcpy.management.AddField(source, name, 'SHORT')
    geometry = arcpy.PointGeometry(arcpy.Point(36355000, 2621800), sr)
    with arcpy.da.InsertCursor(source, ['SHAPE@', 'PJDYBH', 'DKBH', 'POINT_ID', 'SAMPLE_ID', 'DESIGN_ST', 'DEPTH_TOP', 'DEPTH_BOT']) as cursor:
        cursor.insertRow([geometry, None, '31', '31-01', None, 'DESIGN_ONLY', 0, 20])
    output = str(tmp_path / 'coords.csv')
    P.write_point_csv(output, source, arcpy)
    with open(output, encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    expected = geometry.projectAs(arcpy.SpatialReference(4490)).firstPoint
    assert row['评价单元'] == '' and row['图斑编号'] == '31'
    assert float(row['经度_CGCS2000_度']) == pytest.approx(expected.X, abs=5e-7)
    assert float(row['纬度_CGCS2000_度']) == pytest.approx(expected.Y, abs=5e-7)
    assert row['经度_度分秒'].endswith('E') and row['纬度_度分秒'].endswith('N')


def test_degree_minute_second_format_handles_rounding_carry():
    assert P.format_dms(179.9999999, 'longitude') == '180°00′00.00″E'
    assert P.format_dms(-23.5, 'latitude') == '23°30′00.00″S'


def test_snap_edits_only_working_point_and_immediately_rechecks(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "source.gdb")
    source = str(tmp_path / "source.gdb" / "parcels")
    sr = arcpy.SpatialReference(4525)
    arcpy.management.CreateFeatureclass(str(tmp_path / "source.gdb"), "parcels", "POLYGON", spatial_reference=sr)
    for name in ("DKBH", "PJDYBH"): arcpy.management.AddField(source, name, "TEXT", field_length=30)
    geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(36500000, 2550000), arcpy.Point(36500120, 2550000),
        arcpy.Point(36500120, 2550120), arcpy.Point(36500000, 2550120)]), sr)
    with arcpy.da.InsertCursor(source, ["SHAPE@", "DKBH", "PJDYBH"]) as cursor:
        cursor.insertRow([geometry, "P01", "U01"])
    out = str(tmp_path / "task")
    O.run(source, out, task_action="prepare", make_maps=False)
    state = T.read_task(out)
    grid = state["phases"]["sampling_prepared"]["detail"]["grid"]
    existing = list(arcpy.da.SearchCursor(grid["sample"], ["POINT_ID", "CELL_NO", "SHAPE@XY"]))
    used = {row[1] for row in existing}
    free = next(row for row in arcpy.da.SearchCursor(grid["center"], ["CELL_NO", "IN_UNIT", "SHAPE@XY"])
                if row[0] not in used and row[1])
    result = P.adjust_point(out, existing[0][0], free[2][0], free[2][1], 4525, "测试调整人", max_snap_m=5)
    assert result["qa"]["status"] != "FAIL"
    assert result["new_cell"] == free[0]
    assert result["old_xy"] != result["new_xy"]
    assert T.read_task(out)["design_revision"] == 1
    assert T.read_task(out)["phases"]["maps_built"]["status"] == "PENDING"
    context = T.read_task(out)["phases"]["sampling_prepared"]["detail"]
    shp = context["shapefiles"]["sample"]
    copy_xy = next(row[1] for row in arcpy.da.SearchCursor(shp, ["POINT_ID", "SHAPE@XY"]) if row[0] == existing[0][0])
    assert list(copy_xy) == pytest.approx(result["new_xy"])
    assert int(arcpy.management.GetCount(source)[0]) == 1
    with pytest.raises(ValueError, match="候选"):
        P.adjust_point(out, existing[0][0], 37500000, 2650000, 4525, "测试调整人", max_snap_m=5)
