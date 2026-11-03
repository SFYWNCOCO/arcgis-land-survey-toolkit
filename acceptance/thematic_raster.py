"""Summarize a single continuous thematic raster by evaluation unit.

Statistics are screening evidence only. This module never reprojects input data
or derives a cultivated-land quality grade.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import uuid
from pathlib import Path

from acceptance.remote_sensing import _crs_signature


def validate_pair_crs(raster_sr, units_sr):
    raster_signature = _crs_signature(raster_sr)
    units_signature = _crs_signature(units_sr)
    if raster_signature is None or units_signature is None:
        raise ValueError("栅格或评价单元坐标系未知/缺少有效定义；已拒绝统计。")
    if raster_signature != units_signature:
        raise ValueError("栅格与评价单元坐标系不一致；需依据元数据/控制资料明确转换后再运行。")
    return raster_signature


def _copy_units_for_raster(arcpy, units, copy_fc, raster_sr, expected_signature,
                           expected_count, copy_extent=None):
    """Copy units with explicit CRS settings, then verify ArcPy's output."""
    environments = {
        "outputCoordinateSystem": raster_sr,
        "geographicTransformations": "",
    }
    if copy_extent is not None:
        environments["extent"] = copy_extent
    with arcpy.EnvManager(**environments):
        arcpy.management.CopyFeatures(units, copy_fc)
    copied_sr = getattr(arcpy.Describe(copy_fc), "spatialReference", None)
    if _crs_signature(copied_sr) != expected_signature:
        raise ValueError("评价单元临时副本坐标系与专题栅格不一致；已停止栅格化。")
    copied_count = int(arcpy.management.GetCount(copy_fc)[0])
    if copied_count != int(expected_count):
        raise ValueError("评价单元临时副本要素数发生变化；已停止栅格化。")
    return copied_count


