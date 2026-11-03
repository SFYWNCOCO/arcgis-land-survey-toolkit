"""DEM math and synthetic raster integration; no project terrain values are assumed."""
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import dem as D


def test_horn_slope_matches_known_plane_in_degrees():
    y, x = np.mgrid[:9, :9]
    values = x * 10.0 + y * 0.0
    slope = D.slope_degrees(values, 10, 10)
    assert np.allclose(slope[1:-1, 1:-1], 45.0)
    assert np.isnan(slope[0]).all()


def test_flat_surface_and_feet_conversion():
    assert np.allclose(D.slope_degrees(np.full((5, 5), 123), 5, 5)[1:-1, 1:-1], 0)
    y, x = np.mgrid[:5, :5]
    slope = D.slope_degrees(x * 10 / .3048, 10, 10, z_to_m=.3048)
    assert slope[2, 2] == pytest.approx(45)
    assert D.vertical_factor('英尺') == .3048
    with pytest.raises(ValueError, match='高程单位'):
        D.vertical_factor('unknown')


def test_nodata_is_not_flat_ground():
    values = np.full((7, 7), 100.0)
    values[3, 3] = -9999
    slope = D.slope_degrees(values, 10, 10, nodata=-9999)
    assert np.isnan(slope[2:5, 2:5]).all()
    assert slope[1, 1] == 0


def test_bilinear_sampling_rejects_missing_contributors():
    array = np.array([[0., 10.], [20., 30.]])
    transform = (0, 10, 0, 20, 0, -10)
    assert D.sample_bilinear(array, transform, 10, 10) == 15
    assert D.sample_bilinear(array, transform, 50, 50) is None
    array[0, 0] = np.nan
    assert D.sample_bilinear(array, transform, 10, 10) is None


def _raster(gdal, osr, path, array, epsg, transform):
    dataset = gdal.GetDriverByName('GTiff').Create(str(path), array.shape[1], array.shape[0], 1, gdal.GDT_Float32)
    spatial = osr.SpatialReference(); spatial.ImportFromEPSG(epsg)
    dataset.SetProjection(spatial.ExportToWkt()); dataset.SetGeoTransform(transform)
    dataset.GetRasterBand(1).WriteArray(array); dataset.GetRasterBand(1).SetNoDataValue(-9999)
    dataset = None


def _features(arcpy, tmp_path, point_xy, polygons=None):
    arcpy.management.CreateFileGDB(str(tmp_path), 'input.gdb')
    gdb = str(tmp_path / 'input.gdb'); sr = arcpy.SpatialReference(4524)
    points = gdb + '/points'
    arcpy.management.CreateFeatureclass(gdb, 'points', 'POINT', spatial_reference=sr)
    arcpy.management.AddField(points, 'POINT_ID', 'TEXT', field_length=30)
    with arcpy.da.InsertCursor(points, ['POINT_ID', 'SHAPE@XY']) as cursor:
        for index, xy in enumerate(point_xy, 1): cursor.insertRow(['031-{:02d}'.format(index), xy])
    units = None
    if polygons:
        units = gdb + '/parcels'
        arcpy.management.CreateFeatureclass(gdb, 'parcels', 'POLYGON', spatial_reference=sr)
        arcpy.management.AddField(units, 'DKBH', 'TEXT', field_length=30)
        with arcpy.da.InsertCursor(units, ['DKBH', 'SHAPE@']) as cursor:
            for ring in polygons:
                cursor.insertRow(['031', arcpy.Polygon(arcpy.Array([arcpy.Point(*xy) for xy in ring]), sr)])
    return points, units


