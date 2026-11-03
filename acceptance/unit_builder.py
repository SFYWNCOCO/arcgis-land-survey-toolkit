"""评价单元划分：属性一致 + 空间集中 + 面积上限，输出单元面与图斑归属。

四个子问题分阶段处理，避免全部塞进一条贪心流程：
1. 并：按四因素分组后，先合回同一源图斑的碎片，再按空间相邻和面积上限合并；
2. 切：只有原子仍超过面积上限时才切；优先级为因素边界（已由 factor_overlay
   落成碎片）> 线状地物（沟渠/道路/田埂，可选输入）> 主轴面积对半几何切割；
3. 目标：单元数最少 > 新切割次数最少 > 被拆源图斑最少 > 单元紧凑度最高；
4. 追溯：同时给出源图斑→单元、单元→源图斑的面积份额表，多对多关系不强行归并。

所有切割/拆分只出候选，结果状态固定 NEEDS_REVIEW，必须人工确认。
"""

from __future__ import annotations

import math
import os

from .data_intake import FACTOR_FIELDS, field_mapping
from .sampling import _create_output, _projected_meters_per_unit, _reject_filtered_layer

MU_M2 = 666.6666667
DEFAULT_MIN_POINTS = 3
DEFAULT_MIN_SPACING_M = 15.0
DEFAULT_INSET_M = 5.0
CUT_REVIEW_TEXT = "切割/拆分结果须人工确认"
OBJECTIVE_POLICY = "单元数最少 > 新切割次数最少 > 被拆源图斑最少 > 单元紧凑度最高"
CUT_PRIORITY = ("因素边界", "线状地物（沟渠/道路/田埂）", "主轴面积对半几何切割")
SPLIT_ORIENTATIONS = {"auto", "along", "across"}


def _arcpy():
    import arcpy

    return arcpy


def _text(value, missing="未填"):
    if value is None:
        return missing
    text = str(value).strip()
    return text or missing


# ---------------------------------------------------------------- 纯几何/规划辅助


def _append_point(points, point):
    x = getattr(point, "X", None)
    y = getattr(point, "Y", None)
    if x is None and isinstance(point, (tuple, list)) and len(point) >= 2:
        x, y = point[0], point[1]
    if x is not None and y is not None:
        points.append((float(x), float(y)))


def _geometry_points(geometry):
    """读取面几何的折点；兼容 ArcPy Polygon、Array 和测试替身。"""
    points = []
    raw_points = getattr(geometry, "points", None)
    if raw_points is not None:
        for point in raw_points:
            _append_point(points, point)
        if points:
            return points
    try:
        parts = list(geometry)
    except TypeError:
        parts = []
    if not parts:
        parts = [geometry]
    for part in parts:
        try:
            ring = list(part)
        except TypeError:
            ring = [part]
        for point in ring:
            _append_point(points, point)
    return points


def _planar_area(points):
    if len(points) < 3:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):  # noqa: B905
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _area(geometry):
    """优先椭球面积；测试替身或未知几何回退到平面面积。"""
    try:
        return abs(float(geometry.getArea("GEODESIC", "SQUAREMETERS")))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        pass
    try:
        return abs(float(geometry.area))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return _planar_area(_geometry_points(geometry))


def _centroid(geometry):
    try:
        centroid = geometry.centroid
        return (float(centroid.X), float(centroid.Y))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        points = _geometry_points(geometry)
        if not points:
            return None
        return (
            sum(x for x, _ in points) / len(points),
            sum(y for _, y in points) / len(points),
        )


def _perimeter(geometry):
    points = _geometry_points(geometry)
    if len(points) < 2:
        try:
            return float(geometry.length)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return 0.0
    total = 0.0
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        total += math.hypot(x2 - x1, y2 - y1)
    if points[0] != points[-1]:
        total += math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1])
    return total


def _compactness(geometry):
    """Polsby-Popper 紧凑度 4πA/P²，取值 0–1，越大越紧凑。"""
    area = _area(geometry)
    perimeter = _perimeter(geometry)
    if area <= 0 or perimeter <= 0:
        return 0.0
    return min(1.0, 4.0 * math.pi * area / (perimeter * perimeter))


def _principal_axis(points):
    """返回 (cx, cy, ux, uy)：外环折点的主惯性轴，方向确定且单位化。"""
    unique = []
    for point in points:
        if not unique or point != unique[-1]:
            unique.append(point)
    if len(unique) > 1 and unique[0] == unique[-1]:
        unique.pop()
    points = unique
    if len(points) < 2:
        raise ValueError("主轴计算至少需要两个折点")
    count = float(len(points))
    cx = sum(x for x, _ in points) / count
    cy = sum(y for _, y in points) / count
    sxx = sum((x - cx) ** 2 for x, _ in points)
    syy = sum((y - cy) ** 2 for _, y in points)
    sxy = sum((x - cx) * (y - cy) for x, y in points)
    if abs(sxy) < 1e-12 and abs(sxx - syy) < 1e-12:
        ux, uy = 1.0, 0.0
    else:
        angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
        ux, uy = math.cos(angle), math.sin(angle)
    if ux < 0 or (abs(ux) < 1e-12 and uy < 0):
        ux, uy = -ux, -uy
    return cx, cy, ux, uy


