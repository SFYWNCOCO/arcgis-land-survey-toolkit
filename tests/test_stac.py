import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance.stac import (
    build_search_payload, group_candidates, parse_item, parse_search_page,
    search_items, validate_asset_href,
)


def _asset(key, band_name, href, gsd, scale=None, offset=None):
    raster_band = {"nodata": 0, "data_type": "uint16", "spatial_resolution": gsd}
    if scale is not None:
        raster_band["scale"] = scale
    if offset is not None:
        raster_band["offset"] = offset
    return {
        "href": href,
        "raster:bands": [raster_band],
        "eo:bands": [{"name": band_name, "common_name": key}],
    }


def _item():
    base = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sample/"
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "S2A_T49QDG_20261008T031125_L2A",
        "collection": "sentinel-2-c1-l2a",
        "geometry": {"type": "Polygon", "coordinates": [[[110, 24], [111, 24], [111, 25], [110, 25], [110, 24]]]},
        "properties": {
            "datetime": "2026-10-08T03:21:26.153Z",
            "platform": "sentinel-2a",
            "eo:cloud_cover": 12.5,
            "s2:processing_baseline": "05.13",
            "grid:code": "MGRS-49QDG",
        },
        "assets": {
            "red": _asset("red", "B04", base + "B04.tif", 10, 0.0001, -0.1),
            "nir": _asset("nir", "B08", base + "B08.tif", 10, 0.0001, -0.1),
            "scl": _asset("scl", "SCL", base + "SCL.tif", 20),
        },
    }


def test_parse_sentinel_item_preserves_source_metadata_and_scale_offset():
    item = parse_item(_item())

    assert item["item_id"] == "S2A_T49QDG_20261008T031125_L2A"
    assert item["acquisition_date"] == "2026-10-08"
    assert item["platform"] == "sentinel-2a"
    assert item["processing_baseline"] == "05.13"
    assert item["cloud_cover_pct"] == pytest.approx(12.5)
    assert item["grid_code"] == "MGRS-49QDG"
    assert item["assets"]["red"]["band_name"] == "B04"
    assert item["assets"]["nir"]["scale"] == pytest.approx(0.0001)
    assert item["assets"]["nir"]["offset"] == pytest.approx(-0.1)
    assert item["assets"]["scl"]["gsd_m"] == 20


def test_parse_item_rejects_wrong_collection_missing_scl_and_unknown_asset_host():
    wrong_collection = _item()
    wrong_collection["collection"] = "landsat-c2-l2"
    with pytest.raises(ValueError, match="collection"):
        parse_item(wrong_collection)

    missing_scl = _item()
    del missing_scl["assets"]["scl"]
    with pytest.raises(ValueError, match="SCL"):
        parse_item(missing_scl)

    malicious_href = _item()
    malicious_href["assets"]["nir"]["href"] = "https://example.org/B08.tif"
    with pytest.raises(ValueError, match="不允许的资产主机"):
        parse_item(malicious_href)


def test_parse_item_requires_reflectance_scale_and_offset():
    missing_scale = _item()
    missing_scale["assets"]["red"]["raster:bands"][0].pop("scale")

    with pytest.raises(ValueError, match="scale/offset"):
        parse_item(missing_scale)


def test_asset_href_must_use_allowed_https_host():
    href = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/a.tif"

    assert validate_asset_href(href) == href
    with pytest.raises(ValueError, match="HTTPS"):
        validate_asset_href(href.replace("https://", "http://"))
    with pytest.raises(ValueError, match="不允许的资产主机"):
        validate_asset_href("https://evil.invalid/a.tif")


def test_parse_search_page_returns_valid_items_and_rejection_reasons():
    invalid = _item()
    invalid["id"] = "bad"
    invalid["assets"].pop("scl")
    page = {"type": "FeatureCollection", "features": [_item(), invalid], "links": []}

    result = parse_search_page(page)

    assert len(result["items"]) == 1
    assert result["items"][0]["item_id"] == _item()["id"]
    assert result["rejected"] == [{"item_id": "bad", "reason": "Sentinel-2 L2A资产缺少SCL。"}]


def test_search_geometry_must_be_closed_wgs84_polygon():
    geometry = _item()["geometry"]
    assert build_search_payload(geometry, "2026-10-01", "2026-10-31")["collections"] == ["sentinel-2-c1-l2a"]
    open_ring = {"type": "Polygon", "coordinates": [[[110, 24], [111, 24], [111, 25], [110, 25]]]}
    with pytest.raises(ValueError, match="闭合"):
        build_search_payload(open_ring, "2026-10-01", "2026-10-31")
    outside = {"type": "Polygon", "coordinates": [[[190, 24], [191, 24], [191, 25], [190, 25], [190, 24]]]}
    with pytest.raises(ValueError, match="WGS84"):
        build_search_payload(outside, "2026-10-01", "2026-10-31")


def test_candidate_groups_separate_platform_and_processing_baseline():
    first = parse_item(_item())
    neighbor = parse_item(_item())
    neighbor["item_id"] = "neighbor"
    neighbor["grid_code"] = "MGRS-49QEG"
    other = dict(first)
    other["item_id"] = "other-platform"
    other["platform"] = "sentinel-2b"

    groups = group_candidates([first, neighbor, first, other])

    assert len(groups) == 2
    same = next(group for group in groups if group["platform"] == "sentinel-2a")
    assert same["tiles"] == ["MGRS-49QDG", "MGRS-49QEG"]
    assert same["item_count"] == 2


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        import json
        return json.dumps(self.payload).encode("utf-8")


def test_search_follows_validated_pages_and_keeps_completion_status():
    first_page = {
        "type": "FeatureCollection",
        "features": [_item()],
        "links": [{"rel": "next", "href": "https://earth-search.aws.element84.com/v1/search?page=2", "method": "GET"}],
    }
    second = _item()
    second["id"] = "second"
    second_page = {"type": "FeatureCollection", "features": [second], "links": []}
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout))
        return _Response(first_page if len(calls) == 1 else second_page)

    result = search_items(
        _item()["geometry"], "2026-10-01", "2026-10-31", opener=opener,
    )

    assert result["search_complete"] is True
    assert result["pages"] == 2
    assert [item["item_id"] for item in result["items"]] == [_item()["id"], "second"]
    assert calls[1][0].endswith("?page=2")


def test_search_does_not_report_page_cap_results_as_complete():
    page = {
        "type": "FeatureCollection", "features": [_item()],
        "links": [{"rel": "next", "href": "https://earth-search.aws.element84.com/v1/search?page=2", "method": "GET"}],
    }
    result = search_items(
        _item()["geometry"], "2026-10-01", "2026-10-31", max_pages=1,
        opener=lambda *_args, **_kwargs: _Response(page),
    )
    assert result["search_complete"] is False
    assert "最大翻页数" in result["page_error"]


def test_search_allows_same_post_endpoint_with_a_new_cursor_body():
    url = "https://earth-search.aws.element84.com/v1/search"
    first = {"type": "FeatureCollection", "features": [_item()], "links": [
        {"rel": "next", "href": url, "method": "POST", "body": {"token": "cursor-2"}},
    ]}
    second = {"type": "FeatureCollection", "features": [], "links": []}
    calls = []

    def opener(request, timeout):
        calls.append(request.data)
        return _Response(first if len(calls) == 1 else second)

    result = search_items(_item()["geometry"], "2026-10-01", "2026-10-31", opener=opener)

    assert result["search_complete"] is True
    assert len(calls) == 2
