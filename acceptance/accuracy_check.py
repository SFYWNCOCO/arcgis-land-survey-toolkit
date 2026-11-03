"""影像位置精度检核：用检查点（RTK/控制点）算残差，生成一页精度记录（HTML）。

口径：
- 检查点必须是影像上的同名点；本工具只按邻近/控制点号配对，配对是否同名须人审。
- 残差 = 检查点实测坐标 − 影像上同名点采集坐标（米，平面）。
- RMSE 按全部配对的平面残差平方根计算；中误差口径是否可接受由业务方确认。
- 本页只是计算记录，不自动证明满足任何报备精度门槛；是否被接受由业务方确认。
"""
from __future__ import annotations

import csv
import datetime
import html
import json
import math
import os

THRESHOLD_M = 0.2


def _number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("{}不是有效数字：{!r}".format(label, value)) from None
    if not math.isfinite(result):
        raise ValueError("{}不是有限值：{!r}".format(label, value))
    return result


def correspondence_residuals(checks, images):
    """按 ID 配对检查点与影像同名点，返回逐对残差。

    checks: [{"id", "x", "y"}]；images: [{"id", "x", "y"}]；均为同一投影坐标系的米。
    重复 ID、配对不上的 ID 都直接报错，不静默丢弃。
    """
    seen, by_id = set(), {}
    for row in checks:
        key = str(row.get("id") or "").strip()
        if not key:
            raise ValueError("检查点缺少编号")
        if key in seen:
            raise ValueError("检查点编号重复：{}".format(key))
        seen.add(key)
        by_id[key] = (_number(row.get("x"), key + " X"), _number(row.get("y"), key + " Y"))
    seen_image = set()
    image_by_id = {}
    for row in images:
        key = str(row.get("id") or "").strip()
        if key in seen_image:
            raise ValueError("影像同名点编号重复：{}".format(key))
        seen_image.add(key)
        image_by_id[key] = (_number(row.get("x"), key + " 影像X"), _number(row.get("y"), key + " 影像Y"))
    only_check = sorted(set(by_id) - set(image_by_id))
    only_image = sorted(set(image_by_id) - set(by_id))
    if only_check or only_image:
        raise ValueError("检查点与影像同名点未一一配对：检查点缺影像 {}；影像缺检查点 {}".format(
            "、".join(only_check) or "无", "、".join(only_image) or "无"))
    rows = []
    for key in sorted(by_id):
        dx = by_id[key][0] - image_by_id[key][0]
        dy = by_id[key][1] - image_by_id[key][1]
        rows.append({"id": key, "dx": round(dx, 4), "dy": round(dy, 4),
                     "residual": round(math.hypot(dx, dy), 4)})
    return rows


def nearest_correspondence(checks, image_points, max_distance_m=None):
    """无配对号时按最近距离配对；每对都记录距离，由人审判断是否同名。

    image_points: [{"label", "x", "y"}]（在影像上人工采的点）。
    """
    rows, seen, used_points = [], set(), {}
    for check in checks:
        key = str(check.get("id") or "").strip()
        if not key:
            raise ValueError("检查点缺少编号")
        if key in seen:
            raise ValueError("检查点编号重复：{}".format(key))
        seen.add(key)
        cx, cy = _number(check.get("x"), key + " X"), _number(check.get("y"), key + " Y")
        best = sorted(((math.hypot(cx - _number(p.get("x"), "影像点 X"),
                                   cy - _number(p.get("y"), "影像点 Y")), p) for p in image_points),
                      key=lambda item: item[0])
        if not best:
            raise ValueError("没有任何影像同名点可供配对：{}".format(key))
        distance, point = best[0]
        if max_distance_m is not None and distance > float(max_distance_m):
            raise ValueError("检查点 {} 最近的影像点超过 {} m，疑似采错位置".format(key, max_distance_m))
        label = str(point.get("label") or "{},{}".format(point.get("x"), point.get("y")))
        if label in used_points:
            raise ValueError("检查点 {} 与 {} 重复使用同一影像点（{}）；这会虚增样本".format(
                key, used_points[label], label))
        used_points[label] = key
        dx, dy = cx - _number(point.get("x"), "影像点 X"), cy - _number(point.get("y"), "影像点 Y")
        rows.append({"id": key, "dx": round(dx, 4), "dy": round(dy, 4), "residual": round(distance, 4),
                     "paired_image_point": str(point.get("label") or "")})
    return rows