def minimum_fragment_area_m2(
    min_points=DEFAULT_MIN_POINTS,
    min_spacing_m=DEFAULT_MIN_SPACING_M,
    inset_m=DEFAULT_INSET_M,
):
    """由网格采样参数推得的最小碎片面积下界。

    网格法要求每个单元至少 ``min_points`` 个点、点间距不小于 ``min_spacing_m``，
    且点离边界不小于 ``inset_m``。最保守地按每点占一格、格边为
    ``min_spacing_m + 2*inset_m`` 估算，最小碎片面积为
    ``min_points * (min_spacing_m + 2*inset_m) ** 2``（平方米）。

    这只是规划下界，用来阻止把超限图斑切成无法布点的窄条；不替代小图斑例外、
    现场条件和专业判读。
    """
    try:
        points = float(min_points)
        spacing = float(min_spacing_m)
        inset = float(inset_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("最小碎片面积参数必须是数字") from exc
    if not math.isfinite(points) or points < 1 or points != int(points):
        raise ValueError("每单元最低点数必须是正整数")
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("最小间距必须为正数（米）")
    if not math.isfinite(inset) or inset < 0:
        raise ValueError("避边距离不能为负数（米）")
    return points * (spacing + 2.0 * inset) ** 2


def evaluate_partition(units, cut_count=0, split_source_count=0):
    """按显式目标评价一个划分方案；score 越小越好。"""
    unit_count = len(units)
    areas = [max(0.0, float(unit.get("area_m2") or 0.0)) for unit in units]
    total = sum(areas)
    if total > 0:
        compactness = (
            sum(
                max(0.0, min(1.0, float(unit.get("compactness") or 0.0))) * area
                for unit, area in zip(units, areas)  # noqa: B905
            )
            / total
        )
    else:
        compactness = 0.0
    score = [
        unit_count,
        int(cut_count),
        int(split_source_count),
        -round(compactness, 6),
    ]
    return {
        "policy": OBJECTIVE_POLICY,
        "unit_count": unit_count,
        "cut_count": int(cut_count),
        "split_source_count": int(split_source_count),
        "compactness": round(compactness, 6),
        "score": score,
        "lower_is_better": True,
    }


def build_traceability(assignments):
    """生成源图斑↔评价单元的面积份额表。

    ``assignments`` 每项至少含 pid、src_pid、unit_id、area_m2（或 geom）。
    同一源图斑可以对应多个单元；返回的 ``parcels[].primary_unit`` 只是最大面积
    份额的展示建议，不表示整块图斑归入该单元。
    """
    links, parcels, units = [], [], {}
    by_source = {}
    for item in assignments:
        source = str(item.get("src_pid") or item.get("pid") or "").strip()
        unit_id = str(item.get("unit_id") or "").strip()
        pid = str(item.get("pid") or "").strip()
        area = (
            float(item["area_m2"])
            if item.get("area_m2") is not None
            else _area(item["geom"])
        )
        row = dict(item, pid=pid, src_pid=source, unit_id=unit_id, area_m2=area)
        by_source.setdefault(source, []).append(row)
        units.setdefault(unit_id, []).append(row)

    for source in sorted(by_source):
        rows = sorted(
            by_source[source], key=lambda item: (item["unit_id"], item["pid"])
        )
        total = sum(item["area_m2"] for item in rows)
        grouped = {}
        for item in rows:
            grouped.setdefault(item["unit_id"], []).append(item)
        unit_rows = []
        for unit_id in sorted(grouped):
            unit_items = grouped[unit_id]
            area = sum(item["area_m2"] for item in unit_items)
            unit_rows.append(
                {
                    "unit": unit_id,
                    "area_m2": area,
                    "share": (area / total) if total > 0 else 0.0,
                    "fragments": [item["pid"] for item in unit_items],
                    "cut_types": sorted(
                        {str(item.get("cut_type") or "none") for item in unit_items}
                    ),
                }
            )
        status = "SPLIT" if len(unit_rows) > 1 else "WHOLE"
        primary = max(unit_rows, key=lambda item: (item["area_m2"], item["unit"]))
        parcels.append(
            {
                "source": source,
                "area_m2": total,
                "status": status,
                "primary_unit": primary["unit"],
                "units": unit_rows,
                "review": CUT_REVIEW_TEXT if status == "SPLIT" else "",
            }
        )
        for unit in unit_rows:
            links.append(
                {
                    "source": source,
                    "unit": unit["unit"],
                    "area_m2": unit["area_m2"],
                    "share": unit["share"],
                    "fragments": unit["fragments"],
                    "cut_types": unit["cut_types"],
                }
            )

    unit_rows = []
    for unit_id in sorted(units):
        rows = sorted(units[unit_id], key=lambda item: (item["src_pid"], item["pid"]))
        total = sum(item["area_m2"] for item in rows)
        grouped = {}
        for item in rows:
            grouped.setdefault(item["src_pid"], []).append(item)
        sources = []
        for source in sorted(grouped):
            area = sum(item["area_m2"] for item in grouped[source])
            sources.append(
                {
                    "source": source,
                    "area_m2": area,
                    "share": (area / total) if total > 0 else 0.0,
                    "fragments": [item["pid"] for item in grouped[source]],
                }
            )
        status = "MERGED" if len(sources) > 1 else "SINGLE"
        unit_rows.append(
            {
                "unit": unit_id,
                "area_m2": total,
                "status": status,
                "sources": sources,
                "review": CUT_REVIEW_TEXT if status == "MERGED" else "",
            }
        )

    pids = [row["pid"] for row in assignments]
    checks = {
        "all_fragments_assigned": all(row.get("unit_id") for row in assignments),
        "unique_fragment_ids": len(pids) == len(set(pids)),
        "source_shares_sum_to_one": all(
            abs(sum(unit["share"] for unit in parcel["units"]) - 1.0) <= 1e-9
            for parcel in parcels
        ),
        "unit_shares_sum_to_one": all(
            abs(sum(source["share"] for source in unit["sources"]) - 1.0) <= 1e-9
            for unit in unit_rows
        ),
    }
    return {
        "policy": "源图斑与单元为多对多；按面积份额回汇，不把整块图斑强行归给单一单元",
        "links": links,
        "parcels": parcels,
        "units": unit_rows,
        "checks": checks,
    }


# ---------------------------------------------------------------- 输入读取


def _load(parcels, id_field, factor_fields):
    arcpy = _arcpy()
    desc = arcpy.Describe(parcels)
    if str(getattr(desc, "shapeType", "")) != "Polygon":
        raise ValueError("评价单元划分的输入必须是面要素")
    _projected_meters_per_unit(desc.spatialReference)
    fields = [field.name for field in arcpy.ListFields(parcels)]
    missing = [name for name in [id_field, *factor_fields] if name not in fields]
    if missing:
        raise ValueError("字段不存在：" + "、".join(missing))
    extra = [name for name in ("SRC_DKBH", "CUT_TYPE", "CUT_PARENT") if name in fields]
    columns = ["OID@", id_field, *extra, *factor_fields, "SHAPE@"]
    rows = []
    with arcpy.da.SearchCursor(parcels, columns) as cursor:
        for values in cursor:
            oid, pid, geometry = values[0], values[1], values[-1]
            extras = dict(zip(extra, values[2 : 2 + len(extra)]))  # noqa: B905
            factors = tuple(_text(value) for value in values[2 + len(extra) : -1])
            if geometry is None or _area(geometry) <= 0:
                raise ValueError(f"图斑 OID {oid} 为空几何或零面积；请先修复")
            if getattr(geometry, "partCount", 1) > 1:
                raise ValueError(f"图斑 {pid} 为多部件；附录 C 注 6 禁止多部件图斑")
            source = _text(extras.get("SRC_DKBH"), "")
            pid_text = _text(pid, "")
            rows.append(
                {
                    "oid": oid,
                    "pid": pid_text,
                    "src_pid": source or pid_text,
                    "parent_pid": _text(extras.get("CUT_PARENT"), "") or pid_text,
                    "cut_type": _text(extras.get("CUT_TYPE"), "")
                    or ("factor" if source else "none"),
                    "factors": factors,
                    "geom": geometry,
                    "area_m2": _area(geometry),
                    "missing": [],
                    "project": "未填",
                }
            )
    if not rows:
        raise ValueError("输入要素为空")
    pids = [row["pid"] for row in rows]
    if len(set(pids)) != len(pids):
        raise ValueError("图斑编号存在重复；请先统一编号")
    if any(not row["pid"] for row in rows):
        raise ValueError("图斑编号存在空值；请先修复")
    return rows


# ---------------------------------------------------------------- 阶段一：并


def _unit_from_members(members, geometry, factors, area_m2, meters):
    return {
        "factors": factors,
        "members": list(members),
        "geom": geometry,
        "area_m2": float(area_m2),
        "compactness": _compactness(geometry),
    }


def _merge_group(arcpy, items, max_m2, gap_native, allow_multipart, meters):
    """同因素组内合并；先合回同源图斑碎片，再按面积和紧凑度贪心装填。"""
    units, unmerged = [], []
    remaining = list(items)

    # 先合回同一源图斑的碎片：减少源图斑被拆到多个单元的机会。
    by_source = {}
    for item in remaining:
        by_source.setdefault(item["src_pid"], []).append(item)
    folded = set()
    for source, group in by_source.items():
        if len(group) < 2:
            continue
        total = sum(item["area_m2"] for item in group)
        if total > max_m2 * (1 + 1e-9):
            continue
        merged = group[0]["geom"]
        for item in group[1:]:
            merged = merged.union(item["geom"])
        if not allow_multipart and getattr(merged, "partCount", 1) > 1:
            continue
        units.append(
            _unit_from_members(group, merged, group[0]["factors"], total, meters)
        )
        folded.add(source)
    if folded:
        remaining = [item for item in remaining if item["src_pid"] not in folded]

    remaining.sort(key=lambda item: -item["area_m2"])
    while remaining:
        seed = remaining.pop(0)
        members = [seed]
        total = seed["area_m2"]
        merged = seed["geom"]
        member_sources = {seed["src_pid"]}
        while True:
            best = None
            best_key = None
            for candidate in remaining:
                if total + candidate["area_m2"] > max_m2 * (1 + 1e-9):
                    continue
                distance = candidate["geom"].distanceTo(merged)
                if distance > gap_native + 1e-9:
                    continue
                union = merged.union(candidate["geom"])
                if not allow_multipart and getattr(union, "partCount", 1) > 1:
                    unmerged.append(
                        {
                            "图斑": [candidate["src_pid"], sorted(member_sources)],
                            "面积合计亩": round(
                                (total + candidate["area_m2"]) / MU_M2, 2
                            ),
                            "间距米": round(distance * meters, 2),
                            "未合并原因": "两图斑不共边，合并后为多部件；如项目认定相对集中，"
                            "可在参数中允许多部件单元",
                        }
                    )
                    continue
                same_source = 1 if candidate["src_pid"] in member_sources else 0
                key = (
                    candidate["area_m2"],
                    same_source,
                    _compactness(union),
                    -distance,
                )
                if best_key is None or key > best_key:
                    best = (candidate, union, distance)
                    best_key = key
            if best is None:
                break
            candidate, union, _ = best
            members.append(candidate)
            remaining.remove(candidate)
            total += candidate["area_m2"]
            merged = union
            member_sources.add(candidate["src_pid"])
        units.append(
            _unit_from_members(members, merged, seed["factors"], total, meters)
        )
    return units, unmerged


# ---------------------------------------------------------------- 阶段二：切


def _load_linear_features(arcpy, linear_features, sr):
    """读取线状地物；可以是要素类路径，也可以是 ArcPy 线几何列表。"""
    if not linear_features:
        return []
    if isinstance(linear_features, (str, os.PathLike)):
        sources = [linear_features]
    else:
        sources = list(linear_features)
    lines = []
    expected_sr = sr.exportToString()
    for index, source in enumerate(sources):
        if isinstance(source, (str, os.PathLike)):
            desc = arcpy.Describe(str(source))
            if str(getattr(desc, "shapeType", "")) != "Polyline":
                raise ValueError("线状地物必须是线要素：" + str(source))
            for oid, geometry in arcpy.da.SearchCursor(str(source), ["OID@", "SHAPE@"]):
                if geometry is None or getattr(geometry, "partCount", 1) < 1:
                    continue
                if geometry.spatialReference.exportToString() != expected_sr:
                    raise ValueError(
                        "线状地物与图斑坐标系不一致；请先用已确认的投影转换"
                    )
                lines.append(
                    {
                        "oid": oid,
                        "name": os.path.basename(str(source)),
                        "geom": geometry,
                    }
                )
        else:
            geometry = source
            if (
                getattr(geometry, "spatialReference", None) is not None
                and geometry.spatialReference.exportToString() != expected_sr
            ):
                raise ValueError("线状地物与图斑坐标系不一致；请先用已确认的投影转换")
            lines.append({"oid": index + 1, "name": "inline", "geom": geometry})
    return lines


def _extended_line(arcpy, line, geometry, sr):
    """把线两端延长到面范围之外，确保 cut 能真正穿过面。"""
    points = _geometry_points(line)
    if len(points) < 2:
        return line
    extent = geometry.extent
    span = max(1.0, math.hypot(extent.width, extent.height) * 2.0)
    (x0, y0), (x1, y1) = points[0], points[1]
    (x2, y2), (x3, y3) = points[-2], points[-1]
    start = _extend(x0, y0, x1, y1, span)
    end = _extend(x3, y3, x2, y2, span)
    try:
        array = arcpy.Array(
            [arcpy.Point(*start)]
            + [arcpy.Point(x, y) for x, y in points]
            + [arcpy.Point(*end)]
        )
        return arcpy.Polyline(array, sr)
    except Exception:  # 延长失败时退回原线，由 cut 结果决定
        return line


def _extend(x0, y0, x1, y1, length):
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy)
    if norm <= 1e-12:
        return (x0, y0)
    return (x0 - dx / norm * length, y0 - dy / norm * length)


