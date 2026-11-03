import pytest

arcpy = pytest.importorskip("arcpy")
from acceptance import factor_overlay as F


def _polygon(x0, x1, sr):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(x0, 2550000), arcpy.Point(x1, 2550000),
        arcpy.Point(x1, 2550100), arcpy.Point(x0, 2550100)]), sr)


@pytest.fixture
def sources(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "input.gdb")
    gdb = str(tmp_path / "input.gdb")
    sr = arcpy.SpatialReference(4525)
    parcels, soil = gdb + "/parcels", gdb + "/soil"
    for name, field in (("parcels", "DKBH"), ("soil", "TZMC")):
        arcpy.management.CreateFeatureclass(gdb, name, "POLYGON", spatial_reference=sr)
        arcpy.management.AddField(gdb + "/" + name, field, "TEXT", field_length=255)
    with arcpy.da.InsertCursor(parcels, ["SHAPE@", "DKBH"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500100, sr), "P1"])
    return gdb, sr, parcels, soil


def test_overlay_splits_and_preserves_original_parcel_link(sources):
    gdb, sr, parcels, soil = sources
    with arcpy.da.InsertCursor(soil, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500050, sr), "土种A"])
        cursor.insertRow([_polygon(36500050, 36500100, sr), "土种B"])
    result = F.overlay_factors(parcels, "DKBH", gdb, {"soil_species": {"path": soil, "field": "TZMC"}})
    rows = list(arcpy.da.SearchCursor(result["output"], ["PART_ID", "SRC_DKBH", "TZMC", "SHAPE@AREA"]))
    assert len(rows) == 2
    assert len({row[0] for row in rows}) == 2
    assert {row[1] for row in rows} == {"P1"}
    assert {row[2] for row in rows} == {"土种A", "土种B"}
    assert sum(row[3] for row in rows) == pytest.approx(10000)
    assert result["geometry_check"]["status"] == "PASS"


def test_uncovered_factor_area_is_retained_as_missing(sources):
    gdb, sr, parcels, soil = sources
    with arcpy.da.InsertCursor(soil, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500050, sr), "土种A"])
    result = F.overlay_factors(parcels, "DKBH", gdb, {"soil_species": {"path": soil, "field": "TZMC"}})
    rows = list(arcpy.da.SearchCursor(result["output"], ["TZMC", "SHAPE@AREA"]))
    assert len(rows) == 2 and any(row[0] is None for row in rows)
    assert sum(row[1] for row in rows) == pytest.approx(10000)
    assert result["issues"][0]["role"] == "soil_species"


def test_conflicting_overlay_never_silently_duplicates_land(sources):
    gdb, sr, parcels, soil = sources
    with arcpy.da.InsertCursor(soil, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500070, sr), "土种A"])
        cursor.insertRow([_polygon(36500050, 36500100, sr), "土种B"])
    with pytest.raises(ValueError, match="重叠冲突"):
        F.overlay_factors(parcels, "DKBH", gdb, {"soil_species": {"path": soil, "field": "TZMC"}})


def test_landform_raw_units_are_merged_to_canonical_classes(sources):
    gdb, sr, parcels, landform = sources
    with arcpy.da.InsertCursor(landform, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500050, sr), "河漫滩"])
        cursor.insertRow([_polygon(36500050, 36500100, sr), "一级阶地"])
    result = F.overlay_factors(
        parcels, "DKBH", gdb, {"landform": {"path": landform, "field": "TZMC"}},
        terrain_mapping={"河漫滩": "平原低阶", "一级阶地": "平原低阶"})
    rows = list(arcpy.da.SearchCursor(result["output"], ["DXBW"]))
    assert {row[0] for row in rows} == {"平原低阶"}
    assert result["terrain_check"]["mapping_used"] is True
    assert result["terrain_check"]["unmapped"] == []


def test_unmapped_landform_value_is_flagged_not_guessed(sources):
    gdb, sr, parcels, landform = sources
    with arcpy.da.InsertCursor(landform, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([_polygon(36500000, 36500100, sr), "图上没见过的地貌"])
    result = F.overlay_factors(
        parcels, "DKBH", gdb, {"landform": {"path": landform, "field": "TZMC"}},
        terrain_mapping={"河漫滩": "平原低阶"})
    assert result["terrain_check"]["unmapped"] == ["图上没见过的地貌"]
    assert any(item["role"] == "landform" for item in result["issues"])
