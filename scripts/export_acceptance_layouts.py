"""Refresh explicit numeric scale labels and export the acceptance project's two layouts.

Run with ArcGIS Pro's Python environment. The scale label is written from the live
map-frame scale immediately before export so the image and saved APRX agree.
"""
import argparse
from pathlib import Path

import arcpy

LAYOUTS = {
    "A4 Portrait": "采样点位布置图.jpg",
    "A4 Portrait1": "地理位置图.jpg",
}
MAP_FRAME_NAME = "WEBMAP_MAP_FRAME"
SCALE_TEXT_NAME = "Text"
MAP_UNIT_NAME = "制图单位"
SAMPLE_MAP_UNIT = "制图单位：模拟项目样例"


def export_layouts(aprx_path: Path, output_dir: Path) -> list[dict]:
    aprx = arcpy.mp.ArcGISProject(str(aprx_path))
    layouts = {layout.name: layout for layout in aprx.listLayouts()}
    missing = set(LAYOUTS) - set(layouts)
    if missing:
        raise RuntimeError("缺少预期布局：{}".format(sorted(missing)))

    output_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    for layout_name, filename in LAYOUTS.items():
        layout = layouts[layout_name]
        frames = [e for e in layout.listElements("MAPFRAME_ELEMENT") if e.name == MAP_FRAME_NAME]
        labels = [e for e in layout.listElements("TEXT_ELEMENT") if e.name == SCALE_TEXT_NAME]
        if len(frames) != 1 or len(labels) != 1:
            raise RuntimeError(
                "布局 {} 的地图框/比例尺文本数量不唯一：{}/{}".format(
                    layout_name, len(frames), len(labels)
                )
            )
        scale = float(frames[0].camera.scale)
        if not scale or scale <= 0:
            raise RuntimeError("布局 {} 的比例尺无效：{}".format(layout_name, scale))
        rounded_scale = int(round(scale))
        labels[0].text = "比例尺：1:{:,}".format(rounded_scale)
        # The project template contains these elements but leaves them hidden.
        # Make the required scale explicit on both maps and show the map unit on
        # the sampling map; location-map unit/date are already in its visible
        # metadata line.
        labels[0].visible = True
        if layout_name == "A4 Portrait":
            unit_labels = [
                e for e in layout.listElements("TEXT_ELEMENT")
                if e.name == MAP_UNIT_NAME
            ]
            if len(unit_labels) != 1:
                raise RuntimeError(
                    "布局 {} 的制图单位文本数量不唯一：{}".format(
                        layout_name, len(unit_labels)
                    )
                )
            unit_labels[0].text = SAMPLE_MAP_UNIT
            unit_labels[0].visible = True

        output_path = output_dir / filename
        if output_path.exists():
            raise FileExistsError("拒绝覆盖已有输出：{}".format(output_path))
        layout.exportToJPEG(str(output_path), resolution=300, jpeg_quality=95)
        if not output_path.is_file() or output_path.stat().st_size < 100_000:
            raise RuntimeError("布局导出未生成有效图件：{}".format(output_path))
        receipts.append({
            "layout": layout_name,
            "map_frame_scale": scale,
            "scale_label": labels[0].text,
            "output": str(output_path),
            "bytes": output_path.stat().st_size,
        })

    # Persist the refreshed labels only after both exports succeed. Do not call
    # layout APIs after save() in this process (ArcGIS Pro 3.1 constraint).
    aprx.save()
    return receipts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aprx", required=True, type=Path, help="ArcGIS Pro project")
    parser.add_argument("--output-dir", required=True, type=Path, help="New/empty output directory")
    args = parser.parse_args()
    if not args.aprx.is_file():
        parser.error("APRX not found: {}".format(args.aprx))
    if not args.output_dir.is_absolute():
        parser.error("output-dir must be an absolute path")
    for item in export_layouts(args.aprx, args.output_dir):
        print("{layout}\t{scale_label}\t{bytes}\t{output}".format(**item))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
