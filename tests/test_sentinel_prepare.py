import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.sentinel_prepare import (
    HTTP_MULTIRANGE_MODE,
    _make_warp_progress,
    _run_isolated_warp,
    _temporary_gdal_config,
    _warp_role,
    candidate_key,
    estimate_pixel_count,
    load_selected_groups,
    prepare_pair,
)


def _group(date, platform, href_suffix, baseline="05.13"):
    base = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/"
    assets = {
        "red": {"href": base + href_suffix + "B04.tif", "scale": 0.0001, "offset": -0.1, "nodata": 0, "gsd_m": 10},
        "nir": {"href": base + href_suffix + "B08.tif", "scale": 0.0001, "offset": -0.1, "nodata": 0, "gsd_m": 10},
        "scl": {"href": base + href_suffix + "SCL.tif", "nodata": 0, "gsd_m": 20},
    }
    item = {"item_id": "item-" + date + "-" + href_suffix, "assets": assets}
    return {
        "acquisition_date": date, "platform": platform,
        "processing_baseline": baseline, "items": [item],
        "item_ids": [item["item_id"]], "tiles": ["MGRS-49QDG"],
        "cloud_cover_min_pct": 1.0, "cloud_cover_max_pct": 1.0,
    }


def _catalog(tmp_path, before=None, after=None, complete=True, aoi_hash="aoi"):
    before = before or _group("2026-10-08", "sentinel-2c", "2026/10/08/")
    after = after or _group("2026-10-13", "sentinel-2b", "2026/10/13/")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"search_complete": complete, "aoi_sha256": aoi_hash, "groups": [before, after]}), encoding="utf-8")
    return path, before, after


def test_candidate_key_encodes_date_platform_and_baseline():
    group = _group("2026-10-08", "sentinel-2c", "x/")
    assert candidate_key(group) == "2026-10-08|sentinel-2c|05.13"


def test_s3_range_workaround_uses_serial_multirange_mode():
    worker = (ROOT / "acceptance" / "sentinel_warp_worker.py").read_text(encoding="utf-8")
    assert HTTP_MULTIRANGE_MODE == "SERIAL"
    assert '"GDAL_HTTP_MULTIRANGE": HTTP_MULTIRANGE_MODE' in worker


def test_load_groups_requires_complete_search_same_aoi_and_explicit_mixed_confirmation(tmp_path):
    path, before, after = _catalog(tmp_path)
    before_key, after_key = candidate_key(before), candidate_key(after)
    with pytest.raises(ValueError, match="平台或处理基线不同"):
        load_selected_groups(path, before_key, after_key, "aoi")
    catalog, selected_before, selected_after = load_selected_groups(
        path, before_key, after_key, "aoi", allow_platform_mismatch=True,
    )
    assert catalog["search_complete"] is True
    assert selected_before["acquisition_date"] < selected_after["acquisition_date"]

    with pytest.raises(ValueError, match="指纹不同"):
        load_selected_groups(path, before_key, after_key, "other", allow_platform_mismatch=True)
    incomplete, _, _ = _catalog(tmp_path, complete=False)
    with pytest.raises(ValueError, match="分页不完整"):
        load_selected_groups(incomplete, before_key, after_key, "aoi", allow_platform_mismatch=True)


def test_load_groups_rejects_wrong_order_same_date_and_untrusted_assets(tmp_path):
    path, before, after = _catalog(tmp_path)
    with pytest.raises(ValueError, match="前期日期必须早于"):
        load_selected_groups(path, candidate_key(after), candidate_key(before), "aoi", allow_platform_mismatch=True)
    same = _group("2026-10-08", "sentinel-2c", "2026/10/08/other/")
    same_path, first, second = _catalog(tmp_path, after=same)
    with pytest.raises(ValueError, match="不同日期"):
        load_selected_groups(same_path, candidate_key(first), candidate_key(second), "aoi")
    after["items"][0]["assets"]["nir"]["href"] = "https://evil.invalid/B08.tif"
    bad_path, first, second = _catalog(tmp_path, before=before, after=after)
    with pytest.raises(ValueError, match="不允许的资产主机"):
        load_selected_groups(bad_path, candidate_key(first), candidate_key(second), "aoi", allow_platform_mismatch=True)


def test_pixel_estimate_uses_conservative_bbox_and_validates_bounds():
    assert estimate_pixel_count((0, 0, 101, 201), 10) == 231
    with pytest.raises(ValueError, match="范围无效"):
        estimate_pixel_count((2, 0, 1, 1))


def test_gdal_network_options_are_restored_after_operation():
    class FakeGdal:
        values = {"GDAL_HTTP_TIMEOUT": "91"}

        @classmethod
        def GetConfigOption(cls, key):
            return cls.values.get(key)

        @classmethod
        def SetConfigOption(cls, key, value):
            if value is None:
                cls.values.pop(key, None)
            else:
                cls.values[key] = value

    with _temporary_gdal_config(FakeGdal, {"GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_MAX_RETRY": "2"}):
        assert FakeGdal.values["GDAL_HTTP_TIMEOUT"] == "60"
        assert FakeGdal.values["GDAL_HTTP_MAX_RETRY"] == "2"
    assert FakeGdal.values == {"GDAL_HTTP_TIMEOUT": "91"}