def _safe_cut(geometry, line):
    try:
        pieces = geometry.cut(line)
    except Exception:  # 不穿过面的线由调用方过滤
        return []
    if pieces is None:
        return []
    return [piece for piece in pieces if piece is not None and _area(piece) > 1e-8]


def _best_linear_cut(arcpy, geometry, lines, min_piece_m2, sr):
    total = _area(geometry)
    best = None
    for line in lines:
        extended = _extended_line(arcpy, line["geom"], geometry, sr)
        pieces = _safe_cut(geometry, extended)
        if len(pieces) < 2:
            continue
        areas = [_area(piece) for piece in pieces]
        if abs(sum(areas) - total) > max(1e-4, total * 1e-7):
            continue
        if min(areas) < min_piece_m2 - 1e-9:
            continue
        key = (-len(pieces), min(_compactness(piece) for piece in pieces))
        if best is None or key > best["key"]:
            best = {"key": key, "pieces": pieces, "line": line, "orientation": "linear"}
    return best


def _axis_cut(arcpy, geometry, axis, mode, sr, min_piece_m2):
    """沿主轴累计面积到一半处下刀；mode=along 切线平行主轴，across 垂直主轴。"""
    points = _geometry_points(geometry)
    if len(points) < 3:
        return None
    cx, cy, ux, uy = axis
    normal = (-uy, ux)
    if mode == "across":
        line_dir, sweep = (ux, uy), normal
    else:
        line_dir, sweep = normal, (ux, uy)
    projections = [x * sweep[0] + y * sweep[1] for x, y in points]
    if not projections:
        return None
    lo, hi = min(projections), max(projections)
    pad = max(1.0, (hi - lo) * 0.01)
    lo, hi = lo - pad, hi + pad
    total = _area(geometry)
    if total <= 0:
        return None
    center_projection = cx * sweep[0] + cy * sweep[1]
    extent = geometry.extent
    span = max(1.0, math.hypot(extent.width, extent.height) * 2.2)
    best, best_error = None, None
    for _ in range(28):
        mid = (lo + hi) / 2.0
        anchor = (
            cx + sweep[0] * (mid - center_projection),
            cy + sweep[1] * (mid - center_projection),
        )
        start = (anchor[0] - line_dir[0] * span, anchor[1] - line_dir[1] * span)
        end = (anchor[0] + line_dir[0] * span, anchor[1] + line_dir[1] * span)
        try:
            line = arcpy.Polyline(
                arcpy.Array([arcpy.Point(*start), arcpy.Point(*end)]), sr
            )
        except Exception:
            return None
        pieces = _safe_cut(geometry, line)
        if len(pieces) < 2:
            return None
        areas = [_area(piece) for piece in pieces]
        if abs(sum(areas) - total) > max(1e-4, total * 1e-7):
            return None
        negative = 0.0
        for piece, area in zip(pieces, areas):  # noqa: B905
            center = _centroid(piece)
            if center is None:
                continue
            side = (center[0] - anchor[0]) * sweep[0] + (center[1] - anchor[1]) * sweep[
                1
            ]
            if side < 0:
                negative += area
        error = abs(negative - total / 2.0)
        if best_error is None or error < best_error:
            best_error, best = error, pieces
        if negative < total / 2.0:
            lo = mid
        else:
            hi = mid
    if best is None:
        return None
    if min(_area(piece) for piece in best) < min_piece_m2 - 1e-9:
        return None
    return best


