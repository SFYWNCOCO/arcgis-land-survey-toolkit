"""运行摘要与检查清单：分级、下一步、聚合状态与 HTML 输出。"""
from __future__ import annotations

import json

from acceptance import triage


def _report():
    return {
        "坐标系": {"名称": "CGCS2000_3_Degree_GK_Zone_36", "wkid": 4524},
        "字段": {"图斑编号": "DIKUAI_NO"},
        "数据缺口": {"四因素缺项": [], "项目编号": "F99000220261461"},
        "编号计划": {"missing_project_units": []},
        "单元明细": [{"unit": "U1", "mu": 10.0}],
        "网格单元明细": [{"评价单元": "U1", "达标": True, "间距达标": True, "覆盖缺失图斑": []}],
        "报备影像状态": {"status": "PASS", "detail": "项目 DOM 0.2 m"},
        "外业表格状态": "PASS",
        "外业表格": {"单元数": 1, "未填项": {}},
        "待人工确认": [],
    }


def _by_item(checks, item):
    return next(row for row in checks if row["项目"] == item)


def test_all_green_report_is_pass():
    checks = triage.checks_for(_report())
    assert triage.overall(checks) == "PASS"


def test_summary_starts_with_clickable_map_previews_and_keeps_failures_visible(tmp_path):
    from pathlib import Path
    report = _report()
    image = tmp_path / "地图 #39.jpg"
    image.write_bytes(b"thumbnail fixture")
    report["图件用途"] = "设计预览"
    report["出图"] = {"status": "FAIL", "outputs": [
        {"target": "地块<script>39", "jpg": str(image), "thumbnail": str(image), "status": "NEEDS_REVIEW"},
        {"target": "地块40", "status": "FAIL", "checks": [{"detail": "取图失败"}]}]}
    html = Path(triage.write_summary(report, tmp_path)["运行摘要"]).read_text(encoding="utf-8")
    assert "<img" in html and "%23" in html
    assert "地块&lt;script&gt;39" in html and "取图失败" in html
    assert "设计预览" in html and html.index("<img") < html.index("<table")


def test_missing_crs_and_area_overflow_are_failures_with_next_step():
    report = _report()
    report["坐标系"] = {"名称": "", "wkid": 0}
    report["单元明细"][0]["mu"] = 60
    checks = triage.checks_for(report)
    assert triage.overall(checks) == "FAIL"
    crs = _by_item(checks, "坐标系")
    assert crs["级别"] == "FAIL" and crs["下一步"]
    area = _by_item(checks, "单元面积上限")
    assert area["级别"] == "FAIL" and "50" in area["证据"]


def test_missing_factor_and_project_number_are_review_not_fail():
    report = _report()
    report["数据缺口"] = {"四因素缺项": ["soil_species"], "项目编号": "未提供"}
    report["编号计划"] = {"missing_project_units": ["U1"]}
    checks = triage.checks_for(report)
    assert _by_item(checks, "四因素资料")["级别"] == "NEEDS_REVIEW"
    assert _by_item(checks, "系统项目编号")["级别"] == "NEEDS_REVIEW"
    assert triage.overall(checks) == "NEEDS_REVIEW"


def test_every_non_pass_check_has_an_action():
    report = _report()
    report["报备影像状态"] = {"status": "FAIL", "detail": "影像分辨率不足"}
    report["外业表格状态"] = "NEEDS_REVIEW"
    report["待人工确认"] = ["设计点须现场复核"]
    checks = triage.checks_for(report)
    for row in checks:
        if row["级别"] != "PASS":
            assert row["下一步"].strip(), row


def test_imagery_precision_check_uses_system_review_source_mapping():
    reference = triage.SOURCE_REFERENCES["system_imagery_precision"]
    assert reference["document_id"] == "system_review_technical_requirements"
    assert reference["physical_pages"] == (21, 62, 78)
    check = _by_item(triage.checks_for(_report()), "报备影像")
    assert check["依据"] == triage.format_source_reference("system_imagery_precision")


def test_summary_writes_json_and_html(tmp_path):
    result = triage.write_summary(_report(), tmp_path)
    assert result["运行摘要状态"] == "PASS"
    payload = json.loads((tmp_path / "检查清单.json").read_text(encoding="utf-8"))
    assert payload["总体状态"] == "PASS"
    text = (tmp_path / "本次运行摘要.html").read_text(encoding="utf-8")
    assert "border-collapse" in text
    assert "本次运行摘要" in text