def test_synthetic_projected_dem_point_and_polygon_statistics(tmp_path):
    arcpy = pytest.importorskip('arcpy')
    gdal = pytest.importorskip('osgeo.gdal'); osr = pytest.importorskip('osgeo.osr')
    x, y = 36550000, 2600000
    raster = tmp_path / 'synthetic_plane.tif'
    values = np.tile(100 + (np.arange(20) + .5), (20, 1))
    _raster(gdal, osr, raster, values, 4524, (x, 10, 0, y + 200, 0, -10))
    points, units = _features(arcpy, tmp_path, [(x + 65, y + 65), (x + 500, y + 500)],
        [[(x+40,y+40),(x+80,y+40),(x+80,y+80),(x+40,y+80),(x+40,y+40)]])
    result = D.extract_terrain(str(raster), points, 'POINT_ID', str(tmp_path / 'result'),
                               unit_fc=units, unit_id_field='DKBH', vertical_unit='米')
    assert result['calculation_status'] == 'PASS'
    assert result['vertical_reference_status'] == 'UNVERIFIED'
    assert result['points'][0]['point_id'] == '031-01'
    assert result['points'][0]['elevation_m'] == pytest.approx(106.5, abs=.01)
    assert result['points'][0]['slope_deg'] == pytest.approx(math.degrees(math.atan(.1)), abs=.01)
    assert result['points'][1]['elevation_m'] is None
    assert result['points'][1]['slope_deg'] is None
    assert result['units'][0]['unit_id'] == '031'
    assert result['units'][0]['mean_elevation_m'] == pytest.approx(106, abs=.01)
    assert result['units'][0]['elevation_coverage'] == 1.0
    assert int(arcpy.management.GetCount(points)[0]) == 2
    assert 'Z_DEM_M' not in {f.name for f in arcpy.ListFields(points)}
    assert {'Z_DEM_M', 'SLOPE_DEG'} <= {f.name for f in arcpy.ListFields(result['point_output'])}
    stored = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert stored['points'][1]['elevation_m'] is None


def test_geographic_dem_is_reprojected_and_vertical_units_are_respected(tmp_path):
    arcpy = pytest.importorskip('arcpy')
    gdal = pytest.importorskip('osgeo.gdal'); osr = pytest.importorskip('osgeo.osr')
    raster = tmp_path / 'synthetic_geographic_dem.tif'
    _raster(gdal, osr, raster, np.full((100, 100), 123), 4326, (106.595, .0001, 0, 23.605, 0, -.0001))
    point = arcpy.PointGeometry(arcpy.Point(106.6, 23.6), arcpy.SpatialReference(4326)).projectAs(arcpy.SpatialReference(4524)).firstPoint
    points, _ = _features(arcpy, tmp_path, [(point.X, point.Y)])
    result = D.extract_terrain(str(raster), points, 'POINT_ID', str(tmp_path / 'result'), vertical_unit='英尺')
    assert result['dem']['reprojected'] is True
    assert result['dem']['working_resolution_m'][0] > 1
    assert result['points'][0]['elevation_m'] == pytest.approx(123 * .3048, abs=.001)
    assert result['points'][0]['slope_deg'] == pytest.approx(0, abs=.001)


def test_actual_gp_tool_executes_and_writes_derived_report(tmp_path):
    arcpy = pytest.importorskip('arcpy')
    gdal = pytest.importorskip('osgeo.gdal'); osr = pytest.importorskip('osgeo.osr')
    x, y = 36550000, 2600000
    raster = tmp_path / 'synthetic_gp_dem.tif'
    _raster(gdal, osr, raster, np.full((10, 10), 123), 4524, (x, 10, 0, y + 100, 0, -10))
    points, _ = _features(arcpy, tmp_path, [(x + 45, y + 45)])
    toolbox = arcpy.ImportToolbox(str(ROOT / 'toolboxes/GridSampling.pyt'), 'demsmoke')
    toolbox.ExtractDemTerrain(str(raster), points, 'POINT_ID', None, None,
                             str(tmp_path / 'gp_result'), '米', '')
    report = json.loads((tmp_path / 'gp_result/DEM计算报告.json').read_text(encoding='utf-8'))
    assert report['calculation_status'] == 'PASS'
    assert report['points'][0]['elevation_m'] == pytest.approx(123)
    assert report['points'][0]['slope_deg'] == pytest.approx(0)


