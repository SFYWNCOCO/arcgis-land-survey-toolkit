"""Conservative, auditable NDVI time-series screening for acceptance projects.

Outputs are screening evidence only. They do not classify land use or decide
soil quality/acceptance, and they must be checked against field observations.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import re
import uuid


SCL_INVALID_CLASSES = (0, 1, 2, 3, 7, 8, 9, 10, 11)
SCL_CLASS_NAMES = {
    0: "无数据", 1: "饱和或缺陷像元", 2: "地形/投射阴影",
    3: "云影", 7: "未分类", 8: "中概率云", 9: "高概率云",
    10: "卷云", 11: "雪或冰",
}


def _crs_signature(sr):
    """Stable CRS identity: prefer authoritative WKID, otherwise use normalized WKT."""
    if sr is None:
        return None
    try:
        wkid = int(getattr(sr, "factoryCode", 0) or 0)
    except (TypeError, ValueError):
        wkid = 0
    if wkid > 0:
        return ("WKID", wkid)
    try:
        wkt = str(sr.exportToString() or "")
    except Exception:
        return None
    name = str(getattr(sr, "name", "Unknown") or "Unknown")
    if not wkt.strip() or name.casefold() == "unknown":
        return None
    return ("WKT", re.sub(r"\s+", "", wkt).casefold())


def _raster_info(arcpy, path):
    if not arcpy.Exists(path):
        raise ValueError("栅格不存在：{}".format(path))
    raster = arcpy.Raster(path)
    desc = arcpy.Describe(raster)
    sr = getattr(desc, "spatialReference", None)
    crs_signature = _crs_signature(sr)
    if crs_signature is None:
        raise ValueError("栅格坐标系未知或缺少可用WKT定义：{}".format(path))
    if int(getattr(raster, "bandCount", 1)) != 1:
        raise ValueError("请在 ArcGIS Pro 中先选取单独红光/NIR 波段：{}".format(path))
    cell_x, cell_y = float(raster.meanCellWidth), float(raster.meanCellHeight)
    if not (cell_x > 0 and cell_y > 0 and math.isfinite(cell_x) and math.isfinite(cell_y)):
        raise ValueError("栅格像元大小无效：{}".format(path))
    extent = raster.extent
    return {
        "path": os.path.abspath(path), "raster": raster, "sr": sr,
        "wkid": int(getattr(sr, "factoryCode", 0) or 0), "crs_signature": crs_signature,
        "crs_name": str(getattr(sr, "name", "Unknown") or "Unknown"),
        "crs_wkt": sr.exportToString(), "cell_x": cell_x, "cell_y": cell_y,
        "extent": (float(extent.XMin), float(extent.YMin), float(extent.XMax), float(extent.YMax)),
        "width": int(raster.width), "height": int(raster.height),
        "nodata": getattr(raster, "noDataValue", None),
        "pixel_type": str(getattr(raster, "pixelType", "unknown")),
    }


def _same_grid(infos):
    base = infos[0]
    tol = max(base["cell_x"], base["cell_y"]) * 1e-7
    for info in infos[1:]:
        if info["crs_signature"] != base["crs_signature"]:
            raise ValueError("四个波段坐标系定义不一致；请先依据项目资料完成投影，工具不会猜测转换。")
        if info["width"] != base["width"] or info["height"] != base["height"]:
            raise ValueError("四个波段行列数不一致；请先统一像元网格。")
        if any(abs(a - b) > tol for a, b in zip(info["extent"], base["extent"])):
            raise ValueError("四个波段范围/网格未对齐；请先重采样到同一网格。")
        if abs(info["cell_x"] - base["cell_x"]) > tol or abs(info["cell_y"] - base["cell_y"]) > tol:
            raise ValueError("四个波段像元大小不一致；请先统一像元网格。")


def _read_array(arcpy, info):
    import numpy as np
    raster = info["raster"]
    array = arcpy.RasterToNumPyArray(
        raster, raster.extent.lowerLeft, info["width"], info["height"], -9999.0
    ).astype("float64", copy=False)
    nodata = info["nodata"]
    if nodata is not None:
        try:
            nd = float(nodata)
            if math.isfinite(nd):
                array[array == nd] = np.nan
        except (TypeError, ValueError):
            pass
    array[array == -9999.0] = np.nan
    return array


def _ndvi(red, nir, scale, offset):
    import numpy as np
    red = red * scale + offset
    nir = nir * scale + offset
    denom = nir + red
    valid = np.isfinite(red) & np.isfinite(nir) & np.isfinite(denom) & (np.abs(denom) > 1e-12)
    result = np.full(red.shape, np.nan, dtype="float32")
    result[valid] = (nir[valid] - red[valid]) / denom[valid]
    result[(result < -1.00001) | (result > 1.00001)] = np.nan
    return result


def _scl_valid_mask(scl, nodata=0):
    """Return valid Sentinel-2 pixels, excluding no-data, defects, shadows, clouds and snow."""
    import numpy as np

    valid = np.isfinite(scl)
    if nodata is not None:
        try:
            valid &= scl != float(nodata)
        except (TypeError, ValueError):
            pass
    for code in SCL_INVALID_CLASSES:
        valid &= scl != code
    return valid


def _mask_outside_evaluation_units(values, unit_ids):
    """Return a copy with pixels outside all rasterized evaluation units masked."""
    import numpy as np

    if values.shape != unit_ids.shape:
        raise ValueError("评价单元栅格与遥感数组大小不一致；已停止统计。")
    result = values.copy()
    result[unit_ids <= 0] = np.nan
    return result


def _scl_area_counts(scl, evaluation_mask):
    """Count SCL NoData separately from classified pixels inside evaluation units."""
    import numpy as np

    if scl.shape != evaluation_mask.shape:
        raise ValueError("SCL栅格与评价单元栅格大小不一致；已停止统计。")
    finite = np.isfinite(scl)
    values, counts = np.unique(scl[evaluation_mask & finite], return_counts=True)
    return {
        "nodata_pixels": int((evaluation_mask & ~finite).sum()),
        "class_counts": {str(int(value)): int(count) for value, count in zip(values, counts)},
    }


def _align_scl_to_grid(arcpy, scl_path, ref_info, out_path):
    """Nearest-neighbor align a same-CRS categorical SCL raster to a 10 m band grid."""
    from osgeo import gdal

    info = _raster_info(arcpy, scl_path)
    if info["crs_signature"] != ref_info["crs_signature"]:
        raise ValueError("SCL与红光/NIR坐标系不同；请先按项目资料明确投影与基准转换。")
    xmin, ymin, xmax, ymax = ref_info["extent"]
    options = gdal.WarpOptions(
        format="GTiff", outputBounds=(xmin, ymin, xmax, ymax),
        width=ref_info["width"], height=ref_info["height"],
        dstSRS=ref_info["sr"].exportToString(),
        resampleAlg=gdal.GRA_NearestNeighbour,
        srcNodata=info["nodata"], dstNodata=0, outputType=gdal.GDT_Byte,
    )
    aligned = gdal.Warp(out_path, scl_path, options=options)
    if aligned is None:
        raise ValueError("SCL无法按红光/NIR网格对齐：{}".format(scl_path))
    aligned = None
    result = _raster_info(arcpy, out_path)
    _same_grid([ref_info, result])
    return result


def _save_float_raster(arcpy, np, array, ref_info, path):
    raster_array = np.where(np.isfinite(array), array, -9999.0).astype("float32")
    ll = arcpy.Point(ref_info["extent"][0], ref_info["extent"][1])
    out = arcpy.NumPyArrayToRaster(
        raster_array, ll, ref_info["cell_x"], ref_info["cell_y"], -9999.0
    )
    out.save(path)
    arcpy.management.DefineProjection(path, ref_info["sr"])


def _unit_raster(arcpy, units, unit_field, ref_info):
    if not arcpy.Exists(units):
        raise ValueError("评价单元面不存在：{}".format(units))
    desc = arcpy.Describe(units)
    if getattr(desc, "shapeType", "").lower() != "polygon":
        raise ValueError("评价单元输入必须为面要素类。")
    sr = getattr(desc, "spatialReference", None)
    if sr is None or _crs_signature(sr) != ref_info["crs_signature"]:
        raise ValueError("评价单元与影像坐标系不一致或未知；请先按项目资料统一坐标系。")
    fields = {f.name.lower(): f.name for f in arcpy.ListFields(units)}
    if unit_field.lower() not in fields:
        raise ValueError("评价单元编号字段不存在：{}".format(unit_field))
    actual_field = fields[unit_field.lower()]
    token = uuid.uuid4().hex[:8]
    copy_fc = "in_memory/RS_Units_{}".format(token)
    out_raster = "in_memory/RS_UnitRaster_{}".format(token)
    try:
        arcpy.management.CopyFeatures(units, copy_fc)
        copied_fields = {f.name.lower() for f in arcpy.ListFields(copy_fc)}
        out_field = "SCQA_RSID"
        suffix = 1
        while out_field.lower() in copied_fields:
            out_field = "SCQA_R{}".format(suffix)
            suffix += 1
        arcpy.management.AddField(copy_fc, out_field, "LONG")
        labels, label_to_code = [], {}
        with arcpy.da.UpdateCursor(copy_fc, [actual_field, out_field]) as cursor:
            for row in cursor:
                if row[0] is None or str(row[0]).strip() == "":
                    raise ValueError("评价单元编号存在空值。")
                label = str(row[0])
                if label not in label_to_code:
                    label_to_code[label] = len(labels) + 1
                    labels.append(label)
                row[1] = label_to_code[label]
                cursor.updateRow(row)
        with arcpy.EnvManager(snapRaster=ref_info["raster"], cellSize=ref_info["raster"],
                              extent=ref_info["raster"], outputCoordinateSystem=ref_info["sr"]):
            arcpy.conversion.FeatureToRaster(copy_fc, out_field, out_raster, ref_info["cell_x"])
        return out_raster, labels
    finally:
        if arcpy.Exists(copy_fc):
            arcpy.management.Delete(copy_fc)


def run_ndvi_screening(red_before, nir_before, red_after, nir_after, units,
                       unit_field, out_folder, before_date, after_date,
                       before_source, after_source, before_scale, before_offset,
                       after_scale, after_offset, cloud_mask_confirmed,
                       scl_before=None, scl_after=None):
    """Create aligned NDVI rasters, change raster, per-unit CSV and evidence JSON."""
    import arcpy
    import numpy as np

    if not os.path.isdir(out_folder):
        raise ValueError("输出目录不存在：{}".format(out_folder))
    try:
        d0 = dt.date.fromisoformat(before_date)
        d1 = dt.date.fromisoformat(after_date)
    except ValueError:
        raise ValueError("影像日期必须使用 YYYY-MM-DD 格式。")
    if d1 <= d0:
        raise ValueError("后期影像日期必须晚于前期影像日期。")
    has_scl_before, has_scl_after = bool(scl_before), bool(scl_after)
    if has_scl_before != has_scl_after:
        raise ValueError("请同时提供前后期SCL分类栅格，或两期都不提供。")
    if not has_scl_before and not cloud_mask_confirmed:
        raise ValueError("请先对两期影像完成云/阴影掩膜，或确认产品为无云影像，再运行时相筛查。")
    scales = [float(before_scale), float(before_offset), float(after_scale), float(after_offset)]
    if any(not math.isfinite(v) for v in scales) or scales[0] <= 0 or scales[2] <= 0:
        raise ValueError("反射率比例因子/偏移量无效；请按影像产品元数据填写。")

    inputs = [red_before, nir_before, red_after, nir_after]
    infos = [_raster_info(arcpy, p) for p in inputs]
    _same_grid(infos)
    uid = uuid.uuid4().hex[:8]
    base = "RS_NDVI_{}_{}_{}".format(d0.strftime("%Y%m%d"), d1.strftime("%Y%m%d"), uid)
    paths = {
        "before": os.path.join(out_folder, base + "_before.tif"),
        "after": os.path.join(out_folder, base + "_after.tif"),
        "delta": os.path.join(out_folder, base + "_delta.tif"),
        "csv": os.path.join(out_folder, base + "_by_unit.csv"),
        "json": os.path.join(out_folder, base + "_evidence.json"),
        "scl_before_aligned": os.path.join(out_folder, base + "_scl_before_aligned.tif"),
        "scl_after_aligned": os.path.join(out_folder, base + "_scl_after_aligned.tif"),
    }
    if any(os.path.exists(p) for p in paths.values()):
        raise ValueError("输出名冲突；请重新运行。")

    unit_info = None
    unit_raster = None
    try:
        arrays = [_read_array(arcpy, info) for info in infos]
        ndvi0 = _ndvi(arrays[0], arrays[1], scales[0], scales[1])
        ndvi1 = _ndvi(arrays[2], arrays[3], scales[2], scales[3])
        scl_summary = {"applied": False, "method": "operator_confirmed_pre-masked_input" if cloud_mask_confirmed else "no_cloud_mask_evidence"}
        if has_scl_before:
            scl_infos = [
                _align_scl_to_grid(arcpy, scl_before, infos[0], paths["scl_before_aligned"]),
                _align_scl_to_grid(arcpy, scl_after, infos[0], paths["scl_after_aligned"]),
            ]
            scl_arrays = [_read_array(arcpy, info) for info in scl_infos]
            valid_before = _scl_valid_mask(scl_arrays[0], scl_infos[0]["nodata"])
            valid_after = _scl_valid_mask(scl_arrays[1], scl_infos[1]["nodata"])
            ndvi0[~valid_before] = np.nan
            ndvi1[~valid_after] = np.nan
            invalid_codes = SCL_INVALID_CLASSES
            scl_summary = {
                "applied": True,
                "method": "Sentinel-2 SCL nearest-neighbor aligned to 10 m red/NIR grid",
                "invalid_classes": list(invalid_codes),
                "invalid_class_names": {str(code): SCL_CLASS_NAMES[code] for code in invalid_codes},
                "scl_class_reference": "Sentinel-2 L2A SCL；类2为阴影类，具体语义受处理基线影响",
                "before_source": os.path.abspath(scl_before),
                "after_source": os.path.abspath(scl_after),
                "before_excluded_pixels": int((~valid_before).sum()),
                "after_excluded_pixels": int((~valid_after).sum()),
                "before_class_counts": {str(int(k)): int(v) for k, v in zip(*np.unique(scl_arrays[0][np.isfinite(scl_arrays[0])], return_counts=True))},
                "after_class_counts": {str(int(k)): int(v) for k, v in zip(*np.unique(scl_arrays[1][np.isfinite(scl_arrays[1])], return_counts=True))},
            }
        delta = np.where(np.isfinite(ndvi0) & np.isfinite(ndvi1), ndvi1 - ndvi0, np.nan).astype("float32")
        if not np.isfinite(delta).any():
            raise ValueError("影像有效像元为零；请核对 NoData、比例因子和波段。")
        unit_raster, labels = _unit_raster(arcpy, units, unit_field, infos[0])
        unit_info = _raster_info(arcpy, unit_raster)
        unit_ids = arcpy.RasterToNumPyArray(unit_info["raster"], unit_info["raster"].extent.lowerLeft,
                                             unit_info["width"], unit_info["height"], 0)
        unit_info = None
        arcpy.management.Delete(unit_raster)
        unit_raster = None
        if unit_ids.shape != delta.shape:
            raise ValueError("评价单元栅格化后与影像矩阵大小不一致；已停止汇总。")

        evaluation_mask = unit_ids > 0
        ndvi0 = _mask_outside_evaluation_units(ndvi0, unit_ids)
        ndvi1 = _mask_outside_evaluation_units(ndvi1, unit_ids)
        delta = _mask_outside_evaluation_units(delta, unit_ids)
        if has_scl_before:
            scl_summary["before_excluded_pixels"] = int((evaluation_mask & ~valid_before).sum())
            scl_summary["after_excluded_pixels"] = int((evaluation_mask & ~valid_after).sum())
            before_scl_counts = _scl_area_counts(scl_arrays[0], evaluation_mask)
            after_scl_counts = _scl_area_counts(scl_arrays[1], evaluation_mask)
            scl_summary["before_nodata_pixels"] = before_scl_counts["nodata_pixels"]
            scl_summary["after_nodata_pixels"] = after_scl_counts["nodata_pixels"]
            scl_summary["before_class_counts"] = before_scl_counts["class_counts"]
            scl_summary["after_class_counts"] = after_scl_counts["class_counts"]
        _save_float_raster(arcpy, np, ndvi0, infos[0], paths["before"])
        _save_float_raster(arcpy, np, ndvi1, infos[0], paths["after"])
        _save_float_raster(arcpy, np, delta, infos[0], paths["delta"])

        with open(paths["csv"], "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["评价单元编号", "有效像元数", "覆盖像元数", "有效覆盖率", "前期NDVI均值", "后期NDVI均值", "NDVI差值均值", "NDVI差值标准差", "正向像元数", "负向像元数", "前期SCL排除像元数", "后期SCL排除像元数"])
            rows = []
            for code, label in enumerate(labels, 1):
                mask_unit = unit_ids == code
                valid = mask_unit & np.isfinite(delta)
                count, total = int(valid.sum()), int(mask_unit.sum())
                row = {
                    "unit_id": label, "valid_pixels": count, "unit_pixels": total,
                    "valid_coverage": round(count / total, 6) if total else 0.0,
                    "mean_ndvi_before": round(float(np.nanmean(ndvi0[valid])), 6) if count else None,
                    "mean_ndvi_after": round(float(np.nanmean(ndvi1[valid])), 6) if count else None,
                    "mean_ndvi_delta": round(float(np.nanmean(delta[valid])), 6) if count else None,
                    "std_ndvi_delta": round(float(np.nanstd(delta[valid])), 6) if count else None,
                    "positive_delta_pixels": int((valid & (delta > 0)).sum()),
                    "negative_delta_pixels": int((valid & (delta < 0)).sum()),
                }
                if has_scl_before:
                    row["scl_excluded_pixels_before"] = int((mask_unit & ~valid_before).sum())
                    row["scl_excluded_pixels_after"] = int((mask_unit & ~valid_after).sum())
                rows.append(row)
                writer.writerow([row["unit_id"], count, total, row["valid_coverage"], row["mean_ndvi_before"], row["mean_ndvi_after"], row["mean_ndvi_delta"], row["std_ndvi_delta"], row["positive_delta_pixels"], row["negative_delta_pixels"], row.get("scl_excluded_pixels_before", 0), row.get("scl_excluded_pixels_after", 0)])
        finite_delta = delta[np.isfinite(delta)]
        report = {
            "status": "NEEDS_FIELD_REVIEW",
            "screening_only": True,
            "method": "NDVI=(NIR-Red)/(NIR+Red); delta=after-before",
            "source": {"before": before_source, "after": after_source},
            "date": {"before": before_date, "after": after_date},
            "band_semantics": {"red_before": red_before, "nir_before": nir_before, "red_after": red_after, "nir_after": nir_after},
            "reflectance_transform": {"before_scale": scales[0], "before_offset": scales[1], "after_scale": scales[2], "after_offset": scales[3]},
            "cloud_shadow_mask_confirmed_by_operator": bool(cloud_mask_confirmed),
            "cloud_mask": scl_summary,
            "grid": {"wkid": infos[0]["wkid"], "crs_name": infos[0]["crs_name"], "crs_wkt": infos[0]["crs_wkt"], "cell_size_x": infos[0]["cell_x"], "cell_size_y": infos[0]["cell_y"], "width": infos[0]["width"], "height": infos[0]["height"]},
            "delta_summary": {"valid_pixels": int(finite_delta.size), "mean": float(np.mean(finite_delta)), "std": float(np.std(finite_delta)), "minimum": float(np.min(finite_delta)), "maximum": float(np.max(finite_delta))},
            "unit_summary": rows,
            "outputs": {key: paths[key] for key in ("before", "after", "delta", "csv", "json")},
            "review_note": "NDVI变化受季节、物候、云/阴影、作物类型、土壤背景、影像预处理和分辨率影响；本结果仅为线索。应核实影像元数据/云掩膜/大气校正，并结合RTK、现场踏勘和样品记录；不得据此自动认定补充耕地变化、质量等级或验收结论。",
        }
        with open(paths["json"], "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return report
    finally:
        try:
            import gc
            unit_info = None
            if "scl_infos" in locals():
                del scl_infos
            if "scl_arrays" in locals():
                del scl_arrays
            if "valid_before" in locals():
                del valid_before
            if "valid_after" in locals():
                del valid_after
            gc.collect()
            arcpy.management.ClearWorkspaceCache()
        except Exception:
            pass
        for temporary in (paths["scl_before_aligned"], paths["scl_after_aligned"]):
            if os.path.exists(temporary):
                try:
                    arcpy.management.Delete(temporary)
                except Exception:
                    pass
                try:
                    os.remove(temporary)
                except OSError:
                    pass
        if unit_raster and arcpy.Exists(unit_raster):
            try:
                arcpy.management.Delete(unit_raster)
            except Exception as exc:
                try:
                    arcpy.AddWarning("遥感内存临时栅格未能自动删除：{}".format(exc))
                except Exception:
                    pass
