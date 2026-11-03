"""Non-destructive vector, CAD and RTK intake helpers for ArcGIS Pro 3.1."""
from __future__ import annotations

import csv
import codecs
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path

from acceptance.crs import parse_wkid, spatial_reference


VECTOR_EXTENSIONS = {".shp", ".gdb", ".gpkg", ".sqlite", ".geojson", ".json"}
CAD_EXTENSIONS = {".dwg", ".dxf", ".dgn"}


def _file_fingerprint(path):
    p = Path(path).resolve(strict=True)
    stat = p.stat()
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(p),
        "size_bytes": stat.st_size,
        "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(timespec="seconds"),
        "sha256": digest.hexdigest(),
    }


def _verify_file_unchanged(initial_fingerprint, path):
    current = _file_fingerprint(path)
    if current["size_bytes"] != initial_fingerprint["size_bytes"] or current["sha256"] != initial_fingerprint["sha256"]:
        raise RuntimeError("导入期间源文件内容发生变化，已停止并清理本次输出：" + str(path))
    return initial_fingerprint


def _source_file_fingerprints(path):
    """Fingerprint file based vector inputs, including shapefile sidecars."""
    source = Path(path).resolve()
    if source.suffix.lower() == ".shp":
        candidates = sorted(source.parent.glob(source.stem + ".*"))
        paths = [candidate for candidate in candidates if candidate.is_file()]
    else:
        paths = [source] if source.is_file() else []
    return {str(item): _file_fingerprint(item) for item in paths}


def _verify_source_files_unchanged(initial, path):
    current = _source_file_fingerprints(path)
    if current.keys() != initial.keys():
        raise RuntimeError("导入期间源文件或Shapefile配套文件集合发生变化，已停止并清理本次输出：" + str(path))
    for item, fingerprint in initial.items():
        _verify_file_unchanged(fingerprint, item)


def _validate_vector_layer(arcpy, path):
    desc = arcpy.Describe(path)
    data_type = str(getattr(desc, "dataType", "") or "").lower()
    selected_ids = str(getattr(desc, "FIDSet", "") or "").strip()
    where_clause = str(getattr(desc, "whereClause", "") or "").strip()
    definition_query = str(getattr(desc, "definitionQuery", "") or "").strip()
    if selected_ids or where_clause or definition_query:
        raise ValueError("输入图层存在选择集或定义查询，可能只导入部分要素；请改用未筛选的源要素类")
    return desc


def _assert_spatial_reference(arcpy, dataset, expected_sr):
    actual_sr = arcpy.Describe(dataset).spatialReference
    expected_code = int(getattr(expected_sr, "factoryCode", 0) or 0)
    actual_code = int(getattr(actual_sr, "factoryCode", 0) or 0)
    if expected_code and actual_code == expected_code:
        return actual_sr
    expected_wkt = expected_sr.exportToString() if hasattr(expected_sr, "exportToString") else None
    actual_wkt = actual_sr.exportToString() if hasattr(actual_sr, "exportToString") else None
    if expected_wkt and expected_wkt == actual_wkt:
        return actual_sr
    raise ValueError("输出坐标系核验失败：预期 {} (WKID {})，实际 {} (WKID {})".format(
        getattr(expected_sr, "name", "Unknown"), expected_code,
        getattr(actual_sr, "name", "Unknown"), actual_code,
    ))


def _cleanup_rtk_snapshot(path):
    try:
        Path(path).unlink(missing_ok=True)
        return None
    except Exception as exc:
        return "RTK 临时快照未能删除（{}）；请关闭占用程序后手动清理".format(exc)


def _create_rtk_snapshot(source, out_gdb):
    """Freeze one exact CSV byte stream for both Python preflight and ArcPy import."""
    source_path = Path(source).resolve(strict=True)
    initial = _file_fingerprint(source_path)
    raw = source_path.read_bytes()
    current = _file_fingerprint(source_path)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != initial["sha256"] or current["sha256"] != initial["sha256"]:
        raise RuntimeError("创建 RTK 快照期间源文件内容发生变化；请确认原始文件后重试")
    temp_dir = Path(out_gdb).resolve().parent / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    descriptor, snapshot = tempfile.mkstemp(prefix="rtk_snapshot_", suffix=".csv", dir=str(temp_dir))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if _file_fingerprint(snapshot)["sha256"] != initial["sha256"]:
            raise RuntimeError("RTK 临时快照校验失败，已停止导入")
    except Exception as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        cleanup_warning = _cleanup_rtk_snapshot(snapshot)
        if cleanup_warning:
            raise RuntimeError("RTK 快照创建失败：{}；{}".format(exc, cleanup_warning)) from exc
        raise
    return snapshot, initial


def _validate_rtk_csv_for_arcpy(source, csv_format):
    if csv_format.get("encoding") != "utf-8":
        raise ValueError("ArcGIS Pro RTK 导入首版仅接受 UTF-8（无BOM）编码；当前为{}，请先转码后导入".format(csv_format.get("encoding")))
    if csv_format.get("delimiter") != "逗号":
        raise ValueError("ArcGIS Pro RTK 导入要求标准逗号分隔 CSV；当前文件为{}，请先另存为 UTF-8 逗号 CSV".format(csv_format.get("delimiter")))
    schema_ini = Path(source).resolve().parent / "schema.ini"
    if schema_ini.is_file():
        try:
            text = schema_ini.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = schema_ini.read_text(encoding="gb18030")
        section = re.compile(r"^\s*\[\s*{}\s*\]\s*$".format(re.escape(Path(source).name)), re.IGNORECASE | re.MULTILINE)
        if section.search(text):
            raise ValueError("源目录 schema.ini 对该 CSV 定义了字段解析规则；首版拒绝丢失该规则的快照导入，请先按项目要求导出规范 CSV")
    return True


