"""影像底图准备：项目影像 / 天地图 / 演示影像三级来源，统一输出带地理参考的 GeoTIFF。

原则：
- 项目自有 DOM 优先，天地图其次，公开演示影像只用于自查与样例，不得进正式成果。
- 影像时相、比例尺、来源许可必须随成果记录；无时相资料时写明"未提供"。
- 任何跨坐标系重投影都记录源/目标 CRS，不做无依据的基准转换猜测。
"""
from __future__ import annotations

import hashlib
import csv
import datetime
import json
import math
import os
import re
import tempfile
import threading
import time
import socket
import string
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit, parse_qsl, quote
from .imagery_qa import inspect_raster_coverage
from . import gcj02 as _gcj02

MERC_HALF = 20037508.342789244
TILE = 256
# 天地图等站点会对连续请求返回 418/429（CloudWAF）；退避后仍被拦就停止整批，避免越拦越久。
THROTTLE_HTTP = (418, 429)
THROTTLE_BACKOFF_SECONDS = (2, 5)
# Esri 在无影像覆盖处返回固定灰底提示图（Map data not yet available），
# 与层级无关、各位置字节一致。误把它当影像会让整幅图变成灰块。
PLACEHOLDER_MD5 = {"f27d9de7f80c13501f470595e327aa6d"}
BLANK_STD = 6.0
BLANK_MEAN = 180.0
# 成图比例尺按 0.2 mm 像元计：1:N 中 N = GSD(m) / 0.0002
MAP_PIXEL_M = 0.0002
PROVIDERS = {
    "demo": {
        "label": "演示影像（Esri World Imagery）",
        "short": "Esri World Imagery",
        "url": "https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "demo_only": True,
        "license": "仅限自查/样例；正式报件须使用项目自有影像，公开服务使用条款未核实",
    },
    "tianditu": {
        "label": "天地图影像底图",
        "short": "天地图影像",
        "url": ("https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                "&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={tk}"),
        "max_zoom": 18,
        # 天地图对并发敏感：12 并发会触发 CloudWAF 418 并持续拦截。
        "workers": 2,
        "demo_only": False,
        "license": "需在天地图申请 key；成果使用范围与审图要求须按项目合同确认",
    },
}
for _tdt_name, _tdt_layer, _tdt_label in (
    ('tianditu_vector', 'vec', '天地图矢量'),
    ('tianditu_image_labels', 'cia', '天地图影像注记'),
    ('tianditu_vector_labels', 'cva', '天地图矢量注记'),
    ('tianditu_terrain', 'ter', '天地图地形晕渲'),
    ('tianditu_terrain_labels', 'cta', '天地图地形注记'),
):
    PROVIDERS[_tdt_name] = dict(PROVIDERS['tianditu'], label=_tdt_label, short=_tdt_label,
        url=PROVIDERS['tianditu']['url'].replace('img_w', _tdt_layer + '_w').replace('LAYER=img', 'LAYER=' + _tdt_layer))
# 高德：GCJ-02 瓦片，必须逐像素纠偏后才能与 CGCS2000 数据叠加；使用条款未核实，仅供参考底图。
PROVIDERS['amap_satellite'] = {
    'label': '高德卫星影像（GCJ-02 参考底图）',
    'short': '高德卫星',
    'url': 'https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
    'subdomains': (1, 2, 3, 4),
    'max_zoom': 18,
    'workers': 4,
    'demo_only': True,
    'coordinate_system': 'GCJ-02',
    'license': '参考底图，使用条款未核实；GCJ-02 须逐像素纠偏后才可与 CGCS2000 叠加，不得作为正式成果',
}
PROVIDERS['amap_vector'] = {
    'label': '高德矢量地图（GCJ-02 参考底图）',
    'short': '高德矢量',
    'url': 'https://wprd0{s}.is.autonavi.com/appmaptile?style=7&x={x}&y={y}&z={z}',
    'subdomains': (1, 2, 3, 4),
    'max_zoom': 18,
    'workers': 4,
    'demo_only': True,
    'coordinate_system': 'GCJ-02',
    'license': '参考底图，使用条款未核实；GCJ-02 须逐像素纠偏后才可与 CGCS2000 叠加，不得作为正式成果',
}
for _cfg in PROVIDERS.values():
    _cfg.setdefault('coordinate_system', 'EPSG:3857')
# 注记瓦片允许透明底、内容可以很小；矢量瓦片按 256×256 严格校验。
ANNOTATION_PROVIDERS = ('tianditu_vector_labels', 'tianditu_image_labels', 'tianditu_terrain_labels')
VECTOR_PROVIDERS = ('tianditu_vector',)
# 天地图三类底图各有独立注记层（cva/cia/cta）；labels=False 时必须跳过注记下载。
LABEL_OVERLAY_SOURCES = ('tianditu', 'tianditu_vector', 'tianditu_terrain')
_NETWORK_ROUTES = {}


def _request_url(request, timeout=12):
    """系统代理握手超时后仅对当前进程尝试直连；服务明确拒绝时不换路。"""
    url = request.full_url if hasattr(request, 'full_url') else str(request)
    parsed = urlsplit(url)
    host = '{}://{}:{}'.format(parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    def direct():
        # ProxyHandler 会原地改写 Request.host/_tunnel_host，直连必须重建请求。
        if hasattr(request, 'header_items'):
            headers = {key: value for key, value in request.header_items()
                       if key.lower() not in ('proxy-authorization', 'proxy-connection', 'host')}
            fresh = urllib.request.Request(url, data=request.data, headers=headers, method=request.get_method())
        else:
            fresh = url
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(fresh, timeout=timeout)
    if _NETWORK_ROUTES.get(host) == 'direct_after_proxy_timeout':
        return direct()
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        if not urllib.request.getproxies().get(parsed.scheme):
            raise
        response = direct()
        _NETWORK_ROUTES[host] = 'direct_after_proxy_timeout'
        return response


_ARCGIS_SERVICE_CONFIGS = {}


def _arcgis_service_config(endpoint, credential=None):
    identity = (endpoint, hashlib.sha256(str(credential or '').encode()).hexdigest())
    if identity in _ARCGIS_SERVICE_CONFIGS:
        return dict(_ARCGIS_SERVICE_CONFIGS[identity])
    parsed = urlsplit(endpoint)
    if parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.path.lower().endswith('/mapserver'):
        raise ValueError('请输入 ArcGIS 缓存 MapServer 根地址；key 单独填写')
    query = dict(f='json')
    if credential: query['token'] = credential
    request = urllib.request.Request(endpoint.rstrip('/') + '?' + urlencode(query), headers={'User-Agent': 'ArcGIS-Pro-Acceptance-Assistant'})
    try:
        with _request_url(request, timeout=12) as response:
            payload = json.loads(response.read(1024 * 1024).decode('utf-8'))
    except Exception as exc:
        raise ValueError('影像服务元数据暂不可用：' + _online_error_reason(exc)) from None
    info = payload.get('tileInfo') or {}
    if payload.get('error'):
        raise ValueError('ArcGIS影像服务返回授权或参数错误，请核对服务地址和凭证')
    sr = info.get('spatialReference') or {}
    origin = info.get('origin') or {}
    levels = info.get('lods') or []
    valid_matrix = (sr.get('latestWkid', sr.get('wkid')) in (3857, 102100, 102113)
        and info.get('rows') == info.get('cols') == 256
        and abs(origin.get('x', 0) + MERC_HALF) < .1 and abs(origin.get('y', 0) - MERC_HALF) < .1)
    if not valid_matrix or not levels:
        raise ValueError('该服务不属于已支持的 Web Mercator 256像元缓存矩阵，请选其它在线底图')
    zooms = []
    for row in levels:
        level = int(row['level'])
        expected = 2 * MERC_HALF / (TILE * 2 ** level)
        if not 0 <= level <= 22 or abs(float(row['resolution']) - expected) > expected * .00001:
            raise ValueError('服务层级与标准 Web Mercator XYZ 不一致')
        zooms.append(level)
    cfg = dict(label=parsed.path.rsplit('/', 2)[-2], short=parsed.path.rsplit('/', 2)[-2],
        url=endpoint.rstrip('/') + '/tile/{z}/{y}/{x}' + ('?token={tk}' if credential else ''), version=str(payload.get('currentVersion', 'UNVERIFIED')),
        min_zoom=min(zooms), max_zoom=max(zooms), demo_only=True,
        license=payload.get('copyrightText') or 'UNVERIFIED', authorization_reference='当前地图/操作者提供的服务，使用条件待核',
        source_gsd_status='UNVERIFIED', coordinate_system='EPSG:3857')
    _ARCGIS_SERVICE_CONFIGS[identity] = cfg
    return dict(cfg)


def _provider_config(provider, tk=None):
    """接收已配置的 XYZ/Web Mercator 服务，不推测其它矩阵和坐标系。"""
    if provider in PROVIDERS:
        return dict(PROVIDERS[provider], version='builtin', min_zoom=12, max_zoom=PROVIDERS[provider].get('max_zoom', 22))
    if str(provider).lower().startswith(('https://', 'http://')):
        return dict(_arcgis_service_config(str(provider).rstrip('/'), credential=tk))
    path = os.path.abspath(os.fspath(provider))
    if not path.lower().endswith('.json') or not os.path.isfile(path):
        raise ValueError('在线服务须选内置来源或已配置的服务 JSON')
    with open(path, encoding='utf-8-sig') as stream:
        cfg = json.load(stream)
    if not isinstance(cfg, dict) or cfg.get('schema_version') != 1 or cfg.get('tile_scheme') not in ('xyz_web_mercator', 'wmts_web_mercator'):
        raise ValueError('目前支持 schema_version=1 的 xyz_web_mercator / wmts_web_mercator 服务配置')
    if cfg.get('coordinate_system') != 'EPSG:3857':
        raise ValueError('服务坐标必须明确声明 EPSG:3857；GCJ-02、BD-09 或未知坐标服务不能直接生成 CGCS2000 成果')
    resolution = cfg.get('level_zero_resolution')
    if (cfg.get('tile_size') != TILE or cfg.get('matrix_origin') != 'global_north_west'
            or not isinstance(resolution, (int, float)) or isinstance(resolution, bool)
            or not math.isfinite(resolution) or abs(resolution - 2 * MERC_HALF / TILE) > .001):
        raise ValueError('服务矩阵必须为全球西北原点、256像元瓦片及标准 XYZ 层级（0级分辨率156543.033928米）')
    for field in ('label', 'url', 'version', 'license', 'authorization_reference'):
        if not isinstance(cfg.get(field), str) or not cfg[field].strip():
            raise ValueError('影像服务配置缺少：' + field)
    try:
        parts = list(string.Formatter().parse(cfg['url']))
        if any(spec or conv for _, _, spec, conv in parts):
            raise ValueError()
        fields = [field for _, field, _, _ in parts if field is not None]
        if not {'x', 'y', 'z'} <= set(fields) or any(field not in {'x', 'y', 'z', 's', 'tk'} for field in fields):
            raise ValueError()
        cfg['url'].format(x=0, y=0, z=12, s=0, tk='')
    except (ValueError, KeyError, IndexError):
        raise ValueError('服务模板须含 {z}/{y}/{x}，仅允许 x/y/z/s/tk 占位字段') from None
    parsed = urlsplit(cfg['url'])
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('服务地址须为 HTTP(S)，凭证请在 key 输入框填写')
    secrets = {'tk', 'key', 'token', 'access_token', 'api_key', 'authorization'}
    if any(key.lower() in secrets and value not in ('', '{tk}') for key, value in parse_qsl(parsed.query)):
        raise ValueError('服务地址不能内嵌凭证，改用 {tk} 占位字段')
    lo, hi = int(cfg.get('min_zoom', 12)), int(cfg.get('max_zoom', 19))
    if not 0 <= lo <= hi <= 22:
        raise ValueError('服务层级范围应为0至22，且最低层级不大于最高层级')
    return dict(cfg, short=cfg['label'], demo_only=bool(cfg.get('demo_only', False)), min_zoom=lo, max_zoom=hi)


def _online_error_reason(exc):
    """不保存异常 URL/响应正文，避免凭证被错误报告回显。"""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in THROTTLE_HTTP:
            return 'THROTTLED:HTTP{}'.format(exc.code)
        code = 'AUTHORIZATION_FAILED' if exc.code in (401, 403) else 'HTTP_NOT_FOUND' if exc.code == 404 else 'HTTP_FAILED'
        return '{}:HTTP{}'.format(code, exc.code)
    if isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError)):
        reason = getattr(exc, 'reason', None)
        return 'NETWORK_FAILED:{}:{}'.format(type(exc).__name__, type(reason).__name__ if reason is not None else '')
    return 'DOWNLOAD_FAILED:' + type(exc).__name__


