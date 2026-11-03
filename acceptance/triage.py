"""把一键成果报告聚合成人读摘要与可行动检查清单。

纯 Python，不依赖 arcpy/docx；只读报告字典，不重新计算业务结论。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from urllib.parse import quote

LEVELS = ("FAIL", "NEEDS_REVIEW", "UNVERIFIED", "PASS")
SOURCE_REFERENCES = {
    "system_imagery_precision": {
        "document_id": "system_review_technical_requirements",
        "title": "《补充耕地系统审查技术要求》",
        "physical_pages": (21, 62, 78),
        "scope": "适用系统报备环节",
    },
}


def format_source_reference(reference_id: str) -> str:
    reference = SOURCE_REFERENCES[reference_id]
    pages = "、".join(str(page) for page in reference["physical_pages"])
    return "{}物理页 {}（{}）".format(reference["title"], pages, reference["scope"])


def _add(checks: list[dict], level: str, item: str, evidence: str,
         next_step: str, source: str = "") -> None:
    checks.append({"级别": level, "项目": item, "证据": evidence,
                   "下一步": next_step, "依据": source})


def _project_number(report: dict) -> tuple[str, list[str]]:
    number = str((report.get("数据缺口") or {}).get("项目编号") or "").strip()
    missing = list((report.get("编号计划") or {}).get("missing_project_units") or [])
    if re.fullmatch(r"[A-Z][0-9]{14}", number):
        return number, missing
    return "", missing


def checks_for(report: dict) -> list[dict]:
    checks: list[dict] = []
    crs = report.get("坐标系") or {}
    name, wkid = str(crs.get("名称") or ""), int(crs.get("wkid") or 0)
    if not wkid:
        _add(checks, "FAIL", "坐标系", "报告没有 WKID", "在 Pro 中定义投影后重跑", "§10.2/CGCS2000")
    elif "CGCS2000" in name or wkid in (4490, 4523, 4524, 4525, 4526, 4543, 4544, 4545, 4546, 4547):
        _add(checks, "PASS", "坐标系", f"{name}（WKID {wkid}）", "无需处理", "§10.2/CGCS2000")
    else:
        _add(checks, "NEEDS_REVIEW", "坐标系", f"{name}（WKID {wkid}）不是已识别的 CGCS2000 分带",
             "核对项目资料后再出图", "§10.2")

    fields = report.get("字段") or {}
    if fields.get("图斑编号"):
        _add(checks, "PASS", "图斑编号字段", fields["图斑编号"], "无需处理", "附录C/BSM")
    else:
        _add(checks, "FAIL", "图斑编号字段", "未识别编号字段", "在面板选择图斑编号字段后重跑", "附录C")

    missing_factors = (report.get("数据缺口") or {}).get("四因素缺项") or []
    if missing_factors:
        _add(checks, "NEEDS_REVIEW", "四因素资料", "缺：" + "、".join(missing_factors),
             "补属性字段或提供因素面图层后重跑；当前单元为临时候选", "课件 p31-p32")
    else:
        _add(checks, "PASS", "四因素资料", "四因素字段齐全", "无需处理", "课件 p31")

    number, missing_units = _project_number(report)
    if number and not missing_units:
        _add(checks, "PASS", "系统项目编号", number, "无需处理", "附录C/15位")
    elif number:
        _add(checks, "NEEDS_REVIEW", "系统项目编号", f"{number}，仍有 {len(missing_units)} 个单元缺项目号",
             "在项目参数中填系统验收备案编号后重跑", "附录C/15位")
    else:
        _add(checks, "NEEDS_REVIEW", "系统项目编号", "未取得 15 位系统验收备案编号",
             "从广西占补平衡动态监管系统复制备案编号；不得自编", "附录C")

    units = report.get("网格单元明细") or []
    grid_check = report.get('网格几何核验')
    if grid_check:
        _add(checks, grid_check['status'], '实际网格几何',
             '{}格，实测边长{}–{}m；{}'.format(grid_check['cell_count'], grid_check['width_min_m'],
                 grid_check['width_max_m'], '；'.join(grid_check['failures']) or '方格、中心点、节点及设计点关联一致'),
             '几何失败须重新生成，不能用报告参数代替实际尺寸')
    for row in units:
        quality = row.get('采样质量') or {}
        density = quality.get('平均每点_亩')
        if density is not None:
            nearest = quality.get('最近邻中位_m')
            nearest_text = '不足2点，无法计算' if nearest is None else '{:.2f}m'.format(nearest)
            coverage = quality.get('覆盖半径_m')
            coverage_text = '未计算' if coverage is None else '{:.2f}m'.format(coverage)
            _add(checks, 'NEEDS_REVIEW', '采样密度 ' + str(row['评价单元']),
                 '{}点，平均每点{:.2f}亩；最近邻中位{}；覆盖半径估计{}'.format(
                     quality['设计点数'], density, nearest_text, coverage_text),
                 '按最终设计点与实际单元面积计算；复核代表性，覆盖距离为探针估计')
    for name, pixels in (report.get('图件像元诊断') or {}).items():
        if pixels and pixels.get('status') == 'MEASURED':
            _add(checks, 'NEEDS_REVIEW', '像元采样 ' + str(name),
                 '{:.3f}×{:.3f}m/像元；纸面{:.3f}mm/像元；1:{:g}，{:g}DPI'.format(
                     pixels['output_pixel_x_m'], pixels['output_pixel_y_m'], pixels['paper_pixel_mm'],
                     pixels['scale'], pixels['dpi']), '核对原始GSD与独立定位证据；DPI不作为精度证明')
    unit_rows = report.get("单元明细") or []
    over = [str(row.get("unit")) for row in unit_rows if float(row.get("mu") or 0) > 50.5]
    if over:
        _add(checks, "FAIL", "单元面积上限", "超过 50 亩：" + "、".join(over[:5]),
             "重划或拆分超限单元后重跑", "课件 p31/原则上≤50亩")
    elif any(50.0 < float(row.get("mu") or 0) <= 50.5 for row in unit_rows):
        _add(checks, "NEEDS_REVIEW", "单元面积上限", "存在略高于 50 亩的单元",
             "核对是否属于“原则上”例外并留依据", "课件 p31")
    elif not unit_rows:
        _add(checks, "UNVERIFIED", "单元面积上限", "报告没有单元明细", "重新运行一键流程", "课件 p31")
    else:
        _add(checks, "PASS", "单元面积上限", "单元均不超过 50 亩", "无需处理", "课件 p31")

    missing_cover = [str(row.get("评价单元")) for row in units if row.get("覆盖缺失图斑")]
    if missing_cover:
        _add(checks, "FAIL", "每图斑至少 1 点", "缺图斑覆盖：" + "、".join(missing_cover[:5]),
             "补点或调整网格后重跑", "课件 p33")
    else:
        _add(checks, "PASS", "每图斑至少 1 点", "无缺失图斑", "无需处理", "课件 p33")

    not_enough = [str(row.get("评价单元")) for row in units if row.get("达标") is False]
    if not_enough:
        _add(checks, "FAIL", "每单元最低点数", "未达标：" + "、".join(not_enough[:5]),
             "检查布点数/网格尺寸/避边后重跑", "课件 p33")
    else:
        _add(checks, "PASS", "每单元最低点数", "全部达标", "无需处理", "课件 p33")

    spacing = [str(row.get("评价单元")) for row in units if row.get("间距达标") is False]
    if spacing:
        _add(checks, "NEEDS_REVIEW", "15 米间距", "间距不足：" + "、".join(spacing[:5]),
             "小图斑须全斑覆盖并留审核依据", "课件 p45/一般≥15m")
    else:
        _add(checks, "PASS", "15 米间距", "未发现间距不足", "无需处理", "课件 p45")

    imagery = report.get("报备影像状态") or {}
    level = str(imagery.get("status") or "UNVERIFIED")
    if level not in LEVELS:
        level = "UNVERIFIED"
    _add(checks, level, "报备影像", str(imagery.get("detail") or "报告未给出影像状态"),
         "正式报件须 0.2 m 项目 DOM；在线影像只能自查" if level != "PASS" else "无需处理",
         format_source_reference("system_imagery_precision"))

    forms_status = report.get("外业表格状态")
    if forms_status in ("PASS", "NEEDS_REVIEW", "FAIL"):
        extra = report.get("外业表格") or {}
        missing = extra.get("未填项") or {}
        detail = "已生成 {} 个单元".format(extra.get("单元数", "?"))
        if missing:
            detail += "；未填项：" + "、".join(sorted(missing)[:5])
        _add(checks, forms_status, "外业表格与标签", detail,
             "空白项由现场/检测填写；预填不等于实采" if forms_status != "PASS" else "无需处理",
             "课件 p34")
    else:
        _add(checks, "UNVERIFIED", "外业表格与标签", "报告未包含表格生成结果",
             "运行一键流程或补跑 scripts/build_field_forms.py", "课件 p34")

    pending = list(report.get("待人工确认") or [])
    if pending:
        _add(checks, "NEEDS_REVIEW", "人工复核项", f"共 {len(pending)} 条",
             "逐条核对并在交付清单签字；前三条：" + "；".join(str(item) for item in pending[:3]),
             "§10/汇交")
    else:
        _add(checks, "PASS", "人工复核项", "报告未列待确认项", "仍须按规范完成签审", "§10")
    return checks


def overall(checks: list[dict]) -> str:
    levels = {item["级别"] for item in checks}
    if "FAIL" in levels:
        return "FAIL"
    if levels & {"NEEDS_REVIEW", "UNVERIFIED"}:
        return "NEEDS_REVIEW"
    return "PASS"


def write_summary(report: dict, out_dir) -> dict:
    """写 检查清单.json + 本次运行摘要.html，返回两个路径与总体状态。"""
    out_dir = os.path.abspath(os.fspath(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    checks = checks_for(report)
    status = overall(checks)
    counts = {level: sum(1 for item in checks if item["级别"] == level) for level in LEVELS}
    payload = {"生成时间": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "总体状态": status, "统计": counts, "检查": checks}
    json_path = os.path.join(out_dir, "检查清单.json")
    with open(json_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    html_path = os.path.join(out_dir, "本次运行摘要.html")
    rows = "".join(
        "<tr class='{level}'><td>{level}</td><td>{item}</td><td>{evidence}</td>"
        "<td>{next_step}</td><td>{source}</td></tr>".format(
            level=html.escape(item["级别"]), item=html.escape(item["项目"]),
            evidence=html.escape(item["证据"]), next_step=html.escape(item["下一步"]),
            source=html.escape(item["依据"]))
        for item in checks)
    cards = []
    for output in (report.get("出图") or {}).get("outputs", []):
        title = html.escape(str(output.get("target") or "未命名图件"))
        image = output.get("jpg") or output.get("png")
        thumbnail = output.get("thumbnail") or image
        state = output.get("status", "FAIL")
        label = {"PASS": "自动检查通过", "FAIL": "检查有失败项", "NEEDS_REVIEW": "待复核"}.get(state, "待复核")
        if image and thumbnail and os.path.isfile(image) and os.path.isfile(thumbnail):
            link = quote(os.path.relpath(image, out_dir).replace("\\", "/"), safe="/")
            thumb = quote(os.path.relpath(thumbnail, out_dir).replace("\\", "/"), safe="/")
            content = "<a href='{}'><img src='{}' loading='lazy' alt='{}'><strong>{}</strong></a>".format(
                link, thumb, title, title)
        else:
            content = "<strong>{}</strong><p>未生成可浏览图件</p>".format(title)
        detail = "；".join(str(c.get("detail") or c.get("item") or "") for c in output.get("checks", [])
                          if c.get("status") != "PASS")[:240]
        cards.append("<article>{}<p>{}</p><small>{}</small></article>".format(content, label, html.escape(detail)))
    gallery = "<section class='gallery'>" + "".join(cards) + "</section>" if cards else ""
    document = """<!doctype html><meta charset='utf-8'><title>本次运行摘要</title>
