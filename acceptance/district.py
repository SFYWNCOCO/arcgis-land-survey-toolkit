"""高德行政区划区图 CLI：县/乡界线与驻地，GCJ-02→WGS84→目标 CRS，写入文件地理数据库。

默认 ``--level 县乡``：同时输出区县界线与乡镇/街道界线（同一要素类用 LEVEL 字段区分），
也保留 ``--level 县`` / ``--level 乡`` 单级筛选。只用参考底图：高德界线不是法定行政
界线，正式报件必须使用三调/民政官方界线。

坐标处理：高德 district 接口返回 GCJ-02 经纬度，先逐点反算为 WGS84，再由 ArcGIS Pro
的 Project 工具转到目标坐标系；``--keywords`` 缺省时用官方逆地理编码 v3/geocode/regeo
反查区县 adcode（该接口 location 也是 GCJ-02，必须先把 WGS84 范围中心转成 GCJ-02）。
WGS84→CGCS2000 视为同一基准，不做七参数转换，并在输出 JSON 的 datum_note 中明确标注。

用法::

    py district.py --out-gdb D:\\out\\区图.gdb --name 上林 --wkid 4525 \\
        --extent 108.3 23.1 108.9 23.7
    py district.py --out-gdb D:\\out\\区图.gdb --name 上林乡 --wkid 4525 \\
        --extent 108.3 23.1 108.9 23.7 --keywords 上林县 --subdistrict 2 --level 乡

stdout 最后一行固定为 JSON：成功 ``{"status":"OK",...}``、失败
``{"status":"FAIL","error":...}``；只有驻地没有界线时 failure 文本含 ``NO_BOUNDARY``；
成功 exit 0、失败 exit 1。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 允许按 `py acceptance/district.py` 直接运行：把工具包根目录加入 sys.path，
# 使 `acceptance.poi_reference` 等包内导入可用（脚本目录不在其内）。
_TOOLKIT_ROOT = str(Path(__file__).resolve().parents[1])
if _TOOLKIT_ROOT not in sys.path:
    sys.path.insert(0, _TOOLKIT_ROOT)

try:  # 作为包导入（Pro 内嵌解释器 / 测试）
    from . import gcj02
except ImportError:  # 作为脚本直接运行（py acceptance/district.py）
    import gcj02  # type: ignore[no-redef]

AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
REQUEST_TIMEOUT = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
EXTENT_PAD_RATIO = 0.10
SOURCE_TEXT = "高德行政区划（GCJ-02→WGS84）"
# 区图默认同时输出县界与乡镇界；保留县/乡单级筛选。
LEVELS = {"县": ("district",), "乡": ("street",), "县乡": ("district", "street")}
LEVEL_ORDER = ("district", "street")
DEFAULT_LEVEL = "县乡"
LEVEL_NAMES = {"country": "国家", "province": "省", "city": "市",
               "district": "区县", "street": "乡镇/街道", "biz_area": "商圈"}
FIELDS = (
    ("ADCODE", "TEXT", 32, "行政区划代码"),
    ("NAME", "TEXT", 200, "名称"),
    ("LEVEL", "TEXT", 40, "高德层级"),
    ("CENTER_LON", "DOUBLE", None, "驻地经度WGS84"),
    ("CENTER_LAT", "DOUBLE", None, "驻地纬度WGS84"),
    ("SOURCE", "TEXT", 80, "数据来源"),
)


class _ArgumentParser(argparse.ArgumentParser):
    """参数错误也输出 JSON FAIL，而不是只有 argparse 的 stderr 文本。"""

    def error(self, message):
        raise ValueError("参数错误：" + message)


# ---------------------------------------------------------------------------
# 纯几何/解析函数（不依赖 arcpy、不联网，供测试直接复用）
# ---------------------------------------------------------------------------

def parse_polyline(text):
    """解析高德 polyline：``|`` 分环、``;`` 分点、``,`` 分经纬度（GCJ-02 lon,lat）。"""
    value = str(text or "").strip()
    if not value or value in ("[]", "null", "None"):
        return []
    rings = []
    for chunk in value.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ring = []
        for pair in chunk.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            parts = pair.split(",")
            if len(parts) != 2:
                raise ValueError("高德面边界坐标无法解析：{}".format(pair[:40]))
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                raise ValueError("高德面边界坐标不是数字：{}".format(pair[:40])) from None
            if not (math.isfinite(lon) and math.isfinite(lat)):
                raise ValueError("高德面边界坐标含无效值：{}".format(pair[:40]))
            if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                raise ValueError("高德面边界坐标超出经纬度范围：{}".format(pair[:40]))
            ring.append((lon, lat))
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def parse_center(text):
    """解析高德 center（``lon,lat``）；缺失或非法返回 None（不阻断边界）。"""
    value = str(text or "").strip()
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        lon, lat = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat


def ring_bbox(ring):
    longitudes = [point[0] for point in ring]
    latitudes = [point[1] for point in ring]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def point_in_ring(lon, lat, ring):
    """射线法点在多边形内判定（环首尾自动闭合）。"""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > lat) != (y2 > lat):
            cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < cross:
                inside = not inside
    return inside


def _point_in_rect(lon, lat, rect):
    return rect[0] <= lon <= rect[2] and rect[1] <= lat <= rect[3]


def _segments_intersect(first, second, third, fourth):
    def orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(value) < 1e-15:
            return 0
        return 1 if value > 0 else -1

    def on_segment(a, b, c):
        return (min(a[0], b[0]) - 1e-15 <= c[0] <= max(a[0], b[0]) + 1e-15
                and min(a[1], b[1]) - 1e-15 <= c[1] <= max(a[1], b[1]) + 1e-15)

    o1 = orientation(first, second, third)
    o2 = orientation(first, second, fourth)
    o3 = orientation(third, fourth, first)
    o4 = orientation(third, fourth, second)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(first, second, third):
        return True
    if o2 == 0 and on_segment(first, second, fourth):
        return True
    if o3 == 0 and on_segment(third, fourth, first):
        return True
    if o4 == 0 and on_segment(third, fourth, second):
        return True
    return False


def ring_intersects_rect(ring, rect):
    """环与 WGS84 范围是否有交集：包含、被包含或边相交都算。"""
    if not ring:
        return False
    bbox = ring_bbox(ring)
    if bbox[2] < rect[0] or bbox[0] > rect[2] or bbox[3] < rect[1] or bbox[1] > rect[3]:
        return False
    if any(_point_in_rect(lon, lat, rect) for lon, lat in ring):
        return True
    corners = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
    if any(point_in_ring(lon, lat, ring) for lon, lat in corners):
        return True
    count = len(ring)
    for index in range(count):
        start = ring[index]
        end = ring[(index + 1) % count]
        for edge_index in range(4):
            if _segments_intersect(start, end, corners[edge_index],
                                   corners[(edge_index + 1) % 4]):
                return True
    return False


def _signed_area(ring):
    total = 0.0
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _orient_ring(ring, clockwise):
    """ArcGIS 约定：外环顺时针（负面积），内环逆时针（正面积）。"""
    is_clockwise = _signed_area(ring) < 0
    if is_clockwise == clockwise:
        return ring
    return list(reversed(ring))


def _ring_inside(outer, ring):
    """粗略判定：环的代表点与外环范围都落在 outer 内（用于识别内环）。"""
    if not ring:
        return False
    inner_bbox = ring_bbox(ring)
    outer_bbox = ring_bbox(outer)
    if (inner_bbox[0] < outer_bbox[0] or inner_bbox[2] > outer_bbox[2]
            or inner_bbox[1] < outer_bbox[1] or inner_bbox[3] > outer_bbox[3]):
        return False
    return all(point_in_ring(lon, lat, outer) for lon, lat in ring[: min(3, len(ring))])


def split_rings(rings):
    """把高德未声明语义的环拆成 (外环, [内环]) 列表；最大环作为外环。"""
    ordered = sorted(rings, key=lambda ring: abs(_signed_area(ring)), reverse=True)
    parts = []
    for ring in ordered:
        area = abs(_signed_area(ring))
        if area < 1e-12:
            continue
        for _outer, holes in parts:
            if _ring_inside(_outer, ring):
                holes.append(ring)
                break
        else:
            parts.append((ring, []))
    return parts


def _pad_rect(extent, pad_ratio):
    lon_min, lat_min, lon_max, lat_max = [float(value) for value in extent]
    pad_lon = (lon_max - lon_min) * float(pad_ratio)
    pad_lat = (lat_max - lat_min) * float(pad_ratio)
    return (lon_min - pad_lon, lat_min - pad_lat, lon_max + pad_lon, lat_max + pad_lat)


def _node_from_item(item, rect):
    name = str(item.get("name") or "").strip()
    adcode = str(item.get("adcode") or "").strip()
    level = str(item.get("level") or "").strip()
    center_gcj = parse_center(item.get("center"))
    rings_gcj = parse_polyline(item.get("polyline"))
    rings_wgs = []
    for ring in rings_gcj:
        longitudes, latitudes = gcj02.gcj02_to_wgs84(
            [point[0] for point in ring], [point[1] for point in ring])
        rings_wgs.append(list(zip(longitudes, latitudes)))
    center_wgs = None
    if center_gcj is not None:
        center_wgs = gcj02.gcj02_to_wgs84(center_gcj[0], center_gcj[1])
    if rings_wgs:
        inside = any(ring_intersects_rect(ring, rect) for ring in rings_wgs)
    elif center_wgs is not None:
        inside = _point_in_rect(center_wgs[0], center_wgs[1], rect)
    else:
        inside = False
    if not inside:
        return None
    return {"adcode": adcode, "name": name, "level": level,
            "center_gcj": center_gcj, "center_wgs": center_wgs,
            "rings_gcj": rings_gcj, "rings_wgs": rings_wgs}


def _target_levels(level):
    """把 --level 归一为高德层级元组：县→(district,)、乡→(street,)、县乡→两者。"""
    key = str(level)
    if key in LEVELS:
        return LEVELS[key]
    if key in ("district", "street"):
        return (key,)
    raise ValueError("--level 只支持 县、乡 或 县乡（高德层级 district/street）")


def parse_district_payload(payload, level, extent_wgs84, pad_ratio=EXTENT_PAD_RATIO):
    """递归解析 ``districts[]``，抽出目标层级并与 WGS84 外扩范围过滤。

    ``level`` 可传中文（县/乡/县乡）或高德层级（district/street）；县乡同时收集
    区县与乡镇两级。返回 ``{"nodes": [...], "skipped": [...], "extent": ...,
    "padded_extent": ...}``。
    """
    targets = _target_levels(level)
    rect = _pad_rect(extent_wgs84, pad_ratio)
    nodes, skipped = [], []

    def walk(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("level") or "").strip() in targets:
                node = _node_from_item(item, rect)
                if node is None:
                    skipped.append({"name": item.get("name"), "adcode": item.get("adcode"),
                                    "reason": "边界/驻地与查询范围无交集或缺少几何"})
                else:
                    nodes.append(node)
            walk(item.get("districts") or [])

    walk(payload.get("districts") if isinstance(payload, dict) else [])
    unique = {}
    for node in nodes:
        identity = node["adcode"] or ("{}-{:.6f},{:.6f}".format(node["name"], *node["center_wgs"])
                                      if node["center_wgs"] else node["name"])
        unique[identity] = node
    ordered = sorted(unique.values(), key=lambda node: (node["adcode"] or "999999", node["name"]))
    target_level = targets[0] if len(targets) == 1 else "+".join(targets)
    return {"nodes": ordered, "skipped": skipped, "target_level": target_level,
            "extent": [float(value) for value in extent_wgs84], "padded_extent": list(rect)}


# ---------------------------------------------------------------------------
# 高德接口（网络；测试中一律 monkeypatch）
# ---------------------------------------------------------------------------

def _request_json(url, params, timeout=REQUEST_TIMEOUT):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url + "?" + query, headers={"User-Agent": "SupplementaryCultivatedLandAcceptance/1.58"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError("高德行政区划请求失败（HTTP {}）。请检查网络、Key 权限和服务配额。".format(
            exc.code)) from None
    except Exception:
        # urllib 异常文本可能带完整请求地址（含 key），这里只给固定原因。
        raise ValueError("高德行政区划请求未完成，请检查网络或稍后重试；请求地址和密钥不会写入日志。") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("高德接口响应超过 8 MB 安全读取上限。")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        raise ValueError("高德接口响应不是可读取的 JSON。") from None
    if not isinstance(payload, dict):
        raise ValueError("高德接口响应结构无法识别（不是 JSON 对象）。")
    if str(payload.get("status", "")) != "1":
        code = re.sub(r"[^0-9]", "", str(payload.get("infocode", "")))[:10]
        raise ValueError("高德接口返回失败{}；请检查 Web 服务 Key 权限、关键词和服务配额。".format(
            "（错误码 {}）".format(code) if code else ""))
    return payload


def reverse_adcode(lon, lat, key):
    """按中心经纬度反查所属区县 adcode（用于无 --keywords 时确定查询对象）。

    高德 v3/config/district 没有 location 参数，因此先用官方逆地理编码
    v3/geocode/regeo 取 addressComponent.adcode，再拿 adcode 作为 keywords 查下级。
    **入参 lon/lat 必须是 GCJ-02**（高德 location 的基准），调用方负责从 WGS84 转换。
    """
    payload = _request_json(AMAP_REGEO_URL, {
        "key": key, "location": "{:.6f},{:.6f}".format(float(lon), float(lat)),
        "extensions": "base", "output": "JSON"})
    component = (payload.get("regeocode") or {}).get("addressComponent") or {}
    adcode = str(component.get("adcode") or "").strip()
    if not adcode:
        raise ValueError("高德逆地理编码未返回所属区县 adcode，请改用 --keywords 指定县/市名")
    return adcode


def query_district(key, keywords, subdistrict):
    """调用高德行政区划接口（extensions=all，返回下级 districts 树）。"""
    return _request_json(AMAP_DISTRICT_URL, {
        "key": key, "keywords": str(keywords), "subdistrict": int(subdistrict),
        "extensions": "all", "output": "JSON"})


def _amap_key():
    """复用 poi_reference 的 DPAPI 读取；不在本模块复制解密逻辑。"""
    try:
        from acceptance.poi_reference import load_amap_key
    except ImportError:  # 直接运行脚本时
        from poi_reference import load_amap_key  # type: ignore[no-redef]
    return load_amap_key()


# ---------------------------------------------------------------------------
# arcpy 写库（Pro 专属；测试用桩或纯函数拆分覆盖）
# ---------------------------------------------------------------------------

def _add_fields(arcpy, feature_class):
    for field, kind, length, alias in FIELDS:
        options = {"field_alias": alias}
        if length:
            options["field_length"] = length
        arcpy.management.AddField(feature_class, field, kind, **options)


def _ring_array(arcpy, ring):
    return arcpy.Array([arcpy.Point(lon, lat) for lon, lat in ring])


def polygon_geometry(arcpy, rings, spatial_reference):
    """按 ArcGIS 环方向约定构建多边形：外环顺时针、内环逆时针。"""
    if not rings:
        raise ValueError("行政区划边界没有任何可用环")
    ring_arrays = []
    for outer, holes in split_rings(rings):
        ring_arrays.append(_ring_array(arcpy, _orient_ring(outer, clockwise=True)))
        for hole in holes:
            ring_arrays.append(_ring_array(arcpy, _orient_ring(hole, clockwise=False)))
    return arcpy.Polygon(arcpy.Array(ring_arrays), spatial_reference)


def _insert_features(arcpy, feature_class, rows):
    fields = [field[0] for field in FIELDS]
    with arcpy.da.InsertCursor(feature_class, ["SHAPE@"] + fields) as cursor:
        for geometry, values in rows:
            cursor.insertRow([geometry] + values)


def write_gdb(arcpy, out_gdb, name, target_wkid, nodes):
    """写 ``<name>_区界``（POLYGON）与 ``<name>_驻地``（POINT），再投影到目标坐标系。"""
    path = os.path.abspath(os.path.expanduser(out_gdb))
    if not path.lower().endswith(".gdb"):
        raise ValueError("--out-gdb 必须是 .gdb 文件地理数据库路径：{}".format(path))
    target_wkid = int(target_wkid)
    if target_wkid <= 0:
        raise ValueError("--wkid 必须是正整数坐标系代码")
    if not any(node.get("rings_wgs") for node in nodes):
        raise ValueError("NO_BOUNDARY：没有可用界线要素（节点只有驻地中心或边界为空），"
                         "未创建空界线要素类；请调整 --level/--keywords/--extent 或直接参考驻地结果")
    created = False
    if not arcpy.Exists(path):
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        arcpy.management.CreateFileGDB(parent, os.path.basename(path))
        created = True
    elif not os.path.isdir(path):
        raise ValueError("--out-gdb 指向的不是有效的文件地理数据库：{}".format(path))

    wgs84 = arcpy.SpatialReference(4326)
    target_sr = arcpy.SpatialReference(target_wkid)
    boundary_name, label_name = name + "_区界", name + "_驻地"
    boundary_temp, label_temp = boundary_name + "_WGS84", label_name + "_WGS84"
    for feature_name in (boundary_temp, label_temp, boundary_name, label_name):
        if arcpy.Exists(os.path.join(path, feature_name)):
            raise ValueError("成果要素类已存在，请换用新的 --name 或新 GDB：{}".format(feature_name))

    boundary_rows, label_rows = [], []
    for node in nodes:
        center_lon = center_lat = None
        if node.get("center_wgs") is not None:
            center_lon, center_lat = node["center_wgs"]
        level_name = LEVEL_NAMES.get(node.get("level"), node.get("level") or "")
        values = [node.get("adcode") or "", node.get("name") or "", level_name,
                  center_lon, center_lat, SOURCE_TEXT]
        if node.get("rings_wgs"):
            boundary_rows.append((polygon_geometry(arcpy, node["rings_wgs"], wgs84), values))
        if node.get("center_wgs") is not None:
            point = arcpy.PointGeometry(arcpy.Point(center_lon, center_lat), wgs84)
            label_rows.append((point, values))

    boundary_fc = os.path.join(path, boundary_name)
    label_fc = os.path.join(path, label_name)
    boundary_temp_path = os.path.join(path, boundary_temp)
    label_temp_path = os.path.join(path, label_temp)
    created_finals = []
    completed = False
    try:
        arcpy.management.CreateFeatureclass(path, boundary_temp, "POLYGON", spatial_reference=wgs84)
        _add_fields(arcpy, boundary_temp_path)
        _insert_features(arcpy, boundary_temp_path, boundary_rows)
        arcpy.management.CreateFeatureclass(path, label_temp, "POINT", spatial_reference=wgs84)
        _add_fields(arcpy, label_temp_path)
        _insert_features(arcpy, label_temp_path, label_rows)
        if target_wkid == 4326:
            arcpy.management.CopyFeatures(boundary_temp_path, boundary_fc)
            created_finals.append(boundary_fc)
            arcpy.management.CopyFeatures(label_temp_path, label_fc)
            created_finals.append(label_fc)
        else:
            # WGS84→CGCS2000 视为同一基准；未指定七参数，datum_note 已在结果中声明。
            arcpy.management.Project(boundary_temp_path, boundary_fc, target_sr)
            created_finals.append(boundary_fc)
            arcpy.management.Project(label_temp_path, label_fc, target_sr)
            created_finals.append(label_fc)
        completed = True
    except Exception as exc:  # noqa: BLE001 - 失败要清理临时/半成品要素类并保留原因
        raise ValueError("行政区划要素写入或投影到目标坐标系失败：{}".format(exc)) from None
    finally:
        # 统一清理 WGS84 临时要素类；失败时还要清理本次可能产生的正式要素类。
        for feature_path in (boundary_temp_path, label_temp_path):
            try:
                arcpy.management.Delete(feature_path)
            except Exception:  # noqa: BLE001 - 清理失败不覆盖主错误
                pass
        if not completed:
            for feature_path in created_finals:
                try:
                    arcpy.management.Delete(feature_path)
                except Exception:  # noqa: BLE001 - 清理失败不覆盖主错误
                    pass

    return {"gdb": path, "boundary_fc": boundary_fc, "label_fc": label_fc,
            "feature_count": len(boundary_rows), "label_count": len(label_rows),
            "created_gdb": created}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(args):
    out_gdb = os.path.abspath(os.path.expanduser(str(args.out_gdb)))
    name = str(args.name or "").strip()
    if not name or any(character in name for character in '\\/:*?"<>|'):
        raise ValueError("--name 只能包含合法文件名字符，不能带路径分隔符")
    target_wkid = int(args.wkid)
    extent = tuple(float(value) for value in args.extent)
    if not (len(extent) == 4 and -180.0 <= extent[0] < extent[2] <= 180.0
            and -90.0 <= extent[1] < extent[3] <= 90.0):
        raise ValueError("--extent 须为 WGS84 经纬度 xmin ymin xmax ymax，且 min < max")
    level_key = str(args.level)
    targets = _target_levels(level_key)
    subdistrict = int(args.subdistrict)
    if not 0 <= subdistrict <= 3:
        raise ValueError("--subdistrict 须为 0–3 的整数")
    keywords = str(args.keywords or "").strip()
    key = _amap_key()
    if not keywords:
        center_lon = (extent[0] + extent[2]) / 2.0
        center_lat = (extent[1] + extent[3]) / 2.0
        # 高德 v3/geocode/regeo 的 location 是 GCJ-02；把 WGS84 直接传过去会偏移约 500 m，
        # 在区县边界附近会反查到相邻区县，因此必须先做 WGS84→GCJ-02。
        reverse_lon, reverse_lat = gcj02.wgs84_to_gcj02(center_lon, center_lat)
        keywords = reverse_adcode(reverse_lon, reverse_lat, key)
    payload = query_district(key, keywords, subdistrict)
    parsed = parse_district_payload(payload, level_key, extent, EXTENT_PAD_RATIO)
    nodes = parsed["nodes"]
    if not nodes:
        raise ValueError("范围内未取到 {} 级行政区划；请调整 --extent/--keywords/--subdistrict 或 --level".format(
            level_key))
    boundary_nodes = [node for node in nodes if node.get("rings_wgs")]
    if not boundary_nodes:
        # 只有驻地中心、没有任何界线：不建空界线要素类，明确失败（NO_BOUNDARY）。
        raise ValueError("NO_BOUNDARY：范围内只取到驻地中心、没有可用界线（该层级 polyline 可能为空）；"
                         "请改用 --level 县/县乡、调整 --keywords/--extent，或直接参考驻地结果")
    import arcpy  # 由 ArcGIS Pro Python 提供
    written = write_gdb(arcpy, out_gdb, name, target_wkid, nodes)
    present = {node["level"] for node in nodes}
    levels = [LEVEL_NAMES[name] for name in LEVEL_ORDER if name in present]
    note = "高德界线与驻地仅供参考，不是法定行政界线；正式报件须使用三调/民政官方界线。"
    if "street" in targets and "street" not in present:
        note += " 本次未取到乡镇级界线（可增大 --subdistrict）；当前只输出县级。"
    result = {
        "status": "OK",
        "requested_level": level_key,
        "gdb": written["gdb"],
        "boundary_fc": written["boundary_fc"],
        "label_fc": written["label_fc"],
        "feature_count": written["feature_count"],
        "label_count": written["label_count"],
        "levels": levels,
        "adcodes": sorted({node["adcode"] for node in nodes if node["adcode"]}),
        "source": "高德行政区划",
        "coordinate": "GCJ-02→WGS84→目标CRS",
        "legal_status": "REFERENCE_ONLY_NOT_LEGAL_BOUNDARY",
        "datum_note": "WGS84→CGCS2000 按基准视为一致处理，未做七参数/布尔莎转换；正式成果须核实基准转换依据。",
        "note": note,
        "skipped": parsed["skipped"][:20],
    }
    return result


def main(argv=None):
    parser = _ArgumentParser(
        description="按高德行政区划生成县/乡参考界线与驻地（区图）。默认县乡两级同出。")
    parser.add_argument("--out-gdb", required=True, help="输出 .gdb 绝对路径")
    parser.add_argument("--name", required=True, help="要素类基名，例如 上林")
    parser.add_argument("--wkid", required=True, type=int, help="目标坐标系 WKID")
    parser.add_argument("--extent", required=True, nargs=4, type=float,
                        metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="WGS84 经纬度范围")
    parser.add_argument("--keywords", default="", help="县/市名或 adcode；缺省按范围中心反查（内部转 GCJ-02）")
    parser.add_argument("--subdistrict", type=int, default=2, help="下级层级数（0–3，默认 2）")
    parser.add_argument("--level", choices=("县乡", "县", "乡"), default=DEFAULT_LEVEL,
                        help="县乡=同时输出县界与乡镇界（默认）；县/乡=只输出单级")
    try:
        args = parser.parse_args(argv)
        result = run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 调用方需要真实失败原因
        print(json.dumps({"status": "FAIL", "error": "{}: {}".format(type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
