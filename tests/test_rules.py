"""50 亩口径唯一规则源：新划分与复核场景分级不同。"""
from __future__ import annotations

from acceptance import rules


def test_design_over_limit_is_fail_review_over_limit_is_review():
    assert rules.unit_area_status(50, context="design") == "PASS"
    assert rules.unit_area_status(50.1, context="design") == "FAIL"
    assert rules.unit_area_status(50.1, context="review") == "NEEDS_REVIEW"


def test_invalid_area_is_unverified_not_pass():
    assert rules.unit_area_status(None) == "UNVERIFIED"
    assert rules.unit_area_status("abc") == "UNVERIFIED"
