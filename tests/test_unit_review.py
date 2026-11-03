import pytest
from acceptance import unit_review as R


def test_manual_sampling_groups_keep_source_parcels_and_three_points_per_group():
    rows = [{"parcel": p, "project": "same", "factors": {"land_use": "旱地"}}
            for p in ["1", "22", "30", "31", "39", "111", "124", "171", "198"]]
    groups = [{"group_id": "SG_22_111", "parcels": ["22", "111"], "basis": "用户确认相邻同质"},
              {"group_id": "SG_30_31", "parcels": ["30", "31"], "basis": "用户确认共同采样"}]
    result = R.plan_sampling_groups(rows, groups)
    assert len(result["groups"]) == 7 and result["design_points"] == 21
    assert result["mapping"]["22"] == result["mapping"]["111"]
    assert result["mapping"]["30"] == result["mapping"]["31"]
    assert len(result["mapping"]) == 9 and result["status"] == "NEEDS_REVIEW"
    assert result["formal_unit_mapping"] == {}


@pytest.mark.parametrize("groups", [
    [{"group_id": "SG_a", "parcels": ["22", "999"], "basis": "确认"}],
    [{"group_id": "SG_a", "parcels": ["22", "111"], "basis": ""}],
    [{"group_id": "SG_a", "parcels": ["22", "22"], "basis": "确认"}],
    [{"group_id": "SG_a", "parcels": ["22", "111"], "basis": "确认"},
     {"group_id": "SG_b", "parcels": ["111", "30"], "basis": "确认"}],
])
def test_manual_sampling_groups_reject_unknown_duplicate_or_unexplained_members(groups):
    with pytest.raises(ValueError):
        R.plan_sampling_groups([{"parcel": p} for p in ["22", "111", "30"]], groups)


@pytest.mark.parametrize("second", [
    {"project": "other", "factors": {}},
    {"project": "same", "factors": {"land_use": "水田"}},
])
def test_manual_sampling_groups_do_not_override_known_project_or_factor_conflicts(second):
    rows = [{"parcel": "22", "project": "same", "factors": {"land_use": "旱地"}},
            dict(second, parcel="111")]
    with pytest.raises(ValueError, match="冲突"):
        R.plan_sampling_groups(rows, [{"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认"}])


@pytest.mark.parametrize("first,second", [(0, 1), (False, True)])
def test_sampling_group_numeric_zero_is_a_real_factor_value(first, second):
    rows = [{"parcel": "22", "factors": {"custom": first}},
            {"parcel": "111", "factors": {"custom": second}}]
    with pytest.raises(ValueError, match="冲突"):
        R.plan_sampling_groups(rows, [{"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认"}])


def test_saved_sampling_group_does_not_apply_to_another_project_with_same_parcel_ids():
    group = {"group_id": "SG_pair", "parcels": ["22", "111"], "basis": "确认", "project": "project-a"}
    with pytest.raises(ValueError, match="项目归属"):
        R.plan_sampling_groups([{"parcel": p, "project": "project-b"} for p in ['22', '111']], [group])
    assert R.plan_sampling_groups([{"parcel": p, "project": "project-a"} for p in ['22', '111']], [group])['design_points'] == 3


def test_numbering_uses_each_system_project_and_syncs_mixed_bulk_ids():
    rows = [{"unit": "candidate-b", "project": "F99000220261462"},
            {"unit": "candidate-a", "project": "F99000220261461"}]
    planned = R.plan_unit_numbers(rows)
    assert planned["mapping"] == {"candidate-a": "F99000220261461E0001", "candidate-b": "F99000220261462E0001"}
    assert planned["sample_ids"]["candidate-a"] == {"mixed": "F99000220261461E0001", "bulk": "RZF99000220261461E0001"}


def test_unknown_project_keeps_candidates_and_never_invents_system_number():
    result = R.plan_unit_numbers([{"unit": "PJDY0001", "project": "未填"}])
    assert result["mapping"] == {}
    assert result["status"] == "NEEDS_REVIEW"


def test_one_override_cannot_collapse_multiple_projects():
    rows = [{"unit": "U1", "project": "project-a"}, {"unit": "U2", "project": "project-b"}]
    with pytest.raises(ValueError, match="多个项目"):
        R.plan_unit_numbers(rows, "F99000220261461")


def test_explicit_project_number_is_validated_then_ordered_deterministically():
    rows = [{"unit": "U2"}, {"unit": "U1"}]
    with pytest.raises(ValueError, match="15位"):
        R.plan_unit_numbers(rows, "自编项目001")
    planned = R.plan_unit_numbers(rows, "F99000220261461")
    assert planned["mapping"]["U1"] == "F99000220261461E0001"
