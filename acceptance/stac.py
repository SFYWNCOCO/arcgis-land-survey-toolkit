"""Constrained Earth Search STAC access for Sentinel-2 L2A screening."""
from __future__ import annotations

import datetime as dt
import json
import math
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
SEARCH_HOST = "earth-search.aws.element84.com"
ASSET_HOSTS = {
    "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com",
}
COLLECTION = "sentinel-2-c1-l2a"
MAX_PAGE_SIZE = 100
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30


def _finite_number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("{}缺失或不是数值。".format(label))
    if not math.isfinite(result):
        raise ValueError("{}不是有限数值。".format(label))
    return result


def _date(value):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("日期必须使用 YYYY-MM-DD 格式。")


def validate_asset_href(href):
    """Accept only unsigned HTTPS assets from the configured public bucket."""
    parsed = urlparse(str(href or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise ValueError("影像资产 URL 必须使用 HTTPS。")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("影像资产 URL 含有不允许的认证信息或端口。")
    if host not in ASSET_HOSTS:
        raise ValueError("不允许的资产主机：{}。".format(host or "(空)"))
    if parsed.query or parsed.fragment:
        raise ValueError("不接受带查询参数或片段的签名资产 URL。")
    if not parsed.path.lower().endswith(".tif"):
        raise ValueError("影像资产必须为 GeoTIFF。")
    return href


def validate_search_href(href):
    parsed = urlparse(str(href or ""))
    if (parsed.scheme.lower() != "https" or
            (parsed.hostname or "").lower() != SEARCH_HOST or
            parsed.username or parsed.password or
            parsed.port not in (None, 443) or
            parsed.path.rstrip("/") != "/v1/search"):
        raise ValueError("STAC分页链接不属于已配置的 Earth Search 搜索端点。")
    return href


def _band_labels(asset):
    eo_bands = asset.get("eo:bands") or []
    result = set()
    for band in eo_bands:
        for key in ("name", "common_name"):
            value = band.get(key)
            if value:
                result.add(str(value).strip().casefold())
    return result


def _asset_role(key, asset):
    labels = _band_labels(asset)
    if "red" in labels or "b04" in labels:
        return "red"
    if "nir" in labels or "b08" in labels:
        return "nir"
    if str(key).strip().casefold() == "scl" or "scl" in labels:
        return "scl"
    return None


def _asset_metadata(key, asset, role):
    href = validate_asset_href(asset.get("href"))
    raster_bands = asset.get("raster:bands") or []
    if len(raster_bands) != 1:
        raise ValueError("{}资产必须包含一个明确的栅格波段元数据。".format(role))
    band = raster_bands[0]
    nodata = band.get("nodata")
    if nodata is None:
        raise ValueError("{}资产缺少 NoData 元数据。".format(role))
    eo_bands = asset.get("eo:bands") or []
    declared_name = ""
    if eo_bands and isinstance(eo_bands[0], dict):
        declared_name = str(eo_bands[0].get("name") or "").strip()
    output = {
        "asset_key": str(key),
        "href": href,
        "band_name": declared_name or str(key).strip(),
        "eo_labels": sorted(_band_labels(asset)),
        "nodata": nodata,
        "gsd_m": _finite_number(band.get("spatial_resolution"), role + "空间分辨率"),
        "data_type": str(band.get("data_type") or "unknown"),
    }
    if role in ("red", "nir"):
        output["scale"] = _finite_number(band.get("scale"), role + " scale/offset")
        output["offset"] = _finite_number(band.get("offset"), role + " scale/offset")
        if output["scale"] <= 0:
            raise ValueError("{}资产 scale 必须大于0。".format(role))
    return output


def parse_item(item):
    if not isinstance(item, dict) or item.get("type") != "Feature":
        raise ValueError("STAC Item 不是 GeoJSON Feature。")
    if item.get("collection") != COLLECTION:
        raise ValueError("STAC Item collection 不是 Sentinel-2 C1 L2A。")
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        raise ValueError("STAC Item 缺少 id。")
    properties = item.get("properties") or {}
    platform = str(properties.get("platform") or "").strip().lower()
    if not platform.startswith("sentinel-2"):
        raise ValueError("STAC Item 平台不是 Sentinel-2。")
    timestamp = str(properties.get("datetime") or "")
    try:
        captured = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("STAC Item datetime 缺失或格式无效。")
    if captured.tzinfo is None:
        raise ValueError("STAC Item datetime 必须包含时区。")
    cloud = _finite_number(properties.get("eo:cloud_cover"), "景级云量")
    if cloud < 0 or cloud > 100:
        raise ValueError("景级云量必须在0到100之间。")
    geometry = item.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("STAC Item 缺少有效面足迹。")

    assets = item.get("assets") or {}
    found = {}
    for key, asset in assets.items():
        role = _asset_role(key, asset)
        if role and role not in found:
            found[role] = (key, asset)
    for role in ("red", "nir", "scl"):
        if role not in found:
            raise ValueError("Sentinel-2 L2A资产缺少{}。".format({"red": "B04红光", "nir": "B08近红外", "scl": "SCL"}[role]))
    parsed_assets = {
        role: _asset_metadata(key, asset, role)
        for role, (key, asset) in found.items()
    }
    red_labels = set(parsed_assets["red"]["eo_labels"])
    nir_labels = set(parsed_assets["nir"]["eo_labels"])
    if not red_labels.intersection(("red", "b04")) or not nir_labels.intersection(("nir", "b08")):
        raise ValueError("红光/NIR资产的 EO 波段语义与 B04/B08 不匹配。")
    scl_name = parsed_assets["scl"]["band_name"]
    if scl_name and scl_name not in ("scl",):
        # SCL assets commonly expose the key rather than an eo:bands declaration.
        if parsed_assets["scl"]["asset_key"].casefold() != "scl":
            raise ValueError("SCL 资产无法通过元数据确认。")

    baseline = str(properties.get("s2:processing_baseline") or "").strip()
    grid_code = str(properties.get("grid:code") or properties.get("s2:mgrs_tile") or "").strip()
    return {
        "item_id": item_id,
        "collection": COLLECTION,
        "datetime": captured.isoformat().replace("+00:00", "Z"),
        "acquisition_date": captured.date().isoformat(),
        "platform": platform,
        "processing_baseline": baseline or None,
        "cloud_cover_pct": cloud,
        "grid_code": grid_code or None,
        "geometry": geometry,
        "assets": parsed_assets,
    }


def parse_search_page(page):
    if not isinstance(page, dict) or page.get("type") != "FeatureCollection":
        raise ValueError("STAC 搜索响应不是 FeatureCollection。")
    features = page.get("features")
    if not isinstance(features, list):
        raise ValueError("STAC 搜索响应缺少 features 列表。")
    items, rejected, seen = [], [], set()
    for raw in features:
        item_id = str(raw.get("id") or "(无id)") if isinstance(raw, dict) else "(非对象)"
        try:
            parsed = parse_item(raw)
            if parsed["item_id"] not in seen:
                seen.add(parsed["item_id"])
                items.append(parsed)
        except (TypeError, ValueError) as exc:
            rejected.append({"item_id": item_id, "reason": str(exc)})
    next_link = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
    return {"items": items, "rejected": rejected, "next": next_link}


def group_candidates(items):
    """Group adjacent MGRS tiles only when date, platform and baseline agree."""
    groups = {}
    for item in items:
        key = (item["acquisition_date"], item["platform"], item["processing_baseline"])
        group = groups.setdefault(key, {
            "acquisition_date": key[0],
            "platform": key[1],
            "processing_baseline": key[2],
            "items": [],
            "tiles": set(),
            "item_ids": set(),
            "cloud_cover_min_pct": None,
            "cloud_cover_max_pct": None,
        })
        if item["item_id"] in group["item_ids"]:
            continue
        group["item_ids"].add(item["item_id"])
        group["items"].append(item)
        if item.get("grid_code"):
            group["tiles"].add(item["grid_code"])
        cloud = item["cloud_cover_pct"]
        group["cloud_cover_min_pct"] = cloud if group["cloud_cover_min_pct"] is None else min(group["cloud_cover_min_pct"], cloud)
        group["cloud_cover_max_pct"] = cloud if group["cloud_cover_max_pct"] is None else max(group["cloud_cover_max_pct"], cloud)
    result = []
    for group in groups.values():
        group["items"].sort(key=lambda item: (item.get("grid_code") or "", item["item_id"]))
        group["tiles"] = sorted(group["tiles"])
        group["item_ids"] = sorted(group["item_ids"])
        group["item_count"] = len(group["items"])
        result.append(group)
    return sorted(result, key=lambda group: (group["acquisition_date"], group["platform"], group["processing_baseline"] or ""))


def build_search_payload(geometry, start_date, end_date, limit=MAX_PAGE_SIZE):
    start = _date(start_date)
    end = _date(end_date)
    if end < start:
        raise ValueError("结束日期不能早于开始日期。")
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("检索范围必须是 WGS84 GeoJSON 面。")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, (list, tuple)):
        raise ValueError("检索几何缺少坐标。")
    rings = coordinates if geometry["type"] == "Polygon" else [ring for polygon in coordinates for ring in polygon]
    if not rings:
        raise ValueError("检索几何没有面环。")
    for ring in rings:
        if not isinstance(ring, (list, tuple)) or len(ring) < 4:
            raise ValueError("检索面环至少需要4个坐标点。")
        normalized = []
        for point in ring:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                raise ValueError("检索几何包含无效坐标点。")
            lon = _finite_number(point[0], "经度")
            lat = _finite_number(point[1], "纬度")
            if lon < -180 or lon > 180 or lat < -90 or lat > 90:
                raise ValueError("检索几何坐标不在 WGS84 经纬度范围内。")
            normalized.append((lon, lat))
        if normalized[0] != normalized[-1]:
            raise ValueError("检索面环必须闭合。")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError("每页条数必须为整数。")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError("每页条数必须在1到{}之间。".format(MAX_PAGE_SIZE))
    return {
        "collections": [COLLECTION],
        "intersects": geometry,
        "datetime": "{}T00:00:00Z/{}T23:59:59Z".format(start.isoformat(), end.isoformat()),
        "limit": limit,
    }


def _read_json_response(response):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("STAC 响应超过16 MB安全上限。")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("STAC 响应不是有效 UTF-8 JSON：{}".format(exc))


def search_items(geometry, start_date, end_date, max_cloud_cover=80,
                 limit=MAX_PAGE_SIZE, max_pages=MAX_PAGES,
                 timeout=DEFAULT_TIMEOUT_SECONDS, opener=None):
    """Search Earth Search, retaining incomplete-pagination status explicitly."""
    payload = build_search_payload(geometry, start_date, end_date, limit)
    max_cloud = _finite_number(max_cloud_cover, "最大景级云量")
    if max_cloud < 0 or max_cloud > 100:
        raise ValueError("最大景级云量必须在0到100之间。")
    try:
        page_cap = int(max_pages)
    except (TypeError, ValueError):
        raise ValueError("最大翻页数必须为正整数。")
    if page_cap < 1 or page_cap > 100:
        raise ValueError("最大翻页数必须在1到100之间。")
    opener = opener or urlopen
    url, method, body = SEARCH_URL, "POST", json.dumps(payload).encode("utf-8")
    seen_requests, seen_items = set(), set()
    items, rejected, pages = [], [], 0
    complete, page_error = False, None
    seen_requests.add((url, method, body))
    while pages < page_cap:
        validate_search_href(url)
        headers = {"Accept": "application/geo+json, application/json", "User-Agent": "SCQA-ArcGISPro/1.2"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body if method == "POST" else None,
                          headers=headers, method=method)
        try:
            with opener(request, timeout=timeout) as response:
                page = _read_json_response(response)
        except Exception as exc:
            page_error = "STAC 第{}页请求失败：{}".format(pages + 1, exc)
            break
        pages += 1
        parsed = parse_search_page(page)
        rejected.extend(parsed["rejected"])
        for item in parsed["items"]:
            if item["item_id"] not in seen_items:
                seen_items.add(item["item_id"])
                if item["cloud_cover_pct"] <= max_cloud:
                    items.append(item)
        next_link = parsed["next"]
        if not next_link:
            complete = True
            break
        try:
            next_url = validate_search_href(next_link.get("href"))
        except ValueError as exc:
            page_error = str(exc)
            break
        method = str(next_link.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            page_error = "STAC 分页方法不支持：{}。".format(method)
            break
        url = next_url
        body = None
        if method == "POST":
            next_body = next_link.get("body")
            if not isinstance(next_body, dict):
                page_error = "STAC POST 分页缺少请求体，检索不完整。"
                break
            body = json.dumps(next_body).encode("utf-8")
        signature = (url, method, body)
        if signature in seen_requests:
            page_error = "STAC 分页请求游标重复，检索不完整。"
            break
        seen_requests.add(signature)
    if pages >= page_cap and not complete and page_error is None:
        page_error = "已达到最大翻页数{}，检索结果不完整。".format(page_cap)
    return {
        "items": items,
        "rejected": rejected,
        "pages": pages,
        "search_complete": bool(complete),
        "page_error": page_error,
        "query": payload,
        "max_cloud_cover_pct": max_cloud,
        "service": SEARCH_URL,
    }
