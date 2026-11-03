from types import SimpleNamespace
from acceptance import point_adjustment as E


def test_signature_tracks_profile_and_bulk_geometry_and_sample_ids():
    rows = {"sample": [("P1", "U1", "parcel", 1, "U1", "DESIGN_ONLY", "geometry")],
            "units": [("U1", "unit-geometry")], "assign": [("U1", "parcel", "parcel-geometry")],
            "grid": [("U1", 1, "cell")], "node": [("U1", "node")], "center": [("U1", 1, "parcel", 1, "center")],
            "profiles": [("U1", "DESIGN_ONLY", "profile-geometry")],
            "bulk": [("U1", "RZU1", 1, "DESIGN_ONLY", "old-bulk-geometry")]}
    class Cursor:
        def __init__(self, source, _fields): self.rows = rows[source]
        def __enter__(self): return iter(self.rows)
        def __exit__(self, *_args): pass
    api = SimpleNamespace(da=SimpleNamespace(SearchCursor=Cursor))
    data = {"assign": "assign", "unit_field": "UNIT", "assign_id": "PARCEL"}
    sampling = {"grid": {key: key for key in ("sample", "grid", "node", "center")}, "units_for_map": "units",
                "bulk_design": {"profiles": "profiles", "output": "bulk"}}
    first = E.design_signature(data, sampling, api)
    rows["bulk"][0] = ("U1", "WRONG_ID", 1, "DESIGN_ONLY", "moved-bulk-geometry")
    second = E.design_signature(data, sampling, api)
    assert first != second
    rows["profiles"][0] = ("U1", "DESIGN_ONLY", "moved-profile-geometry")
    assert E.design_signature(data, sampling, api) != second
