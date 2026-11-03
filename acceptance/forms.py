"""外业成套资料：附录 D/E/F 表单与混合样/容重样标签。

只读模板包原件，输出到成果目录。所有预填值来自一键成果报告、采样点清单与项目
信息；现场/检测值一律留空，不把设计点写成实采记录。
"""
from __future__ import annotations

import copy
import datetime as dt
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "vendor"
TEMPLATE_NAMES = {
    "D": "表单_附录D_实地踏勘表.docx",
    "E": "表单_附录E_土壤样品采集记录表.docx",
    "F": "表单_附录F_补充耕地质量鉴定意见表.docx",
}
PLACEHOLDER = "（待填写）"
LABEL_TEMPLATE_NAME = "表单_样品标签.docx"
USE_NOTE = "设计预填，不是实采记录；现场核对并签字。"


def _docx():
    """优先用已安装 python-docx；Pro Python 缺包时使用包内 vendor。"""
    try:
        import docx
    except ImportError:
        if VENDOR.is_dir() and str(VENDOR) not in sys.path:
            sys.path.insert(0, str(VENDOR))
        import docx
    return docx


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _text(value, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


@dataclass
class DesignPoint:
    point_id: str = ""
    sample_id: str = ""
    parcel: str = ""
    x: str = ""
    y: str = ""


@dataclass
class UnitForm:
    unit: str
    project_name: str = ""
    project_number: str = ""
    parcels: list[str] = field(default_factory=list)
    area_m2: float | None = None
    location: str = ""
    land_use: str = ""
    region: str = ""
    point_count: int = 0
    points: list[DesignPoint] = field(default_factory=list)

    @property
    def mixed_id(self) -> str:
        return self.unit

    @property
    def bulk_id(self) -> str:
        return "RZ" + self.unit


def _read_json(path) -> dict:
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象：" + os.fspath(path))
    return data


def _read_points(path) -> dict[str, list[DesignPoint]]:
    if not path or not os.path.isfile(path):
        return {}
    import csv
    grouped: dict[str, list[DesignPoint]] = {}
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            unit = _text(row.get("评价单元") or row.get("PJDYBH"))
            if not unit:
                continue
            grouped.setdefault(unit, []).append(DesignPoint(
                point_id=_text(row.get("设计点编号") or row.get("POINT_ID")),
                sample_id=_text(row.get("混合样号") or row.get("SAMPLE_ID")),
                parcel=_text(row.get("图斑编号") or row.get("DKBH")),
                x=_text(row.get("X_成果坐标系") or row.get("X")),
                y=_text(row.get("Y_成果坐标系") or row.get("Y"))))
    return grouped


def _region_lookup(config_path=None) -> dict[str, str]:
    """县名 → 二级农业区名。配置缺失时返回空映射，由调用方标待补。"""
    path = config_path or (Path(__file__).resolve().parent.parent / "config" / "region_counties.json")
    if not os.path.isfile(path):
        return {}
    data = _read_json(path)
    result: dict[str, str] = {}
    for zone, payload in (data.get("zones") or {}).items():
        for county in payload.get("counties") or []:
            result[_norm(county)] = zone
            cleaned = re.sub(r"(市|县|区)$", "", _norm(county))
            result.setdefault(cleaned, zone)
    return result


def region_for_county(county: str, lookup: dict[str, str] | None = None) -> str:
    lookup = lookup if lookup is not None else _region_lookup()
    key = _norm(county)
    if not key:
        return ""
    return (lookup.get(key) or lookup.get(re.sub(r"(市|县|区)$", "", key))
            or lookup.get(key + "区", "") or lookup.get(key + "县", ""))


def context_from_report(report: dict, points: dict[str, list[DesignPoint]] | None = None,
                        meta: dict | None = None, county_lookup: dict[str, str] | None = None) -> list[UnitForm]:
    """把一键成果报告转成逐单元表单上下文；缺项留空，不猜值。"""
    meta = dict(meta if meta is not None else (report.get("项目信息") or {}))
    points = points if points is not None else _read_points(report.get("点位清单"))
    lookup = county_lookup if county_lookup is not None else _region_lookup()
    county = _text(meta.get("county") or meta.get("县") or meta.get("县（市、区）"))
    town = _text(meta.get("town") or meta.get("乡") or meta.get("乡（镇、街办）"))
    village = _text(meta.get("village") or meta.get("村"))
    city = _text(meta.get("city") or meta.get("市") or meta.get("市（州）"))
    location = "".join(part for part in (city, county, town, village) if part)
    project_name = _text(meta.get("project_name") or meta.get("项目名称"))
    grid_units = {str(row.get("评价单元")): row for row in (report.get("网格单元明细") or [])}
    result: list[UnitForm] = []
    for row in report.get("单元明细") or []:
        unit = _text(row.get("unit"))
        if not unit:
            continue
        unit_points = points.get(unit, [])
        grid = grid_units.get(unit, {})
        area = row.get("area_m2")
        if area in (None, ""):
            mu = row.get("mu")
            area = round(float(mu) * 666.6666667, 2) if mu not in (None, "") else None
        result.append(UnitForm(
            unit=unit,
            project_name=project_name,
            project_number=_text(meta.get("project_number") or report.get("项目编号")
                                 or (unit[:15] if re.fullmatch(r"[A-Z][0-9]{14}E[0-9]{4}", unit) else "")),
            parcels=[str(item) for item in (row.get("parcels") or [])],
            area_m2=round(float(area), 2) if area not in (None, "") else None,
            location=location,
            land_use=_text(row.get("landtype")),
            region=region_for_county(county, lookup),
            point_count=int(grid.get("设计点数") or len(unit_points)),
            points=unit_points))
    return result


# ---------------------------------------------------------------- 模板拆分与填表

def _body_children(doc):
    return list(doc.element.body.iterchildren())


def _is_paragraph(element, prefix: str) -> bool:
    if not element.tag.endswith("}p"):
        return False
    text = "".join(node.text or "" for node in element.iter() if node.tag.endswith("}t"))
    return text.strip().startswith(prefix)


def _section_bounds(doc, prefix: str, next_prefix: str | None) -> tuple[int, int]:
    """取正文中的章节，跳过目录/内容索引里的同名条目。

    判定：正文标题是该前缀的最后一次出现（目录在前），区间到下一次出现
    ``next_prefix`` 为止；区间内必须有表格。
    """
    children = _body_children(doc)
    starts = [index for index, item in enumerate(children) if _is_paragraph(item, prefix)]
    if not starts:
        raise ValueError(f"模板中找不到章节：{prefix}")
    start = starts[-1]
    end = next((index for index, item in enumerate(children)
                if index > start and _is_paragraph(item, next_prefix or "附录")), None)
    if end is None and next_prefix is None:
        end = len(children)
    if end is None:
        raise ValueError(f"模板中找不到章节结束标记：{next_prefix}")
    if not any(item.tag.endswith("}tbl") for item in children[start:end]):
        raise ValueError(f"章节 {prefix} 与 {next_prefix} 之间没有表格")
    return start, end


def split_templates(source_docx, out_dir) -> dict[str, str]:
    """把整份模板拆成附录 D/E/F 三个单表模板；保留标题、说明与目标表。"""
    docx = _docx()
    source_docx, out_dir = os.path.abspath(source_docx), os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    ranges = {"D": ("附录D", "附录E"), "E": ("附录E", "附录F"), "F": ("附录F", None)}
    outputs: dict[str, str] = {}
    prepared = {}
    for key, (prefix, next_prefix) in ranges.items():
        doc = docx.Document(source_docx)
        start, end = _section_bounds(doc, prefix, next_prefix)
        children = _body_children(doc)
        keep = set(range(start, end))
        sect_pr = [index for index, item in enumerate(children) if item.tag.endswith("}sectPr")]
        keep.update(sect_pr)
        for index, item in enumerate(children):
            if index not in keep:
                doc.element.body.remove(item)
        tables = doc.tables
        if len(tables) != 1:
            raise ValueError(f"附录{key}模板拆分后应有 1 张表，实际 {len(tables)}")
        _trim_trailing_empty(doc)
        prepared[key] = doc
    for key, doc in prepared.items():
        target = os.path.join(out_dir, TEMPLATE_NAMES[key])
        _save_from_template(doc, source_docx, target)
        outputs[key] = target
    return outputs


def _save_from_template(doc, template, output) -> None:
    """只替换正文 XML；原表的样式、关系及不透明部件按原字节保留。"""
    from zipfile import ZipFile
    from lxml.etree import tostring
    if Path(template).resolve() == Path(output).resolve() or (
            os.path.exists(output) and os.path.samefile(template, output)):
        raise ValueError("填表输出不能覆盖原模板")
    xml = tostring(doc.element, xml_declaration=True, encoding="UTF-8", standalone=True)
    with ZipFile(template) as source, ZipFile(output, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, xml if entry.filename == "word/document.xml" else source.read(entry))


def _trim_trailing_empty(doc) -> None:
    """移除表后空段及空白分页，不碰正文里的说明页分隔。"""
    for item in reversed(_body_children(doc)):
        if item.tag.endswith("}sectPr"):
            continue
        if item.tag.endswith("}p") and not any(
                node.text for node in item.iter() if node.tag.endswith("}t")):
            doc.element.body.remove(item)
        else:
            break


def ensure_templates(template_dir=None, source_docx=None) -> dict[str, str]:
    """确保 D/E/F 和标签原表齐全；D/E/F 缺失时从源模板包拆分。"""
    template_dir = os.path.abspath(template_dir or (Path(__file__).resolve().parent.parent / "templates"))
    label_path = os.path.join(template_dir, LABEL_TEMPLATE_NAME)
    if not os.path.isfile(label_path):
        raise FileNotFoundError("表单模板缺失：" + label_path)
    existing = {key: os.path.join(template_dir, name) for key, name in TEMPLATE_NAMES.items()}
    if all(os.path.isfile(path) for path in existing.values()):
        return existing
    if not source_docx or not os.path.isfile(source_docx):
        missing = [path for path in existing.values() if not os.path.isfile(path)]
        raise FileNotFoundError("表单模板缺失：{}；请提供源模板包以拆分".format("、".join(missing)))
    return split_templates(source_docx, template_dir)


def _heading_table(doc, prefix: str):
    seen = False
    for item in _body_children(doc):
        if not seen and _is_paragraph(item, prefix):
            seen = True
            continue
        if seen and item.tag.endswith("}tbl"):
            from docx.table import Table
            return Table(item, doc)
    raise ValueError(f"找不到章节 {prefix} 后的表格")


def _set_cell(cell, value) -> None:
    text = "" if value is None else str(value)
    paragraph = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def _value_after_label(table, label: str):
    """返回标签右侧的值单元格；先精确匹配，避免“利用类型”误中“变更前土地利用类型”。"""
    target = _norm(label)
    if not target:
        return None
    for exact in (True, False):
        for row in table.rows:
            cells = row.cells
            for index, cell in enumerate(cells):
                text = _norm(cell.text)
                matched = text == target if exact else target in text
                if matched:
                    cursor = index + 1
                    while cursor < len(cells) and cells[cursor]._tc is cells[index]._tc:
                        cursor += 1
                    if cursor < len(cells):
                        return cells[cursor]
    return None


def _fill_unit_basics(table, values: list[tuple[str, object]]) -> list[str]:
    """按显式字段清单填表；只把"模板应有但为空"的项计入未填。"""
    missing = []
    for label, value in values:
        cell = _value_after_label(table, label)
        if cell is None:
            missing.append(label)
            continue
        text = PLACEHOLDER if value in (None, "") else str(value)
        _set_cell(cell, text)
        if text == PLACEHOLDER:
            missing.append(label)
    return missing


def _select_region(table, value: str) -> list[str]:
    cell = _value_after_label(table, "农业分区")
    if cell is None or not value or "□" + value not in cell.text:
        return ["农业分区"]
    _set_cell(cell, cell.text.replace("□" + value, "☑" + value))
    return []


def _note_in_table(table, label: str) -> None:
    cell = _value_after_label(table, label)
    if cell is None:
        raise ValueError("模板缺少备注栏：" + label)
    _set_cell(cell, (cell.text.strip() + "\n" if cell.text.strip() else "") + USE_NOTE)


def _ensure_sample_rows(table, count: int) -> None:
    """附录 E 的分样点行不足时复制末行；不删模板原有行。"""
    rows = [row for row in table.rows
            if _norm(row.cells[0].text).startswith("分样点")
            and "地理坐标" in _norm(row.cells[0].text)]
    if not rows:
        return
    existing = len(rows)
    if count <= existing:
        return
    template_row = rows[-1]
    cursor = template_row._tr
    for number in range(existing + 1, count + 1):
        new_tr = copy.deepcopy(template_row._tr)
        cursor.addnext(new_tr)
        cursor = new_tr
        from docx.table import _Row
        new_row = _Row(new_tr, table)
        label = new_row.cells[0]
        _set_cell(label, re.sub(r"分样点[0-9]+", f"分样点{number}", label.paragraphs[0].text))


def _fill_field_form(template: str, output: str, unit: UnitForm) -> list[str]:
    docx = _docx()
    doc = docx.Document(template)
    table = _heading_table(doc, "附录D")
    missing = _fill_unit_basics(table, [
        ("评价单元编号", unit.unit),
        ("项目名称", unit.project_name),
        ("图斑编号", "、".join(unit.parcels)),
        ("面积（m2）", f"{unit.area_m2:.2f}" if unit.area_m2 is not None else ""),
        ("评价单元地理位置", unit.location),
        ("利用类型", unit.land_use),
    ])
    missing += _select_region(table, unit.region)
    _note_in_table(table, "其他备注说明")
    _save_from_template(doc, template, output)
    return missing


def _fill_sample_form(template: str, output: str, unit: UnitForm) -> list[str]:
    docx = _docx()
    doc = docx.Document(template)
    table = _heading_table(doc, "附录E")
    missing = _fill_unit_basics(table, [
        ("土壤混合样品编号", unit.mixed_id),
        ("土壤容重样品编号", unit.bulk_id),
        ("地理位置", unit.location),
        ("分样点数量（个）", unit.point_count or ""),
    ])
    _ensure_sample_rows(table, int(unit.point_count or 0))
    _note_in_table(table, "备注")
    _save_from_template(doc, template, output)
    return missing


def _fill_opinion_form(template: str, output: str, unit: UnitForm) -> list[str]:
    docx = _docx()
    doc = docx.Document(template)
    table = _heading_table(doc, "附录F")
    missing = _fill_unit_basics(table, [
        ("评价单元编号", unit.unit),
        ("项目名称或批次名称", unit.project_name),
        ("评价单元地理位置", unit.location),
        ("评价单元面积（m2）", f"{unit.area_m2:.2f}" if unit.area_m2 is not None else ""),
        ("利用类型", unit.land_use),
    ])
    missing += _select_region(table, unit.region)
    conclusion = _value_after_label(table, "农业生产符合性评价")
    if conclusion is None:
        raise ValueError("附录F模板缺少农业生产符合性评价结论栏")
    _set_cell(conclusion, "")
    rows = [row for row in table.rows if re.fullmatch(r"地块编号[①-⑳0-9]+", _norm(row.cells[0].text))]
    if not rows:
        raise ValueError("附录F模板缺少地块编号行")
    from docx.table import _Row, _Cell
    for number in range(len(rows) + 1, len(unit.parcels) + 1):
        new_tr = copy.deepcopy(rows[-1]._tr)
        rows[-1]._tr.addnext(new_tr)
        new_row = _Row(new_tr, table)
        mark = chr(ord("①") + number - 1) if number <= 20 else str(number)
        _set_cell(new_row.cells[0], "地块编号" + mark)
        rows.append(new_row)
    for index, row in enumerate(rows):
        _set_cell(_Cell(row._tr.tc_lst[1], table), unit.parcels[index] if index < len(unit.parcels) else "")
    _note_in_table(table, "有关情况说明")
    _save_from_template(doc, template, output)
    return missing


# ---------------------------------------------------------------- 标签

def build_labels(units: list[UnitForm], output: str, template=None) -> dict:
    """按原表四行结构排印；每单元 1 张混合样 + 3 张容重平行样。"""
    docx = _docx()
    from docx.enum.text import WD_BREAK
    from docx.table import Table
    template = template or (Path(__file__).resolve().parent.parent / "templates" / LABEL_TEMPLATE_NAME)
    doc = docx.Document(template)
    if len(doc.tables) != 1:
        raise ValueError("标签模板必须包含一张表")
    for label in ("采样地点", "采样日期", "采样人", "采样深度", "土壤名称", "编号"):
        if _value_after_label(doc.tables[0], label) is None:
            raise ValueError("标签模板缺少字段：" + label)
    _trim_trailing_empty(doc)
    pattern = [copy.deepcopy(item) for item in _body_children(doc) if not item.tag.endswith("}sectPr")]
    for item in _body_children(doc):
        if not item.tag.endswith("}sectPr"):
            doc.element.body.remove(item)
    count = 0
    for unit in units:
        for parallel in range(4):
            if count:
                paragraph = doc.add_paragraph()
                if count % 4 == 0:
                    paragraph.add_run().add_break(WD_BREAK.PAGE)
            table = None
            for item in pattern:
                cloned = copy.deepcopy(item)
                doc.element.body.insert(len(doc.element.body) - 1, cloned)
                if cloned.tag.endswith("}tbl"):
                    table = Table(cloned, doc)
            sample_id = unit.bulk_id if parallel else unit.mixed_id
            category = f"容重平行样 {parallel}/3" if parallel else "混合样"
            _set_cell(_value_after_label(table, "编号"), f"{sample_id}（{category}）\n{USE_NOTE}")
            count += 1
    _save_from_template(doc, template, output)
    return {"path": output, "count": count, "units": len(units)}


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-鿿._-]+", "_", value).strip("._") or "unit"


