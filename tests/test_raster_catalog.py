import json
import sys
from types import SimpleNamespace

import pytest


class _FakeRaster:
    def __init__(self, path, band_count=1, wkid=4525, sr_name="CGCS2000 / GK Zone 37", nodata_values=None):
        self.path = path
        self.width = 120
        self.height = 80
        self.bandCount = band_count
        self.meanCellWidth = 10.0
        self.meanCellHeight = 10.0
        self.pixelType = "F32"
        self.noDataValue = -9999.0
        if nodata_values is not None:
            self.noDataValues = nodata_values
        self.extent = SimpleNamespace(XMin=37400000, YMin=2689000, XMax=37401200, YMax=2689800)
        self.spatialReference = SimpleNamespace(
            factoryCode=wkid,
            name=sr_name,
            type="Projected" if sr_name != "Unknown" else "Unknown",
            exportToString=lambda: "CUSTOM_WKT" if wkid == 0 and sr_name != "Unknown" else "KNOWN_WKT",
        )


def _install_fake_arcpy(monkeypatch, raster_by_name):
    def raster(path):
        value = raster_by_name[path]
        if isinstance(value, Exception):
            raise value
        return value

    fake = SimpleNamespace(
        Raster=raster,
        Describe=lambda path: SimpleNamespace(spatialReference=raster_by_name[path].spatialReference),
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake)


def test_audit_raster_package_distinguishes_known_custom_and_unknown_crs(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    known = source / "known.tif"
    custom = source / "custom.tif"
    unknown = source / "unknown.asc"
    for path in (known, custom, unknown):
        path.write_bytes(b"probe")
    (source / "known.tfw").write_text("world file", encoding="ascii")
    rasters = {
        str(known): _FakeRaster(str(known)),
        str(custom): _FakeRaster(str(custom), wkid=0, sr_name="Sphere ArcInfo LAEA"),
        str(unknown): _FakeRaster(str(unknown), wkid=0, sr_name="Unknown"),
    }
    _install_fake_arcpy(monkeypatch, rasters)

    report = audit_raster_package(str(source), str(output))

    by_path = {item["path"].replace("\\", "/"): item for item in report["rasters"]}
    assert by_path["known.tif"]["status"] == "REVIEW_KNOWN_CRS"
    assert by_path["known.tif"]["companions"] == ["known.tfw"]
    assert by_path["custom.tif"]["status"] == "REVIEW_CUSTOM_CRS"
    assert by_path["custom.tif"]["crs_wkt"] == "CUSTOM_WKT"
    assert by_path["custom.tif"]["crs_defined"] is True
    assert by_path["custom.tif"]["same_crs_requires_pairwise_verification"] is True
    assert by_path["unknown.asc"]["status"] == "BLOCKED_UNKNOWN_CRS"
    assert report["summary"]["raster_count"] == 3
    assert (output / report["outputs"]["json"]).is_file()
    assert (output / report["outputs"]["csv"]).is_file()
    assert json.loads((output / report["outputs"]["json"]).read_text(encoding="utf-8"))["summary"] == report["summary"]


def test_multiband_nodata_and_review_flags_are_preserved(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    raster_path = source / "multiband.tif"
    raster_path.write_bytes(b"probe")
    _install_fake_arcpy(monkeypatch, {
        str(raster_path): _FakeRaster(str(raster_path), band_count=3, wkid=0,
                                     sr_name="Sphere CRS", nodata_values=(-9999, None, float("nan")))
    })

    item = audit_raster_package(str(source), str(output))["rasters"][0]

    assert item["nodata_values"] == [-9999.0, None, None]
    assert item["status"] == "REVIEW_CUSTOM_CRS"
    assert "MULTIBAND" in item["review_flags"]
    assert item["same_crs_requires_pairwise_verification"] is True
    csv_text = next(output.glob("*栅格清单.csv")).read_text(encoding="utf-8-sig")
    assert "crs_wkt" in csv_text and "extent" in csv_text


def test_crf_directory_and_filegdb_rasters_are_discovered(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    crf = source / "scene.crf"
    gdb = source / "data.gdb"
    crf.mkdir()
    gdb.mkdir()
    rasters = {
        str(crf): _FakeRaster(str(crf)),
        str(gdb / "elevation"): _FakeRaster(str(gdb / "elevation")),
    }
    calls = []
    fake = SimpleNamespace(
        Raster=lambda path: rasters[path],
        Describe=lambda path: SimpleNamespace(spatialReference=rasters[path].spatialReference),
        env=SimpleNamespace(workspace=None),
        ListRasters=lambda *args: calls.append(fake.env.workspace) or (["elevation"] if fake.env.workspace == str(gdb) else []),
        ListDatasets=lambda *args: [],
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake)

    report = audit_raster_package(str(source), str(output))

    found = {item["path"].replace("\\", "/") for item in report["rasters"]}
    assert found == {"scene.crf", "data.gdb/elevation"}
    assert report["summary"]["raster_count"] == 2
    assert str(gdb) in calls


def test_audit_raster_package_keeps_read_errors_in_inventory(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    broken = source / "broken.tif"
    broken.write_bytes(b"bad")
    _install_fake_arcpy(monkeypatch, {str(broken): RuntimeError("ArcPy cannot open raster")})

    report = audit_raster_package(str(source), str(output))

    assert report["rasters"][0]["status"] == "READ_ERROR"
    assert "ArcPy cannot open raster" in report["rasters"][0]["error"]


def test_unreferenced_jpeg_is_reviewed_as_image_not_blocked_raster(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    illustration = source / "map-figure.jpg"
    illustration.write_bytes(b"probe")
    _install_fake_arcpy(monkeypatch, {
        str(illustration): _FakeRaster(str(illustration), wkid=0, sr_name="Unknown")
    })

    report = audit_raster_package(str(source), str(output))

    item = report["rasters"][0]
    assert item["status"] == "REVIEW_IMAGE_NO_GEOREFERENCE"
    assert "IMAGE_NO_GEOREFERENCE" in item["review_flags"]
    assert item["crs_defined"] is False
    assert item["same_crs_requires_pairwise_verification"] is False
    assert "普通图件" in item["message"]


def test_unknown_crs_jpeg_with_world_file_remains_spatially_blocked(tmp_path, monkeypatch):
    from acceptance.raster_catalog import audit_raster_package

    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    image = source / "georeferenced.jpg"
    image.write_bytes(b"probe")
    (source / "georeferenced.jgw").write_text("world file", encoding="ascii")
    _install_fake_arcpy(monkeypatch, {
        str(image): _FakeRaster(str(image), wkid=0, sr_name="Unknown")
    })

    item = audit_raster_package(str(source), str(output))["rasters"][0]

    assert item["status"] == "BLOCKED_UNKNOWN_CRS"
    assert item["companions"] == ["georeferenced.jgw"]

