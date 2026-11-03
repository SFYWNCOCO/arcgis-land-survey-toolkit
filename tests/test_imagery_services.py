"""在线服务配置与失败原因必须可区分，预算不足不能冒充无覆盖。"""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acceptance import imagery as I
from acceptance import task_state as T


@pytest.mark.parametrize('provider,transparent', [('tianditu_vector',False),('tianditu_vector_labels',True),('tianditu_image_labels',True)])
def test_compressed_palette_tiles_are_decoded_consistently_by_probe_and_download(tmp_path,monkeypatch,provider,transparent):
    import io
    Image=pytest.importorskip('PIL.Image')
    image=Image.new('P',(256,256),0)
    image.putpalette([245,242,233,205,210,200])
    image.putpixel((128,128),1)
    stream=io.BytesIO()
    kwargs={'transparency':0} if transparent else {}
    image.save(stream,format='PNG',bits=1,optimize=True,**kwargs)
    payload=stream.getvalue();assert 32<len(payload)<500
    calls=[]
    monkeypatch.setattr(I,'_bbox_tiles',lambda *_args:(1,2,1,2))
    monkeypatch.setattr(I,'_fetch_tile',lambda *_args:calls.append(1) or payload)
    assert I._probe_tile(provider,(0,0,1,1),15,str(tmp_path),'synthetic',1)[:2]==(1,1)
    downloaded=I._download(provider,(0,0,1,1),15,str(tmp_path),'synthetic',workers=1)
    assert downloaded['ok']==1 and downloaded['fail']==0 and not downloaded['errors']
    assert I._probe_tile(provider,(0,0,1,1),15,str(tmp_path),'synthetic',1)[:2]==(1,1)
    assert I._download(provider,(0,0,1,1),15,str(tmp_path),'synthetic',workers=1)['ok']==1
    assert len(calls)==2  # Both valid compact caches are reused.


@pytest.mark.parametrize('bad_kind',['broken','wrong_size','transparent_base','known_placeholder'])
def test_vector_decode_gate_does_not_accept_invalid_or_no_coverage_tiles(tmp_path,monkeypatch,bad_kind):
    import io
    Image=pytest.importorskip('PIL.Image')
    stream=io.BytesIO()
    size=(16,16) if bad_kind=='wrong_size' else (256,256)
    image=Image.new('P',size,0);image.putpalette([245,242,233,205,210,200])
    options={'transparency':0} if bad_kind=='transparent_base' else {}
    image.save(stream,format='PNG',bits=1,optimize=True,**options)
    payload=b'not-an-image'*12 if bad_kind=='broken' else stream.getvalue()
    if bad_kind=='known_placeholder':monkeypatch.setattr(I,'PLACEHOLDER_MD5',{hashlib.md5(payload).hexdigest()})
    monkeypatch.setattr(I,'_bbox_tiles',lambda *_args:(1,2,1,2))
    monkeypatch.setattr(I,'_fetch_tile',lambda *_args:payload)
    good,sampled,reasons=I._probe_tile('tianditu_vector',(0,0,1,1),15,str(tmp_path),'synthetic',1)
    result=I._download('tianditu_vector',(0,0,1,1),15,str(tmp_path),'synthetic',workers=1)
    assert good==0 and sampled==1 and reasons
    assert result['ok']==0 and result['fail']==1
    assert 'AUTHORIZATION_FAILED' not in str(result['errors'])


def config(tmp_path, **changes):
    value = dict(schema_version=1, tile_scheme='xyz_web_mercator', label='授权影像',
                 url='https://example.invalid/tile/{z}/{y}/{x}?token={tk}', version='v1',
                 license='仅本项目使用', authorization_reference='项目授权资料第1项',
                 min_zoom=12, max_zoom=19, coordinate_system='EPSG:3857',
                 tile_size=256, matrix_origin='global_north_west', level_zero_resolution=156543.03392804097)
    value.update(changes)
    path = tmp_path / '影像服务.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return str(path)