MIN_CHECK_POINTS = 20  # 建议值，来自测绘评审意见，不是规范明文；可在调用处覆盖


def statistics(rows, threshold_m=THRESHOLD_M, min_check_points=MIN_CHECK_POINTS):
    """逐对残差统计；空输入返回 None 值，不把“无检查点”报成 0 残差。

    样本数低于 min_check_points 判 INSUFFICIENT：残差为 0 也要先说清“样本不够”，
    避免一两个点算出来的 PASS 被当成精度证明。
    """
    if not rows:
        return {"检查点数": 0, "最小检查点数": int(min_check_points), "RMSE_m": None,
                "平面中误差_m": None, "最大残差_m": None,
                "阈值_m": float(threshold_m), "超限点数": None, "状态": "UNTESTABLE"}
    residuals = [float(row["residual"]) for row in rows]
    rmse_x = math.sqrt(sum(float(row["dx"]) ** 2 for row in rows) / len(rows))
    rmse_y = math.sqrt(sum(float(row["dy"]) ** 2 for row in rows) / len(rows))
    rmse = math.sqrt(sum(value ** 2 for value in residuals) / len(rows))
    over = [row["id"] for row in rows if float(row["residual"]) >= float(threshold_m)]
    return {
        "检查点数": len(rows),
        "RMSE_m": round(rmse, 4),
        "X向RMSE_m": round(rmse_x, 4),
        "Y向RMSE_m": round(rmse_y, 4),
        # 平面中误差＝平面 RMSE（√(ΣΔ²/n)）；若业务方要按等权中误差 √(ΣΔ²/(2n)) 计，须另行确认
        "平面中误差_m": round(rmse, 4),
        "最大残差_m": round(max(residuals), 4),
        "阈值_m": float(threshold_m),
        "超限点数": len(over),
        "超限点号": over,
        "最小检查点数": int(min_check_points),
        "状态": ("INSUFFICIENT" if len(rows) < int(min_check_points)
                 else "FAIL" if over else "PASS"),
    }


def read_csv(path, x_field, y_field, id_field):
    """读检查点/影像点 CSV；表头缺失或空行直接报错。"""
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        for field in (x_field, y_field, id_field):
            if field not in headers:
                raise ValueError("CSV 缺少字段：{}（现有：{}）".format(field, "、".join(headers)))
        rows = []
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            rows.append({"id": row[id_field], "x": row[x_field], "y": row[y_field],
                         "label": row.get("LABEL") or row.get("点名") or row[id_field]})
    if not rows:
        raise ValueError("CSV 没有检查点数据行：" + str(path))
    return rows


