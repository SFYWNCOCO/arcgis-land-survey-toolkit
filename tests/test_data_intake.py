from pathlib import Path
from types import SimpleNamespace

from acceptance import data_intake as D


class FakeArcPy:
    def __init__(self):
        self.env = SimpleNamespace(workspace="previous")
    def Describe(self, path):
        raster = str(path).endswith(".tif")
        return SimpleNamespace(dataType="RasterDataset" if raster else "FeatureClass", shapeType="Polygon",
            spatialReference=SimpleNamespace(name="CGCS2000", factoryCode=4525),
            extent=SimpleNamespace(XMin=1, YMin=2, XMax=3, YMax=4),
            meanCellWidth=0.5, meanCellHeight=0.5, bandCount=3)
    def ListFields(self, path):
        names = ["DKBH", "XMBH", "DLMC", "PURPOSE"] if "parcels" in str(path) else ["TZMC"]
        return [SimpleNamespace(name=name, aliasName=name, type="String") for name in names]
    class management:
        @staticmethod
        def GetCount(_path): return [9]
    class da:
        @staticmethod
        def Walk(path, **_kwargs): yield str(path), [], ["parcels"]


def test_folder_inventory_is_stable_and_gdb_is_one_source(tmp_path):
    (tmp_path / "parcels.shp").write_bytes(b"shape")
    (tmp_path / "DOM.tif").write_bytes(b"raster")
    gdb = tmp_path / "inputs.gdb"
    gdb.mkdir()
    (gdb / "hidden.gdbtable").write_bytes(b"internal")
    api = FakeArcPy()
    first = D.discover_folder(tmp_path, arcpy_module=api)
    second = D.discover_folder(tmp_path, arcpy_module=api)
    assert [item["id"] for item in first["candidates"]] == [item["id"] for item in second["candidates"]]
    assert len(first["candidates"]) == 3
    assert api.env.workspace == "previous"
    assert all("hidden.gdbtable" not in item["path"] for item in first["candidates"])
    parcel = next(item for item in first["candidates"] if item["path"].endswith("parcels.shp"))
    assert parcel["field_mapping"]["land_use"] == "DLMC"
    assert "supplement_source" in parcel["missing_factors"]
    assert "PURPOSE" not in parcel["field_mapping"].values()


def test_ambiguity_requires_explicit_role_choice(tmp_path):
    for name in ("parcels_a.shp", "parcels_b.shp", "DOM.tif"):
        (tmp_path / name).write_bytes(b"input")
    found = D.discover_folder(tmp_path, arcpy_module=FakeArcPy())
    assert found["roles"]["parcels"]["status"] == "AMBIGUOUS"
    options = found["roles"]["parcels"]["candidates"]
    selected = D.resolve_roles(found["candidates"], {"parcels": options[1]})
    assert selected["parcels"]["selected"] == options[1]
    assert selected["imagery"]["status"] == "SELECTED"


def test_map_inventory_retains_uri_query_and_selection(tmp_path):
    path = str(tmp_path / "parcels.shp")
    layer = SimpleNamespace(name="同名", longName="组/同名", URI="layer-1", dataSource=path,
        isBroken=False, isGroupLayer=False, definitionQuery="XMBH = 'A'", getSelectionSet=lambda: {3, 7})
    twin = SimpleNamespace(**dict(vars(layer), URI="layer-2", definitionQuery="", getSelectionSet=lambda: set()))
    result = D.discover_map(SimpleNamespace(listLayers=lambda: [layer, twin]), arcpy_module=FakeArcPy())
    assert len({row["id"] for row in result["candidates"]}) == 2
    assert result["candidates"][0]["selection_oids"] == [3, 7]
    assert result["candidates"][0]["definition_query"] == "XMBH = 'A'"
    assert result["roles"]["parcels"]["status"] == "AMBIGUOUS"


def test_failed_dataset_remains_visible_as_issue(tmp_path):
    (tmp_path / "broken.tif").write_bytes(b"bad")
    api = FakeArcPy()
    api.Describe = lambda _path: (_ for _ in ()).throw(RuntimeError("cannot read"))
    result = D.discover_folder(tmp_path, arcpy_module=api)
    assert result["issues"][0]["status"] == "FAIL"
    assert "broken.tif" in result["issues"][0]["path"]


def test_generated_demo_is_recognized_from_adjacent_manifest(tmp_path):
    import json
    raster = tmp_path / "overview.tif"
    raster.write_bytes(b"image")
    (tmp_path / "imagery_sources.json").write_text(json.dumps({"sources": {
        "overview": {"output": r"D:\old\project\overview.tif", "demo_only": True}}}), encoding="utf-8")
    row = D.inspect_source(str(raster), FakeArcPy())
    assert row["demo_only"] is True
    assert "演示" in row["name"]
