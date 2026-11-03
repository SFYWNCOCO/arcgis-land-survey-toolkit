"""ArcPy integration contracts: real geometry, negative controls and source preservation."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

arcpy = pytest.importorskip("arcpy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance.parcel_tools import audit_parcels, prepare_parcels, export_parcel_maps


def _import_toolbox(tmp_path, alias):
    # ImportToolbox creates .pyt.xml beside its input. Keep those runtime files
    # in the test sandbox rather than modifying the source directory.
    runtime = tmp_path / "toolbox_runtime"
    (runtime / "toolboxes").mkdir(parents=True)
    (runtime / "acceptance").mkdir()
    for name in ("__init__.py", "parcel_tools.py"):
        shutil.copyfile(ROOT / "acceptance" / name, runtime / "acceptance" / name)
    path = runtime / "toolboxes" / "ParcelTools.pyt"
    shutil.copyfile(ROOT / "toolboxes" / path.name, path)
    return arcpy.ImportToolbox(str(path), alias)


@pytest.fixture
def polygons(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "source.gdb")
    fc = str(tmp_path / "source.gdb" / "parcels")
    sr = arcpy.SpatialReference(3857)
    arcpy.management.CreateFeatureclass(str(tmp_path / "source.gdb"), "parcels", "POLYGON", spatial_reference=sr)
    for name, typ in [("PID", "TEXT"), ("NOTE", "TEXT"), ("AREA_M2", "DOUBLE")]:
        arcpy.management.AddField(fc, name, typ)
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "PID", "NOTE", "AREA_M2"]) as cur:
        for x, pid, area in [(10000, "A'39", 100), (10005, "A'39", 100), (20000, "B", 999)]:
            geom = arcpy.Polygon(arcpy.Array([arcpy.Point(x, 20000), arcpy.Point(x+10, 20000), arcpy.Point(x+10, 20010), arcpy.Point(x, 20010)]), sr)
            cur.insertRow((geom, pid, None, area))
    yield fc
    arcpy.management.ClearWorkspaceCache()


def test_negative_controls_duplicate_blank_overlap_area(polygons, tmp_path):
    result = audit_parcels(polygons, "PID", tmp_path / "audit", required_fields=["NOTE"], area_field="AREA_M2", area_unit="平方米", method="平面面积")
    counts = {code: sum(i["问题类型"] == code for i in result["issues"]) for code in {i["问题类型"] for i in result["issues"]}}
    assert counts["编号重复"] == 2
    assert counts["必填字段为空"] == 3
    assert counts["面间重叠"] == 2
    assert counts["面积不一致"] == 1
    assert result["anomaly_count"] == 3
    assert int(arcpy.management.GetCount(result["anomaly_fc"])[0]) == 3


def test_selection_and_non_overwrite(polygons, tmp_path):
    layer = arcpy.management.MakeFeatureLayer(polygons, "selected_parcels").getOutput(0)
    arcpy.management.SelectLayerByAttribute(layer, "NEW_SELECTION", "PID = 'B'")
    out = tmp_path / "selected"
    result = audit_parcels(layer, "PID", out, method="平面面积")
    assert result["feature_count"] == 1
    assert result["issue_count"] == 0
    original = (out / "质检报告.json").read_bytes()
    with pytest.raises(FileExistsError):
        audit_parcels(layer, "PID", out, method="平面面积")
    assert (out / "质检报告.json").read_bytes() == original
    arcpy.management.Delete(layer)


def test_numeric_zero_identifier_remains_visible_in_anomalies(polygons, tmp_path):
    arcpy.management.AddField(polygons, "CODE", "LONG")
    with arcpy.da.UpdateCursor(polygons, ["CODE"]) as cur:
        for index, row in enumerate(cur):
            row[0] = 0 if index < 2 else 1
            cur.updateRow(row)
    result = audit_parcels(polygons, "CODE", tmp_path / "numeric", method="平面面积")
    duplicate = [r for r in result["issues"] if r["问题类型"] == "编号重复"]
    assert len(duplicate) == 2 and {r["地块编号"] for r in duplicate} == {"0"}
    with arcpy.da.SearchCursor(result["anomaly_fc"], ["PARCEL_ID"]) as cur:
        assert "0" in {row[0] for row in cur}


def test_prepare_mapping_areas_and_preserves_source(polygons, tmp_path):
    before = list(arcpy.da.SearchCursor(polygons, ["PID", "AREA_M2"]))
    rule = tmp_path / "rules.json"
    rule.write_text(json.dumps({"copy": [{"source": "PID", "target": "DKBH"}], "set": [{"field": "AUTHOR", "value": "测试人员"}]}), encoding="utf-8")
    result = prepare_parcels(polygons, tmp_path / "work", rules_path=rule)
    assert result["count"] == 3
    with arcpy.da.SearchCursor(result["output"], ["PID", "DKBH", "AUTHOR", "PT_P_M2", "PT_G_M2"]) as cur:
        rows = list(cur)
    assert all(pid == copied and author == "测试人员" for pid, copied, author, _, _ in rows)
    assert all(abs(planar-100) < 0.01 and geodesic > 0 for _, _, _, planar, geodesic in rows)
    assert list(arcpy.da.SearchCursor(polygons, ["PID", "AREA_M2"])) == before
    assert "PT_P_M2" not in [f.name for f in arcpy.ListFields(polygons)]


def test_invalid_rule_rolls_back_only_output(polygons, tmp_path):
    rule = tmp_path / "rules.json"
    rule.write_text(json.dumps({"copy": [{"source": "PID", "target": "PID"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="不覆盖"):
        prepare_parcels(polygons, tmp_path / "bad", rules_path=rule)
    assert not arcpy.Exists(str(tmp_path / "bad" / "work.gdb" / "整理地块"))
    assert int(arcpy.management.GetCount(polygons)[0]) == 3


def test_copy_failure_rolls_back_partial_output(polygons, tmp_path, monkeypatch):
    copy = arcpy.management.CopyFeatures

    def partial_copy(*args, **kwargs):
        copy(*args, **kwargs)
        raise RuntimeError("injected copy failure after creating output")

    monkeypatch.setattr(arcpy.management, "CopyFeatures", partial_copy)
    with pytest.raises(RuntimeError, match="injected copy failure"):
        prepare_parcels(polygons, tmp_path / "partial")
    assert not arcpy.Exists(str(tmp_path / "partial" / "work.gdb" / "整理地块"))
    assert int(arcpy.management.GetCount(polygons)[0]) == 3


def test_unknown_crs_is_blocked(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "unknown.gdb")
    fc = str(tmp_path / "unknown.gdb" / "unknown")
    arcpy.management.CreateFeatureclass(str(tmp_path / "unknown.gdb"), "unknown", "POLYGON")
    with pytest.raises(ValueError, match="坐标系未知"):
        audit_parcels(fc, "OBJECTID", tmp_path / "not_created")
    assert not (tmp_path / "not_created").exists()


def test_real_sample_geodesic_matches_source(tmp_path):
    source = os.environ.get("PARCEL_SAMPLE")
    if not source:
        pytest.skip("Set PARCEL_SAMPLE to the user-supplied Shapefile")
    result = audit_parcels(source, "DIKUAI_NO", tmp_path / "real", area_field="DIKUAIAREA")
    assert result["feature_count"] == 9
    assert result["issue_count"] == 0
    assert len(result["source"]) >= 4


def test_dynamic_gp_boundary(polygons, tmp_path):
    toolbox = _import_toolbox(tmp_path, "parcelcontract")
    result = toolbox.ParcelAudit(polygons, "PID", str(tmp_path / "gp"), "", "AREA_M2", "平方米", "平面面积", 0.5, 0.01)
    assert result.status == 4
    assert arcpy.Exists(result.getOutput(1))
    assert arcpy.Describe(result.getOutput(1)).shapeType == "Polygon"
    report = json.loads((tmp_path / "gp" / "质检报告.json").read_text(encoding="utf-8"))
    assert report["feature_count"] == 3
    assert report["issue_count"] > 0


def test_dynamic_prepare_projection_and_geographic_areas(polygons, tmp_path):
    toolbox = _import_toolbox(tmp_path, "parcelpreparecontract")
    result = toolbox.ParcelPrepare(polygons, str(tmp_path / "projected"), arcpy.SpatialReference(4326))
    fc = result.getOutput(1)
    assert arcpy.Describe(fc).spatialReference.factoryCode == 4326
    assert int(arcpy.management.GetCount(fc)[0]) == 3
    names = [f.name for f in arcpy.ListFields(fc)]
    assert "PT_G_M2" in names and "PT_P_M2" not in names


def test_worker_map_export_honors_selection(polygons, tmp_path):
    project_path = os.environ.get("PARCEL_PROJECT")
    if not project_path:
        pytest.skip("Set PARCEL_PROJECT to an existing APRX")
    project = arcpy.mp.ArcGISProject(project_path)
    map_obj = project.listMaps()[0]
    for layer in map_obj.listLayers():
        if layer.isBasemapLayer or layer.isWebLayer:
            layer.visible = False
    selected = arcpy.management.MakeFeatureLayer(polygons, "export_one_parcel").getOutput(0)
    arcpy.management.SelectLayerByAttribute(selected, "NEW_SELECTION", "PID = 'B'")
    result = export_parcel_maps(selected, "PID", tmp_path / "pdfs", project, map_obj.name, dpi=150)
    assert result["status"] == "COMPLETE" and result["parcel_count"] == 1 and result["pdf_count"] == 2
    assert not (tmp_path / "pdfs" / "temp").exists()
    assert all(Path(p).read_bytes().startswith(b"%PDF-") for p in result["outputs"])
    arcpy.management.Delete(selected)
