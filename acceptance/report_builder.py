"""县级初鉴报告底稿：按附录 L 模板填表 1/表 2，不编造正文与结论。

只填有计算依据的表格与元数据；正文说明保留模板提示，签字盖章与正式结论
必须由专业人员完成。
"""
from __future__ import annotations

import copy
import datetime as dt
import os
from pathlib import Path

from . import forms

_TOOLKIT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = _TOOLKIT / "templates" / "初鉴报告_附录L_模板.docx"
_LEGACY_TEMPLATE = _TOOLKIT / "docs" / "广西补充耕地质量初鉴报告编制模板_修订版_2026-10-20.docx"
if not DEFAULT_TEMPLATE.is_file() and _LEGACY_TEMPLATE.is_file():
    DEFAULT_TEMPLATE = _LEGACY_TEMPLATE


def _docx():
    return forms._docx()


def _set_paragraph(paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def _replace_cover(doc, meta: dict) -> None:
    replacements = {
        "［按正式资料填写］": meta.get("project_name") or "（待填写）",
        "［按系统编号填写］": meta.get("project_number") or "（待填写）",
        "［YYYY年MM月］": meta.get("date") or dt.datetime.now().strftime("%Y年%m月"),
    }
    for paragraph in doc.paragraphs:
        text = paragraph.text
        if not text:
            continue
        for old, new in replacements.items():
            if old in text:
                _set_paragraph(paragraph, text.replace(old, new))
                text = paragraph.text
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text == "承担单位：［填写］":
            _set_paragraph(paragraph, "承担单位：" + (meta.get("org") or "（待填写）"))
        elif text == "第三方服务单位：［填写；无则说明］":
            _set_paragraph(paragraph, "第三方服务单位：" + (meta.get("third_party") or "（待填写；无则说明）"))


def _ensure_rows(table, template_index: int, count: int) -> None:
    """把模板数据行复制到 count 行；不删除原行。"""
    if count <= 1:
        return
    source = table.rows[template_index]._tr
    for _ in range(count - 1):
        source.addnext(copy.deepcopy(source))


def _check_map(result: dict) -> dict:
    return {item["指标"]: item for item in (result or {}).get("checks") or []}


def _fill_compliance_table(table, units: list[dict], results: dict) -> None:
    _ensure_rows(table, 1, len(units))
    for index, unit in enumerate(units, start=1):
        values = _check_map(results.get(unit["unit"]))
        row = table.rows[index]
        mapping = [
            unit["unit"], unit.get("land_use") or "",
            _value(values, "有效土层厚度"), _value(values, "土壤有机质含量"),
            _value(values, "土壤pH"), _value(values, "水资源保障条件"),
            _value(values, "地形坡度"), _value(values, "田面平整程度"),
            _value(values, "土壤侵入体及砾石"), _value(values, "道路通行条件"),
            _value(values, "土壤污染状况"),
            (results.get(unit["unit"]) or {}).get("result", "待判定"),
            "、".join((results.get(unit["unit"]) or {}).get("failures") or []) or "无",
        ]
        for column, value in enumerate(mapping):
            if column < len(row.cells):
                forms._set_cell(row.cells[column], value)


def _value(checks: dict, name: str) -> str:
    item = checks.get(name)
    if not item:
        return "待补"
    value = item.get("实测值")
    if isinstance(value, dict):
        if name == "土壤侵入体及砾石":
            return "{}%（>100mm：{}）".format(value.get("10_100mm_percent", "待补"),
                                             "有" if value.get("over_100mm") else "无")
        if name == "土壤污染状况":
            label = {"PASS": "合格", "FAIL": "不合格", "UNVERIFIED": "无法判定"}.get(
                item.get("判定"), item.get("判定"))
            return "{}（pH {}）".format(label, value.get("ph", "待补"))
        return str(value)
    return str(value)


def _fill_grade_table(table, units: list[dict], grades: dict) -> None:
    _ensure_rows(table, 1, len(units))
    average_row = len(units) + 1
    total_area = sum(float(unit.get("area_mu") or 0) for unit in units)
    weighted_grade = 0.0
    weighted_precise = 0.0
    precise_ok = True
    has_grade = False
    for index, unit in enumerate(units, start=1):
        result = grades.get(unit["unit"]) or {}
        row = table.rows[index]
        area = float(unit.get("area_mu") or 0)
        grade = result.get("ZLDJ")
        precise = result.get("JQZLDJ")
        values = [unit["unit"], "、".join(unit.get("parcels") or []), f"{area:.2f}",
                  str(grade) if grade is not None else "待补",
                  f"{precise:.2f}" if precise is not None else "UNVERIFIED"]
        for column, value in enumerate(values):
            if column < len(row.cells):
                forms._set_cell(row.cells[column], value)
        if grade is not None:
            weighted_grade += float(grade) * area
            has_grade = True
        if precise is not None:
            weighted_precise += float(precise) * area
        else:
            precise_ok = False
    if average_row < len(table.rows):
        row = table.rows[average_row]
        forms._set_cell(row.cells[0], "项目耕地质量平均等级")
        if len(row.cells) > 2:
            forms._set_cell(row.cells[2], f"面积合计 {total_area:.2f} 亩")
        if len(row.cells) > 3:
            forms._set_cell(row.cells[3], f"{weighted_grade / total_area:.2f}"
                            if total_area and has_grade else "待补")
        if len(row.cells) > 4:
            forms._set_cell(row.cells[4], f"{weighted_precise / total_area:.2f}"
                            if total_area and precise_ok else "UNVERIFIED")


def _append_basis(doc, meta: dict, grades: dict, results: dict) -> None:
    doc.add_heading("生成依据与待补清单", level=1)
    doc.add_paragraph("本文件由工具按附录 L 模板生成底稿：表 1/表 2 已填入计算结果；"
                      "正文说明、签字盖章、专家意见与最终结论须由专业人员按正式资料补写。")
    doc.add_paragraph("生成时间：{}".format(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    doc.add_paragraph("项目名称：{}；项目编号：{}".format(
        meta.get("project_name") or "（待填写）", meta.get("project_number") or "（待填写）"))
    doc.add_paragraph("等级参数：config/grading_parameters.json（指导书 v1.1 附录 K.1–K.5）；"
                      "两位小数等级为区间线性插值，状态 UNVERIFIED，待与三普规范附录 J 原公式核对。")
    pending = []
    for unit, result in sorted(results.items()):
        if result.get("status") != "PASS":
            pending.append("{}：符合性 {}".format(unit, result.get("status")))
    for unit, result in sorted(grades.items()):
        if result.get("status") != "PASS" or result.get("JQZLDJ") is None:
            pending.append("{}：等级 {}".format(unit, result.get("status")))
    doc.add_paragraph("待补/待核：" + ("；".join(pending) if pending else "无（仍需人工审核）"))


def build_report(units: list[dict], results: dict, grades: dict, output,
                 template_path=None, meta=None) -> dict:
    """生成县级初鉴报告底稿；units 需含 unit/parcels/area_mu/land_use。"""
    docx = _docx()
    template_path = str(template_path or DEFAULT_TEMPLATE)
    if not os.path.isfile(template_path):
        raise FileNotFoundError("初鉴报告模板不存在：" + template_path)
    meta = dict(meta or {})
    doc = docx.Document(template_path)
    _replace_cover(doc, meta)
    if len(doc.tables) < 2:
        raise ValueError("初鉴报告模板应至少包含表 1/表 2")
    _fill_compliance_table(doc.tables[0], units, results)
    _fill_grade_table(doc.tables[1], units, grades)
    _append_basis(doc, meta, grades, results)
    output = os.path.abspath(os.fspath(output))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    doc.save(output)
    return {"path": output, "units": len(units),
            "status": "NEEDS_REVIEW",
            "note": "底稿已填表；正文说明、签字与正式结论未生成"}