def test_configured_service_and_version_isolate_cache(tmp_path):
    path = config(tmp_path)
    first = I._service_cache_dir(str(tmp_path), path, 'secret')
    cfg = I._provider_config(path)
    assert cfg['label'] == '授权影像' and cfg['demo_only'] is False
    config(tmp_path, version='v2')
    assert first != I._service_cache_dir(str(tmp_path), path, 'secret')
    assert 'secret' not in first


def test_wmts_provider_and_datum_contract(tmp_path):
    cfg = I._provider_config(config(tmp_path, tile_scheme='wmts_web_mercator',
        url='https://example.invalid/wmts?SERVICE=WMTS&TILEMATRIX=EPSG:3857:{z}&TILEROW={y}&TILECOL={x}&key={tk}'))
    assert cfg['coordinate_system'] == 'EPSG:3857'
    for crs in ('GCJ-02', 'BD-09', 'UNVERIFIED', '', None):
        with pytest.raises(ValueError, match='坐标'):
            I._provider_config(config(tmp_path, coordinate_system=crs))
    for matrix in (dict(tile_size=512), dict(matrix_origin='local'), dict(level_zero_resolution=100000)):
        with pytest.raises(ValueError, match='矩阵'):
            I._provider_config(config(tmp_path, **matrix))


def test_builtin_tianditu_supports_vector_and_image_labels():
    for source, layer in [('tianditu', 'img'), ('tianditu_vector', 'vec'),
                          ('tianditu_image_labels', 'cia'), ('tianditu_vector_labels', 'cva')]:
        cfg = I._provider_config(source)
        assert 'LAYER=' + layer in cfg['url']
        assert cfg['coordinate_system'] == 'EPSG:3857'


@pytest.mark.parametrize('changes', [dict(tile_scheme='tms'), dict(url='file:///x/{z}/{y}/{x}'),
    dict(url='https://host/{z}/{y}/{x}?token=literal-secret'), dict(authorization_reference=''),
    dict(url='https://host/{z}/{y}/{x.__class__}')])
def test_config_rejects_unsupported_or_unattributed_services(tmp_path, changes):
    with pytest.raises(ValueError):
        I._provider_config(config(tmp_path, **changes))


def test_service_file_content_invalidates_imagery_stage(tmp_path):
    source = tmp_path / 'parcels.shp'
    source.write_bytes(b'shape')
    path = config(tmp_path)
    old = T.input_fingerprint(str(source), {'imagery_provider': path})[1]
    config(tmp_path, version='v2')
    new = T.input_fingerprint(str(source), {'imagery_provider': path})[1]
    assert T.invalidated_phase(old, new) == 'imagery_prepared'


@pytest.mark.parametrize('error,code', [
    (HTTPError('https://host/?token=SECRET', 401, 'SECRET', {}, None), 'AUTHORIZATION_FAILED'),
    (HTTPError('https://host', 404, '', {}, None), 'HTTP_NOT_FOUND'),
    (TimeoutError('SECRET'), 'NETWORK_FAILED'), (URLError('SECRET'), 'NETWORK_FAILED')],
    ids=['unauthorized', 'not-found', 'timeout', 'url-error'])
def test_failures_do_not_leak_tokens_or_conflate_with_coverage(error, code):
    result = I._online_error_reason(error)
    assert code in result and 'SECRET' not in result and 'NO_COVERAGE' not in result


def test_probe_deduplicates_small_aoi_tiles(monkeypatch, tmp_path):
    from PIL import Image
    import io
    stream = io.BytesIO()
    Image.new('RGB', (256, 256), (20, 60, 80)).save(stream, format='JPEG')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return stream.getvalue()
    calls = []
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_args: (1, 2, 1, 2))
    monkeypatch.setattr(I.urllib.request, 'urlopen', lambda *a, **kw: calls.append(a) or Response())
    good, count, _ = I._probe_tile('demo', (0, 0, 1, 1), 12, str(tmp_path), None, 1)
    assert good == count == len(calls) == 1