def _rollback_outputs(arcpy, paths):
    failures = []
    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            if arcpy.Exists(path):
                arcpy.management.Delete(path)
        except Exception as exc:
            failures.append("{}: {}".format(path, exc))
    return failures


def _rtk_elevation_status(z_field):
    if not z_field:
        return {"z_field": None, "vertical_reference_status": "NOT_PROVIDED", "vertical_reference": None}
    return {"z_field": str(z_field), "vertical_reference_status": "UNVERIFIED", "vertical_reference": None}


def _confirmed_transformation(arcpy, source_sr, target_sr, transformation):
    methods = arcpy.ListTransformations(source_sr, target_sr)
    if methods and not transformation:
        raise ValueError("可用坐标基准转换方法：{}。请按项目测量资料确认后填写。".format("、".join(methods)))
    if transformation and transformation not in methods:
        raise ValueError("指定的转换方法不在 ArcGIS 可用列表中")
    return transformation or ""


def inspect_vector(path):
    import arcpy

    p = os.path.abspath(path)
    if Path(p).suffix.lower() in {".geojson", ".json"} and os.path.isfile(p):
        with open(p, "r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        if Path(p).suffix.lower() == ".geojson":
            features = payload.get("features", []) if payload.get("type") == "FeatureCollection" else []
            geometry_types = sorted({feature.get("geometry", {}).get("type") for feature in features if feature.get("geometry")})
            shape_names = {"Point": "Point", "MultiPoint": "Multipoint", "LineString": "Polyline", "MultiLineString": "Polyline", "Polygon": "Polygon", "MultiPolygon": "Polygon"}
            shapes = sorted({shape_names[t] for t in geometry_types if t in shape_names})
            crs_name = (((payload.get("crs") or {}).get("properties") or {}).get("name"))
            wkid = None
            if crs_name:
                import re
                match = re.search(r"(?:EPSG[:/]+)(\d+)\s*$", str(crs_name), re.IGNORECASE)
                wkid = parse_wkid(match.group(1), label="GeoJSON WKID") if match else None
            return {"path": p, "data_type": "GeoJSON", "shape_type": shapes[0] if len(shapes) == 1 else ("Mixed" if shapes else None), "shape_types": shapes, "count": len(features), "coordinate_system": crs_name or ("WGS 1984 (GeoJSON default)" if Path(p).suffix.lower() == ".geojson" else "Unknown"), "factory_code": wkid or (4326 if Path(p).suffix.lower() == ".geojson" and not crs_name else 0), "extent": "", "status": "PASS" if shapes else "NEEDS_REVIEW"}
        if "features" in payload and "geometryType" in payload:
            esri_shape = str(payload["geometryType"]).rsplit(".", 1)[-1]
            shape = {"esriGeometryPoint": "Point", "esriGeometryMultipoint": "Multipoint", "esriGeometryPolyline": "Polyline", "esriGeometryPolygon": "Polygon"}.get(esri_shape, esri_shape)
            spatial_ref = payload.get("spatialReference") or {}
            wkid = spatial_ref.get("latestWkid") or spatial_ref.get("wkid") or 0
            wkid = parse_wkid(wkid, label="Esri JSON WKID") if wkid else None
            return {"path": p, "data_type": "Esri JSON", "shape_type": shape, "shape_types": [shape], "count": len(payload.get("features", [])), "coordinate_system": "WKID {}".format(wkid) if wkid else "Unknown", "factory_code": wkid or 0, "extent": "", "status": "PASS" if shape else "NEEDS_REVIEW"}
    if not arcpy.Exists(p):
        raise ValueError("输入矢量数据不存在或 ArcGIS 无法读取")
    desc = _validate_vector_layer(arcpy, p)
    sr = getattr(desc, "spatialReference", None)
    return {
        "path": p,
        "data_type": getattr(desc, "dataType", "unknown"),
        "shape_type": getattr(desc, "shapeType", None),
        "count": int(arcpy.management.GetCount(p)[0]) if hasattr(desc, "shapeType") else None,
        "coordinate_system": getattr(sr, "name", "Unknown"),
        "factory_code": int(getattr(sr, "factoryCode", 0) or 0),
        "extent": str(getattr(desc, "extent", "")),
        "status": "PASS" if sr and sr.name != "Unknown" else "NEEDS_REVIEW",
    }


def import_vector_copy(source, out_gdb, out_name, target_wkid=None, source_wkid=None, transformation=None):
    """Copy a vector into a new GDB feature class; project only with confirmed WKID."""
    import arcpy

    target_wkid = parse_wkid(target_wkid, "目标 WKID")
    source_wkid = parse_wkid(source_wkid, "源 WKID")

    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在；请先运行项目建库工具")
    if Path(source).suffix.lower() in {".geojson", ".json"}:
        return import_json_vector(source, out_gdb, out_name, target_wkid, source_wkid, transformation)
    source_fingerprints = _source_file_fingerprints(source)
    _validate_vector_layer(arcpy, source)
    info = inspect_vector(source)
    if info["shape_type"] not in ("Point", "Polyline", "Polygon", "Multipoint"):
        raise ValueError("仅接收点、线、面矢量")
    source_sr = arcpy.Describe(source).spatialReference
    dest = os.path.join(out_gdb, out_name)
    source_copy = None
    owned_outputs = []
    if source_sr.name == "Unknown":
        if not source_wkid:
            raise ValueError("源坐标系未知。请按正式测量资料指定源 WKID；禁止猜测")
        source_sr = spatial_reference(arcpy, source_wkid, "源 WKID", required=True)[1]
        if source_sr.name == "Unknown":
            raise ValueError("ArcGIS 无法解析指定的源 WKID")
        target_sr = spatial_reference(arcpy, target_wkid, "目标 WKID")[1] if target_wkid else source_sr
        stage = dest if source_sr.factoryCode == target_sr.factoryCode else os.path.join(out_gdb, out_name + "_SOURCE")
        if arcpy.Exists(stage) or (stage != dest and arcpy.Exists(dest)):
            raise FileExistsError("输出已存在，不覆盖：" + stage)
        method = _confirmed_transformation(arcpy, source_sr, target_sr, transformation) if stage != dest else ""
        owned_outputs = [stage] + ([dest] if stage != dest else [])
        try:
            with arcpy.EnvManager(outputCoordinateSystem=source_sr):
                arcpy.conversion.FeatureClassToFeatureClass(source, out_gdb, os.path.basename(stage))
            arcpy.management.DefineProjection(stage, source_sr)
            source_copy = stage
            if stage != dest:
                with arcpy.EnvManager(outputCoordinateSystem=target_sr):
                    arcpy.management.Project(stage, dest, target_sr, method)
            _assert_spatial_reference(arcpy, dest, target_sr)
        except Exception as exc:
            cleanup = _rollback_outputs(arcpy, owned_outputs)
            if cleanup:
                raise RuntimeError("矢量导入失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup))) from exc
            raise
    elif target_wkid:
        target_sr = spatial_reference(arcpy, target_wkid, "目标 WKID", required=True)[1]
        if source_wkid and source_wkid != int(source_sr.factoryCode or 0):
            raise ValueError("填写的源 WKID 与矢量元数据不一致")
        if arcpy.Exists(dest):
            raise FileExistsError("输出要素已存在，不覆盖：" + dest)
        method = _confirmed_transformation(arcpy, source_sr, target_sr, transformation) if source_sr.factoryCode != target_sr.factoryCode else ""
        owned_outputs = [dest]
        try:
            if source_sr.factoryCode != target_sr.factoryCode:
                with arcpy.EnvManager(outputCoordinateSystem=target_sr):
                    arcpy.management.Project(source, dest, target_sr, method)
            else:
                with arcpy.EnvManager(outputCoordinateSystem=target_sr):
                    arcpy.conversion.FeatureClassToFeatureClass(source, out_gdb, out_name)
            _assert_spatial_reference(arcpy, dest, target_sr)
        except Exception as exc:
            cleanup = _rollback_outputs(arcpy, owned_outputs)
            if cleanup:
                raise RuntimeError("矢量导入失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup))) from exc
            raise
    else:
        if arcpy.Exists(dest):
            raise FileExistsError("输出要素已存在，不覆盖：" + dest)
        owned_outputs = [dest]
        try:
            with arcpy.EnvManager(outputCoordinateSystem=source_sr):
                arcpy.conversion.FeatureClassToFeatureClass(source, out_gdb, out_name)
            _assert_spatial_reference(arcpy, dest, source_sr)
        except Exception as exc:
            cleanup = _rollback_outputs(arcpy, owned_outputs)
            if cleanup:
                raise RuntimeError("矢量导入失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup))) from exc
            raise
    if info.get("count") is not None:
        try:
            _verify_source_files_unchanged(source_fingerprints, source)
            actual_count = int(arcpy.management.GetCount(dest)[0])
            if actual_count != info["count"]:
                raise ValueError("矢量导入数量不一致：源要素 {} 个，目标要素 {} 个".format(info["count"], actual_count))
        except Exception as exc:
            cleanup = _rollback_outputs(arcpy, owned_outputs)
            if cleanup:
                raise RuntimeError("矢量数量核验失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup))) from exc
            raise
    return {"source": info, "output": dest, "coordinate_source_copy": source_copy, "target_wkid": target_wkid or source_wkid or info["factory_code"], "source_preserved": True}


def _discover_batch_vector_sources(arcpy, source_folder, recursive=True):
    root = os.path.abspath(source_folder)
    if not os.path.isdir(root):
        raise ValueError("请选择存在的矢量数据文件夹。")
    sources, failures = [], []

    def add_gdb(path):
        try:
            for directory, _subdirs, names in arcpy.da.Walk(path, datatype="FeatureClass"):
                sources.extend(os.path.join(directory, name) for name in names)
        except Exception as exc:
            failures.append({"source": path, "error": "FileGDB 扫描失败：" + str(exc)})

    if root.lower().endswith(".gdb"):
        add_gdb(root)
    else:
        for directory, subdirs, filenames in os.walk(root):
            subdirs.sort(key=str.casefold)
            gdb_dirs = [name for name in subdirs if name.lower().endswith(".gdb")]
            for name in gdb_dirs:
                add_gdb(os.path.join(directory, name))
                subdirs.remove(name)
            if not recursive:
                subdirs[:] = []
            for name in filenames:
                if os.path.splitext(name)[1].lower() in (".shp", ".geojson", ".json"):
                    sources.append(os.path.join(directory, name))

    unique = {}
    for source in sources:
        key = os.path.normcase(os.path.abspath(source))
        unique[key] = os.path.abspath(source)
    return sorted(unique.values(), key=lambda item: os.path.normcase(item)), failures


def _batch_output_name(arcpy, source, info, root, out_gdb, occupied, index):
    raw_base = os.path.splitext(os.path.basename(source.rstrip("\\/")))[0]
    base = arcpy.ValidateTableName(raw_base, out_gdb) or "VECTOR_{:03d}".format(index)
    shape_names = {"Point": "POINT", "Multipoint": "MULTIPOINT", "Polyline": "LINE", "Polygon": "POLYGON"}
    shapes = info.get("shape_types") or ([info.get("shape_type")] if info.get("shape_type") else [])
    suffixes = ["_" + shape_names[shape] for shape in shapes] if len(shapes) > 1 and all(shape in shape_names for shape in shapes) else [""]

    def names_for(candidate):
        return [arcpy.ValidateTableName(candidate + suffix, out_gdb) for suffix in suffixes]

    planned = names_for(base)
    if any(not name or name.casefold() in occupied for name in planned):
        try:
            relative = os.path.relpath(source, root)
        except ValueError:
            relative = source
        digest = hashlib.sha256(os.path.normcase(relative).encode("utf-8", "replace")).hexdigest()[:8]
        base = arcpy.ValidateTableName(base + "_" + digest, out_gdb)
        planned = names_for(base)
    attempt = 2
    while any(not name or name.casefold() in occupied for name in planned):
        base = arcpy.ValidateTableName("{}_{}".format(raw_base, attempt), out_gdb) or "VECTOR_{:03d}_{}".format(index, attempt)
        planned = names_for(base)
        attempt += 1
    occupied.update(name.casefold() for name in planned)
    return base


def import_vector_folder(source_folder, out_folder, recursive=True):
    """Batch copy supported vector sources to a new FileGDB without changing their CRS."""
    import arcpy

    source_root = os.path.abspath(source_folder)
    out_folder = os.path.abspath(os.path.expanduser(out_folder))
    if not os.path.isdir(source_root):
        raise ValueError("请选择存在的矢量数据文件夹。")
    if os.path.exists(out_folder):
        raise ValueError("成果目录已存在，为防止覆盖请另选一个新目录。")
    try:
        common = os.path.commonpath([source_root, out_folder])
    except ValueError:
        common = None
    if common and os.path.normcase(common) == os.path.normcase(source_root):
        raise ValueError("输出目录不能位于输入目录内部，以免下一次扫描误读本次成果。")

    sources, discovery_failures = _discover_batch_vector_sources(arcpy, source_root, bool(recursive))
    if not sources:
        detail = "另有 {} 个 FileGDB 扫描失败。".format(len(discovery_failures)) if discovery_failures else ""
        raise ValueError("没有发现可批量接入的 SHP、GeoJSON、Esri JSON 或 FileGDB 要素类。" + detail)
    if len(sources) > 200:
        raise ValueError("本次发现 {} 个矢量源，超过单批 200 项上限；请分子目录接入。".format(len(sources)))

    os.makedirs(out_folder, exist_ok=False)
    gdb_name = "矢量批量接入.gdb"
    arcpy.management.CreateFileGDB(out_folder, gdb_name)
    out_gdb = os.path.join(out_folder, gdb_name)
    records, outputs, occupied = [], [], set()
    for index, source in enumerate(sources, 1):
        record = {"source": source, "status": "FAIL"}
        try:
            record["source_files"] = list(_source_file_fingerprints(source).values())
            info = inspect_vector(source)
            record["source_info"] = info
            if info.get("count") == 0:
                raise ValueError("源数据没有要素，已跳过空图层。")
            name = _batch_output_name(arcpy, source, info, source_root, out_gdb, occupied, index)
            result = import_vector_copy(source, out_gdb, name)
            copied = result.get("outputs", [result["output"]])
            outputs.extend(copied)
            record.update({"status": "PASS", "output_name": name, "outputs": copied,
                           "source_preserved": True, "coordinate_system_preserved": True})
        except Exception as exc:
            record["error"] = str(exc)
        records.append(record)

    success_count = sum(1 for item in records if item["status"] == "PASS")
    failure_count = sum(1 for item in records if item["status"] != "PASS") + len(discovery_failures)
    status = "PASS" if not failure_count else ("PARTIAL" if success_count else "FAIL")
    manifest_path = os.path.join(out_folder, "矢量批量接入清单.json")
    manifest = write_intake_manifest(manifest_path, {
        "operation": "batch_vector_import",
        "status": status,
        "source_folder": source_root,
        "recursive": bool(recursive),
        "output_gdb": out_gdb,
        "target_coordinate_system": "每个输出保留各自源坐标系；不自动重投影",
        "source_count": len(sources),
        "success_count": success_count,
        "failure_count": failure_count,
        "discovery_failures": discovery_failures,
        "items": records,
    })
    return {"output_gdb": out_gdb, "outputs": outputs, "manifest": manifest,
            "source_count": len(sources), "success_count": success_count,
            "failure_count": failure_count, "status": status,
            "items": records,
            "discovery_failures": discovery_failures}


def import_json_vector(source, out_gdb, out_name, target_wkid=None, source_wkid=None, transformation=None):
    """Convert Esri JSON or mixed-geometry GeoJSON into one or more feature classes."""
    import arcpy

    target_wkid = parse_wkid(target_wkid, "目标 WKID")
    source_wkid = parse_wkid(source_wkid, "源 WKID")

    p = os.path.abspath(source)
    if Path(p).suffix.lower() not in {".geojson", ".json"} or not os.path.isfile(p):
        raise ValueError("请选择存在的 .geojson 或 Esri .json 文件")
    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在；请先运行项目建库工具")
    source_fingerprints = _source_file_fingerprints(p)
    info = inspect_vector(p)
    shapes = info.get("shape_types") or ([info["shape_type"]] if info.get("shape_type") else [])
    if not shapes:
        raise ValueError("JSON 文件没有可识别的点、线或面几何")
    info_wkid = parse_wkid(info.get("factory_code"), "JSON 元数据 WKID") if info.get("factory_code") else None
    if source_wkid and info_wkid and source_wkid != info_wkid:
        raise ValueError("填写的源 WKID 与 JSON 坐标系元数据不一致")
    inferred_wkid = source_wkid or info_wkid or 0
    if not inferred_wkid:
        raise ValueError("JSON 未提供可用坐标系；请依据正式资料填写源 WKID")
    source_sr = spatial_reference(arcpy, inferred_wkid, "源 WKID", required=True)[1]
    target_sr = spatial_reference(arcpy, target_wkid, "目标 WKID")[1] if target_wkid else source_sr
    if source_sr.name == "Unknown" or target_sr.name == "Unknown":
        raise ValueError("ArcGIS 无法解析源或目标坐标系 WKID")
    method = _confirmed_transformation(arcpy, source_sr, target_sr, transformation) if source_sr.factoryCode != target_sr.factoryCode else ""
    type_codes = {"Point": "POINT", "Multipoint": "MULTIPOINT", "Polyline": "POLYLINE", "Polygon": "POLYGON"}
    suffixes = {"Point": "POINT", "Multipoint": "MULTIPOINT", "Polyline": "LINE", "Polygon": "POLYGON"}
    source_counts = _json_geometry_counts(p, info, shapes)
    suffix = uuid.uuid4().hex[:10]
    planned = []
    for shape in shapes:
        shape_suffix = suffixes.get(shape)
        if not shape_suffix:
            raise ValueError("JSON 含不支持的几何类型：{}".format(shape))
        final_name = out_name + ("_" + shape_suffix if len(shapes) > 1 else "")
        dest = os.path.join(out_gdb, final_name)
        stage = os.path.join(out_gdb, "_JSON_STAGE_{}_{}".format(suffix, shape_suffix))
        final_stage = os.path.join(out_gdb, "_JSON_FINAL_{}_{}".format(suffix, shape_suffix))
        for candidate in (dest, stage, final_stage):
            if arcpy.Exists(candidate):
                raise FileExistsError("JSON 输出已存在，不覆盖：" + candidate)
        planned.append({"shape": shape, "dest": dest, "stage": stage, "final_stage": final_stage})

    temporary = [path for item in planned for path in (item["stage"], item["final_stage"])]
    outputs = []
    try:
        for item in planned:
            shape = item["shape"]
            stage = item["stage"]
            if Path(p).suffix.lower() == ".geojson":
                with arcpy.EnvManager(outputCoordinateSystem=source_sr):
                    arcpy.conversion.JSONToFeatures(p, stage, type_codes[shape])
            else:
                with arcpy.EnvManager(outputCoordinateSystem=source_sr):
                    arcpy.conversion.JSONToFeatures(p, stage)
            stage_count = int(arcpy.management.GetCount(stage)[0])
            expected_count = source_counts[shape]
            if stage_count != expected_count:
                raise ValueError("{}几何导入数量不一致：源 {} 个，ArcGIS 输出 {} 个".format(
                    shape, expected_count, stage_count,
                ))
            actual_sr = arcpy.Describe(stage).spatialReference
            if actual_sr.name == "Unknown":
                arcpy.management.DefineProjection(stage, source_sr)
            if source_sr.factoryCode != target_sr.factoryCode:
                with arcpy.EnvManager(outputCoordinateSystem=target_sr):
                    arcpy.management.Project(stage, item["final_stage"], target_sr, method)
            else:
                with arcpy.EnvManager(outputCoordinateSystem=target_sr):
                    arcpy.management.CopyFeatures(stage, item["final_stage"])
            _assert_spatial_reference(arcpy, item["final_stage"], target_sr)
            final_count = int(arcpy.management.GetCount(item["final_stage"])[0])
            if final_count != expected_count:
                raise ValueError("{}几何投影后数量不一致：源 {} 个，目标 {} 个".format(
                    shape, expected_count, final_count,
                ))
        for item in planned:
            arcpy.management.Rename(item["final_stage"], item["dest"])
            outputs.append(item["dest"])
        _verify_source_files_unchanged(source_fingerprints, p)
    except Exception as exc:
        cleanup = _rollback_outputs(arcpy, temporary + outputs)
        if cleanup:
            raise RuntimeError("JSON 导入失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup))) from exc
        raise
    cleanup = _rollback_outputs(arcpy, temporary)
    if cleanup:
        output_cleanup = _rollback_outputs(arcpy, outputs)
        detail = "；正式输出回滚失败：{}".format("；".join(output_cleanup)) if output_cleanup else ""
        raise RuntimeError("JSON 导入完成前未能清理临时要素：{}{}".format("；".join(cleanup), detail))
    if not outputs:
        raise ValueError("JSON 文件未生成点、线或面要素")
    return {"source": info, "output": outputs[0], "outputs": outputs, "target_wkid": int(target_wkid or inferred_wkid), "source_preserved": True, "review": "混合几何已按点/线/面拆分；请核对源坐标参考、字段语义与要素数量后再纳入正式成果。"}


def _json_geometry_counts(path, info, shapes):
    """Count importable source geometries and reject null/unsupported records."""
    with open(path, "r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    geometry_to_shape = {
        "Point": "Point", "MultiPoint": "Multipoint",
        "LineString": "Polyline", "MultiLineString": "Polyline",
        "Polygon": "Polygon", "MultiPolygon": "Polygon",
    }
    counts = {shape: 0 for shape in shapes}
    features = payload.get("features", [])
    if info.get("data_type") == "GeoJSON":
        for index, feature in enumerate(features, 1):
            geometry = feature.get("geometry") if isinstance(feature, dict) else None
            if geometry is None:
                raise ValueError("GeoJSON 第 {} 个要素没有几何；请先修复源数据，未创建输出".format(index))
            shape = geometry_to_shape.get(geometry.get("type"))
            if shape not in counts:
                raise ValueError("GeoJSON 第 {} 个要素几何类型不受支持：{}".format(index, geometry.get("type")))
            counts[shape] += 1
    else:
        shape = info.get("shape_type")
        for index, feature in enumerate(features, 1):
            geometry = feature.get("geometry") if isinstance(feature, dict) else None
            if geometry is None:
                raise ValueError("Esri JSON 第 {} 个要素没有几何；请先修复源数据，未创建输出".format(index))
            if shape not in counts:
                raise ValueError("Esri JSON 几何类型无法映射到导入类型：{}".format(shape))
            counts[shape] += 1
    if not any(counts.values()):
        raise ValueError("JSON 不含可导入的有效几何要素")
    if set(counts) != {shape for shape, count in counts.items() if count > 0}:
        raise ValueError("JSON 几何类型统计与检查结果不一致：{}".format(counts))
    return counts


def _cad_feature_class_summary(arcpy, output_dataset):
    feature_classes = []
    for workspace, _dirs, names in arcpy.da.Walk(output_dataset, datatype="FeatureClass"):
        for name in names:
            path = os.path.join(workspace, name)
            feature_classes.append({
                "name": name,
                "path": path,
                "shape_type": getattr(arcpy.Describe(path), "shapeType", "Unknown"),
                "count": int(arcpy.management.GetCount(path)[0]),
            })
    feature_count = sum(item["count"] for item in feature_classes)
    status = "NEEDS_REVIEW" if feature_count else "EMPTY_NEEDS_REVIEW"
    if not feature_classes:
        empty_reason = "CAD 转换未生成任何要素类；检查源 CAD 实体、图层及转换消息"
    elif not feature_count:
        empty_reason = "CAD 已生成要素类，但总要素数为 0；不得作为有效成果使用"
    else:
        empty_reason = None
    return feature_classes, feature_count, status, empty_reason


def import_cad(source, out_gdb, dataset_name, source_wkid, reference_scale=1000):
    """Convert CAD to a new feature dataset after explicit coordinate confirmation."""
    import arcpy

    source_wkid = parse_wkid(source_wkid, "CAD 源 WKID", required=True)

    p = os.path.abspath(source)
    if Path(p).suffix.lower() not in CAD_EXTENSIONS or not os.path.isfile(p):
        raise ValueError("请选择存在的 DWG、DXF 或 DGN 文件")
    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在；请先运行项目建库工具")
    source_wkid, sr = spatial_reference(arcpy, source_wkid, "CAD 源 WKID", required=True)
    output_dataset = os.path.join(out_gdb, dataset_name)
    if arcpy.Exists(output_dataset):
        raise FileExistsError("CAD 输出数据集已存在，不覆盖：" + output_dataset)
    base = os.path.splitext(p)[0]
    sidecar_candidates = (
        base + ".prj", base + ".wld", base + ".world", base + ".wld3",
        p + ".wld", p + ".world", p + ".wld3",
    )
    registration_materials = sorted({os.path.basename(item) for item in sidecar_candidates if os.path.isfile(item)})
    source_files = [_file_fingerprint(item) for item in [p] + [os.path.join(os.path.dirname(p), name) for name in registration_materials]]
    try:
        arcpy.conversion.CADToGeodatabase(p, out_gdb, dataset_name, float(reference_scale), sr)
        current_materials = sorted({os.path.basename(item) for item in sidecar_candidates if os.path.isfile(item)})
        if current_materials != registration_materials:
            raise RuntimeError("CAD 导入期间配套坐标文件集合发生变化；请复核后重试")
        for source_file in source_files:
            _verify_file_unchanged(source_file, source_file["path"])
        feature_classes, feature_count, status, empty_reason = _cad_feature_class_summary(arcpy, output_dataset)
    except Exception as exc:
        cleanup_failures = _rollback_outputs(arcpy, [output_dataset])
        if cleanup_failures:
            raise RuntimeError("CAD 导入失败：{}；回滚未完成：{}".format(exc, "；".join(cleanup_failures))) from exc
        raise
    return {
        "source": p,
        "source_files": source_files,
        "output_dataset": output_dataset,
        "feature_classes": feature_classes,
        "feature_count": feature_count,
        "status": status,
        "empty_reason": empty_reason,
        "source_wkid_input": int(source_wkid),
        "source_preserved": True,
        "registration_materials": registration_materials,
        "registration_status": "NEEDS_REVIEW",
        "registration_material_check": "发现配套文件，须核验其来源、控制点和残差" if registration_materials else "未发现 PRJ/WLD/world 配套文件；须在 ArcGIS Pro Georeference CAD 中完成控制点配准并保存记录",
        "review": "source WKID 仅定义坐标参考，不能证明 CAD 已配准；必须在 ArcGIS Pro Georeference CAD 中核查控制点、残差、单位/基准/带号并保存配准材料。发现的 PRJ/WLD/world 文件仅作证据线索，不能证明已配准。",
    }


def _rtk_csv_reader(path):
    raw = Path(path).read_bytes()
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        encoding = "utf-32"
    elif raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        encoding = "utf-16"
    elif raw.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        try:
            raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "gb18030"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("RTK CSV 编码无法识别；请从仪器重新导出为 UTF-8、GB18030 或带 BOM 的 UTF-16 CSV") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, dialect=dialect)
    if reader.fieldnames is None:
        raise ValueError("RTK CSV 缺少字段标题行")
    reader.fieldnames = [name.strip().lstrip("\ufeff") if name is not None else name for name in reader.fieldnames]
    delimiter = {"\t": "TAB", ",": "逗号", ";": "分号", "|": "竖线"}.get(dialect.delimiter, dialect.delimiter)
    return reader, {"encoding": encoding, "delimiter": delimiter}


def csv_headers(path):
    reader, _format = _rtk_csv_reader(path)
    return reader.fieldnames or []


def _read_rtk_coordinates(path, x_field, y_field, z_field, axis_order, geographic):
    if axis_order not in {"XY", "YX"}:
        raise ValueError("字段轴序必须明确选择 XY 或 YX")
    row_count = 0
    ranges = {"x": None, "y": None, "z": None}
    reader, csv_format = _rtk_csv_reader(path)
    headers = reader.fieldnames or []
    selected_fields = (x_field, y_field) + ((z_field,) if z_field else ())
    if x_field == y_field:
        raise ValueError("X/Y 候选字段必须是不同字段")
    for field in selected_fields:
        if not field or field not in headers:
            raise ValueError("CSV 找不到指定字段：{}".format(field or "(空字段名)"))
        if headers.count(field) != 1:
            raise ValueError("CSV 字段名 {} 重复，无法唯一确定坐标映射".format(field))
    x_source, y_source = (x_field, y_field) if axis_order == "XY" else (y_field, x_field)
    for line_number, row in enumerate(reader, start=2):
        values = {}
        for label, field in (("X", x_source), ("Y", y_source)) + ((('Z', z_field),) if z_field else ()):
            raw = row.get(field)
            try:
                number = float(raw.strip()) if raw is not None else math.nan
            except (TypeError, ValueError):
                number = math.nan
            if not math.isfinite(number):
                raise ValueError("CSV 第{}行字段 {}（{}）为空或不是有限数值".format(line_number, field, label))
            values[label.lower()] = number
        if geographic:
            if not -180.0 <= values["x"] <= 180.0 or not -90.0 <= values["y"] <= 90.0:
                raise ValueError("CSV 第{}行经纬度范围错误：X（经度）应在[-180,180]，Y（纬度）应在[-90,90]；请检查 XY/YX 字段映射".format(line_number))
            # In particular, reject the common China RTK swap where a latitude-like
            # X and longitude-like Y cannot be valid longitude/latitude in this order.
            if abs(values["x"]) <= 90.0 < abs(values["y"]):
                raise ValueError("CSV 第{}行坐标表现为纬度/经度顺序；请确认并显式选择 YX 映射".format(line_number))
        row_count += 1
        for axis in (("x", "y", "z") if z_field else ("x", "y")):
            value = values[axis]
            if ranges[axis] is None:
                ranges[axis] = {"min": value, "max": value}
            else:
                ranges[axis]["min"] = min(ranges[axis]["min"], value)
                ranges[axis]["max"] = max(ranges[axis]["max"], value)
    if not row_count:
        raise ValueError("RTK CSV 没有坐标数据行")
    return row_count, ranges, csv_format


def import_rtk_csv(csv_path, out_gdb, out_name, x_field, y_field, source_wkid, target_wkid, z_field=None, transformation=None, axis_order="XY"):
    """Preflight RTK rows, create source-CRS points, then perform at most one explicit projection."""
    import arcpy

    source_wkid = parse_wkid(source_wkid, "RTK 源 WKID", required=True)
    target_wkid = parse_wkid(target_wkid, "RTK 目标 WKID", required=True)

    p = os.path.abspath(csv_path)
    if not os.path.isfile(p) or Path(p).suffix.lower() != ".csv":
        raise ValueError("RTK 首版仅接收 CSV；请先导出原始记录，不要覆盖仪器文件")
    if not source_wkid or not target_wkid:
        raise ValueError("RTK 源坐标系和项目目标坐标系 WKID 均须从测量资料确认")
    if axis_order not in {"XY", "YX"}:
        raise ValueError("字段轴序必须明确选择 XY 或 YX")
    if not arcpy.Exists(out_gdb):
        raise ValueError("目标 FileGDB 不存在；请先运行项目建库工具")
    raw_path = os.path.join(out_gdb, out_name + "_RTK_RAW")
    projected_path = os.path.join(out_gdb, out_name + "_RTK_PROJECTED")
    if arcpy.Exists(raw_path) or arcpy.Exists(projected_path):
        raise FileExistsError("RTK 输出已存在，不覆盖：" + out_name)
    source_sr = spatial_reference(arcpy, source_wkid, "RTK 源 WKID", required=True)[1]
    target_sr = spatial_reference(arcpy, target_wkid, "RTK 目标 WKID", required=True)[1]
    if source_sr.name == "Unknown" or target_sr.name == "Unknown":
        raise ValueError("ArcGIS 无法解析坐标系 WKID")
    method = _confirmed_transformation(arcpy, source_sr, target_sr, transformation) if source_sr.factoryCode != target_sr.factoryCode else ""
    elevation = _rtk_elevation_status(z_field)
    snapshot_path, source_fingerprint = _create_rtk_snapshot(p, out_gdb)
    try:
        row_count, coordinate_ranges, csv_format = _read_rtk_coordinates(
            snapshot_path, x_field, y_field, z_field, axis_order, getattr(source_sr, "type", "") == "Geographic"
        )
        _validate_rtk_csv_for_arcpy(p, csv_format)
    except Exception as exc:
        cleanup_warning = _cleanup_rtk_snapshot(snapshot_path)
        if cleanup_warning:
            raise RuntimeError("RTK CSV预检失败：{}；{}".format(exc, cleanup_warning)) from exc
        raise
    try:
        # XYTableToPoint honors outputCoordinateSystem. Isolate the call so an ArcGIS Pro
        # map/project environment cannot silently reproject the raw coordinates first.
        with arcpy.EnvManager(outputCoordinateSystem=None):
            arcpy.management.XYTableToPoint(snapshot_path, raw_path, x_field if axis_order == "XY" else y_field, y_field if axis_order == "XY" else x_field, z_field or "", source_sr)
        raw_desc = arcpy.Describe(raw_path)
        raw_sr = getattr(raw_desc, "spatialReference", None)
        if getattr(raw_sr, "factoryCode", 0) != source_sr.factoryCode:
            raise RuntimeError("RTK 原坐标点坐标系读回不等于源 WKID；已阻止后续投影")
        raw_count = int(arcpy.management.GetCount(raw_path)[0])
        if raw_count != row_count:
            raise RuntimeError("RTK 原坐标点数 {} 与已预检 CSV 行数 {} 不一致；检查失败行或空几何".format(raw_count, row_count))
        null_count = 0
        with arcpy.da.SearchCursor(raw_path, ["SHAPE@"] ) as cursor:
            for (geometry,) in cursor:
                if geometry is None or getattr(geometry, "isEmpty", False):
                    null_count += 1
        if null_count:
            raise RuntimeError("RTK 原坐标点包含 {} 个空几何；已阻止后续投影".format(null_count))
        if source_sr.factoryCode == target_sr.factoryCode:
            with arcpy.EnvManager(outputCoordinateSystem=None):
                arcpy.management.CopyFeatures(raw_path, projected_path)
        else:
            # Likewise prevent the ambient output CRS from changing Project's declared target.
            with arcpy.EnvManager(outputCoordinateSystem=None):
                arcpy.management.Project(raw_path, projected_path, target_sr, method)
        projected_sr = getattr(arcpy.Describe(projected_path), "spatialReference", None)
        if getattr(projected_sr, "factoryCode", 0) != target_sr.factoryCode:
            raise RuntimeError("RTK 投影点坐标系读回不等于目标 WKID")
        _verify_file_unchanged(source_fingerprint, p)
        for point_path in (raw_path, projected_path):
            arcpy.management.AddField(point_path, "Z_STATUS", "TEXT", field_length=20)
            arcpy.management.AddField(point_path, "Z_FIELD", "TEXT", field_length=64)
            arcpy.management.AddField(point_path, "VERT_REF", "TEXT", field_length=128)
            with arcpy.da.UpdateCursor(point_path, ["Z_STATUS", "Z_FIELD", "VERT_REF"]) as cursor:
                for row in cursor:
                    row[0] = elevation["vertical_reference_status"]
                    row[1] = elevation["z_field"]
                    row[2] = elevation["vertical_reference"] or "未提供/未核实"
                    cursor.updateRow(row)
    except Exception as exc:
        cleanup_failures = _rollback_outputs(arcpy, [projected_path, raw_path])
        snapshot_cleanup_warning = _cleanup_rtk_snapshot(snapshot_path)
        diagnostics = []
        if cleanup_failures:
            diagnostics.append("回滚未完成：{}".format("；".join(cleanup_failures)))
        if snapshot_cleanup_warning:
            diagnostics.append(snapshot_cleanup_warning)
        if diagnostics:
            raise RuntimeError("RTK 导入失败：{}；{}".format(exc, "；".join(diagnostics))) from exc
        raise
    snapshot_cleanup_warning = _cleanup_rtk_snapshot(snapshot_path)
    return {
        "source_csv": p,
        "source_files": [source_fingerprint],
        "raw_points": raw_path,
        "projected_points": projected_path,
        "source_wkid": int(source_wkid),
        "target_wkid": int(target_wkid),
        "field_mapping": {"X": x_field if axis_order == "XY" else y_field, "Y": y_field if axis_order == "XY" else x_field, "Z": z_field},
        "axis_order": axis_order,
        "row_count": row_count,
        "csv_format": csv_format,
        "coordinates": coordinate_ranges,
        "raw_output_wkid": int(raw_sr.factoryCode),
        "projected_output_wkid": int(projected_sr.factoryCode),
        "null_geometry_count": null_count,
        "z_field": elevation["z_field"],
        "vertical_reference_status": elevation["vertical_reference_status"],
        "vertical_reference": elevation["vertical_reference"],
        "snapshot_cleanup_warning": snapshot_cleanup_warning,
        "raw_csv_preserved": True,
        "status": "NEEDS_REVIEW",
        "review": "坐标数值与范围预检通过；仍须核对 RTK 固定解/浮点解、天线高、椭球高/正常高、坐标轴序、单位、中央子午线及转换参数。Z_STATUS 字段明确标记垂直基准状态；检查结果不证明 datum 或高程基准正确。",
    }


def write_intake_manifest(path, payload):
    data = dict(payload)
    data.update({"created_at": dt.datetime.now().isoformat(timespec="seconds"), "source_preserved": True})
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError("处理清单已存在，不覆盖：" + str(out))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(out)
