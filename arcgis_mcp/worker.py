"""ArcGIS Pro Python worker for the arcgis-pro MCP server.

Runs under ArcGIS Pro's Python (3.9 + arcpy). Reads a JSON request file and
writes a JSON result file. Only whitelisted actions are available; there is
no eval/exec or arbitrary Python entry point.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from arcgis_mcp.path_policy import read_roots as _policy_read_roots
from arcgis_mcp.path_policy import within as _path_within
from arcgis_mcp.path_policy import write_roots as _policy_write_roots

ALLOWED_OPTIONS = {
    "id_field", "unit_field", "factor_fields", "max_mu", "gap_m", "allow_multipart",
    "cell_size_m", "min_points", "min_spacing_m", "inset_m", "imagery_raster",
    "imagery_provider", "dpi", "meta", "make_maps", "template", "keep_tiles",
    "scale", "selection_oids", "source_where_clause", "factor_sources",
    "project_number", "prepare_only", "unit_imagery_sources", "task_action",
    "review_confirmation", "profile", "sampling_groups",
}
def _read_roots() -> list[Path]:
    return _policy_read_roots()


def _write_roots() -> list[Path]:
    return _policy_write_roots()


def _roots() -> list[Path]:
    """Compatibility alias for the read scope; it never grants write access."""
    return _read_roots()


def _read_within(path) -> Path:
    return _path_within(path, _read_roots(), "只读", ValueError)


def _write_within(path) -> Path:
    return _path_within(path, _write_roots(), "可写", ValueError)


def _within(path, roots=None) -> Path:
    if roots is not None:
        return _path_within(path, roots, "只读", ValueError)
    return _read_within(path)


def _arcpy():
    import arcpy
    return arcpy


def _sr_info(sr) -> dict:
    if sr is None:
        return {}
    return {"name": getattr(sr, "name", ""), "wkid": getattr(sr, "factoryCode", 0),
            "type": getattr(sr, "type", "")}


def action_pro_info(_params: dict) -> dict:
    arcpy = _arcpy()
    install = dict(arcpy.GetInstallInfo() or {})
    return {"status": "PASS", "python": sys.version.split()[0],
            "arcpy": install.get("Version", ""), "product": install.get("ProductName", ""),
            "install_dir": install.get("InstallDir", ""), "executable": sys.executable}


def action_describe(params: dict) -> dict:
    arcpy = _arcpy()
    path = str(_read_within(params["path"]))
    desc = arcpy.Describe(path)
    datatype = getattr(desc, "dataType", "")
    raster = datatype in ("RasterDataset", "RasterLayer", "RasterBand", "MosaicDataset")
    result = {"status": "PASS", "path": path, "data_type": datatype,
              "shape_type": getattr(desc, "shapeType", ""),
              "spatial_reference": _sr_info(getattr(desc, "spatialReference", None)),
              "extent": [getattr(getattr(desc, "extent", None), key, None)
                         for key in ("XMin", "YMin", "XMax", "YMax")]}
    if raster:
        result["pixel_size"] = [getattr(desc, "meanCellWidth", None),
                                getattr(desc, "meanCellHeight", None)]
        result["band_count"] = getattr(desc, "bandCount", None)
    else:
        result["count"] = int(arcpy.management.GetCount(path)[0])
        result["fields"] = [{"name": field.name, "type": field.type,
                             "length": getattr(field, "length", None),
                             "alias": getattr(field, "aliasName", "")}
                            for field in arcpy.ListFields(path)]
    return result


def action_list_project(params: dict) -> dict:
    arcpy = _arcpy()
    path = str(_read_within(params["aprx"]))
    project = arcpy.mp.ArcGISProject(path)
    maps = []
    for item in project.listMaps():
        layers = []
        for layer in item.listLayers():
            layers.append({"name": layer.name, "long_name": getattr(layer, "longName", ""),
                           "type": type(layer).__name__, "visible": getattr(layer, "visible", None),
                           "data_source": getattr(layer, "dataSource", ""),
                           "definition_query": getattr(layer, "definitionQuery", "")})
        maps.append({"name": item.name, "map_units": getattr(item, "mapUnits", ""),
                     "spatial_reference": _sr_info(getattr(item, "spatialReference", None)),
                     "layers": layers})
    layouts = [{"name": item.name, "page_width": getattr(item, "pageWidth", None),
                "page_height": getattr(item, "pageHeight", None)}
               for item in project.listLayouts()]
    return {"status": "PASS", "aprx": path, "maps": maps, "layouts": layouts}


def _summarize_report(report: dict) -> dict:
    keys = ("技术状态", "业务审核状态", "运行摘要状态", "外业表格状态", "评价单元数",
            "图斑数", "网格", "数据缺口", "待人工确认", "运行摘要", "检查清单",
            "外业表格清单", "成果报告路径", "耗时秒")
    summary = {key: report.get(key) for key in keys if key in report}
    if "外业表格" in report:
        forms = report["外业表格"] or {}
        summary["外业表格"] = {"状态": forms.get("状态"), "单元数": forms.get("单元数"),
                            "标签": forms.get("标签"), "未填项": forms.get("未填项"),
                            "清单": forms.get("清单")}
    return summary


def action_run_oneclick(params: dict) -> dict:
    source = str(_read_within(params["source"]))
    out_dir = str(_write_within(params["out_dir"]))
    options = dict(params.get("options") or {})
    unknown = sorted(set(options) - ALLOWED_OPTIONS)
    if unknown:
        raise ValueError("不支持的 oneclick 参数：{}（允许：{}）".format(
            "、".join(unknown), "、".join(sorted(ALLOWED_OPTIONS))))
    from acceptance import oneclick
    result = oneclick.run(source, out_dir, **options)
    if options.get("task_action") == "check":
        return {"status": result.get("状态", ""), "check": result}
    if options.get("task_action") == "cancel":
        return {"status": result.get("status", ""), "cancel": result}
    return {"status": result.get("技术状态") or result.get("运行摘要状态") or "UNKNOWN",
            "out_dir": out_dir, "report": _summarize_report(result)}


def action_read_run(params: dict) -> dict:
    root = Path(str(_read_within(params["out_dir"])))
    result = {"status": "PASS", "out_dir": str(root)}
    for name, key in (("task_state.json", "task"), ("一键成果报告.json", "report"),
                      ("检查清单.json", "checks"), ("外业表格清单.json", "forms")):
        path = root / name
        if not path.is_file():
            result[key] = {"missing": str(path)}
            continue
        with path.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if name == "一键成果报告.json" and not params.get("full"):
            result[key] = {"path": str(path), "summary": _summarize_report(data)}
        else:
            result[key] = data
    for name, key in (("本次运行摘要.html", "summary_html"),
                      ("外业表格清单.html", "forms_html")):
        path = root / name
        result[key] = {"path": str(path), "exists": path.is_file(),
                       "bytes": path.stat().st_size if path.is_file() else 0}
    if isinstance(result.get("report"), dict):
        result["report_summary"] = _summarize_report(result["report"])
    return result


def action_export_layout(params: dict) -> dict:
    arcpy = _arcpy()
    aprx = str(_read_within(params["aprx"]))
    out = str(_write_within(params["out_path"]))
    project = arcpy.mp.ArcGISProject(aprx)
    layout = next((item for item in project.listLayouts() if item.name == params["layout"]), None)
    if layout is None:
        raise ValueError("找不到布局：{}；可用：{}".format(
            params["layout"], "、".join(item.name for item in project.listLayouts())))
    suffix = Path(out).suffix.lower()
    dpi = int(params.get("dpi") or 200)
    if suffix == ".pdf":
        layout.exportToPDF(out, resolution=dpi)
    elif suffix == ".png":
        layout.exportToPNG(out, resolution=dpi)
    elif suffix in (".jpg", ".jpeg"):
        layout.exportToJPEG(out, resolution=dpi)
    else:
        raise ValueError("只支持 .pdf/.png/.jpg/.jpeg 输出")
    return {"status": "NEEDS_REVIEW", "technical_status":"PASS", "path": out, "bytes": os.path.getsize(out),
            "scope":"原生布局技术导出；未执行验收图件、比例尺或视觉检查"}


def action_render_acceptance_maps(params: dict) -> dict:
    plan_path=str(_read_within(params['plan_path']))
    with open(plan_path,encoding='utf-8') as stream:
        plan=json.load(stream)
    _read_within(plan['project'])
    for target in plan.get('targets',[]):
        name=target.get('name')
        if not isinstance(name,str) or not name.strip() or any(c in name for c in ('/','\\',':')) or name in ('.','..'):
            raise ValueError('图件名称必须为不含路径的文件名')
        for kind in ('jpg','png','pdf'):
            _write_within(target[kind])
        _write_within(str(Path(target['png']).parent/'thumbnails'/(name+'.jpg')))
        for image_path in (target.get('imagery_path'),(target.get('imagery') or {}).get('output')):
            if image_path:
                _read_within(image_path)
    result_path=str(_write_within(os.path.splitext(plan_path)[0]+'.result.json'))
    from acceptance.map_export import export_all
    from acceptance.task_state import _atomic_json
    result=export_all(plan_path)
    _atomic_json(result_path,result)
    return result


def action_run_tests(params: dict) -> dict:
    paths = params.get("paths") or ["tests"]
    resolved = []
    for item in paths:
        raw = Path(item)
        target = (raw if raw.is_absolute() else ROOT / raw).resolve()
        tests_root = (ROOT / "tests").resolve()
        try:
            target.relative_to(tests_root)
        except ValueError:
            raise ValueError("测试路径必须位于工具包 tests/ 下：" + str(target))
        resolved.append(str(target))
    process = subprocess.run([sys.executable, "-m", "pytest", *resolved, "-q"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=1800)
    output = (process.stdout or "") + (process.stderr or "")
    return {"status": "PASS" if process.returncode == 0 else "FAIL",
            "returncode": process.returncode, "tail": output[-8000:]}


ACTIONS = {
    "pro_info": action_pro_info,
    "describe_dataset": action_describe,
    "list_project": action_list_project,
    "run_oneclick": action_run_oneclick,
    "read_run": action_read_run,
    "export_layout": action_export_layout,
    "render_acceptance_maps": action_render_acceptance_maps,
    "run_tests": action_run_tests,
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: worker.py request.json result.json", file=sys.stderr)
        return 2
    request_path, result_path = argv
    with open(request_path, encoding="utf-8") as stream:
        request = json.load(stream)
    action = request.get("action")
    try:
        if action not in ACTIONS:
            raise ValueError("未知 action：{}；允许：{}".format(action, "、".join(sorted(ACTIONS))))
        result = ACTIONS[action](request.get("params") or {})
    except Exception as exc:
        result = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()[-4000:]}
    with open(result_path, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return 0 if result.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