# ---------------------------------------------------------------- 对外入口

def build_forms(report: dict, out_dir, template_dir=None, source_docx=None,
                meta=None) -> dict:
    """从报告字典生成附录 D/E/F 表格与标签；不依赖 arcpy。"""
    out_dir = os.path.abspath(os.fspath(out_dir))
    points = _read_points(report.get("点位清单"))
    units = context_from_report(report, points=points, meta=meta)
    if not units:
        raise ValueError("报告中没有可生成表单的评价单元")
    templates = ensure_templates(template_dir, source_docx)
    form_dir = os.path.join(out_dir, "附件")
    label_dir = os.path.join(out_dir, "标签")
    field_dir = os.path.join(form_dir, "实地踏勘表")
    sample_dir = os.path.join(form_dir, "土壤样品采集记录表")
    opinion_dir = os.path.join(form_dir, "补充耕地质量鉴定意见表")
    for path in (field_dir, sample_dir, opinion_dir, label_dir):
        os.makedirs(path, exist_ok=True)
    manifest = {"生成时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "报告": report.get("成果报告路径") or report.get("报告路径") or "",
                "单元数": len(units),
                "踏勘表": [], "采样记录表": [], "鉴定意见表": [],
                "标签": {}, "未填项": {}, "状态": "PASS"}
    try:
        for unit in units:
            stem = _safe_name(unit.unit)
            field_path = os.path.join(field_dir, f"实地踏勘表_{stem}.docx")
            sample_path = os.path.join(sample_dir, f"土壤样品采集记录表_{stem}.docx")
            opinion_path = os.path.join(opinion_dir, f"补充耕地质量鉴定意见表_{stem}.docx")
            missing = _fill_field_form(templates["D"], field_path, unit)
            missing += _fill_sample_form(templates["E"], sample_path, unit)
            missing += _fill_opinion_form(templates["F"], opinion_path, unit)
            manifest["踏勘表"].append(field_path)
            manifest["采样记录表"].append(sample_path)
            manifest["鉴定意见表"].append(opinion_path)
            if missing:
                manifest["未填项"][unit.unit] = sorted(set(missing))
        manifest["标签"] = build_labels(units, os.path.join(label_dir, "样品标签_设计待确认.docx"),
                                       Path(templates["D"]).parent / LABEL_TEMPLATE_NAME)
        if manifest["未填项"]:
            manifest["状态"] = "NEEDS_REVIEW"
    except Exception as exc:
        manifest["状态"] = "FAIL"
        manifest["错误"] = f"{type(exc).__name__}: {exc}"
    finally:
        manifest_path = os.path.join(out_dir, "外业表格清单.json")
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    manifest["清单"] = manifest_path
    return manifest


