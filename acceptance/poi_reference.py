"""Fetch bounded AMap POI reference points for a selected project polygon."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from urllib.parse import urlencode


AMAP_POLYGON_URL = "https://restapi.amap.com/v5/place/polygon"
PAGE_SIZE = 25
MAX_RESULTS = 200
REQUEST_TIMEOUT = 15


def _arcpy():
    import arcpy
    return arcpy


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _load_amap_key():
    """Read the current-user DPAPI secret written by ServiceCredentials.cs."""
    if os.name != "nt":
        raise ValueError("高德密钥只能在 Windows 当前用户配置中读取。")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ValueError("未找到当前 Windows 用户的本地应用数据目录。")
    path = os.path.join(local_app_data, "SamplingAssistant", "credentials", "amap_poi.key.dpapi")
    if not os.path.isfile(path):
        raise ValueError("尚未配置高德 Web 服务 Key；请先点击“配置高德Key”。")
    try:
        encrypted = bytes.fromhex(open(path, "r", encoding="ascii").read().strip())
    except (OSError, UnicodeError, ValueError):
        raise ValueError("本机高德密钥文件无法读取，请重新配置。") from None
    if not encrypted:
        raise ValueError("本机高德密钥文件为空，请重新配置。")

    input_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = _DataBlob(len(encrypted), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _DataBlob()
    description = ctypes.c_wchar_p()
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p),
                          ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    unprotect.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not unprotect(ctypes.byref(input_blob), ctypes.byref(description), None, None, None, 0,
                         ctypes.byref(output_blob)):
            raise ValueError("当前 Windows 用户无法解密高德密钥，请重新配置。")
        key = ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-16-le").strip()
        if not key:
            raise ValueError("本机高德密钥为空，请重新配置。")
        return key
    finally:
        if output_blob.pbData:
            ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))
        ctypes.memset(input_buffer, 0, len(encrypted))


def load_amap_key():
    """公开读取入口：复用本模块的 DPAPI 解密实现，不复制解密逻辑。

    供底图/行政区划等同样使用高德 Web 服务 Key 的模块调用；错误信息保持中文，
    不返回、不打印明文密钥。
    """
    return _load_amap_key()


def _transform_lat(x, y):
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return value


def _transform_lon(x, y):
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    value += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return value


def _inside_china(lon, lat):
    return 72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271


def _wgs84_to_gcj02(lon, lat):
    if not _inside_china(lon, lat):
        return lon, lat
    x, y = lon - 105.0, lat - 35.0
    dlat, dlon = _transform_lat(x, y), _transform_lon(x, y)
    rad_lat = lat / 180.0 * math.pi
    magic = 1.0 - 0.00669342162296594323 * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    dlat = dlat * 180.0 / ((6335552.717000426 * magic * sqrt_magic) * math.pi)
    dlon = dlon * 180.0 / (6378245.0 / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lon + dlon, lat + dlat


def _gcj02_to_wgs84_approx(lon, lat):
    if not _inside_china(lon, lat):
        return lon, lat
    x, y = lon, lat
    for _ in range(5):
        shifted_lon, shifted_lat = _wgs84_to_gcj02(x, y)
        x += lon - shifted_lon
        y += lat - shifted_lat
    return x, y


def _projected_scope(source, transformation):
    arcpy = _arcpy()
    desc = arcpy.Describe(source)
    if desc.shapeType != "Polygon":
        raise ValueError("POI 查询范围必须是面图层。")
    source_sr = desc.spatialReference
    if not source_sr or source_sr.name == "Unknown":
        raise ValueError("查询范围坐标系未知，不能定位 POI。")
    wgs84 = arcpy.SpatialReference(4326)
    gcs = source_sr.GCS
    gcs_code = int(getattr(gcs, "factoryCode", 0) or 0) if gcs else 0
    if gcs_code == 4326 and transformation:
        raise ValueError("查询范围已是 WGS84，无需填写基准转换。")
    if gcs_code != 4326:
        available = arcpy.ListTransformations(source_sr, wgs84)
        if not transformation or transformation not in available:
            raise ValueError("源坐标系不是 WGS84；请从“基准转换”参数中选择 ArcGIS 列出的转换方法，或先准备 WGS84 查询范围。")
    geometries = []
    with arcpy.da.SearchCursor(source, ["SHAPE@"]) as cursor:
        for (geometry,) in cursor:
            if geometry and geometry.pointCount:
                projected = geometry.projectAs(wgs84, transformation) if transformation else geometry.projectAs(wgs84)
                if projected and projected.pointCount:
                    geometries.append(projected)
    if not geometries:
        raise ValueError("查询范围为空，或当前选择集没有有效面。")
    return geometries, wgs84, source_sr.name


def _polygon_bounds(geometries):
    xmin = min(g.extent.XMin for g in geometries)
    ymin = min(g.extent.YMin for g in geometries)
    xmax = max(g.extent.XMax for g in geometries)
    ymax = max(g.extent.YMax for g in geometries)
    corners = [_wgs84_to_gcj02(x, y) for x, y in ((xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax))]
    left = min(p[0] for p in corners)
    right = max(p[0] for p in corners)
    bottom = min(p[1] for p in corners)
    top = max(p[1] for p in corners)
    if not (-180 <= left < right <= 180 and -90 <= bottom < top <= 90):
        raise ValueError("查询范围转换后经纬度无效。")
    # 高德矩形区域搜索接受左上、右下两个坐标对。
    return "{:.6f},{:.6f}|{:.6f},{:.6f}".format(left, top, right, bottom)


def _request_page(key, polygon, keywords, types, page_num):
    params = {"key": key, "polygon": polygon, "page_size": PAGE_SIZE, "page_num": page_num,
              "output": "JSON"}
    if keywords:
        params["keywords"] = keywords
    if types:
        params["types"] = types
    request = urllib.request.Request(AMAP_POLYGON_URL + "?" + urlencode(params),
                                     headers={"User-Agent": "SupplementaryCultivatedLandAcceptance/1.58"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError("高德 POI 请求失败（HTTP {}）。请检查网络、Key 权限和服务配额。".format(exc.code)) from None
    except Exception:
        # urllib exceptions may include the full request URL, which contains the key.
        raise ValueError("高德 POI 请求未完成，请检查网络或稍后重试。请求地址和密钥不会写入日志。") from None
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("高德接口响应超过 4 MB 安全读取上限。")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        raise ValueError("高德接口响应不是可读取的 JSON。") from None
    if str(payload.get("status", "")) != "1":
        code = re.sub(r"[^0-9]", "", str(payload.get("infocode", "")))[:10]
        raise ValueError("高德接口返回失败{}；请检查 Web 服务 Key 权限、关键词和服务配额。".format(
            "（错误码 {}）".format(code) if code else ""))
    return payload


def _text(value, limit):
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v is not None)
    return str(value or "")[:limit]


def _create_output(arcpy, out_folder, wgs84, rows, geometries, metadata):
    out_folder = os.path.abspath(os.path.expanduser(out_folder))
    if os.path.exists(out_folder):
        raise ValueError("成果目录已存在，为防止覆盖请另选一个新目录。")
    os.makedirs(out_folder, exist_ok=False)
    gdb_name = "POI参考数据.gdb"
    arcpy.management.CreateFileGDB(out_folder, gdb_name)
    gdb = os.path.join(out_folder, gdb_name)
    feature_name = "POI参考数据_不可用于验收报件"
    feature_class = os.path.join(gdb, feature_name)
    arcpy.management.CreateFeatureclass(gdb, feature_name, "POINT", spatial_reference=wgs84)
    fields = [
        ("POI_ID", "TEXT", 80, "高德POI编号"), ("NAME", "TEXT", 200, "名称"),
        ("TYPE", "TEXT", 160, "类型"), ("TYPECODE", "TEXT", 40, "类型码"),
        ("ADDRESS", "TEXT", 300, "地址"), ("PROVINCE", "TEXT", 80, "省份"),
        ("CITY", "TEXT", 80, "城市"), ("DISTRICT", "TEXT", 80, "区县"),
        ("GCJ_LON", "DOUBLE", None, "原始GCJ02经度"), ("GCJ_LAT", "DOUBLE", None, "原始GCJ02纬度"),
        ("SOURCE", "TEXT", 80, "数据来源"), ("KEYWORD", "TEXT", 80, "检索关键词"),
        ("QUERY_TYPES", "TEXT", 500, "检索类型码"),
        ("QUERY_AT", "TEXT", 40, "查询时间"), ("SCOPE_CRS", "TEXT", 100, "范围原坐标系"),
        ("POSITION_QA", "TEXT", 120, "位置质量说明")]
    for name, kind, length, alias in fields:
        kwargs = {"field_alias": alias}
        if length:
            kwargs["field_length"] = length
        arcpy.management.AddField(feature_class, name, kind, **kwargs)
    cursor_fields = ["SHAPE@XY"] + [row[0] for row in fields]
    with arcpy.da.InsertCursor(feature_class, cursor_fields) as cursor:
        for row in rows:
            point = row["wgs84"]
            attrs = [row["id"], row["name"], row["type"], row["typecode"], row["address"],
                     row["province"], row["city"], row["district"], row["gcj_lon"], row["gcj_lat"],
                     "高德 Web POI 2.0", metadata["keyword"], metadata["types"], metadata["query_at"],
                     metadata["scope_crs"], "GCJ-02近似反算；仅供位置参考"]
            cursor.insertRow([(point[0], point[1])] + attrs)
    return feature_class


def search_poi_reference(source, keywords, types, transformation, max_results, out_folder):
    """Search one bounded project extent, exact-clip to the input polygons and write a WGS84 feature class."""
    arcpy = _arcpy()
    keywords, types = str(keywords or "").strip(), str(types or "").strip()
    if not keywords and not types:
        raise ValueError("关键词与高德 POI 类型码至少填写一项。")
    if len(keywords) > 80:
        raise ValueError("单个 POI 关键词不能超过 80 个字符。")
    if types and (len(types) > 500 or any(not re.fullmatch(r"\d{2,10}", code) for code in types.split("|"))):
        raise ValueError("POI 类型码只能填写数字；多个类型码用 | 分隔。")
    if not 1 <= int(max_results) <= MAX_RESULTS:
        raise ValueError("最多读取结果数必须在 1–200 之间。")

    out_folder = os.path.abspath(os.path.expanduser(out_folder))
    if os.path.exists(out_folder):
        raise ValueError("成果目录已存在，为防止覆盖请另选一个新目录。")
    key = _load_amap_key()
    geometries, wgs84, source_crs = _projected_scope(source, str(transformation or "").strip())
    polygon = _polygon_bounds(geometries)
    first_page = _request_page(key, polygon, keywords, types, 1)
    try:
        total_count = max(0, int(first_page.get("count", 0) or 0))
    except (TypeError, ValueError):
        total_count = 0
    target_count = min(int(max_results), MAX_RESULTS, total_count if total_count else int(max_results))
    pages = min(math.ceil(target_count / PAGE_SIZE), MAX_RESULTS // PAGE_SIZE)
    fetched = list(first_page.get("pois") or [])[:target_count]
    for page in range(2, pages + 1):
        if len(fetched) >= target_count:
            break
        time.sleep(0.25)
        payload = _request_page(key, polygon, keywords, types, page)
        page_rows = payload.get("pois") or []
        if not page_rows:
            break
        fetched.extend(page_rows[:max(0, target_count - len(fetched))])

    seen = set()
    rows = []
    for poi in fetched:
        try:
            raw_location = str(poi.get("location") or "").split(",")
            gcj_lon, gcj_lat = float(raw_location[0]), float(raw_location[1])
            if not (math.isfinite(gcj_lon) and math.isfinite(gcj_lat) and -180 <= gcj_lon <= 180 and -90 <= gcj_lat <= 90):
                continue
        except (IndexError, TypeError, ValueError):
            continue
        wgs_lon, wgs_lat = _gcj02_to_wgs84_approx(gcj_lon, gcj_lat)
        point = arcpy.PointGeometry(arcpy.Point(wgs_lon, wgs_lat), wgs84)
        if not any(poly.contains(point) or poly.touches(point) for poly in geometries):
            continue
        poi_id = _text(poi.get("id"), 80)
        identity = ("id", poi_id) if poi_id else ("name_location", _text(poi.get("name"), 200), round(gcj_lon, 6), round(gcj_lat, 6))
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({
            "id": poi_id, "name": _text(poi.get("name"), 200), "type": _text(poi.get("type"), 160),
            "typecode": _text(poi.get("typecode"), 40), "address": _text(poi.get("address"), 300),
            "province": _text(poi.get("pname"), 80), "city": _text(poi.get("cityname"), 80),
            "district": _text(poi.get("adname"), 80), "gcj_lon": gcj_lon, "gcj_lat": gcj_lat,
            "wgs84": (wgs_lon, wgs_lat)})

    query_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {"keyword": keywords, "types": types, "query_at": query_at, "scope_crs": source_crs}
    output = _create_output(arcpy, out_folder, wgs84, rows, geometries, metadata)
    truncated = total_count >= MAX_RESULTS or total_count > int(max_results) or len(fetched) < min(total_count, int(max_results))
    return {"output": output, "out_folder": os.path.abspath(out_folder), "fetched_count": len(fetched),
            "inside_count": len(rows), "output_count": len(rows), "truncated": truncated}