def test_tile_budget_is_enforced_without_download(monkeypatch, tmp_path):
    monkeypatch.setattr(I, '_extent_wgs84', lambda *_args: (1, 2, 3, 4))
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_args: (0, 0, 100, 100))
    def forbidden(*_a, **_kw): raise AssertionError('预算超限不应发请求')
    monkeypatch.setattr(I, '_probe_tile', forbidden)
    monkeypatch.setattr(I, '_download', forbidden)
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider='tianditu', tk='k',
        max_tiles=3, arcpy=SimpleNamespace(SpatialReference=lambda _: object()))
    assert result['online_diagnostics']['primary_reason'] == 'BUDGET_EXCEEDED'
    assert result['coverage_status'] == 'UNVERIFIED' and result['output'] is None


def test_network_failure_stops_zoom_fallback_and_remains_unknown_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(I, '_extent_wgs84', lambda *_args: (108, 22, 108.01, 22.01))
    levels = []
    def probe(_p, _b, z, *_a):
        levels.append(z)
        return 0, 1, ['NETWORK_FAILED:TimeoutError']
    monkeypatch.setattr(I, '_probe_tile', probe)
    monkeypatch.setattr(I, '_download', lambda *_a, **_kw: pytest.fail('网络失败不能继续批量请求'))
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider='tianditu', tk='k',
        arcpy=SimpleNamespace(SpatialReference=lambda _: object()))
    assert levels == [18]
    assert result['online_diagnostics']['primary_reason'] == 'NETWORK_FAILED'
    assert result['coverage_status'] == 'UNVERIFIED'


def test_placeholder_failure_is_confirmed_no_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(I, '_extent_wgs84', lambda *_args: (108, 22, 108.01, 22.01))
    monkeypatch.setattr(I, '_probe_tile', lambda *_a: (0, 1, ['NO_COVERAGE:已知占位图']))
    monkeypatch.setattr(I, '_download', lambda *_a, **_kw: dict(ok=0, fail=1, errors=['NO_COVERAGE:已知占位图'], cache=''))
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider='tianditu', tk='k',
        arcpy=SimpleNamespace(SpatialReference=lambda _: object()))
    assert result['online_diagnostics']['primary_reason'] == 'NO_COVERAGE'
    assert result['coverage_status'] == 'FAIL'


def test_arcgis_cached_service_connects_without_json_file(monkeypatch):
    payload = dict(currentVersion=10.9, copyrightText='地图服务提供者', tileInfo=dict(
        rows=256, cols=256, spatialReference=dict(wkid=102100),
        origin=dict(x=-I.MERC_HALF, y=I.MERC_HALF),
        lods=[dict(level=level, resolution=2 * I.MERC_HALF / (256 * 2 ** level)) for level in (0, 1, 2, 18, 19)]))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def read(self, *_n): return json.dumps(payload).encode()
    monkeypatch.setattr(I.urllib.request, 'urlopen', lambda *_a, **_kw: Response())
    cfg = I._provider_config('https://example.invalid/arcgis/rest/services/Imagery/MapServer')
    assert cfg['url'].endswith('/MapServer/tile/{z}/{y}/{x}')
    assert cfg['max_zoom'] == 19 and cfg['license'] == '地图服务提供者'
    assert cfg['source_gsd_status'] == 'UNVERIFIED'


