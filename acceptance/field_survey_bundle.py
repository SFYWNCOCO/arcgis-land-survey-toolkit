"""Inspect field-survey ZIP exports without importing or changing source data.

The first version supports one point GeoJSON and one point Shapefile package. It
compares the paired exports, checks CRS/coordinate references and referenced
photos, and always leaves formal business acceptance to a qualified reviewer.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import math
import os
import re
import struct
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


MAX_ARCHIVE_MEMBERS = 5000
MAX_GEOJSON_BYTES = 128 * 1024 * 1024
MAX_SHP_BYTES = 256 * 1024 * 1024
MAX_DBF_BYTES = 256 * 1024 * 1024
MAX_CPG_BYTES = 4096
MAX_PRJ_BYTES = 1024 * 1024
MATCH_KEY_CANDIDATES = ("id", "point_id", "pointid", "name", "tbbh")
OBSERVED_FIELD_CANDIDATES = (
    "YXTCHD", "GCHD", "DXPD", "DLTDX", "TMPZCD", "SZYBZTJ", "PSNL",
    "SFHG", "BHGYY", "YJZHL", "SJD", "YXPHL", "SXJHL", "TRRZ", "ZHDF", "ZLDJ",
)


def _finding(code: str, severity: str, message: str, **details: Any) -> dict:
    row = {"code": code, "severity": severity, "message": message}
    row.update(details)
    return row


def _safe_zip_name(name: str) -> bool:
    normalized = str(name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    return ".." not in normalized.split("/")


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0o170000) == 0o120000


def _read_limited(archive: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int) -> bytes:
    if info.flag_bits & 0x1:
        raise ValueError("加密 ZIP 成员不支持：" + info.filename)
    if info.file_size > max_bytes:
        raise ValueError("文件超过检查上限（{} 字节）：{}".format(max_bytes, info.filename))
    payload = archive.read(info)
    if len(payload) != info.file_size:
        raise ValueError("ZIP 成员解压长度与目录记录不一致：" + info.filename)
    return payload


def _safe_value(value: Any, limit: int = 180) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…（共{}字符）".format(len(value))
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, limit) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _safe_value(item, limit) for key, item in list(value.items())[:20]}
    return value


def _encoding_from_cpg(payload: Optional[bytes]) -> Tuple[str, Optional[str]]:
    if not payload:
        return "utf-8", "Shapefile 未提供 CPG 编码文件；先按 UTF-8 解码，需人工核实中文字段。"
    label = payload.decode("ascii", "ignore").strip().replace("\x00", "")
    key = label.casefold().replace("-", "").replace("_", "").replace(" ", "")
    mapping = {"utf8": "utf-8", "65001": "utf-8", "gbk": "gbk", "936": "gbk",
               "gb18030": "gb18030", "54936": "gb18030", "big5": "big5", "950": "big5"}
    encoding = mapping.get(key)
    if encoding:
        return encoding, None
    return "utf-8", "无法识别 CPG 编码 {!r}；字段文本可能需要人工核对。".format(label)


def _dbf_value(raw: bytes, kind: str, decimals: int, encoding: str) -> Any:
    value = raw.strip()
    if not value or value == b"?":
        return None
    if kind in ("N", "F"):
        try:
            number = Decimal(value.decode("ascii"))
            if decimals == 0 and number == number.to_integral_value():
                return int(number)
            return float(number)
        except (UnicodeDecodeError, InvalidOperation):
            return {"unparsed_numeric": value.decode("ascii", "replace")}
    if kind == "D":
        return value.decode("ascii", "replace")
    if kind == "L":
        marker = value[:1].upper()
        if marker in (b"T", b"Y"):
            return True
        if marker in (b"F", b"N"):
            return False
        return None
    if kind in ("I", "+") and len(value) == 4:
        return struct.unpack("<i", value)[0]
    if kind in ("B", "O") and len(value) == 8:
        return struct.unpack("<d", value)[0]
    return value.decode(encoding, "replace").strip()


def _parse_dbf(payload: bytes, encoding: str) -> Tuple[List[dict], List[dict]]:
    if len(payload) < 33 or payload[0] not in (2, 3, 4, 5, 0x30, 0x31, 0x32, 0xF5):
        raise ValueError("DBF 文件头无效或格式不受支持。")
    record_count = struct.unpack_from("<I", payload, 4)[0]
    header_length = struct.unpack_from("<H", payload, 8)[0]
    record_length = struct.unpack_from("<H", payload, 10)[0]
    if header_length < 33 or record_length < 1 or header_length > len(payload):
        raise ValueError("DBF 头部长度或记录长度无效。")
    fields = []
    cursor = 32
    while cursor < header_length:
        if payload[cursor] == 0x0D:
            break
        descriptor = payload[cursor:cursor + 32]
        if len(descriptor) != 32:
            raise ValueError("DBF 字段描述符被截断。")
        raw_name = descriptor[:11].split(b"\x00", 1)[0]
        name = raw_name.decode("ascii", "replace").strip()
        if not name:
            raise ValueError("DBF 中存在空字段名。")
        fields.append({"name": name, "type": chr(descriptor[11]),
                       "width": descriptor[16], "decimals": descriptor[17]})
        cursor += 32
    if not fields:
        raise ValueError("DBF 中未找到字段。")
    minimum_record_length = 1 + sum(field["width"] for field in fields)
    if record_length < minimum_record_length:
        raise ValueError("DBF 记录长度小于字段宽度之和。")
    rows = []
    offset = header_length
    for row_number in range(record_count):
        end = offset + record_length
        if end > len(payload):
            raise ValueError("DBF 第 {} 条记录不完整。".format(row_number + 1))
        record = payload[offset:end]
        deleted = record[:1] == b"*"
        value_offset = 1
        row = {"_deleted": deleted}
        for field in fields:
            width = field["width"]
            raw = record[value_offset:value_offset + width]
            value_offset += width
            row[field["name"]] = _dbf_value(raw, field["type"], field["decimals"], encoding)
        rows.append(row)
        offset = end
    return fields, rows


def _parse_shp(payload: bytes) -> Tuple[int, List[Optional[Tuple[float, float]]]]:
    if len(payload) < 100 or struct.unpack_from(">i", payload, 0)[0] != 9994:
        raise ValueError("SHP 文件头无效。")
    declared_words = struct.unpack_from(">i", payload, 24)[0]
    if declared_words * 2 != len(payload):
        raise ValueError("SHP 头部记录的文件长度与实际长度不一致。")
    version, shape_type = struct.unpack_from("<2i", payload, 28)
    if version != 1000:
        raise ValueError("SHP 版本不受支持：{}".format(version))
    if shape_type not in (1, 11, 21):
        raise ValueError("外业踏勘包只接受 Point/PointZ/PointM；当前 SHP 类型 {}。".format(shape_type))
    points = []
    offset = 100
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("SHP 记录头被截断。")
        _record_number, content_words = struct.unpack_from(">2i", payload, offset)
        content_size = content_words * 2
        start, end = offset + 8, offset + 8 + content_size
        if content_size < 4 or end > len(payload):
            raise ValueError("SHP 几何记录长度无效或被截断。")
        record_type = struct.unpack_from("<i", payload, start)[0]
        if record_type == 0:
            points.append(None)
        elif record_type in (1, 11, 21) and content_size >= 20:
            x, y = struct.unpack_from("<2d", payload, start + 4)
            points.append((x, y))
        else:
            raise ValueError("SHP 第 {} 条记录不是受支持的点几何。".format(len(points) + 1))
        offset = end
    return shape_type, points


def _crs_info(value: Any, default_geojson: bool = False) -> Tuple[Optional[str], Optional[int]]:
    if not value and default_geojson:
        return "OGC:CRS84 (GeoJSON default)", 4326
    text = str(value or "").strip()
    upper = text.upper()
    if "CRS84" in upper:
        return "OGC:CRS84", 4326
    if "4326" in upper or "WGS_1984" in upper or "WGS 84" in upper:
        return "WGS 84", 4326
    if "EPSG" in upper:
        match = re.search(r"(?:EPSG[:/]+)(\d+)\s*$", text, re.IGNORECASE)
        if match:
            return text, int(match.group(1))
    return (text or None), None


def _geojson_crs(payload: dict) -> Tuple[Optional[str], Optional[int]]:
    crs = payload.get("crs") or {}
    name = ((crs.get("properties") or {}).get("name") if isinstance(crs, dict) else None)
    return _crs_info(name, default_geojson=not bool(name))


def _shapefile_crs(payload: Optional[bytes]) -> Tuple[Optional[str], Optional[int]]:
    if not payload:
        return None, None
    return _crs_info(payload.decode("utf-8", "replace"))


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * radius * math.asin(math.sqrt(a))


def _norm_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _parse_list_like(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                return json.loads(text)
            except ValueError:
                try:
                    parsed = ast.literal_eval(text)
                    if isinstance(parsed, (list, tuple, dict)):
                        return parsed
                except (ValueError, SyntaxError):
                    return value
    return value


def _normalize_value(value: Any) -> Any:
    value = _parse_list_like(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value)).normalize()
            return ("number", str(number))
        except InvalidOperation:
            pass
    if isinstance(value, (list, tuple)):
        return ("list", tuple(_normalize_value(item) for item in value))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(key), _normalize_value(item)) for key, item in value.items())))
    text = str(value).strip()
    if not text:
        return None
    date_text = text.replace("/", "-")
    if date_text.endswith("+00"):
        date_text += ":00"
    try:
        parsed = dt.datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(dt.timezone.utc)
            return ("datetime_utc", parsed.isoformat(timespec="microseconds"))
    except ValueError:
        pass
    try:
        number = Decimal(text)
        return ("number", str(number.normalize()))
    except InvalidOperation:
        return ("text", text)


def _field_map(row: dict) -> Dict[str, str]:
    return {_norm_key(key): key for key in row if key != "_deleted"}


def _choose_match_key(geo_rows: List[dict], dbf_rows: List[dict]) -> Tuple[Optional[str], dict, dict]:
    geo_maps = [_field_map(row) for row in geo_rows]
    dbf_maps = [_field_map(row) for row in dbf_rows]
    common = set.intersection(*(set(row.keys()) for row in geo_maps + dbf_maps)) if geo_rows and dbf_rows else set()
    for candidate in MATCH_KEY_CANDIDATES:
        if candidate not in common:
            continue
        geo_values = [_normalize_value(row[geo_map[candidate]]) for row, geo_map in zip(geo_rows, geo_maps)]
        dbf_values = [_normalize_value(row[dbf_map[candidate]]) for row, dbf_map in zip(dbf_rows, dbf_maps)]
        if (None not in geo_values and None not in dbf_values
                and len(set(geo_values)) == len(geo_values)
                and len(set(dbf_values)) == len(dbf_values)
                and set(geo_values) == set(dbf_values)):
            return candidate, dict(zip(geo_values, geo_rows)), dict(zip(dbf_values, dbf_rows))
    return None, {}, {}


def _paired_geometry_distances(geo_rows: List[dict], dbf_rows: List[dict],
                               shape_points: List[Optional[Tuple[float, float]]]) -> Optional[List[float]]:
    """Pair GeoJSON and SHP geometries through their shared unique DBF identity."""
    key_field, geo_by_key, dbf_by_key = _choose_match_key(geo_rows, dbf_rows)
    if not key_field:
        return None
    dbf_indexes = {id(row): index for index, row in enumerate(dbf_rows)}
    distances = []
    for normalized_key, geo_row in geo_by_key.items():
        dbf_row = dbf_by_key[normalized_key]
        index = dbf_indexes.get(id(dbf_row))
        geo_point = geo_row.get("_geometry")
        shape_point = shape_points[index] if index is not None and index < len(shape_points) else None
        if geo_point and shape_point:
            distances.append(_haversine_m(geo_point[0], geo_point[1], shape_point[0], shape_point[1]))
    return distances


def _photo_references(value: Any) -> List[str]:
    value = _parse_list_like(value)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if item not in (None, "")]
    return []


def _referenced_member_path(value: str) -> Optional[str]:
    normalized = str(value).strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)


def _safe_archive_index(archive: zipfile.ZipFile, findings: List[dict]) -> Optional[dict]:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        findings.append(_finding("ARCHIVE_MEMBER_LIMIT", "FAIL", "ZIP 文件数超过检查上限。",
                                 count=len(infos), limit=MAX_ARCHIVE_MEMBERS))
        return None
    members = {}
    for info in infos:
        name = info.filename.replace("\\", "/")
        if not _safe_zip_name(name) or _is_zip_symlink(info):
            findings.append(_finding("UNSAFE_ARCHIVE_PATH", "FAIL",
                                     "ZIP 含绝对路径、上级目录或符号链接成员，未读取数据。",
                                     path=info.filename))
            continue
        folded = name.casefold()
        if folded in members:
            findings.append(_finding("DUPLICATE_ARCHIVE_PATH", "FAIL",
                                     "ZIP 中存在大小写归一后重名的成员。", path=name))
            continue
        members[folded] = info
    if any(item["severity"] == "FAIL" for item in findings):
        return None
    return members


def _find_single(members: dict, suffixes: Tuple[str, ...], kind: str,
                 findings: List[dict]) -> Optional[zipfile.ZipInfo]:
    matches = [info for name, info in members.items() if name.endswith(suffixes)]
    if len(matches) > 1:
        findings.append(_finding("AMBIGUOUS_{}".format(kind.upper()), "REVIEW",
                                 "ZIP 中存在多个候选{}，未自动选择。".format(kind),
                                 paths=[item.filename for item in matches]))
        return None
    return matches[0] if matches else None


def _safe_member_names(members: dict) -> List[str]:
    return sorted(info.filename.replace("\\", "/") for info in members.values())


def _display_identifier(row: dict) -> str:
    fields = _field_map(row)
    for candidate in ("name", "tbbh", "point_id", "id"):
        key = fields.get(candidate)
        if key and row.get(key) not in (None, ""):
            return str(row[key])
    return ""


def _compare_rows(geo_rows: List[dict], dbf_rows: List[dict], findings: List[dict]) -> dict:
    key_field, geo_by_key, dbf_by_key = _choose_match_key(geo_rows, dbf_rows)
    if not key_field:
        findings.append(_finding("NO_UNIQUE_MATCH_KEY", "REVIEW",
                                 "GeoJSON 与 Shapefile 没有可确认的唯一共有编号，未按行号猜配。"))
        return {"identity_field": None, "matched_records": 0,
                "geojson_unmatched": len(geo_rows), "shapefile_unmatched": len(dbf_rows),
                "attribute_conflict_count": 0}
    paired = []
    conflict_count = 0
    for normalized_key in sorted(set(geo_by_key) & set(dbf_by_key), key=str):
        geo_row, dbf_row = geo_by_key[normalized_key], dbf_by_key[normalized_key]
        geo_fields, dbf_fields = _field_map(geo_row), _field_map(dbf_row)
        conflicts = []
        for normalized_field in sorted(set(geo_fields) & set(dbf_fields)):
            geo_name, dbf_name = geo_fields[normalized_field], dbf_fields[normalized_field]
            if normalized_field in ("description", "descriptio"):
                continue
            geo_value, dbf_value = geo_row.get(geo_name), dbf_row.get(dbf_name)
            if _normalize_value(geo_value) != _normalize_value(dbf_value):
                conflict = {"field": geo_name, "shapefile_field": dbf_name,
                            "geojson_value": _safe_value(geo_value),
                            "shapefile_value": _safe_value(dbf_value)}
                conflicts.append(conflict)
                conflict_count += 1
                findings.append(_finding("ATTRIBUTE_CONFLICT", "REVIEW",
                                         "同一编号在 GeoJSON 与 Shapefile 中的属性值不同。",
                                         feature_id=_display_identifier(geo_row) or str(geo_row.get(geo_fields[key_field])),
                                         field=geo_name, values={"geojson": _safe_value(geo_value),
                                                                 "shapefile": _safe_value(dbf_value)}))
        paired.append({"key": _safe_value(geo_row.get(geo_fields[key_field])),
                       "display_id": _display_identifier(geo_row),
                       "geojson_geometry": None,
                       "attribute_conflicts": conflicts})
    matched = len(paired)
    return {"identity_field": key_field, "matched_records": matched,
            "geojson_unmatched": max(0, len(geo_rows) - matched),
            "shapefile_unmatched": max(0, len(dbf_rows) - matched),
            "attribute_conflict_count": conflict_count, "records": paired}


def _check_coordinate_attributes(geo_rows: List[dict], geojson_crs_wkid: Optional[int],
                                 findings: List[dict]) -> List[dict]:
    checks = []
    if geojson_crs_wkid != 4326:
        return checks
    for row in geo_rows:
        fields = _field_map(row)
        geometry = row.get("_geometry")
        if not geometry:
            continue
        lon, lat = geometry
        jd_key, wd_key = fields.get("jd"), fields.get("wd")
        if jd_key and wd_key:
            try:
                distance = _haversine_m(lon, lat, float(row[jd_key]), float(row[wd_key]))
                checks.append({"feature_id": _display_identifier(row), "pair": "JD/WD",
                               "distance_m": round(distance, 3), "source_crs": "OGC:CRS84/WGS84"})
                if distance > 5.0:
                    findings.append(_finding("GEOMETRY_ATTRIBUTE_COORDINATE_CONFLICT", "REVIEW",
                                             "要素几何与 JD/WD 属性坐标相差超过 5 米。",
                                             feature_id=_display_identifier(row), distance_m=round(distance, 3)))
            except (TypeError, ValueError):
                findings.append(_finding("INVALID_JD_WD", "REVIEW", "JD/WD 属性坐标无法解析。",
                                         feature_id=_display_identifier(row)))
        gcj_lon_key, gcj_lat_key = fields.get("lnggcj02"), fields.get("latgcj02")
        if gcj_lon_key and gcj_lat_key:
            try:
                distance = _haversine_m(lon, lat, float(row[gcj_lon_key]), float(row[gcj_lat_key]))
                findings.append(_finding("ALTERNATE_GCJ02_COORDINATES", "INFO",
                                         "存在 GCJ-02 属性坐标；它与 CRS84/WGS84 几何属于不同坐标来源，不作为几何偏移错误，也不得直接替换几何。",
                                         feature_id=_display_identifier(row), distance_m=round(distance, 1)))
            except (TypeError, ValueError):
                findings.append(_finding("INVALID_GCJ02_COORDINATE", "REVIEW",
                                         "GCJ-02 属性坐标无法解析。", feature_id=_display_identifier(row)))
    return checks


def _collect_photos(rows: List[dict], archive_names: List[str], findings: List[dict]) -> dict:
    available = {name.casefold() for name in archive_names}
    references, bad_paths = [], []
    for row in rows:
        fields = _field_map(row)
        photo_key = fields.get("photos")
        count_key = fields.get("photocount")
        if not photo_key:
            continue
        for reference in _photo_references(row.get(photo_key)):
            member_path = _referenced_member_path(reference)
            if member_path is None:
                bad_paths.append(reference)
                findings.append(_finding("UNSAFE_PHOTO_REFERENCE", "REVIEW",
                                         "照片引用为绝对路径或包含上级目录，未尝试访问。",
                                         feature_id=_display_identifier(row), path=reference))
                continue
            exists = member_path.casefold() in available
            references.append({"feature_id": _display_identifier(row), "path": member_path,
                               "present_in_bundle": exists})
            if not exists:
                findings.append(_finding("PHOTO_REFERENCE_MISSING", "REVIEW",
                                         "属性记录了照片路径，但 ZIP 中没有对应照片文件。",
                                         feature_id=_display_identifier(row), path=member_path))
        if count_key:
            try:
                declared = int(row[count_key] or 0)
            except (TypeError, ValueError):
                declared = None
            actual_refs = len(_photo_references(row.get(photo_key)))
            if declared is not None and declared != actual_refs:
                findings.append(_finding("PHOTO_COUNT_MISMATCH", "REVIEW",
                                         "photoCount 与 photos 字段引用数不一致。",
                                         feature_id=_display_identifier(row),
                                         declared_count=declared, reference_count=actual_refs))
    return {"reference_count": len(references), "present_count": sum(1 for item in references if item["present_in_bundle"]),
            "missing_count": sum(1 for item in references if not item["present_in_bundle"]),
            "unsafe_references": bad_paths, "references": references}


def _observed_field_completeness(rows: List[dict]) -> dict:
    fields = set()
    for row in rows:
        fields.update(_field_map(row).keys())
    result = {}
    for candidate in OBSERVED_FIELD_CANDIDATES:
        key = candidate.casefold()
        if key not in fields:
            result[candidate] = {"present": False, "blank_count": None, "feature_count": len(rows)}
            continue
        blank = 0
        for row in rows:
            source_key = _field_map(row).get(key)
            if source_key is None or row.get(source_key) in (None, ""):
                blank += 1
        result[candidate] = {"present": True, "blank_count": blank, "feature_count": len(rows)}
    return result


def _mapping_candidates(fields: set) -> List[dict]:
    candidates = {
        "YXTCHD": ("有效土层厚度", "exact_code"),
        "GCHD": ("耕层厚度", "exact_code"),
        "DXBW": ("地形部位", "exact_code"),
        "ZDGX": ("质地构型", "exact_code"),
        "TZ": ("土种", "exact_code"),
        "HBGD": ("海拔", "exact_code"),
        "GCXZ": ("耕层质地（标准字段 GCZD）", "candidate_requires_confirmation"),
        "YJZHL": ("有机质（标准字段 OM）", "candidate_requires_confirmation"),
        "YXPHL": ("有效磷（标准字段 AP）", "candidate_requires_confirmation"),
        "SXJHL": ("速效钾（标准字段 AK）", "candidate_requires_confirmation"),
        "SJD": ("含义待数据字典确认；不得自动映射 pH", "unknown_requires_confirmation"),
        "QRTLXJHL": ("含义待数据字典确认；需区分 10–100 mm 含量与 >100 mm 是否存在", "unknown_requires_confirmation"),
        "DLTDX": ("道路通行条件", "candidate_requires_confirmation"),
        "TMPZCD": ("田面平整程度", "candidate_requires_confirmation"),
        "SZYBZTJ": ("水资源保障条件", "candidate_requires_confirmation"),
    }
    output = []
    for field, (meaning, status) in candidates.items():
        if field.casefold() in fields:
            output.append({"source_field": field, "candidate_meaning": meaning,
                           "status": status, "auto_apply": False})
    return output


def _inspect(source_path: Path) -> dict:
    findings = []
    source_hash = hashlib.sha256()
    with source_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            source_hash.update(chunk)
    report = {
        "schema_version": 1,
        "tool": "field_survey_bundle_inspector",
        "source": {"path": str(source_path), "size_bytes": source_path.stat().st_size,
                   "sha256": source_hash.hexdigest()},
        "status": "NEEDS_REVIEW",
        "structural_status": "NEEDS_REVIEW",
        "business_acceptance": "NOT_EVALUATED",
        "findings": findings,
        "comparison": {"identity_field": None, "matched_records": 0,
                        "geojson_unmatched": 0, "shapefile_unmatched": 0,
                        "attribute_conflict_count": 0},
    }
    if report["source"]["size_bytes"] == 0:
        findings.append(_finding("EMPTY_ARCHIVE", "FAIL", "ZIP 文件为空。"))
        report["status"] = report["structural_status"] = "FAIL"
        return report
    try:
        archive = zipfile.ZipFile(str(source_path), "r")
    except (OSError, zipfile.BadZipFile) as exc:
        findings.append(_finding("INVALID_ZIP", "FAIL", "无法读取 ZIP：{}".format(exc)))
        report["status"] = report["structural_status"] = "FAIL"
        return report
    with archive:
        members = _safe_archive_index(archive, findings)
        if members is None:
            report["status"] = report["structural_status"] = "FAIL"
            return report
        archive_names = _safe_member_names(members)
        report["files"] = [{"path": info.filename.replace("\\", "/"), "size_bytes": info.file_size}
                           for info in sorted(members.values(), key=lambda item: item.filename.casefold())]
        geojson_info = _find_single(members, (".geojson",), "geojson", findings)
        shp_info = _find_single(members, (".shp",), "shapefile", findings)
        if geojson_info is None and shp_info is None:
            findings.append(_finding("NO_SUPPORTED_POINT_DATA", "FAIL",
                                     "ZIP 中未找到 GeoJSON 或 Shapefile 点数据。"))
            report["status"] = report["structural_status"] = "FAIL"
            return report
        geo_rows, geo_coords, geo_wkid = [], [], None
        if geojson_info:
            try:
                raw = _read_limited(archive, geojson_info, MAX_GEOJSON_BYTES)
                payload = json.loads(raw.decode("utf-8-sig"))
                if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
                    raise ValueError("GeoJSON 须为 FeatureCollection。")
                geo_crs, geo_wkid = _geojson_crs(payload)
                report["geojson"] = {"path": geojson_info.filename.replace("\\", "/"),
                                     "feature_count": len(payload["features"]),
                                     "crs": geo_crs or "Unknown", "wkid_candidate": geo_wkid,
                                     "geometry_types": sorted({str((feature.get("geometry") or {}).get("type"))
                                                                for feature in payload["features"]})}
                for index, feature in enumerate(payload["features"], 1):
                    geometry = feature.get("geometry") if isinstance(feature, dict) else None
                    properties = feature.get("properties") if isinstance(feature, dict) else None
                    if not isinstance(properties, dict):
                        properties = {}
                        findings.append(_finding("INVALID_GEOJSON_PROPERTIES", "REVIEW",
                                                 "GeoJSON 属性不是对象。", record=index))
                    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
                        findings.append(_finding("UNSUPPORTED_GEOJSON_GEOMETRY", "FAIL",
                                                 "外业踏勘包首版只处理 Point 几何。", record=index,
                                                 geometry_type=(geometry or {}).get("type")))
                        continue
                    coordinates = geometry.get("coordinates") or []
                    if len(coordinates) < 2:
                        findings.append(_finding("INVALID_GEOJSON_COORDINATES", "FAIL",
                                                 "点几何坐标不足两个值。", record=index))
                        continue
                    lon, lat = float(coordinates[0]), float(coordinates[1])
                    if geo_wkid == 4326 and not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        findings.append(_finding("GEOJSON_COORDINATE_RANGE", "FAIL",
                                                 "CRS84/WGS84 点坐标超出经纬度范围。", record=index,
                                                 coordinates=[lon, lat]))
                        continue
                    row = dict(properties)
                    row["_geometry"] = (lon, lat)
                    geo_rows.append(row)
                    geo_coords.append((lon, lat))
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                findings.append(_finding("INVALID_GEOJSON", "FAIL", "GeoJSON 读取失败：{}".format(exc),
                                         path=geojson_info.filename))
        dbf_rows, dbf_fields, shp_coords, shp_wkid = [], [], [], None
        if shp_info:
            shp_path = Path(shp_info.filename.replace("\\", "/"))
            base = str(shp_path.with_suffix(""))
            sidecars = {Path(info.filename.replace("\\", "/")).suffix.casefold(): info
                        for info in members.values()
                        if str(Path(info.filename.replace("\\", "/")).with_suffix("")) == base}
            missing = [ext for ext in (".shx", ".dbf") if ext not in sidecars]
            if missing:
                findings.append(_finding("SHAPEFILE_SIDECARS_MISSING", "FAIL",
                                         "Shapefile 缺少必需配套文件。", missing=missing))
            else:
                try:
                    shp_data = _read_limited(archive, shp_info, MAX_SHP_BYTES)
                    _shape_type, shp_coords = _parse_shp(shp_data)
                    dbf_data = _read_limited(archive, sidecars[".dbf"], MAX_DBF_BYTES)
                    cpg_data = (_read_limited(archive, sidecars[".cpg"], MAX_CPG_BYTES)
                                if ".cpg" in sidecars else None)
                    encoding, encoding_warning = _encoding_from_cpg(cpg_data)
                    if encoding_warning:
                        findings.append(_finding("DBF_ENCODING_UNVERIFIED", "REVIEW", encoding_warning))
                    dbf_fields, dbf_rows = _parse_dbf(dbf_data, encoding)
                    if len(shp_coords) != len(dbf_rows):
                        findings.append(_finding("SHP_DBF_COUNT_MISMATCH", "FAIL",
                                                 "SHP 几何数与 DBF 属性记录数不同。",
                                                 shp_count=len(shp_coords), dbf_count=len(dbf_rows)))
                    prj_data = (_read_limited(archive, sidecars[".prj"], MAX_PRJ_BYTES)
                                if ".prj" in sidecars else None)
                    shp_crs, shp_wkid = _shapefile_crs(prj_data)
                    if not prj_data:
                        findings.append(_finding("SHAPEFILE_CRS_MISSING", "REVIEW",
                                                 "Shapefile 缺少 PRJ 坐标参考文件。"))
                    report["shapefile"] = {"path": shp_info.filename.replace("\\", "/"),
                                           "feature_count": len(shp_coords), "shape_type": _shape_type,
                                           "crs": shp_crs or "Unknown", "wkid_candidate": shp_wkid,
                                           "fields": [{"name": field["name"], "type": field["type"],
                                                       "width": field["width"], "decimals": field["decimals"]}
                                                      for field in dbf_fields]}
                except (ValueError, struct.error, UnicodeDecodeError) as exc:
                    findings.append(_finding("INVALID_SHAPEFILE", "FAIL", "Shapefile 读取失败：{}".format(exc),
                                             path=shp_info.filename))
        if geo_rows and dbf_rows:
            report["comparison"] = _compare_rows(geo_rows, dbf_rows, findings)
        elif geo_rows or dbf_rows:
            report["comparison"] = {"identity_field": None, "matched_records": 0,
                                     "geojson_unmatched": len(geo_rows),
                                     "shapefile_unmatched": len(dbf_rows),
                                     "attribute_conflict_count": 0}
            findings.append(_finding("CROSS_FORMAT_COMPARISON_UNAVAILABLE", "REVIEW",
                                     "ZIP 中未同时提供可读的 GeoJSON 与 Shapefile；只完成单格式检查。"))
        if geo_rows:
            report["coordinate_review"] = {"geojson_crs": report.get("geojson", {}).get("crs", "Unknown"),
                                            "geojson_wkid_candidate": geo_wkid,
                                            "shapefile_crs": report.get("shapefile", {}).get("crs", "Unknown"),
                                            "shapefile_wkid_candidate": shp_wkid}
            if geo_wkid == 4326 and shp_wkid == 4326 and geo_coords and shp_coords:
                distances = _paired_geometry_distances(geo_rows, dbf_rows, shp_coords)
                if distances is None:
                    report["coordinate_review"]["paired_geometry_distance_max_m"] = None
                else:
                    distances = [round(value, 3) for value in distances]
                    report["coordinate_review"]["paired_geometry_distance_max_m"] = max(distances) if distances else None
                if distances and max(distances) > 5.0:
                    findings.append(_finding("GEOMETRY_CONFLICT", "REVIEW",
                                             "两种格式的配对点几何相差超过 5 米。",
                                             max_distance_m=round(max(distances), 3)))
            report["coordinate_review"]["attribute_checks"] = _check_coordinate_attributes(geo_rows, geo_wkid, findings)
            report["field_completeness"] = _observed_field_completeness(geo_rows)
            all_fields = {key for row in geo_rows for key in _field_map(row)}
            report["field_mapping_candidates"] = _mapping_candidates(all_fields)
            xmmc_values = sorted({str(row.get(_field_map(row).get("xmmc"))).strip()
                                  for row in geo_rows
                                  if _field_map(row).get("xmmc") and row.get(_field_map(row).get("xmmc")) not in (None, "")})
            if len(xmmc_values) > 1:
                findings.append(_finding("PROJECT_NAME_VARIATION", "REVIEW",
                                         "同一 GeoJSON 文件中项目名称存在多个取值；先确认记录是否属于同一项目。",
                                         values=xmmc_values))
            report["photos"] = _collect_photos(geo_rows, archive_names, findings)
        elif dbf_rows:
            report["field_completeness"] = _observed_field_completeness(dbf_rows)
            all_fields = {key for row in dbf_rows for key in _field_map(row)}
            report["field_mapping_candidates"] = _mapping_candidates(all_fields)
            report["photos"] = _collect_photos(dbf_rows, archive_names, findings)
    fatal = any(item["severity"] == "FAIL" for item in findings)
    review = any(item["severity"] == "REVIEW" for item in findings)
    report["structural_status"] = "FAIL" if fatal else "NEEDS_REVIEW" if review else "PASS"
    # 结构检查不能替代现场真实性、检测结果、签章或专业审核，因此整体不自动判为业务通过。
    report["status"] = "FAIL" if fatal else "NEEDS_REVIEW"
    report["business_acceptance"] = "NOT_EVALUATED"
    report["next_steps"] = [
        "先处理 GeoJSON 与 Shapefile 属性冲突，确定唯一可信来源并保留原值。",
        "确认坐标字段和坐标系；GCJ-02 属性坐标不得直接替换 CRS84/WGS84 几何。",
        "补齐缺失照片文件并人工核对照片内容、外业真实性和业务字段含义。",
        "完成字段映射后再运行广西项目符合性评价；本报告不是合格结论。",
    ]
    return report


def inspect_field_survey_bundle(source, report_path=None) -> dict:
    """Create a non-destructive structural review of a zipped field-survey export.

    The output path must be new. The original ZIP is only read and is never extracted
    or modified. ``status`` can be FAIL or NEEDS_REVIEW; this tool never certifies
    formal business acceptance.
    """
    source_path = Path(source).resolve(strict=True)
    destination = Path(report_path).resolve() if report_path else None
    if destination and destination.exists():
        raise FileExistsError("检查报告已存在，不覆盖：" + str(destination))
    if source_path.suffix.casefold() != ".zip":
        raise ValueError("外业踏勘包检查仅接受 .zip 文件。")
    report = _inspect(source_path)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        if temporary.exists():
            raise FileExistsError("临时报告已存在，不覆盖：" + str(temporary))
        try:
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(str(temporary), str(destination))
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        report["report_path"] = str(destination)
    return report
