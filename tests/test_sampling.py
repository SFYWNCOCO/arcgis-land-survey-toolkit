import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.sampling import _projected_meters_per_unit
from acceptance.rules import RULES
from acceptance import sampling


def test_sampling_distance_requires_projected_coordinate_system():
    geographic = SimpleNamespace(
        name="GCS_WGS_1984",
        type="Geographic",
        linearUnitName="Degree",
        metersPerUnit=111319.49079327358,
    )

    with pytest.raises(ValueError, match="不能直接用经纬度计算"):
        _projected_meters_per_unit(geographic)


@pytest.mark.parametrize(
    "unit,meters_per_unit",
    [("Meter", 1.0), ("Foot", 0.3048)],
)
def test_sampling_distance_accepts_projected_linear_units(unit, meters_per_unit):
    projected = SimpleNamespace(
        name="Project CRS",
        type="Projected",
        linearUnitName=unit,
        metersPerUnit=meters_per_unit,
    )

    assert _projected_meters_per_unit(projected) == pytest.approx(meters_per_unit)


@pytest.mark.parametrize("meters_per_unit", [0, -1, float("nan"), float("inf")])
def test_sampling_distance_rejects_invalid_unit_conversion(meters_per_unit):
    projected = SimpleNamespace(
        name="Project CRS",
        type="Projected",
        linearUnitName="Meter",
        metersPerUnit=meters_per_unit,
    )

    with pytest.raises(ValueError, match="有效的投影线性单位换算"):
        _projected_meters_per_unit(projected)


def test_sampling_rules_are_mapped_to_the_guide_clauses_that_define_them():
    design = RULES["composite_sampling_design"]
    collection = RULES["composite_sample_collection"]
    density = RULES["bulk_density"]

    assert design["clause"] == "6.1.4"
    assert design["min_subsites"] == 3 and design["per_parcel"] == 1
    assert collection["clause"] == "6.2.4.3(1)"
    assert collection["min_spacing_m"] == 15 and collection["depth_cm"] == [0, 20]
    assert density["sample_id_prefix"] == "RZ"
    assert density["cores_per_unit"] == 3 and density["ring_cm3"] == 100


def test_sampling_failure_rolls_back_partially_created_output():
    existing = set()
    fake_arcpy = SimpleNamespace(
        Exists=lambda path: path in existing,
        management=SimpleNamespace(Delete=lambda path: existing.remove(path)),
    )
    run = getattr(sampling, "_with_output_rollback", None)
    assert callable(run), "sampling must clean partial feature classes after ArcPy failures"

    def create_then_fail(mark_created):
        existing.add("project.gdb\\sample_design")
        mark_created()
        raise RuntimeError("simulated insert failure")

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        run(fake_arcpy, "project.gdb\\sample_design", create_then_fail)

    assert existing == set()


def test_sampling_rollback_never_deletes_preexisting_output():
    existing = {"project.gdb\\sample_design"}
    fake_arcpy = SimpleNamespace(
        Exists=lambda path: path in existing,
        management=SimpleNamespace(Delete=lambda path: existing.remove(path)),
    )

    def fail_before_ownership(_mark_created):
        raise FileExistsError("output already exists")

    with pytest.raises(FileExistsError, match="already exists"):
        sampling._with_output_rollback(fake_arcpy, "project.gdb\\sample_design", fail_before_ownership)

    assert existing == {"project.gdb\\sample_design"}


def test_sampling_cleanup_failure_preserves_original_error():
    existing = set()

    def delete_fails(_path):
        raise PermissionError("simulated lock")

    fake_arcpy = SimpleNamespace(
        Exists=lambda path: path in existing,
        management=SimpleNamespace(Delete=delete_fails),
    )

    def create_then_fail(mark_created):
        existing.add("project.gdb\\sample_design")
        mark_created()
        raise ValueError("primary insert error")

    with pytest.raises(RuntimeError, match="primary insert error.*simulated lock"):
        sampling._with_output_rollback(fake_arcpy, "project.gdb\\sample_design", create_then_fail)


@pytest.mark.parametrize(
    "description,selection_state,expected",
    [
        (SimpleNamespace(shapeType="Polygon", whereClause="", FIDSet="2;4"), [2, 4], "存在选择集"),
        (SimpleNamespace(shapeType="Polygon", whereClause="UNIT='U1'", FIDSet=""), None, "存在定义查询"),
        (SimpleNamespace(shapeType="Polygon", whereClause="", FIDSet=""), [], "存在选择集"),
    ],
)
def test_composite_generator_rejects_filtered_or_selected_input(
    monkeypatch, description, selection_state, expected,
):
    description.spatialReference = SimpleNamespace(
        name="Project CRS", type="Projected", linearUnitName="Meter", metersPerUnit=1.0,
    )
    arcpy = SimpleNamespace(
        Describe=lambda _path: description,
        da=SimpleNamespace(
            Describe=lambda _path: {"FIDSet": selection_state},
            SearchCursor=lambda *_args, **_kwargs: pytest.fail("must reject before cursor iteration"),
        ),
    )
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)

    with pytest.raises(ValueError, match=expected):
        sampling.composite_candidates("filtered_layer", "UNIT", "PARCEL", "out.gdb")


@pytest.mark.parametrize(
    "selection_state,where_clause,expected",
    [([3], "", "存在选择集"), ([], "", "存在选择集"), (None, "UNIT='U1'", "存在定义查询")],
)
def test_bulk_density_generator_rejects_filtered_or_selected_input(
    monkeypatch, selection_state, where_clause, expected,
):
    description = SimpleNamespace(
        shapeType="Point", whereClause=where_clause, FIDSet="3" if selection_state else "",
        spatialReference=SimpleNamespace(
            name="Project CRS", type="Projected", linearUnitName="Meter", metersPerUnit=1.0,
        ),
    )
    arcpy = SimpleNamespace(
        Describe=lambda _path: description,
        da=SimpleNamespace(
            Describe=lambda _path: {"FIDSet": selection_state},
            SearchCursor=lambda *_args, **_kwargs: pytest.fail("must reject before cursor iteration"),
        ),
    )
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)

    with pytest.raises(ValueError, match=expected):
        sampling.bulk_density_candidates("selected_layer", "UNIT", "out.gdb")


def test_bulk_density_generator_requires_evaluation_unit_polygons(monkeypatch):
    description = SimpleNamespace(
        shapeType="Point", whereClause="", FIDSet="",
        spatialReference=SimpleNamespace(
            name="Project CRS", type="Projected", linearUnitName="Meter", metersPerUnit=1.0,
        ),
    )
    arcpy = SimpleNamespace(
        Describe=lambda _path: description,
        da=SimpleNamespace(Describe=lambda _path: {"FIDSet": None}),
    )
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)

    with pytest.raises(ValueError, match="必须提供评价单元面图层"):
        sampling.bulk_density_candidates("profiles", "UNIT", "out.gdb")
