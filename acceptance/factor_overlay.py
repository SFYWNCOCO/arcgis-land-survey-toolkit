"""四因素面资料叠加到项目工作副本；未覆盖部分保留，重叠冲突停止。"""
from __future__ import annotations

import os

from .data_intake import FACTOR_FIELDS, field_mapping
from .task_state import _atomic_json
from .terrain import TERRAIN_CLASSES, normalize

CANONICAL_FIELDS = {"landform": "DXBW", "soil_species": "TZMC",
                    "supplement_source": "BCGDLY", "land_use": "DLMC"}


def _touches_extent(a, b):
    return a.XMin <= b.XMax and a.XMax >= b.XMin and a.YMin <= b.YMax and a.YMax >= b.YMin


def overlay_factors(parcels, id_field, out_gdb, sources, output_name="四因素工作图斑",
                    max_parts=100000, terrain_mapping=None):
    import arcpy
    unknown = set(sources) - set(FACTOR_FIELDS)
    if unknown:
        raise ValueError("未知因素角色：" + ",".join(sorted(unknown)))
    output = os.path.join(out_gdb, output_name)
    if arcpy.Exists(output):
        raise FileExistsError("不覆盖已有因素成果：" + output)
    desc = arcpy.Describe(parcels)
    sr = desc.spatialReference
    attributes = [field.name for field in arcpy.ListFields(parcels)
                  if field.type not in ("OID", "Geometry", "Blob", "Raster") and field.editable]
    if id_field not in attributes:
        raise ValueError("因素叠加需要稳定的图斑编号字段，不能使用对象ID")
    if "PART_ID" in attributes or "SRC_DKBH" in attributes:
        raise ValueError("输入已含因素处理关联字段，请使用原始图斑工作副本")
    prepared = {}
    terrain_unmapped = set()
    for role, choice in sources.items():
        path = choice["path"]
        factor_desc = arcpy.Describe(path)
        if factor_desc.shapeType != "Polygon":
            raise ValueError("因素 {} 需要面资料".format(role))
        names = [field.name for field in arcpy.ListFields(path)]
        value_field = choice.get("field") or field_mapping(names).get(role)
        if value_field not in names:
            raise ValueError("因素 {} 未指定可用属性字段".format(role))
        factor_sr = factor_desc.spatialReference
        same_crs = factor_sr.exportToString() == sr.exportToString()
        if not same_crs and (not factor_sr.GCS.factoryCode or factor_sr.GCS.factoryCode != sr.GCS.factoryCode):
            raise ValueError("因素 {} 大地基准不同或未知，请先提供明确转换依据".format(role))
        values = []
        with arcpy.da.SearchCursor(path, ["OID@", value_field, "SHAPE@"]) as cursor:
            for oid, value, geometry in cursor:
                if geometry is None or geometry.area <= 0:
                    raise ValueError("因素 {} OID {} 几何无效".format(role, oid))
                if not same_crs:
                    geometry = geometry.projectAs(sr)
                if role == "landform":
                    value = normalize(value, terrain_mapping or {})
                    if value and value not in TERRAIN_CLASSES:
                        terrain_unmapped.add(str(value))
                values.append((oid, value, geometry))
        prepared[role] = values
    segments, issues = [], []
    original_area = 0.0
    with arcpy.da.SearchCursor(parcels, ["SHAPE@"] + attributes) as cursor:
        for row in cursor:
            geometry, values = row[0], dict(zip(attributes, row[1:]))
            if geometry is None or geometry.area <= 0:
                raise ValueError("图斑存在空几何或零面积")
            pid = str(values[id_field])
            original_area += geometry.area
            pieces = [(geometry, {})]
            for role, factors in prepared.items():
                next_pieces = []
                for piece, assigned in pieces:
                    hits, covered = [], None
                    for oid, value, boundary in factors:
                        if not _touches_extent(piece.extent, boundary.extent):
                            continue
                        clip = piece.intersect(boundary, 4)
                        if clip is None or clip.area <= 1e-8:
                            continue
                        if covered is not None:
                            overlap = covered.intersect(clip, 4)
                            if overlap is not None and overlap.area > max(1e-6, piece.area * 1e-8):
                                raise ValueError("因素 {} 在图斑 {} 的 OID {} 存在重叠冲突，请先核实边界".format(role, pid, oid))
                        covered = clip if covered is None else covered.union(clip)
                        hits.append((clip, dict(assigned, **{role: value})))
                    remaining = piece if covered is None else piece.difference(covered)
                    if remaining is not None and remaining.area > 1e-8:
                        hits.append((remaining, dict(assigned, **{role: None})))
                        issues.append({"parcel": pid, "role": role, "status": "NEEDS_REVIEW",
                                       "detail": "因素资料未覆盖，保留缺项候选", "area_native": remaining.area})
                    next_pieces.extend(hits)
                pieces = next_pieces
                if len(segments) + len(pieces) > max_parts:
                    raise ValueError("叠加片段过多，请核实因素资料范围及碎片")
            for piece, assigned in pieces:
                for part in piece:
                    single = arcpy.Polygon(part, sr)
                    if single.area > 1e-8:
                        segments.append((single, values, pid, assigned))
    total_area = sum(row[0].area for row in segments)
    difference = abs(total_area - original_area)
    if not segments or difference > max(1e-4, original_area * 1e-7):
        raise RuntimeError("因素叠加面积不守恒，停止输出：差 {} 坐标单位²".format(difference))
    created = False
    try:
        arcpy.management.CreateFeatureclass(out_gdb, output_name, "POLYGON", template=parcels, spatial_reference=sr)
        created = True
        for name in ("PART_ID", "SRC_DKBH"):
            arcpy.management.AddField(output, name, "TEXT", field_length=255)
        added = []
        for role in sources:
            name = CANONICAL_FIELDS[role]
            if name not in attributes:
                arcpy.management.AddField(output, name, "TEXT", field_length=255)
                added.append(name)
        write_fields = attributes + ["PART_ID", "SRC_DKBH"] + added
        with arcpy.da.InsertCursor(output, ["SHAPE@"] + write_fields) as cursor:
            for index, (geometry, values, pid, assigned) in enumerate(segments, 1):
                record = dict(values, PART_ID="F{:08d}".format(index), SRC_DKBH=pid)
                record.update({CANONICAL_FIELDS[role]: value for role, value in assigned.items()})
                cursor.insertRow([geometry] + [record.get(name) for name in write_fields])
        terrain_check = None
        if "landform" in sources:
            terrain_check = {"canonical_classes": list(TERRAIN_CLASSES),
                             "mapping_used": bool(terrain_mapping),
                             "unmapped": sorted(terrain_unmapped)}
            if terrain_unmapped:
                issues.append({"role": "landform", "status": "NEEDS_REVIEW",
                               "detail": "地形部位未归并到 3.10 的 12 类：" + "、".join(sorted(terrain_unmapped))})
        result = {"output": output, "id_field": "PART_ID", "source_id_field": "SRC_DKBH", "parts": len(segments),
                  "sources": sources, "issues": issues, "status": "NEEDS_REVIEW",
                  "terrain_check": terrain_check,
                  "geometry_check": {"status": "PASS", "input_area_native": original_area,
                                     "output_area_native": total_area, "area_difference_native": difference}}
        _atomic_json(os.path.join(os.path.dirname(out_gdb), output_name + ".json"), result)
        return result
    except Exception:
        if created and arcpy.Exists(output):
            arcpy.management.Delete(output)
        raise