<style>
body{{font:14px/1.6 "Microsoft YaHei",sans-serif;margin:24px;color:#222}}
h1{{font-size:22px}} .badge{{padding:2px 10px;border-radius:4px;color:#fff}}
.PASS{{background:#2e7d32}} .FAIL{{background:#c62828}}
.NEEDS_REVIEW{{background:#ef6c00}} .UNVERIFIED{{background:#6a1b9a}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #bbb;padding:6px 8px;vertical-align:top}}
tr.PASS td:first-child{{color:#2e7d32;font-weight:bold}}
tr.FAIL td:first-child{{color:#c62828;font-weight:bold}}
tr.NEEDS_REVIEW td:first-child{{color:#ef6c00;font-weight:bold}}
tr.UNVERIFIED td:first-child{{color:#6a1b9a;font-weight:bold}}
.small{{color:#666;font-size:12px}} a{{color:#1565c0}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin:20px 0}}
.gallery article{{border:1px solid #dbe2e9;border-radius:8px;padding:10px;background:white}}
.gallery img{{width:100%;height:235px;object-fit:contain;background:#f3f5f7;display:block;margin-bottom:8px}}
.gallery a{{text-decoration:none;color:#17283b}} .gallery p{{margin:6px 0}} .gallery small{{color:#687787}}
summary{{cursor:pointer;margin:18px 0 10px;font-weight:bold}}
</style>
<h1>{purpose} <span class='badge {status}'>{status}</span></h1>
<p>生成时间：{time}　评价单元：{units}　图斑：{parcels}　输出目录：<code>{out}</code></p>
{gallery}
<details><summary>检查明细：失败 {fail} 项，待复核 {pending} 项</summary>
<table><tr><th>级别</th><th>项目</th><th>证据</th><th>下一步</th><th>依据</th></tr>{rows}</table>
</details>
<p class='small'>机器可读清单：<a href='检查清单.json'>检查清单.json</a>
外业表格：<a href='外业表格清单.html'>外业表格清单.html</a></p>
""".format(status=status, time=payload["生成时间"], units=report.get("评价单元数", "?"),
           parcels=report.get("图斑数", "?"), out=html.escape(out_dir),
           fail=counts["FAIL"], review=counts["NEEDS_REVIEW"],
           unverified=counts["UNVERIFIED"], **{"pass": counts["PASS"]}, rows=rows,
           purpose=html.escape(str(report.get("图件用途") or "本次运行摘要")), gallery=gallery,
           pending=counts["NEEDS_REVIEW"] + counts["UNVERIFIED"])
    with open(html_path, "w", encoding="utf-8") as stream:
        stream.write(document)
    return {"运行摘要": html_path, "检查清单": json_path, "运行摘要状态": status}
