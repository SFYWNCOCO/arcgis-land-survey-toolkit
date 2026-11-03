import sys
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.sampling_qa import (
    _resolve_fields,
    _reject_filtered_layer,
    _write_json_new,
    check_bulk_density_design_records,
    check_composite_design_records,
)


class Point:
    def __init__(self, x, y=0):
        self.x = x
        self.y = y

    def distanceTo(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


class Parcel:
    def __init__(self, xmin, xmax):
        self.xmin = xmin
        self.xmax = xmax

    def contains(self, point):
        return self.xmin <= point.x <= self.xmax


class EvaluationUnit:
    def __init__(self, xmin=-10, xmax=10, ymin=-10, ymax=10):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    def contains(self, point):
        if hasattr(point, "xmin"):
            return self.xmin <= point.xmin and point.xmax <= self.xmax
        return self.xmin < point.x < self.xmax and self.ymin < point.y < self.ymax


def test_resolve_fields_is_case_insensitive_and_preserves_source_names():
    class FakeArcPy:
        @staticmethod
        def ListFields(_dataset):
            return [SimpleNamespace(name="PJDYBH"), SimpleNamespace(name="DKBH")]

    assert _resolve_fields(FakeArcPy, "layer", ["pjdybh", "dkbh"]) == ["PJDYBH", "DKBH"]


def test_resolve_fields_rejects_missing_field():
    class FakeArcPy:
        @staticmethod
        def ListFields(_dataset):
            return [SimpleNamespace(name="PJDYBH")]

    with pytest.raises(ValueError, match="缺少字段：DKBH"):
        _resolve_fields(FakeArcPy, "layer", ["DKBH"])


def parcel(unit="U1", parcel_id="P1", geometry=None):
    return {"unit_id": unit, "parcel_id": parcel_id,
            "geometry": geometry or Parcel(0, 100)}


def point(point_id, x, unit="U1", parcel_id="P1"):
    return {"unit_id": unit, "parcel_id": parcel_id,
            "point_id": point_id, "sample_id": unit, "geometry": Point(x)}


def test_valid_design_meets_hard_minimums_but_keeps_manual_review_status():
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50)], 1.0
    )

    assert result["status"] == "NEEDS_REVIEW"
    assert result["unit_checks"][0]["generated"] == 3
    assert result["parcel_checks"][0]["generated"] == 3
    assert result["spacing_violations"] == []
    assert result["manual_review_required"]


def test_design_point_count_below_unit_minimum_is_a_hard_failure():
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30)], 1.0
    )

    assert result["status"] == "FAIL"
    assert result["unit_checks"][0]["generated"] == 2
    assert result["unit_checks"][0]["status"] == "FAIL"


def test_point_outside_its_associated_parcel_is_a_hard_failure():
    result = check_composite_design_records(
        [parcel(geometry=Parcel(0, 20))],
        [point("A", 10), point("B", 30), point("C", 50)], 1.0,
    )

    assert result["status"] == "FAIL"
    assert result["spatial_relation_failures"] == [
        {"point_id": "B", "unit_id": "U1", "parcel_id": "P1"},
        {"point_id": "C", "unit_id": "U1", "parcel_id": "P1"},
    ]


def test_short_spacing_is_reported_for_review_due_to_small_parcel_exception():
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 12), point("C", 30)], 1.0
    )

    assert result["status"] == "NEEDS_REVIEW"
    assert result["spacing_violations"] == [{
        "unit_id": "U1", "point_id_a": "A", "point_id_b": "B",
        "parcel_id_a": "P1", "parcel_id_b": "P1",
        "distance_m": 2.0, "required_m": 15.0,
    }]
    assert result["spacing_status"] == "NEEDS_REVIEW"
    assert len(result["spacing_exception_review"]) == 1
    assert result["spacing_exception_review"][0]["status"] == "NEEDS_REVIEW"
    assert result["spacing_exception_review"][0]["reviewer"] is None


def test_complete_spacing_exception_record_is_documented_but_not_automatically_approved():
    review = [{
        "unit_id": "U1", "point_id_a": "A", "point_id_b": "B",
        "parcel_id_a": "P1", "parcel_id_b": "P1",
        "small_parcel_evidence": "地块宽度及现场障碍照片见附件F-03",
        "whole_parcel_coverage_confirmed": True, "edge_avoidance_confirmed": True,
        "reviewer": "张某", "review_date": "2026-10-20", "evidence_reference": "附件F-03",
    }]
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 12), point("C", 30)], 1.0,
        spacing_exception_reviews=review,
    )

    assert result["status"] == "NEEDS_REVIEW"
    assert result["spacing_status"] == "NEEDS_REVIEW"
    assert result["spacing_exception_review"][0]["status"] == "DOCUMENTED_PENDING_JUDGMENT"
    assert result["unmatched_spacing_exception_reviews"] == []