def _reason_counts(reasons):
    counts = {}
    codes = ('THROTTLED', 'AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_NOT_FOUND', 'HTTP_FAILED',
             'NO_COVERAGE', 'DECODE_FAILED', 'READ_FAILED', 'CONTENT_INVALID', 'DOWNLOAD_FAILED')
    for reason in reasons:
        code = next((value for value in codes if value in str(reason)), None)
        if code is None and ('占位' in str(reason) or '无覆盖' in str(reason)):
            code = 'NO_COVERAGE'
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


def _service_cache_dir(cache, provider, tk=None):
    """服务/图层版本与凭证分仓；只把凭证摘要放入目录名，不泄露 key。"""
    cfg = _provider_config(provider, tk=tk)
    identity = "\0".join((provider, cfg.get("url", ""), cfg.get('version', ''), str(tk or "")))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    safe_provider = (re.sub(r"[^A-Za-z0-9_-]+", "_", str(provider)).strip("_") or "service")[:40]
    return os.path.join(cache, "{}_{}".format(safe_provider, digest))


def _write_json_atomic(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".imagery-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _manifest_label(label):
    value = str(label or "project")
    if value in (".", "..") or os.path.basename(value) != value or "/" in value or "\\" in value:
        raise ValueError("影像标签不得包含路径：{}".format(value))
    return value


def _write_source_manifest(out_folder, record):
    """每次调用留存逐图记录，并更新含所有标签的项目清单。"""
    label = _manifest_label(record.get("label"))
    normalized = dict(record)
    normalized["label"] = label
    normalized.setdefault("source_gsd_m", None)
    normalized.setdefault("source_gsd_status", "UNVERIFIED")
    normalized.setdefault("output_pixel_size_m", None)
    normalized.setdefault("positional_accuracy", {"value_m": None, "status": "UNVERIFIED"})
    if 'online_diagnostics' in normalized:
        normalized['online_diagnostics']['network_routes'] = dict(_NETWORK_ROUTES)
    os.makedirs(out_folder, exist_ok=True)
    _write_json_atomic(os.path.join(out_folder, "imagery_source_{}.json".format(label)), normalized)
    catalog_path = os.path.join(out_folder, "imagery_sources.json")
    try:
        with open(catalog_path, encoding="utf-8") as fh:
            catalog = json.load(fh)
        if not isinstance(catalog.get("sources"), dict):
            catalog = {"schema_version": 1, "sources": {}}
    except (OSError, ValueError, TypeError):
        catalog = {"schema_version": 1, "sources": {}}
    catalog["schema_version"] = 1
    catalog.setdefault("sources", {})[label] = normalized
    _write_json_atomic(catalog_path, catalog)
    # Keep the historic single-record path for older callers; the catalog is the complete source of truth.
    _write_json_atomic(os.path.join(out_folder, "imagery_source.json"), normalized)
    return normalized


def _web_pixel_metadata(zoom, latitude, output_pixel_size_m):
    """Web tile sampling distance is not sensor GSD or positional accuracy."""
    spacing = (2 * MERC_HALF) / (TILE * 2 ** int(zoom))
    spacing *= math.cos(math.radians(float(latitude)))
    return {
        "service_pixel_spacing_m": round(spacing, 4),
        "output_pixel_size_m": round(float(output_pixel_size_m), 4),
        "source_gsd_m": None,
        "source_gsd_status": "UNVERIFIED",
        "positional_accuracy": {"value_m": None, "status": "UNVERIFIED", "evidence": None},
    }


def combine_unit_sources(out_folder, records, target_wkid, bounds, label="overview", max_pixels=16000000):
    """总览拼接只控制显示预算，逐单元保留独立高清来源与精度状态。"""
    from osgeo import gdal, osr
    available = [record["output"] for record in records.values() if record.get("output") and os.path.isfile(record["output"])]
    meta = {"label": label, "provider": "mixed_units", "source_label": "多来源拼接（详见逐单元清单）",
            "source_labels": sorted({str(record.get("source_label") or record.get("label") or "未核实") for record in records.values()}),
            "unit_sources": records, "source_gsd_m": None, "source_gsd_status": "UNVERIFIED",
            "positional_accuracy": {"status": "UNVERIFIED", "value_m": None}, "acquired": "逐单元时相见清单",
            "coverage_status": "NEEDS_REVIEW", "status": "NEEDS_REVIEW", "demo_only": any(record.get("demo_only") for record in records.values())}
    if not available:
        return _write_source_manifest(out_folder, dict(meta, output=None, coverage_status="FAIL"))
    sr = osr.SpatialReference()
    if sr.ImportFromEPSG(int(target_wkid)) != 0 or not sr.IsProjected():
        raise ValueError("混合影像总览需要已知投影坐标系")
    native_meters = sr.GetLinearUnits()
    minimum = min(float(record.get("output_pixel_size_m") or 1) for record in records.values()) / native_meters
    width_native, height_native = bounds[2] - bounds[0], bounds[3] - bounds[1]
    cell = max(minimum, math.sqrt(width_native * height_native / max_pixels))
    width, height = max(1, int(width_native / cell)), max(1, int(height_native / cell))
    while width * height > max_pixels:
        cell *= 1.01
        width, height = max(1, int(width_native / cell)), max(1, int(height_native / cell))
    os.makedirs(out_folder, exist_ok=True)
    output = os.path.join(out_folder, _manifest_label(label) + "_mixed.tif")
    if os.path.exists(output):
        raise FileExistsError("总览影像已存在，不覆盖：" + output)
    prepared_sources = []
    for path in dict.fromkeys(available):
        source = gdal.Open(path)
        if source is None:
            raise ValueError("单元影像无法解码：" + path)
        has_alpha = source.GetRasterBand(source.RasterCount).GetColorInterpretation() == gdal.GCI_AlphaBand
        prepared_sources.append(gdal.Warp("", source, format="VRT", srcAlpha=has_alpha, dstAlpha=True))
        source = None
    dataset = gdal.Warp(output, prepared_sources, dstSRS=sr.ExportToWkt(), outputBounds=bounds, srcAlpha=True,
        width=width, height=height, dstAlpha=True, resampleAlg="bilinear", multithread=True,
        creationOptions=["COMPRESS=LZW", "TILED=YES"])
    if dataset is None:
        raise RuntimeError("逐单元影像总览拼接失败")
    dataset = None
    actual_pixel = max(width_native / width, height_native / height) * native_meters
    meta.update({"output": output, "output_pixel_size_m": actual_pixel, "resolution_m": actual_pixel,
                 "budget_resampled": cell > minimum, "overview_max_pixels": max_pixels,
                 "note": "总览按显示预算重采样；逐单元影像不受此预算降清晰度，输出像元不作为源精度证明。"})
    return _write_source_manifest(out_folder, meta)


def query_world_imagery_metadata(bbox, timeout=12):
    """查询 Esri World Imagery 在中心/四角返回的逐位置元数据；不推导精度结论。"""
    endpoint = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify"
    xmin, ymin, xmax, ymax = [float(v) for v in bbox]
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    points = [("center", cx, cy), ("nw", xmin, ymax), ("ne", xmax, ymax),
              ("sw", xmin, ymin), ("se", xmax, ymin)]
    observations = []
    errors = []
    def observe(point):
        label, x, y = point
        found, failed = [], []
        params = {
            "f": "json", "geometry": "{:.8f},{:.8f}".format(x, y),
            "geometryType": "esriGeometryPoint", "sr": 4326,
            "mapExtent": "{},{},{},{}".format(xmin, ymin, xmax, ymax),
            "imageDisplay": "512,512,96", "tolerance": 2,
            "layers": "visible:0", "returnGeometry": "false",
        }
        url = endpoint + "?" + urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ArcGIS-Pro-Acceptance-Assistant"})
            with _request_url(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("error"):
                raise RuntimeError(str(data["error"].get("message", "服务返回错误")))
            for result in data.get("results", []):
                found.append({"location": label, "x": x, "y": y,
                                     "layer": result.get("layerName"),
                                     "value": result.get("value"),
                                     "attributes": result.get("attributes") or {}})
        except Exception as exc:  # noqa: BLE001 - 网络元数据失败也必须留状态
            failed.append({"location": label, "error": _online_error_reason(exc)})
        return found, failed
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as pool:
        for found, failed in pool.map(observe, points):
            observations.extend(found)
            errors.extend(failed)
    dates, sources, resolutions = set(), set(), set()
    for item in observations:
        attrs = {re.sub(r'[^a-z0-9]', '', str(k).lower()): v for k, v in item["attributes"].items()}
        date = next((attrs[key] for key in ("acquisitiondate", "acqdate", "imagedate", "srcdate2", "srcdate", "date")
                     if attrs.get(key) not in (None, "")), None)
        source = next((attrs[key] for key in ("source", "sourceimagery", "provider")
                       if attrs.get(key) not in (None, "")), None)
        resolution = next((attrs[key] for key in ("resolution", "resolutionm", "pixelresolution", "pixelsize")
                           if attrs.get(key) not in (None, "")), None)
        if date is not None:
            dates.add(str(date))
        if source is not None:
            sources.add(str(source))
        if resolution is not None:
            resolutions.add(str(resolution))
    return {
        "status": "PASS" if observations else "NEEDS_REVIEW",
        "observations": observations,
        "acquisition_dates": sorted(dates),
        "sources": sorted(sources),
        "reported_resolutions": sorted(resolutions),
        "errors": errors,
        "positional_accuracy": {"value_m": None, "status": "UNVERIFIED"},
        "source_gsd_m": None,
        "note": "服务逐位置返回的属性仅作为来源线索；原始 GSD 和位置精度仍需来源文件/检核报告证明。",
    }


def _source_fingerprint(path):
    try:
        stat = os.stat(path)
        return {"path": os.path.abspath(path), "size_bytes": stat.st_size,
                "modified_ns": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))}
    except OSError:
        return {"path": os.path.abspath(path), "status": "UNVERIFIED"}