def build_forms_from_report(report_path, out_dir, template_dir=None, source_docx=None,
                            meta=None) -> dict:
    """从一键成果报告文件生成表单；供 CLI 与已有任务补做使用。"""
    report_path = os.path.abspath(os.fspath(report_path))
    report = _read_json(report_path)
    report.setdefault("成果报告路径", report_path)
    return build_forms(report, out_dir, template_dir=template_dir,
                       source_docx=source_docx, meta=meta)


def outputs_available(manifest: dict) -> bool:
    """续跑前读回整套 Word 产物，含标签；缺失或损坏时重做表格。"""
    from zipfile import BadZipFile, ZipFile
    from xml.etree.ElementTree import fromstring, ParseError
    if manifest.get("状态") not in ("PASS", "NEEDS_REVIEW"):
        return False
    count = manifest.get("单元数", 0)
    groups = [manifest.get(key) or [] for key in ("踏勘表", "采样记录表", "鉴定意见表")]
    labels = manifest.get("标签") or {}
    if not count or any(len(group) != count for group in groups) or labels.get("count") != count * 4:
        return False
    paths = [path for group in groups for path in group] + [labels.get("path")]
    try:
        for path in paths:
            if not path or not os.path.isfile(path):
                return False
            with ZipFile(path) as archive:
                if archive.testzip():
                    return False
                if fromstring(archive.read("word/document.xml")).tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document":
                    return False
    except (OSError, BadZipFile, KeyError, ParseError, RuntimeError):
        return False
    return True


