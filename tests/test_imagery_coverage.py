from pathlib import Path

import pytest

gdal = pytest.importorskip("osgeo.gdal")
from osgeo import osr
from acceptance import imagery as I


def _raster(tmp_path, missing=False):
    path = str(tmp_path / "coverage.tif")
    dataset = gdal.GetDriverByName("GTiff").Create(path, 100, 100, 4, gdal.GDT_Byte)
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(4525)
    dataset.SetProjection(sr.ExportToWkt())
    dataset.SetGeoTransform((36500000, 1, 0, 2550100, 0, -1))
    for index in range(1, 4): dataset.GetRasterBand(index).Fill(150)
    alpha = dataset.GetRasterBand(4)
    alpha.SetColorInterpretation(gdal.GCI_AlphaBand)
    alpha.Fill(255)
    if missing:
        import numpy as np
        alpha.WriteArray(np.zeros((20, 20), dtype="uint8"), 40, 40)
    dataset = None
    polygon = "POLYGON ((36500000 2550000,36500100 2550000,36500100 2550100,36500000 2550100,36500000 2550000))"
    return path, polygon


def test_full_coverage_still_flags_low_texture(tmp_path):
    path, polygon = _raster(tmp_path)
    result = I.inspect_raster_coverage(path, polygon)
    assert result["coverage_status"] == "PASS"
    assert result["missing_pixels"] == 0
    assert result["texture_status"] == "NEEDS_REVIEW"


def test_internal_alpha_hole_is_found_for_unit(tmp_path):
    path, polygon = _raster(tmp_path, missing=True)
    result = I.inspect_raster_coverage(path, polygon)
    assert result["coverage_status"] == "FAIL"
    assert result["missing_pixels"] == 400
    assert result["coverage_ratio"] == pytest.approx(0.96)


def test_outside_dom_part_is_a_coverage_gap(tmp_path):
    path, _polygon = _raster(tmp_path)
    polygon = "POLYGON ((36500000 2550000,36500200 2550000,36500200 2550100,36500000 2550100,36500000 2550000))"
    result = I.inspect_raster_coverage(path, polygon)
    assert result["coverage_status"] == "FAIL"
    assert result["coverage_ratio"] == pytest.approx(0.5)


def test_combined_overview_preserves_each_source_and_records_budget_resampling(tmp_path):
    path, polygon = _raster(tmp_path)
    sources = {"U1": {"output": path, "source_label": "项目DOM A", "output_pixel_size_m": 1},
               "U2": {"output": path, "source_label": "项目DOM B", "output_pixel_size_m": 1}}
    result = I.combine_unit_sources(str(tmp_path / "combined"), sources, 4525,
        [36500000, 2550000, 36500100, 2550100], max_pixels=2500)
    assert set(result["source_labels"]) == {"项目DOM A", "项目DOM B"}
    assert set(result["unit_sources"]) == {"U1", "U2"}
    assert result["output_pixel_size_m"] >= 2
    assert result["source_gsd_m"] is None
    assert result["budget_resampled"] is True
    dataset = gdal.Open(result["output"])
    assert dataset.RasterCount == 4
    assert dataset.RasterXSize * dataset.RasterYSize <= 2500
