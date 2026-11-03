"""Read-only parcel audit, copy-first preparation and isolated map export."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import gc
import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

AREA_UNITS = {"平方米": 1.0, "公顷": 10000.0, "亩": 10000.0 / 15.0}
AREA_METHODS = {"椭球面积": "GEODESIC", "平面面积": "PLANAR"}


def _arcpy():
    import arcpy
    return arcpy


def _polygon(source):
    arcpy = _arcpy()
    d = arcpy.Describe(source)
    if d.shapeType != "Polygon":
        raise ValueError("请选择面图层。")
    if not d.spatialReference or d.spatialReference.name == "Unknown":
        raise ValueError("坐标系未知，不能计算面积或投影。")
    if int(arcpy.management.GetCount(source)[0]) == 0:
        raise ValueError("输入为空或当前选择集没有要素。")
    return d


def _field(source, name):
    matches = [f for f in _arcpy().ListFields(source) if f.name.lower() == str(name).lower()]
    if not matches:
        raise ValueError("字段不存在：" + str(name))
    return matches[0]


def area_m2(geometry, method="椭球面积"):
    if method not in AREA_METHODS:
        raise ValueError("面积方法必须是椭球面积或平面面积。")
    if geometry is None or geometry.pointCount == 0:
        raise ValueError("空几何无法量算。")
    if method == "平面面积" and geometry.spatialReference.type != "Projected":
        raise ValueError("平面面积要求投影坐标系。")
    area = float(geometry.getArea(AREA_METHODS[method], "SQUAREMETERS"))
    if not math.isfinite(area) or area <= 0:
        raise ValueError("几何面积必须为有限正数。")
    return area


def _fingerprint(source):
    p = Path(_arcpy().Describe(source).catalogPath)
    if p.suffix.lower() != ".shp":
        return {"catalog_path": str(p)}
    return {str(p.with_suffix(ext)): hashlib.sha256(p.with_suffix(ext).read_bytes()).hexdigest()
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg") if p.with_suffix(ext).is_file()}


def _new_folder(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def _json(path, data):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def _csv(path, fields, rows):
    with Path(path).open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def audit_parcels(source, id_field, out_folder, required_fields=(), area_field=None,
                  area_unit="公顷", method="椭球面积", tolerance_m2=0.5,
                  overlap_tolerance_m2=0.01):
    arcpy = _arcpy()
    desc = _polygon(source)
    id_field = _field(source, id_field).name
    required = [_field(source, f).name for f in required_fields]
    area_field = _field(source, area_field).name if area_field else None
    if area_unit not in AREA_UNITS or method not in AREA_METHODS:
        raise ValueError("面积单位或方法无效。")
    for n in (tolerance_m2, overlap_tolerance_m2):
        if not math.isfinite(float(n)) or float(n) < 0:
            raise ValueError("面积容差必须为有限非负数。")
    if method == "平面面积" and desc.spatialReference.type != "Projected":
        raise ValueError("平面面积要求投影坐标系。")
    original = _fingerprint(source)
    names = list(dict.fromkeys([id_field] + required + ([area_field] if area_field else [])))
    rows = []
    with arcpy.da.SearchCursor(source, ["OID@", "SHAPE@"] + names) as cur:
        for oid, geom, *values in cur:
            rows.append({"oid": oid, "geom": geom, **dict(zip(names, values))})
    issues = []

    def add(row, code, detail, peer=None):
        issues.append({"源OID": row["oid"], "地块编号": "" if row[id_field] is None else str(row[id_field]),
                       "问题类型": code, "说明": detail, "关联OID": peer})

    ids = Counter(str(row[id_field]).strip() for row in rows if row[id_field] is not None and str(row[id_field]).strip())
    check_table = "memory/parcel_geom_" + uuid.uuid4().hex
    invalid_ids = set()
    try:
        arcpy.management.CheckGeometry(source, check_table, "ESRI")
        with arcpy.da.SearchCursor(check_table, ["FEATURE_ID", "PROBLEM"]) as cur:
            by_oid = {r["oid"]: r for r in rows}
            for oid, problem in cur:
                if oid in by_oid:
                    add(by_oid[oid], "几何错误", str(problem))
                    invalid_ids.add(oid)
    finally:
        if arcpy.Exists(check_table):
            arcpy.management.Delete(check_table)
    measurements = []
    for row in rows:
        key = "" if row[id_field] is None else str(row[id_field]).strip()
        if not key:
            add(row, "编号为空", id_field)
        elif ids[key] > 1:
            add(row, "编号重复", "{} 出现 {} 次".format(key, ids[key]))
        for name in required:
            if row[name] is None or (isinstance(row[name], str) and not row[name].strip()):
                add(row, "必填字段为空", name)
        if row["oid"] in invalid_ids:
            continue
        try:
            actual = area_m2(row["geom"], method)
            measure = {"源OID": row["oid"], "地块编号": key, "面积方法": method,
                       "几何平方米": actual, "属性平方米": None, "属性减几何平方米": None}
            if area_field:
                try:
                    value = float(row[area_field]) * AREA_UNITS[area_unit]
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError("面积非有限正数")
                    delta = value - actual
                    measure.update({"属性平方米": value, "属性减几何平方米": delta})
                    if abs(delta) > tolerance_m2:
                        add(row, "面积不一致", "{}：属性{:.3f}m²，几何{:.3f}m²，差{:+.3f}m²，容差{}m²".format(method, value, actual, delta, tolerance_m2))
                except (TypeError, ValueError):
                    add(row, "属性面积无效", area_field)
            measurements.append(measure)
        except ValueError as exc:
            add(row, "面积无法计算", str(exc))
    # Sweep on X excludes disjoint bounding boxes before geometry operations.
    valid = sorted((r for r in rows if r["oid"] not in invalid_ids and r["geom"]), key=lambda r: r["geom"].extent.XMin)
    active = []
    comparisons = 0
    for row in valid:
        box = row["geom"].extent
        active = [other for other in active if other["geom"].extent.XMax > box.XMin]
        for other in active:
            b = other["geom"].extent
            if b.YMax <= box.YMin or b.YMin >= box.YMax:
                continue
            comparisons += 1
            if comparisons > 100000:
                raise ValueError("重叠候选超过10万对，请按区域分批质检。未生成通过报告。")
            intersection = row["geom"].intersect(other["geom"], 4)
            overlap = area_m2(intersection, method) if intersection and intersection.pointCount else 0.0
            if overlap > overlap_tolerance_m2:
                for a, b_row in ((row, other), (other, row)):
                    add(a, "面间重叠", "与OID {}重叠{:.3f}m²".format(b_row["oid"], overlap), b_row["oid"])
        active.append(row)
    if _fingerprint(source) != original:
        raise RuntimeError("读取期间源文件发生变化，请关闭编辑后重新质检。")
    out = _new_folder(out_folder)
    arcpy.management.CreateFileGDB(str(out), "quality.gdb")
    anomaly_fc = str(out / "quality.gdb" / "异常地块")
    arcpy.management.CreateFeatureclass(str(out / "quality.gdb"), "异常地块", "POLYGON", spatial_reference=desc.spatialReference)
    for name, typ, length in [("SRC_OID", "LONG", None), ("PARCEL_ID", "TEXT", 254), ("ISSUES", "TEXT", 2000)]:
        arcpy.management.AddField(anomaly_fc, name, typ, field_length=length)
    grouped = {}
    for issue in issues:
        grouped.setdefault(issue["源OID"], []).append(issue["问题类型"] + ":" + issue["说明"])
    with arcpy.da.InsertCursor(anomaly_fc, ["SHAPE@", "SRC_OID", "PARCEL_ID", "ISSUES"]) as cur:
        for row in rows:
            if row["oid"] in grouped:
                cur.insertRow([row["geom"], row["oid"], ("" if row[id_field] is None else str(row[id_field]))[:254], "；".join(grouped[row["oid"]])[:2000]])
    _csv(out / "异常清单.csv", ["源OID", "地块编号", "问题类型", "说明", "关联OID"], issues)
    _csv(out / "面积核算.csv", ["源OID", "地块编号", "面积方法", "几何平方米", "属性平方米", "属性减几何平方米"], measurements)
    report = {"created_at": dt.datetime.now().isoformat(timespec="seconds"), "source": original,
              "feature_count": len(rows), "issue_count": len(issues), "anomaly_count": len(grouped),
              "method": method, "area_unit": area_unit, "area_field": area_field,
              "tolerance_m2": tolerance_m2, "required_fields": required,
              "status": "ISSUES_FOUND" if issues else "NO_ISSUES_IN_CHECKED_SCOPE",
              "scope": "仅几何/重叠/编号/指定必填字段/指定面积口径，不代表项目验收合格。",
              "issues": issues, "anomaly_fc": anomaly_fc}
    _json(out / "质检报告.json", report)
    return report


def _rules(path):
    if not path:
        return {"copy": [], "set": []}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) - {"copy", "set"}:
        raise ValueError("字段规则只支持 copy 和 set 数组。")
    for key in ("copy", "set"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(key + " 必须是数组。")
    return {"copy": data.get("copy", []), "set": data.get("set", [])}


def prepare_parcels(source, out_folder, target_sr=None, transformation=None, rules_path=None):
    arcpy = _arcpy()
    desc = _polygon(source)
    original = _fingerprint(source)
    rules = _rules(rules_path)
    target_sr = target_sr or desc.spatialReference
    if not getattr(target_sr, "name", None) or target_sr.name == "Unknown":
        raise ValueError("目标坐标系未知。")
    candidates = arcpy.ListTransformations(desc.spatialReference, target_sr)
    if transformation and transformation not in candidates:
        raise ValueError("所填基准转换不在当前源/目标坐标系可用列表中。")
    if desc.spatialReference.GCS.name != target_sr.GCS.name and not transformation:
        raise ValueError("源/目标地理基准不同，请指定经确认的基准转换；不自动挑选。")
    out = _new_folder(out_folder)
    fc = str(out / "work.gdb" / "整理地块")
    try:
        arcpy.management.CreateFileGDB(str(out), "work.gdb")
        with arcpy.EnvManager(overwriteOutput=False, outputCoordinateSystem=desc.spatialReference):
            if desc.spatialReference.exportToString().split(";")[0] == target_sr.exportToString().split(";")[0]:
                arcpy.management.CopyFeatures(source, fc)
            else:
                arcpy.management.Project(source, fc, target_sr, transformation or "")
        if int(arcpy.management.GetCount(source)[0]) != int(arcpy.management.GetCount(fc)[0]):
            raise RuntimeError("整理前后要素数量不一致。")
        existing = {f.name.lower(): f for f in arcpy.ListFields(fc)}
        new_names = set()

        def add_field(name, kind, length=None):
            if not name or arcpy.ValidateFieldName(name, str(out / "work.gdb")) != name:
                raise ValueError("目标字段名不合法：" + str(name))
            if name.lower() in existing or name.lower() in new_names:
                raise ValueError("目标字段已存在，不覆盖：" + name)
            arcpy.management.AddField(fc, name, kind, field_length=length)
            new_names.add(name.lower())

        operations = []
        for rule in rules["copy"]:
            src = _field(fc, rule["source"])
            typ = {"String": "TEXT", "Integer": "LONG", "SmallInteger": "SHORT", "Double": "DOUBLE", "Single": "FLOAT", "Date": "DATE"}.get(src.type)
            if not typ:
                raise ValueError("不支持复制字段类型：" + src.type)
            add_field(rule["target"], typ, src.length if typ == "TEXT" else None)
            operations.append((src.name, rule["target"]))
        for rule in rules["set"]:
            kind = rule.get("type", "TEXT").upper()
            if kind not in {"TEXT", "LONG", "DOUBLE"}:
                raise ValueError("赋值字段仅支持 TEXT、LONG、DOUBLE。")
            value = rule.get("value")
            length = int(rule.get("length", 254)) if kind == "TEXT" else None
            if kind == "TEXT" and (length < 1 or length > 4000 or (value is not None and len(str(value)) > length)):
                raise ValueError("文本值超过字段长度，或长度不在1–4000。")
            if kind in {"LONG", "DOUBLE"} and value is not None:
                value = float(value)
                if not math.isfinite(value) or (kind == "LONG" and (not value.is_integer() or not -2147483648 <= value <= 2147483647)):
                    raise ValueError("字段赋值数值越界。")
            add_field(rule["field"], kind, length)
            rule["value"] = value
        can_planar = target_sr.type == "Projected"
        computed = [("PT_G_M2", "椭球面积", 1), ("PT_G_HA", "椭球面积", 10000), ("PT_G_MU", "椭球面积", 10000 / 15)]
        if can_planar:
            computed += [("PT_P_M2", "平面面积", 1), ("PT_P_HA", "平面面积", 10000), ("PT_P_MU", "平面面积", 10000 / 15)]
        for name, _, _ in computed:
            add_field(name, "DOUBLE")
        fields = list(dict.fromkeys(["SHAPE@"] + [f for pair in operations for f in pair] + [r["field"] for r in rules["set"]] + [c[0] for c in computed]))
        with arcpy.da.UpdateCursor(fc, fields) as cur:
            for row in cur:
                for src, dst in operations:
                    row[fields.index(dst)] = row[fields.index(src)]
                for rule in rules["set"]:
                    row[fields.index(rule["field"])] = rule["value"]
                measures = {m: area_m2(row[0], m) for m in set(c[1] for c in computed)}
                for name, method, denominator in computed:
                    row[fields.index(name)] = measures[method] / denominator
                cur.updateRow(row)
        if _fingerprint(source) != original:
            raise RuntimeError("源文件在整理期间发生变化，请重新执行。")
        report = {"output": fc, "source": original, "count": int(arcpy.management.GetCount(fc)[0]),
                  "crs": target_sr.name, "transformation": transformation, "computed_fields": [c[0] for c in computed],
                  "field_rules": rules, "planar_available": can_planar}
        _json(out / "整理记录.json", report)
        return report
    except Exception:
        # The entire output directory was exclusively created by this call;
        # remove even a partially created feature class after Copy/Project fails.
        if arcpy.Exists(fc):
            arcpy.management.Delete(fc)
        raise


def _text_element(name, text, x, y, size=8):
    return {"type": "CIMGraphicElement", "name": name, "visible": True, "anchor": "BottomLeftCorner",
            "graphic": {"type": "CIMTextGraphic", "text": text, "shape": {"x": x, "y": y},
                        "symbol": {"type": "CIMSymbolReference", "symbol": {"type": "CIMTextSymbol",
                            "fontFamilyName": "SimSun", "fontStyleName": "Regular", "height": size,
                            "horizontalAlignment": "Left", "verticalAlignment": "Bottom",
                            "symbol": {"type": "CIMPolygonSymbol", "symbolLayers": [{"type": "CIMSolidFill", "enable": True,
                                "color": {"type": "CIMRGBColor", "values": [0, 0, 0, 100]}}]}}}}}


def _map_template(arcpy, destination, custom=None):
    source = Path(custom) if custom else Path(arcpy.GetInstallInfo()["InstallDir"]) / "Resources/ArcToolBox/Templates/ExportWebMapTemplates/A4 Landscape.pagx"
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    layout = data["layoutDefinition"]
    if not custom:
        for element in layout["elements"]:
            name = element.get("name")
            if name in {"Author", "Credits", "{Date}"}:
                element["visible"] = False
            if name == "North Arrow":
                element["visible"] = True
            if name == "Scale bar":
                element["elements"] = [x for x in element.get("elements", []) if x.get("name") != "Scale Line"]
                for scale in element["elements"]:
                    if scale.get("type") == "CIMScaleLine":
                        scale["units"] = {"uwkid": 9001}
                        scale["unitLabel"] = "m"
                        scale["numberFormat"]["roundingValue"] = 0
        layout["elements"].append(_text_element("PT_META", "地块信息", 1.0, 0.6, 8))
    _json(destination, data)


def _safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:60]
    return value or "未命名"


def _export_maps_worker(source, id_field, out_folder, project, map_name, template=None, title="地块核查图", author="", dpi=300, provenance=None):
    """project is an ArcGISProject; only its temporary saved copy is modified."""
    arcpy = _arcpy()
    desc = _polygon(source)
    id_field = _field(source, id_field).name
    if not 150 <= int(dpi) <= 600:
        raise ValueError("输出分辨率应在150–600 DPI。")
    original = _fingerprint(source)
    out = _new_folder(out_folder)
    temporary = out / "temp"
    temporary.mkdir()
    outputs = []
    try:
        # Pro 3.1 can retain APRX handles until process exit. The parent removes
        # this private scratch directory only after the worker has terminated.
        with contextlib.nullcontext(tempfile.mkdtemp(prefix="maps_", dir=str(temporary))) as workdir:
            work = Path(workdir)
            snapshot = work / "parcels.gdb"
            arcpy.management.CreateFileGDB(str(work), snapshot.name)
            fc = str(snapshot / "地块")
            with arcpy.EnvManager(outputCoordinateSystem=desc.spatialReference, overwriteOutput=False):
                arcpy.management.CopyFeatures(source, fc)
            with arcpy.da.SearchCursor(fc, ["OID@", id_field, "SHAPE@"]) as cur:
                parcels = [(oid, str(pid).strip() if pid is not None else "", geom) for oid, pid, geom in cur]
            ids = [r[1] for r in parcels]
            if any(not key for key in ids) or len(set(ids)) != len(ids):
                raise ValueError("制图编号为空或重复，请先质检并选用唯一编号字段。")
            copy_path = str(work / "map_export.aprx")
            project.saveACopy(copy_path)
            aprx = arcpy.mp.ArcGISProject(copy_path)
            maps = [m for m in aprx.listMaps() if m.name == map_name]
            if len(maps) != 1:
                raise ValueError("当前地图名称不唯一或不存在，不能确定制图地图。")
            map_obj = maps[0]
            map_obj.spatialReference = desc.spatialReference
            layer = None
            label = None
            legend = None
            original_paths = {os.path.normcase(os.path.normpath(str(desc.catalogPath)))}
            if provenance:
                original_paths.update(os.path.normcase(os.path.normpath(p)) for p in provenance if p.lower().endswith(".shp"))
                if provenance.get("catalog_path"):
                    original_paths.add(os.path.normcase(os.path.normpath(provenance["catalog_path"])))
            for layer in map_obj.listLayers():
                if layer.isFeatureLayer and layer.supports("DATASOURCE") and os.path.normcase(os.path.normpath(str(layer.dataSource))) in original_paths:
                    layer.visible = False
            target = map_obj.addDataFromPath(fc)
            target.name = "地块边界"
            sym = target.symbology
            if hasattr(sym, "renderer") and hasattr(sym.renderer, "symbol"):
                sym.renderer.symbol.color = {"RGB": [255, 215, 0, 35]}
                sym.renderer.symbol.outlineColor = {"RGB": [220, 0, 0, 100]}
                sym.renderer.symbol.outlineWidth = 1.5
                target.symbology = sym
            if target.supports("SHOWLABELS") and target.listLabelClasses():
                label = target.listLabelClasses()[0]
                label.expression = "$feature." + id_field
                label.expressionEngine = "Arcade"
                label.visible = True
                target.showLabels = True
            template_path = work / "parcel.pagx"
            _map_template(arcpy, template_path, template)
            imported = aprx.importDocument(str(template_path))
            layout = imported if hasattr(imported, "listElements") else aprx.listLayouts()[-1]
            frames = layout.listElements("MAPFRAME_ELEMENT")
            if len(frames) != 1:
                raise ValueError("模板必须恰有一个地图框。")
            frame = frames[0]
            frame.map = map_obj
            for legend in layout.listElements("LEGEND_ELEMENT"):
                legend.mapFrame = frame
            titles = layout.listElements("TEXT_ELEMENT", "Title")
            metadata = layout.listElements("TEXT_ELEMENT", "PT_META")
            if not titles or not metadata:
                raise ValueError("模板须含 Title 和 PT_META 文本元素。")
            oid_field = arcpy.Describe(fc).OIDFieldName
            total = sum(area_m2(r[2]) for r in parcels)
            jobs = [("总览", None, total)] + [(pid, oid, area_m2(geom)) for oid, pid, geom in parcels]
            for index, (pid, oid, area) in enumerate(jobs):
                target.definitionQuery = "" if oid is None else "{} = {}".format(arcpy.AddFieldDelimiters(fc, oid_field), oid)
                frame.camera.setExtent(frame.getLayerExtent(target, False, True))
                frame.camera.scale *= 1.2
                titles[0].text = title + " — " + ("总览" if oid is None else "地块 " + pid)
                metadata[0].text = "{}　椭球面积：{:.4f}公顷　坐标系：{}\n编制：{}　日期：{}　数据为用户项目资料".format(
                    "共{}块".format(len(parcels)) if oid is None else "编号：" + pid, area / 10000,
                    desc.spatialReference.name, author or "未填写", dt.date.today().isoformat())
                pdf = out / ("{:03d}_{}.pdf".format(index, _safe_name(pid)))
                layout.exportToPDF(str(pdf), resolution=int(dpi), embed_fonts=True)
                if not pdf.is_file() or pdf.stat().st_size < 100:
                    raise RuntimeError("PDF导出为空：" + str(pdf))
                outputs.append(str(pdf))
            # Release project/data handles before TemporaryDirectory removes its files.
            del titles, metadata, sym, label, legend, layer
            del layout, frames, frame, target, map_obj, maps, aprx, imported
            gc.collect()
            arcpy.management.ClearWorkspaceCache()
        if _fingerprint(source) != original:
            raise RuntimeError("制图期间源数据发生变化。")
        result = {"status": "COMPLETE", "source": provenance or original, "map_name": map_name,
                  "parcel_count": len(parcels), "pdf_count": len(outputs), "outputs": outputs,
                  "area_method": "GEODESIC", "dpi": int(dpi)}
        _json(out / "制图记录.json", result)
        return result
    except Exception as exc:
        _json(out / "制图失败.json", {"status": "FAILED", "reason": str(exc), "partial_pdfs": outputs})
        raise
    finally:
        if temporary.is_dir() and not any(temporary.iterdir()):
            temporary.rmdir()


def export_parcel_maps(source, id_field, out_folder, project, map_name, template=None, title="地块核查图", author="", dpi=300):
    arcpy = _arcpy()
    desc = _polygon(source)
    id_field = _field(source, id_field).name
    out = Path(out_folder).resolve()
    if out.exists():
        raise FileExistsError("成果目录已存在，不覆盖：" + str(out))
    if not 150 <= int(dpi) <= 600:
        raise ValueError("输出分辨率应在150–600 DPI。")
    provenance = _fingerprint(source)
    count = int(arcpy.management.GetCount(source)[0])
    scratch_parent = out.parent / "temp"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="parcel_job_", dir=str(scratch_parent))).resolve()
    if scratch.parent != scratch_parent.resolve():
        raise RuntimeError("临时目录越界。")
    arcpy.management.CreateFileGDB(str(scratch), "selection.gdb")
    snapshot = str(scratch / "selection.gdb" / "parcels")
    with arcpy.EnvManager(outputCoordinateSystem=desc.spatialReference, overwriteOutput=False):
        arcpy.management.CopyFeatures(source, snapshot)
    copy_path = str(scratch / "current.aprx")
    project.saveACopy(copy_path)
    job = {"source": snapshot, "id_field": id_field, "out_folder": str(out), "project": copy_path,
           "map_name": map_name, "template": str(Path(template).resolve()) if template else None,
           "title": title, "author": author, "dpi": int(dpi), "provenance": provenance}
    job_path = scratch / "job.json"
    _json(job_path, job)
    python = Path(arcpy.GetInstallInfo()["InstallDir"]) / "bin/Python/envs/arcgispro-py3/python.exe"
    if not python.is_file():
        raise RuntimeError("找不到 ArcGIS Pro Python 工作进程：" + str(python))
    log_path = scratch / "worker.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run([str(python), str(Path(__file__).resolve()), "--map-job", str(job_path)],
                              stdout=log, stderr=subprocess.STDOUT, timeout=min(3600, max(300, count * 60)),
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    manifest = out / "制图记录.json"
    if proc.returncode != 0 or not manifest.is_file():
        failure = out / "制图失败.json"
        reason = json.loads(failure.read_text(encoding="utf-8")).get("reason", "") if failure.is_file() else "工作进程退出码 " + str(proc.returncode)
        raise RuntimeError("制图失败：{}；诊断日志：{}".format(reason, log_path))
    result = json.loads(manifest.read_text(encoding="utf-8"))
    if result.get("status") != "COMPLETE" or result.get("pdf_count") != count + 1:
        raise RuntimeError("制图记录与输入要素数不一致：" + str(manifest))
    for pdf in result["outputs"]:
        path = Path(pdf).resolve()
        if path.parent != out or not path.is_file() or path.stat().st_size < 100:
            raise RuntimeError("PDF缺失或路径不正确：" + str(path))
    if _fingerprint(source) != provenance:
        raise RuntimeError("制图期间源数据发生变化，需重新检查成果。")
    shutil.copyfile(log_path, out / "制图日志.txt")
    pending = []
    gc.collect()
    arcpy.management.ClearWorkspaceCache()
    for path, parent in ((out / "temp", out), (scratch, scratch_parent.resolve())):
        if path.resolve().parent != parent.resolve():
            raise RuntimeError("拒绝清理范围外目录。")
        try:
            if path.is_dir():
                shutil.rmtree(path)
        except OSError as exc:
            pending.append(str(path))
            arcpy.AddWarning("PDF已完成；临时文件被ArcGIS占用，关闭会话后可清理：{} ({})".format(path, exc))
    if pending:
        result["cleanup_pending"] = pending
        # This is this invocation's own newly created manifest, not source data.
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if scratch_parent.is_dir() and not any(scratch_parent.iterdir()):
        scratch_parent.rmdir()
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--map-job":
        raise SystemExit("Internal map worker requires --map-job <job.json>")
    job = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    job["project"] = _arcpy().mp.ArcGISProject(job["project"])
    _export_maps_worker(**job)
