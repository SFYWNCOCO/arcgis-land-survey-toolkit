import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.remote_sensing import (
    _crs_signature,
    _mask_outside_evaluation_units,
    _raster_info,
    _same_grid,
    _scl_area_counts,
    _scl_valid_mask,
)


class _Raster:
    bandCount = 1
    meanCellWidth = 10.0
    meanCellHeight = 10.0
    width = 4
    height = 3
    noDataValue = -9999
    pixelType = "F32"
    extent = type("Extent", (), {"XMin": 0.0, "YMin": 0.0, "XMax": 40.0, "YMax": 30.0})()


class _SpatialReference:
    factoryCode = 0
    name = "Sphere ArcInfo LAEA"
    type = "Projected"

    def __init__(self, wkt):
        self.wkt = wkt

    def exportToString(self):
        return self.wkt


class _ArcPy:
    def __init__(self, spatial_reference):
        self.spatial_reference = spatial_reference

    def Exists(self, _path):
        return True

    def Raster(self, _path):
        return _Raster()

    def Describe(self, _raster):
        return type("Description", (), {"spatialReference": self.spatial_reference})()


def test_sentinel2_scl_mask_rejects_nodata_cast_shadow_cloud_and_snow_classes():
    scl = np.array([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]], dtype=np.uint8)

    valid = _scl_valid_mask(scl, nodata=0)

    assert valid.tolist() == [[False, False, False, False, True, True, True, False, False, False, False, False]]


def test_evaluation_summary_mask_excludes_pixels_outside_all_units():
    values = np.array([[0.1, -0.2, 0.3], [0.4, 0.5, -0.6]], dtype="float32")
    unit_ids = np.array([[1, 0, 2], [0, 0, 2]], dtype="int32")

    masked = _mask_outside_evaluation_units(values, unit_ids)

    assert np.isfinite(masked).sum() == 3
    assert np.nanmean(masked) == pytest.approx((0.1 + 0.3 - 0.6) / 3)
    assert np.isnan(masked[0, 1])
    assert np.isnan(masked[1, 0])


def test_evaluation_summary_mask_rejects_mismatched_grids():
    with pytest.raises(ValueError, match="评价单元栅格"):
        _mask_outside_evaluation_units(np.ones((2, 2)), np.ones((1, 2)))


def test_scl_area_counts_separate_nodata_from_cloud_classes_inside_units():
    scl = np.array([[np.nan, 4, 9], [7, 6, np.nan]], dtype="float32")
    evaluation_mask = np.array([[True, True, False], [True, False, True]])

    result = _scl_area_counts(scl, evaluation_mask)

    assert result["nodata_pixels"] == 2
    assert result["class_counts"] == {"4": 1, "7": 1}


def test_custom_wkt_raster_can_be_used_when_all_grids_have_exact_same_crs():
    wkt = 'PROJCS["Sphere ArcInfo LAEA",GEOGCS["GCS_Sphere_ARC_INFO"]]'
    arcpy = _ArcPy(_SpatialReference(wkt))

    info = _raster_info(arcpy, "custom.tif")
    other = dict(info, path="other.tif")
    _same_grid([info, other])

    assert info["wkid"] == 0
    assert info["crs_signature"] == _crs_signature(arcpy.spatial_reference)
    assert info["crs_name"] == "Sphere ArcInfo LAEA"


def test_custom_wkt_mismatch_and_unreferenced_raster_are_rejected():
    wkt = 'PROJCS["Sphere ArcInfo LAEA",GEOGCS["GCS_Sphere_ARC_INFO"]]'
    arcpy = _ArcPy(_SpatialReference(wkt))
    info = _raster_info(arcpy, "custom.tif")

    with pytest.raises(ValueError, match="坐标系定义不一致"):
        _same_grid([info, dict(info, crs_signature=("WKT", "OTHER"))])

    unknown = _SpatialReference("")
    unknown.name = "Unknown"
    with pytest.raises(ValueError, match="坐标系未知"):
        _raster_info(_ArcPy(unknown), "unknown.asc")
