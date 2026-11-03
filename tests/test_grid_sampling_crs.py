from types import SimpleNamespace

import pytest

from acceptance import grid_sampling as grid


def _crs(*, name, projection="Gauss_Kruger", wkid=4524, datum=4490,
         central_meridian=108.0, false_easting=36_500_000.0, sr_type="Projected"):
    return SimpleNamespace(
        name=name,
        type=sr_type,
        factoryCode=wkid,
        projectionName=projection,
        centralMeridian=central_meridian,
        falseEasting=false_easting,
        linearUnitName="Meter",
        metersPerUnit=1.0,
        GCS=SimpleNamespace(factoryCode=datum, datumName="D_China_2000"),
    )


@pytest.mark.parametrize(
    "crs,expected_wkid",
    [
        (_crs(name="CGCS2000_3_Degree_GK_Zone_36"), 4524),
        (_crs(name="CGCS2000_3_Degree_GK_CM_123E", wkid=4550,
              central_meridian=123.0, false_easting=500_000.0), 4550),
    ],
)
def test_sampling_crs_accepts_verified_china_2000_three_degree_gauss_kruger(crs, expected_wkid):
    result = grid._validate_sampling_crs(crs)

    assert result["wkid"] == expected_wkid
    assert result["zone_width_degrees"] == 3
    assert result["spacing_method"] == "GEODESIC"


@pytest.mark.parametrize(
    "crs",
    [
        _crs(name="GCS_CGCS2000", projection="", sr_type="Geographic",
             central_meridian=0, false_easting=0),
        _crs(name="WGS_1984_Web_Mercator_Auxiliary_Sphere",
             projection="Mercator_Auxiliary_Sphere", wkid=3857, datum=4326,
             central_meridian=0, false_easting=0),
        _crs(name="WGS_1984_UTM_Zone_49N", projection="Transverse_Mercator",
             wkid=32649, datum=4326, central_meridian=111, false_easting=500_000),
        _crs(name="CGCS2000_6_Degree_GK_Zone_19", central_meridian=111,
             false_easting=19_500_000),
        _crs(name="CGCS2000_3_Degree_GK_Zone_36", central_meridian=111),
        _crs(name="Custom_Gauss_Kruger", central_meridian=123,
             false_easting=500_000),
    ],
)
def test_sampling_crs_rejects_unsupported_or_inconsistent_coordinate_systems(crs):
    with pytest.raises(ValueError, match="CGCS2000.*三度高斯—克吕格"):
        grid._validate_sampling_crs(crs)


class _GeodesicPoint:
    def __init__(self, name, distances):
        self.name = name
        self.distances = distances
        self.methods = []

    def angleAndDistanceTo(self, other, method):
        self.methods.append(method)
        return 0.0, self.distances[other.name]


@pytest.mark.parametrize("distance,expected", [(14.99, False), (15.0, True), (15.01, True)])
def test_spacing_threshold_uses_ground_distance_in_meters(distance, expected):
    a = _GeodesicPoint("a", {"b": distance})
    b = _GeodesicPoint("b", {"a": distance})

    assert grid._spacing_is_ok([{"pt": a}, {"pt": b}], 15.0) is expected
    assert a.methods == ["GEODESIC"]
