import sys
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import ingest
from acceptance.ingest import _cad_feature_class_summary
from acceptance.crs import parse_wkid


def _fake_arcpy(feature_classes, counts):
    def walk(_dataset, datatype=None):
        assert datatype == "FeatureClass"
        yield r"C:\project.gdb\CAD_Imported", [], feature_classes

    return SimpleNamespace(
        da=SimpleNamespace(Walk=walk),
        Describe=lambda path: SimpleNamespace(shapeType="Polyline"),
        management=SimpleNamespace(GetCount=lambda path: [str(counts[Path(path).name])]),
    )


def test_empty_cad_conversion_is_explicitly_marked_unusable():
    feature_classes, count, status, reason = _cad_feature_class_summary(
        _fake_arcpy(["Polyline"], {"Polyline": 0}), r"C:\project.gdb\CAD_Imported"
    )

    assert len(feature_classes) == 1
    assert count == 0
    assert status == "EMPTY_NEEDS_REVIEW"
    assert "不得作为有效成果使用" in reason


def test_cad_conversion_reports_per_feature_class_counts():
    feature_classes, count, status, reason = _cad_feature_class_summary(
        _fake_arcpy(["Polyline", "Annotation"], {"Polyline": 7, "Annotation": 2}),
        r"C:\project.gdb\CAD_Imported",
    )

    assert [item["count"] for item in feature_classes] == [7, 2]
    assert count == 9
    assert status == "NEEDS_REVIEW"
    assert reason is None


def test_intake_source_fingerprint_captures_exact_file_bytes(tmp_path):
    source = tmp_path / "rtk.csv"
    source.write_bytes(b"X,Y,Z\n1,2,3\n")

    fingerprint_fn = getattr(ingest, "_file_fingerprint", None)
    assert callable(fingerprint_fn), "ingest must provide source fingerprinting"
    fingerprint = fingerprint_fn(source)

    assert fingerprint["size_bytes"] == 12
    assert fingerprint["sha256"] == "0829be53fc89f54b73b245cc5b23a5cb4bde0b9edc41ddebdbdd704c3b43f222"
    assert fingerprint["path"] == str(source.resolve())
    assert fingerprint["modified_utc"].endswith("+00:00")


def test_rtk_elevation_with_z_field_is_never_claimed_as_verified():
    status_fn = getattr(ingest, "_rtk_elevation_status", None)
    assert callable(status_fn), "ingest must expose explicit RTK vertical-reference status"
    assert status_fn("Elevation") == {
        "z_field": "Elevation",
        "vertical_reference_status": "UNVERIFIED",
        "vertical_reference": None,
    }


def test_rtk_elevation_without_z_field_is_marked_not_provided():
    status_fn = getattr(ingest, "_rtk_elevation_status", None)
    assert callable(status_fn), "ingest must expose explicit RTK vertical-reference status"
    assert status_fn(None) == {
        "z_field": None,
        "vertical_reference_status": "NOT_PROVIDED",
        "vertical_reference": None,
    }


def test_source_change_during_import_is_detected(tmp_path):
    source = tmp_path / "rtk.csv"
    source.write_bytes(b"X,Y\n1,2\n")
    initial = ingest._file_fingerprint(source)
    source.write_bytes(b"X,Y\n9,9\n")

    verify_fn = getattr(ingest, "_verify_file_unchanged", None)
    assert callable(verify_fn), "ingest must detect source mutation during import"
    with pytest.raises(RuntimeError, match="内容发生变化"):
        verify_fn(initial, source)


def test_source_file_fingerprints_include_shapefile_sidecars(tmp_path):
    shp = tmp_path / "parcel.shp"
    dbf = tmp_path / "parcel.dbf"
    shp.write_bytes(b"geometry")
    dbf.write_bytes(b"attributes")

    fingerprints = ingest._source_file_fingerprints(shp)

    assert set(fingerprints) == {str(shp.resolve()), str(dbf.resolve())}


def test_geodatabase_feature_class_is_left_to_arcpy_fingerprinting():
    assert ingest._source_file_fingerprints(r"Z:\project.gdb\parcels") == {}


def test_filtered_feature_layer_is_rejected_before_import():
    fake_arcpy = SimpleNamespace(Describe=lambda _path: SimpleNamespace(
        dataType="FeatureLayer", FIDSet="1;4;7", whereClause="", definitionQuery="",
    ))

    with pytest.raises(ValueError, match="选择集或定义查询"):
        ingest._validate_vector_layer(fake_arcpy, "selected_layer")


