"""批量补充耕地验收：一个目录里的每份原始图斑走一遍标准流程。

适用场景：以后会不断收到「某区某年第一批耕地恢复.shp（+dbf/prj）」这类原始基础文件，
每个项目一个子目录，目录里放一份 项目参数.json（排除/合并/点数/面积上限/编制单位等），
本脚本按目录批量输出成果与检查汇总，单个项目失败不影响其他项目。

必须用 ArcGIS Pro 自带的 Python（需要 arcpy），示例中的 <批次目录>/<批次成果> 换成实际路径：
  "<Pro安装目录>\\bin\\Python\\envs\\arcgispro-py3\\python.exe" ^
      land_survey_toolkit/scripts/batch_acceptance.py <批次目录> ^
      --out-root <批次成果> --action preview

参数：
  --action check|prepare|preview   默认 preview（出预览图，最慢也最完整）
  --no-maps                        只算单元/点位，不出图（联调或快速核查用）
  --pattern "*.shp"                默认 *.shp
  --force                          输出目录已存在也重跑（复用同一目录继续）
  --dry-run                        只列出将要处理的项目
  --include / --exclude           额外包含/排除的文件名子串（可多次）

输出：
  <out-root>/<项目名>_成果/          每个项目的一键成果（报告、检查清单、图件）
  <out-root>/批量汇总.json / .csv    全部项目的状态、单元数、FAIL/复核统计
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 插件自己生成的派生图层，不是原始图斑；按文件名整名匹配，避免误伤真实项目
DERIVED_STEMS = {"方格网", "网格中心点", "网格交点", "设计采样点", "评价单元", "图斑与评价单元",
                 "方里网", "区位图", "四因素工作图斑"}


def find_projects(folder: Path, pattern: str, out_root: Path,
                  include: list[str], exclude: list[str]) -> tuple[list[Path], list[tuple[str, str]]]:
    """返回 (项目, 被排除清单+原因)。只排除输出目录与派生图层，不按目录名猜。"""
    projects, skipped = [], []
    out_resolved = out_root.resolve()
    for path in sorted(folder.rglob(pattern)):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(out_resolved)
            skipped.append((str(path), "在输出目录内"))
            continue
        except ValueError:
            pass
        if path.stem in DERIVED_STEMS or path.stem.endswith("_成果"):
            skipped.append((str(path), "插件派生图层"))
            continue
        if exclude and any(word in str(path) for word in exclude):
            skipped.append((str(path), "命中 --exclude"))
            continue
        if include and not any(word in str(path) for word in include):
            skipped.append((str(path), "未命中 --include"))
            continue
        projects.append(path)
    return projects, skipped


def level_counts_from_items(items) -> dict:
    counts: dict = {}
    for item in items or []:
        level = item.get("状态") or item.get("级别") or "?"
        counts[level] = counts.get(level, 0) + 1
    return counts


def level_counts(checklist_path) -> dict:
    if not checklist_path:
        return {}
    try:
        data = json.loads(Path(str(checklist_path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return level_counts_from_items(data.get("检查"))


def summarize(report: dict) -> dict:
    """兼容 preview/prepare（一键成果报告）与 check（资料检查）两种返回。"""
    report = report or {}
    if "技术状态" in report or "评价单元数" in report:
        grid = report.get("网格") or {}
        return {
            "技术状态": report.get("技术状态"),
            "业务审核状态": report.get("业务审核状态"),
            "评价单元数": report.get("评价单元数"),
            "图斑数": report.get("图斑数"),
            "设计点数": grid.get("设计点"),
            "检查统计": level_counts(report.get("检查清单")),
            "报告": report.get("成果报告路径"),
        }
    # check 动作：preflight 返回 {状态, 检查[], 源图斑数}
    return {
        "技术状态": report.get("状态"),
        "业务审核状态": None,
        "评价单元数": None,
        "图斑数": report.get("源图斑数"),
        "设计点数": None,
        "检查统计": level_counts_from_items(report.get("检查")),
        "报告": None,
    }


def _inside(path: Path, parent: Path) -> bool:
    """Python 3.9 没有 Path.is_relative_to（Pro 自带 3.9）。"""
    try:
        path.relative_to(parent)
    except ValueError:  # 不在 parent 之下是正常判断结果，不是错误
        return False
    return True


def output_folder(folder: Path, path: Path) -> str:
    """用相对目录 + 文件名做输出名，避免不同项目同名 shp 互相覆盖。"""
    rel = path.parent.relative_to(folder) if _inside(path.parent, folder) else Path()
    parts = [part for part in rel.parts if part not in (".", "")]
    return "__".join(parts + [path.stem]) if parts else path.stem


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="批量补充耕地验收")
    parser.add_argument("folder", help="含原始图斑 shp 的目录（可含多级项目子目录）")
    parser.add_argument("--out-root", default="", help="成果输出根目录（--dry-run 可省）")
    parser.add_argument("--action", default="preview", choices=["check", "prepare", "preview"])
    parser.add_argument("--no-maps", action="store_true", help="不出图，只算单元与点位")
    parser.add_argument("--pattern", default="*.shp")
    parser.add_argument("--include", action="append", default=[], help="只处理路径含该子串的项目")
    parser.add_argument("--exclude", action="append", default=[], help="跳过路径含该子串的项目")
    parser.add_argument("--force", action="store_true", help="输出已存在也重跑（复用目录继续）")
    parser.add_argument("--tianditu-key", default="", help="天地图 Key；不填时从侧栏底图配置（DPAPI）读取")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def prepare_targets(args):
    """校验输入/输出目录并返回 (folder, out_root)。"""
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        raise SystemExit(f"输入目录不存在：{folder}")
    if not args.out_root and not args.dry_run:
        raise SystemExit("需要 --out-root；只做预演时加 --dry-run")
    out_root = Path(args.out_root).resolve() if args.out_root else folder / "_批量成果"
    if args.out_root and (_inside(folder, out_root) or folder == out_root):
        raise SystemExit("--out-root 必须在输入目录之外（当前 out_root 等于或包含输入目录，扫描会被整体排除）")
    return folder, out_root


def load_tianditu_key(args) -> str:
    """天地图 Key：参数优先，否则从侧栏底图配置（DPAPI）读取，避免命令行暴露密钥。"""
    if args.tianditu_key or args.no_maps:
        return args.tianditu_key
    try:
        from acceptance.tianditu_snapshot import _tianditu_key
        print(">> 已从本机底图配置读取天地图 Key")
        return _tianditu_key()
    except Exception as exc:  # noqa: BLE001 - 取不到时交给插件按缺省处理
        print(f"!! 未取得天地图 Key（{exc}）；在线影像阶段可能失败，可用 --tianditu-key 指定")
        return ""


def run_project(path: Path, folder: Path, out_root: Path, args, oneclick, tianditu_key: str) -> dict:
    """跑单个项目，返回汇总行；单项目失败只记 FAILED，不中断整批。"""
    out_name = output_folder(folder, path)
    out_dir = out_root / f"{out_name}_成果"
    # check 是只读动作（只做资料检查），不因输出目录已存在而跳过
    if args.action != "check" and out_dir.exists() and not args.force:
        print(f"  跳过：{out_dir.name} 已存在（数据更新请加 --force 复用目录继续）")
        return {"项目": out_name, "状态": "SKIPPED", "输出目录": str(out_dir)}
    if args.action != "check" and out_dir.exists() and args.force \
            and not (out_dir / "task_state.json").is_file():
        print(f"  跳过：{out_dir.name} 已存在但没有 task_state.json，--force 无法续跑；请改名或先移走")
        return {"项目": out_name, "状态": "SKIPPED", "输出目录": str(out_dir)}
    started = time.time()
    try:
        report = oneclick.run(str(path), str(out_dir),
                              task_action=args.action,
                              make_maps=not args.no_maps,
                              **({"tianditu_key": tianditu_key} if tianditu_key else {}))
        row = {"项目": out_name, "状态": "OK", "输出目录": str(out_dir),
               "耗时秒": round(time.time() - started, 1)}
        row.update(summarize(report or {}))
        return row
    except Exception as exc:  # noqa: BLE001 - 单项目失败不阻断整批
        traceback.print_exc()
        return {"项目": out_name, "状态": "FAILED", "输出目录": str(out_dir),
                "错误": str(exc)[:500], "耗时秒": round(time.time() - started, 1)}


def write_summary(results: list[dict], out_root: Path) -> int:
    """写 JSON/CSV 汇总，失败项目才返回非零。"""
    summary_json = out_root / "批量汇总.json"
    summary_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_csv = out_root / "批量汇总.csv"
    fields = ["项目", "状态", "技术状态", "业务审核状态", "评价单元数", "图斑数", "设计点数",
              "PASS", "NEEDS_REVIEW", "FAIL", "UNVERIFIED", "耗时秒", "输出目录", "错误"]
    with open(summary_csv, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            flat = dict(row)
            counts = flat.pop("检查统计", None) or {}
            if isinstance(counts, dict):
                for key in ("PASS", "NEEDS_REVIEW", "FAIL", "UNVERIFIED"):
                    flat[key] = counts.get(key, 0)
            writer.writerow(flat)
    ok = sum(1 for row in results if row.get("状态") == "OK")
    skipped_n = sum(1 for row in results if row.get("状态") == "SKIPPED")
    print(f"\n完成 {ok}，跳过 {skipped_n}，失败 {len(results) - ok - skipped_n}；汇总：{summary_json}")
    return 0 if ok + skipped_n == len(results) else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    folder, out_root = prepare_targets(args)
    projects, skipped = find_projects(folder, args.pattern, out_root, args.include, args.exclude)

    print(f"发现 {len(projects)} 个项目（排除 {len(skipped)} 个文件）")
    for path in projects:
        profile = path.parent / "项目参数.json"
        print(f"  - {path.relative_to(folder)}  项目参数：{'有' if profile.is_file() else '缺（默认值）'}")
    if skipped and args.dry_run:
        for path, reason in skipped[:10]:
            print(f"  [排除] {path}（{reason}）")
    if not projects:
        print("没有可处理的项目；如果是目录名或文件名被过滤，用 --include 明确指定")
        return 1
    if args.dry_run:
        return 0

    try:
        import arcpy  # noqa: F401
    except ImportError:
        raise SystemExit("需要在 ArcGIS Pro 自带的 Python 下运行（arcpy 不可用）")
    from acceptance import oneclick

    tianditu_key = load_tianditu_key(args)
    out_root.mkdir(parents=True, exist_ok=True)
    results = []
    for index, path in enumerate(projects, 1):
        print(f"\n[{index}/{len(projects)}] {output_folder(folder, path)}")
        results.append(run_project(path, folder, out_root, args, oneclick, tianditu_key))
    return write_summary(results, out_root)


if __name__ == "__main__":
    sys.exit(main())
