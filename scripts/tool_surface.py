"""工具面摘要：把 ai/tools.json 按「上下文 × 是否只读」分类，生成人读文档。

用途：Agent 选工具前先看权限与副作用，避免把写操作当只读调用；也用于评审工具面是否过大。

用法：
  py -3.12 scripts/tool_surface.py                # 写 docs/tool-surface.md
  py -3.12 scripts/tool_surface.py --json          # 只打印汇总
  py -3.12 scripts/tool_surface.py --check         # 覆盖率检查：工具必须有 context 与 readOnlyHint
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ai" / "tools.json"
OUTPUT = ROOT / "docs" / "tool-surface.md"
CONTEXTS = {"map": "地图上下文（需要活动地图）", "project": "工程上下文（只要打开工程）"}


def load(catalog: Path = CATALOG) -> list[dict]:
    data = json.loads(catalog.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"工具目录为空或格式不对：{catalog}")
    return data


def classify(tools: list[dict]) -> dict:
    """返回 {(context, readonly): [tool, ...]}，并收集缺失元数据的工具。"""
    buckets: dict = {}
    missing = []
    for tool in tools:
        context = (tool.get("_meta") or {}).get("pro-ai-bridge/context")
        readonly = (tool.get("annotations") or {}).get("readOnlyHint")
        if context not in CONTEXTS or not isinstance(readonly, bool):
            missing.append(tool.get("name", "?"))
            continue
        buckets.setdefault((context, readonly), []).append(tool)
    return {"buckets": buckets, "missing": missing}


def render(tools: list[dict], classified: dict) -> str:
    buckets = classified["buckets"]
    lines = ["# 工具面摘要（自动生成，勿手改）", "",
             f"> 来源 `ai/tools.json`，共 {len(tools)} 个工具。生成脚本：`scripts/tool_surface.py`。",
             "",
             "## 总览", "",
             "| 上下文 | 只读 | 写（有副作用） |", "|---|---|---|",
             "| 地图 | {} | {} |".format(
                 len(buckets.get(("map", True), [])), len(buckets.get(("map", False), []))),
             "| 工程 | {} | {} |".format(
                 len(buckets.get(("project", True), [])), len(buckets.get(("project", False), []))),
             ""]
    if classified["missing"]:
        lines += ["## 元数据缺失（必须补齐）", ""]
        lines += [f"- `{name}`：缺 `_meta/context` 或 `annotations/readOnlyHint`"
                  for name in classified["missing"]]
        lines.append("")
    for (context, readonly), items in sorted(buckets.items()):
        title = "只读" if readonly else "写操作（会改数据/工程，须确认）"
        lines += [f"## {CONTEXTS[context]} · {title}", ""]
        for tool in sorted(items, key=lambda item: item.get("name", "")):
            lines.append(f"- `{tool['name']}` — {tool.get('description', '')}")
        lines.append("")
    lines += ["## 使用约定", "",
              "- 只读工具可直接调用；写工具必须先确认目标与范围，禁止自动重试。",
              "- 地图上下文工具需先 `list_maps` / `activate_map` 选定地图。",
              "- 工具面变化后重跑本脚本，并同步 `docs/2026-10-28-unified-acceptance.md` 的工具说明。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成工具面摘要")
    parser.add_argument("--json", action="store_true", help="只打印汇总 JSON")
    parser.add_argument("--check", action="store_true", help="元数据缺失时返回非零")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args(argv)

    tools = load()
    classified = classify(tools)
    summary = {
        "total": len(tools),
        "readonly": sum(len(v) for (_, ro), v in classified["buckets"].items() if ro),
        "writable": sum(len(v) for (_, ro), v in classified["buckets"].items() if not ro),
        "missing_metadata": classified["missing"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render(tools, classified), encoding="utf-8")
        print(f"工具 {summary['total']}：只读 {summary['readonly']}，写 {summary['writable']}；"
              f"文档：{output}")
    if args.check and classified["missing"]:
        print("元数据缺失：" + "、".join(classified["missing"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
