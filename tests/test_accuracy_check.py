"""影像位置精度检核；系统 Python 3.12 可运行，不依赖 arcpy。

金丝雀：已知残差 0.3 m 的点必须算成 0.3 且判超限；全部 0 残差时 RMSE 必须为 0 而不是“缺测”。
"""
from __future__ import annotations

import json

import pytest

from acceptance import accuracy_check as A


def _write_csv(path, header, rows):
    path.write_text("﻿" + ",".join(header) + "\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def test_known_residual_0_3m_is_computed_and_flagged():
    rows = A.correspondence_residuals(
        [{"id": "C1", "x": 1000.0, "y": 2000.0}, {"id": "C2", "x": 1100.0, "y": 2000.0}],
        [{"id": "C1", "x": 1000.18, "y": 2000.24}, {"id": "C2", "x": 1100.0, "y": 2000.0}])
    assert rows[0]["residual"] == pytest.approx(0.3, abs=1e-4)
    stats = A.statistics(rows, min_check_points=2)
    assert stats["状态"] == "FAIL"
    assert stats["超限点号"] == ["C1"]
    assert stats["RMSE_m"] == pytest.approx((0.3 ** 2 / 2) ** 0.5, abs=1e-4)


def test_zero_residuals_give_zero_rmse_not_missing():
    rows = A.correspondence_residuals([{"id": "C1", "x": 1.0, "y": 2.0}], [{"id": "C1", "x": 1.0, "y": 2.0}])
    stats = A.statistics(rows)
    # 0 残差不等于 0 误差；单点样本按测绘评审判 INSUFFICIENT
    assert stats["RMSE_m"] == 0.0 and stats["状态"] == "INSUFFICIENT"


def test_no_check_points_is_untestable_not_pass():
    stats = A.statistics([])
    assert stats["状态"] == "UNTESTABLE"
    assert stats["RMSE_m"] is None


def test_unpaired_ids_raise_instead_of_silently_dropping():
    with pytest.raises(ValueError, match="未一一配对"):
        A.correspondence_residuals([{"id": "C1", "x": 0, "y": 0}, {"id": "C2", "x": 1, "y": 1}],
                                   [{"id": "C1", "x": 0, "y": 0}])
    with pytest.raises(ValueError, match="重复"):
        A.correspondence_residuals([{"id": "C1", "x": 0, "y": 0}, {"id": "C1", "x": 1, "y": 1}],
                                   [{"id": "C1", "x": 0, "y": 0}])


def test_invalid_number_is_rejected():
    with pytest.raises(ValueError, match="不是有效数字"):
        A.correspondence_residuals([{"id": "C1", "x": "", "y": 0}], [{"id": "C1", "x": 0, "y": 0}])


def test_nearest_pairing_records_distance_and_rejects_far_matches():
    rows = A.nearest_correspondence([{"id": "C1", "x": 10.0, "y": 10.0}],
                                    [{"label": "img-1", "x": 10.0, "y": 10.15}], max_distance_m=1.0)
    assert rows[0]["residual"] == pytest.approx(0.15) and rows[0]["paired_image_point"] == "img-1"
    with pytest.raises(ValueError, match="疑似采错位置"):
        A.nearest_correspondence([{"id": "C1", "x": 10.0, "y": 10.0}],
                                 [{"label": "img-1", "x": 30.0, "y": 10.0}], max_distance_m=1.0)


def test_read_csv_requires_fields(tmp_path):
    path = _write_csv(tmp_path / "p.csv", ["ID", "X"], [["C1", 1]])
    with pytest.raises(ValueError, match="缺少字段：Y"):
        A.read_csv(path, "X", "Y", "ID")


def test_render_marks_fail_for_known_0_3m_residual():
    rows = A.correspondence_residuals([{"id": "C1", "x": 0.0, "y": 0.0}], [{"id": "C1", "x": 0.3, "y": 0.0}])
    page = A.render_html(rows, A.statistics(rows, min_check_points=1), {})
    assert "<td class='FAIL'>FAIL</td>" in page
    assert "<td>0.3</td>" in page


def test_paried_by_id_rejects_max_distance_and_unknown_mode():
    with pytest.raises(ValueError, match="nearest 可带最大距离，id 不带"):
        A.build_record([{"id": "C1", "x": 0, "y": 0}], [{"id": "C1", "x": 0, "y": 0}], {}, "a.html", "a.json",
                       pairing="id", max_distance_m=5)
    with pytest.raises(ValueError, match="id 或 nearest"):
        A.build_record([], [], {}, "a.html", "a.json", pairing="auto")


def test_record_writes_html_and_json_and_refuses_overwrite(tmp_path):
    checks = _write_csv(tmp_path / "checks.csv", ["ID", "X", "Y"], [["C1", 1000, 2000], ["C2", 1010, 2000]])
    images = _write_csv(tmp_path / "images.csv", ["ID", "X", "Y"], [["C1", 1000.1, 2000.0], ["C2", 1010.0, 2000.0]])
    rows = A.correspondence_residuals(A.read_csv(checks, "X", "Y", "ID"), A.read_csv(images, "X", "Y", "ID"))
    stats = A.statistics(rows)
    out_html, out_json = tmp_path / "精度记录.html", tmp_path / "精度记录.json"
    A.write_record(rows, stats, {"影像": "<天地图>"}, out_html, out_json)
    page = out_html.read_text(encoding="utf-8")
    assert "&lt;天地图&gt;" in page and "<天地图>" not in page
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["statistics"]["检查点数"] == 2
    assert "0.1" in page
    with pytest.raises(FileExistsError):
        A.write_record(rows, stats, {}, out_html, out_json)


def test_too_few_check_points_is_insufficient_not_pass():
    """测绘评审：n=1/2 残差为 0 不能判 PASS（无统计意义）。"""
    one = A.correspondence_residuals([{"id": "C1", "x": 0, "y": 0}], [{"id": "C1", "x": 0, "y": 0}])
    assert A.statistics(one)["状态"] == "INSUFFICIENT"
    many_ok = A.correspondence_residuals(
        [{"id": "C%d" % i, "x": i * 10.0, "y": 0} for i in range(20)],
        [{"id": "C%d" % i, "x": i * 10.0, "y": 0} for i in range(20)])
    assert A.statistics(many_ok)["状态"] == "PASS"
    assert A.statistics(many_ok)["最小检查点数"] == 20


def test_nearest_pairing_must_not_reuse_one_image_point():
    """测绘评审：两个检查点不能共用同一个影像点，否则虚增样本。"""
    checks = [{"id": "C1", "x": 10.0, "y": 10.0}, {"id": "C2", "x": 10.4, "y": 10.2}]
    with pytest.raises(ValueError, match="同一影像点|重复使用"):
        A.nearest_correspondence(checks, [{"label": "img-1", "x": 10.2, "y": 10.1}])