def test_warp_progress_reports_and_stops_at_deadline():
    current = [100.0]
    updates = []
    callback, state = _make_warp_progress(
        "after_nir", timeout_seconds=10, progress=lambda role, pct: updates.append((role, pct)),
        clock=lambda: current[0],
    )
    assert callback(0.25, "", None) == 1
    assert updates == [("after_nir", 25)]
    current[0] = 111.0
    assert callback(0.5, "", None) == 0
    assert state["timed_out"] is True


def test_warp_timeout_is_reported_as_timeout_not_empty_output():
    group = _group("2026-10-08", "sentinel-2c", "2026/10/08/")

    class FakeGdal:
        GDT_UInt16 = 2
        GDT_Float32 = 6

        @staticmethod
        def GetDataTypeName(_value):
            return "Float32"

        @staticmethod
        def WarpOptions(**kwargs):
            return kwargs

        @staticmethod
        def Warp(_output, _sources, options):
            assert options["callback"](0.1, "", None) == 0
            return None

    with pytest.raises(TimeoutError, match="超过0秒"):
        _warp_role(FakeGdal, group, "red", "unused.tif", "unused.geojson", "WKT", 10, "bilinear", timeout_seconds=0)


def test_isolated_warp_kills_stalled_worker_at_hard_deadline(tmp_path):
    calls = {"killed": False}

    class StalledWorker:
        returncode = None

        @staticmethod
        def poll():
            return None

        def kill(self):
            calls["killed"] = True
            self.returncode = -9

        @staticmethod
        def wait(timeout=None):
            return -9

    with pytest.raises(TimeoutError, match="父进程已终止" ):
        _run_isolated_warp(
            {"phase": "before", "role": "nir"}, str(tmp_path), timeout_seconds=0,
            popen_factory=lambda *args, **kwargs: StalledWorker(),
        )
    assert calls["killed"] is True


def test_isolated_warp_preserves_sanitized_failure_diagnostics(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class FailedWorker:
        returncode = 1

        @staticmethod
        def poll():
            return 1

        @staticmethod
        def wait(timeout=None):
            return 1

    def fail_with_gdal_log(command, **kwargs):
        with open(command[-1], "r", encoding="utf-8") as stream:
            task = json.load(stream)
        kwargs["stdout"].write(
            b"ERROR TIFFReadEncodedTile https://example.invalid/tile.tif?X-Amz-Signature=secret-value&part=1\n"
        )
        kwargs["stdout"].flush()
        Path(task["error_path"]).write_text(
            json.dumps({"type": "RuntimeError", "message": "GDAL tile decode failed"}),
            encoding="utf-8",
        )
        return FailedWorker()

    with pytest.raises(RuntimeError, match="诊断记录"):
        _run_isolated_warp(
            {"phase": "before", "role": "nir"}, str(run_dir), timeout_seconds=2,
            popen_factory=fail_with_gdal_log,
        )

    diagnostic_root = tmp_path / "diagnostics"
    logs = list(diagnostic_root.glob("*.log"))
    records = list(diagnostic_root.glob("*.json"))
    assert len(logs) == 1
    assert len(records) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    record_text = records[0].read_text(encoding="utf-8")
    assert "TIFFReadEncodedTile" in log_text
    assert "X-Amz-Signature=[REDACTED]" in log_text
    assert "secret-value" not in log_text + record_text
    assert list(run_dir.iterdir()) == []


def test_isolated_warp_removes_success_logs_and_returns_result(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class SuccessfulWorker:
        returncode = 0

        @staticmethod
        def poll():
            return 0

        @staticmethod
        def wait(timeout=None):
            return 0

    def finish_worker(command, **kwargs):
        with open(command[-1], "r", encoding="utf-8") as stream:
            task = json.load(stream)
        kwargs["stdout"].write(b"worker succeeded\n")
        kwargs["stdout"].flush()
        Path(task["result_path"]).write_text(json.dumps({"width": 8}), encoding="utf-8")
        return SuccessfulWorker()

    result = _run_isolated_warp(
        {"phase": "before", "role": "nir"}, str(run_dir), timeout_seconds=2,
        popen_factory=finish_worker,
    )

    assert result == {"width": 8}
    assert list(run_dir.iterdir()) == []
    assert not (tmp_path / "diagnostics").exists()


def test_prepare_pair_removes_its_run_directory_when_worker_fails(tmp_path, monkeypatch):
    import acceptance.sentinel_prepare as module
    catalog_path, before, after = _catalog(tmp_path)

    def fail_worker(*args, **kwargs):
        raise TimeoutError("synthetic worker deadline")

    monkeypatch.setattr(module, "_run_isolated_warp", fail_worker)
    output_root = tmp_path / "prepared"
    with pytest.raises(TimeoutError, match="synthetic worker deadline"):
        prepare_pair(
            str(catalog_path), candidate_key(before), candidate_key(after), "aoi",
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "TEST_WKT", str(output_root), allow_platform_mismatch=True,
        )
    assert list(output_root.iterdir()) == []