def test_arcgis_service_in_wrong_tile_matrix_is_rejected(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def read(self, *_n): return json.dumps(dict(tileInfo=dict(rows=256, cols=256,
            spatialReference=dict(wkid=4326), origin=dict(x=-180, y=90), lods=[]))).encode()
    monkeypatch.setattr(I.urllib.request, 'urlopen', lambda *_a, **_kw: Response())
    with pytest.raises(ValueError, match='Web Mercator'):
        I._provider_config('https://example.invalid/arcgis/rest/services/geographic/MapServer')


def test_empty_zoom_intersection_never_returns_zero_budget_plan(tmp_path):
    with pytest.raises(ValueError, match='层级'):
        I.select_zoom('demo', (1, 2, 3, 4), str(tmp_path), max_tiles=3, min_zoom=19, max_zoom=18)


def test_download_enforces_own_budget_before_enumerating_tiles(monkeypatch, tmp_path):
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_a: (0, 0, 100000, 100000))
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        I._download('demo', (1, 2, 3, 4), 19, str(tmp_path), max_tiles=3)


def test_partial_network_failure_retries_original_resolution_and_keeps_history(monkeypatch, tmp_path):
    levels = []
    monkeypatch.setattr(I, '_extent_wgs84', lambda *_a: (108, 22, 108.01, 22.01))
    monkeypatch.setattr(I, 'select_zoom', lambda *_a, **_k: (18, 3, []))
    def download(_p, _b, z, cache, **_kw):
        levels.append(z)
        return dict(ok=2 if len(levels) == 1 else 3, fail=1 if len(levels) == 1 else 0,
                    errors=['NETWORK_FAILED:TimeoutError'] if len(levels) == 1 else [], cache=cache)
    def mosaic(_a, _tiles, _z, _cache, out): Path(out).write_bytes(b'x')
    monkeypatch.setattr(I, '_download', download)
    monkeypatch.setattr(I, '_mosaic', mosaic)
    monkeypatch.setattr(I, '_warp', lambda *_a: None)
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider='tianditu', tk='k',
        arcpy=SimpleNamespace(SpatialReference=lambda _: object()))
    assert levels == [18, 18]
    assert result['online_diagnostics']['download_attempts'][0]['reason_counts']['NETWORK_FAILED'] == 1


def test_per_unit_service_file_content_invalidates_imagery(tmp_path):
    source = tmp_path / 'source.shp'
    source.write_bytes(b'x')
    path = config(tmp_path)
    options = dict(unit_imagery_sources={'U1': dict(provider=path)})
    old = T.input_fingerprint(str(source), options)[1]
    config(tmp_path, version='v2')
    new = T.input_fingerprint(str(source), options)[1]
    assert T.invalidated_phase(old, new) == 'imagery_prepared'


def test_key_is_encoded_as_one_query_value(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    key = 'abc+def&ghi/=x'
    calls = []
    def open_url(request, **_kw):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 503, 'offline', {}, None)
    monkeypatch.setattr(I.urllib.request, 'urlopen', open_url)
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_a: (1, 2, 1, 2))
    I._probe_tile(config(tmp_path), (1, 2, 3, 4), 12, str(tmp_path), key, 1)
    assert parse_qs(urlsplit(calls[0]).query)['token'] == [key]


def test_proxy_timeout_retries_direct_and_reuses_verified_route(monkeypatch):
    calls = []
    response = object()
    def system(*_a, **_kw):
        calls.append('system')
        raise URLError(TimeoutError('TLS handshake timed out'))
    class Direct:
        def open(self, *_a, **_kw):
            calls.append('direct')
            return response
    monkeypatch.setattr(I.urllib.request, 'urlopen', system)
    monkeypatch.setattr(I.urllib.request, 'getproxies', lambda: dict(https='http://127.0.0.1:1'))
    monkeypatch.setattr(I.urllib.request, 'build_opener', lambda *_a: Direct())
    I._NETWORK_ROUTES.clear()
    assert I._request_url('https://proxy-route.invalid/tile', timeout=1) is response
    assert I._request_url('https://proxy-route.invalid/metadata', timeout=1) is response
    assert calls == ['system', 'direct', 'direct']
    I._NETWORK_ROUTES.clear()


