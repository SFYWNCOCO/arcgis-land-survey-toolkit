"""质地构型调查点选点口径；需在 ArcGIS Pro Python 下运行。

细则 8.1.3.5：在每个评价单元内选择面积最大的图斑，在 7.2 布设的点位中
选择最靠近该图斑中心位置的点位。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

arcpy = pytest.importorskip("arcpy")

from acceptance import bulk_design as B  # noqa: E402


def _square(x, y, w, h, sr):
    ring = arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + w, y),
                        arcpy.Point(x + w, y + h), arcpy.Point(x, y + h),
                        arcpy.Point(x, y)])
    return arcpy.Polygon(ring, sr)


@pytest.fixture()
def gdb(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "t.gdb")
    return str(tmp_path / "t.gdb")


def _make_layer(gdb, name, shape_type, sr, fields, rows):
    arcpy.management.CreateFeatureclass(gdb, name, shape_type, spatial_reference=sr)
    for field, kind, length in fields:
        if kind == "TEXT":
            arcpy.management.AddField(gdb + "/" + name, field, "TEXT", field_length=length)
    with arcpy.da.InsertCursor(gdb + "/" + name, ["SHAPE@"] + [f[0] for f in fields]) as cur:
        for row in rows:
            cur.insertRow(list(row))


def test_profile_point_is_nearest_to_largest_parcel_centre(gdb):
    """最大图斑（右侧 60×100）中心在 x+70；候选 A 在单元中心(x+50)、B 在 x+70。

    旧口径“离边界最远”会选 A（距边界 50 m），8.1.3.5 应选 B（离最大图斑中心 0 m）。
    """
    sr = arcpy.SpatialReference(4524)
    base_x, base_y = 37377500, 2712500
    units = gdb + "/units"
    arcpy.management.CreateFeatureclass(gdb, "units", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(units, "PJDYBH", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(units, ["SHAPE@", "PJDYBH"]) as cur:
        cur.insertRow([_square(base_x, base_y, 100, 100, sr), "U1"])

    centers = gdb + "/centers"
    arcpy.management.CreateFeatureclass(gdb, "centers", "POINT", spatial_reference=sr)
    arcpy.management.AddField(centers, "PJDYBH", "TEXT", field_length=30)
    arcpy.management.AddField(centers, "IN_UNIT", "SHORT")
    unit_centre = arcpy.PointGeometry(arcpy.Point(base_x + 50, base_y + 50), sr)
    large_centre = arcpy.PointGeometry(arcpy.Point(base_x + 70, base_y + 50), sr)
    with arcpy.da.InsertCursor(centers, ["SHAPE@", "PJDYBH", "IN_UNIT"]) as cur:
        cur.insertRow([unit_centre, "U1", 1])
        cur.insertRow([large_centre, "U1", 1])

    parcels = gdb + "/parcels"
    arcpy.management.CreateFeatureclass(gdb, "parcels", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(parcels, "PJDYBH", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(parcels, ["SHAPE@", "PJDYBH"]) as cur:
        cur.insertRow([_square(base_x, base_y, 40, 100, sr), "U1"])        # 小图斑
        cur.insertRow([_square(base_x + 40, base_y, 60, 100, sr), "U1"])   # 最大图斑，中心 x+70

    result = B.prepare_bulk_candidates(units, "PJDYBH", centers, gdb, parcel_layer=parcels)
    with arcpy.da.SearchCursor(result["profiles"], ["SHAPE@XY"]) as cur:
        (x, y), = cur.next()
    assert abs(x - (base_x + 70)) < 1e-6 and abs(y - (base_y + 50)) < 1e-6, (x, y)
