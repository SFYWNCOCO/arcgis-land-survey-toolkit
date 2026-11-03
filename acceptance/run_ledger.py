"""任务运行账本：每次验收任务追加一条 JSONL，供时延、结果与成本统计。

只记录排障和评估需要的字段：动作、状态、单元/图斑/设计点数、检查计数、
耗时、人工确认与错误摘要。不记录密钥、影像内容或完整绝对路径（只留目录名）。

账本目录默认 `%LOCALAPPDATA%/SamplingAssistant/ledger.jsonl`，可用环境变量
`LAND_SURVEY_LEDGER` 指向别处。写账本失败不影响主流程，但会在返回里说明。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_ERROR_CHARS = 300


def ledger_path(root=None):
    """账本文件路径：显式参数 > 环境变量 > 本机 LOCALAPPDATA。"""
    if root:
        return Path(root)
    override = os.environ.get("LAND_SURVEY_LEDGER")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "SamplingAssistant" / "ledger.jsonl"


def _counts(report):
    """从一键成果报告的检查清单路径读取 PASS/FAIL 等计数。"""
    path = (report or {}).get("检查清单")
    if not path:
        return {}
    try:
        data = json.loads(Path(str(path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    counts = {}
    for item in data.get("检查", []):
        level = item.get("级别") or "?"
        counts[level] = counts.get(level, 0) + 1
    return counts


def build_entry(report, *, action, elapsed_s, status="", error="", extras=None):
    """把一次任务的报告压成账本条目（纯函数，便于测试）。"""
    report = report or {}
    grid = report.get("网格") or {}
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
        "status": status or report.get("技术状态") or "",
        "business_status": report.get("业务审核状态") or "",
        "run_id": report.get("任务运行ID") or "",
        "input_fingerprint": report.get("输入指纹") or "",
        "task_dir": Path(str(report.get("成果报告路径") or "")).parent.name
        if report.get("成果报告路径") else "",
        "units": report.get("评价单元数"),
        "parcels": report.get("图斑数"),
        "points": grid.get("设计点"),
        "checks": _counts(report),
        "elapsed_s": round(float(elapsed_s or 0), 1),
        "manual_confirmed": bool(report.get("设计确认")),
        "error": (str(error) or "")[:MAX_ERROR_CHARS],
    }
    if extras:
        entry.update({key: value for key, value in extras.items() if key not in entry})
    return entry


def record(report, *, action, elapsed_s, status="", error="", extras=None, root=None):
    """追加一条账本；返回写入路径，失败返回 None（不抛给主流程）。"""
    entry = build_entry(report, action=action, elapsed_s=elapsed_s,
                        status=status, error=error, extras=extras)
    try:
        path = ledger_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None


def summarize(path=None):
    """按动作汇总账本：任务数、成功/失败、平均与 P95 耗时。供人工或评估脚本查看。"""
    path = ledger_path(path)
    rows = []
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, ValueError):
        return {"账本": str(path), "任务数": 0, "说明": "账本不存在或无法解析"}
    by_action = {}
    for row in rows:
        bucket = by_action.setdefault(row.get("action", "?"), {"任务数": 0, "失败": 0, "耗时": []})
        bucket["任务数"] += 1
        if row.get("status") == "FAILED":
            bucket["失败"] += 1
        bucket["耗时"].append(float(row.get("elapsed_s") or 0))
    for bucket in by_action.values():
        times = sorted(bucket.pop("耗时"))
        bucket["耗时均值_s"] = round(sum(times) / len(times), 1) if times else 0
        bucket["P95_s"] = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0
    return {"账本": str(path), "任务数": len(rows), "按动作": by_action}
