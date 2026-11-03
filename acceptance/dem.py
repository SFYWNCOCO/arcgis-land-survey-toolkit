"""DEM-derived elevation and slope; source identifiers and unknown values are preserved.

Uses GDAL and NumPy, without a Spatial Analyst extension. DEM elevations are not
field observations and an entered datum name is not proof of a vertical datum.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
from pathlib import Path

import numpy as np


def vertical_factor(unit):
    factors = {'米': 1.0, 'm': 1.0, 'metre': 1.0, 'meter': 1.0, 'metres': 1.0, 'meters': 1.0,
               '英尺': .3048, 'ft': .3048, 'foot': .3048, 'feet': .3048}
    key = str(unit or '').strip().lower()
    if key not in factors:
        raise ValueError('高程单位须明确选择米或英尺；不能从影像颜色推断高程')
    return factors[key]


def slope_degrees(elevation, cell_width_m, cell_height_m, z_to_m=1.0, nodata=None):
    """Horn 3×3 slope in degrees; edges and incomplete neighborhoods remain NaN."""
    dx, dy, scale = float(cell_width_m), float(cell_height_m), float(z_to_m)
    if not all(math.isfinite(v) and v > 0 for v in (dx, dy, scale)):
        raise ValueError('坡度计算的水平格距和垂直单位换算必须为正数')
    values = np.asarray(elevation, dtype=np.float64).copy()
    if values.ndim != 2:
        raise ValueError('DEM必须为二维单波段高程栅格')
    if nodata is not None:
        values[np.isclose(values, float(nodata), rtol=1e-7, atol=0)] = np.nan
    values *= scale
    result = np.full(values.shape, np.nan)
    if min(values.shape) < 3:
        return result
    a, b, c = values[:-2, :-2], values[:-2, 1:-1], values[:-2, 2:]
    d, e, f = values[1:-1, :-2], values[1:-1, 1:-1], values[1:-1, 2:]
    g, h, i = values[2:, :-2], values[2:, 1:-1], values[2:, 2:]
    valid = np.logical_and.reduce([np.isfinite(v) for v in (a, b, c, d, e, f, g, h, i)])
    gradient_x = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    gradient_y = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)
    interior = np.degrees(np.arctan(np.hypot(gradient_x, gradient_y)))
    result[1:-1, 1:-1] = np.where(valid, interior, np.nan)
    return result


def sample_bilinear(values, transform, x, y):
    """Sample continuous terrain using pixel centers; missing contributors stay unknown."""
    if transform[2] != 0 or transform[4] != 0:
        raise ValueError('请先把旋转栅格重投影为北向上的工作栅格')
    col, row = (x - transform[0]) / transform[1] - .5, (y - transform[3]) / transform[5] - .5
    c0, r0 = math.floor(col), math.floor(row)
    dc, dr = col - c0, row - r0
    result = 0.0
    for rr, cc, weight in ((r0, c0, (1-dr)*(1-dc)), (r0, c0+1, (1-dr)*dc),
                           (r0+1, c0, dr*(1-dc)), (r0+1, c0+1, dr*dc)):
        if weight <= 1e-12:
            continue
        if not (0 <= rr < values.shape[0] and 0 <= cc < values.shape[1]) or not np.isfinite(values[rr, cc]):
            return None
        result += float(values[rr, cc]) * weight
    return float(result)


def _records(arcpy, source, id_field, shape_type):
    description = arcpy.Describe(source)
    if str(description.shapeType).lower() != shape_type.lower():
        raise ValueError('输入必须为{}要素：{}'.format(shape_type, source))
    fields = {f.name.lower(): f for f in arcpy.ListFields(source)}
    field = fields.get(str(id_field).lower())
    if field is None or field.type in ('OID', 'Geometry'):
        raise ValueError('请选稳定的原始编号字段，不使用对象OID：' + str(id_field))
    records, seen = [], set()
    with arcpy.da.SearchCursor(source, [field.name, 'SHAPE@']) as cursor:
        for identifier, geometry in cursor:
            text = '' if identifier is None else str(identifier).strip()
            if not text or text in seen or geometry is None:
                raise ValueError('编号为空、重复或几何为空；请先核对原始编号字段：' + field.name)
            seen.add(text)
            records.append((text, geometry))
    if not records:
        raise ValueError('输入没有可计算要素：' + str(source))
    return records, field.name, description.spatialReference


def _write_raster(gdal, path, values, transform, projection):
    raster = gdal.GetDriverByName('GTiff').Create(str(path), values.shape[1], values.shape[0],
        1, gdal.GDT_Float32, options=['COMPRESS=LZW', 'TILED=YES'])
    raster.SetGeoTransform(transform); raster.SetProjection(projection)
    band = raster.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    band.WriteArray(np.where(np.isfinite(values), values, -9999))
    raster.FlushCache(); raster = None


def _copy_attributes(arcpy, source, gdb, name, id_field, records, fields):
    output = str(gdb / name)
    arcpy.management.CopyFeatures(source, output)
    existing = {field.name for field in arcpy.ListFields(output)}
    for field, alias, _key in fields:
        if field in existing:
            arcpy.management.DeleteField(output, field)
        arcpy.management.AddField(output, field, 'DOUBLE', field_alias=alias, field_is_nullable='NULLABLE')
    by_id = {row['point_id'] if 'point_id' in row else row['unit_id']: row for row in records}
    with arcpy.da.UpdateCursor(output, [id_field] + [field for field, _alias, _key in fields]) as cursor:
        for row in cursor:
            item = by_id[str(row[0]).strip()]
            cursor.updateRow([row[0]] + [item[key] for _field, _alias, key in fields])
    return output


def extract_terrain(dem_path, point_fc, point_id_field, out_dir, unit_fc=None,
                    unit_id_field=None, vertical_unit='米', vertical_reference='',
                    max_pixels=10_000_000):
    """Create terrain rasters, point/unit tables and new feature copies in an empty folder."""
    import arcpy
    from osgeo import gdal, ogr, osr
    from .sampling import _projected_meters_per_unit

    factor = vertical_factor(vertical_unit)
    points, point_id_field, spatial = _records(arcpy, point_fc, point_id_field, 'Point')
    if spatial.type != 'Projected' or abs(_projected_meters_per_unit(spatial) - 1.0) > 1e-9:
        raise ValueError('设计点须使用已确认的米制投影坐标系；请先明确转换，不能把经纬度当米计算坡度')
    working_srs = osr.SpatialReference()
    if int(spatial.factoryCode or 0) > 0:
        working_srs.ImportFromEPSG(int(spatial.factoryCode))
    elif working_srs.ImportFromWkt(spatial.exportToString().split(';')[0]) != 0:
        raise ValueError('无法识别设计点的米制工作投影')
    working_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    units = []
    if unit_fc:
        if not unit_id_field:
            raise ValueError('提供面图层时须选择原始面编号字段')
        rows, unit_id_field, unit_spatial = _records(arcpy, unit_fc, unit_id_field, 'Polygon')
        if unit_spatial.name == 'Unknown':
            raise ValueError('面图层坐标系未知')
        units = [(identifier, geometry.projectAs(spatial)) for identifier, geometry in rows]
    catalog = str(getattr(arcpy.Describe(dem_path), 'catalogPath', dem_path))
    source = gdal.Open(catalog)
    if source is None or source.RasterCount != 1:
        raise ValueError('DEM须为可读取的单波段高程栅格；RGB卫星影像不能作为DEM')
    band_unit = source.GetRasterBand(1).GetUnitType() or ''
    try:
        metadata_factor = vertical_factor(band_unit)
    except ValueError:
        metadata_factor = None
    if metadata_factor is not None and not math.isclose(metadata_factor, factor, rel_tol=0, abs_tol=1e-12):
        raise ValueError('DEM高程单位与波段元数据不一致：源数据为{}，所选为{}；请核实后重新选择'.format(
            band_unit, vertical_unit))
    band_scale = source.GetRasterBand(1).GetScale()
    band_offset = source.GetRasterBand(1).GetOffset()
    band_scale = float(1.0 if band_scale is None else band_scale)
    band_offset = float(0.0 if band_offset is None else band_offset)
    if not math.isfinite(band_scale) or not math.isfinite(band_offset) or band_scale == 0:
        raise ValueError('DEM波段缩放或偏移元数据无效')
    projection = source.GetProjection()
    source_srs = osr.SpatialReference()
    if not projection or source_srs.ImportFromWkt(projection) != 0:
        raise ValueError('DEM缺少可识别的水平坐标系')
    # ArcGIS rasters and EPSG northing/easting axis declarations can otherwise
    # disagree inside GDAL. Use explicit GIS X/Y WKT on both sides of the warp.
    source_warp_wkt = source_srs.ExportToWkt(['FORMAT=WKT1_ESRI'])
    working_warp_wkt = working_srs.ExportToWkt(['FORMAT=WKT1_ESRI'])
    mask_spatial = osr.SpatialReference()
    mask_spatial.ImportFromWkt(working_warp_wkt)
    normalized_source = osr.SpatialReference()
    normalized_source.ImportFromWkt(source_warp_wkt)
    suggested = gdal.AutoCreateWarpedVRT(source, source_warp_wkt, working_warp_wkt, gdal.GRA_Bilinear)
    suggested_gt = suggested.GetGeoTransform()
    resolution = max(abs(suggested_gt[1]), abs(suggested_gt[5]))
    suggested = None
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError('不能确认DEM米制工作像元大小')
    geometries = [geometry for _identifier, geometry in points + units]
    bounds = (min(g.extent.XMin for g in geometries) - resolution * 3,
              min(g.extent.YMin for g in geometries) - resolution * 3,
              max(g.extent.XMax for g in geometries) + resolution * 3,
              max(g.extent.YMax for g in geometries) + resolution * 3)
    pixels = math.ceil((bounds[2]-bounds[0])/resolution) * math.ceil((bounds[3]-bounds[1])/resolution)
    if pixels > int(max_pixels):
        raise ValueError('工作范围超过内存计算上限；请按地块分批处理，不能静默降低DEM分辨率')
    out = Path(out_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('DEM成果目录必须为空，不覆盖已有文件：' + str(out))
    out.mkdir(parents=True, exist_ok=True)
    warped = gdal.Warp('', source, format='MEM', srcSRS=source_warp_wkt, dstSRS=working_warp_wkt,
        outputBounds=bounds, xRes=resolution, yRes=resolution, targetAlignedPixels=True,
        resampleAlg='bilinear', dstNodata=-1e30, outputType=gdal.GDT_Float64)
    if warped is None:
        raise RuntimeError('DEM重投影失败')
    transform = warped.GetGeoTransform()
    elevation = warped.ReadAsArray().astype(np.float64)
    elevation[np.isclose(elevation, -1e30, rtol=1e-7, atol=0)] = np.nan
    elevation = (elevation * band_scale + band_offset) * factor
    slope = slope_degrees(elevation, abs(transform[1]), abs(transform[5]))
    assert np.all((slope[np.isfinite(slope)] >= 0) & (slope[np.isfinite(slope)] <= 90))
    elevation_path, slope_path = out / 'DEM_米制高程.tif', out / '坡度_度.tif'
    _write_raster(gdal, elevation_path, elevation, transform, working_warp_wkt)
    _write_raster(gdal, slope_path, slope, transform, working_warp_wkt)
    point_rows = []
    for identifier, geometry in points:
        point = geometry.firstPoint
        z = sample_bilinear(elevation, transform, point.X, point.Y)
        gradient = sample_bilinear(slope, transform, point.X, point.Y)
        point_rows.append(dict(point_id=identifier, elevation_m=z, slope_deg=gradient,
            status='DEM_DERIVED' if z is not None and gradient is not None else 'NO_DATA'))
    unit_rows = []
    for identifier, geometry in units:
        vector = ogr.GetDriverByName('Memory').CreateDataSource('unit')
        layer = vector.CreateLayer('unit', mask_spatial, ogr.wkbPolygon)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkb(bytes(geometry.WKB))); layer.CreateFeature(feature)
        mask_raster = gdal.GetDriverByName('MEM').Create('', elevation.shape[1], elevation.shape[0], 1, gdal.GDT_Byte)
        mask_raster.SetGeoTransform(transform); mask_raster.SetProjection(working_warp_wkt)
        mask_raster.GetRasterBand(1).Fill(0); gdal.RasterizeLayer(mask_raster, [1], layer, burn_values=[1])
        mask = mask_raster.ReadAsArray() == 1
        total = int(mask.sum()); valid_z = mask & np.isfinite(elevation); valid_slope = mask & np.isfinite(slope)
        unit_rows.append(dict(unit_id=identifier, mean_elevation_m=float(elevation[valid_z].mean()) if valid_z.any() else None,
            min_elevation_m=float(elevation[valid_z].min()) if valid_z.any() else None,
            max_elevation_m=float(elevation[valid_z].max()) if valid_z.any() else None,
            mean_slope_deg=float(slope[valid_slope].mean()) if valid_slope.any() else None,
            elevation_coverage=float(valid_z.sum()/total) if total else None,
            slope_coverage=float(valid_slope.sum()/total) if total else None,
            polygon_cell_count=total, status='DEM_DERIVED' if total and valid_z.sum()==total and valid_slope.sum()==total else 'NO_DATA_OR_PARTIAL'))
        mask_raster = None; vector = None
    arcpy.management.CreateFileGDB(str(out), 'DEM成果.gdb')
    gdb = out / 'DEM成果.gdb'
    point_output = _copy_attributes(arcpy, point_fc, gdb, '点位_DEM', point_id_field, point_rows,
        [('Z_DEM_M', 'DEM高程_米', 'elevation_m'), ('SLOPE_DEG', 'DEM坡度_度', 'slope_deg')])
    unit_output = None
    if unit_fc:
        unit_output = _copy_attributes(arcpy, unit_fc, gdb, '面统计_DEM', unit_id_field, unit_rows,
            [('DEM_HB_M', 'DEM平均高程_米', 'mean_elevation_m'), ('DEM_PD_D', 'DEM平均坡度_度', 'mean_slope_deg')])
    point_csv, unit_csv = out / '点位高程坡度.csv', out / '面平均高程坡度.csv'
    with point_csv.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['点位编号', 'DEM高程_m', 'DEM坡度_度', '数据状态', '高程基准'])
        writer.writerows([row['point_id'], row['elevation_m'], row['slope_deg'], row['status'], vertical_reference or '未提供/未核实'] for row in point_rows)
    if units:
        with unit_csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream); writer.writerow(['原始面编号', 'DEM平均高程_m', 'DEM平均坡度_度', '高程有效覆盖率', '坡度有效覆盖率', '数据状态'])
            writer.writerows([r['unit_id'], r['mean_elevation_m'], r['mean_slope_deg'], r['elevation_coverage'], r['slope_coverage'], r['status']] for r in unit_rows)
    report_path = out / 'DEM计算报告.json'
    source_nodata = source.GetRasterBand(1).GetNoDataValue()
    if source_nodata is not None and not math.isfinite(float(source_nodata)):
        source_nodata = str(source_nodata)
    result = dict(status='NEEDS_REVIEW', calculation_status='PASS', generated_at=datetime.datetime.now().isoformat(timespec='seconds'),
        source_dem=catalog, source_points=str(point_fc), source_units=str(unit_fc) if unit_fc else None,
        vertical_unit=str(vertical_unit), vertical_to_m=factor, vertical_reference=vertical_reference or None,
        vertical_unit_status='MATCHES_BAND_METADATA' if metadata_factor is not None else 'USER_SELECTED_UNVERIFIED',
        vertical_reference_status='USER_PROVIDED_UNVERIFIED' if vertical_reference else 'UNVERIFIED',
        dem=dict(source_crs=source_srs.GetName(), working_crs=spatial.name,
            reprojected=not bool(normalized_source.IsSame(mask_spatial)), source_geotransform=list(source.GetGeoTransform()),
            source_band_unit=band_unit or None,
            source_band_scale=band_scale, source_band_offset=band_offset,
            source_nodata=source_nodata, working_nodata=-9999,
            valid_elevation_pixels=int(np.isfinite(elevation).sum()), valid_slope_pixels=int(np.isfinite(slope).sum()),
            working_resolution_m=[abs(transform[1]), abs(transform[5])], width=elevation.shape[1], height=elevation.shape[0],
            resampling='bilinear', working_elevation_raster=str(elevation_path), slope_raster=str(slope_path)),
        methods=dict(slope='Horn 3×3，水平米/垂直米，单位度；边缘或邻域NoData留空', point='像元中心双线性插值；缺少贡献像元留空',
                     polygon='像元中心落入面后的有效像元均值；未覆盖像元不计，覆盖率另报',
                     vertical_reference='记录源DEM基准说明；不执行垂直基准转换，填写1985不会把其他基准高程转换为1985'),
        points=point_rows, units=unit_rows, point_output=point_output, unit_output=unit_output,
        point_coverage=dict(total=len(point_rows), elevation_valid=sum(r['elevation_m'] is not None for r in point_rows),
                            slope_valid=sum(r['slope_deg'] is not None for r in point_rows)),
        point_csv=str(point_csv), unit_csv=str(unit_csv) if units else None, report_path=str(report_path),
        review=['DEM结果是栅格推算值，不能冒充实测高程或现场坡度', '源DEM时相、精度与高程基准须核实；填写基准名称不等于已验证',
                '小于像元尺度的田坎/小地块可能不能可靠表达；无效值保留空白，未填0'])
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    source = None; warped = None
    return result
