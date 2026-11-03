"""Read-only inventory of raster packages before remote-sensing workflows."""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import uuid
from pathlib import Path


RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".asc", ".jp2", ".vrt", ".crf", ".bmp", ".jpg", ".jpeg", ".png", ".dat"}
IMAGE_EXTENSIONS = {".bmp", ".jpg", ".jpeg", ".png"}
WORLD_FILE_SUFFIXES = {
    ".bmp": (".bpw", ".bmpw", ".wld"),
    ".jpg": (".jgw", ".jpgw", ".wld"),
    ".jpeg": (".jgw", ".jpgw", ".jpegw", ".wld"),
    ".png": (".pgw", ".pngw", ".wld"),
}
SIDECAR_SUFFIXES = (
    ".prj", ".tfw", ".tifw", ".wld", ".wld3", ".aux.xml", ".xml", ".ovr",
    ".jgw", ".jpgw", ".jpegw", ".pgw", ".pngw", ".bpw", ".bmpw",
)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _companions(path):
    candidates = [path + suffix for suffix in SIDECAR_SUFFIXES]
    stem, _extension = os.path.splitext(path)
    candidates.extend(stem + suffix for suffix in SIDECAR_SUFFIXES)
    return sorted({os.path.basename(item) for item in candidates if os.path.isfile(item)})


def _has_world_file(path):
    stem, extension = os.path.splitext(path)
    suffixes = WORLD_FILE_SUFFIXES.get(extension.lower(), (".wld", ".wld3"))
    return any(os.path.isfile(stem + suffix) for suffix in suffixes)


def _nodata_values(raster):
    values = getattr(raster, "noDataValues", None)
    if values is None:
        values = getattr(raster, "noDataValue", None)
    if isinstance(values, (tuple, list)):
        return [_number(value) for value in values]
    return [_number(values)]


def _inspect_raster(arcpy, path, root):
    raster = arcpy.Raster(path)
    desc = arcpy.Describe(path)
    sr = getattr(desc, "spatialReference", None) or getattr(raster, "spatialReference", None)
    wkid = int(getattr(sr, "factoryCode", 0) or 0) if sr else 0
    crs_name = str(getattr(sr, "name", "Unknown") or "Unknown") if sr else "Unknown"
    try:
        crs_wkt = sr.exportToString() if sr else ""
    except Exception:
        crs_wkt = ""
    if wkid > 0:
        status = "REVIEW_KNOWN_CRS"
        message = "坐标系可识别；仍需核对波段含义、时相、分辨率和处理参数后再选具体工具。"
    elif crs_name.lower() != "unknown" and crs_wkt:
        status = "REVIEW_CUSTOM_CRS"
        message = "发现完整自定义 WKT 但无标准 WKID；可在所有输入定义完全相同且来源已核实的前提下同 CRS 运算，跨 CRS 转换须依据控制资料确认。"
    elif Path(path).suffix.lower() in IMAGE_EXTENSIONS and not _has_world_file(path):
        status = "REVIEW_IMAGE_NO_GEOREFERENCE"
        message = "ArcPy 未读到空间参考且未发现常见世界文件；可能是普通图件，也可能缺少配准文件。可作为附件预览，空间分析前须核实地理配准元数据。"
    else:
        status = "BLOCKED_UNKNOWN_CRS"
        message = "没有可识别坐标系；必须从原始元数据或测量控制资料确认，禁止按位置猜测或直接赋予 EPSG。"

    extent = getattr(raster, "extent", None)
    extent_values = None
    if extent is not None:
        extent_values = [_number(getattr(extent, key, None)) for key in ("XMin", "YMin", "XMax", "YMax")]
    bands = int(getattr(raster, "bandCount", getattr(desc, "bandCount", 1)) or 1)
    review_flags = []
    if status == "REVIEW_CUSTOM_CRS":
        review_flags.append("CUSTOM_CRS")
    if status == "BLOCKED_UNKNOWN_CRS":
        review_flags.append("UNKNOWN_CRS")
    if status == "REVIEW_IMAGE_NO_GEOREFERENCE":
        review_flags.append("IMAGE_NO_GEOREFERENCE")
    if bands != 1:
        review_flags.append("MULTIBAND")
    if bands != 1 and status == "REVIEW_KNOWN_CRS":
        status = "REVIEW_MULTIBAND"
        message = "坐标系可识别但包含多个波段；需先确认波段顺序/语义，不能直接当作单波段输入。"
    return {
        "path": os.path.relpath(path, root),
        "format": Path(path).suffix.lower().lstrip("."),
        "width": int(getattr(raster, "width", getattr(desc, "width", 0)) or 0),
        "height": int(getattr(raster, "height", getattr(desc, "height", 0)) or 0),
        "band_count": bands,
        "pixel_type": str(getattr(raster, "pixelType", getattr(desc, "pixelType", "Unknown"))),
        "cell_size_x": _number(getattr(raster, "meanCellWidth", getattr(desc, "meanCellWidth", None))),
        "cell_size_y": _number(getattr(raster, "meanCellHeight", getattr(desc, "meanCellHeight", None))),
        "nodata": _number(getattr(raster, "noDataValue", None)),
        "nodata_values": _nodata_values(raster),
        "extent": extent_values,
        "wkid": wkid,
        "crs_name": crs_name,
        "crs_type": str(getattr(sr, "type", "Unknown")) if sr else "Unknown",
        "crs_wkt": crs_wkt,
        "crs_defined": status not in {"BLOCKED_UNKNOWN_CRS", "REVIEW_IMAGE_NO_GEOREFERENCE", "READ_ERROR"},
        "same_crs_requires_pairwise_verification": status not in {"BLOCKED_UNKNOWN_CRS", "REVIEW_IMAGE_NO_GEOREFERENCE", "READ_ERROR"},
        "review_flags": review_flags,
        "companions": _companions(path),
        "status": status,
        "message": message,
    }