def test_service_authorization_rejection_does_not_retry_without_proxy(monkeypatch):
    def denied(*_a, **_kw): raise HTTPError('https://host', 403, '', {}, None)
    monkeypatch.setattr(I.urllib.request, 'urlopen', denied)
    monkeypatch.setattr(I.urllib.request, 'build_opener', lambda *_a: pytest.fail('服务拒绝不能换路绕过'))
    with pytest.raises(HTTPError):
        I._request_url('https://authorization.invalid/tile', timeout=1)


def test_proxy_connection_error_with_text_reason_still_tries_direct(monkeypatch):
    response = object()
    def failed(*_a, **_kw): raise URLError('proxy tunnel connection timed out')
    class Direct:
        def open(self, *_a, **_kw): return response
    monkeypatch.setattr(I.urllib.request, 'urlopen', failed)
    monkeypatch.setattr(I.urllib.request, 'getproxies', lambda: dict(https='http://127.0.0.1:1'))
    monkeypatch.setattr(I.urllib.request, 'build_opener', lambda *_a: Direct())
    I._NETWORK_ROUTES.clear()
    assert I._request_url('https://text-proxy-reason.invalid/', timeout=1) is response
    I._NETWORK_ROUTES.clear()


def test_metadata_queries_are_concurrent_not_five_serial_timeouts(monkeypatch):
    import threading
    barrier = threading.Barrier(5)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def read(self):
            barrier.wait(timeout=1)
            return json.dumps(dict(results=[dict(layerName='imagery', attributes=dict(Source='provider'))])).encode()
    monkeypatch.setattr(I, '_request_url', lambda *_a, **_kw: Response())
    result = I.query_world_imagery_metadata([108, 22, 108.01, 22.01])
    assert len(result['observations']) == 5


def test_probe_stops_on_service_authorization_failure(monkeypatch, tmp_path):
    calls = []
    def denied(*_a, **_kw):
        calls.append(1)
        raise HTTPError('https://host', 401, '', {}, None)
    monkeypatch.setattr(I, '_request_url', denied)
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_a: (0, 0, 5, 5))
    _, sampled, reasons = I._probe_tile('demo', (1, 2, 3, 4), 12, str(tmp_path), None, 1)
    assert sampled == len(calls) == 1 and 'AUTHORIZATION_FAILED' in reasons[0]


def test_direct_retry_rebuilds_request_after_proxy_handler_mutated_host(monkeypatch):
    request = I.urllib.request.Request('https://request-mutation.invalid/image', headers={'User-Agent': 'preview'})
    response = object()
    def failed(req, **_kw):
        req.set_proxy('127.0.0.1:1', 'https')
        raise URLError(TimeoutError('proxy TLS timed out'))
    class Direct:
        def open(self, req, **_kw):
            assert req.host == 'request-mutation.invalid'
            assert req.get_header('User-agent') == 'preview'
            return response
    monkeypatch.setattr(I.urllib.request, 'urlopen', failed)
    monkeypatch.setattr(I.urllib.request, 'getproxies', lambda: dict(https='http://127.0.0.1:1'))
    monkeypatch.setattr(I.urllib.request, 'build_opener', lambda *_a: Direct())
    I._NETWORK_ROUTES.clear()
    assert I._request_url(request, timeout=1) is response
    I._NETWORK_ROUTES.clear()


