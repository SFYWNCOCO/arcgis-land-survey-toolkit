"""把 docs/courseware/*.md 转成培训 PPTX。

用法：
    python scripts/build_courseware.py [--out <pptx>]

截图：Markdown 中没有真实截图，生成的 PPT 只放文字；需要截图的页在
`待补截图清单` 中列出，必须用真实工具界面补拍，不得伪造。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COURSEWARE = ROOT / "docs" / "courseware"


def _parse(path: Path) -> tuple[str, list[tuple[str, list[str]]]]:
    title = path.stem
    slides: list[tuple[str, list[str]]] = []
    current_title = ""
    bullets: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            if current_title or bullets:
                slides.append((current_title or title, bullets))
            current_title, bullets = line[3:].strip(), []
        elif line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and not all(set(cell) <= set("-: ") for cell in cells):
                bullets.append("｜".join(cells))
        elif line.startswith("- ") or line.startswith("* "):
            bullets.append(line[2:].strip())
        elif re.match(r"^\d+\.\s", line):
            bullets.append(line.strip())
        elif line.startswith("> "):
            bullets.append(line[2:].strip())
    if current_title or bullets:
        slides.append((current_title or title, bullets))
    return title, slides


def build(out_path: Path) -> Path:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise SystemExit("缺少 python-pptx；请在系统 Python 安装后再生成课件") from exc
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
    cover = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = cover.shapes.add_textbox(Inches(0.8), Inches(2.2), Inches(11.7), Inches(1.2))
    frame = box.text_frame
    frame.text = "补充耕地质量鉴定工具培训"
    frame.paragraphs[0].runs[0].font.size = Pt(36)
    frame.paragraphs[0].runs[0].font.bold = True
    sub = cover.shapes.add_textbox(Inches(0.8), Inches(3.5), Inches(11.7), Inches(1.0))
    sub.text_frame.text = f"按广西作业指导书 v1.1 与官方课件模块编排 · {dt.date.today().isoformat()}"
    for path in sorted(COURSEWARE.glob("*.md")):
        _title, slides = _parse(path)
        for slide_title, bullets in slides:
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            heading = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.8))
            heading.text_frame.text = slide_title
            heading.text_frame.paragraphs[0].runs[0].font.size = Pt(26)
            heading.text_frame.paragraphs[0].runs[0].font.bold = True
            body = slide.shapes.add_textbox(Inches(0.7), Inches(1.3), Inches(11.9), Inches(5.8))
            body.text_frame.word_wrap = True
            for index, text in enumerate(bullets[:12]):
                paragraph = body.text_frame.paragraphs[0] if index == 0 else body.text_frame.add_paragraph()
                paragraph.text = "• " + text
                paragraph.font.size = Pt(16 if len(text) < 60 else 13)
            if not bullets:
                body.text_frame.text = "（本页为章节页；截图/演示待补）"
    # 待补截图清单
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    heading = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.8))
    heading.text_frame.text = "待补截图清单（必须用真实界面）"
    heading.text_frame.paragraphs[0].runs[0].font.size = Pt(26)
    body = slide.shapes.add_textbox(Inches(0.7), Inches(1.3), Inches(11.9), Inches(5.8))
    for index, text in enumerate([
        "采样助手面板：图层选择、编号字段、项目参数保存/加载",
        "检查资料结果：PASS/FAIL/NEEDS_REVIEW 列表",
        "地图预览：四套图层与设计点",
        "本次运行摘要：检查项与统计",
        "附件/标签：踏勘表、采样记录表、混合样/容重标签",
        "报告底稿：表 1/表 2 与生成依据",
    ]):
        paragraph = body.text_frame.paragraphs[0] if index == 0 else body.text_frame.add_paragraph()
        paragraph.text = "• " + text
        paragraph.font.size = Pt(16)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(out_path)
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成补充耕地培训课件 PPTX")
    parser.add_argument("--out", help="输出文件，默认 docs/courseware/培训课件_补充耕地质量鉴定.pptx")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else COURSEWARE / "培训课件_补充耕地质量鉴定.pptx"
    print("PPTX", build(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