def _summary_stats(values, nodata):
    import numpy as np

    arr = np.asarray(values)
    finite = np.isfinite(arr)
    if nodata is not None:
        try:
            nd = float(nodata)
        except (TypeError, ValueError):
            nd = None
        if nd is not None:
            finite &= ~np.isnan(arr) if math.isnan(nd) else (arr != nd)
    valid = arr[finite].astype("float64", copy=False)
    total = int(arr.size)
    count = int(valid.size)
    result = {
        "total_pixel_count": total,
        "valid_pixel_count": count,
        "nodata_pixel_count": total - count,
        "valid_pixel_percent": round(count / total * 100.0, 4) if total else 0.0,
        "minimum": None,
        "mean": None,
        "median": None,
        "std": None,
        "maximum": None,
    }
    if count:
        result.update({
            "minimum": float(np.min(valid)),
            "mean": float(np.mean(valid)),
            "median": float(np.median(valid)),
            "std": float(np.std(valid, ddof=0)),
            "maximum": float(np.max(valid)),
        })
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path, payload):
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def summarize_continuous_raster(raster_path, units, unit_field, out_folder,
                                max_cells=5_000_000, arcpy_module=None):
    """Summarize valid single-band pixel values by unique polygon ID.

    Coverage is measured only over cell-center pixels within the raster extent;
    it is not a claim that the entire evaluation-unit polygon is covered.
    """
    import numpy as np

    if arcpy_module is None:
        import arcpy as arcpy_module
    arcpy = arcpy_module
    raster_path = os.path.abspath(os.fspath(raster_path))
    out_folder = Path(out_folder).expanduser().resolve()
    if not arcpy.Exists(raster_path):
        raise ValueError("专题栅格不存在：{}".format(raster_path))
    if not os.path.isfile(raster_path):
        raise ValueError("首版只处理文件型栅格；请先导出 FileGDB/服务图层到本地栅格文件。")
    if not arcpy.Exists(units):
        raise ValueError("评价单元面不存在：{}".format(units))
    if not out_folder.is_dir():
        raise ValueError("输出目录不存在：{}".format(out_folder))
    if int(max_cells) <= 0:
        raise ValueError("像元预算必须为正整数。")

    raster = arcpy.Raster(raster_path)
    raster_desc = arcpy.Describe(raster_path)
    units_desc = arcpy.Describe(units)
    if int(getattr(raster, "bandCount", 1) or 1) != 1:
        raise ValueError("仅支持已确认波段含义的单波段连续栅格。")
    if str(getattr(units_desc, "shapeType", "")).lower() != "polygon":
        raise ValueError("评价单元输入必须为面要素类。")
    raster_sr = getattr(raster_desc, "spatialReference", None) or getattr(raster, "spatialReference", None)
    units_sr = getattr(units_desc, "spatialReference", None)
    crs_signature = validate_pair_crs(raster_sr, units_sr)
    fields = {field.name.casefold(): field for field in arcpy.ListFields(units)}
    actual_field = fields.get(str(unit_field).casefold())
    if actual_field is None:
        raise ValueError("评价单元编号字段不存在：{}".format(unit_field))
    if str(actual_field.type).casefold() not in {"string", "integer", "smallinteger", "double", "single", "oid"}:
        raise ValueError("评价单元编号字段类型不受支持：{}".format(actual_field.type))

    unit_ids = []
    seen = set()
    with arcpy.da.SearchCursor(units, [actual_field.name]) as cursor:
        for row in cursor:
            value = row[0]
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError("评价单元编号存在空值。")
            key = str(value)
            if key in seen:
                raise ValueError("评价单元编号不唯一，无法按单元统计：{}".format(key))
            seen.add(key)
            unit_ids.append(value)
    if not unit_ids:
        raise ValueError("评价单元图层没有要素。")

    cell_x = abs(float(raster.meanCellWidth))
    cell_y = abs(float(raster.meanCellHeight))
    if not (math.isfinite(cell_x) and math.isfinite(cell_y) and cell_x > 0 and cell_y > 0):
        raise ValueError("栅格像元大小无效。")
    if not math.isclose(cell_x, cell_y, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("首版要求方形像元；当前栅格 X/Y 像元大小不同，已拒绝重采样或统计。")
    raster_extent = raster.extent
    unit_extent = units_desc.extent
    xmin = max(float(raster_extent.XMin), float(unit_extent.XMin))
    ymin = max(float(raster_extent.YMin), float(unit_extent.YMin))
    xmax = min(float(raster_extent.XMax), float(unit_extent.XMax))
    ymax = min(float(raster_extent.YMax), float(unit_extent.YMax))
    if not (xmax > xmin and ymax > ymin):
        raise ValueError("评价单元范围与专题栅格没有相交区域。")

    col0 = max(0, int(math.floor((xmin - float(raster_extent.XMin)) / cell_x)))
    col1 = min(int(raster.width), int(math.ceil((xmax - float(raster_extent.XMin)) / cell_x)))
    row0 = max(0, int(math.floor((ymin - float(raster_extent.YMin)) / cell_y)))
    row1 = min(int(raster.height), int(math.ceil((ymax - float(raster_extent.YMin)) / cell_y)))
    estimated_cells = max(0, col1 - col0) * max(0, row1 - row0)
    if estimated_cells <= 0:
        raise ValueError("栅格与评价单元没有可处理像元。")
    if estimated_cells > int(max_cells):
        raise ValueError("分析范围预计 {} 个像元，超过预算 {}；请缩小范围或显式提高预算。".format(estimated_cells, max_cells))

    token = uuid.uuid4().hex[:10]
    copy_fc = "in_memory/SCQA_Units_{}".format(token)
    zone_raster_path = "in_memory/SCQA_Zones_{}".format(token)
    temp_paths = [copy_fc, zone_raster_path]
    cleanup_warnings = []
    primary_error = None
    rows = []
    try:
        _copy_units_for_raster(
            arcpy, units, copy_fc, raster_sr, crs_signature, len(unit_ids), unit_extent
        )
        copied_names = {field.name.casefold() for field in arcpy.ListFields(copy_fc)}
        code_field = "SCQA_ZCODE"
        suffix = 1
        while code_field.casefold() in copied_names:
            code_field = "SCQAZ{}".format(suffix)
            suffix += 1
        arcpy.management.AddField(copy_fc, code_field, "LONG")
        id_by_code = {}
        with arcpy.da.UpdateCursor(copy_fc, [actual_field.name, code_field]) as cursor:
            code = 1
            for row in cursor:
                id_by_code[code] = row[0]
                row[1] = code
                cursor.updateRow(row)
                code += 1

        clip_extent = arcpy.Extent(
            float(raster_extent.XMin) + col0 * cell_x,
            float(raster_extent.YMin) + row0 * cell_y,
            float(raster_extent.XMin) + col1 * cell_x,
            float(raster_extent.YMin) + row1 * cell_y,
        )
        with arcpy.EnvManager(snapRaster=raster_path, cellSize=raster_path,
                              extent=clip_extent, outputCoordinateSystem=raster_sr,
                              geographicTransformations=""):
            arcpy.conversion.PolygonToRaster(
                copy_fc, code_field, zone_raster_path, "CELL_CENTER", "", cell_x
            )

        zone_raster = arcpy.Raster(zone_raster_path)
        zone_extent = zone_raster.extent
        cols, rows_count = int(zone_raster.width), int(zone_raster.height)
        if cols * rows_count > int(max_cells):
            raise ValueError("对齐后的处理窗口超过像元预算；请缩小范围。")
        lower_left = zone_extent.lowerLeft
        zone_array = arcpy.RasterToNumPyArray(
            zone_raster, lower_left, cols, rows_count, nodata_to_value=0
        )
        nodata = getattr(raster, "noDataValue", None)
        if nodata is None:
            raster_array = arcpy.RasterToNumPyArray(raster, lower_left, cols, rows_count)
        else:
            raster_array = arcpy.RasterToNumPyArray(
                raster, lower_left, cols, rows_count, nodata_to_value=nodata
            )
        if zone_array.shape != raster_array.shape:
            raise ValueError("评价单元栅格与专题栅格窗口不一致；已停止统计。")
        if len(id_by_code) != len(unit_ids):
            raise RuntimeError("评价单元编码数量与输入要素数不一致。")

        for code, unit_id in id_by_code.items():
            zone_values = raster_array[zone_array == code]
            stats = _summary_stats(zone_values, nodata)
            row = {"unit_id": unit_id, **stats,
                   "status": "NEEDS_FIELD_REVIEW" if stats["valid_pixel_count"] else "NO_VALID_PIXELS"}
            rows.append(row)
    except BaseException as exc:
        primary_error = exc
    finally:
        for path in reversed(temp_paths):
            try:
                if arcpy.Exists(path):
                    arcpy.management.Delete(path)
            except Exception as exc:
                cleanup_warnings.append("临时数据清理失败 {}: {}".format(path, exc))
    if primary_error is not None:
        if cleanup_warnings:
            raise RuntimeError(
                "单期专题栅格统计失败：{}；临时数据清理失败：{}".format(
                    primary_error, "；".join(cleanup_warnings)
                )
            ) from primary_error
        raise primary_error

    source_stat = os.stat(raster_path)
    source = {
        "path": raster_path,
        "size_bytes": source_stat.st_size,
        "modified_at_utc": dt.datetime.fromtimestamp(source_stat.st_mtime, dt.timezone.utc).isoformat(),
        "sha256": _sha256(raster_path),
        "band_count": int(getattr(raster, "bandCount", 1) or 1),
        "pixel_type": str(getattr(raster, "pixelType", "Unknown")),
        "cell_size_x": cell_x,
        "cell_size_y": cell_y,
        "nodata": nodata,
        "extent": [float(raster_extent.XMin), float(raster_extent.YMin),
                   float(raster_extent.XMax), float(raster_extent.YMax)],
        "wkid": int(getattr(raster_sr, "factoryCode", 0) or 0),
        "crs_name": str(getattr(raster_sr, "name", "Unknown")),
        "crs_wkt": raster_sr.exportToString(),
        "crs_signature": list(crs_signature),
    }
    outside_extent = (
        float(unit_extent.XMin) < float(raster_extent.XMin)
        or float(unit_extent.YMin) < float(raster_extent.YMin)
        or float(unit_extent.XMax) > float(raster_extent.XMax)
        or float(unit_extent.YMax) > float(raster_extent.YMax)
    )
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    csv_path = out_folder / "单期专题栅格评价单元摘要_{}.csv".format(stamp)
    json_path = out_folder / "单期专题栅格评价单元摘要_{}.json".format(stamp)
    report = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": "NEEDS_FIELD_REVIEW",
        "theme_type": "continuous",
        "unit_source": os.path.abspath(os.fspath(units)),
        "unit_id_field": actual_field.name,
        "source_raster": source,
        "processing": {
            "crs_pair_verified_equal": True,
            "resampling": "none; source grid retained",
            "cell_assignment": "CELL_CENTER",
            "estimated_window_cells": estimated_cells,
            "max_cells": int(max_cells),
            "pixel_coverage_denominator": "评价单元中心像元中落在栅格范围内的像元数；不代表整个评价单元边界完整覆盖。",
            "raster_extent_may_not_cover_unit_extent": outside_extent,
            "quality_grade_or_acceptance_decision": False,
        },
        "summary_fields": ["valid_pixel_count", "nodata_pixel_count", "valid_pixel_percent", "minimum", "mean", "median", "std", "maximum"],
        "std_definition": "population standard deviation (ddof=0)",
        "units": rows,
        "cleanup_warnings": cleanup_warnings,
        "outputs": {"csv": csv_path.name, "json": json_path.name},
        "limitations": "单期专题统计仅为采样分层/现场核查线索；不得据此判定耕地质量等级、地类真实性或验收结论。",
    }
    try:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            columns = ["unit_id", "total_pixel_count", "valid_pixel_count", "nodata_pixel_count",
                       "valid_pixel_percent", "minimum", "mean", "median", "std", "maximum", "status"]
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        _atomic_json(json_path, report)
    except Exception:
        csv_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        raise
    report["outputs"] = {"csv": str(csv_path), "json": str(json_path)}
    return report