def _normalise_reviewed_evidence(evidence, review, base_dir=None, require_hash=False):
    """Return a local evidence file and explicit reviewer record only when verifiable.

    This checks file presence/integrity and review-record completeness. It does not
    interpret the evidence document or independently certify its technical claim.
    """
    if not isinstance(evidence, dict) or not isinstance(review, dict):
        return None
    if review.get("status") != "VERIFIED" or not str(review.get("reviewer") or "").strip():
        return None
    reviewed_at = str(review.get("reviewed_at") or "").strip()
    try:
        if "T" in reviewed_at or " " in reviewed_at:
            datetime.datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        else:
            datetime.date.fromisoformat(reviewed_at)
    except ValueError:
        return None

    raw_path = evidence.get("path")
    if not isinstance(raw_path, (str, os.PathLike)) or not str(raw_path).strip():
        return None
    raw_path = os.path.expanduser(os.fspath(raw_path).strip())
    scheme = urlsplit(raw_path).scheme
    is_windows_drive = len(scheme) == 1 and len(raw_path) > 1 and raw_path[1] == ":"
    if scheme and not is_windows_drive:
        return None
    if not os.path.isabs(raw_path):
        if base_dir is None:
            return None
        raw_path = os.path.join(base_dir, raw_path)
    path = os.path.realpath(raw_path)

    expected_hash = str(evidence.get("sha256") or "").strip().lower()
    if require_hash and not expected_hash:
        return None
    if expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return None
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
    except OSError:
        return None
    if expected_hash and expected_hash != actual_hash:
        return None
    return {
        "evidence": {"path": path, "sha256": actual_hash},
        "review": {"status": "VERIFIED", "reviewer": str(review["reviewer"]).strip(),
                   "reviewed_at": reviewed_at},
    }


def _prepare_raster_source(raster, out_folder, label, arcpy):
    """接受单个栅格或资料目录；目录含多幅栅格时只拼到工作副本。"""
    if not os.path.isdir(raster):
        return raster, [raster], False
    previous_workspace = getattr(arcpy.env, "workspace", None)
    try:
        arcpy.env.workspace = raster
        names = arcpy.ListRasters("*", "ALL") or []
    finally:
        arcpy.env.workspace = previous_workspace
    sources = [os.path.join(raster, name) for name in names]
    if not sources:
        raise ValueError("所选资料目录中没有可读取的栅格影像：{}".format(raster))
    if len(sources) == 1:
        return sources[0], sources, False

    descs = [arcpy.Describe(path) for path in sources]
    first_sr = getattr(descs[0], "spatialReference", None)
    sr_name = getattr(first_sr, "name", "Unknown") if first_sr else "Unknown"
    sr_code = getattr(first_sr, "factoryCode", 0) if first_sr else 0
    if not sr_code or sr_name == "Unknown":
        raise ValueError("影像目录中存在未定义坐标系的栅格，不能安全拼接：{}".format(raster))
    for path, desc in zip(sources[1:], descs[1:]):
        sr = getattr(desc, "spatialReference", None)
        if not sr or getattr(sr, "factoryCode", 0) != sr_code:
            raise ValueError("影像目录中的栅格坐标系不一致，不能自动猜测转换：{}".format(path))
    pixel_codes = {"U8": "8_BIT_UNSIGNED", "S8": "8_BIT_SIGNED", "U16": "16_BIT_UNSIGNED",
                   "S16": "16_BIT_SIGNED", "U32": "32_BIT_UNSIGNED", "S32": "32_BIT_SIGNED",
                   "F32": "32_BIT_FLOAT", "F64": "64_BIT"}
    pixel_type = pixel_codes.get(str(getattr(descs[0], "pixelType", "U8")).upper(), "8_BIT_UNSIGNED")
    band_count = int(getattr(descs[0], "bandCount", 3) or 3)
    cell_size = getattr(descs[0], "meanCellWidth", "")
    output = os.path.join(out_folder, "{}_mosaic.tif".format(label))
    if os.path.exists(output):
        raise FileExistsError("影像拼接工作副本已存在，不覆盖：{}".format(output))
    arcpy.management.MosaicToNewRaster(
        sources, out_folder, os.path.basename(output), first_sr, pixel_type, cell_size,
        band_count, "LAST", "FIRST")
    if not arcpy.Exists(output):
        raise RuntimeError("影像拼接工具未生成工作副本：{}".format(output))
    return output, sources, True


def _local_dom_copy(raster, out_folder, label, extent, target_wkid, arcpy):
    """将项目 DOM 接收到任务目录；共享路径和原始文件不会成为成果工程外部依赖。"""
    output = os.path.join(os.path.abspath(out_folder), "{}_dom.tif".format(_manifest_label(label)))
    if arcpy.Exists(output) or os.path.exists(output):
        raise FileExistsError("DOM 工作副本已存在，不覆盖：{}".format(output))
    operation = {"source": str(raster), "output": output, "resampling": "保留源像元网格"}
    if extent is None:
        arcpy.management.CopyRaster(raster, output, format="TIFF")
        operation["operation"] = "CopyRaster"
    else:
        source_sr = arcpy.Describe(raster).spatialReference
        target_sr = arcpy.SpatialReference(int(target_wkid))
        rectangle = extent
        if source_sr.exportToString() != target_sr.exportToString():
            source_gcs = getattr(getattr(source_sr, "GCS", None), "factoryCode", None)
            target_gcs = getattr(getattr(target_sr, "GCS", None), "factoryCode", None)
            if not source_gcs or source_gcs != target_gcs:
                raise ValueError("DOM 与图斑的大地基准不同，需先提供明确的基准转换依据后再准备本地副本。")
            polygon = arcpy.Polygon(arcpy.Array([
                arcpy.Point(extent.XMin, extent.YMin), arcpy.Point(extent.XMax, extent.YMin),
                arcpy.Point(extent.XMax, extent.YMax), arcpy.Point(extent.XMin, extent.YMax)]), target_sr)
            rectangle = polygon.projectAs(source_sr).extent
        bounds = [rectangle.XMin, rectangle.YMin, rectangle.XMax, rectangle.YMax]
        arcpy.management.Clip(raster, " ".join(str(value) for value in bounds), output,
                              clipping_geometry="NONE", maintain_clipping_extent="NO_MAINTAIN_EXTENT")
        operation.update({"operation": "Clip", "clip_bounds_source_crs": bounds})
    if not arcpy.Exists(output):
        raise RuntimeError("DOM 接收没有生成本地工作副本：{}".format(output))
    return output, operation


