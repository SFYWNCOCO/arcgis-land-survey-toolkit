"""只读资料发现与角色映射。候选冲突不取第一项，不修改原始数据。"""
from __future__ import annotations

import hashlib
import json
import os

from .task_state import _atomic_json

FACTOR_FIELDS = {
    "landform": ("DXBW", "地形部位", "TERRAIN_POSITION"),
    "soil_species": ("TZMC", "土种", "土种名称", "SOIL_SPECIES"),
    "supplement_source": ("BCGDLY", "补充耕地来源", "SUPPLEMENT_SOURCE"),
    "land_use": ("DLMC", "LANDTYPE", "土地利用现状", "LAND_USE"),
}
OTHER_FIELDS = {
    "parcel_id": ("PE_ID", "DIKUAI_NO", "DKBH", "DKBM", "BSM", "PL_NAME"),
    "unit_id": ("PJDYBH", "PJDYBH_M", "DYBH", "EVAL_UNIT"),
    "project": ("XMBH", "项目编号", "PROJECT_ID", "XM_ID"),
    "point_id": ("POINT_ID", "CYDBH", "采样点编号"),
}
ROLES = ("parcels", "imagery", "units", "samples", "soil_species", "landform",
         "land_use", "supplement_source", "auxiliary")
RASTER_EXTENSIONS = (".tif", ".tiff", ".img", ".jp2", ".ecw", ".sid", ".vrt")


def field_mapping(names):
    lookup = {str(name).casefold(): name for name in names}
    return {role: lookup[next(key.casefold() for key in aliases if key.casefold() in lookup)]
            for role, aliases in dict(FACTOR_FIELDS, **OTHER_FIELDS).items()
            if any(key.casefold() in lookup for key in aliases)}


def _api(arcpy_module):
    if arcpy_module is not None:
        return arcpy_module
    import arcpy
    return arcpy


def imagery_provenance(path):
    """读取旁置证明和旧生成清单；搬迁后按同目录唯一文件名保留演示标记。"""
    path = os.path.abspath(os.fspath(path))
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    direct = os.path.join(path, "imagery_metadata.json") if os.path.isdir(path) else path + ".source.json"
    files = [direct, os.path.join(directory, "imagery_sources.json"), os.path.join(directory, "imagery_source.json")]
    matched, evidence, issues = [], [], []
    for filename in dict.fromkeys(files):
        if not os.path.isfile(filename):
            continue
        try:
            with open(filename, encoding="utf-8-sig") as stream:
                payload = json.load(stream)
            records = list(payload["sources"].values()) if isinstance(payload.get("sources"), dict) else [payload]
            accepted = []
            for record in records:
                output = str(record.get("output") or "") if isinstance(record, dict) else ""
                if isinstance(record, dict) and (filename == direct or (output and
                        os.path.normcase(os.path.basename(output)) == os.path.normcase(os.path.basename(path)))):
                    accepted.append(record)
            if accepted:
                matched.extend(accepted)
                evidence.append(filename)
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            issues.append({"file": filename, "detail": str(exc)})
    return {"demo_only": any(record.get("demo_only") is True for record in matched),
            "records": matched, "evidence_files": evidence, "issues": issues}


def inspect_source(path, arcpy_module=None, layer_uri=None):
    api = _api(arcpy_module)
    path = os.fspath(path)
    desc = api.Describe(path)
    datatype = getattr(desc, "dataType", "")
    raster = datatype in ("RasterDataset", "RasterLayer", "RasterBand", "MosaicDataset", "ImageServer")
    names = [] if raster else [f.name for f in api.ListFields(path)]
    fields = field_mapping(names)
    geometry = "Raster" if raster else getattr(desc, "shapeType", "Table")
    roles = []
    if raster:
        roles.append("imagery")
    elif geometry == "Polygon":
        if fields.get("parcel_id"):
            roles.append("parcels")
        if fields.get("unit_id"):
            roles.append("units")
        roles.extend(role for role in FACTOR_FIELDS if role in fields)
    elif geometry in ("Point", "Multipoint") and fields.get("point_id"):
        roles.append("samples")
    if not roles:
        roles.append("auxiliary")
    canonical = path if "://" in path else os.path.normcase(os.path.abspath(path))
    identity = "{}\0{}".format(canonical, layer_uri or "")
    sr = getattr(desc, "spatialReference", None)
    extent = getattr(desc, "extent", None)
    candidate = {"id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "path": path, "name": os.path.basename(path), "layer_uri": layer_uri,
        "data_type": datatype, "geometry": geometry, "roles": roles, "fields": names,
        "field_mapping": fields, "missing_factors": [key for key in FACTOR_FIELDS if key not in fields],
        "crs": {"name": getattr(sr, "name", "Unknown"), "wkid": getattr(sr, "factoryCode", 0)},
        "extent": [getattr(extent, attr, None) for attr in ("XMin", "YMin", "XMax", "YMax")],
        "count": None if raster else int(api.management.GetCount(path)[0]),
        "status": "NEEDS_REVIEW", "evidence": "按数据类型及明确字段名识别；需确认用途"}
    if raster:
        candidate["band_count"] = getattr(desc, "bandCount", None)
        candidate["pixel_size_native"] = [getattr(desc, "meanCellWidth", None), getattr(desc, "meanCellHeight", None)]
        sidecar = path + ".source.json"
        if os.path.isfile(sidecar):
            try:
                with open(sidecar, encoding="utf-8-sig") as stream:
                    metadata = json.load(stream)
                candidate["demo_only"] = bool(metadata.get("demo_only"))
                candidate["source_label"] = metadata.get("source_label")
            except (OSError, ValueError, TypeError) as exc:
                candidate["metadata_issue"] = str(exc)
        provenance = imagery_provenance(path)
        candidate["provenance"] = provenance
        if provenance["demo_only"]:
            candidate["demo_only"] = True
            candidate["name"] = "[演示/合成成果] " + candidate["name"]
    return candidate