def test_world_imagery_live_attribute_aliases_are_retained(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def read(self):
            return json.dumps(dict(results=[dict(attributes={'RESOLUTION (M)': '0.34',
                'SOURCE': 'Vantor', 'SRC_DATE2': '1/3/2025'})])).encode()
    monkeypatch.setattr(I, '_request_url', lambda *_a, **_kw: Response())
    result = I.query_world_imagery_metadata([108, 22, 108.01, 22.01])
    assert result['reported_resolutions'] == ['0.34']
    assert result['acquisition_dates'] == ['1/3/2025']
    assert result['source_gsd_m'] is None


def test_tianditu_uses_supported_service_zoom_and_default_style():
    cfg = I._provider_config('tianditu')
    assert cfg['max_zoom'] == 18
    assert 'STYLE=default' in cfg['url']


def test_online_requests_identify_the_application_instead_of_fake_browser(monkeypatch, tmp_path):
    calls=[]
    def respond(request, **_kw):
        calls.append(request.get_header('User-agent'))
        raise HTTPError(request.full_url,503,'temporary',{},None)
    monkeypatch.setattr(I,'_request_url',respond)
    monkeypatch.setattr(I,'_bbox_tiles',lambda *_a:(1,2,1,2))
    I._probe_tile('tianditu',(1,2,3,4),18,str(tmp_path),'test-key',1)
    I._download('tianditu',(1,2,3,4),18,str(tmp_path),'test-key',workers=1)
    assert len(calls)==2 and all(value.startswith('ArcGIS-Pro-Acceptance-Assistant') for value in calls)


def test_direct_arcgis_service_uses_only_explicit_key_without_plaintext_cache(monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    calls=[]
    payload=dict(tileInfo=dict(rows=256,cols=256,spatialReference=dict(wkid=3857),
        origin=dict(x=-I.MERC_HALF,y=I.MERC_HALF),lods=[dict(level=18,resolution=2*I.MERC_HALF/(256*2**18))]))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_a):pass
        def read(self,*_a):return json.dumps(payload).encode()
    def request(req,**_kw):
        calls.append(req.full_url)
        return Response()
    monkeypatch.setattr(I,'_request_url',request)
    cfg=I._provider_config('https://private-service.invalid/Imagery/MapServer',tk='explicit+key&')
    assert parse_qs(urlsplit(calls[0]).query)['token']==['explicit+key&']
    assert cfg['url'].endswith('?token={tk}')
    assert 'explicit+key&' not in json.dumps(cfg)


def test_transparent_tile_cannot_claim_valid_coverage(tmp_path):
    from PIL import Image
    import numpy as np
    pixels=np.random.default_rng(42).integers(0,255,(64,64,4),dtype='uint8')
    pixels[:,:,3]=0
    path=tmp_path/'transparent.png'
    Image.fromarray(pixels,'RGBA').save(path)
    valid,reason=I._tile_quality(str(path))
    assert valid is False and 'NO_COVERAGE' in reason


def test_throttled_tile_retries_then_succeeds(monkeypatch):
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return b'x' * 600
    def flaky(request, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise HTTPError(request.full_url, 418, '', {}, None)
        return Response()
    monkeypatch.setattr(I, '_request_url', flaky)
    monkeypatch.setattr(I.time, 'sleep', lambda *_args: None)
    assert I._fetch_tile('https://tile.invalid/{z}', 5) == b'x' * 600
    assert len(calls) == 3


def test_throttled_batch_aborts_instead_of_hammering(monkeypatch, tmp_path):
    calls = []
    def denied(url, timeout, **_kwargs):
        calls.append(url)
        raise HTTPError(url, 418, '', {}, None)
    monkeypatch.setattr(I, '_fetch_tile', denied)
    monkeypatch.setattr(I, '_bbox_tiles', lambda *_args: (10, 10, 12, 12))
    result = I._download('tianditu', (1, 2, 3, 4), 18, str(tmp_path), 'test-key', workers=2)
    assert result['ok'] == 0
    assert result['reason_counts']['THROTTLED'] >= 1
    assert len(calls) <= 3


def test_throttle_is_distinct_from_other_http_failures():
    assert I._online_error_reason(HTTPError('u', 418, '', {}, None)).startswith('THROTTLED')
    assert I._online_error_reason(HTTPError('u', 429, '', {}, None)).startswith('THROTTLED')
    assert I._online_error_reason(HTTPError('u', 503, '', {}, None)).startswith('HTTP_FAILED')


def test_map_caption_never_names_a_source_without_imagery():
    text = I.imagery_caption({'source_label': '天地图影像',
                              'online_diagnostics': {'primary_reason': 'THROTTLED'}})
    assert text.startswith('影像底图：未取得')
    assert '天地图' not in text


def test_map_caption_states_delivered_pixel_size():
    text = I.imagery_caption({'output': 'a.tif', 'source_label': '天地图影像',
                              'acquired': '2025年3月', 'output_pixel_size_m': 0.5468})
    assert '0.55 m' in text and '非原生 GSD' in text


def test_report_gate_requires_output_and_point_two_meters():
    assert I.imagery_report_gate([{'output': None}])[0] == 'FAIL'
    assert I.imagery_report_gate([{'output': 'a', 'output_pixel_size_m': 0.55}])[0] == 'NEEDS_REVIEW'
    assert I.imagery_report_gate([{'output': 'a', 'output_pixel_size_m': 0.2}])[0] == 'NEEDS_REVIEW'


def test_report_precision_cannot_be_inferred_from_output_pixel_size():
    status, detail = I.imagery_report_gate([{
        'output': 'a', 'output_pixel_size_m': 0.1,
        'source_gsd_m': 0.1, 'source_gsd_status': 'VERIFIED',
        'positional_accuracy': {'value_m': None, 'status': 'UNVERIFIED', 'evidence': None},
    }])
    assert status == 'NEEDS_REVIEW'
    assert '位置精度' in detail


def test_report_gate_passes_only_with_verified_accuracy_below_threshold_and_evidence():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        evidence_path = Path(directory) / 'quality_report.pdf'
        evidence_path.write_bytes(b'precision proof')
        evidence = {'path': str(evidence_path), 'sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest()}
        review = {'status': 'VERIFIED', 'reviewer': '审核员甲', 'reviewed_at': '2026-10-24'}
        record = {
            'output': 'a', 'output_pixel_size_m': 0.8,
            'positional_accuracy': {'value_m': 0.19, 'status': 'VERIFIED', 'evidence': evidence, 'review': review},
        }
        assert I.imagery_report_gate([record])[0] == 'PASS'
        record['positional_accuracy']['value_m'] = 0.2
        assert I.imagery_report_gate([record])[0] == 'NEEDS_REVIEW'
        record['positional_accuracy']['value_m'] = 0.19
        evidence_path.write_bytes(b'changed after review')
        assert I.imagery_report_gate([record])[0] == 'NEEDS_REVIEW'


def test_report_gate_rejects_unverified_precision_evidence(tmp_path):
    evidence_path = tmp_path / 'missing_quality_report.pdf'
    record = {
        'output': 'a', 'output_pixel_size_m': 0.8,
        'positional_accuracy': {
            'value_m': 0.19, 'status': 'VERIFIED', 'evidence': {'path': str(evidence_path)},
            'review': {'status': 'VERIFIED', 'reviewer': '审核员甲', 'reviewed_at': '2026-10-24'},
        },
    }
    assert I.imagery_report_gate([record])[0] == 'NEEDS_REVIEW'


def test_report_gate_requires_a_named_reviewer_and_review_date(tmp_path):
    evidence_path = tmp_path / 'quality_report.pdf'
    evidence_path.write_bytes(b'precision proof')
    record = {
        'output': 'a', 'output_pixel_size_m': 0.8,
        'positional_accuracy': {
            'value_m': 0.19, 'status': 'VERIFIED', 'evidence': {'path': str(evidence_path)},
        },
    }
    record['positional_accuracy']['evidence']['sha256'] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    assert I.imagery_report_gate([record])[0] == 'NEEDS_REVIEW'


def test_report_gate_rejects_remote_or_reference_only_precision_proofs():
    record = {
        'output': 'a',
        'positional_accuracy': {
            'value_m': 0.19, 'status': 'VERIFIED',
            'evidence': {'path': 'https://example.invalid/quality.pdf', 'sha256': '0' * 64},
            'review': {'status': 'VERIFIED', 'reviewer': '审核员甲', 'reviewed_at': '2026-10-24'},
        },
    }
    assert I.imagery_report_gate([record])[0] == 'NEEDS_REVIEW'
