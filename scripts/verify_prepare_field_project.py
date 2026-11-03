"""创建不含真实项目资料的离线现场测试项目；在 ArcGIS Pro Python 中执行。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

import arcpy
import arcpy.mp
import numpy as np
from osgeo import gdal, osr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance.map_worker import _blank_project, _style


def create_demo(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("现场测试目录已存在，不覆盖：" + str(output))
    output.mkdir(parents=True)
    sr = arcpy.SpatialReference(4525)
    source = str(output / "合成测试图斑.shp")
    arcpy.management.CreateFeatureclass(str(output), "合成测试图斑.shp", "POLYGON", spatial_reference=sr)
    for name in ("DKBH", "DXBW", "TZMC", "BCGDLY", "DLMC", "XMBH"):
        arcpy.management.AddField(source, name, "TEXT", field_length=80)
    with arcpy.da.InsertCursor(source, ["SHAPE@", "DKBH", "DXBW", "TZMC", "BCGDLY", "DLMC", "XMBH"]) as cursor:
        for index in (0, 1):
            x, y = 36500000 + index * 180, 2550000
            polygon = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + 120, y),
                arcpy.Point(x + 120, y + 120), arcpy.Point(x, y + 120)]), sr)
            cursor.insertRow([polygon, "TEST-{:02d}".format(index + 1), "平地", "合成土种A", "恢复", "旱地", "合成测试项目_不得备案"])
    raster = str(output / "合成纹理测试DOM_不得报件.tif")
    dataset = gdal.GetDriverByName("GTiff").Create(raster, 700, 700, 3, gdal.GDT_Byte,
                                                  options=["COMPRESS=LZW", "TILED=YES"])
    projection = osr.SpatialReference()
    projection.ImportFromEPSG(4525)
    dataset.SetProjection(projection.ExportToWkt())
    dataset.SetGeoTransform((36499800, 1, 0, 2550400, 0, -1))
    rng = np.random.default_rng(20261024)
    yy, xx = np.indices((700, 700))
    rows = ((yy // 9) % 2) * 30 + ((xx // 80) % 2) * 20
    for index, base in enumerate((65, 120, 50), 1):
        array = np.clip(base + rows + rng.integers(-18, 19, (700, 700)), 0, 255).astype("uint8")
        dataset.GetRasterBand(index).WriteArray(array)
    dataset = None
    Path(raster + ".source.json").write_text(json.dumps({"demo_only": True,
        "source_label": "合成纹理测试DOM，不是真实航摄影像", "acquired": "无拍摄日期（计算生成）",
        "license": "本测试包自生成，仅供操作测试；不得报件", "source_gsd_status": "UNVERIFIED"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    project_path = output / "打开这个测试项目.aprx"
    shutil.copy2(_blank_project(), project_path)
    project = arcpy.mp.ArcGISProject(str(project_path))
    mapping = project.listMaps()[0] if project.listMaps() else project.createMap("现场操作测试", "MAP")
    mapping.name = "现场操作测试（合成资料）"
    mapping.spatialReference = sr
    for layer in mapping.listLayers():
        mapping.removeLayer(layer)
    _style(mapping.addDataFromPath(raster), name="合成测试影像（不得报件）")
    _style(mapping.addDataFromPath(source), name="合成测试图斑", fill=(255, 214, 90, 12),
           outline=(210, 30, 30, 100), width=1)
    camera = mapping.defaultCamera
    camera.setExtent(arcpy.Describe(source).extent)
    camera.scale *= 1.5
    mapping.defaultCamera = camera
    project.save()
    del mapping, project
    receipt = {"status": "PASS", "project": project_path.name, "source": Path(source).name, "dom": Path(raster).name,
               "parcels": 2, "expected_units": 2, "minimum_points": 6,
               "coordinate_system_wkid": 4525, "synthetic_only": True, "arcgis_version": arcpy.GetInstallInfo()["Version"]}
    (output / "测试项目说明.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(json.dumps(create_demo(args.out), ensure_ascii=False), flush=True)