def test_rollback_removes_partial_raw_and_projected_outputs():
    existing = {"raw", "projected"}
    arcpy = SimpleNamespace(
        Exists=lambda path: path in existing,
        management=SimpleNamespace(Delete=lambda path: existing.remove(path)),
    )
    rollback = getattr(ingest, "_rollback_outputs", None)
    assert callable(rollback), "ingest must provide rollback for every reserved output path"

    failures = rollback(arcpy, ["projected", "raw"])

    assert failures == []
    assert existing == set()


def test_cad_summary_failure_rolls_back_created_dataset(tmp_path, monkeypatch):
    source = tmp_path / "project.dxf"
    source.write_text("synthetic CAD", encoding="utf-8")
    gdb = str(tmp_path / "target.gdb")
    output = str(Path(gdb) / "CAD_Imported")
    existing = {gdb}

    def cad_to_gdb(_source, _gdb, _dataset, _scale, _sr):
        existing.add(output)

    fake_arcpy = SimpleNamespace(
        Exists=lambda path: str(path) in existing,
        SpatialReference=lambda wkid: SimpleNamespace(name="Synthetic projected CRS", factoryCode=wkid),
        conversion=SimpleNamespace(CADToGeodatabase=cad_to_gdb),
        da=SimpleNamespace(Walk=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("summary failed"))),
        management=SimpleNamespace(Delete=lambda path: existing.remove(str(path))),
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake_arcpy)

    with pytest.raises(RuntimeError, match="summary failed"):
        ingest.import_cad(str(source), gdb, "CAD_Imported", 4525)

    assert output not in existing


def test_rtk_projection_failure_rolls_back_partially_created_target(tmp_path, monkeypatch):
    source = tmp_path / "rtk.csv"
    source.write_text("X,Y\n37500000,2500000\n", encoding="utf-8")
    gdb = str(tmp_path / "target.gdb")
    name = "RTK"
    raw_path = str(Path(gdb) / (name + "_RTK_RAW"))
    projected_path = str(Path(gdb) / (name + "_RTK_PROJECTED"))
    existing = {gdb}
    rows = {}

    class _Environment:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Cursor:
        def __enter__(self):
            return iter([(SimpleNamespace(isEmpty=False),)])

        def __exit__(self, *_args):
            return False

    def spatial_reference(wkid):
        return SimpleNamespace(name="Synthetic projected CRS", factoryCode=wkid, type="Projected")

    def xy_to_point(_table, output, *_args):
        existing.add(output)
        rows[output] = 1

    def project(_source, output, *_args):
        existing.add(output)  # ArcPy may create the target before raising.
        raise RuntimeError("simulated projection failure")

    fake_arcpy = SimpleNamespace(
        Exists=lambda path: str(path) in existing,
        SpatialReference=spatial_reference,
        EnvManager=lambda **_kwargs: nullcontext(),
        ListTransformations=lambda *_args: [],
        Describe=lambda _path: SimpleNamespace(spatialReference=spatial_reference(4525)),
        management=SimpleNamespace(
            XYTableToPoint=xy_to_point,
            GetCount=lambda path: [str(rows[path])],
            Project=project,
            Delete=lambda path: existing.remove(str(path)),
        ),
        da=SimpleNamespace(SearchCursor=lambda *_args: _Cursor()),
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake_arcpy)

    with pytest.raises(RuntimeError, match="simulated projection failure"):
        ingest.import_rtk_csv(str(source), gdb, name, "X", "Y", 4525, 4524)

    assert raw_path not in existing
    assert projected_path not in existing


def test_rtk_parser_and_arcpy_can_share_an_immutable_csv_snapshot(tmp_path):
    source = tmp_path / "rtk.csv"
    source.write_bytes(b"X,Y,Z\n1,2,3\n")

    snapshot_fn = getattr(ingest, "_create_rtk_snapshot", None)
    assert callable(snapshot_fn), "RTK parser and ArcPy must consume the same source snapshot"
    snapshot, fingerprint = snapshot_fn(source, tmp_path / "project.gdb")
    try:
        assert Path(snapshot).read_bytes() == source.read_bytes()
        assert Path(snapshot).parent == tmp_path / "temp"
        assert fingerprint["sha256"] == ingest._file_fingerprint(source)["sha256"]
    finally:
        Path(snapshot).unlink(missing_ok=True)


