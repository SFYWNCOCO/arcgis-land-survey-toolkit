"""从一键成果报告生成附录 D/E/F 表格与样品标签。

用法：
    python scripts/build_field_forms.py --report <一键成果报告.json> --out <成果目录>
        [--template-dir <templates>] [--source-template <整份表单模板.docx>]
        [--meta meta.json] [--html]

不依赖 arcpy；可在 Pro Python 或系统 Python 运行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from acceptance import forms  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成补充耕地鉴定外业表格与标签")
    parser.add_argument("--report", required=True, help="一键成果报告.json")
    parser.add_argument("--out", required=True, help="成果目录（表格写入 附件/ 与 标签/）")
    parser.add_argument("--template-dir", help="附录D/E/F及表单_样品标签.docx所在目录，默认工具包 templates/")
    parser.add_argument("--source-template", help="缺少单表模板时，从整份表单模板拆分")
    parser.add_argument("--meta", help="项目信息 JSON（project_name/city/county/town/village）")
    parser.add_argument("--html", action="store_true", help="同时生成人读清单 HTML")
    args = parser.parse_args(argv)
    meta = None
    if args.meta:
        with open(args.meta, encoding="utf-8-sig") as stream:
            meta = json.load(stream)
        if not isinstance(meta, dict):
            raise SystemExit("--meta 必须是 JSON 对象")
    manifest = forms.build_forms_from_report(
        args.report, args.out, template_dir=args.template_dir,
        source_docx=args.source_template, meta=meta)
    if args.html:
        manifest["HTML"] = forms.write_manifest_html(
            manifest, os.path.join(args.out, "外业表格清单.html"))
    print(json.dumps({key: manifest.get(key) for key in ("状态", "单元数", "标签", "未填项", "清单")},
                     ensure_ascii=False, indent=2))
    return 0 if manifest.get("状态") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
