"""大范围项目的索引与主分幅几何计划；比例尺为表达建议。"""
from __future__ import annotations

import math
import re
import json
import copy


def map_scale_key(target):
    """Bind a scale choice to kind and original parcels, independent of sheet numbering."""
    return target['kind'] + ':' + json.dumps(sorted(str(p) for p in target['parcel_ids']),
                                             ensure_ascii=False, separators=(',', ':'))


def validate_map_scales(values):
    if not isinstance(values, dict) or any(not isinstance(k, str) or not k.strip() or
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or
            not 1 <= v <= 10000000 for k, v in values.items()):
        raise ValueError('逐幅比例尺须为1至10000000的有限数值')
    return values


def apply_map_scales(targets, overrides, frame_width_cm=27, frame_height_cm=15.4, meters_per_unit=1):
    """Apply explicit denominators before imagery acquisition; reject clipped parcel bounds."""
    validate_map_scales(overrides)
    result = copy.deepcopy(targets)
    width, height = frame_width_cm * .01 / meters_per_unit, frame_height_cm * .01 / meters_per_unit
    for target in result:
        key = target['scale_key'] = map_scale_key(target)
        target['automatic_scale'] = target['scale']
        if key not in overrides:
            continue
        scale = overrides[key]
        x0, y0, x1, y1 = target['extent']
        required = max((x1-x0)/width, (y1-y0)/height) * 1.12
        if scale < required and not target.get('partial_coverage'):
            raise ValueError('{}：1:{}会裁切地块；比例尺分母至少为{}'.format(
                target['name'], scale, math.ceil(required)))
        if target.get('partial_coverage') and scale != target['scale']:
            raise ValueError('总览分幅须保持规划比例尺，请调整总览比例尺后重新分幅')
        cx, cy = (x0+x1)/2, (y0+y1)/2
        target.update(scale=scale, frame_extent=[cx-width*scale*.51, cy-height*scale*.51,
                                                cx+width*scale*.51, cy+height*scale*.51])
    for target in result:
        target['scale_recommendation'] = 'NEEDS_REVIEW' if target['kind'] in ('sample','sheet') and target['scale'] > 10000 else 'PASS'
    return result


def plan_atlas(parcel_rows, frame_width_cm=27.0, frame_height_cm=15.4, meters_per_unit=1, project_rules=None):
    """Plan location regions and one sampling sheet per original parcel, before acquiring imagery."""
    if not parcel_rows:
        return []
    if not all(math.isfinite(float(x)) and x > 0
               for x in (frame_width_cm, frame_height_cm, meters_per_unit)):
        raise ValueError("图框尺寸或坐标单位无效")
    if len(parcel_rows) > 1000:
        raise ValueError("本次图册超过1000地块，请分批处理")
    seen = set()
    for row in parcel_rows:
        pid = str(row["parcel"])
        if not pid.strip() or pid in seen:
            raise ValueError("原始地块编号为空或重复：" + pid)
        seen.add(pid)
        box = row["bbox_native"]
        if len(box) != 4 or not all(math.isfinite(float(x)) for x in box) or box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("地块范围无效：" + pid)
        if not row.get("parcel_ids") or not row.get("unit_ids"):
            raise ValueError("地块缺少制图与单元关联：" + pid)
    natural = lambda row: tuple((0, int(v)) if v.isdigit() else (1, v)
                               for v in re.split(r"(\d+)", str(row["parcel"])))
    rows = sorted(parcel_rows, key=natural)
    rules = project_rules or {}
    index_scale = float(rules.get('index_scale', 25000))
    sample_min_scale = float(rules.get('sample_min_scale', 2000))
    if not all(math.isfinite(v) and v > 0 for v in (index_scale, sample_min_scale)):
        raise ValueError('项目比例尺必须为正数')
    width = frame_width_cm * .01 / meters_per_unit
    height = frame_height_cm * .01 / meters_per_unit

    def bounds(items):
        return [min(r["bbox_native"][0] for r in items), min(r["bbox_native"][1] for r in items),
                max(r["bbox_native"][2] for r in items), max(r["bbox_native"][3] for r in items)]

    def required(box):
        return max((box[2] - box[0]) / width, (box[3] - box[1]) / height) * 1.12

    def target(items, kind, name, minimum, parcel=None, locked_scale=None, box=None):
        box = box or bounds(items)
        needed = max(minimum, required(box))
        scales = (1000, 1500, 2000, 2500, 3000, 5000, 10000, 15000, 20000, 25000, 50000,
                  100000, 200000, 250000, 500000, 1000000)
        scale = next((s for s in scales if s >= needed), math.ceil(needed / 100000) * 100000)
        if locked_scale is not None:
            scale = locked_scale
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return {"kind": kind, "name": name, "unit": None, "parcel": parcel,
                "source_parcels": [str(r["parcel"]) for r in sorted(items, key=natural)],
                "parcel_ids": sorted({str(p) for r in items for p in r["parcel_ids"]}),
                "unit_ids": sorted({str(u) for r in items for u in r["unit_ids"]}),
                "area_mu": sum(float(r.get("mu") or 0) for r in items),
                "extent": box, "scale": scale,
                "frame_extent": [cx - width * scale * .51, cy - height * scale * .51,
                                 cx + width * scale * .51, cy + height * scale * .51],
                "label_mode": "source_parcel"}

    remaining = sorted(rows, key=lambda r: (-r["bbox_native"][3], r["bbox_native"][0], natural(r)))
    regions = []
    while remaining:
        group = [remaining.pop(0)]
        while remaining:
            box = bounds(group)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            fitting = [r for r in remaining if required(bounds(group + [r])) <= 10000]
            if not fitting:
                break
            closest = min(fitting, key=lambda r: (((r["bbox_native"][0] + r["bbox_native"][2]) / 2 - cx) ** 2
                          + ((r["bbox_native"][1] + r["bbox_native"][3]) / 2 - cy) ** 2, natural(r)))
            group.append(closest)
            remaining.remove(closest)
        regions.append(group)
    targets = []
    if len(regions) > 1:
        groups, pending = [], list(rows)
        while pending:
            group = [pending.pop(0)]
            for row in list(pending):
                if required(bounds(group + [row])) <= index_scale:
                    group.append(row)
                    pending.remove(row)
            groups.append(group)
        for group in groups:
            if required(bounds(group)) <= index_scale:
                targets.append(target(group, 'index', '项目总览图', index_scale, locked_scale=index_scale))
            else:
                tiles = plan_sheets([{'unit':r['parcel'],'bbox_native':r['bbox_native']} for r in group],
                                    frame_width_cm, frame_height_cm, scale=index_scale, meters_per_unit=meters_per_unit)
                for tile in tiles:
                    members = [r for r in group if str(r['parcel']) in tile['unit_ids']]
                    targets.append(dict(target(members, 'index', '项目总览图', index_scale,
                                               locked_scale=index_scale, box=tile['extent']), partial_coverage=True))
        for i, item in enumerate(targets, 1):
            item['name'] = '项目总览图' if len(targets) == 1 else '项目总览图_{:02d}'.format(i)
        if len(targets)>1000:
            raise ValueError('总览分幅超过1000幅，请分项目处理')
    for index, group in enumerate(regions, 1):
        name = "地理位置图" if len(regions) == 1 else "地理位置图_{:02d}".format(index)
        targets.append(target(group, "geo", name, 10000))
    combined, used = [], set()
    for ids in rules.get('combine_parcels', []):
        ids = {str(pid) for pid in ids}
        if used & ids or not ids <= seen:
            raise ValueError('合并采样图的地块重复或不在本次范围')
        members = [row for row in rows if str(row['parcel']) in ids]
        combined.append(members)
        used.update(ids)
    combined.extend([row] for row in rows if str(row['parcel']) not in used)
    for members in sorted(combined, key=lambda group: natural(group[0])):
        parcel = '、'.join(str(row['parcel']) for row in sorted(members, key=natural))
        targets.append(target(members, 'sample', '采样点位布置图_地块_' + parcel.replace('、','与'),
                              sample_min_scale, parcel))
    return apply_map_scales(targets, rules.get('map_scales', {}), frame_width_cm, frame_height_cm, meters_per_unit)