def resolve_roles(candidates, choices=None):
    choices = choices or {}
    result = {}
    for role in ROLES:
        options = [row["id"] for row in candidates if role in row["roles"]]
        chosen = choices.get(role)
        if chosen and chosen not in options:
            raise ValueError("角色 {} 的选择不在有效候选中：{}".format(role, chosen))
        if not chosen and len(options) == 1:
            chosen = options[0]
        result[role] = {"candidates": options, "selected": chosen,
                        "status": "SELECTED" if chosen else "AMBIGUOUS" if options else "MISSING"}
    return result


def _catalog(candidates, issues, choices=None):
    return {"schema_version": 1, "candidates": candidates, "issues": issues,
            "roles": resolve_roles(candidates, choices), "status": "NEEDS_REVIEW"}


def _reparse_directory(path):
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400) or os.path.islink(path)
    except OSError:
        return True


def discover_folder(folder, arcpy_module=None, choices=None, output=None, max_sources=10000):
    api = _api(arcpy_module)
    folder = os.path.abspath(os.fspath(folder))
    if not os.path.isdir(folder):
        raise FileNotFoundError("资料文件夹不存在：" + folder)
    paths, candidates, issues = [], [], []
    def walk_error(exc):
        issues.append({"path": exc.filename, "status": "FAIL", "detail": str(exc)})
    for current, dirs, files in os.walk(folder, followlinks=False, onerror=walk_error):
        workspaces = [current] if current.lower().endswith((".gdb", ".gpkg")) else []
        kept = []
        for name in sorted(dirs):
            path = os.path.join(current, name)
            if _reparse_directory(path):
                issues.append({"path": path, "status": "NEEDS_REVIEW", "detail": "未跟随目录链接，避免重复及循环"})
            elif name.lower().endswith(".gdb"):
                workspaces.append(path)
            else:
                kept.append(name)
        dirs[:] = [] if workspaces == [current] else kept
        for name in files:
            path = os.path.join(current, name)
            if name.lower().endswith((".shp", ".geojson") + RASTER_EXTENSIONS):
                paths.append(path)
            elif name.lower().endswith(".gpkg"):
                workspaces.append(path)
        for workspace in workspaces:
            try:
                for base, _subdirs, datasets in api.da.Walk(workspace, datatype=["FeatureClass", "RasterDataset"]):
                    paths.extend(os.path.join(base, name) for name in datasets)
            except Exception as exc:
                issues.append({"path": workspace, "status": "FAIL", "detail": str(exc)})
        if len(paths) > max_sources:
            raise ValueError("资料候选超过 {} 个，请缩小资料目录范围".format(max_sources))
    for path in sorted(set(paths), key=str.casefold):
        try:
            candidates.append(inspect_source(path, api))
        except Exception as exc:
            issues.append({"path": path, "status": "FAIL", "detail": str(exc)})
    result = _catalog(candidates, issues, choices)
    result["folder"] = folder
    if output:
        _atomic_json(output, result)
    return result


def discover_map(mapping, arcpy_module=None, choices=None):
    api = _api(arcpy_module)
    candidates, issues = [], []
    for layer in mapping.listLayers():
        if getattr(layer, "isGroupLayer", False):
            continue
        try:
            if layer.isBroken:
                raise ValueError("图层数据连接失效")
            candidate = inspect_source(layer.dataSource, api, getattr(layer, "URI", None))
            candidate.update({"name": layer.name, "long_name": layer.longName,
                              "definition_query": getattr(layer, "definitionQuery", ""),
                              "selection_oids": sorted(layer.getSelectionSet()) if hasattr(layer, "getSelectionSet") else []})
            candidates.append(candidate)
        except Exception as exc:
            issues.append({"path": getattr(layer, "longName", "未知图层"), "status": "FAIL", "detail": str(exc)})
    return _catalog(candidates, issues, choices)