def write_manifest_html(manifest: dict, output: str) -> str:
    rows = []
    for key, label in (("踏勘表", "实地踏勘表"), ("采样记录表", "土壤样品采集记录表"),
                       ("鉴定意见表", "鉴定意见表")):
        for path in manifest.get(key) or []:
            rows.append(f"<tr><td>{html.escape(label)}</td><td>{html.escape(os.path.basename(path))}</td></tr>")
    tags = manifest.get("标签") or {}
    if tags.get("path"):
        rows.append("<tr><td>样品标签</td><td>{}（{} 张）</td></tr>".format(
            html.escape(os.path.basename(tags["path"])), tags.get("count", 0)))
    missing = manifest.get("未填项") or {}
    missing_html = "".join("<li>{}：{}</li>".format(html.escape(unit), html.escape("、".join(items)))
                           for unit, items in sorted(missing.items())) or "<li>无</li>"
    document = ("<!doctype html><meta charset='utf-8'><title>外业表格清单</title>"
                "<style>body{{font:14px/1.6 sans-serif;margin:24px}}table{{border-collapse:collapse}}"
                "td,th{{border:1px solid #999;padding:4px 8px}}</style>"
                "<h1>外业表格与标签清单</h1><p>状态：{status}；单元数：{count}</p>"
                "<table><tr><th>类别</th><th>文件</th></tr>{rows}</table>"
                "<h2>未填项</h2><ul>{missing}</ul>"
                "<p>说明：预填不等于实采；现场/检测值须人工填写并签字。</p>"
                "<p>汇交前：将每单元 DOCX 打印签字后导出/合并为 "
                "<code>附件/实地踏勘表.pdf</code> 与 <code>附件/土壤样品采集记录表.pdf</code>；"
                "本目录的 DOCX 是编辑底稿，不是最终 PDF。</p>").format(
                    status=html.escape(str(manifest.get("状态"))), count=manifest.get("单元数", 0),
                    rows="".join(rows), missing=missing_html)
    with open(output, "w", encoding="utf-8") as stream:
        stream.write(document)
    return os.fspath(output)
