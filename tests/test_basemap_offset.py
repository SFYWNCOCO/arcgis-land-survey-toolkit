"""偏移报告 CLI：JSON 契约、四角顺序、中心/范围两种模式与错误退出码。"""
from __future__ import annotations

import json
import sys

import pytest

from acceptance import basemap_offset

GUANGXI_EXTENT = [108.0, 22.0, 108.5, 22.5]


def test_build_report_extent_contract():
    report = basemap_offset.build_report(extent=GUANGXI_EXTENT, grid=3)
    assert report["status"] == "OK"
    assert "公开近似算法" in report["method"]
    assert {"lon", "lat", "east_m", "north_m", "distance_m"} <= set(report["center"])
    assert [corner["corner"] for corner in report["corners"]] == ["SW", "SE", "NW", "NE"]
    for corner in report["corners"]:
        assert {"corner", "lon", "lat", "east_m", "north_m", "distance_m"} <= set(corner)
    assert report["center"]["lon"] == pytest.approx(108.25)
    assert report["center"]["lat"] == pytest.approx(22.25)
    assert report["corners"][0]["lon"] == pytest.approx(108.0)
    assert report["corners"][0]["lat"] == pytest.approx(22.0)
    assert report["variation_m"] > 0
    assert report["max_offset_m"] >= 50
    assert report["samples"] == 9
    assert "不能替代控制点配准" in report["note"]
    assert "已精校正" in report["note"]


def test_centre_mode_uses_small_box_and_reports_scope():
    report = basemap_offset.build_report(center=(108.25, 22.25))
    assert report["extent"] == pytest.approx([108.2495, 22.2495, 108.2505, 22.2505])
    assert "中心模式" in report["note"]
    assert report["center"]["distance_m"] > 50
    assert report["corners"][0]["corner"] == "SW"


def test_outside_china_reports_zero_offset_instead_of_guessing():
    report = basemap_offset.build_report(center=(2.3522, 48.8566))
    assert report["inside_china"] is False
    assert report["center"]["distance_m"] == 0
    assert report["max_offset_m"] == 0 and report["variation_m"] == 0


@pytest.mark.parametrize("kwargs", [
    dict(extent=GUANGXI_EXTENT, center=(108.2, 22.2)),
    dict(),
    dict(extent=[109.0, 22.0, 108.0, 23.0]),
    dict(extent=[108.0, 22.0, 108.5]),
    dict(center=(108.2,)),
    dict(center=(200.0, 22.2)),
    dict(extent=GUANGXI_EXTENT, grid=1),
    dict(extent=GUANGXI_EXTENT, grid=99),
])
def test_invalid_input_is_rejected(kwargs):
    with pytest.raises(ValueError):
        basemap_offset.build_report(**kwargs)


def test_cli_prints_single_json_line_and_exit_zero(capsys):
    code = basemap_offset.main(["--extent", "108.0", "22.0", "108.5", "22.5", "--grid", "3"])
    assert code == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    report = json.loads(lines[-1])
    assert report["status"] == "OK"
    assert report["corners"][0]["corner"] == "SW"


def test_cli_failure_prints_json_error_and_exit_one(capsys):
    code = basemap_offset.main(["--center", "108.2"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "FAIL" and payload["error"]


def test_offset_cli_runs_without_pro_libraries(monkeypatch):
    """纯数学 CLI：禁止依赖 arcpy/gdal，运行期也不得偷偷导入。"""
    for name in ("arcpy", "osgeo", "osgeo.gdal"):
        monkeypatch.setitem(sys.modules, name, None)
    assert basemap_offset.main(["--center", "108.2", "22.2"]) == 0