def _geometric_cut(arcpy, geometry, orientation, sr, min_piece_m2):
    points = _geometry_points(geometry)
    if len(points) < 3:
        return None
    axis = _principal_axis(points)
    modes = ["across", "along"] if orientation == "auto" else [orientation]
    best = None
    for mode in modes:
        pieces = _axis_cut(arcpy, geometry, axis, mode, sr, min_piece_m2)
        if not pieces:
            continue
        key = (min(_compactness(piece) for piece in pieces), -len(pieces))
        if best is None or key > best["key"]:
            best = {"key": key, "pieces": pieces, "orientation": mode}
    return best


def _split_children(
    arcpy,
    item,
    pieces,
    method,
    cuts,
    context,
    orientation=None,
    line=None,
    depth=0,
):
    children = []
    child_pids = []
    for index, piece in enumerate(pieces, 1):
        child = dict(item)
        child["pid"] = "{}#{}".format(item["pid"], index)
        child["parent_pid"] = item["pid"]
        child["geom"] = piece
        child["area_m2"] = _area(piece)
        child["cut_type"] = method
        child_pids.append(child["pid"])
        children.append(child)
    cut = {
        "SRC_DKBH": item["src_pid"],
        "PARENT": item["pid"],
        "method": method,
        "orientation": orientation,
        "children": child_pids,
        "area_m2": item["area_m2"],
        "max_m2": context.get("max_m2"),
        "min_fragment_m2": context.get("min_piece_m2"),
        "review": "REQUIRED",
        "detail": "切割结果只作候选，须人工确认后使用",
    }
    if line:
        cut["line"] = {"oid": line.get("oid"), "name": line.get("name")}
    cuts.append(cut)
    resolved = []
    for child in children:
        resolved.extend(_split_geometry(arcpy, child, cuts, context, depth=depth + 1))
    return resolved


