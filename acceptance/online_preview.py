"""按真实图斑预览在线影像；不要求本地 DOM，不写源数据。"""
from pathlib import Path
from . import imagery as I
from .task_state import _atomic_json


def preview_online(source, out_dir, provider='demo', key=None, selection_oids=None,
                   source_where_clause=None, max_zoom=19, arcpy_module=None):
    if arcpy_module is None:
        import arcpy as arcpy_module
    api = arcpy_module
    output = Path(out_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('影像预览目录已有内容，请使用新目录')
    output.mkdir(parents=True, exist_ok=True)
    from .oneclick import _selection_where
    where, _count = _selection_where(api, source, selection_oids)
    if source_where_clause:
        where = '({}) AND ({})'.format(where, source_where_clause) if where else source_where_clause
    fields = {field.name.upper(): field.name for field in api.ListFields(source)}
    id_field = next((fields[name] for name in ('DIKUAI_NO', 'DKBH', 'DKBM', 'BSM') if name in fields), 'OID@')
    with api.da.SearchCursor(source, [id_field, 'SHAPE@'], where_clause=where) as cursor:
        first = next(iter(cursor), None)
    if first is None or first[1] is None:
        raise ValueError('本次处理范围没有可预览图斑')
    parcel_id, geometry = first
    sr = api.Describe(source).spatialReference
    record = I.prepare_imagery(str(output / 'imagery'), sr.factoryCode, extent=geometry.extent,
        provider=provider, tk=key, max_tiles=100, max_zoom=int(max_zoom), arcpy=api, label='online_preview',
        labels=provider == 'tianditu_vector')
    preview = None
    if record.get('output'):
        quality = I.inspect_raster_coverage(record['output'], geometry.WKT)
        record['pixel_qa'] = quality
        record['coverage_status'] = quality['coverage_status']
        record = I._write_source_manifest(str(output / 'imagery'), record)
        from osgeo import gdal
        import numpy as np
        from PIL import Image, ImageDraw
        raster = gdal.Open(record['output'])
        if raster is None:
            raise ValueError('在线影像下载后无法解码')
        width = min(1400, raster.RasterXSize)
        height = max(1, round(width * raster.RasterYSize / raster.RasterXSize))
        array = raster.ReadAsArray(buf_xsize=width, buf_ysize=height)
        transform = raster.GetGeoTransform()
        image = Image.fromarray(np.moveaxis(array[:4], 0, -1).astype('uint8'))
        painter = ImageDraw.Draw(image)
        for part in geometry:
            ring = []
            for point in part:
                if point is None:
                    if len(ring) > 1: painter.line(ring, fill=(255, 30, 30), width=3)
                    ring = []
                else:
                    ring.append(((point.X - transform[0]) / transform[1] * width / raster.RasterXSize,
                        (point.Y - transform[3]) / transform[5] * height / raster.RasterYSize))
            if len(ring) > 1: painter.line(ring + [ring[0]], fill=(255, 30, 30), width=3)
        preview = str(output / '在线影像地块预览.png')
        image.save(preview)
        raster = None
    report_path = str(output / '在线影像预览报告.json')
    result = dict(status='NEEDS_REVIEW', parcel_id=str(parcel_id), source=str(source),
        scope='本次选择/查询范围的第一个图斑；不是项目全范围覆盖结论', imagery=record,
        preview_png=preview, report_path=report_path,
        usable_for_parcel_preview=bool(preview and record.get('coverage_status') == 'PASS'),
        note='真实在线影像预览；可看图定位。服务像元大小不是位置精度，报件适用性仍待核。')
    _atomic_json(report_path, result)
    return result
