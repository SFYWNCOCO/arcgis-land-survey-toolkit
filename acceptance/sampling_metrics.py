"""采样点位质量指标：均匀性、最近邻距离、覆盖半径。

只给人工复核提供量化线索，不改变设计点、不替代规范判定。
每个需求人工调点的单元必须写明原因；指标本身不判 PASS/FAIL。
"""
from __future__ import annotations

import math
from statistics import median


def boundary_probes(boundary, cell_size):
    """沿图斑外边界按格长取采样探针（含格角），按坐标去重。

    boundary: [(x, y)] 首尾相接的边界折线。探针是单元内最远的盲区位置，
    只取格中心会把“格中心覆盖”当成“单元覆盖”，低估盲区。
    """
    if len(boundary) < 2 or not float(cell_size or 0) > 0:
        return []
    step = float(cell_size)
    probes, seen = [], set()
    ring = boundary if boundary[0] == boundary[-1] else [*boundary, boundary[0]]
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):  # Pro 3.x 为 Python 3.9，不用 itertools.pairwise
        length = _distance((x0, y0), (x1, y1))
        count = max(1, math.ceil(length / step))
        for k in range(count):
            t = k / count
            for fraction in (t, t + 0.5 / count):
                if fraction > 1.0:
                    continue
                probes.append((x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction))
    unique = []
    for probe in probes:
        key = (round(probe[0], 3), round(probe[1], 3))
        if key not in seen:
            seen.add(key)
            unique.append(probe)
    return unique


def apply_area_weights(centroids, cells):
    """把每个格中心归属到最近的本单元方格；格面积按格角归属均分，避免外包框偏置。

    centroids: [(x, y)]；cells: [{"xy": (x, y), "inside_area": 平方米}]。
    返回与 centroids 等长的权重列表；无正面积方格可归属时返回 None。
    网格大小不一（自适应缩格）时，相邻距离超过 1.5 倍最小间距的格不参与。
    """
    usable = [c for c in cells if float(c.get("inside_area") or 0) > 0]
    if not usable:
        return None
    centres = [c["xy"] for c in usable]
    size = _cell_size(centres)
    neighbours = [[j for j in range(len(centres))
                   if i != j and _distance(centres[i], centres[j]) <= size * 1.5]
                  for i in range(len(centres))]
    weights = []
    for centroid in centroids:
        anchor = _nearest(centroid, centres)
        area = float(usable[anchor]["inside_area"])
        for j in neighbours[anchor]:
            area += float(usable[j]["inside_area"]) / (len(neighbours[j]) + 1)
        weights.append(area)
    return weights


def _cell_size(centres):
    """固定网格边长：全部相邻格心距离取最小值；不足两格返回 1.0（单格，无参照）。"""
    if len(centres) < 2:
        return 1.0
    return min(_distance(a, b) for i, a in enumerate(centres) for b in centres[i + 1:]) or 1.0


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _nearest(point, candidates):
    return min(range(len(candidates)), key=lambda i: _distance(point, candidates[i]))


def nearest_distances(coords):
    """每个点到同单元最近其他点的距离（米）。至少两点；不足两点返回空列表。"""
    if len(coords) < 2:
        return []
    result = []
    for i, point in enumerate(coords):
        result.append(min(_distance(point, other) for j, other in enumerate(coords) if j != i))
    return result


def coverage_radius(coords, probe_points):
    """探针点到最近设计点的最大距离（米），即单元内最远盲区半径。

    probe_points 应是外围探针（图斑边界中点等），只取格中心会把口径缩成
    “格中心覆盖”，会低估田间盲区。探针为空时返回 None，不拿点间距离代替。
    """
    if not coords or not probe_points:
        return None
    return max(min(_distance(probe, point) for point in coords) for probe in probe_points)


def unit_quality(points, cells=None, probes=None, unit_boundary=None, edge_threshold=15.0,
                 min_spacing_m=15.0, area_m2=None):
    """单元级指标。

    points: [{"pid": POINT_ID, "xy": (x, y)}]；cells: 见 apply_area_weights；
    probes: 外围探针（boundary_probes 的输出），用于覆盖半径。
    unit_boundary: (x, y) -> 到图斑边界的距离（米），可空。
    返回 dict：指标、需调点单元标志与原因。任何一项不可计算都写 None，不猜。
    """
    coords = [p["xy"] for p in points]
    if area_m2 is not None:
        area_m2 = float(area_m2)
        if not math.isfinite(area_m2) or area_m2 <= 0:
            raise ValueError('采样密度需要有效的单元面积（平方米）')
    gaps = nearest_distances(coords)
    coverage = coverage_radius(coords, probes)
    reasons = []
    if len(points) < 2:
        reasons.append("单元只有 {} 个设计点，无法评估分布".format(len(points)))
    if gaps and min(gaps) < min_spacing_m:
        reasons.append("最近邻距离 {:.1f} m 小于一般间距 {} m".format(min(gaps), min_spacing_m))
    if unit_boundary is not None and coords:
        near_edge = [p["pid"] for p in points if unit_boundary(p["xy"]) < edge_threshold]
        if near_edge:
            reasons.append("设计点接近图斑边界：" + "、".join(map(str, near_edge)))
    if coverage is not None and gaps:
        spread = coverage / max(sum(gaps) / len(gaps), 1e-6)
        uniformity = 1.0 / (1.0 + spread)
    elif coverage is not None and len(coords) == 1:
        uniformity = 1.0 / (1.0 + coverage)  # 单点：均匀性按覆盖半径（米）换算，只作相对比较
    else:
        uniformity = None
    return {
        "设计点数": len(points),
        "面积_㎡": round(float(area_m2), 3) if area_m2 is not None else None,
        "采样密度_点每公顷": round(len(points) * 10000 / area_m2, 6) if area_m2 is not None else None,
        "平均每点_亩": round(area_m2 / (10000 / 15) / len(points), 6) if area_m2 is not None and points else None,
        "最近邻中位_m": round(median(gaps), 3) if gaps else None,
        "最近邻最小_m": round(min(gaps), 3) if gaps else None,
        "最近邻最大_m": round(max(gaps), 3) if gaps else None,
        "最近邻均值_m": round(sum(gaps) / len(gaps), 3) if gaps else None,
        "覆盖半径_m": round(coverage, 3) if coverage is not None else None,
        "均匀性": round(uniformity, 3) if uniformity is not None else None,
        "需人工调点": bool(reasons),
        "调点原因": reasons,
    }