def _split_geometry(arcpy, item, cuts, context, depth=0):
    if depth > 64:
        raise ValueError("切割层数过深，请人工划分：" + str(item["src_pid"]))
    context["cut_steps"] = context.get("cut_steps", 0) + 1
    if context["cut_steps"] > 10000:
        raise ValueError("切割步数过多，请人工划分：" + str(item["src_pid"]))
    if item["area_m2"] <= context["max_m2"] * (1 + 1e-9):
        return [
            _unit_from_members(
                [item],
                item["geom"],
                item["factors"],
                item["area_m2"],
                context["meters"],
            )
        ]
    if context["linear_lines"]:
        choice = _best_linear_cut(
            arcpy,
            item["geom"],
            context["linear_lines"],
            context["min_piece_m2"],
            context["sr"],
        )
        if choice:
            return _split_children(
                arcpy,
                item,
                choice["pieces"],
                "linear",
                cuts,
                context,
                orientation="linear",
                line=choice.get("line"),
                depth=depth,
            )
    choice = _geometric_cut(
        arcpy,
        item["geom"],
        context["orientation"],
        context["sr"],
        context["min_piece_m2"],
    )
    if choice:
        context["orientation"] = choice.get("orientation")
        return _split_children(
            arcpy,
            item,
            choice["pieces"],
            "geometric",
            cuts,
            context,
            orientation=choice.get("orientation"),
            depth=depth,
        )
    raise ValueError(
        "超限图斑 {} 无法在面积上限内可靠拆分（最小碎片面积 {:.1f} 平方米）；"
        "请人工划分或补充沟渠/道路/田埂线状地物".format(
            item["src_pid"], context["min_piece_m2"]
        )
    )


