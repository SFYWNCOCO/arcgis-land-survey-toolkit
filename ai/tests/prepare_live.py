"""Run with Pro Python to prepare a synthetic, disposable integration project."""

import json
import sys
from pathlib import Path

import arcpy


def prepare(target):
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise RuntimeError(
            "Test directory must be empty; no existing files are overwritten"
        )
    target.mkdir(parents=True, exist_ok=True)
    installation = Path(arcpy.GetInstallInfo()["InstallDir"])
    blank = (
        installation / "Resources/ArcToolBox/Services/routingservices/data/Blank.aprx"
    )
    project = arcpy.mp.ArcGISProject(str(blank))
    test_map = project.listMaps()[0]
    test_map.name = "Bridge Map"
    test_map.spatialReference = arcpy.SpatialReference(4326)
    for layer in test_map.listLayers():
        test_map.removeLayer(layer)
    gdb = str(target / "smoke.gdb")
    arcpy.management.CreateFileGDB(str(target), "smoke.gdb")
    fc = str(target / "smoke.gdb" / "bridge_points")
    arcpy.management.CreateFeatureclass(
        gdb, "bridge_points", "POINT", spatial_reference=arcpy.SpatialReference(4326)
    )
    arcpy.management.AddField(fc, "sample_id", "LONG")
    arcpy.management.AddField(fc, "group_id", "TEXT", field_length=8)
    with arcpy.da.InsertCursor(fc, ["SHAPE@XY", "sample_id", "group_id"]) as cursor:
        for i, xy in enumerate([(108.30, 22.80), (108.31, 22.81), (108.32, 22.82)], 1):
            cursor.insertRow([xy, i, "A" if i < 3 else "B"])
    clip = str(target / "smoke.gdb" / "clip_area")
    arcpy.management.CreateFeatureclass(gdb, "clip_area", "POLYGON", spatial_reference=arcpy.SpatialReference(4326))
    with arcpy.da.InsertCursor(clip, ["SHAPE@"]) as cursor:
        ring = arcpy.Array([arcpy.Point(x,y) for x,y in [(108.295,22.795),(108.315,22.795),(108.315,22.815),(108.295,22.815),(108.295,22.795)]])
        cursor.insertRow([arcpy.Polygon(ring, arcpy.SpatialReference(4326))])
    arcpy.management.Project(clip, str(target / "smoke.gdb" / "clip_projected"), arcpy.SpatialReference(3857))
    zones = str(target / "smoke.gdb" / "join_zones")
    arcpy.management.CreateFeatureclass(gdb, "join_zones", "POLYGON", spatial_reference=arcpy.SpatialReference(4326))
    arcpy.management.AddField(zones, "zone_name", "TEXT", field_length=8)
    arcpy.management.AddField(zones, "qa_id", "TEXT", field_length=8)
    arcpy.management.AddField(zones, "qa_required", "TEXT", field_length=8)
    with arcpy.da.InsertCursor(zones, ["SHAPE@", "zone_name", "qa_id", "qa_required"]) as cursor:
        for label in ["A", "B"]:
            cursor.insertRow([arcpy.Polygon(ring, arcpy.SpatialReference(4326)), label, "dup", None if label == "A" else "ok"])
    datum_fc = str(target / "smoke.gdb" / "datum_wgs")
    arcpy.management.CreateFeatureclass(gdb, "datum_wgs", "POINT", spatial_reference=arcpy.SpatialReference(4326))
    with arcpy.da.InsertCursor(datum_fc, ["SHAPE@XY"]) as cursor:
        cursor.insertRow([(-120, 38)])
    arcpy.management.CreateFeatureclass(gdb, "unknown_points", "POINT")
    arcpy.management.CreateFeatureclass(gdb, "cancel_points", "POINT", spatial_reference=arcpy.SpatialReference(4326))
    with arcpy.da.InsertCursor(str(target / "smoke.gdb" / "cancel_points"), ["SHAPE@XY"]) as cursor:
        for i in range(20000):
            cursor.insertRow([(108.30 + (i % 200)*0.0001, 22.80 + (i // 200)*0.0001)])
    test_map.addDataFromPath(fc)
    arcpy.management.CopyFeatures(fc, str(target / "points.shp"))
    test_map.listLayers()[0].saveACopy(str(target / "symbols.lyrx"))
    test_map.defaultCamera.setExtent(arcpy.Extent(108.28, 22.78, 108.34, 22.84))
    mapx = str(target / "second.mapx")
    test_map.exportToMAPX(mapx)
    project.importDocument(mapx)
    second = next(m for m in project.listMaps() if m.name != "Bridge Map")
    second.name = "Bridge Other Map"
    # A reusable repository layout is only a paper/frame template; every map reference
    # is replaced by the synthetic map before the disposable project is saved.
    template = Path(__file__).resolve().parent / "fixtures" / "BlankLayout.pagx"
    if template.is_file():
        project.importDocument(str(template))
        layout = project.listLayouts()[0]
        layout.name = "Bridge Layout"
        frame = layout.listElements("MAPFRAME_ELEMENT")[0]
        definition = layout.getDefinition("V3")
        definition.elements = [
            e
            for e in definition.elements
            if type(e).__name__ == "CIMMapFrame" and e.name == frame.name
        ]
        definition.elements[0].grids = []
        layout.setDefinition(definition)
        frame = layout.listElements("MAPFRAME_ELEMENT")[0]
        frame.map = test_map
        frame.camera.setExtent(arcpy.Extent(108.28, 22.78, 108.34, 22.84))
    project.saveACopy(str(target / "BridgeSmoke.aprx"))
    print(
        json.dumps(
            {
                "project": str(target / "BridgeSmoke.aprx"),
                "count": int(arcpy.management.GetCount(fc)[0]),
                "layouts": [layout.name for layout in project.listLayouts()],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    prepare(sys.argv[1])