def test_rtk_snapshot_readback_failure_removes_partial_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "rtk.csv"
    source.write_bytes(b"X,Y\n1,2\n")
    original_fingerprint = ingest._file_fingerprint

    def fail_snapshot_readback(path):
        if Path(path).name.startswith("rtk_snapshot_"):
            raise OSError("simulated snapshot readback error")
        return original_fingerprint(path)

    monkeypatch.setattr(ingest, "_file_fingerprint", fail_snapshot_readback)
    with pytest.raises(OSError, match="simulated snapshot readback error"):
        ingest._create_rtk_snapshot(source, tmp_path / "project.gdb")

    assert list((tmp_path / "temp").glob("rtk_snapshot_*.csv")) == []


def test_rtk_snapshot_cleanup_reports_os_error_without_raising(tmp_path, monkeypatch):
    snapshot = tmp_path / "rtk_snapshot_locked.csv"
    snapshot.write_text("X,Y\n1,2\n", encoding="utf-8")

    def fail_unlink(_self, missing_ok=False):
        raise PermissionError("simulated file lock")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    cleanup = getattr(ingest, "_cleanup_rtk_snapshot", None)
    assert callable(cleanup), "snapshot cleanup must be observable without masking its caller"
    warning = cleanup(snapshot)

    assert "simulated file lock" in warning


