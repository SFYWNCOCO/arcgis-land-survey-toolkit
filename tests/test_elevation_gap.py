"""数据缺口里的高程基准必须反映项目参数，不再固定写「未提供」。

背景：`workflow.py` 曾把报告 `数据缺口.高程基准` 固定为「未提供」，
与同一份报告的 `项目信息.elevation_datum`、以及图面「高程基准：1985国家高程基准」
自相矛盾。本组用例钉住新行为：有值如实写，未核实标明，无值仍写「未提供」，不臆造。
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.workflow import elevation_gap


def test_missing_value_stays_missing():
    assert elevation_gap(None) == "未提供"
    assert elevation_gap({}) == "未提供"
    assert elevation_gap({"elevation_datum": "   "}) == "未提供"


def test_value_without_status_is_reported_as_is():
    assert elevation_gap({"elevation_datum": "1985国家高程基准"}) == "1985国家高程基准"


def test_value_with_unverified_status_is_flagged():
    result = elevation_gap({
        "elevation_datum": "1985国家高程基准",
        "elevation_datum_status": "USER_SPECIFIED_UNVERIFIED",
    })
    assert "1985国家高程基准" in result
    assert "未核实" in result


def test_other_status_is_appended():
    assert elevation_gap({
        "elevation_datum": "1985国家高程基准",
        "elevation_datum_status": "MEASURED",
    }) == "1985国家高程基准（MEASURED）"