def write_uav_resurvey_request(out_folder, units, imagery_by_unit, wkid, aoi_path=None):
    """生成供测绘单位确认的补测需求清单；不生成或伪造精度结论。"""
    os.makedirs(out_folder, exist_ok=True)
    rows = []
    for unit in units:
        uid = str(unit.get("unit", ""))
        meta = imagery_by_unit.get(uid) or imagery_by_unit.get("overview") or {}
        issues = []
        if not meta.get("output"):
            issues.append("缺少可用正射影像")
        if not meta.get("acquired") or str(meta.get("acquired", "")).startswith("未提供"):
            issues.append("拍摄时相未核实")
        try:
            source_gsd = float(meta.get("source_gsd_m"))
        except (TypeError, ValueError):
            source_gsd = None
        if source_gsd is not None and (not math.isfinite(source_gsd) or source_gsd <= 0):
            source_gsd = None
        gsd_attestation = _normalise_reviewed_evidence(
            meta.get("source_gsd_evidence"), meta.get("source_gsd_review"), require_hash=True)
        if (source_gsd is None or meta.get("source_gsd_status") != "VERIFIED"
                or gsd_attestation is None):
            issues.append("原始 GSD 未提供证明")
        accuracy = meta.get("positional_accuracy") or {}
        try:
            accuracy_value = float(accuracy.get("value_m"))
        except (TypeError, ValueError):
            accuracy_value = None
        accuracy_attestation = _normalise_reviewed_evidence(
            accuracy.get("evidence"), accuracy.get("review"), require_hash=True)
        if (accuracy.get("status") != "VERIFIED" or accuracy_value is None
                or not math.isfinite(accuracy_value) or accuracy_value <= 0
                or accuracy_attestation is None):
            issues.append("位置精度未提供证明")
        elif accuracy_value >= 0.2:
            issues.append("位置精度未优于 0.2 m")
        if meta.get("demo_only"):
            issues.append("当前影像仅用于演示/自查")
        rows.append({
            "unit": uid,
            "parcel_ids": list(unit.get("parcels", [])),
            "bbox_native": unit.get("bbox_native"),
            "source": meta.get("source_label") or meta.get("provider") or "未提供",
            "acquired": meta.get("acquired") or "未提供时相资料",
            "service_pixel_spacing_m": meta.get("service_pixel_spacing_m"),
            "output_pixel_size_m": meta.get("output_pixel_size_m"),
            "source_gsd_m": source_gsd,
            "positional_accuracy": accuracy_value,
            "reasons": issues,
            "status": "NEEDS_REVIEW" if issues else "PASS",
        })
    payload = {
        "schema_version": 1,
        "purpose": "补充耕地质量采样与项目资料归档的正射影像补测需求；最终用途及验收条款由项目负责人确认。",
        "coordinate_system_wkid": int(wkid),
        "aoi_geojson": os.path.abspath(aoi_path) if aoi_path else None,
        "delivery_checklist": [
            "交付覆盖整个 AOI 的正射影像 GeoTIFF，并保留坐标参考与 NoData 信息。",
            "提供拍摄日期、传感器/平台、原始 GSD 和处理软件/正射处理说明。",
            "提供位置精度检核报告、控制/检查点说明及坐标基准转换依据。",
            "提供数据许可、使用限制、交付范围和缺失覆盖说明。",
            "对接边界、重叠、空洞、像元大小和坐标系进行逐项复核。",
        ],
        "units": rows,
        "status": "NEEDS_REVIEW" if any(row["status"] != "PASS" for row in rows) or not aoi_path else "PASS",
    }
    _write_json_atomic(os.path.join(out_folder, "补测需求.json"), payload)
    with open(os.path.join(out_folder, "补测清单.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["评价单元", "图斑编号", "问题", "来源", "时相", "服务像元间距_m",
                         "输出像元大小_m", "原始GSD_m", "位置精度_m", "状态"])
        for row in rows:
            writer.writerow([row["unit"], "、".join(row["parcel_ids"]), "；".join(row["reasons"]),
                             row["source"], row["acquired"], row["service_pixel_spacing_m"],
                             row["output_pixel_size_m"], row["source_gsd_m"],
                             row["positional_accuracy"], row["status"]])
    readme = "# 无人机/测绘正射影像补测需求\n\n"
    readme += "坐标系 WKID：{}；AOI：{}\n\n".format(payload["coordinate_system_wkid"], payload["aoi_geojson"] or "未导出")
    readme += "请测绘单位按补测需求 JSON 与 CSV 回填资料，并交付正射成果及精度证明。文件中的待核项不代表已满足任何精度门槛。\n"
    with open(os.path.join(out_folder, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme)
    return payload


def _tile_quality(path, annotation=False, vector=False):
    """判断下载到的瓦片是否含真实影像；返回 (valid, reason)。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return False, "READ_FAILED:读取失败"
    if hashlib.md5(data).hexdigest() in PLACEHOLDER_MD5:
        return False, "NO_COVERAGE:占位图（无影像覆盖）"
    if len(data) < (32 if annotation or vector else 500):
        return False, "CONTENT_INVALID:内容过短"
    try:
        from PIL import Image
        import numpy as np
        with Image.open(path) as im:
            if (annotation or vector) and im.size != (TILE,TILE):
                return False, 'CONTENT_INVALID:矢量或注记瓦片尺寸不是256×256'
            rgba = np.asarray(im.convert('RGBA'))
        valid_mask = rgba[:, :, 3] > 0
        if not valid_mask.any():
            if annotation:
                return True, ""
            return False, 'NO_COVERAGE:透明瓦片（无有效像元）'
        arr = rgba[:, :, :3][valid_mask].astype('float32')
        std = float(arr.std(axis=0).mean())
    except Exception as exc:
        return False, "DECODE_FAILED:瓦片解码失败：{}".format(type(exc).__name__)
    if std < BLANK_STD:
        return True, "低纹理/近纯色疑点，保留并复核"
    if not valid_mask.all():
        return True, '部分透明像元，须检查地块有效覆盖'
    return True, ""


def _fetch_tile(url, timeout, backoff=THROTTLE_BACKOFF_SECONDS):
    """取单瓦片字节；418/429 退避重试，重建 Request 避免代理改写残留。"""
    for attempt in range(len(backoff) + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "ArcGIS-Pro-Acceptance-Assistant"})
        try:
            with _request_url(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in THROTTLE_HTTP or attempt >= len(backoff):
                raise
            time.sleep(backoff[attempt])
    raise RuntimeError('unreachable')


def _tile_url(cfg, z, x, y, tk=None):
    """按服务模板生成瓦片地址；有 subdomains 时用 1..N 轮换，否则沿用 t0..t7。"""
    subdomains = cfg.get('subdomains')
    s = (x + y) % 8 if not subdomains else subdomains[(x + y) % len(subdomains)]
    return cfg["url"].format(z=z, x=x, y=y, s=s, tk=quote(str(tk or ''), safe=''))


def _probe_tile(provider, bbox, z, cache, tk, timeout):
    """取 bbox 中心与四角瓦片做抽样，返回 (有效数, 抽样数, 原因列表)。"""
    cfg = _provider_config(provider, tk=tk)
    annotation = provider in ANNOTATION_PROVIDERS
    vector = provider in VECTOR_PROVIDERS
    quality = lambda path: _tile_quality(path,annotation=annotation,vector=vector)
    cache = _service_cache_dir(cache, provider, tk)
    os.makedirs(cache, exist_ok=True)
    x0, y0, x1, y1 = _bbox_tiles(bbox, z)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    picks = list(dict.fromkeys([(cx, cy), (x0, y0), (x1, y0), (x0, y1), (x1, y1)]))
    good, sampled, reasons = 0, 0, []
    for x, y in picks:
        sampled += 1
        path = os.path.join(cache, "probe_{}_{}_{}.jpg".format(z, x, y))
        valid, reason = quality(path) if os.path.isfile(path) else (False,'')
        if not valid:
            url = _tile_url(cfg, z, x, y, tk)
            try:
                data = _fetch_tile(url, timeout)
                with open(path, "wb") as fh:
                    fh.write(data)
            except Exception as exc:  # noqa: BLE001
                reasons.append("{}:{} {}".format(x, y, _online_error_reason(exc)))
                break
        valid, reason = quality(path)
        if valid:
            good += 1
        if reason:
            reasons.append("{}:{} {}".format(x, y, reason))
    return good, sampled, reasons


def select_zoom(provider, bbox, cache, tk=None, max_tiles=600, max_zoom=18,
                min_zoom=12, need_ratio=0.8, timeout=25):
    """从高到低试层级，跳过无影像覆盖的层级；返回 (z, 计划瓦片数, 探测记录)。"""
    z, count = choose_zoom(bbox, max_tiles=max_tiles, max_zoom=max_zoom, min_zoom=min_zoom)
    attempts = []
    if count > max_tiles:
        return z, count, [{'zoom': z, 'valid': 0, 'sampled': 0, 'decision': 'BUDGET_EXCEEDED', 'planned_tiles': count}]
    if z < max_zoom:
        attempts.append({'zoom': z, 'decision': 'BUDGET_REDUCED', 'planned_tiles': count})
    while z >= min_zoom:
        good, total, reasons = _probe_tile(provider, bbox, z, cache, tk, timeout)
        causes = _reason_counts(reasons)
        attempts.append({"zoom": z, "valid": good, "sampled": total, "reasons": reasons,
                         "reason_counts": causes})
        if total and good / float(total) >= need_ratio:
            plan = _bbox_tiles(bbox, z)
            planned = (plan[2] - plan[0] + 1) * (plan[3] - plan[1] + 1)
            if planned > max_tiles:
                attempts.append({'zoom': z, 'decision': 'BUDGET_EXCEEDED', 'planned_tiles': planned})
            return z, planned, attempts
        if any(key in causes for key in ('THROTTLED', 'AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_FAILED', 'HTTP_NOT_FOUND')):
            return z, count, attempts
        attempts[-1]['decision'] = 'NO_HIGH_ZOOM_COVERAGE' if causes.get('NO_COVERAGE') else 'PROBE_QUALITY_FALLBACK'
        z -= 1
    return min_zoom, count, attempts


def usable_map_scale(gsd_m):
    """影像 GSD 对应的最精细成图比例尺（分母），用于限制出图放大倍数。"""
    if not gsd_m:
        return None
    return int(math.ceil(float(gsd_m) / MAP_PIXEL_M / 500.0) * 500)


def raster_sampling_diagnostics(path, scale, dpi, arcpy_module=None):
    """Measure the actual output raster; print sampling never certifies source accuracy."""
    if not all(math.isfinite(float(v)) and float(v) > 0 for v in (scale, dpi)):
        raise ValueError('比例尺分母与DPI必须为有限正数')
    if arcpy_module is None:
        import arcpy as arcpy_module
    raster = arcpy_module.Raster(path)
    sr = raster.spatialReference
    result = {'raster': str(path), 'scale': float(scale), 'dpi': float(dpi),
              'coordinate_system': sr.name, 'width': raster.width, 'height': raster.height,
              'bands': raster.bandCount, 'source_gsd_status': 'UNVERIFIED',
              'positional_accuracy_status': 'UNVERIFIED',
              'scope': '输出栅格与纸面采样实测；原始GSD和定位精度须另核证明'}
    if sr.type != 'Projected' or not math.isfinite(float(sr.metersPerUnit)) or sr.metersPerUnit <= 0:
        return dict(result, status='UNVERIFIED', detail='非已知投影单位，不把角度像元猜成米')
    x = abs(float(raster.meanCellWidth) * sr.metersPerUnit)
    y = abs(float(raster.meanCellHeight) * sr.metersPerUnit)
    if not all(math.isfinite(v) and v > 0 for v in (x, y)):
        raise ValueError('栅格实际像元大小无效')
    print_pixel = float(scale) * .0254 / float(dpi)
    result.update(status='MEASURED', output_pixel_x_m=x, output_pixel_y_m=y,
                  paper_pixel_mm=max(x, y) * 1000 / float(scale),
                  ground_print_pixel_m=print_pixel,
                  print_pixels_per_raster_pixel=max(x, y) / print_pixel)
    return result


def imagery_caption(record, limit=32):
    """图面影像注记。无输出时不得写来源，避免空白底图被误读为已有影像。"""
    record = record or {}
    if not record.get("output"):
        reason = ((record.get("online_diagnostics") or {}).get("primary_reason")
                  or record.get("warning") or "未提供")
        return "影像底图：未取得（{}）\n不得作为报件图".format(str(reason)[:limit])
    source = str(record.get("source_label") or record.get("label") or "未提供")
    if len(source) > limit:
        source = source[:limit - 2] + "…"
    text = "影像来源：{}".format(source)
    if record.get("demo_only"):
        return text + "\n仅供测试/预览；来源精度待核"
    text += "\n时相：{}；精度待核".format(str(record.get("acquired") or "未核实")[:16])
    gsd = record.get("output_pixel_size_m") or record.get("resolution_m")
    if gsd:
        text += "\n输出像元：{:.2f} m（非原生 GSD）".format(float(gsd))
    else:
        text += "\n输出像元：未核实"
    return text


def imagery_report_gate(records):
    """报备影像门槛：只按有证据的正射位置精度判断，不从输出像元推断精度。"""
    flattened = []
    for row in records or []:
        row = row or {}
        unit_sources = row.get("unit_sources")
        if isinstance(unit_sources, dict) and unit_sources:
            flattened.extend(value or {} for value in unit_sources.values())
        else:
            flattened.append(row)
    with_output = [row for row in flattened if row.get("output")]
    if not with_output:
        return "FAIL", "未取得可用影像底图，不得作为报件图"
    missing = [row for row in flattened if not row.get("output") and row.get("label") not in (None, "overview")]
    if missing:
        labels = [str(row.get("label") or "未命名单元") for row in missing]
        return "FAIL", "以下影像记录没有可用输出：{}".format("、".join(labels[:8]))
    issues = []
    for row in with_output:
        label = str(row.get("label") or row.get("source_label") or "未命名影像")
        if row.get("demo_only"):
            issues.append("{}为演示影像".format(label))
        accuracy = row.get("positional_accuracy") or {}
        value = accuracy.get("value_m")
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        attestation = _normalise_reviewed_evidence(
            accuracy.get("evidence"), accuracy.get("review"), require_hash=True)
        if accuracy.get("status") != "VERIFIED" or value is None or not math.isfinite(value) or value <= 0:
            issues.append("{}的位置精度未核实".format(label))
        elif value >= 0.2:
            issues.append("{}的位置精度 {:.3f} m 未优于 0.2 m".format(label, value))
        if attestation is None:
            issues.append("{}的位置精度证明文件、哈希或审核记录缺失/不匹配".format(label))
    if issues:
        return "NEEDS_REVIEW", "；".join(issues[:8]) + "；输出像元大小不能替代精度证明"
    values = [float(row["positional_accuracy"]["value_m"]) for row in with_output]
    return "PASS", "所有报备影像均有位置精度证明，最差为 {:.3f} m（优于 0.2 m）".format(max(values))


def export_imagery_evidence_checks(quality):
    """Revalidate evidence files and review records immediately before export status is set."""
    quality = quality if isinstance(quality, dict) else {}
    checks = []

    try:
        source_gsd = float(quality.get("source_gsd_m"))
    except (TypeError, ValueError):
        source_gsd = None
    if source_gsd is not None and (not math.isfinite(source_gsd) or source_gsd <= 0):
        source_gsd = None
    gsd_proof = _normalise_reviewed_evidence(
        quality.get("source_gsd_evidence"), quality.get("source_gsd_review"), require_hash=True)
    gsd_ok = quality.get("source_gsd_status") == "VERIFIED" and source_gsd is not None and gsd_proof is not None
    gsd_detail = ("原始 GSD {:.3f} m；证明文件 SHA-256 与审核记录匹配（未判读证明内容）".format(source_gsd)
                  if gsd_ok else "原始 GSD 数值、证明文件哈希或审核记录缺失/已变化")
    checks.append({"item": "原始GSD", "status": "PASS" if gsd_ok else "NEEDS_REVIEW", "detail": gsd_detail})

    accuracy = quality.get("positional_accuracy") or {}
    try:
        accuracy_value = float(accuracy.get("value_m"))
    except (TypeError, ValueError):
        accuracy_value = None
    if accuracy_value is not None and (not math.isfinite(accuracy_value) or accuracy_value <= 0):
        accuracy_value = None
    accuracy_proof = _normalise_reviewed_evidence(
        accuracy.get("evidence"), accuracy.get("review"), require_hash=True)
    accuracy_ok = (accuracy.get("status") == "VERIFIED" and accuracy_value is not None
                   and accuracy_value < 0.2 and accuracy_proof is not None)
    if accuracy_value is not None and accuracy_value >= 0.2:
        accuracy_detail = "位置精度 {:.3f} m 未优于 0.2 m".format(accuracy_value)
    elif accuracy_ok:
        accuracy_detail = "位置精度 {:.3f} m；证明文件 SHA-256 与审核记录匹配（未判读证明内容）".format(accuracy_value)
    else:
        accuracy_detail = "位置精度数值、证明文件哈希或审核记录缺失/已变化"
    checks.append({"item": "影像位置精度", "status": "PASS" if accuracy_ok else "NEEDS_REVIEW",
                   "detail": accuracy_detail})
    return checks


def _tile_xy(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    return x, y


def _bbox_tiles(bbox, z):
    x0, y0 = _tile_xy(bbox[0], bbox[3], z)
    x1, y1 = _tile_xy(bbox[2], bbox[1], z)
    return (int(math.floor(x0)), int(math.floor(y0)), int(math.floor(x1)), int(math.floor(y1)))


def choose_zoom(bbox, max_tiles=600, max_zoom=18, min_zoom=12):
    """在瓦片预算内取最高层级（清晰度优先，避免一次拉几万张）。"""
    if not 0 <= min_zoom <= max_zoom <= 22 or max_tiles < 1:
        raise ValueError('请求层级与服务层级没有交集，或瓦片预算无效')
    last = None
    for z in range(max_zoom, min_zoom - 1, -1):
        x0, y0, x1, y1 = _bbox_tiles(bbox, z)
        count = (x1 - x0 + 1) * (y1 - y0 + 1)
        last = (z, count)
        if count <= max_tiles:
            return z, count
    return last if last else (min_zoom, 0)


def _download(provider, bbox, z, cache, tk=None, timeout=25, workers=12, max_tiles=600):
    cfg = _provider_config(provider, tk=tk)
    annotation = provider in ANNOTATION_PROVIDERS
    vector = provider in VECTOR_PROVIDERS
    minimum_bytes = 32 if annotation or vector else 500
    quality = lambda path: _tile_quality(path, annotation=annotation, vector=vector)
    if provider == "tianditu" and not tk:
        raise ValueError("天地图影像需要先申请 key（tk）。请在插件里填入 key，或改用项目自有影像。")
    cache = _service_cache_dir(cache, provider, tk)
    os.makedirs(cache, exist_ok=True)
    x0, y0, x1, y1 = _bbox_tiles(bbox, z)
    planned = (x1 - x0 + 1) * (y1 - y0 + 1)
    if planned > max_tiles:
        raise ValueError('BUDGET_EXCEEDED:下载计划超过瓦片预算，请缩小范围')
    targets = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            path = os.path.join(cache, "{}_{}_{}.jpg".format(z, x, y))
            if os.path.isfile(path):
                valid, _ = quality(path)
                if valid:
                    continue
                os.remove(path)
            targets.append((x, y, path))
    errors = []
    throttled = threading.Event()

    def fetch(item):
        x, y, path = item
        if throttled.is_set():
            return "{}:{} THROTTLED:skipped".format(x, y)
        url = _tile_url(cfg, z, x, y, tk)
        try:
            data = _fetch_tile(url, timeout)
            if len(data) < minimum_bytes:
                return '{}:{} CONTENT_INVALID:内容过短'.format(x,y)
            with open(path, "wb") as fh:
                fh.write(data)
            valid, reason = quality(path)
            if not valid:
                os.remove(path)
                return "{}:{} {}".format(x, y, reason)
            return None
        except urllib.error.HTTPError as exc:
            if exc.code in THROTTLE_HTTP:
                throttled.set()
            return "{}:{} {}".format(x, y, _online_error_reason(exc))
        except Exception as exc:  # noqa: BLE001 - 逐瓦片记录真实错误
            return "{}:{} {}".format(x, y, _online_error_reason(exc))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("workers", workers)))) as pool:
        for result in pool.map(fetch, targets):
            if result:
                errors.append(result)
    total = (x1 - x0 + 1) * (y1 - y0 + 1)
    cached, suspects = 0, []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            path = os.path.join(cache, "{}_{}_{}.jpg".format(z, x, y))
            if os.path.isfile(path):
                valid, reason = quality(path)
                if valid:
                    cached += 1
                if valid and reason:
                    suspects.append({"x": x, "y": y, "zoom": z, "reason": reason})
    return {"range": (x0, y0, x1, y1), "ok": cached, "fail": total - cached,
            "errors": errors[:5], "reason_counts": _reason_counts(errors), "cache": cache, "suspects": suspects}


def _mosaic(arcpy_gdal, tiles, z, cache, out_tif, overlay_cache=None):
    from osgeo import gdal, osr
    from PIL import Image
    x0, y0, x1, y1 = tiles["range"]
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    res = (2 * MERC_HALF) / (TILE * 2 ** z)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out_tif, nx * TILE, ny * TILE, 4, gdal.GDT_Byte,
                    options=["COMPRESS=LZW", "TILED=YES"])
    ds.SetGeoTransform((x0 * TILE * res - MERC_HALF, res, 0, MERC_HALF - y0 * TILE * res, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    for index in range(1, 5):
        ds.GetRasterBand(index).Fill(0)
    for i, x in enumerate(range(x0, x1 + 1)):
        for j, y in enumerate(range(y0, y1 + 1)):
            path = os.path.join(cache, "{}_{}_{}.jpg".format(z, x, y))
            if not os.path.exists(path):
                continue
            with Image.open(path) as source:
                tile = source.convert("RGBA").resize((TILE, TILE))
            overlay_path = os.path.join(overlay_cache, "{}_{}_{}.jpg".format(z, x, y)) if overlay_cache else None
            if overlay_path and os.path.isfile(overlay_path):
                with Image.open(overlay_path) as source:
                    overlay = source.convert("RGBA").resize((TILE, TILE))
                tile = Image.alpha_composite(tile, overlay)
            for band, channel in enumerate("RGBA", 1):
                ds.GetRasterBand(band).WriteRaster(i * TILE, j * TILE, TILE, TILE, tile.getchannel(channel).tobytes())
    ds.FlushCache()
    ds = None
    return out_tif


def _warp(src_tif, out_tif, target_wkid, resolution_m, resample_alg="bilinear"):
    from osgeo import gdal, osr
    src = osr.SpatialReference()
    src.ImportFromEPSG(3857)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(int(target_wkid))
    for srs in (src, dst):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    options = gdal.WarpOptions(dstSRS=dst, srcSRS=src, xRes=resolution_m, yRes=resolution_m,
                               resampleAlg=resample_alg, dstNodata=0, multithread=True)
    gdal.Warp(out_tif, src_tif, options=options)
    return out_tif


def _mercator_xy(lon, lat):
    """经纬度 → Web Mercator 米（与 XYZ 瓦片矩阵的 3857 定义一致）。"""
    import numpy as np
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    x = lon * MERC_HALF / 180.0
    y = np.log(np.tan((90.0 + lat) * math.pi / 360.0)) * MERC_HALF / math.pi
    return x, y


def _split_rgba(window, raster_count):
    """把 GDAL 读出的波段窗口规范化为 (RGB 三波段, alpha 单波段)。

    4 波段及以上：前 3 波段为 RGB、第 4 波段为 alpha；
    3 波段：前 3 波段为 RGB，补 alpha=255（不能当单波段灰度处理）；
    1 波段：灰度复制成 3 通道，补 alpha=255；
    2 波段没有明确 RGBA 语义，直接报错而不是猜测。
    """
    import numpy as np
    data = np.asarray(window, dtype=np.float64)
    count = int(raster_count)
    if count >= 4:
        return data[:3], data[3]
    if count == 3:
        return data[:3], np.full(data.shape[1:], 255.0)
    if count == 1:
        gray = data if data.ndim == 2 else data[0]
        return np.repeat(gray[None, :, :], 3, axis=0), np.full(gray.shape, 255.0)
    raise ValueError("GCJ-02 纠偏：源栅格波段数须为 1、3 或 ≥4，实际为 {}".format(count))


def _resample_bilinear_rgba(bands, alpha, col, row):
    """按源像元坐标（0 = 首像元中心）双线性采样 RGBA，越界贡献 alpha=0。

    颜色按预乘 alpha 加权后再反除，透明/越界像元的 RGB 不会在边界形成暗边。
    返回 uint8 数组，形状 (4, 行, 列)；完全无覆盖的像元 alpha=0。
    """
    import numpy as np
    bands = np.asarray(bands, dtype=np.float64)
    alpha = np.asarray(alpha, dtype=np.float64)
    col = np.asarray(col, dtype=np.float64)
    row = np.asarray(row, dtype=np.float64)
    if bands.ndim != 3 or bands.shape[0] < 3 or alpha.ndim != 2:
        raise ValueError("GCJ-02 纠偏：源 RGB/alpha 数组形状无效")
    if col.shape != row.shape:
        raise ValueError("GCJ-02 纠偏：采样坐标形状不一致")
    if col.size == 0:
        return np.zeros((4,) + col.shape, dtype=np.uint8)
    height, width = alpha.shape
    column_base = np.floor(col)
    row_base = np.floor(row)
    dc, dr = col - column_base, row - row_base
    column_base = column_base.astype(np.int64)
    row_base = row_base.astype(np.int64)
    accumulated_alpha = np.zeros(col.shape, dtype=np.float64)
    accumulated_rgb = np.zeros((3,) + col.shape, dtype=np.float64)
    for column, row_index, weight in (
            (column_base, row_base, (1.0 - dc) * (1.0 - dr)),
            (column_base + 1, row_base, dc * (1.0 - dr)),
            (column_base, row_base + 1, (1.0 - dc) * dr),
            (column_base + 1, row_base + 1, dc * dr)):
        valid = ((column >= 0) & (column < width) & (row_index >= 0) & (row_index < height)
                 & (weight > 0))
        if not valid.any():
            continue
        safe_column = np.clip(column, 0, width - 1)
        safe_row = np.clip(row_index, 0, height - 1)
        contribution = np.where(valid, alpha[safe_row, safe_column], 0.0) * weight
        accumulated_alpha += contribution
        for band_index in range(3):
            accumulated_rgb[band_index] += np.where(
                valid, bands[band_index][safe_row, safe_column], 0.0) * contribution
    corrected = np.zeros((4,) + col.shape, dtype=np.uint8)
    covered = accumulated_alpha > 1e-9
    denominator = np.maximum(accumulated_alpha, 1e-9)
    for band_index in range(3):
        corrected[band_index] = np.where(
            covered, np.clip(np.rint(accumulated_rgb[band_index] / denominator), 0, 255), 0.0
        ).astype(np.uint8)
    corrected[3] = np.clip(np.rint(accumulated_alpha), 0, 255).astype(np.uint8)
    return corrected


def _lonlat_from_mercator(x, y):
    """Web Mercator 米 → 经纬度（与 _mercator_xy 互逆，纯数学不依赖 GDAL）。"""
    if (hasattr(x, "shape") or hasattr(y, "shape")
            or isinstance(x, (list, tuple)) or isinstance(y, (list, tuple))):
        import numpy as np
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        return x / MERC_HALF * 180.0, np.degrees(np.arctan(np.sinh(y / MERC_HALF * np.pi)))
    lon = float(x) / MERC_HALF * 180.0
    lat = math.degrees(math.atan(math.sinh(float(y) / MERC_HALF * math.pi)))
    return lon, lat


def _normalized_srs(wkid):
    """返回 (原始 SRS, 传统轴序 SRS)。

    个别 GDAL 构建（如 ArcGIS Pro 3.1.5 的 GDAL 3.4e）在直接 ImportFromEPSG 后，
    AutoCreateWarpedVRT 与反向坐标变换会按权威轴序（N,E）解释投影 SRS，而正向变换
    仍按东/北轴序，导致输出网格与逐点反算不一致。经 ESRI WKT 往返后两种变换轴序
    一致（东/北）。原始对象保留 EPSG 编号，用于写输出投影。
    """
    from osgeo import osr
    original = osr.SpatialReference()
    original.ImportFromEPSG(int(wkid))
    original.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    normalized = osr.SpatialReference()
    normalized.ImportFromWkt(original.ExportToWkt(["FORMAT=WKT1_ESRI"]))
    normalized.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return original, normalized


def _gcj02_corrected_output(src_tif, out_tif, target_wkid, resolution_m, block_rows=64):
    """逐像素反算 GCJ-02 纠偏：目标 CRS 网格 → WGS84 → GCJ-02 → 3857 米制 → 双线性采样。

    对每个输出像元按自身位置反算源瓦片坐标，不使用整体平移；超出源范围的像元
    alpha=0。返回目标网格与方法记录，供 meta.correction 使用。
    """
    from osgeo import gdal, osr
    import numpy as np
    source = gdal.Open(src_tif)
    if source is None:
        raise ValueError("GCJ-02 纠偏：无法打开 Web Mercator 中间栅格")
    if source.RasterCount < 3:
        raise ValueError("GCJ-02 纠偏：中间栅格须含 RGB 波段")
    source_gt = source.GetGeoTransform()
    if source_gt[2] or source_gt[4] or source_gt[1] <= 0 or source_gt[5] >= 0:
        raise ValueError("GCJ-02 纠偏：中间栅格须为北向上的标准 Web Mercator 网格")
    source_width, source_height = source.RasterXSize, source.RasterYSize
    _merc_original, merc_srs = _normalized_srs(3857)
    target_original, target_srs = _normalized_srs(int(target_wkid))
    _wgs_original, wgs84_srs = _normalized_srs(4326)
    # 目标网格范围沿用 GDAL 建议的变换范围，仅用于确定输出网格；真实取样由下面的反算完成。
    vrt = gdal.AutoCreateWarpedVRT(source, merc_srs.ExportToWkt(), target_srs.ExportToWkt(),
                                   gdal.GRA_NearestNeighbour)
    if vrt is None:
        raise RuntimeError("GCJ-02 纠偏：无法建立目标坐标系输出网格")
    vrt_gt = vrt.GetGeoTransform()
    left, top = vrt_gt[0], vrt_gt[3]
    right = left + vrt_gt[1] * vrt.RasterXSize
    bottom = top + vrt_gt[5] * vrt.RasterYSize
    vrt = None
    # 投影坐标系用米制像元；地理坐标系退化为 GDAL 建议的度制像元，避免把米当度。
    cell = float(resolution_m) if target_original.IsProjected() else max(abs(vrt_gt[1]), abs(vrt_gt[5]))
    if not math.isfinite(cell) or cell <= 0:
        raise ValueError("GCJ-02 纠偏：目标像元大小无效")
    width = max(1, int(math.ceil((right - left) / cell)))
    height = max(1, int(math.ceil((top - bottom) / cell)))
    geo_transform = (left, cell, 0.0, top, 0.0, -cell)
    transform = osr.CoordinateTransformation(target_srs, wgs84_srs)
    if transform is None:
        raise ValueError("GCJ-02 纠偏：无法建立目标坐标系到 WGS84 的坐标变换")
    # 运行时探测反向变换轴序：以输出网格中心（对应源中心）为探针，不依赖正向变换
    # 的返回轴序（个别 GDAL 构建正反向轴序并不一致）。
    center_lon, center_lat = _lonlat_from_mercator(
        source_gt[0] + source_gt[1] * source_width / 2.0,
        source_gt[3] + source_gt[5] * source_height / 2.0)
    probe_x, probe_y = (left + right) / 2.0, (top + bottom) / 2.0

    def _hit(lon, lat):
        return (math.isfinite(lon) and math.isfinite(lat)
                and abs(lon - center_lon) < 0.05 and abs(lat - center_lat) < 0.05)

    axis_swap = False
    lon, lat, _ = transform.TransformPoint(probe_x, probe_y)
    if not _hit(lon, lat):
        lon, lat, _ = transform.TransformPoint(probe_y, probe_x)
        axis_swap = bool(_hit(lon, lat))
    if not axis_swap and not _hit(lon, lat):
        raise ValueError("GCJ-02 纠偏：目标坐标系到 WGS84 的坐标变换不可用")
    def _to_wgs(x, y):
        lon, lat, _ = transform.TransformPoint(y, x) if axis_swap else transform.TransformPoint(x, y)
        return lon, lat

    if target_original.IsProjected():
        # 防御：+x 方向应主要增加经度（东），+y 方向应主要增加纬度（北）；网格若被按 N/E
        # 轴序生成，这里会变号，宁可报错也不产出错位图。不能用“坐标量级接近 false_easting”
        # 判断：EPSG:3857 的 false_easting 为 0，会把正常的东坐标误判成北坐标。
        center_east, center_north = (left + right) / 2.0, (top + bottom) / 2.0
        probe_step = max(abs(cell) * 50.0, 100.0)
        lon0, lat0 = _to_wgs(center_east, center_north)
        lon_x, lat_x = _to_wgs(center_east + probe_step, center_north)
        lon_y, lat_y = _to_wgs(center_east, center_north + probe_step)
        if not (math.isfinite(lon0) and math.isfinite(lat0) and math.isfinite(lon_x)
                and math.isfinite(lat_x) and math.isfinite(lon_y) and math.isfinite(lat_y)):
            raise ValueError("GCJ-02 纠偏：目标网格方向探测失败，无法保证位置正确")
        if lon_x <= lon0 or lat_y <= lat0 or abs(lat_x - lat0) > abs(lat_y - lat0) \
                or abs(lon_y - lon0) > abs(lon_x - lon0):
            raise ValueError("GCJ-02 纠偏：目标网格轴序不是东/北（疑似 N/E），无法保证位置正确；"
                             "请核对 GDAL/PROJ 版本后重试")
    dataset = gdal.GetDriverByName("GTiff").Create(
        out_tif, width, height, 4, gdal.GDT_Byte, options=["COMPRESS=LZW", "TILED=YES"])
    if dataset is None:
        raise RuntimeError("GCJ-02 纠偏：无法创建输出 GeoTIFF")
    dataset.SetGeoTransform(geo_transform)
    dataset.SetProjection(target_original.ExportToWkt())
    dataset.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    columns = np.arange(width, dtype=np.float64)
    step = max(1, int(block_rows))
    # 控制单块临时点数，避免宽幅底图一次构造过大的坐标列表。
    if width * step > 2_000_000:
        step = max(1, 2_000_000 // max(1, width))
    for row_start in range(0, height, step):
        row_stop = min(height, row_start + step)
        xs = left + (columns + 0.5) * cell
        ys = top - (np.arange(row_start, row_stop, dtype=np.float64) + 0.5) * cell
        grid_x, grid_y = np.meshgrid(xs, ys)
        # ArcGIS Pro 3.1.5 的 GDAL 3.4e SWIG 绑定没有可用的批量 TransformPoints
        # （签名只剩 nCount），这里逐像元反算；1e6 像元约 1–2 s，仍保持逐像素精度。
        transform_point = transform.TransformPoint
        if axis_swap:
            transformed = [transform_point(float(y), float(x))
                           for x, y in zip(grid_x.ravel(), grid_y.ravel())]
        else:
            transformed = [transform_point(float(x), float(y))
                           for x, y in zip(grid_x.ravel(), grid_y.ravel())]
        coordinate = np.asarray(transformed, dtype=np.float64)
        valid_geo = np.isfinite(coordinate[:, 0]) & np.isfinite(coordinate[:, 1])
        gcj_lon, gcj_lat = _gcj02.wgs84_to_gcj02(coordinate[:, 0], coordinate[:, 1])
        merc_x, merc_y = _mercator_xy(gcj_lon, gcj_lat)
        source_col = ((merc_x - source_gt[0]) / source_gt[1] - 0.5).reshape(grid_x.shape)
        source_row = ((source_gt[3] - merc_y) / (-source_gt[5]) - 0.5).reshape(grid_x.shape)
        # 坐标变换失败（越出投影域）或反算溢出时按越界处理，采样结果 alpha=0。
        finite = (valid_geo & np.isfinite(source_col.ravel()) & np.isfinite(source_row.ravel())
                  ).reshape(grid_x.shape)
        source_col = np.where(finite, source_col, -10.0)
        source_row = np.where(finite, source_row, -10.0)
        # 只读取本块需要的源窗口，外扩 2 像元保证双线性邻域不被窗口截断。
        # 整块反算位置落在源范围外时也要收敛到合法窗口（结果全是 alpha=0）。
        win_col0 = max(0, min(source_width - 1, int(math.floor(float(source_col.min()))) - 2))
        win_col1 = max(win_col0, min(source_width - 1, int(math.ceil(float(source_col.max()))) + 2))
        win_row0 = max(0, min(source_height - 1, int(math.floor(float(source_row.min()))) - 2))
        win_row1 = max(win_row0, min(source_height - 1, int(math.ceil(float(source_row.max()))) + 2))
        window_width, window_height = win_col1 - win_col0 + 1, win_row1 - win_row0 + 1
        window = np.asarray(source.ReadAsArray(win_col0, win_row0, window_width, window_height),
                            dtype=np.float64)
        bands, alpha = _split_rgba(window, source.RasterCount)
        corrected = _resample_bilinear_rgba(bands, alpha, source_col - win_col0, source_row - win_row0)
        for band_index in range(4):
            dataset.GetRasterBand(band_index + 1).WriteArray(corrected[band_index], 0, row_start)
    dataset.FlushCache()
    dataset = None
    source = None
    return {"method": "per_pixel_inverse_gcj02", "applied": True, "resampling": "bilinear",
            "target_grid": {"width": width, "height": height, "cell_m": round(cell, 6),
                            "bounds": [round(left, 4), round(bottom, 4), round(right, 4), round(top, 4)]},
            "target_to_wgs84_axis_order": "northing_easting" if axis_swap else "easting_northing",
            "residual_tolerance_deg": _gcj02.INVERSE_TOLERANCE_DEG}


def _extent_wgs84(arcpy, extent, sr):
    pts = [arcpy.Point(extent.XMin, extent.YMin), arcpy.Point(extent.XMax, extent.YMax)]
    wgs = arcpy.SpatialReference(4326)
    lons, lats = [], []
    for pt in pts:
        projected = arcpy.PointGeometry(pt, sr).projectAs(wgs)
        lons.append(projected.centroid.X)
        lats.append(projected.centroid.Y)
    pad = max(0.002, abs(lons[1] - lons[0]) * 0.06)
    pad_y = max(0.002, abs(lats[1] - lats[0]) * 0.06)
    return (min(lons) - pad, min(lats) - pad_y, max(lons) + pad, max(lats) + pad_y)


def prepare_imagery(out_folder, target_sr_wkid, extent=None, raster=None, provider="demo",
                    tk=None, max_tiles=600, resolution_m=None, arcpy=None, label="project",
                    max_zoom=18, cache_dir=None, labels=False, resample_alg="bilinear",
                    gcj02_correct=False):
    """产出 imagery/<label>.tif（目标坐标系）与 imagery_source.json。

    高德等 GCJ-02 来源必须显式传 ``gcj02_correct=True``，否则抛 ValueError：GCJ-02
    不能直接生成 CGCS2000 成果，纠偏为逐像素反算（非整体平移），并在 meta 记录方法
    与精度说明。
    """
    if arcpy is None:
        import arcpy  # noqa: F401 - 延迟导入，便于单元测试传入桩
    os.makedirs(out_folder, exist_ok=True)
    meta = {"label": _manifest_label(label), "provider": provider, "target_wkid": int(target_sr_wkid),
            "source_label": PROVIDERS.get(provider, {}).get("short") or PROVIDERS.get(provider, {}).get("label", provider),
            "demo_only": bool(PROVIDERS.get(provider, {}).get("demo_only", False)),
            "license": PROVIDERS.get(provider, {}).get("license", ""),
            "acquired": "未提供时相资料", "status": "NEEDS_REVIEW",
            "source_gsd_m": None, "source_gsd_status": "UNVERIFIED",
            "output_pixel_size_m": None,
            "positional_accuracy": {"value_m": None, "status": "UNVERIFIED", "evidence": None},
            "correction": {"method": "none", "applied": False, "max_residual_m": None,
                           "accuracy_note": "未做 GCJ-02 纠偏；该方法仅适用于 GCJ-02 公共底图，其它来源无需处理。"}}

    # 1) 项目自有影像：直接引用/复制，只核对坐标系
    if raster:
        original_raster = raster
        raster, source_files, mosaicked = _prepare_raster_source(
            str(raster), out_folder, meta["label"], arcpy)
        meta.update({"provider": "project_dom", "source_label": "本地影像（来源未标注，须补来源证明）",
                     "demo_only": False, "license": "UNVERIFIED"})
        sidecar = os.path.join(original_raster, "imagery_metadata.json") if os.path.isdir(original_raster) else original_raster + ".source.json"
        if os.path.isfile(sidecar):
            with open(sidecar, encoding="utf-8") as stream:
                supplied = json.load(stream)
            if not isinstance(supplied, dict):
                raise ValueError("影像元数据旁置文件必须是 JSON 对象：{}".format(sidecar))
            for field in ("source_label", "acquired", "license", "demo_only"):
                if field in supplied:
                    meta[field] = supplied[field]
            if "source_gsd_m" in supplied:
                try:
                    source_gsd = float(supplied["source_gsd_m"])
                except (TypeError, ValueError):
                    source_gsd = None
                if source_gsd is not None and (not math.isfinite(source_gsd) or source_gsd <= 0):
                    source_gsd = None
                meta["source_gsd_m"] = source_gsd
                gsd_attestation = _normalise_reviewed_evidence(
                    supplied.get("source_gsd_evidence"), supplied.get("source_gsd_review"),
                    base_dir=os.path.dirname(sidecar))
                gsd_verified = (source_gsd is not None and supplied.get("source_gsd_status") == "VERIFIED"
                                and gsd_attestation is not None)
                meta["source_gsd_status"] = "VERIFIED" if gsd_verified else "UNVERIFIED"
                if gsd_verified:
                    meta["source_gsd_evidence"] = gsd_attestation["evidence"]
                    meta["source_gsd_review"] = gsd_attestation["review"]
            accuracy = supplied.get("positional_accuracy")
            if isinstance(accuracy, dict):
                try:
                    accuracy_value = float(accuracy.get("value_m"))
                except (TypeError, ValueError):
                    accuracy_value = None
                if accuracy_value is not None and (not math.isfinite(accuracy_value) or accuracy_value <= 0):
                    accuracy_value = None
                accuracy_attestation = _normalise_reviewed_evidence(
                    accuracy.get("evidence"), accuracy.get("review"), base_dir=os.path.dirname(sidecar))
                accuracy_verified = (accuracy.get("status") == "VERIFIED" and accuracy_value is not None
                                     and accuracy_attestation is not None)
                meta["positional_accuracy"] = {
                    "value_m": accuracy_value,
                    "status": "VERIFIED" if accuracy_verified else "UNVERIFIED",
                    "evidence": accuracy_attestation["evidence"] if accuracy_verified else accuracy.get("evidence"),
                    "review": accuracy_attestation["review"] if accuracy_verified else accuracy.get("review"),
                }
            meta["source_evidence_file"] = _source_fingerprint(sidecar)
            meta["supplied_evidence"] = supplied
        from .data_intake import imagery_provenance
        provenance = [imagery_provenance(path) for path in dict.fromkeys([str(original_raster)] + source_files)]
        meta["provenance"] = provenance
        if any(item["demo_only"] for item in provenance):
            meta["demo_only"] = True
            meta["source_label"] = "演示/合成成果（重新导入，不能作为真实项目DOM证明）"
        desc = arcpy.Describe(raster)
        if getattr(desc, "dataType", "") not in ("RasterDataset", "RasterBand", "MosaicDataset", "File"):
            raise ValueError("项目影像无法识别为栅格：{}".format(raster))
        src_sr = getattr(desc, "spatialReference", None)
        if src_sr is None or getattr(src_sr, "name", "Unknown") == "Unknown":
            raise ValueError("项目影像没有坐标系定义，无法定位。请提供配准后的影像（含 .prj/aux.xml）。")
        if str(getattr(src_sr, "factoryCode", 0) or 0) != str(int(target_sr_wkid)):
            meta["source_wkid"] = int(getattr(src_sr, "factoryCode", 0) or 0)
            meta["note"] = "项目影像坐标系与成果坐标系不同，制图时由 Pro 动态投影显示；正式成果须确认基准转换依据。"
        raster, processing = _local_dom_copy(raster, out_folder, meta["label"], extent, target_sr_wkid, arcpy)
        desc = arcpy.Describe(raster)
        cell_width = getattr(desc, "meanCellWidth", None)
        cell_height = getattr(desc, "meanCellHeight", None)
        if cell_width is None or cell_height is None:
            info = arcpy.Raster(raster)
            cell_width, cell_height = info.meanCellWidth, info.meanCellHeight
        mpu = getattr(src_sr, "metersPerUnit", None)
        output_pixel = None
        if cell_width and cell_height and mpu and float(mpu) > 0:
            output_pixel = round(max(abs(float(cell_width)), abs(float(cell_height))) * float(mpu), 4)
        meta.update({"source": original_raster,
                     "source_files": [_source_fingerprint(path) for path in source_files],
                     "source_fingerprint": _source_fingerprint(original_raster),
                     "output": raster, "resolution_m": output_pixel,
                     "output_pixel_size_m": output_pixel, "in_place": False, "processing": processing,
                     "source_kind": "project_dom_folder" if os.path.isdir(original_raster) else "project_dom",
                     "coverage_status": "NEEDS_REVIEW",
                     "note": "栅格像元大小仅作输出像元记录；原始航测 GSD 与位置精度须查原始元数据/检核报告。"})
        return _write_source_manifest(out_folder, meta)

    if extent is None:
        raise ValueError("未指定制图范围，无法准备影像")
    cfg = _provider_config(provider, tk=tk)
    gcj_source = cfg.get('coordinate_system') == 'GCJ-02'
    if gcj_source and not gcj02_correct:
        raise ValueError('高德底图使用 GCJ-02 坐标，不能直接生成 CGCS2000 成果；请设置 gcj02_correct=True '
                         '逐像素反算纠偏后再使用（纠偏为公开近似算法，不能替代控制点配准）。')
    meta.update(source_label=cfg.get('short', cfg['label']), demo_only=cfg.get('demo_only', False),
                license=cfg['license'], service_version=cfg.get('version'),
                service_coordinate_system=cfg['coordinate_system'],
                authorization_reference=cfg.get('authorization_reference', 'UNVERIFIED'))
    if gcj02_correct and not gcj_source:
        meta['correction'] = {"method": "none", "applied": False, "max_residual_m": None,
                              "accuracy_note": "该来源为 WGS84/Web Mercator，无需 GCJ-02 纠偏；gcj02_correct 已忽略。"}
    elif not gcj_source:
        meta['correction']['accuracy_note'] = "该来源为 WGS84/Web Mercator，不涉及 GCJ-02 偏移。"
    if provider not in PROVIDERS:
        if os.path.isfile(str(provider)):
            meta['service_config_fingerprint'] = _source_fingerprint(provider)
        else:
            meta['service_endpoint'] = provider
    if '{tk}' in cfg['url'] and not tk:
        raise ValueError('该影像服务需要 key，请填写授权凭证')
    sr = arcpy.SpatialReference(int(target_sr_wkid))
    bbox = _extent_wgs84(arcpy, extent, sr)
    gcj_padding_m = 0.0
    if gcj_source:
        table = _gcj02.offset_table([bbox[0], bbox[1], bbox[2], bbox[3]], grid=3)
        # 纠偏要把源瓦片整体平移几百米；下载范围必须比请求范围多留出该偏移量，
        # 否则输出覆盖范围的边缘会因源数据不足而变成 alpha=0。
        gcj_padding_m = max(50.0, float(table['max_offset_m']))
        center_lat = (bbox[1] + bbox[3]) / 2.0
        pad_lon = gcj_padding_m / (111320.0 * max(0.2, math.cos(math.radians(center_lat))))
        pad_lat = gcj_padding_m / 110540.0
        bbox = (bbox[0] - pad_lon, bbox[1] - pad_lat, bbox[2] + pad_lon, bbox[3] + pad_lat)
        meta['correction'] = {
            "method": "per_pixel_inverse_gcj02",
            # 纠偏只有在真正生成输出后才会置为 True；失败/无覆盖路径保持 False。
            "applied": False,
            "max_residual_m": None,
            "source_padding_m": round(gcj_padding_m, 3),
            "estimate": {
                "center_offset_m": table['center']['distance_m'],
                "max_offset_m": table['max_offset_m'],
                "offset_variation_m": table['variation_m'],
                "note": "偏移变化量说明整体平移无法消除的残差量级；这是算法偏移估计，不是配准残差实测。",
            },
            "accuracy_note": ("逐像素反算：目标 CRS 网格→WGS84→GCJ-02→Web Mercator→双线性采样，未使用整体平移；"
                              "算法反算容差 ≤1e-9 度（约 0.1 mm）只表示算法可逆性，不代表底图位置精度。"
                              "GCJ-02 为公开近似算法，不能替代控制点配准与测绘成果核查，不得声称“已精校正”。"),
        }
    if provider == "demo":
        online_metadata = query_world_imagery_metadata(bbox)
        meta["online_metadata"] = online_metadata
        if len(online_metadata["acquisition_dates"]) == 1:
            meta["acquired"] = online_metadata["acquisition_dates"][0]
        elif online_metadata["acquisition_dates"]:
            meta["acquired"] = "多位置时相不同；详见 online_metadata"
    cache = os.path.abspath(cache_dir) if cache_dir else os.path.join(out_folder, "tiles")
    z, count, attempts = select_zoom(provider, bbox, cache, tk=tk, max_tiles=max_tiles,
                                     max_zoom=min(int(max_zoom), cfg['max_zoom']), min_zoom=cfg['min_zoom'])
    diagnostics = {'requested_zoom': int(max_zoom), 'budget_tiles': int(max_tiles),
                   'decisions': [row['decision'] for row in attempts if row.get('decision')],
                   'probe_attempts': attempts, 'download_attempts': []}
    diagnostics['service_max_zoom'] = cfg['max_zoom']
    if int(max_zoom) > cfg['max_zoom']:
        diagnostics['decisions'].insert(0, 'SERVICE_MAX_ZOOM')
    meta['online_diagnostics'] = diagnostics
    last_probe = attempts[-1] if attempts else {}
    failures = last_probe.get('reason_counts', {})
    blocking = next((code for code in ('THROTTLED', 'AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_FAILED', 'HTTP_NOT_FOUND')
                     if failures.get(code)), None) if not last_probe.get('valid') else None
    if 'BUDGET_EXCEEDED' in diagnostics['decisions'] or blocking:
        diagnostics['primary_reason'] = blocking or 'BUDGET_EXCEEDED'
        meta.update(output=None, coverage_status='UNVERIFIED', planned_tiles=count, zoom=z,
                    warning='在线影像暂未取得：{}；不能据此判断没有高清覆盖，可缩小范围或核对服务后重试。'.format(
                        diagnostics['primary_reason']))
        return _write_source_manifest(out_folder, meta)
    tiles, used_z = None, int(z)
    for attempt in range(3):
        tiles = _download(provider, bbox, used_z, cache, tk=tk, max_tiles=max_tiles)
        total = tiles["ok"] + tiles["fail"]
        failure_counts = tiles.get('reason_counts') or _reason_counts(tiles['errors'])
        diagnostics['download_attempts'].append(dict(zoom=used_z, ok=tiles['ok'], fail=tiles['fail'],
            reason_counts=failure_counts, errors=tiles['errors']))
        if tiles["ok"] and (not total or tiles["fail"] / float(total) <= 0.02):
            break
        if failure_counts.get('THROTTLED'):
            break  # 已被站点限流，重试只会延长拦截
        if not tiles['ok'] and any(failure_counts.get(code) for code in ('AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_FAILED', 'HTTP_NOT_FOUND')):
            break
        if used_z <= cfg['min_zoom'] or attempt == 2:
            break
        if not any(failure_counts.get(code) for code in ('AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_FAILED', 'HTTP_NOT_FOUND')):
            used_z -= 1  # 只在数据缺失/占位等情况下降层；网络原因先重试原层
    meta.update({"bbox_wgs84": [round(v, 6) for v in bbox], "zoom": used_z,
                 "zoom_requested": int(max_zoom), "zoom_probe": attempts,
                 "planned_tiles": (lambda p: (p[2] - p[0] + 1) * (p[3] - p[1] + 1))(_bbox_tiles(bbox, used_z))})
    diagnostics['download_zoom'] = used_z
    diagnostics['reason_counts'] = tiles.get('reason_counts') or _reason_counts(tiles['errors'])
    if used_z < z:
        diagnostics['decisions'].append('DOWNLOAD_FALLBACK')
    diagnostics['primary_reason'] = next((code for code in ('THROTTLED', 'AUTHORIZATION_FAILED', 'NETWORK_FAILED', 'HTTP_FAILED',
        'HTTP_NOT_FOUND', 'DECODE_FAILED', 'CONTENT_INVALID', 'NO_COVERAGE') if diagnostics['reason_counts'].get(code)),
        'PARTIAL_COVERAGE' if tiles['fail'] else 'AVAILABLE')
    meta["tiles"] = {"ok": tiles["ok"], "fail": tiles["fail"], "errors": tiles["errors"],
                     "suspects": tiles.get("suspects", [])}
    if tiles["ok"] == 0:
        meta.update({"output": None, "coverage_status": "FAIL" if diagnostics['primary_reason'] == 'NO_COVERAGE' else 'UNVERIFIED', "status": "NEEDS_REVIEW",
                     "warning": "在线影像未获取到有效瓦片（{}）；仍可生成标明待确认的矢量预览。".format(
                         "、".join(tiles["errors"]) or provider)})
        return _write_source_manifest(out_folder, meta)

    merc = os.path.join(out_folder, "_{}_merc.tif".format(label))
    # 三类天地图来源的注记层都只在 labels=True 时才下载，避免“用户没要注记却被叠加”。
    if provider == "tianditu_vector" and labels:
        annotations = _download("tianditu_vector_labels", bbox, used_z, cache, tk=tk, max_tiles=max_tiles)
        meta["annotation"] = {"provider": "tianditu_vector_labels", "ok": annotations["ok"],
                              "fail": annotations["fail"], "errors": annotations["errors"],
                              "requested": True, "applied": True, "skipped": False}
        _mosaic(None, tiles, used_z, tiles["cache"], merc, overlay_cache=annotations["cache"])
    elif provider == "tianditu" and labels:
        annotations = _download("tianditu_image_labels", bbox, used_z, cache, tk=tk, max_tiles=max_tiles)
        meta["annotation"] = {"provider": "tianditu_image_labels", "ok": annotations["ok"],
                              "fail": annotations["fail"], "errors": annotations["errors"],
                              "requested": True, "applied": True, "skipped": False}
        _mosaic(None, tiles, used_z, tiles["cache"], merc, overlay_cache=annotations["cache"])
    elif provider == "tianditu_terrain" and labels:
        annotations = _download("tianditu_terrain_labels", bbox, used_z, cache, tk=tk, max_tiles=max_tiles)
        meta["annotation"] = {"provider": "tianditu_terrain_labels", "ok": annotations["ok"],
                              "fail": annotations["fail"], "errors": annotations["errors"],
                              "requested": True, "applied": True, "skipped": False}
        _mosaic(None, tiles, used_z, tiles["cache"], merc, overlay_cache=annotations["cache"])
    else:
        _mosaic(None, tiles, used_z, tiles["cache"], merc)
    if provider in LABEL_OVERLAY_SOURCES and not labels:
        # labels=False：如实记录“未请求注记”，不能留空让上游误以为已叠加。
        # fail=1 表示图面没有取得注记（不是下载失败）；报件门禁对矢量底图仍会提示
        # “地名道路注记未完整取得”，避免静默通过。
        meta["annotation"] = {"provider": None, "ok": 0, "fail": 1, "errors": [],
                              "requested": False, "applied": False, "skipped": True,
                              "note": "labels=False，未请求注记叠加（图面不含独立注记层）"}
    if gcj_source:
        meta.setdefault("annotation", {
            "provider": None, "ok": 0, "fail": 0, "errors": [],
            "requested": bool(labels), "applied": False, "skipped": True,
            "note": ("高德瓦片本身含注记，不叠加独立注记层。" if labels
                     else "labels=False；高德瓦片本身含注记，无独立注记层。")})
    web_res = (2 * MERC_HALF) / (TILE * 2 ** used_z)  # Web Mercator 地图单位像元间距
    out_tif = os.path.join(out_folder, "{}.tif".format(label))
    service_spacing = web_res * math.cos(math.radians((bbox[1] + bbox[3]) / 2))
    res = float(resolution_m) if resolution_m else service_spacing
    if gcj_source:
        # 逐像素反算纠偏；双线性采样是该方法的一部分，不接受其它重采样算法。
        meta['correction'].update(
            _gcj02_corrected_output(merc, out_tif, target_sr_wkid, max(0.01, res)))
        os.remove(merc)
    else:
        _warp(merc, out_tif, target_sr_wkid, max(0.01, res), resample_alg)
        os.remove(merc)
    pixel = _web_pixel_metadata(used_z, (bbox[1] + bbox[3]) / 2, res)
    meta.update({"output": out_tif, "resolution_m": pixel["output_pixel_size_m"],
                 "in_place": False, "zoom": used_z,
                 "zoom_requested": int(max_zoom), "service_pixel_spacing_m": pixel["service_pixel_spacing_m"],
                 "output_pixel_size_m": pixel["output_pixel_size_m"],
                 "source_gsd_m": None, "source_gsd_status": "UNVERIFIED",
                 "positional_accuracy": pixel["positional_accuracy"],
                 "usable_map_scale_advisory": usable_map_scale(pixel["service_pixel_spacing_m"]),
                 "coverage_status": "PASS" if tiles["fail"] == 0 else "NEEDS_REVIEW",
                 "status": "NEEDS_REVIEW",
                 "note": "在线瓦片像元间距不等于传感器原生 GSD，也不证明位置精度；报备适用性须核来源资料。"})
    return _write_source_manifest(out_folder, meta)