@pytest.mark.parametrize("delimiter", ["TAB", "分号", "竖线"])
def test_rtk_arcpy_import_rejects_noncomma_delimiters(tmp_path, delimiter):
    source = tmp_path / "rtk.csv"
    source.write_text("X,Y\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="标准逗号分隔"):
        ingest._validate_rtk_csv_for_arcpy(source, {"delimiter": delimiter, "encoding": "utf-8"})


def test_rtk_arcpy_import_rejects_matching_schema_ini_section(tmp_path):
    source = tmp_path / "rtk.csv"
    source.write_text("X,Y\n1,2\n", encoding="utf-8")
    (tmp_path / "schema.ini").write_text("[rtk.csv]\nFormat=CSVDelimited\nCol1=X Double\n", encoding="utf-8")

    with pytest.raises(ValueError, match="schema.ini"):
        ingest._validate_rtk_csv_for_arcpy(source, {"delimiter": "逗号", "encoding": "utf-8"})


@pytest.mark.parametrize("encoding", ["gb18030", "utf-16"])
def test_rtk_arcpy_import_rejects_unverified_text_encodings(tmp_path, encoding):
    source = tmp_path / "rtk.csv"
    source.write_text("X,Y\n1,2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="UTF-8（无BOM）"):
        ingest._validate_rtk_csv_for_arcpy(source, {"delimiter": "逗号", "encoding": encoding})


class _GeojsonEnvironment:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _geojson_arcpy(existing, feature_counts, fail_shape=None):
    shape_names = {"POINT": "Point", "MULTIPOINT": "Multipoint", "POLYLINE": "Polyline", "POLYGON": "Polygon"}
    spatial_reference = SimpleNamespace(name="WGS 1984", factoryCode=4326)
    counts = {}

    def json_to_features(_source, output, shape_code=None):
        existing.add(output)
        shape = shape_names[shape_code]
        counts[output] = feature_counts.get(shape, 0)
        if shape == fail_shape:
            raise RuntimeError("simulated second geometry conversion failure")

    def copy_features(source, output):
        existing.add(output)
        counts[output] = counts[source]

    def project(source, output, *_args):
        existing.add(output)
        counts[output] = counts[source]

    def rename(source, output, *_args):
        if output in existing:
            raise FileExistsError(output)
        existing.remove(source)
        existing.add(output)
        counts[output] = counts.pop(source)

    def delete(path):
        existing.discard(path)
        counts.pop(path, None)

    return SimpleNamespace(
        Exists=lambda path: str(path) in existing,
        EnvManager=lambda **_kwargs: _GeojsonEnvironment(),
        SpatialReference=lambda wkid: SimpleNamespace(
            name="Unknown" if int(wkid) not in (4326, 4525) else "Synthetic CRS",
            factoryCode=int(wkid),
        ),
        ListTransformations=lambda *_args: [],
        Describe=lambda _path: SimpleNamespace(spatialReference=spatial_reference),
        conversion=SimpleNamespace(JSONToFeatures=json_to_features),
        management=SimpleNamespace(
            GetCount=lambda path: [str(counts[str(path)])],
            CopyFeatures=copy_features, Project=project, Rename=rename, Delete=delete,
        ),
    )


def _write_geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return path


def _feature(geometry_type, coordinates):
    return {"type": "Feature", "properties": {}, "geometry": {"type": geometry_type, "coordinates": coordinates}}


def test_json_import_rolls_back_all_outputs_when_later_geometry_fails(tmp_path, monkeypatch):
    source = _write_geojson(tmp_path / "mixed.geojson", [
        _feature("Point", [110, 24]),
        _feature("Polygon", [[[110, 24], [110.1, 24], [110.1, 24.1], [110, 24]]]),
    ])
    gdb = str(tmp_path / "target.gdb")
    existing = {gdb}
    fake_arcpy = _geojson_arcpy(existing, {"Point": 1, "Polygon": 1}, fail_shape="Polygon")
    monkeypatch.setitem(sys.modules, "arcpy", fake_arcpy)

    with pytest.raises(RuntimeError, match="second geometry"):
        ingest.import_json_vector(str(source), gdb, "Imported", target_wkid=4326)

    assert existing == {gdb}


def test_json_import_rejects_source_output_count_mismatch_and_rolls_back(tmp_path, monkeypatch):
    source = _write_geojson(tmp_path / "points.geojson", [
        _feature("Point", [110, 24]), _feature("Point", [110.1, 24.1]),
    ])
    gdb = str(tmp_path / "target.gdb")
    existing = {gdb}
    fake_arcpy = _geojson_arcpy(existing, {"Point": 1})
    monkeypatch.setitem(sys.modules, "arcpy", fake_arcpy)

    with pytest.raises(ValueError, match="数量不一致"):
        ingest.import_json_vector(str(source), gdb, "Imported", target_wkid=4326)

    assert existing == {gdb}


@pytest.mark.parametrize("value", [True, 4525.75, 4525.0, "4525.75", 0, -1, ""])
def test_wkid_parser_rejects_boolean_fractional_or_nonpositive_values(value):
    with pytest.raises(ValueError, match="WKID"):
        parse_wkid(value, label="测试 WKID", required=True)


def test_wkid_parser_accepts_integer_and_digit_only_toolbox_text():
    assert parse_wkid(4525, required=True) == 4525
    assert parse_wkid("+4525", required=True) == 4525


def test_cad_import_rejects_fractional_wkid_before_arcpy_side_effects(tmp_path, monkeypatch):
    source = tmp_path / "plan.dxf"
    source.write_text("synthetic CAD", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "arcpy", SimpleNamespace())

    with pytest.raises(ValueError, match="正整数"):
        ingest.import_cad(str(source), str(tmp_path / "target.gdb"), "CAD", 4525.75)


@pytest.mark.parametrize("failure", ["conversion", "count_mismatch"])
def test_vector_import_rolls_back_partial_or_mismatched_output(tmp_path, monkeypatch, failure):
    source = tmp_path / "source.shp"
    source.write_text("synthetic source", encoding="utf-8")
    gdb = str(tmp_path / "target.gdb")
    output = str(Path(gdb) / "Imported")
    existing = {gdb}
    counts = {}
    desc = SimpleNamespace(spatialReference=SimpleNamespace(name="Synthetic CRS", factoryCode=4525))

    def copy_features(_source, _gdb, name):
        existing.add(output)
        counts[output] = 1
        if failure == "conversion":
            raise RuntimeError("simulated conversion failure")

    def delete(path):
        existing.discard(str(path))
        counts.pop(str(path), None)

    fake_arcpy = SimpleNamespace(
        Exists=lambda path: str(path) in existing,
        EnvManager=lambda **_kwargs: nullcontext(),
        Describe=lambda _path: desc,
        management=SimpleNamespace(
            GetCount=lambda path: [str(counts[str(path)] if failure != "count_mismatch" else 0)],
            Delete=delete,
        ),
        conversion=SimpleNamespace(FeatureClassToFeatureClass=copy_features),
    )
    monkeypatch.setattr(ingest, "inspect_vector", lambda _path: {
        "shape_type": "Polygon", "count": 2, "factory_code": 4525,
    })
    monkeypatch.setitem(sys.modules, "arcpy", fake_arcpy)

    expected_error = RuntimeError if failure == "conversion" else ValueError
    with pytest.raises(expected_error, match="conversion failure|数量不一致"):
        ingest.import_vector_copy(str(source), gdb, "Imported")

    assert existing == {gdb}
