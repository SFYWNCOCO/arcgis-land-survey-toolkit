"""评价单元划分集成测试：需在 ArcGIS Pro Python 下运行。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

arcpy = pytest.importorskip("arcpy")

from acceptance import unit_builder as U  # noqa: E402


def _square(x, y, w, h, sr):
    ring = arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + w, y),
                        arcpy.Point(x + w, y + h), arcpy.Point(x, y + h),
                        arcpy.Point(x, y)])
    return arcpy.Polygon(ring, sr)


@pytest.fixture()
def gdb(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "t.gdb")
    return str(tmp_path / "t.gdb")


def _parcels(gdb, sr, rows):
    fc = f"{gdb}/parcels"
    arcpy.management.CreateFeatureclass(gdb, "parcels", "POLYGON", spatial_reference=sr)
    for name in ("DIKUAI_NO", "LANDTYPE", "DXBW", "TZMC", "BCGDLY", "XMBH"):
        arcpy.management.AddField(fc, name, "TEXT", field_length=30)
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "DIKUAI_NO", "LANDTYPE", "DXBW", "TZMC", "BCGDLY", "XMBH"]) as cur:
        for pid, landtype, geom in rows:
            cur.insertRow([geom, pid, landtype, "平地", "土种A", "恢复", "F99000220261461"])
    return fc


def test_merges_touching_parcels_up_to_area_cap(gdb):
    sr = arcpy.SpatialReference(4524)
    # 两块共边、各约 12 亩，合计 24 亩 < 50 亩 → 应合并为一个单元
    fc = _parcels(gdb, sr, [
        ("A", "旱地", _square(37377500, 2712500, 90, 90, sr)),
        ("B", "旱地", _square(37377590, 2712500, 90, 90, sr)),
    ])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",), max_mu=50.0, gap_m=30.0)
    assert result["units"] == 1
    assert result["detail"][0]["图斑"] == ["A", "B"]
    assert result["detail"][0]["部件数"] == 1


def test_does_not_merge_when_over_cap(gdb):
    sr = arcpy.SpatialReference(4524)
    # 两块共边、各约 32 亩，合计 64 亩 > 50 亩 → 必须拆成两个单元
    fc = _parcels(gdb, sr, [
        ("A", "旱地", _square(37377500, 2712500, 150, 150, sr)),
        ("B", "旱地", _square(37377650, 2712500, 150, 150, sr)),
    ])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",), max_mu=50.0, gap_m=30.0)
    assert result["units"] == 2
    assert all(d["部件数"] == 1 for d in result["detail"])
    assert all(d["面积亩"] <= 50.0 for d in result["detail"])


def test_does_not_merge_non_touching_parcels_by_default(gdb):
    sr = arcpy.SpatialReference(4524)
    # 相距 5 m、面积都小，但默认不允许多部件 → 不合并，并记录候选
    fc = _parcels(gdb, sr, [
        ("A", "旱地", _square(37377500, 2712500, 40, 40, sr)),
        ("B", "旱地", _square(37377545, 2712500, 40, 40, sr)),
    ])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",), max_mu=50.0, gap_m=30.0)
    assert result["units"] == 2
    assert result["unmerged"], "应记录可合并但未合并的候选"


def test_splits_oversize_parcel(gdb):
    sr = arcpy.SpatialReference(4524)
    # 单图斑约 60 亩 > 50 亩 → 拆成两片并记入 split
    fc = _parcels(gdb, sr, [("A", "旱地", _square(37377500, 2712500, 200, 200, sr))])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",), max_mu=50.0, gap_m=30.0)
    assert result["split"] and result["split"][0]["拆分片数"] >= 2
    assert result["units"] >= 2
    assert all(d["面积亩"] <= 50.5 for d in result["detail"])


def test_different_land_types_stay_separate(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _parcels(gdb, sr, [
        ("A", "旱地", _square(37377500, 2712500, 60, 60, sr)),
        ("B", "水田", _square(37377560, 2712500, 60, 60, sr)),
    ])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",), max_mu=50.0, gap_m=30.0)
    assert result["units"] == 2


def test_missing_factor_never_merges_parcels(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _parcels(gdb, sr, [("A", "旱地", _square(37377500, 2712500, 40, 40, sr)),
                             ("B", "旱地", _square(37377540, 2712500, 40, 40, sr))])
    arcpy.management.DeleteField(fc, "TZMC")
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",))
    assert result["units"] == 2
    assert "soil_species" in result["missing_factors"]
    assert all(row["临时候选"] for row in result["detail"])


def test_project_boundary_is_always_part_of_grouping(gdb):
    sr = arcpy.SpatialReference(4524)
    fc = _parcels(gdb, sr, [("A", "旱地", _square(37377500, 2712500, 40, 40, sr)),
                             ("B", "旱地", _square(37377540, 2712500, 40, 40, sr))])
    with arcpy.da.UpdateCursor(fc, ["DIKUAI_NO", "XMBH"]) as cursor:
        for row in cursor:
            if row[0] == "B":
                cursor.updateRow(["B", "F99000220261462"])
    result = U.build_units(fc, "DIKUAI_NO", gdb, factor_fields=("LANDTYPE",))
    assert result["units"] == 2
    assert result["project_field"] == "XMBH"


def test_uneven_oversize_shape_has_no_over_cap_piece(gdb):
    sr = arcpy.SpatialReference(4524)
    triangle = arcpy.Polygon(arcpy.Array([arcpy.Point(37377500, 2712500),
        arcpy.Point(37378700, 2712500), arcpy.Point(37377500, 2712600)]), sr)
    fc = _parcels(gdb, sr, [("A", "旱地", triangle)])
    result = U.build_units(fc, "DIKUAI_NO", gdb, max_mu=50)
    assert all(row[0] <= 50.00001 for row in arcpy.da.SearchCursor(result["unit_fc"], ["PJDY_MU"]))
    assert result["geometry_check"]["status"] == "PASS"