def test_misdated_or_unmatched_spacing_exception_evidence_cannot_be_applied():
    review = [{
        "unit_id": "U1", "point_id_a": "A", "point_id_b": "B",
        "parcel_id_a": "P1", "parcel_id_b": "P1",
        "small_parcel_evidence": "现场记录", "whole_parcel_coverage_confirmed": True,
        "edge_avoidance_confirmed": True, "reviewer": "张某", "review_date": "20/10/2026",
        "evidence_reference": "附件F-03",
    }]
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 12), point("C", 30)], 1.0,
        spacing_exception_reviews=review,
    )
    assert result["spacing_exception_review"][0]["status"] == "NEEDS_REVIEW"

    unmatched = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50)], 1.0,
        spacing_exception_reviews=review,
    )
    assert unmatched["status"] == "FAIL"
    assert unmatched["unmatched_spacing_exception_reviews"] == [
        {"unit_id": "U1", "point_id_a": "A", "parcel_id_a": "P1",
         "point_id_b": "B", "parcel_id_b": "P1"}
    ]


def test_every_parcel_requires_one_point_even_when_unit_total_is_met():
    result = check_composite_design_records(
        [parcel(parcel_id="P1"), parcel(parcel_id="P2", geometry=Parcel(100, 200))],
        [point("A", 10), point("B", 30), point("C", 50)], 1.0,
    )

    assert result["status"] == "FAIL"
    assert result["parcel_checks"][1]["generated"] == 0
    assert result["parcel_checks"][1]["status"] == "FAIL"


def test_mixed_sample_number_must_equal_evaluation_unit_id():
    points = [point("A", 10), point("B", 30), point("C", 50)]
    points[0]["sample_id"] = "WRONG"
    result = check_composite_design_records([parcel()], points, 1.0)

    assert result["status"] == "FAIL"
    assert result["sample_id_violations"] == [{
        "point_id": "A", "unit_id": "U1", "actual": "WRONG", "required": "U1",
    }]


def test_invalid_or_unlinked_points_do_not_increment_coverage_counts():
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50, parcel_id="MISSING")], 1.0
    )

    assert result["status"] == "FAIL"
    assert result["raw_point_count"] == 3
    assert result["valid_linked_point_count"] == 2
    assert result["unit_checks"][0]["generated"] == 2


def test_parcel_and_candidate_points_must_match_evaluation_unit_geometry():
    units = [{"unit_id": "U1", "geometry": EvaluationUnit(-10, 200)}]
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50)], 1.0, units
    )
    assert result["status"] == "NEEDS_REVIEW"
    assert result["parcel_unit_mismatches"] == []
    assert result["unit_containment_failures"] == []


def test_evaluation_unit_without_valid_composite_points_is_checked():
    units = [
        {"unit_id": "U1", "geometry": EvaluationUnit(-10, 200)},
        {"unit_id": "U2", "geometry": EvaluationUnit(-10, 200)},
    ]
    result = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50)], 1.0, units
    )

    u2 = next(row for row in result["unit_checks"] if row["unit_id"] == "U2")
    assert result["status"] == "FAIL"
    assert u2["generated"] == 0
    assert u2["status"] == "FAIL"


def test_duplicate_or_unlinked_point_identifiers_are_rejected():
    duplicate = check_composite_design_records(
        [parcel()], [point("A", 10), point("A", 30), point("C", 50)], 1.0
    )
    unlinked = check_composite_design_records(
        [parcel()], [point("A", 10), point("B", 30), point("C", 50, parcel_id="P404")], 1.0
    )

    assert duplicate["status"] == "FAIL"
    assert duplicate["duplicate_point_ids"] == ["A"]
    assert duplicate["unit_checks"][0]["generated"] == 1
    assert duplicate["parcel_checks"][0]["generated"] == 1
    assert duplicate["valid_linked_point_count"] == 1
    assert duplicate["spatially_linked_point_count"] == 3
    assert unlinked["status"] == "FAIL"
    assert unlinked["unlinked_points"] == [
        {"point_id": "C", "unit_id": "U1", "parcel_id": "P404"}
    ]


def test_wrong_sample_id_is_excluded_from_coverage_and_spacing_counts():
    points = [point("A", 10), point("B", 30), point("C", 50)]
    points[0]["sample_id"] = "WRONG"
    result = check_composite_design_records([parcel()], points, 1.0)

    assert result["unit_checks"][0]["generated"] == 2
    assert result["parcel_checks"][0]["generated"] == 2
    assert result["valid_linked_point_count"] == 2
    assert result["spatially_linked_point_count"] == 3


