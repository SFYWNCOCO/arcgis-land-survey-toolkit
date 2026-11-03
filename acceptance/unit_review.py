"""候选单元编号计划；格式检查不替代系统备案编号来源核实。"""
from __future__ import annotations

import re

PROJECT_NUMBER = re.compile(r"^[A-Z][0-9]{14}$")


def _text_value(value):
    return "" if value is None else str(value).strip()


def plan_sampling_groups(rows, groups):
    """人工设计分组保留源图斑关联；不产生正式评价单元编号或合格结论。"""
    if not isinstance(groups, (list, tuple)):
        raise ValueError("sampling_groups 必须是人工确认分组数组")
    by_id = {}
    for row in rows:
        pid = _text_value(row.get("parcel"))
        if not pid or pid in by_id:
            raise ValueError("原始地块编号为空或重复，不能建立采样分组")
        by_id[pid] = row
    mapping, planned, seen = {}, [], set()
    for group in groups:
        if not isinstance(group, dict) or set(group) - {"group_id", "parcels", "basis", "project"}:
            raise ValueError("采样组只支持 group_id、parcels、basis、project")
        gid = group.get("group_id")
        members, basis = group.get("parcels"), group.get("basis")
        if not isinstance(gid, str) or not re.fullmatch(r"SG_[A-Za-z0-9_]{1,60}", gid) or gid in seen:
            raise ValueError("采样组编号须为唯一SG_开头临时键")
        if not isinstance(members, list) or len(members) < 2 or any(not isinstance(p, str) or not p.strip() for p in members):
            raise ValueError("采样组须提供至少两个文本地块编号")
        members = [p.strip() for p in members]
        if len(set(members)) != len(members) or any(p not in by_id or p in mapping for p in members):
            raise ValueError("采样组含未知、重复或已归入其他组的地块")
        if not isinstance(basis, str) or not basis.strip() or len(basis) > 1000:
            raise ValueError("采样组必须有人工确认依据，长度不超过1000字")
        projects = {_text_value(by_id[p].get("project")) for p in members} - {"", "未填"}
        if len(projects) > 1:
            raise ValueError("采样组存在已知跨项目冲突")
        expected_project = group.get("project")
        if expected_project is not None:
            if not isinstance(expected_project, str) or not expected_project.strip() or any(
                    _text_value(by_id[p].get("project")) != expected_project.strip() for p in members):
                raise ValueError("人工采样组配置与源地块项目归属冲突或缺失")
        factors = {k for p in members for k in (by_id[p].get("factors") or {})}
        for key in factors:
            values = {_text_value((by_id[p].get("factors") or {}).get(key)) for p in members} - {"", "未填"}
            if len(values) > 1:
                raise ValueError("采样组存在已知因素冲突：" + key)
        seen.add(gid)
        mapping.update({p: gid for p in members})
        planned.append({"group_id": gid, "parcels": members, "basis": basis.strip(),
                        "project": expected_project, "min_points": max(3, len(members)), "status": "NEEDS_REVIEW"})
    for pid in sorted(set(by_id) - set(mapping)):
        gid = "P_" + pid
        if len(gid) > 254:
            raise ValueError("地块编号过长，无法保存采样组关联")
        mapping[pid] = gid
        planned.append({"group_id": gid, "parcels": [pid], "basis": "未人工合组，保留原地块", "min_points": 3,
                        "status": "NEEDS_REVIEW"})
    return {"mapping": mapping, "groups": planned, "design_points": sum(g["min_points"] for g in planned),
            "formal_unit_mapping": {}, "status": "NEEDS_REVIEW",
            "note": "仅人工采样设计组；正式单元、同质性、面积与集中性须另行复核。"}