def audit_raster_package(root, out_folder):
    """Inventory raster files without changing them; write an auditable CSV and JSON."""
    import arcpy

    root_path = Path(root).expanduser().resolve()
    output_path = Path(out_folder).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError("遥感数据包目录不存在：{}".format(root_path))
    if not output_path.is_dir():
        raise ValueError("清单输出目录不存在：{}".format(output_path))
    try:
        output_relative_to_root = output_path.is_relative_to(root_path)
    except AttributeError:  # ArcGIS Pro 3.1 / Python 3.9 supports is_relative_to; keep fallback for older runtimes.
        output_relative_to_root = os.path.commonpath([str(root_path), str(output_path)]) == str(root_path)

    rasters = []
    discovered = set()
    for folder, dirs, files in os.walk(str(root_path)):
        # CRF is a directory raster; treat it as one dataset and don't scan its internals.
        crf_dirs = [name for name in dirs if Path(name).suffix.lower() == ".crf"]
        for name in crf_dirs:
            dirs.remove(name)
            discovered.add(os.path.normcase(os.path.abspath(os.path.join(folder, name))))
        # File geodatabases have no raster file extensions; enumerate datasets through ArcPy.
        gdb_dirs = [name for name in dirs if Path(name).suffix.lower() == ".gdb"]
        for name in gdb_dirs:
            gdb_path = os.path.join(folder, name)
            previous_workspace = getattr(arcpy.env, "workspace", None)
            try:
                arcpy.env.workspace = gdb_path
                for dataset in arcpy.ListRasters("*", "ALL") or []:
                    path = dataset if os.path.isabs(dataset) else os.path.join(gdb_path, dataset)
                    discovered.add(os.path.normcase(os.path.abspath(path)))
            finally:
                arcpy.env.workspace = previous_workspace
        if output_relative_to_root:
            dirs[:] = [name for name in dirs if (Path(folder) / name).resolve() != output_path]
        for name in crf_dirs:
            path = os.path.join(folder, name)
            try:
                rasters.append(_inspect_raster(arcpy, path, str(root_path)))
            except Exception as exc:
                rasters.append(_read_error(path, str(root_path), exc))
        for filename in sorted(files):
            path = os.path.join(folder, filename)
            if Path(filename).suffix.lower() not in RASTER_EXTENSIONS:
                continue
            try:
                rasters.append(_inspect_raster(arcpy, path, str(root_path)))
            except Exception as exc:
                rasters.append(_read_error(path, str(root_path), exc))

    for path in sorted(discovered):
        if Path(path).suffix.lower() == ".crf":
            continue
        try:
            rasters.append(_inspect_raster(arcpy, path, str(root_path)))
        except Exception as exc:
            rasters.append(_read_error(path, str(root_path), exc))

    if not rasters:
        raise ValueError("目录中未发现受支持的栅格文件、CRF 目录或 FileGDB 栅格数据集。")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    json_name = "遥感数据包预检_{}_结果.json".format(stamp)
    csv_name = "遥感数据包预检_{}_栅格清单.csv".format(stamp)
    counts = {}
    for item in rasters:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    report = {
        "schema_version": 1,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_root": str(root_path),
        "read_only": True,
        "summary": {"raster_count": len(rasters), "status_counts": counts},
        "rasters": rasters,
        "outputs": {"json": json_name, "csv": csv_name},
        "limitations": "本清单是数据包预检，不等于影像处理结果或验收结论；未知/自定义坐标系不会自动修复。",
    }
    json_path = output_path / json_name
    csv_path = output_path / csv_name
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    columns = ("path", "format", "width", "height", "band_count", "pixel_type", "cell_size_x", "cell_size_y", "nodata", "nodata_values", "extent", "wkid", "crs_name", "crs_type", "crs_wkt", "crs_defined", "same_crs_requires_pairwise_verification", "review_flags", "status", "companions", "message", "error")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in rasters:
            row = dict(item)
            row["companions"] = "；".join(item["companions"])
            for key in ("nodata_values", "extent", "review_flags"):
                row[key] = json.dumps(item.get(key), ensure_ascii=False)
            writer.writerow(row)
    report["outputs"] = {"json": json_name, "csv": csv_name}
    return report


def _read_error(path, root, exc):
    return {
        "path": os.path.relpath(path, root), "format": Path(path).suffix.lower().lstrip("."),
        "width": None, "height": None, "band_count": None, "pixel_type": None,
        "cell_size_x": None, "cell_size_y": None, "nodata": None, "nodata_values": [], "extent": None,
        "wkid": 0, "crs_name": "Unknown", "crs_type": "Unknown", "crs_wkt": "",
        "crs_defined": False, "same_crs_requires_pairwise_verification": False, "review_flags": [],
        "companions": _companions(path), "status": "READ_ERROR", "error": str(exc),
        "message": "ArcPy 无法读取该栅格；核查文件完整性、格式支持和旁置文件。",
    }