def test_dem_packed_scale_and_offset_are_applied_before_vertical_units(tmp_path):
    arcpy = pytest.importorskip('arcpy')
    gdal = pytest.importorskip('osgeo.gdal'); osr = pytest.importorskip('osgeo.osr')
    x, y = 36550000, 2600000
    raster = tmp_path / 'synthetic_packed_dem.tif'
    _raster(gdal, osr, raster, np.full((10, 10), 120), 4524, (x, 10, 0, y + 100, 0, -10))
    dataset = gdal.Open(str(raster), gdal.GA_Update)
    dataset.GetRasterBand(1).SetScale(.5); dataset.GetRasterBand(1).SetOffset(5)
    dataset = None
    points, _ = _features(arcpy, tmp_path, [(x + 45, y + 45)])
    report = D.extract_terrain(str(raster), points, 'POINT_ID', str(tmp_path / 'result'))
    assert report['points'][0]['elevation_m'] == pytest.approx(65)


def test_actual_coordinate_gp_preserves_source_id_and_exports_dem_fields(tmp_path):
    import csv
    arcpy = pytest.importorskip('arcpy')
    points, _ = _features(arcpy, tmp_path, [(36550045, 2600045)])
    for field, length in [('PJDYBH', 15), ('DKBH', 30), ('SAMPLE_ID', 30), ('DESIGN_ST', 20)]:
        arcpy.management.AddField(points, field, 'TEXT', field_length=length)
    for field in ('DEPTH_TOP', 'DEPTH_BOT'):
        arcpy.management.AddField(points, field, 'SHORT')
    for field in ('Z_DEM_M', 'SLOPE_DEG'):
        arcpy.management.AddField(points, field, 'DOUBLE')
    with arcpy.da.UpdateCursor(points, ['DKBH', 'DESIGN_ST', 'DEPTH_TOP', 'DEPTH_BOT', 'Z_DEM_M', 'SLOPE_DEG']) as cursor:
        for _row in cursor:
            cursor.updateRow(['031', 'DESIGN_ONLY', 0, 20, 123, 3.5])
    output = tmp_path / 'coordinates.csv'
    toolbox = arcpy.ImportToolbox(str(ROOT / 'toolboxes/GridSampling.pyt'), 'coordsmoke')
    toolbox.ExportSamplingCoordinates(points, str(output))
    with output.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]['图斑编号'] == '031'
    assert rows[0]['评价单元'] == ''
    assert 100 < float(rows[0]['经度_CGCS2000_度']) < 120
    assert 15 < float(rows[0]['纬度_CGCS2000_度']) < 35
    assert rows[0]['海拔_DEM_m'] == '123.0'
    assert rows[0]['坡度_DEM_度'] == '3.5'


def test_band_unit_conflict_rejects_meters_for_feet_and_accepts_feet(tmp_path):
    arcpy = pytest.importorskip('arcpy')
    gdal = pytest.importorskip('osgeo.gdal'); osr = pytest.importorskip('osgeo.osr')
    x, y = 36550000, 2600000
    raster = tmp_path / 'synthetic_feet_dem.tif'
    _raster(gdal, osr, raster, np.full((10, 10), 123), 4524, (x, 10, 0, y + 100, 0, -10))
    dataset = gdal.Open(str(raster), gdal.GA_Update)
    dataset.GetRasterBand(1).SetUnitType('ft'); dataset = None
    points, _ = _features(arcpy, tmp_path, [(x + 45, y + 45)])
    with pytest.raises(ValueError, match='高程单位.*不一致'):
        D.extract_terrain(str(raster), points, 'POINT_ID', str(tmp_path / 'wrong'), vertical_unit='米')
    assert not (tmp_path / 'wrong').exists()
    result = D.extract_terrain(str(raster), points, 'POINT_ID', str(tmp_path / 'correct'), vertical_unit='英尺')
    assert result['points'][0]['elevation_m'] == pytest.approx(123 * .3048, abs=.001)
