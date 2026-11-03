"""统一底图快照 CLI：provider 映射、GCJ-02 纠偏开关、JSON 契约与逐像素采样数学。

全部离线：不联网、不 import 真正的 Pro 库（arcpy 用桩注入，imagery 的 tif 写出用桩）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from acceptance import basemap_snapshot as B
from acceptance import imagery as I

REQUIRED_KEYS = {"status", "coverage_status", "output", "zoom", "output_pixel_size_m",
                 "correction", "warning", "ok"}
EXTENT = ["108.0", "22.0", "108.5", "22.5"]


def _fake_arcpy():
    class SpatialReference:
        def __init__(self, wkid):
            self.wkid = wkid

    class Extent:
        def __init__(self, *coordinates, spatial_reference=None):
            self.coordinates = coordinates
            self.spatial_reference = spatial_reference

    return SimpleNamespace(SpatialReference=SpatialReference, Extent=Extent)


def _install(monkeypatch, prepare, key=None):
    monkeypatch.setitem(sys.modules, "arcpy", _fake_arcpy())
    monkeypatch.setattr(B, "_prepare", prepare)
    if key is not None:
        monkeypatch.setattr(B, "_tianditu_key", key)


def _run(capsys, argv):
    code = B.main(argv)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return code, json.loads(lines[-1])


def _argv(source, out):
    return ["--out", str(out), "--wkid", "4525", "--extent"] + EXTENT + ["--source", source]


def _prepare_writing(calls, output_name="snapshot"):
    def prepare(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["out_folder"]) / (kwargs["label"] + ".tif")
        target.write_bytes(b"fake-tif")
        return {"status": "NEEDS_REVIEW", "coverage_status": "PASS", "output": str(target),
                "zoom": 17, "output_pixel_size_m": 0.55,
                "correction": {"method": "none", "applied": False,
                               "max_residual_m": None, "accuracy_note": "无"},
                "warning": None, "primary_reason": "AVAILABLE"}
    return prepare


def test_source_to_provider_mapping_and_key_requirement():
    assert B.provider_for("tianditu_image") == "tianditu"
    assert B.provider_for("tianditu_vector") == "tianditu_vector"
    assert B.provider_for("tianditu_terrain") == "tianditu_terrain"
    assert B.provider_for("amap_satellite") == "amap_satellite"
    assert B.provider_for("amap_vector") == "amap_vector"
    with pytest.raises(ValueError, match="不支持"):
        B.provider_for("amap_district")
    for source in ("tianditu_image", "tianditu_vector", "tianditu_terrain"):
        assert B.needs_tianditu_key(source) is True
    for source in ("amap_satellite", "amap_vector"):
        assert B.needs_tianditu_key(source) is False


def test_tianditu_snapshot_passes_key_labels_and_contract(monkeypatch, capsys, tmp_path):
    calls = []
    _install(monkeypatch, _prepare_writing(calls), key=lambda: "test-key")
    code, payload = _run(capsys, _argv("tianditu_terrain", tmp_path / "terrain.tif"))
    assert code == 0
    assert REQUIRED_KEYS <= set(payload)
    assert payload["ok"] is True
    assert payload["status"] == "NEEDS_REVIEW" and payload["coverage_status"] == "PASS"
    assert payload["zoom"] == 17 and payload["output_pixel_size_m"] == 0.55
    assert Path(payload["output"]).is_file()

    kwargs = calls[0]
    assert kwargs["provider"] == "tianditu_terrain"
    assert kwargs["tk"] == "test-key"
    assert kwargs["gcj02_correct"] is False
    assert kwargs["labels"] is True
    assert kwargs["max_tiles"] == 200 and kwargs["max_zoom"] == 18
    assert kwargs["target_sr_wkid"] == 4525
    assert kwargs["label"] == "terrain"


@pytest.mark.parametrize("source,provider", [("amap_satellite", "amap_satellite"),
                                             ("amap_vector", "amap_vector")])
def test_amap_snapshot_enables_per_pixel_correction_and_never_reads_tianditu_key(
        monkeypatch, capsys, tmp_path, source, provider):
    calls = []
    _install(monkeypatch, _prepare_writing(calls),
             key=lambda: pytest.fail("高德瓦片不需要天地图 key"))
    code, payload = _run(capsys, _argv(source, tmp_path / (source + ".tif")))
    assert code == 0
    assert payload["ok"] is True
    kwargs = calls[0]
    assert kwargs["provider"] == provider
    assert kwargs["gcj02_correct"] is True
    assert kwargs["tk"] is None
    assert kwargs["resample_alg"] == "bilinear"
    # 纠偏记录必须原样透传到 JSON，由调用方决定是否展示。
    assert payload["correction"]["method"] == "none"


def test_labels_false_is_forwarded(monkeypatch, capsys, tmp_path):
    calls = []
    _install(monkeypatch, _prepare_writing(calls), key=lambda: "k")
    argv = _argv("tianditu_image", tmp_path / "t.tif") + ["--labels", "false", "--max-tiles", "30"]
    code, _ = _run(capsys, argv)
    assert code == 0
    assert calls[0]["labels"] is False and calls[0]["max_tiles"] == 30


def test_no_coverage_result_returns_exit_one_without_faking_ok(monkeypatch, capsys, tmp_path):
    def prepare(**_kwargs):
        return {"status": "NEEDS_REVIEW", "coverage_status": "FAIL", "output": None,
                "zoom": 18, "output_pixel_size_m": None, "warning": "在线影像未获取到有效瓦片",
                "correction": None, "primary_reason": "NO_COVERAGE"}

    _install(monkeypatch, prepare, key=lambda: "k")
    code, payload = _run(capsys, _argv("tianditu_image", tmp_path / "empty.tif"))
    assert code == 1
    assert payload["ok"] is False
    assert payload["coverage_status"] == "FAIL"
    assert payload["warning"] == "在线影像未获取到有效瓦片"


def test_prepare_failure_is_reported_as_json_fail(monkeypatch, capsys, tmp_path):
    def prepare(**_kwargs):
        raise ValueError("高德底图使用 GCJ-02 坐标")

    _install(monkeypatch, prepare, key=lambda: "k")
    code, payload = _run(capsys, _argv("amap_vector", tmp_path / "x.tif"))
    assert code == 1
    assert payload["status"] == "FAIL"
    assert "GCJ-02" in payload["error"]


@pytest.mark.parametrize("argv", [
    ["--out", "x.png", "--wkid", "4525", "--extent"] + EXTENT + ["--source", "tianditu_image"],
    ["--out", "x.tif", "--wkid", "4525", "--extent"] + EXTENT + ["--source", "amap_district"],
    ["--out", "x.tif", "--wkid", "4525", "--extent", "108", "22", "108.5"] + ["--source", "tianditu_image"],
])
def test_invalid_cli_input_is_json_fail(monkeypatch, capsys, argv):
    _install(monkeypatch, lambda **_kwargs: pytest.fail("参数错误不应调用 prepare"), key=lambda: "k")
    code, payload = _run(capsys, argv)
    assert code == 1 and payload["status"] == "FAIL"


def test_basemap_providers_declare_coordinate_system_and_layers():
    terrain = I._provider_config("tianditu_terrain")
    assert terrain["coordinate_system"] == "EPSG:3857"
    assert "LAYER=ter" in terrain["url"] and "ter_w" in terrain["url"]
    labels = I._provider_config("tianditu_terrain_labels")
    assert "LAYER=cta" in labels["url"] and "cta_w" in labels["url"]

    satellite = I._provider_config("amap_satellite")
    assert satellite["coordinate_system"] == "GCJ-02"
    assert "style=6" in satellite["url"] and "webst0" in satellite["url"]
    assert 18 <= satellite["max_zoom"] <= 19
    assert "参考底图" in satellite["license"] and "未核实" in satellite["license"]
    vector = I._provider_config("amap_vector")
    assert "style=7" in vector["url"] and "wprd0" in vector["url"]
    assert vector["coordinate_system"] == "GCJ-02"

    # 高德子域必须是 01–04，不能沿用天地图的 t0..t7。
    hosts = {I._tile_url(satellite, 12, x, y).split("/")[2] for x in range(4) for y in range(2)}
    assert hosts == {"webst0{}.is.autonavi.com".format(index) for index in (1, 2, 3, 4)}


def test_prepare_imagery_rejects_gcj02_source_without_correction(tmp_path):
    with pytest.raises(ValueError, match="GCJ-02"):
        I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider="amap_satellite",
                          arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))


def test_mercator_projection_matches_web_tile_matrix():
    np = pytest.importorskip("numpy")
    x, y = I._mercator_xy(180.0, 0.0)
    assert float(x) == pytest.approx(I.MERC_HALF, abs=1e-6)
    assert float(y) == pytest.approx(0.0, abs=1e-6)
    x, y = I._mercator_xy(np.array([0.0, 108.0]), np.array([0.0, 22.0]))
    assert x[0] == pytest.approx(0.0)
    # 3857 经度方向与 XYZ 瓦片线性一致：108° 处 x = MERC_HALF * 108/180
    assert x[1] == pytest.approx(I.MERC_HALF * 108.0 / 180.0, rel=1e-12)
    # 与瓦片行列换算自洽：z=0 时 (0,0) 落在全球一半处
    tile_x, tile_y = I._tile_xy(108.0, 22.0, 0)
    assert tile_x == pytest.approx((108.0 + 180.0) / 360.0)


def test_mercator_inverse_round_trips_for_correction_grid():
    np = pytest.importorskip("numpy")
    longitudes = np.array([108.0, 108.5, 110.25])
    latitudes = np.array([22.0, 22.85, 25.1])
    x, y = I._mercator_xy(longitudes, latitudes)
    back_lon, back_lat = I._lonlat_from_mercator(x, y)
    assert np.allclose(back_lon, longitudes, atol=1e-9)
    assert np.allclose(back_lat, latitudes, atol=1e-9)


def test_split_rgba_three_band_source_gets_opaque_alpha():
    """P2-1：3 波段源必须按 RGB 处理并补 alpha=255，不能当单波段灰度。"""
    np = pytest.importorskip("numpy")
    window = np.stack([np.full((2, 3), 10.0), np.full((2, 3), 20.0), np.full((2, 3), 30.0)])
    bands, alpha = I._split_rgba(window, 3)
    assert bands.shape == (3, 2, 3)
    assert list(bands[:, 0, 0]) == [10.0, 20.0, 30.0]
    assert alpha.shape == (2, 3) and (alpha == 255.0).all()

    rgba = np.concatenate([window, np.full((1, 2, 3), 7.0)])
    bands4, alpha4 = I._split_rgba(rgba, 4)
    assert list(bands4[:, 0, 0]) == [10.0, 20.0, 30.0]
    assert alpha4[0, 0] == 7.0

    gray = np.full((2, 3), 99.0)
    bands_gray, alpha_gray = I._split_rgba(gray, 1)
    assert bands_gray.shape == (3, 2, 3) and (bands_gray == 99.0).all()
    assert (alpha_gray == 255.0).all()

    with pytest.raises(ValueError, match="波段数"):
        I._split_rgba(np.zeros((2, 2, 2)), 2)


def test_gcj02_corrected_output_three_band_source_matches_four_band(tmp_path):
    """P2-14：Pro/GDAL 环境下的 3 波段回归；系统 py3.12 无 osgeo 时跳过。"""
    gdal = pytest.importorskip("osgeo.gdal")
    osr = pytest.importorskip("osgeo.osr")
    np = pytest.importorskip("numpy")
    width = height = 512
    resolution = 2.388657133911758
    data = np.zeros((3, height, width), dtype=np.uint8)
    data[0] = (np.arange(width) * 7 % 256)[None, :]
    data[1] = (np.arange(height) * 5 % 256)[:, None]
    data[2] = 33
    geotransform = (12083165.0, resolution, 0.0, 2614146.0, 0.0, -resolution)

    def write_source(path, bands):
        dataset = gdal.GetDriverByName("GTiff").Create(
            str(path), width, height, bands.shape[0], gdal.GDT_Byte,
            options=["COMPRESS=LZW", "TILED=YES"])
        dataset.SetGeoTransform(geotransform)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        dataset.SetProjection(srs.ExportToWkt())
        for index in range(bands.shape[0]):
            dataset.GetRasterBand(index + 1).WriteArray(bands[index])
        dataset.FlushCache()
        dataset = None

    source_three = tmp_path / "src_three.tif"
    source_four = tmp_path / "src_four.tif"
    write_source(source_three, data)
    write_source(source_four, np.concatenate([data, np.full((1, height, width), 255, dtype=np.uint8)]))
    out_three = tmp_path / "out_three_4525.tif"
    out_four = tmp_path / "out_four_4525.tif"
    I._gcj02_corrected_output(str(source_three), str(out_three), 4525, resolution)
    I._gcj02_corrected_output(str(source_four), str(out_four), 4525, resolution)

    three = gdal.Open(str(out_three)).ReadAsArray()
    four = gdal.Open(str(out_four)).ReadAsArray()
    assert three.shape[0] == 4 and four.shape[0] == 4
    assert np.array_equal(three[:3], four[:3])
    assert np.array_equal(three[3], four[3])
    # 3 波段不得退化成“单波段灰度”：R（列编码）与 G（行编码）在有效覆盖区必须不同。
    covered = three[3] == 255
    assert covered.sum() > 1000
    assert not np.array_equal(three[0][covered], three[1][covered])
    assert not np.array_equal(three[0][covered], three[2][covered])


def test_gcj02_corrected_output_supports_web_mercator_target(tmp_path):
    """EPSG:3857 目标 false_easting=0，不能被误判成 N/E 轴序（安装联调实机回归）。"""
    gdal = pytest.importorskip("osgeo.gdal")
    osr = pytest.importorskip("osgeo.osr")
    np = pytest.importorskip("numpy")
    width = height = 256
    resolution = 4.777314267823516
    data = np.full((4, height, width), 200, dtype=np.uint8)
    geotransform = (12083165.0, resolution, 0.0, 2614146.0, 0.0, -resolution)
    source = tmp_path / "src_3857.tif"
    dataset = gdal.GetDriverByName("GTiff").Create(str(source), width, height, 4, gdal.GDT_Byte)
    dataset.SetGeoTransform(geotransform)
    mercator = osr.SpatialReference()
    mercator.ImportFromEPSG(3857)
    dataset.SetProjection(mercator.ExportToWkt())
    for index in range(4):
        dataset.GetRasterBand(index + 1).WriteArray(data[index])
    dataset.FlushCache()
    dataset = None

    out = tmp_path / "out_3857.tif"
    I._gcj02_corrected_output(str(source), str(out), 3857, resolution)
    result = gdal.Open(str(out))
    assert result is not None
    projection = osr.SpatialReference()
    projection.ImportFromWkt(result.GetProjection())
    # Pro 自带 GDAL 对 ESRI 风格 WKT 的 GetAuthorityCode 可能返回 None，不拿权威码断言；
    # 用投影类型 + false_easting=0 + 中心反算 WGS84 三项判定是否正确落在 Web Mercator。
    assert projection.IsProjected()
    assert "Mercator" in (projection.GetAttrValue("PROJECTION") or "")
    assert abs((projection.GetProjParm(osr.SRS_PP_FALSE_EASTING) or 0.0)) < 1e-9
    gt = result.GetGeoTransform()
    assert gt[1] > 0 and gt[5] < 0
    # 输出中心反算 WGS84 必须落在广西附近；轴序翻转会得到经度/纬度互换或异常值。
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    projection.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_wgs = osr.CoordinateTransformation(projection, wgs84)
    center = to_wgs.TransformPoint(gt[0] + gt[1] * result.RasterXSize / 2.0,
                                   gt[3] + gt[5] * result.RasterYSize / 2.0)
    assert 100.0 < center[0] < 120.0 and 20.0 < center[1] < 30.0


def test_bilinear_resampling_handles_alpha_without_dark_bleed():
    np = pytest.importorskip("numpy")
    bands = np.zeros((3, 2, 2), dtype=np.float64)
    bands[:, 0, 0] = 255.0       # 不透明白色
    bands[:, 0, 1] = 0.0         # 透明像元（RGB 是垃圾值 0）
    bands[:, 1, 0] = 10.0
    bands[:, 1, 1] = 20.0
    alpha = np.array([[255.0, 0.0], [0.0, 0.0]])

    exact = I._resample_bilinear_rgba(bands, alpha, np.array([[0.0]]), np.array([[0.0]]))
    assert exact.shape == (4, 1, 1)
    assert list(exact[:, 0, 0]) == [255, 255, 255, 255]

    mixed = I._resample_bilinear_rgba(bands, alpha, np.array([[0.5]]), np.array([[0.0]]))
    # 透明像元的黑 RGB 不得被混进来形成暗边；alpha 按权重衰减到一半。
    assert mixed[0, 0, 0] == 255 and mixed[1, 0, 0] == 255 and mixed[2, 0, 0] == 255
    assert mixed[3, 0, 0] == 128

    outside = I._resample_bilinear_rgba(bands, alpha, np.array([[-5.0]]), np.array([[0.0]]))
    assert outside[3, 0, 0] == 0

    empty = I._resample_bilinear_rgba(bands, alpha, np.zeros((0, 0)), np.zeros((0, 0)))
    assert empty.shape == (4, 0, 0)


def _patch_imagery_network(monkeypatch, tmp_path, calls):
    """离线替换底图下载：记录实际请求的 provider，mosaic/warp 只写占位文件。"""
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108.0, 22.0, 108.1, 22.1))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kwargs: (16, 1, []))

    def download(provider, *_args, **_kwargs):
        calls.append(provider)
        return {"ok": 1, "fail": 0, "errors": [], "cache": str(tmp_path / provider)}

    def mosaic(_gdal, _tiles, _zoom, _cache, out, **_kwargs):
        Path(out).write_bytes(b"merc")

    monkeypatch.setattr(I, "_download", download)
    monkeypatch.setattr(I, "_mosaic", mosaic)
    monkeypatch.setattr(I, "_warp", lambda *_args: None)


@pytest.mark.parametrize("provider,label_provider", [
    ("tianditu", "tianditu_image_labels"),
    ("tianditu_terrain", "tianditu_terrain_labels"),
    ("tianditu_vector", "tianditu_vector_labels"),
])
def test_tianditu_sources_overlay_labels_only_when_requested(monkeypatch, tmp_path, provider, label_provider):
    """P2-2：labels=True 时三类天地图来源才下载各自注记层并叠加。"""
    calls = []
    _patch_imagery_network(monkeypatch, tmp_path, calls)
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider=provider,
                               tk="k", labels=True,
                               arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))
    assert calls == [provider, label_provider]
    assert result["annotation"]["provider"] == label_provider
    assert result["annotation"]["requested"] is True
    assert result["annotation"]["applied"] is True
    assert result["annotation"]["skipped"] is False


@pytest.mark.parametrize("provider", ["tianditu", "tianditu_terrain", "tianditu_vector"])
def test_tianditu_sources_skip_annotation_download_when_labels_false(monkeypatch, tmp_path, provider):
    """P2-2：labels=False 时不得下载 cva/cia/cta，meta.annotation 如实标 skipped。"""
    calls = []
    _patch_imagery_network(monkeypatch, tmp_path, calls)
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider=provider,
                               tk="k", labels=False,
                               arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))
    assert calls == [provider]  # 只请求底图瓦片，不请求任何注记 provider
    annotation = result["annotation"]
    assert annotation["provider"] is None
    assert annotation["requested"] is False
    assert annotation["applied"] is False
    assert annotation["skipped"] is True
    assert annotation["ok"] == 0 and annotation["fail"] == 1  # 图面没有注记，报件门禁应提示
    assert "labels=False" in annotation["note"]


def test_amap_correction_is_per_pixel_and_records_metadata(monkeypatch, tmp_path):
    """高德来源必须走逐像素纠偏，不得退回整体 _warp；meta 记录方法与偏移估计。"""
    correction_calls = []
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108.0, 22.0, 108.1, 22.1))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kwargs: (16, 1, []))
    monkeypatch.setattr(I, "_download", lambda *_args, **_kwargs: {
        "ok": 1, "fail": 0, "errors": [], "cache": str(tmp_path / "tiles")})

    def mosaic(_gdal, _tiles, _zoom, _cache, out, **_kwargs):
        Path(out).write_bytes(b"merc")

    monkeypatch.setattr(I, "_mosaic", mosaic)
    monkeypatch.setattr(I, "_warp", lambda *_args: pytest.fail("高德底图不得跳过逐像素纠偏"))

    def corrected(source, out, wkid, resolution, **_kwargs):
        correction_calls.append((source, out, wkid, resolution))
        Path(out).write_bytes(b"corrected")
        return {"method": "per_pixel_inverse_gcj02", "applied": True, "resampling": "bilinear",
                "target_grid": {"width": 8, "height": 8, "cell_m": resolution,
                                "bounds": [0, 0, 1, 1]},
                "residual_tolerance_deg": 1e-9}

    monkeypatch.setattr(I, "_gcj02_corrected_output", corrected)
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider="amap_satellite",
                               gcj02_correct=True,
                               arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))

    assert len(correction_calls) == 1
    assert correction_calls[0][2] == 4525
    assert Path(result["output"]).is_file()
    correction = result["correction"]
    assert correction["method"] == "per_pixel_inverse_gcj02"
    assert correction["applied"] is True and correction["resampling"] == "bilinear"
    assert correction["target_grid"]["width"] == 8
    assert correction["estimate"]["center_offset_m"] > 50
    assert correction["estimate"]["offset_variation_m"] > 0
    assert "整体平移" in correction["accuracy_note"]
    assert "不能替代控制点配准" in correction["accuracy_note"]
    assert result["service_coordinate_system"] == "GCJ-02"
    assert result["demo_only"] is True and "参考底图" in result["license"]
    annotation = result["annotation"]
    assert annotation["provider"] is None and annotation["applied"] is False
    assert annotation["requested"] is False and annotation["skipped"] is True
    assert "高德瓦片本身含注记" in annotation["note"] and "labels=False" in annotation["note"]


def test_amap_no_tiles_keeps_correction_marked_unapplied(monkeypatch, tmp_path):
    """没有输出时不得声称已纠偏，但仍要记录方法与偏移估计。"""
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108.0, 22.0, 108.1, 22.1))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kwargs: (16, 1, []))
    monkeypatch.setattr(I, "_download", lambda *_args, **_kwargs: {
        "ok": 0, "fail": 1, "errors": ["NO_COVERAGE:offline"], "cache": str(tmp_path / "tiles"),
        "reason_counts": {"NO_COVERAGE": 1}})
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider="amap_vector",
                               gcj02_correct=True,
                               arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))
    assert result["output"] is None
    correction = result["correction"]
    assert correction["applied"] is False
    assert correction["method"] == "per_pixel_inverse_gcj02"
    assert correction["estimate"]["center_offset_m"] > 0


def test_gcj_flag_on_wgs84_source_is_recorded_as_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108.0, 22.0, 108.1, 22.1))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kwargs: (16, 1, []))
    monkeypatch.setattr(I, "_download", lambda *_args, **_kwargs: {
        "ok": 0, "fail": 1, "errors": ["NO_COVERAGE:offline"], "cache": str(tmp_path / "tiles"),
        "reason_counts": {"NO_COVERAGE": 1}})
    result = I.prepare_imagery(str(tmp_path), 4525, extent=object(), provider="tianditu", tk="k",
                               gcj02_correct=True,
                               arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))
    assert result["correction"]["applied"] is False
    assert "忽略" in result["correction"]["accuracy_note"]
