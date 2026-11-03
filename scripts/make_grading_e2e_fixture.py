"""生成分级赋值端到端验证用的最小要素类（ArcGIS Pro Python 运行）。

不碰项目数据：全部写到 temp/grading_e2e/ 下的独立 gdb。
刻意造出这些情形，用来验证工具是否按契约处理：
    正常值 / 空值 / "未检出" / 限值写法 "<0.5" / 非数值 "abc"
    字段名带批次后缀（EXC_CA_2026）→ 测前缀匹配
    同前缀多字段（TN_2023、TN_2024）→ 测歧义是否被拦住
"""
from __future__ import annotations

import shutil
from pathlib import Path

import arcpy

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "temp" / "grading_e2e"
GDB = OUT / "grading_e2e.gdb"
FC = "yldy_grade"

ROWS = [
    # BSM,        OM,    pH,   EXC_CA_2026, SALINITY, TN_2023, TN_2024
    ("T0001", 40.0, 6.5, 1600.0, 0.8, 2.1, 2.2),
    ("T0002", 34.0, 6.2, 900.0, 2.0, 1.9, 1.8),
    ("T0003", 22.0, 5.0, 300.0, 7.0, 1.2, 1.1),
    ("T0004", None, 6.0, 120.0, 4.0, None, None),
    ("T0005", "未检出", "<4.5", "<0.5", "abc", 0.9, 0.9),
    ("T0006", 30.0, 7.0, 500.0, 1.0, 2.0, 2.0),
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    arcpy.management.CreateFileGDB(str(OUT), GDB.name)
    sr = arcpy.SpatialReference(4526)  # CGCS2000 3-degree GK CM 108E
    arcpy.management.CreateFeatureclass(str(GDB), FC, "POINT", spatial_reference=sr)
    for name, ftype in (("BSM", "TEXT"), ("OM", "TEXT"), ("pH", "TEXT"),
                        ("EXC_CA_2026", "TEXT"), ("SALINITY", "TEXT"),
                        ("TN_2023", "TEXT"), ("TN_2024", "TEXT")):
        arcpy.management.AddField(str(GDB / FC), name, ftype, field_length=20)
    fields = ["BSM", "OM", "pH", "EXC_CA_2026", "SALINITY", "TN_2023", "TN_2024"]
    # 实测值按文本存：化验数据常带 "未检出"/"<0.5" 这类原始写法，正好验证解析口径
    with arcpy.da.InsertCursor(str(GDB / FC), ["SHAPE@"] + fields) as cur:
        for i, row in enumerate(ROWS):
            pt = arcpy.Point(36489000 + i * 30, 2527900 + i * 20)
            cur.insertRow([arcpy.PointGeometry(pt, sr)] + [None if v is None else str(v) for v in row])
    print("E2E_READY", GDB / FC, "rows", len(ROWS))


if __name__ == "__main__":
    main()
