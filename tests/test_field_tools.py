"""外业采集包、照片落点与回收质检的纯 Python 用例。"""
import csv
import sys
from pathlib import Path

from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from field_return_qa import run as qa_run  # noqa: E402
from photo_geotag import read_photo  # noqa: E402
from photo_geotag import run as geotag_run


def dms(value):
    value = abs(value)
    deg = int(value)
    minute = int((value - deg) * 60)
    return (IFDRational(deg), IFDRational(minute), IFDRational(round((value * 3600) % 60, 4)))


def make_photo(path, lon=None, lat=None, when="2026:10:24 09:00:00"):
    exif = Image.Exif()
    exif[ExifTags.Base.DateTimeOriginal] = when
    if lon is not None and lat is not None:
        gps_tag = getattr(getattr(ExifTags, "IFD", None), "GPSInfo", 0x8825)
        exif[gps_tag] = {1: "N", 2: dms(lat), 3: "E", 4: dms(lon)}
    Image.new("RGB", (16, 16), (100, 140, 80)).save(path, exif=exif)


def write_design(path, rows):
    columns = ["POINT_ID", "SAMPLE_ID", "PJDYBH", "X", "Y", "LON", "LAT"]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_photo_is_matched_to_nearest_design_point(tmp_path, monkeypatch):
    write_design(tmp_path / "design.csv", [{
        "POINT_ID": "P-01", "SAMPLE_ID": "F99000120260000E0001", "PJDYBH": "E1",
        "X": 100, "Y": 200, "LON": 106.5, "LAT": 23.5}])
    make_photo(tmp_path / "a.jpg")
    real_open = Image.open

    class FakeExif(dict):
        def get_ifd(self, tag):
            assert tag == 0x8825
            return {1: "N", 2: dms(23.5), 3: "E", 4: dms(106.5)}

    class FakeImage:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getexif(self):
            return FakeExif({ExifTags.Base.DateTimeOriginal: "2026:10:24 09:00:00"})

    monkeypatch.setattr(Image, "open", lambda path, *args, **kwargs:
                        FakeImage() if Path(path).name == "a.jpg" else real_open(path, *args, **kwargs))
    summary = geotag_run(tmp_path, tmp_path / "points.csv", tmp_path / "design.csv", None, 30)
    assert summary["matched"] == 1 and summary["no_gps"] == 0
    row = next(csv.DictReader(open(tmp_path / "points.csv", encoding="utf-8-sig")))
    assert row["point_id"] == "P-01" and float(row["distance_m"]) < 1


def test_photo_without_gps_is_not_guessed(tmp_path):
    make_photo(tmp_path / "b.jpg")
    record = read_photo(tmp_path / "b.jpg")
    assert record["lon"] is None and record["lat"] is None
    summary = geotag_run(tmp_path, tmp_path / "points.csv", None, None, 30)
    assert summary["no_gps"] == 1


def test_return_qa_fails_when_design_point_missing(tmp_path):
    write_design(tmp_path / "design.csv", [
        {"POINT_ID": "P-01", "SAMPLE_ID": "F99000120260000E0001", "PJDYBH": "E1",
         "X": 0, "Y": 0, "LON": 106.5, "LAT": 23.5},
        {"POINT_ID": "P-02", "SAMPLE_ID": "F99000120260000E0001", "PJDYBH": "E1",
         "X": 0, "Y": 0, "LON": 106.5, "LAT": 23.5}])
    (tmp_path / "returned.csv").write_text(
        "POINT_ID,LON,LAT,PHOTO_COUNT\nP-01,106.5,23.5,1\n", encoding="utf-8")
    report = qa_run(tmp_path / "design.csv", tmp_path / "returned.csv", tmp_path / "qa.json")
    assert report["status"] == "FAIL" and report["missing"] == ["P-02"]


def test_return_qa_flags_offset_and_keeps_shared_sample_ids(tmp_path):
    design = tmp_path / "design.csv"
    returned = tmp_path / "returned.csv"
    write_design(design, [
        {"POINT_ID": "P-01", "SAMPLE_ID": "F99000120260000E0001", "PJDYBH": "E1",
         "X": 0, "Y": 0, "LON": 106.5, "LAT": 23.5},
        {"POINT_ID": "P-02", "SAMPLE_ID": "F99000120260000E0001", "PJDYBH": "E1",
         "X": 0, "Y": 0, "LON": 106.5, "LAT": 23.5}])
    returned.write_text("POINT_ID,LON,LAT\nP-01,106.5003,23.5\nP-02,106.5,23.5\n", encoding="utf-8")
    report = qa_run(design, returned, tmp_path / "qa.json")
    assert report["duplicate_sample_ids"] == []
    assert report["offset_over_limit"]["points"] == ["P-01"]
    assert report["status"] == "NEEDS_REVIEW"


def test_photo_categories_are_classified_and_missing_one_is_flagged(tmp_path):
    from field_return_qa import classify_photo, scan_photo_categories
    assert classify_photo("景观照/东.jpg") == "景观照"
    assert classify_photo("工作照/IMG_1.jpg") == "工作照"
    assert classify_photo("未知/IMG_2.jpg") == "未分类"
    folders = ["景观照", "工作照", "剖面", "混合样", "四分法", "容重", "侵入体"]
    for folder in folders:
        (tmp_path / folder).mkdir()
        make_photo(tmp_path / folder / "1.jpg")
    report = scan_photo_categories(tmp_path)
    assert report["status"] == "PASS"
    (tmp_path / "容重" / "1.jpg").unlink()
    assert scan_photo_categories(tmp_path)["status"] == "NEEDS_REVIEW"


def test_return_qa_includes_photo_category_report_when_photos_given(tmp_path):
    design = tmp_path / "design.csv"
    returned = tmp_path / "returned.csv"
    write_design(design, [{"POINT_ID": "P-01", "SAMPLE_ID": "F99000120260000E0001",
                           "PJDYBH": "E1", "X": 0, "Y": 0, "LON": 106.5, "LAT": 23.5}])
    returned.write_text("POINT_ID,LON,LAT\nP-01,106.5,23.5\n", encoding="utf-8")
    photos = tmp_path / "photos"
    (photos / "景观照").mkdir(parents=True)
    make_photo(photos / "景观照" / "东.jpg")
    report = qa_run(design, returned, tmp_path / "qa.json", photos_root=photos)
    assert report["photo_categories"]["counts"]["景观照"] == 1
    assert report["photo_categories"]["status"] == "NEEDS_REVIEW"
