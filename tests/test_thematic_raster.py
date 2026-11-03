import sys

import numpy as np
import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.thematic_raster import _copy_units_for_raster, _summary_stats, validate_pair_crs


def test_copy_units_for_raster_isolates_and_restores_coordinate_environments():
    raster_sr = type("SR", (), {
        "factoryCode": 4525, "name": "CGCS2000",
        "exportToString": lambda self: 'PROJCS["CGCS2000"]',
    })()
    state = {"outputCoordinateSystem": "map-crs", "geographicTransformations": "map-transform"}
    observed = {}

    class EnvManager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            self.previous = dict(state)
            state.update(self.kwargs)

        def __exit__(self, *_exc):
            state.clear()
            state.update(self.previous)

    class Management:
        def CopyFeatures(self, source, target):
            observed["copy_env"] = dict(state)
            observed["source"] = source

        def GetCount(self, _path):
            return ["2"]

    class FakeArcPy:
        management = Management()

        @staticmethod
        def Describe(_path):
            return type("Desc", (), {"spatialReference": raster_sr})()

    FakeArcPy.EnvManager = EnvManager

    assert _copy_units_for_raster(FakeArcPy, "units", "copy", raster_sr, ("WKID", 4525), 2) == 2
    assert observed["copy_env"]["outputCoordinateSystem"] is raster_sr
    assert observed["copy_env"]["geographicTransformations"] == ""
    assert state == {"outputCoordinateSystem": "map-crs", "geographicTransformations": "map-transform"}


def test_summary_stats_excludes_nodata_and_reports_coverage():
    result = _summary_stats(np.array([[1.0, 2.0], [3.0, -9999.0]]), -9999.0)

    assert result["total_pixel_count"] == 4
    assert result["valid_pixel_count"] == 3
    assert result["nodata_pixel_count"] == 1
    assert result["valid_pixel_percent"] == 75.0
    assert result["minimum"] == 1.0
    assert result["mean"] == 2.0
    assert result["median"] == 2.0
    assert result["maximum"] == 3.0
    assert result["std"] == pytest.approx((2.0 / 3.0) ** 0.5)


def test_summary_stats_handles_no_valid_pixels_without_fake_zeroes():
    result = _summary_stats(np.array([[-9999.0, -9999.0]]), -9999.0)

    assert result["valid_pixel_count"] == 0
    assert result["nodata_pixel_count"] == 2
    assert result["valid_pixel_percent"] == 0.0
    assert result["minimum"] is None
    assert result["mean"] is None
    assert result["median"] is None
    assert result["std"] is None
    assert result["maximum"] is None


def test_validate_pair_crs_requires_exact_same_wkid_or_custom_wkt():
    raster_sr = type("SR", (), {"factoryCode": 4525, "name": "CGCS2000"})()
    units_sr = type("SR", (), {"factoryCode": 4326, "name": "WGS 84"})()

    with pytest.raises(ValueError, match="坐标系不一致"):
        validate_pair_crs(raster_sr, units_sr)


def test_validate_pair_crs_accepts_matching_custom_wkt_without_wkid():
    raster_sr = type("SR", (), {
        "factoryCode": 0, "name": "Sphere Custom",
        "exportToString": lambda self: 'PROJCS["Sphere Custom",UNIT["Meter",1.0]]',
    })()
    units_sr = type("SR", (), {
        "factoryCode": 0, "name": "Sphere Custom",
        "exportToString": lambda self: 'PROJCS["Sphere Custom", UNIT["Meter",1.0]]',
    })()

    assert validate_pair_crs(raster_sr, units_sr)[0] == "WKT"


def test_validate_pair_crs_rejects_unknown_custom_wkt():
    unknown_sr = type("SR", (), {"factoryCode": 0, "name": "Unknown"})()

    with pytest.raises(ValueError, match="未知/缺少有效定义"):
        validate_pair_crs(unknown_sr, unknown_sr)