def apply_sampling_groups(arcpy, parcels, id_field, groups, max_mu=50.0, gap_m=30.0, factor_fields=()):
    """仅对调用方已复制的工作图层新增关联字段，不改源边界/源编号/正式号。"""
    from .data_intake import field_mapping, FACTOR_FIELDS
    names = [f.name for f in arcpy.ListFields(parcels)]
    target = "SAMP_GRP"
    if target.lower() in {name.lower() for name in names}:
        raise ValueError("工作图层已存在SAMP_GRP，拒绝覆盖旧关联")
    fields = field_mapping(names)
    roles = [r for r in FACTOR_FIELDS if r in fields]
    extra = list(dict.fromkeys(factor_fields or []))
    if any(name not in names for name in extra):
        raise ValueError("指定的采样分组因素字段不存在")
    selected = list(dict.fromkeys([id_field] + [fields[r] for r in roles] +
                                  ([fields["project"]] if "project" in fields else []) + extra))
    rows, shapes = [], {}
    with arcpy.da.SearchCursor(parcels, selected + ["SHAPE@"]) as cursor:
        for values in cursor:
            record = dict(zip(selected, values[:-1]))
            pid = _text_value(record[id_field])
            shapes[pid] = values[-1]
            rows.append({"parcel": pid, "project": record.get(fields.get("project")),
                         "factors": dict({r: record[fields[r]] for r in roles},
                                         **{"field:" + name: record[name] for name in extra})})
    plan = plan_sampling_groups(rows, groups)
    from .sampling import _projected_meters_per_unit
    meters = _projected_meters_per_unit(arcpy.Describe(parcels).spatialReference)
    for group in plan["groups"]:
        geometries = [shapes[p] for p in group["parcels"]]
        if any(g is None or g.area <= 0 for g in geometries):
            raise ValueError("采样组含空几何或零面积地块")
        group["area_mu"] = sum(g.getArea("GEODESIC", "SQUAREMETERS") for g in geometries) / (10000 / 15)
        group["max_pair_distance_m"] = max([a.distanceTo(b) * meters for i, a in enumerate(geometries)
                                            for b in geometries[i + 1:]] or [0])
        group["review_items"] = ["正式评价单元及四因素同质性"]
        if group["area_mu"] > max_mu:
            group["review_items"].append("面积超过项目原则值，需复核依据")
        if group["max_pair_distance_m"] > gap_m:
            group["review_items"].append("间距超过自动合并距离，需复核集中性")
    arcpy.management.AddField(parcels, target, "TEXT", field_length=254)
    with arcpy.da.UpdateCursor(parcels, [id_field, target]) as cursor:
        for row in cursor:
            row[1] = plan["mapping"][str(row[0]).strip()]
            cursor.updateRow(row)
    return target, plan


def plan_unit_numbers(rows, project_override=None):
    projects = {str(row.get("project") or "").strip() for row in rows} - {"", "未填", "None"}
    if project_override and not PROJECT_NUMBER.fullmatch(project_override):
        raise ValueError("系统项目编号应为15位（方式字母+14位数字）；请复制系统验收备案编号，不得自编")
    if project_override and len(projects) > 1:
        raise ValueError("当前包含多个项目，不能统一覆盖为同一项目编号；请分项目准备或提供逐项目编号")
    mapping, counters, missing = {}, {}, []
    for row in sorted(rows, key=lambda item: str(item["unit"])):
        uid = str(row["unit"])
        project = project_override or str(row.get("project") or "")
        if not PROJECT_NUMBER.fullmatch(project):
            if re.fullmatch(r"[A-Z][0-9]{14}E[0-9]{4}", uid):
                project = uid[:15]
            else:
                missing.append(uid)
                continue
        if uid.startswith(project + "E") and re.fullmatch(r"[A-Z][0-9]{14}E[0-9]{4}", uid):
            mapping[uid] = uid
            counters[project] = max(counters.get(project, 0), int(uid[-4:]))
    for row in sorted(rows, key=lambda item: str(item["unit"])):
        uid = str(row["unit"])
        if uid in mapping or uid in missing:
            continue
        project = project_override or str(row.get("project") or "")
        counters[project] = counters.get(project, 0) + 1
        if counters[project] > 9999:
            raise ValueError("单项目评价单元超过四位顺序号容量")
        mapping[uid] = project + "E{:04d}".format(counters[project])
    # 不发布一半编号完成、一半仍是候选的批次。
    if missing:
        mapping = {}
    return {"mapping": mapping, "missing_project_units": missing, "status": "NEEDS_REVIEW",
            "sample_ids": {old: {"mixed": new, "bulk": "RZ" + new} for old, new in mapping.items()},
            "note": "仅核编号结构；项目编号是否为系统验收备案编号仍需操作者核对。"}


def apply_unit_numbers(data, planned, arcpy):
    mapping = planned["mapping"]
    if not mapping:
        return
    field = data["unit_field"]
    for source in dict.fromkeys(path for path in (data["assign"], data.get("units_fc")) if path):
        definition = next(item for item in arcpy.ListFields(source) if item.name == field)
        if definition.type != "String":
            raise ValueError("评价单元编号必须为文本字段")
        if definition.length < 20:
            arcpy.management.AlterField(source, field, field_length=30)
        with arcpy.da.UpdateCursor(source, [field]) as cursor:
            for row in cursor:
                cursor.updateRow([mapping[str(row[0])]])
    for row in data["unit_rows"]:
        row["candidate_unit"] = row["unit"]
        row["unit"] = mapping[str(row["unit"])]
    for row in (data.get("unit_design") or {}).get("detail", []):
        row["候选单元"] = row["单元"]
        row["单元"] = mapping[str(row["单元"])]
