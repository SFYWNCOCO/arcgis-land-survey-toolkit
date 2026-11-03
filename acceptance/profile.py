"""项目参数文件：把同一区域/批次反复用的参数落成 JSON。

只接受运行参数白名单，不解释规范阈值；读不到或字段非法就报错，不静默忽略。
"""
from __future__ import annotations

import json
import os
import re
import copy
import math

ALLOWED = {
    "max_mu", "gap_m", "allow_multipart", "cell_size_m", "min_points",
    "min_spacing_m", "inset_m", "imagery_provider", "dpi", "meta",
    "factor_fields", "project_number", "sampling_groups",
}


def source_catalog_path(source):
    """Resolve layer names only for sidecars; never replace the processing layer."""
    path = str(source)
    if not os.path.isabs(path) and not os.path.exists(path):
        try:
            import arcpy
        except ImportError:
            pass
        else:
            path = getattr(arcpy.Describe(source), 'catalogPath', path)
    return os.path.abspath(path)


def source_folder(source):
    path = source_catalog_path(source)
    workspace = re.search(r"\.gdb(?=[\\/]|$)", path, re.IGNORECASE)
    return os.path.dirname(path[:workspace.end()]) if workspace else os.path.dirname(path)


def default_path(source):
    candidate = os.path.join(source_folder(source), "项目参数.json")
    return candidate if os.path.isfile(candidate) else None


def merge_defaults(saved, supplied):
    """Unfilled tool fields inherit project defaults; explicit values take priority."""
    result = dict(supplied)
    for key, value in saved.items():
        if key == "_path":
            continue
        if key == "meta":
            result["meta"] = _merge_meta(value or {}, result.get("meta") or {})
        elif key not in result or result[key] is None:
            result[key] = value
    return result


def _merge_meta(saved, supplied):
    result = copy.deepcopy(saved)
    for key, value in supplied.items():
        if value is None or isinstance(value, str) and not value.strip():
            continue
        result[key] = (_merge_meta(result[key], value) if isinstance(value, dict) and value
                       and isinstance(result.get(key), dict) else copy.deepcopy(value))
    return result


def project_rules(meta):
    """Validate explicit project design choices without changing normative sampling rules."""
    if meta is not None and not isinstance(meta,dict):
        raise ValueError('项目信息必须为对象')
    rules = copy.deepcopy((meta or {}).get('project_rules', {}))
    allowed = {'index_scale', 'sample_min_scale', 'excluded_parcels', 'combine_parcels', 'point_counts_by_unit', 'map_scales'}
    if not isinstance(rules, dict) or set(rules) - allowed:
        raise ValueError('项目规则无效或含未知键')
    from .sheet_plan import validate_map_scales
    validate_map_scales(rules.get('map_scales', {}))
    for name in ('index_scale', 'sample_min_scale'):
        if name in rules and (isinstance(rules[name], bool) or not isinstance(rules[name], (int,float))
                              or not math.isfinite(rules[name]) or rules[name] <= 0):
            raise ValueError('项目比例尺必须为有限正数')
    def ids(values):
        if not isinstance(values, list) or any(isinstance(v, bool) or not isinstance(v, (str,int))
                or not str(v).strip() for v in values):
            raise ValueError('项目地块号必须为非空编号数组')
        result = [str(v).strip() for v in values]
        if len(set(result)) != len(result):
            raise ValueError('项目地块号重复')
        return result
    excluded = ids(rules.get('excluded_parcels', []))
    groups, used = [], set()
    if not isinstance(rules.get('combine_parcels', []), list):
        raise ValueError('合并采样图必须为地块数组列表')
    for value in rules.get('combine_parcels', []):
        group = ids(value)
        if len(group) < 2 or used.intersection(group) or set(excluded).intersection(group):
            raise ValueError('合并采样图的地块重复、已排除或不足两块')
        groups.append(group)
        used.update(group)
    counts = rules.get('point_counts_by_unit', {})
    if not isinstance(counts, dict) or any(not isinstance(k,str) or not k.strip() or
            isinstance(v,bool) or not isinstance(v,int) or not 1 <= v <= 10000 for k,v in counts.items()):
        raise ValueError('项目自定义点数必须为1至10000的整数，采样组号不能为空')
    if 'excluded_parcels' in rules: rules['excluded_parcels'] = excluded
    if 'combine_parcels' in rules: rules['combine_parcels'] = groups
    return rules


def load(path):
    path = os.path.abspath(os.fspath(path))
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("项目参数文件必须是 JSON 对象：" + path)
    unknown = sorted(set(data) - ALLOWED)
    if unknown:
        raise ValueError("项目参数含未知键：{}（允许：{}）".format(
            "、".join(unknown), "、".join(sorted(ALLOWED))))
    if "meta" in data and not isinstance(data["meta"], dict):
        raise ValueError("meta 必须是对象")
    if "factor_fields" in data and not isinstance(data["factor_fields"], list):
        raise ValueError("factor_fields 必须是数组")
    if "sampling_groups" in data and not isinstance(data["sampling_groups"], list):
        raise ValueError("sampling_groups 必须是数组")
    project_rules(data.get('meta'))
    return dict(data, _path=path)
