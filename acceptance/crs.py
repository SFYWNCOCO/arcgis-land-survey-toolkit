"""Strict WKID parsing shared by the ArcGIS Pro intake workflow."""
from __future__ import annotations

import re


def parse_wkid(value, label="WKID", required=False):
    """Accept integer WKIDs or digit-only UI strings; reject coercive truncation."""
    if value is None or value == "":
        if required:
            raise ValueError("{}必须依据项目/测量资料明确提供".format(label))
        return None
    if isinstance(value, bool):
        raise ValueError("{}必须是正整数，不能使用布尔值".format(label))
    if isinstance(value, int):
        wkid = value
    elif isinstance(value, str) and re.fullmatch(r"\+?\d+", value.strip()):
        wkid = int(value.strip())
    else:
        raise ValueError("{}必须是正整数，拒绝小数或非整数文本".format(label))
    if wkid <= 0:
        raise ValueError("{}必须是正整数".format(label))
    return wkid


def spatial_reference(arcpy, value, label="WKID", required=False):
    """Parse and resolve a WKID before any geodatabase write starts."""
    wkid = parse_wkid(value, label=label, required=required)
    if wkid is None:
        return None, None
    reference = arcpy.SpatialReference(wkid)
    if reference is None or str(getattr(reference, "name", "Unknown")).casefold() == "unknown":
        raise ValueError("ArcGIS 无法解析{}：{}".format(label, wkid))
    return wkid, reference