def _split_oversize(arcpy, geom, max_m2, sr, min_piece_m2=None):
    """兼容旧调用的单几何拆分入口；已改为“主轴面积对半”，不再用包围盒中线。"""
    max_m2 = float(max_m2)
    min_piece_m2 = min_piece_m2 or minimum_fragment_area_m2()
    if max_m2 <= 0 or min_piece_m2 <= 0:
        raise ValueError("面积上限和最小碎片面积必须为正数")
    context = {
        "max_m2": max_m2,
        "min_piece_m2": float(min_piece_m2),
        "gap_native": 0.0,
        "allow_multipart": False,
        "meters": 1.0,
        "sr": sr,
        "linear_lines": [],
        "orientation": "auto",
    }
    item = {
        "pid": "<split>",
        "src_pid": "<split>",
        "parent_pid": "<split>",
        "cut_type": "none",
        "factors": (),
        "geom": geom,
        "area_m2": _area(geom),
        "missing": [],
        "project": "未填",
        "oid": 0,
    }
    units = _split_geometry(arcpy, item, [], context)
    return [unit["geom"] for unit in units]


def _resolve_oversize(arcpy, unit, cuts, context):
    if unit["area_m2"] <= context["max_m2"] * (1 + 1e-9):
        return [unit]
    members = unit["members"]
    if len(members) > 1:
        packed, _ = _merge_group(
            arcpy,
            members,
            context["max_m2"],
            context["gap_native"],
            context["allow_multipart"],
            context["meters"],
        )
        if packed and all(
            piece["area_m2"] <= context["max_m2"] * (1 + 1e-9) for piece in packed
        ):
            return packed
    item = dict(members[0]) if members else {}
    if not item:
        raise ValueError("超限单元没有可拆分的图斑成员")
    item["geom"] = unit["geom"]
    item["area_m2"] = unit["area_m2"]
    return _split_geometry(arcpy, item, cuts, context)


# ---------------------------------------------------------------- 主流程


