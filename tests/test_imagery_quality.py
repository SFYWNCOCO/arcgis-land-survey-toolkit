"""影像瓦片质量判定：占位图/空白图必须被识别，避免整幅图变灰。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import imagery as I  # noqa: E402


def _save(path, pixels):
    PIL = pytest.importorskip("PIL.Image")
    import numpy as np
    PIL.fromarray(np.asarray(pixels, dtype="uint8")).save(path)


def test_low_texture_tile_is_preserved_as_review_suspect(tmp_path):
    path = str(tmp_path / "blank.jpg")
    _save(path, [[[205, 205, 205]] * 64] * 64)
    valid, reason = I._tile_quality(path)
    assert valid is True and "疑点" in reason


def test_small_valid_palette_vector_is_preserved_without_relaxing_imagery_threshold(tmp_path):
    Image=pytest.importorskip('PIL.Image')
    path=tmp_path/'compact.png'
    image=Image.new('P',(256,256),0);image.putpalette([245,242,233,205,210,200])
    image.save(path,bits=1,optimize=True)
    assert path.stat().st_size<500
    assert I._tile_quality(path)[0] is False
    valid,reason=I._tile_quality(path,vector=True)
    assert valid and '疑点' in reason


def test_undecodable_tile_is_never_accepted(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not-an-image" * 100)
    valid, reason = I._tile_quality(str(path))
    assert valid is False and "解码失败" in reason


def test_empty_annotation_is_valid_but_empty_imagery_is_rejected(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    tile = tmp_path / "empty_label.png"
    Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(tile)
    assert I._tile_quality(tile, annotation=True)[0] is True
    assert I._tile_quality(tile)[0] is False


def test_palette_tiles_keep_rgb_color_and_alpha_when_mosaicked(tmp_path):
    gdal = pytest.importorskip("osgeo.gdal")
    Image = pytest.importorskip("PIL.Image")
    base = tmp_path / "base"; base.mkdir()
    image = Image.new("P", (256, 256), 1)
    image.putpalette([0, 0, 0, 50, 180, 80] + [0] * (768 - 6))
    image.save(base / "12_1_1.jpg", format="PNG", transparency=0)
    output = str(tmp_path / "palette.tif")
    I._mosaic(None, {"range": (1, 1, 1, 1)}, 12, str(base), output)
    ds = gdal.Open(output)
    assert [int(ds.GetRasterBand(i).ReadAsArray(5, 5, 1, 1)[0, 0]) for i in range(1, 5)] == [50, 180, 80, 255]
    ds = None


def test_annotation_alpha_composites_without_changing_base_pixels(tmp_path):
    gdal = pytest.importorskip("osgeo.gdal")
    Image = pytest.importorskip("PIL.Image")
    base = tmp_path / "base"; base.mkdir()
    labels = tmp_path / "labels"; labels.mkdir()
    Image.new("RGBA", (256, 256), (220, 230, 210, 255)).save(base / "12_1_1.jpg", format="PNG")
    annotation = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    annotation.putpixel((5, 5), (200, 0, 0, 255)); annotation.save(labels / "12_1_1.jpg", format="PNG")
    output = str(tmp_path / "labels.tif")
    I._mosaic(None, {"range": (1, 1, 1, 1)}, 12, str(base), output, overlay_cache=str(labels))
    ds = gdal.Open(output)
    read = lambda x, y: [int(ds.GetRasterBand(i).ReadAsArray(x, y, 1, 1)[0, 0]) for i in range(1, 5)]
    assert read(5, 5) == [200, 0, 0, 255] and read(6, 6) == [220, 230, 210, 255]


def test_uniform_colored_surface_is_a_suspect_not_textured(tmp_path):
    path = str(tmp_path / "green.jpg")
    _save(path, [[[10, 180, 20]] * 64] * 64)
    valid, reason = I._tile_quality(path)
    assert valid is True and "疑点" in reason


def test_textured_tile_is_accepted(tmp_path):
    import numpy as np
    path = str(tmp_path / "textured.jpg")
    rng = np.random.default_rng(7)
    _save(path, rng.integers(0, 255, size=(64, 64, 3)))
    valid, reason = I._tile_quality(path)
    assert valid is True and reason == ""


def test_usable_map_scale_from_gsd():
    assert I.usable_map_scale(0.5) == 2500
    assert I.usable_map_scale(0.55) == 3000
    assert I.usable_map_scale(4.38) == 22000


def test_tile_cache_is_namespaced_by_service_and_credential(tmp_path):
    base = str(tmp_path / "tiles")
    esri = I._service_cache_dir(base, "demo")
    tianditu_a = I._service_cache_dir(base, "tianditu", "key-a")
    tianditu_b = I._service_cache_dir(base, "tianditu", "key-b")
    assert len({esri, tianditu_a, tianditu_b}) == 3
    assert "key-a" not in tianditu_a


def test_source_manifest_preserves_each_unit_and_unknown_accuracy(tmp_path):
    I._write_source_manifest(str(tmp_path), {
        "label": "overview", "provider": "demo", "source_gsd_m": None,
        "positional_accuracy": {"value_m": None, "status": "UNVERIFIED"},
    })
    I._write_source_manifest(str(tmp_path), {
        "label": "unit_01", "provider": "dom", "source_gsd_m": None,
        "positional_accuracy": {"value_m": None, "status": "UNVERIFIED"},
    })
    import json
    catalog = json.loads((tmp_path / "imagery_sources.json").read_text(encoding="utf-8"))
    assert set(catalog["sources"]) == {"overview", "unit_01"}
    assert (tmp_path / "imagery_source_overview.json").is_file()
    assert (tmp_path / "imagery_source_unit_01.json").is_file()


def test_web_pixel_spacing_never_claims_native_gsd_or_accuracy():
    metadata = I._web_pixel_metadata(zoom=18, latitude=24.5, output_pixel_size_m=0.2)
    assert metadata["source_gsd_m"] is None
    assert metadata["source_gsd_status"] == "UNVERIFIED"
    assert metadata["positional_accuracy"]["status"] == "UNVERIFIED"
    assert metadata["service_pixel_spacing_m"] > 0
    assert metadata["output_pixel_size_m"] == 0.2


def test_uav_request_package_lists_missing_evidence_and_delivery_requirements(tmp_path):
    result = I.write_uav_resurvey_request(
        str(tmp_path),
        [{"unit": "U01", "parcels": ["P01"], "bbox_native": [1, 2, 3, 4]}],
        {"U01": {"provider": "demo", "demo_only": True, "acquired": "未提供时相资料",
                 "service_pixel_spacing_m": 1.2, "output_pixel_size_m": 0.2}},
        wkid=4547, aoi_path=r"C:\project\aoi.geojson")
    assert result["status"] == "NEEDS_REVIEW"
    assert "位置精度未提供证明" in result["units"][0]["reasons"]
    assert "原始 GSD 未提供证明" in result["units"][0]["reasons"]
    assert len(result["delivery_checklist"]) >= 4
    assert (tmp_path / "补测需求.json").exists()
    assert (tmp_path / "补测清单.csv").exists()


def test_raster_folder_discovery_restores_arcgis_workspace(tmp_path):
    from types import SimpleNamespace
    out = tmp_path / "out"
    out.mkdir()
    mosaic_output = out / "unit_1_mosaic.tif"
    class FakeArcPy:
        class env:
            workspace = "previous-workspace"
        @staticmethod
        def ListRasters(*args):
            return ["a.tif", "b.tif"]
        @staticmethod
        def Describe(path):
            return SimpleNamespace(spatialReference=SimpleNamespace(name="Test CRS", factoryCode=4547),
                                   pixelType="U8", bandCount=3, meanCellWidth=0.5)
        @staticmethod
        def Exists(path):
            return str(path) == str(mosaic_output) and mosaic_output.exists()
        class management:
            @staticmethod
            def MosaicToNewRaster(*args):
                mosaic_output.write_bytes(b"mosaic")
    chosen, sources, mosaicked = I._prepare_raster_source(str(tmp_path), str(out), "unit_1", FakeArcPy)
    assert sources == [str(tmp_path / "a.tif"), str(tmp_path / "b.tif")]
    assert chosen.endswith("unit_1_mosaic.tif")
    assert mosaicked is True
    assert FakeArcPy.env.workspace == "previous-workspace"


def test_world_imagery_metadata_query_keeps_accuracy_unverified(monkeypatch):
    import io
    import json

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self):
            return json.dumps({"results": [{"layerName": "World Imagery",
                "attributes": {"AcquisitionDate": "2024-01-02", "Source": "Provider A",
                               "Resolution": "0.4 m"}}]}).encode("utf-8")

    calls = []
    def fake_open(request, timeout):
        calls.append(request.full_url)
        return Response()
    monkeypatch.setattr(I.urllib.request, "urlopen", fake_open)
    result = I.query_world_imagery_metadata([108.0, 22.0, 108.1, 22.1])
    assert len(calls) == 5
    assert result["acquisition_dates"] == ["2024-01-02"]
    assert result["reported_resolutions"] == ["0.4 m"]
    assert result["positional_accuracy"]["status"] == "UNVERIFIED"
    assert result["source_gsd_m"] is None


def test_no_online_coverage_returns_reviewable_record_instead_of_aborting(monkeypatch, tmp_path):
    from types import SimpleNamespace

    class FakeArcPy:
        @staticmethod
        def SpatialReference(_wkid): return object()

    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108.0, 22.0, 108.1, 22.1))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kwargs: (12, 1, []))
    monkeypatch.setattr(I, "_download", lambda *_args, **_kwargs: {
        "ok": 0, "fail": 1, "errors": ["占位图/无覆盖"], "cache": str(tmp_path / "tiles")})
    result = I.prepare_imagery(str(tmp_path), 4547, extent=SimpleNamespace(), provider="tianditu",
                               tk="test-key", arcpy=FakeArcPy, label="unit_empty", max_zoom=12)
    assert result["coverage_status"] == "FAIL"
    assert result["output"] is None
    assert "矢量预览" in result["warning"]
    assert (tmp_path / "imagery_source_unit_empty.json").exists()


def test_retried_imagery_stages_share_service_scoped_tile_cache(monkeypatch, tmp_path):
    from types import SimpleNamespace
    calls = []
    shared = str(tmp_path / "task_tiles")
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108, 22, 108.01, 22.01))
    def select(_provider, _bbox, cache, **_kwargs):
        calls.append(cache)
        return 12, 1, []
    monkeypatch.setattr(I, "select_zoom", select)
    monkeypatch.setattr(I, "_download", lambda _p, _b, _z, cache, **_kwargs:
                        {"ok": 0, "fail": 1, "errors": ["no coverage"], "cache": cache})
    api = SimpleNamespace(SpatialReference=lambda _wkid: object())
    for attempt in ("first", "continued"):
        I.prepare_imagery(str(tmp_path / attempt), 4524, extent=object(), provider="tianditu",
                          tk="test-key", arcpy=api, max_zoom=12, cache_dir=shared)
    assert calls == [shared, shared]
    assert "test-key" not in I._service_cache_dir(shared, "tianditu", "test-key")


def test_unc_dom_is_received_into_local_working_copy_without_resampling(tmp_path):
    from types import SimpleNamespace
    source = r"\\survey-server\share\项目DOM.tif"
    calls = []
    sr = SimpleNamespace(exportToString=lambda: "same-crs")
    class FakeArcPy:
        @staticmethod
        def Exists(path): return Path(path).is_file()
        @staticmethod
        def Describe(path): return SimpleNamespace(spatialReference=sr)
        @staticmethod
        def SpatialReference(_wkid): return sr
        class management:
            @staticmethod
            def Clip(raster, bounds, output, **kwargs):
                calls.append((raster, bounds, kwargs))
                Path(output).write_bytes(b"local-copy")
    extent = SimpleNamespace(XMin=1, YMin=2, XMax=3, YMax=4)
    output, receipt = I._local_dom_copy(source, str(tmp_path), "unit", extent, 4525, FakeArcPy)
    assert Path(output).parent == tmp_path
    assert calls[0][0] == source
    assert calls[0][2]["maintain_clipping_extent"] == "NO_MAINTAIN_EXTENT"
    assert receipt["resampling"] == "保留源像元网格"


def test_dom_copy_rejects_unknown_datum_transformation(tmp_path):
    from types import SimpleNamespace
    class FakeArcPy:
        @staticmethod
        def Exists(_path): return False
        @staticmethod
        def Describe(_path):
            return SimpleNamespace(spatialReference=SimpleNamespace(
                exportToString=lambda: "source-crs", GCS=SimpleNamespace(factoryCode=4326)))
        @staticmethod
        def SpatialReference(_wkid):
            return SimpleNamespace(exportToString=lambda: "target-crs", GCS=SimpleNamespace(factoryCode=4490))
    with pytest.raises(ValueError, match="大地基准不同"):
        I._local_dom_copy("dom.tif", str(tmp_path), "unit", object(), 4525, FakeArcPy)


def test_partial_tiles_always_mosaic_at_actual_download_zoom(monkeypatch, tmp_path):
    from types import SimpleNamespace
    levels, mosaic_levels = [], []
    monkeypatch.setattr(I, "_extent_wgs84", lambda *_args: (108, 22, 108.01, 22.01))
    monkeypatch.setattr(I, "select_zoom", lambda *_args, **_kw: (18, 2, []))
    def download(_provider, _bbox, zoom, cache, **_kw):
        levels.append(zoom)
        return {"ok": 1, "fail": 1, "errors": ["missing"], "cache": cache}
    def mosaic(_gdal, _tiles, zoom, _cache, output):
        mosaic_levels.append(zoom)
        Path(output).write_bytes(b"mosaic")
    monkeypatch.setattr(I, "_download", download)
    monkeypatch.setattr(I, "_mosaic", mosaic)
    monkeypatch.setattr(I, "_warp", lambda *_args: None)
    result = I.prepare_imagery(str(tmp_path), 4547, extent=object(), provider="tianditu",
                               tk="test", arcpy=SimpleNamespace(SpatialReference=lambda _wkid: object()))
    assert levels == [18, 17, 16]
    assert mosaic_levels == [16]
    assert result["zoom"] == 16


def test_reimported_demo_never_becomes_unmarked_project_dom(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    source = tmp_path / "old_preview.tif"
    source.write_bytes(b"preview")
    (tmp_path / "imagery_source.json").write_text(json.dumps({"output": str(source), "demo_only": True}), encoding="utf-8")
    sr = SimpleNamespace(name="CGCS2000", factoryCode=4525, metersPerUnit=1)
    api = SimpleNamespace(Describe=lambda _path: SimpleNamespace(dataType="RasterDataset", spatialReference=sr,
        meanCellWidth=1, meanCellHeight=1))
    monkeypatch.setattr(I, "_local_dom_copy", lambda *_args: (str(source), {}))
    result = I.prepare_imagery(str(tmp_path / "received"), 4525, raster=str(source), arcpy=api)
    assert result["demo_only"] is True
    assert result["source_gsd_status"] == "UNVERIFIED"


def test_project_dom_sidecar_cannot_claim_precision_from_a_missing_file(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    source = tmp_path / "project.tif"
    source.write_bytes(b"raster")
    sidecar = Path(str(source) + ".source.json")
    sidecar.write_text(json.dumps({
        "source_label": "项目正射影像", "acquired": "2026-09-01", "license": "项目授权",
        "source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
        "positional_accuracy": {
            "value_m": 0.15, "status": "VERIFIED", "evidence": {"path": "检查报告.pdf"},
            "review": {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    sr = SimpleNamespace(name="CGCS2000", factoryCode=4525, metersPerUnit=1)
    api = SimpleNamespace(Describe=lambda _path: SimpleNamespace(dataType="RasterDataset", spatialReference=sr,
        meanCellWidth=0.1, meanCellHeight=0.1))
    monkeypatch.setattr(I, "_local_dom_copy", lambda *_args: (str(source), {}))

    result = I.prepare_imagery(str(tmp_path / "received"), 4525, raster=str(source), arcpy=api)

    assert result["source_gsd_m"] == 0.1
    assert result["source_gsd_status"] == "UNVERIFIED"
    assert result["positional_accuracy"]["value_m"] == 0.15
    assert result["positional_accuracy"]["status"] == "UNVERIFIED"
    assert I.imagery_report_gate([result])[0] == "NEEDS_REVIEW"


def test_project_dom_sidecar_records_hash_for_existing_reviewed_precision_evidence(monkeypatch, tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace

    source = tmp_path / "project.tif"
    source.write_bytes(b"raster")
    evidence = tmp_path / "检查报告.pdf"
    evidence.write_bytes(b"verified precision report")
    sidecar = Path(str(source) + ".source.json")
    sidecar.write_text(json.dumps({
        "source_label": "项目正射影像", "acquired": "2026-09-01", "license": "项目授权",
        "source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
        "source_gsd_evidence": {"path": evidence.name},
        "source_gsd_review": {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"},
        "positional_accuracy": {
            "value_m": 0.15, "status": "VERIFIED", "evidence": {"path": evidence.name},
            "review": {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    sr = SimpleNamespace(name="CGCS2000", factoryCode=4525, metersPerUnit=1)
    api = SimpleNamespace(Describe=lambda _path: SimpleNamespace(dataType="RasterDataset", spatialReference=sr,
        meanCellWidth=0.1, meanCellHeight=0.1))
    monkeypatch.setattr(I, "_local_dom_copy", lambda *_args: (str(source), {}))

    result = I.prepare_imagery(str(tmp_path / "received"), 4525, raster=str(source), arcpy=api)

    proof = result["positional_accuracy"]["evidence"]
    assert proof["path"] == str(evidence.resolve())
    assert proof["sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert result["positional_accuracy"]["status"] == "VERIFIED"
    assert result["source_gsd_status"] == "VERIFIED"
    assert result["source_gsd_evidence"]["path"] == str(evidence.resolve())
    assert result["source_gsd_evidence"]["sha256"] == proof["sha256"]
    assert I.imagery_report_gate([result])[0] == "PASS"


def test_export_imagery_evidence_is_rechecked_against_current_file_hash(tmp_path):
    import hashlib

    evidence = tmp_path / "accuracy.pdf"
    evidence.write_bytes(b"reviewed report")
    review = {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"}
    proof = {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    quality = {
        "source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
        "source_gsd_evidence": proof, "source_gsd_review": review,
        "positional_accuracy": {"value_m": 0.15, "status": "VERIFIED",
                                "evidence": proof, "review": review},
    }

    checks = I.export_imagery_evidence_checks(quality)
    assert {row["item"]: row["status"] for row in checks} == {"原始GSD": "PASS", "影像位置精度": "PASS"}

    evidence.write_bytes(b"changed after preparation")
    checks = I.export_imagery_evidence_checks(quality)
    assert {row["item"]: row["status"] for row in checks} == {
        "原始GSD": "NEEDS_REVIEW", "影像位置精度": "NEEDS_REVIEW"}


def test_export_imagery_evidence_rejects_accuracy_at_or_above_point_two_m(tmp_path):
    evidence = tmp_path / "accuracy.pdf"
    evidence.write_bytes(b"reviewed report")
    import hashlib
    proof = {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    review = {"status": "VERIFIED", "reviewer": "审核员甲", "reviewed_at": "2026-10-24"}
    quality = {"source_gsd_m": 0.1, "source_gsd_status": "VERIFIED",
               "source_gsd_evidence": proof, "source_gsd_review": review,
               "positional_accuracy": {"value_m": 0.2, "status": "VERIFIED",
                                       "evidence": proof, "review": review}}

    checks = I.export_imagery_evidence_checks(quality)

    assert next(row for row in checks if row["item"] == "影像位置精度")["status"] == "NEEDS_REVIEW"


def test_resurvey_request_does_not_trust_self_declared_gsd_or_accuracy(tmp_path):
    request = I.write_uav_resurvey_request(
        str(tmp_path / "request"),
        [{"unit": "U1", "parcels": ["P1"]}],
        {"U1": {
            "output": "project.tif", "acquired": "2026-09-01", "source_gsd_m": 0.1,
            "source_gsd_status": "VERIFIED",
            "positional_accuracy": {"value_m": 0.15, "status": "VERIFIED",
                                     "evidence": {"path": str(tmp_path / "missing.pdf")}},
        }},
        4525, aoi_path="aoi.geojson",
    )

    assert request["status"] == "NEEDS_REVIEW"
    assert "原始 GSD 未提供证明" in request["units"][0]["reasons"]
    assert "位置精度未提供证明" in request["units"][0]["reasons"]
def test_raster_sampling_reads_pixels_instead_of_trusting_manifest():
    from types import SimpleNamespace
    from acceptance.imagery import raster_sampling_diagnostics
    raster = SimpleNamespace(meanCellWidth=4.38, meanCellHeight=4.38, width=200, height=100,
        bandCount=3, spatialReference=SimpleNamespace(name='CGCS2000', type='Projected', metersPerUnit=1.0))
    api = SimpleNamespace(Raster=lambda _: raster)
    result = raster_sampling_diagnostics('overview.tif', 2000, 300, arcpy_module=api)
    assert result['output_pixel_x_m'] == 4.38
    assert result['paper_pixel_mm'] == 2.19
    assert result['ground_print_pixel_m'] == pytest.approx(2000 * .0254 / 300)
    assert result['source_gsd_status'] == 'UNVERIFIED'
    assert result['positional_accuracy_status'] == 'UNVERIFIED'

