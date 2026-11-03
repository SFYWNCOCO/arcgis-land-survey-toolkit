"""按地块范围读取栅格有效掩膜；低纹理只列疑点，不判定地物真实性。"""
from __future__ import annotations

import math


def inspect_raster_coverage(path, geometry_wkt, max_pixels=4000000):
    """geometry_wkt 必须已投影到影像坐标系。大图降采样时明确标为待核。"""
    from osgeo import gdal, ogr, osr
    import numpy as np
    result = {"path": path, "coverage_status": "FAIL", "status": "FAIL", "texture_status": "NEEDS_REVIEW"}
    try:
        dataset = gdal.Open(str(path))
        if dataset is None:
            raise ValueError("栅格无法打开")
        geo = dataset.GetGeoTransform()
        if geo[2] or geo[4] or not geo[1] or not geo[5]:
            raise ValueError("旋转或无有效像元网格，须先生成正射工作副本")
        projection = dataset.GetProjection()
        if not projection:
            raise ValueError("影像缺少坐标参考")
        geometry = ogr.CreateGeometryFromWkt(geometry_wkt)
        if geometry is None or geometry.IsEmpty():
            raise ValueError("范围几何为空")
        xmin, xmax, ymin, ymax = geometry.GetEnvelope()
        xres, yres = abs(geo[1]), abs(geo[5])
        xmin = geo[0] + math.floor((xmin - geo[0]) / xres) * xres
        xmax = geo[0] + math.ceil((xmax - geo[0]) / xres) * xres
        ymin = geo[3] + math.floor((ymin - geo[3]) / yres) * yres
        ymax = geo[3] + math.ceil((ymax - geo[3]) / yres) * yres
        width, height = max(1, round((xmax - xmin) / xres)), max(1, round((ymax - ymin) / yres))
        factor = max(1.0, math.sqrt(width * height / float(max_pixels)))
        sampled = factor > 1
        width, height = max(1, int(width / factor)), max(1, int(height / factor))
        warped = gdal.Warp("", dataset, format="MEM", outputBounds=[xmin, ymin, xmax, ymax],
                           width=width, height=height, dstSRS=projection, dstAlpha=True,
                           resampleAlg="near", warpOptions=["UNIFIED_SRC_NODATA=YES"])
        if warped is None:
            raise ValueError("栅格覆盖窗口无法解码")
        sr = osr.SpatialReference()
        sr.ImportFromWkt(projection)
        vector = ogr.GetDriverByName("Memory").CreateDataSource("")
        layer = vector.CreateLayer("aoi", sr, ogr.wkbUnknown)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        mask_ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
        mask_ds.SetGeoTransform(warped.GetGeoTransform())
        mask_ds.SetProjection(projection)
        gdal.RasterizeLayer(mask_ds, [1], layer, burn_values=[1])
        mask = mask_ds.ReadAsArray().astype(bool)
        total = int(mask.sum())
        if not total:
            raise ValueError("范围小于一个检查像元；需以更细窗口人工复核")
        valid = warped.GetRasterBand(warped.RasterCount).ReadAsArray() > 0
        good = mask & valid
        missing = int((mask & ~valid).sum())
        result.update({"coverage_status": "FAIL" if missing else "NEEDS_REVIEW" if sampled else "PASS",
                       "checked_pixels": total, "missing_pixels": missing,
                       "coverage_ratio": round(float(good.sum()) / total, 8),
                       "sampled": sampled, "sampling_factor": factor,
                       "method": "逐像元有效掩膜" if not sampled else "降采样有效掩膜，须复核原始像元",
                       "window_dimensions": [width, height]})
        channels = [warped.GetRasterBand(index).ReadAsArray()[good][::max(1, int(good.sum()) // 100000)]
                    for index in range(1, min(3, warped.RasterCount - 1) + 1)]
        if channels and channels[0].size:
            values = np.stack(channels).astype("float32")
            texture = float(np.mean(np.std(values, axis=1)))
            black = float(np.all(values <= 5, axis=0).mean())
            white = float(np.all(values >= 250, axis=0).mean()) if dataset.GetRasterBand(1).DataType == gdal.GDT_Byte else None
            suspect = texture < 6 or black > 0.25 or (white is not None and white > 0.25)
            result.update({"texture_status": "NEEDS_REVIEW" if suspect else "PASS", "spatial_std": texture,
                           "black_fraction": black, "white_fraction": white,
                           "texture_note": "黑白/低纹理疑点，保留影像并人工核实" if suspect else "未发现明显低纹理疑点"})
        result["status"] = "FAIL" if result["coverage_status"] == "FAIL" else "NEEDS_REVIEW" if (
            result["coverage_status"] == "NEEDS_REVIEW" or result["texture_status"] == "NEEDS_REVIEW") else "PASS"
    except Exception as exc:
        result["detail"] = "影像覆盖/解码检查失败：{}".format(exc)
    return result