def render_html(rows, stats, context):
    """生成一页精度记录 HTML；所有字段转义。"""
    title = "影像位置精度检查记录"
    lines = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>{}</title><style>body{{font-family:'Microsoft YaHei',sans-serif;margin:2em;}}"
        "table{{border-collapse:collapse;}}td,th{{border:1px solid #888;padding:4px 8px;}}"
        ".FAIL{{color:#b00;font-weight:bold}}.PASS{{color:#070;font-weight:bold}}"
        ".UNTESTABLE{{color:#a60;font-weight:bold}}</style></head><body>".format(title),
        "<h1>{}</h1>".format(title),
        "<p>生成时间：{}；本页为软件计算记录，不是业务审核结论。</p>".format(
            html.escape(str(context.get("generated_at") or ""))),
        "<h2>数据来源</h2><table>",
    ]
    for key in ("影像", "影像坐标系", "原始GSD_m", "检查点文件", "影像点文件", "配对方式", "检查点来源"):
        lines.append("<tr><th>{}</th><td>{}</td></tr>".format(key, html.escape(str(context.get(key) or "未提供"))))
    lines.append("</table><h2>统计</h2><table>")
    for key in ("检查点数", "RMSE_m", "X向RMSE_m", "Y向RMSE_m", "平面中误差_m", "最大残差_m", "阈值_m", "超限点数"):
        value = stats.get(key)
        lines.append("<tr><th>{}</th><td>{}</td></tr>".format(key, "未计算" if value is None else html.escape(str(value))))
    status = str(stats.get("状态") or "UNTESTABLE")
    lines.append("<tr><th>状态</th><td class='{}'>{}</td></tr></table>".format(status, status))
    lines.append("<p>口径：残差＝检查点实测坐标－影像同名点坐标；RMSE＝√(Σ平面残差²/n)；"
                 "平面中误差按同一公式记录，是否采用等权中误差口径须业务方确认。"
                 "同名点配对是否成立、影像时相与来源是否可接受，均须人工复核。</p>")
    lines.append("<h2>逐点残差</h2><table><tr><th>点号</th><th>ΔX(m)</th><th>ΔY(m)</th><th>平面残差(m)</th><th>影像点</th><th>超限</th></tr>")
    threshold = float(stats.get("阈值_m") or THRESHOLD_M)
    for row in rows:
        over = float(row["residual"]) >= threshold
        lines.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row["id"])), row["dx"], row["dy"], row["residual"],
            html.escape(str(row.get("paired_image_point") or "")), "是" if over else "否"))
    lines.append("</table></body></html>")
    return "\n".join(lines)


def write_record(rows, stats, context, out_html, out_json):
    """写 HTML 与 JSON；已存在则拒绝覆盖，半成品不落盘。"""
    for path in (out_html, out_json):
        if os.path.exists(path):
            raise FileExistsError("精度记录已存在，不覆盖：" + str(path))
    context = dict(context or {}, generated_at=datetime.datetime.now().isoformat(timespec="seconds"))
    page = render_html(rows, stats, context)
    payload = {"rows": rows, "statistics": stats, "context": context,
               "note": "计算记录；不自动证明满足报备精度门槛，是否被接受由业务方确认"}
    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as stream:
        stream.write(page)
    with open(out_json, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return payload


def load_points(source, arcpy, id_field, label=None):
    """从点图层读编号与坐标（须与检查点同一投影坐标系，单位米）。

    label: (x, y) -> 描述；缺省空。空几何直接报错。
    """
    sr = arcpy.Describe(source).spatialReference
    if str(getattr(sr, "name", "Unknown")) == "Unknown":
        raise ValueError("点图层坐标系未知，无法计算残差")
    if str(getattr(sr, "type", "")).casefold() != "projected":
        raise ValueError("残差以米计；点图层必须是投影坐标系")
    meters_per_unit = float(getattr(sr, "metersPerUnit", 0) or 0)
    if not meters_per_unit > 0:
        raise ValueError("无法读取点图层线性单位换算")
    rows = []
    with arcpy.da.SearchCursor(source, [id_field, "SHAPE@XY"]) as cursor:
        for key, (x, y) in cursor:
            if x is None or y is None:
                raise ValueError("点 {} 几何为空".format(key))
            rows.append({"id": key, "x": x * meters_per_unit, "y": y * meters_per_unit,
                         "label": label(x, y) if label else None})
    if not rows:
        raise ValueError("点图层没有记录：" + str(source))
    return rows


def build_record(checks, images, context, out_html, out_json, pairing="id", max_distance_m=None):
    """配对 → 统计 → 写记录。pairing 为 id 或 nearest。"""
    if pairing == "nearest":
        rows = nearest_correspondence(checks, images, max_distance_m=max_distance_m)
    elif pairing == "id" and max_distance_m is None:
        rows = correspondence_residuals(checks, images)
    else:
        raise ValueError("配对方式必须是 id 或 nearest；nearest 可带最大距离，id 不带")
    return write_record(rows, statistics(rows), context, out_html, out_json)