def build_units(
    parcel_fc,
    id_field,
    out_gdb,
    factor_fields=(),
    max_mu=50.0,
    gap_m=30.0,
    unit_name="评价单元",
    assign_name="图斑与评价单元",
    allow_multipart=False,
    linear_features=None,
    min_fragment_mu=None,
    min_points=DEFAULT_MIN_POINTS,
    min_spacing_m=DEFAULT_MIN_SPACING_M,
    inset_m=DEFAULT_INSET_M,
    split_orientation="auto",
):
    arcpy = _arcpy()
    desc = arcpy.Describe(parcel_fc)
    _reject_filtered_layer(desc, "图斑输入", arcpy.da.Describe(parcel_fc).get("FIDSet"))
    sr = desc.spatialReference
    meters = _projected_meters_per_unit(sr)
    if not math.isfinite(float(max_mu)) or float(max_mu) <= 0:
        raise ValueError("单元面积上限必须为正数（亩）")
    if not math.isfinite(float(gap_m)) or float(gap_m) < 0:
        raise ValueError("相邻判定间距不能为负数")
    if split_orientation not in SPLIT_ORIENTATIONS:
        raise ValueError("切割方向只支持 auto/along/across")
    mapping = field_mapping([field.name for field in arcpy.ListFields(parcel_fc)])
    missing_factors = [role for role in FACTOR_FIELDS if role not in mapping]
    project_field = mapping.get("project")
    factor_fields = list(
        dict.fromkeys(
            list(factor_fields)
            + [mapping[role] for role in FACTOR_FIELDS if role in mapping]
            + ([project_field] if project_field else [])
        )
    )
    rows = _load(parcel_fc, id_field, factor_fields)
    for row in rows:
        values = dict(zip(factor_fields, row["factors"]))  # noqa: B905
        missing = list(missing_factors)
        missing.extend(
            role
            for role in FACTOR_FIELDS
            if role in mapping and values[mapping[role]] == "未填"
        )
        if mapping.get("supplement_source") and values[
            mapping["supplement_source"]
        ] not in ("恢复", "垦造"):
            missing.append("supplement_source_value")
        if not project_field or values[project_field] == "未填":
            missing.append("project")
        row["missing"] = list(dict.fromkeys(missing))
        row["project"] = values.get(project_field, "未填")

    max_m2 = float(max_mu) * MU_M2
    gap_native = float(gap_m) / meters
    if min_fragment_mu is None:
        min_piece_m2 = minimum_fragment_area_m2(min_points, min_spacing_m, inset_m)
    else:
        value = float(min_fragment_mu)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("最小碎片面积必须为正数（亩）")
        min_piece_m2 = value * MU_M2
    if max_m2 < min_piece_m2:
        raise ValueError("单元面积上限小于最小碎片面积，无法同时满足两项约束")
    linear_lines = _load_linear_features(arcpy, linear_features, sr)

    for name in (unit_name, assign_name):
        if arcpy.Exists(os.path.join(out_gdb, name)):
            raise FileExistsError("输出已存在，不覆盖：" + os.path.join(out_gdb, name))

    groups = {}
    for row in rows:
        # 缺项时按“原图斑”隔离，避免跨图斑凑单元；同一源图斑被因素切开的
        # 片段只要已知因素一致，仍应能合回一个临时单元。
        key = row["factors"] + (
            ("临时逐图斑", row["src_pid"]) if row["missing"] else ()
        )
        groups.setdefault(key, []).append(row)

    cuts, units, warnings, unmerged = [], [], [], []
    context = {
        "max_m2": max_m2,
        "min_piece_m2": min_piece_m2,
        "gap_native": gap_native,
        "allow_multipart": bool(allow_multipart),
        "meters": meters,
        "sr": sr,
        "linear_lines": linear_lines,
        "orientation": split_orientation,
    }
    for _factors, items in groups.items():
        group_units, group_unmerged = _merge_group(
            arcpy, items, max_m2, gap_native, bool(allow_multipart), meters
        )
        unmerged.extend(group_unmerged)
        for unit in group_units:
            units.extend(_resolve_oversize(arcpy, unit, cuts, context))

    # 切后再并一次：切割碎片可能与邻近单元重新组合成更少的单元。
    if cuts:
        regrouped = {}
        for unit in units:
            for member in unit["members"]:
                key = member["factors"] + (
                    (("临时逐图斑", member["src_pid"])) if member["missing"] else ()
                )
                regrouped.setdefault(key, []).append(member)
        units = []
        for _key, atoms in regrouped.items():
            group_units, group_unmerged = _merge_group(
                arcpy, atoms, max_m2, gap_native, bool(allow_multipart), meters
            )
            unmerged.extend(group_unmerged)
            units.extend(group_units)

    units.sort(key=lambda unit: min(member["pid"] for member in unit["members"]))
    width = max(4, len(str(len(units))))
    assignments = []
    for index, unit in enumerate(units, 1):
        unit["unit_id"] = "PJDY" + str(index).zfill(width)
        member_sources = {member["src_pid"] for member in unit["members"]}
        unit["source_ids"] = sorted(member_sources)
        unit["cut_types"] = sorted(
            {member.get("cut_type") or "none" for member in unit["members"]}
        )
        for member in unit["members"]:
            assignments.append(
                {
                    "pid": member["pid"],
                    "src_pid": member["src_pid"],
                    "parent_pid": member.get("parent_pid") or member["pid"],
                    "cut_type": member.get("cut_type") or "none",
                    "unit_id": unit["unit_id"],
                    "geom": member["geom"],
                    "area_m2": member["area_m2"],
                    "oid": member["oid"],
                    "missing": member["missing"],
                    "project": member["project"],
                }
            )

    traceability = build_traceability(assignments)
    split_source_count = sum(
        1 for parcel in traceability["parcels"] if parcel["status"] == "SPLIT"
    )
    objective = evaluate_partition(
        units, cut_count=len(cuts), split_source_count=split_source_count
    )
    source_totals = {}
    for item in assignments:
        source_totals[item["src_pid"]] = (
            source_totals.get(item["src_pid"], 0.0) + item["area_m2"]
        )
    fragment_shares = {
        item["pid"]: (
            item["area_m2"] / source_totals[item["src_pid"]]
            if source_totals.get(item["src_pid"])
            else 0.0
        )
        for item in assignments
    }
    source_reviews = {
        parcel["source"]: parcel["review"]
        for parcel in traceability["parcels"]
        if parcel["status"] == "SPLIT"
    }
    if source_reviews:
        warnings.append(
            {
                "图斑": sorted(source_reviews),
                "问题": "同一源图斑分布到多个单元；按面积份额回汇，须人工确认",
            }
        )
    for unit in units:
        if getattr(unit["geom"], "partCount", 1) > 1:
            warnings.append(
                {
                    "单元图斑": unit["source_ids"],
                    "问题": "合并后为多部件，须人工确认是否算相对集中",
                }
            )

    input_area = sum(row["area_m2"] for row in rows)
    output_area = sum(unit["area_m2"] for unit in units)
    covered = None
    for unit in units:
        covered = unit["geom"] if covered is None else covered.union(unit["geom"])
    overlap = output_area - covered.area
    if abs(input_area - output_area) > max(1e-3, input_area * 1e-7) or overlap > max(
        1e-3, input_area * 1e-7
    ):
        raise ValueError("单元候选存在面积覆盖或重叠问题，停止输出")

    def write(mark_created):
        unit_fc = _create_output(
            out_gdb,
            unit_name,
            sr,
            [
                ("PJDYBH", "TEXT", 30),
                ("PJDY_M2", "DOUBLE", None),
                ("PJDY_MU", "DOUBLE", None),
                ("DKS", "SHORT", None),
                ("DKBH", "TEXT", 254),
                ("RULE", "TEXT", 120),
                ("REVIEW", "TEXT", 200),
            ],
            mark_created,
            shape_type="POLYGON",
        )
        with arcpy.da.InsertCursor(
            unit_fc,
            ["SHAPE@", "PJDYBH", "PJDY_M2", "PJDY_MU", "DKS", "DKBH", "RULE", "REVIEW"],
        ) as cur:
            for unit in units:
                review = []
                if unit["cut_types"] and unit["cut_types"] != ["none"]:
                    review.append(CUT_REVIEW_TEXT)
                if any(source in source_reviews for source in unit["source_ids"]):
                    review.append("源图斑拆分，须确认面积份额")
                if unit["members"][0]["missing"]:
                    review.append(
                        "缺项临时候选：" + ",".join(unit["members"][0]["missing"])
                    )
                if getattr(unit["geom"], "partCount", 1) > 1:
                    review.append("多部件须人工确认")
                cur.insertRow(
                    [
                        unit["geom"],
                        unit["unit_id"],
                        unit["area_m2"],
                        unit["area_m2"] / MU_M2,
                        len(unit["source_ids"]),
                        ",".join(unit["source_ids"]),
                        "属性一致{}；图斑间距≤{:.0f}米；单元≤{:.0f}亩；切法：因素边界>线状地物>主轴面积对半".format(
                            "/".join(str(value) for value in unit["factors"]),
                            gap_m,
                            max_mu,
                        )[:120],
                        "；".join(review)[:200],
                    ]
                )
        assign_fc = _create_output(
            out_gdb,
            assign_name,
            sr,
            [
                ("PJDYBH", "TEXT", 30),
                ("DKBH", "TEXT", 255),
                ("SRC_DKBH", "TEXT", 255),
                ("SRC_OID", "LONG", None),
                ("DY_M2", "DOUBLE", None),
                ("DY_MU", "DOUBLE", None),
                ("CUT_TYPE", "TEXT", 20),
                ("CUT_PARENT", "TEXT", 255),
                ("SRC_SHARE", "DOUBLE", None),
                ("REVIEW", "TEXT", 120),
            ],
            mark_created,
            shape_type="POLYGON",
        )
        with arcpy.da.InsertCursor(
            assign_fc,
            [
                "SHAPE@",
                "PJDYBH",
                "DKBH",
                "SRC_DKBH",
                "SRC_OID",
                "DY_M2",
                "DY_MU",
                "CUT_TYPE",
                "CUT_PARENT",
                "SRC_SHARE",
                "REVIEW",
            ],
        ) as cur:
            for item in assignments:
                share = fragment_shares.get(item["pid"], 0.0)
                review = CUT_REVIEW_TEXT if item["cut_type"] not in ("none",) else ""
                if source_reviews.get(item["src_pid"]):
                    review = "；".join(
                        filter(None, [review, "源图斑跨单元，按份额回汇"])
                    )
                cur.insertRow(
                    [
                        item["geom"],
                        item["unit_id"],
                        item["pid"],
                        item["src_pid"],
                        item["oid"],
                        item["area_m2"],
                        item["area_m2"] / MU_M2,
                        item["cut_type"],
                        item["parent_pid"],
                        share,
                        review[:120],
                    ]
                )
        return {
            "unit_fc": unit_fc,
            "assign_fc": assign_fc,
            "source_id_field": "SRC_DKBH",
            "units": len(units),
            "parcels": len(rows),
            "max_mu": float(max_mu),
            "gap_m": float(gap_m),
            "factor_fields": list(factor_fields),
            "missing_factors": missing_factors,
            "project_field": project_field,
            "min_fragment_m2": min_piece_m2,
            "min_fragment_mu": min_piece_m2 / MU_M2,
            "cut_policy": {
                "priority": list(CUT_PRIORITY),
                "orientation": split_orientation,
                "linear_feature_count": len(linear_lines),
                "review": "所有切割/拆分只作候选，须人工确认",
            },
            "cuts": cuts,
            "traceability": traceability,
            "objective": objective,
            "geometry_check": {
                "status": "PASS",
                "input_area_native": input_area,
                "output_area_native": output_area,
                "overlap_native": max(0, overlap),
            },
            "split": _split_records(assignments, cuts),
            "warnings": warnings,
            "unmerged": unmerged,
            "detail": [
                {
                    "单元": unit["unit_id"],
                    "图斑": unit["source_ids"],
                    "碎片": [member["pid"] for member in unit["members"]],
                    "临时候选": bool(unit["members"][0]["missing"]),
                    "缺项": unit["members"][0]["missing"],
                    "项目": unit["members"][0]["project"],
                    "面积亩": round(unit["area_m2"] / MU_M2, 2),
                    "面积平方米": round(unit["area_m2"], 2),
                    "部件数": getattr(unit["geom"], "partCount", 1),
                    "切割方式": unit["cut_types"],
                    "人工确认": CUT_REVIEW_TEXT
                    if unit["cut_types"] != ["none"]
                    else "",
                }
                for unit in units
            ],
            "status": "NEEDS_REVIEW",
            "rule": "四因素一致分组 + 空间集中 + 单元面积上限；切法优先级：因素边界 > 线状地物 > 主轴面积对半；"
            "所有切割须人工确认；目标：" + OBJECTIVE_POLICY,
        }

    from .sampling import _with_output_rollback

    return _with_output_rollback(arcpy, os.path.join(out_gdb, unit_name), write)


def _split_records(assignments, cuts):
    """按源图斑汇总新切割记录；因素叠加产生的旧切分由 traceability 表达。"""
    by_source = {}
    for cut in cuts:
        by_source.setdefault(cut["SRC_DKBH"], []).append(cut)
    records = []
    for source in sorted(by_source):
        pieces = [item for item in assignments if item["src_pid"] == source]
        records.append(
            {
                "图斑": source,
                "面积亩": round(sum(item["area_m2"] for item in pieces) / MU_M2, 2),
                "拆分片数": len(pieces),
                "切割方式": sorted({cut["method"] for cut in by_source[source]}),
                "人工确认": "REQUIRED",
                "切割": by_source[source],
            }
        )
    return records