def plan_sheets(unit_rows, frame_width_cm, frame_height_cm, scale=10000, meters_per_unit=1,
                margin=1.12, overlap=0.08, max_sheets=1000):
    if not unit_rows:
        return []
    if min(frame_width_cm, frame_height_cm, meters_per_unit, scale) <= 0 or not 0 <= overlap < 1:
        raise ValueError("分幅尺寸、比例尺或单位无效")
    width = frame_width_cm * 0.01 * scale / margin / meters_per_unit
    height = frame_height_cm * 0.01 * scale / margin / meters_per_unit
    bounds = [min(row["bbox_native"][0] for row in unit_rows), min(row["bbox_native"][1] for row in unit_rows),
              max(row["bbox_native"][2] for row in unit_rows), max(row["bbox_native"][3] for row in unit_rows)]
    if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
        return []
    step_x, step_y = width * (1 - overlap), height * (1 - overlap)
    occupied = {}
    for row in unit_rows:
        xmin, ymin, xmax, ymax = row["bbox_native"]
        x0, x1 = max(0, math.floor((xmin - bounds[0] - width) / step_x) + 1), math.floor((xmax - bounds[0]) / step_x)
        y0, y1 = max(0, math.floor((ymin - bounds[1] - height) / step_y) + 1), math.floor((ymax - bounds[1]) / step_y)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > max_sheets:
            raise ValueError("分幅过多，请检查坐标或分项目处理")
        for col in range(x0, x1 + 1):
            for row_index in range(y0, y1 + 1):
                occupied.setdefault((col, row_index), set()).add(str(row["unit"]))
        if len(occupied) > max_sheets:
            raise ValueError("分幅超过 {} 幅，请分批处理".format(max_sheets))
    result = []
    for index, ((col, row_index), units) in enumerate(sorted(occupied.items(), key=lambda item: (-item[0][1], item[0][0])), 1):
        xmin, ymin = bounds[0] + col * step_x, bounds[1] + row_index * step_y
        result.append({"sheet_id": "F{:03d}".format(index), "extent": [xmin, ymin, xmin + width, ymin + height],
                       "scale": scale, "unit_ids": sorted(units), "kind": "sheet", "unit": None})
    # 8% 接边重叠会把只擦到边带的单元算进相邻幅，整块图元重复出现。
    # 单元只要被某一幅完整包含，就只保留该幅；空幅剔除后重新编号。
    boxes = {str(row["unit"]): row["bbox_native"] for row in unit_rows}

    def contains(sheet, box):
        x0, y0, x1, y1 = sheet["extent"]
        return x0 <= box[0] and y0 <= box[1] and x1 >= box[2] and y1 >= box[3]

    for sheet in result:
        kept = []
        for uid in sheet["unit_ids"]:
            owner = next((other for other in result if contains(other, boxes[uid])), None)
            if owner is None or owner is sheet:
                kept.append(uid)
        sheet["unit_ids"] = kept
    result = [sheet for sheet in result if sheet["unit_ids"]]
    for index, sheet in enumerate(result, 1):
        sheet["sheet_id"] = "F{:03d}".format(index)
    return result
