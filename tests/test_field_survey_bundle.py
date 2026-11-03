import json
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import field_survey_bundle as F
from acceptance.field_survey_bundle import inspect_field_survey_bundle


def _shapefile_header(file_length_words, xmin, ymin, xmax, ymax):
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, file_length_words)
    struct.pack_into("<i", header, 28, 1000)
    struct.pack_into("<i", header, 32, 1)
    struct.pack_into("<4d", header, 36, xmin, ymin, xmax, ymax)
    return header


def _point_shp(lon, lat):
    record = struct.pack(">2i", 1, 10) + struct.pack("<i2d", 1, lon, lat)
    return bytes(_shapefile_header((100 + len(record)) // 2, lon, lat, lon, lat) + record)


def _point_shx():
    return bytes(_shapefile_header(54, 0, 0, 0, 0) + struct.pack(">2i", 50, 10))


def _dbf(fields, rows):
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + sum(field[2] for field in fields)
    header = bytearray(32)
    header[0] = 3
    struct.pack_into("<I", header, 4, len(rows))
    struct.pack_into("<H", header, 8, header_length)
    struct.pack_into("<H", header, 10, record_length)
    descriptors = bytearray()
    for name, kind, width, decimals in fields:
        descriptor = bytearray(32)
        descriptor[:11] = name.encode("ascii")[:10].ljust(11, b"\0")
        descriptor[11] = ord(kind)
        descriptor[16] = width
        descriptor[17] = decimals
        descriptors.extend(descriptor)
    records = bytearray()
    for row in rows:
        records.extend(b" ")
        for name, kind, width, _decimals in fields:
            value = row.get(name)
            if value is None:
                raw = b""
            elif kind == "N":
                raw = str(value).encode("ascii").rjust(width)
            else:
                raw = str(value).encode("utf-8")
            if len(raw) > width:
                raise ValueError("test DBF value exceeds field width")
            records.extend(raw.ljust(width))
    return bytes(header + descriptors + b"\r" + records + b"\x1a")


def _bundle(path, *, json_properties=None, dbf_row=None, unsafe_name=None):
    lon, lat = 112.937938, 28.238099
    properties = json_properties or {
        "id": "sample-1", "name": "A1", "TBBH": "A1", "YXTCHD": 80,
        "DLTDX": "基本可通达", "JD": lon, "WD": lat,
        "lngGcj02": 112.9433867, "latGcj02": 28.2345901,
        "photoCount": 1, "photos": ["photos/a.jpg"],
    }
    row = dbf_row or {
        "id": "sample-1", "name": "A1", "TBBH": "A1", "YXTCHD": 110,
        "DLTDX": "不可通达", "JD": lon, "WD": lat,
        "photoCount": 1, "photos": '["photos/a.jpg"]',
    }
    feature_collection = {
        "type": "FeatureCollection", "name": "survey",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": properties}],
    }
    fields = [("id", "C", 20, 0), ("name", "C", 10, 0), ("TBBH", "C", 10, 0),
              ("YXTCHD", "N", 8, 0), ("DLTDX", "C", 24, 0), ("JD", "N", 14, 6),
              ("WD", "N", 14, 6), ("lngGcj02", "N", 14, 6), ("latGcj02", "N", 14, 6),
              ("photoCount", "N", 4, 0), ("photos", "C", 80, 0)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("survey.geojson", json.dumps(feature_collection))
        archive.writestr("survey_point.shp", _point_shp(lon, lat))
        archive.writestr("survey_point.shx", _point_shx())
        archive.writestr("survey_point.dbf", _dbf(fields, [row]))
        archive.writestr("survey_point.prj", 'GEOGCS["GCS_WGS_1984"]')
        archive.writestr("survey_point.cpg", "UTF-8")
        if unsafe_name:
            archive.writestr(unsafe_name, "should not be accepted")
    return path


def test_bundle_comparison_flags_conflicting_values_and_never_claims_business_pass(tmp_path):
    archive = _bundle(tmp_path / "field.zip")

    report = inspect_field_survey_bundle(archive)

    conflicts = {(item.get("field"), item.get("code")) for item in report["findings"]}
    assert report["status"] == "NEEDS_REVIEW"
    assert report["business_acceptance"] == "NOT_EVALUATED"
    assert ("YXTCHD", "ATTRIBUTE_CONFLICT") in conflicts
    assert ("DLTDX", "ATTRIBUTE_CONFLICT") in conflicts
    assert report["comparison"]["matched_records"] == 1


def test_missing_photo_reference_is_reported_without_extracting_archive(tmp_path):
    archive = _bundle(tmp_path / "field.zip")

    report = inspect_field_survey_bundle(archive)

    missing = [item for item in report["findings"] if item["code"] == "PHOTO_REFERENCE_MISSING"]
    assert [item["path"] for item in missing] == ["photos/a.jpg"]
    assert not (tmp_path / "photos" / "a.jpg").exists()


def test_crs84_geometry_and_gcj02_property_are_reported_as_distinct_coordinate_sources(tmp_path):
    archive = _bundle(tmp_path / "field.zip")

    report = inspect_field_survey_bundle(archive)

    assert report["coordinate_review"]["geojson_crs"] == "OGC:CRS84"
    assert report["coordinate_review"]["shapefile_wkid_candidate"] == 4326
    gcj = [item for item in report["findings"] if item["code"] == "ALTERNATE_GCJ02_COORDINATES"]
    assert len(gcj) == 1
    assert 600 < gcj[0]["distance_m"] < 700


def test_unsafe_zip_member_is_rejected(tmp_path):
    archive = _bundle(tmp_path / "field.zip", unsafe_name="../outside.txt")

    report = inspect_field_survey_bundle(archive)

    assert report["status"] == "FAIL"
    assert any(item["code"] == "UNSAFE_ARCHIVE_PATH" for item in report["findings"])


def test_report_write_does_not_overwrite_existing_file(tmp_path):
    archive = _bundle(tmp_path / "field.zip")
    destination = tmp_path / "report.json"
    destination.write_text("keep", encoding="utf-8")

    try:
        inspect_field_survey_bundle(archive, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing report must not be overwritten")
    assert destination.read_text(encoding="utf-8") == "keep"


def test_geometry_comparison_pairs_shapefile_shapes_by_explicit_identity():
    geo_rows = [
        {"id": "A", "_geometry": (0.0, 0.0)},
        {"id": "B", "_geometry": (10.0, 0.0)},
    ]
    dbf_rows = [{"id": "B"}, {"id": "A"}]
    shape_points = [(10.0, 0.0), (0.0, 0.0)]

    distances = F._paired_geometry_distances(geo_rows, dbf_rows, shape_points)

    assert distances == [0.0, 0.0]


def test_dbf_encoding_sidecar_is_size_limited(tmp_path, monkeypatch):
    archive = _bundle(tmp_path / "field.zip")
    monkeypatch.setattr(F, "MAX_CPG_BYTES", 4)

    report = inspect_field_survey_bundle(archive)

    assert report["status"] == "FAIL"
    assert any(item["code"] == "INVALID_SHAPEFILE" for item in report["findings"])