def test_report_writer_never_deletes_or_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "qa.json"
    target.write_text("keep this", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _write_json_new(target, {"status": "PASS"})

    assert target.read_text(encoding="utf-8") == "keep this"


def test_sampling_qa_rejects_selected_feature_layers_to_prevent_partial_checks():
    with pytest.raises(ValueError, match="存在选择集"):
        _reject_filtered_layer(SimpleNamespace(FIDSet="1;3", whereClause=""), "设计点")


def test_sampling_qa_allows_unselected_layer_with_empty_fidset_text():
    _reject_filtered_layer(SimpleNamespace(FIDSet="", whereClause=""), "设计点")


def test_sampling_qa_rejects_applied_empty_selection():
    with pytest.raises(ValueError, match="存在选择集"):
        _reject_filtered_layer(SimpleNamespace(FIDSet="", whereClause=""), "设计点", selection_state=[])


def test_sampling_qa_rejects_definition_queries_to_prevent_partial_checks():
    with pytest.raises(ValueError, match="存在定义查询"):
        _reject_filtered_layer(SimpleNamespace(FIDSet=None, whereClause="UNIT_ID = 'U1'"), "设计点")


def test_composite_sampling_qa_fails_when_both_inputs_are_empty():
    result = check_composite_design_records([], [], 1.0)

    assert result["status"] == "FAIL"
    assert result["empty_inputs"] == ["parcels", "design_points"]


def test_bulk_density_qa_fails_when_both_inputs_are_empty():
    result = check_bulk_density_design_records([], [], 1.0)

    assert result["status"] == "FAIL"
    assert result["empty_inputs"] == ["profiles", "design_points"]


def _density_records(offsets=(0.0, 120.0, 240.0), radius=2 / (3 ** 0.5)):
    profiles = [{"unit_id": "U1", "geometry": Point(0)}]
    samples = []
    for index, angle in enumerate(offsets, 1):
        radians = angle * 3.141592653589793 / 180
        samples.append({
            "unit_id": "U1",
            "sample_id": "RZU1",
            "replicate_no": index,
            "geometry": Point(radius * math.cos(radians), radius * math.sin(radians)),
        })
    return profiles, samples


def test_bulk_density_design_checks_three_replicates_and_2m_geometry():
    profiles, samples = _density_records()

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["unit_checks"] == [{"unit_id": "U1", "required": 3, "generated": 3, "status": "PASS"}]
    assert result["profile_distance_violations"] == []
    assert result["spacing_deviations"] == []


def test_bulk_density_qa_checks_profiles_and_candidates_stay_in_their_unit():
    profiles, samples = _density_records()
    units = [{"unit_id": "U1", "geometry": EvaluationUnit()}]
    inside = check_bulk_density_design_records(profiles, samples, 1.0, evaluation_units=units)
    assert inside["unit_containment_failures"] == []

    units[0]["geometry"] = EvaluationUnit(-0.5, 0.5, -0.5, 0.5)
    outside = check_bulk_density_design_records(profiles, samples, 1.0, evaluation_units=units)
    assert outside["status"] == "FAIL"
    assert outside["unit_containment_failures"]


def test_bulk_density_design_fails_when_replicate_is_over_2m_from_profile():
    profiles, samples = _density_records()
    samples[0]["geometry"] = Point(2.01)

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "FAIL"
    assert result["profile_distance_violations"] == [{
        "unit_id": "U1", "replicate_no": 1, "distance_m": 2.01, "maximum_m": 2.0,
    }]


def test_bulk_density_design_reports_non_2m_parallel_spacing_for_review():
    profiles, samples = _density_records()
    samples[2]["geometry"] = Point(0, 1.0)

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["spacing_deviations"]
    assert result["spacing_status"] == "NEEDS_REVIEW"


def test_bulk_density_design_ignores_submillimeter_numeric_distance_noise():
    profiles, samples = _density_records()
    samples[2]["geometry"] = Point(samples[2]["geometry"].x + 0.00005,
                                   samples[2]["geometry"].y)

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["spacing_deviations"] == []
    assert result["spacing_status"] == "PASS"


def test_bulk_density_design_fails_for_duplicate_replicate_number():
    profiles, samples = _density_records()
    samples[2]["replicate_no"] = 2

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "FAIL"
    assert result["duplicate_replicates"] == [{"unit_id": "U1", "replicate_no": 2}]


def test_bulk_density_design_rejects_fractional_replicate_numbers():
    profiles, samples = _density_records()
    for sample, replicate in zip(samples, (1.2, 2.4, 3.9)):
        sample["replicate_no"] = replicate

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "FAIL"
    assert [row["replicate_no"] for row in result["invalid_samples"]] == [1.2, 2.4, 3.9]


def test_bulk_density_design_fails_when_sample_id_does_not_match_rule():
    profiles, samples = _density_records()
    samples[1]["sample_id"] = "WRONG"

    result = check_bulk_density_design_records(profiles, samples, 1.0)

    assert result["status"] == "FAIL"
    assert result["sample_id_violations"] == [{
        "unit_id": "U1", "replicate_no": 2, "actual": "WRONG", "required": "RZU1",
    }]
