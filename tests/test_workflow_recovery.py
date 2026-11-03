"""在安装的 ArcGIS Pro Python 中验证阶段恢复；系统 Python 自动跳过。"""
from pathlib import Path
import sys

import pytest


arcpy = pytest.importorskip("arcpy")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acceptance import grid_sampling as G
from acceptance import oneclick as O
from acceptance import task_state as T
from acceptance import sampling_qa as QA


@pytest.fixture
def nine_parcels(tmp_path):
    arcpy.management.CreateFileGDB(str(tmp_path), "input.gdb")
    gdb = str(tmp_path / "input.gdb")
    sr = arcpy.SpatialReference(4525)
    fc = str(tmp_path / "input.gdb" / "parcels")
    arcpy.management.CreateFeatureclass(gdb, "parcels", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(fc, "DKBH", "TEXT", field_length=30)
    arcpy.management.AddField(fc, "PJDYBH", "TEXT", field_length=30)
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "DKBH", "PJDYBH"]) as cursor:
        for index in range(9):
            x, y = 36500000 + (index % 3) * 120, 2550000 + (index // 3) * 120
            geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y), arcpy.Point(x + 80, y),
                                                  arcpy.Point(x + 80, y + 80), arcpy.Point(x, y + 80)]), sr)
            cursor.insertRow([geometry, "P{:02d}".format(index + 1), "U{:02d}".format(index + 1)])
    return fc


@pytest.mark.parametrize("minimum", [1, 3])
def test_manual_groups_use_one_design_and_never_create_formal_numbers(nine_parcels, tmp_path, minimum):
    output = str(tmp_path / "manual_groups")
    report = O.run(nine_parcels, output, make_maps=False, min_points=minimum, sampling_groups=[
        {"group_id": "SG_pair", "parcels": ["P01", "P02"], "basis": "用户确认设计组"}])
    assert report["图斑数"] == 9 and report["评价单元数"] == 8
    assert report["网格"]["设计点"] == 24
    assert report["人工采样分组"]["formal_unit_mapping"] == {}
    assert report["数据缺口"]["四因素缺项"]
    state = T.read_task(output)
    data = state["phases"]["data_prepared"]["detail"]
    assert data["numbering"]["mapping"] == {}
    assert len({u for (u,) in arcpy.da.SearchCursor(data["assign"], ["SAMP_GRP"])}) == 8
    if minimum == 1:
        repeated = O.run(data["assign"], str(tmp_path / "from_prepared_layer"), make_maps=False,
                         min_points=minimum, sampling_groups=[
                             {"group_id": "SG_pair", "parcels": ["P01", "P02"], "basis": "用户确认设计组"}])
        assert repeated["网格"]["设计点"] == 24
    if minimum == 3:
        changed = O.run(nine_parcels, output, make_maps=False, min_points=minimum,
                        sampling_groups=[], task_action="resume")
        updated = T.read_task(output)["phases"]["data_prepared"]["detail"]
        assert updated["gdb"] != data["gdb"]
        assert changed["网格"]["设计点"] == 27 and changed["评价单元数"] == 9


def test_resume_reuses_data_after_sampling_failure(nine_parcels, tmp_path, monkeypatch):
    output = str(tmp_path / "sampling_failure")
    original = G.grid_design
    def fail_sampling(*_args, **_kwargs):
        raise RuntimeError("injected sampling interruption")
    monkeypatch.setattr(G, "grid_design", fail_sampling)
    with pytest.raises(RuntimeError, match="injected"):
        O.run(nine_parcels, output, make_maps=False)
    first = T.read_task(output)
    assert first["status"] == "FAILED"
    original_data = first["phases"]["data_prepared"]["detail"]["gdb"]
    monkeypatch.setattr(G, "grid_design", original)
    report = O.run(nine_parcels, output, make_maps=False, task_action="resume")
    resumed = T.read_task(output)
    assert resumed["phases"]["data_prepared"]["detail"]["gdb"] == original_data
    assert report["图斑数"] == 9
    assert report["网格"]["设计点"] >= 27
    assert int(arcpy.management.GetCount(nine_parcels)[0]) == 9
    assert resumed["status"] == "COMPLETED"
    context = resumed["phases"]["sampling_prepared"]["detail"]
    assert Path(context["point_csv"]).exists() and Path(context["label_csv"]).exists()
    labels = Path(report["外业表格"]["标签"]["path"])
    assert report["样品标签预填"] == str(labels) and labels.suffix == '.docx'
    assert labels.is_file()
    labels.unlink()
    monkeypatch.setattr(G, "grid_design", lambda *_args, **_kw: pytest.fail("补做表格不能重算采样"))
    repaired = O.run(nine_parcels, output, make_maps=False, task_action="resume")
    assert Path(repaired["样品标签预填"]).is_file()
    assert context["independent_qa"]["status"] != "FAIL"
    assert context["bulk_design"]["profile_count"] == 9
    assert context["bulk_design"]["count"] == 27
    assert context["bulk_design"]["qa"]["status"] != "FAIL"
    with arcpy.da.UpdateCursor(context["grid"]["sample"], ["SAMPLE_ID"]) as cursor:
        next(cursor)
        cursor.updateRow(["WRONG_SAMPLE_ID"])
    data = resumed["phases"]["data_prepared"]["detail"]
    wrong_id_check = QA.check_composite_design(
        data["assign"], context["units_for_map"], context["grid"]["sample"],
        data["unit_field"], data["assign_id"], data["unit_field"],
        "PJDYBH", "DKBH", "POINT_ID", "SAMPLE_ID", "DESIGN_ST", str(tmp_path / "wrong_id_qa.json"))
    assert wrong_id_check["status"] == "FAIL"
    assert wrong_id_check["sample_id_violations"]


def test_cancel_preserves_sampling_and_resume_does_not_recompute_it(nine_parcels, tmp_path, monkeypatch):
    output = str(tmp_path / "cancelled_sampling")
    original = G.grid_design
    def cancel_after_sampling(*args, **kwargs):
        result = original(*args, **kwargs)
        T.request_cancel(output)
        return result
    monkeypatch.setattr(G, "grid_design", cancel_after_sampling)
    with pytest.raises(T.TaskCancelled):
        O.run(nine_parcels, output, make_maps=False)
    stopped = T.read_task(output)
    saved_grid = stopped["phases"]["sampling_prepared"]["detail"]["grid"]["sample"]
    def forbidden_recompute(*_args, **_kwargs):
        raise AssertionError("completed sampling must be reused")
    monkeypatch.setattr(G, "grid_design", forbidden_recompute)
    report = O.run(nine_parcels, output, make_maps=False, task_action="resume")
    assert report["网格"]["设计点"] >= 27
    assert T.read_task(output)["phases"]["sampling_prepared"]["detail"]["grid"]["sample"] == saved_grid


def test_completed_entry_repairs_corrupt_jpg_and_preserves_valid_maps(nine_parcels, tmp_path, monkeypatch):
    from osgeo import gdal, osr
    import numpy as np
    raster = str(tmp_path / "test_dom.tif")
    ds = gdal.GetDriverByName("GTiff").Create(raster, 500, 500, 3, gdal.GDT_Byte)
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(4525)
    ds.SetProjection(sr.ExportToWkt())
    ds.SetGeoTransform((36499900, 1, 0, 2550400, 0, -1))
    rng = np.random.default_rng(7)
    for index in range(1, 4): ds.GetRasterBand(index).WriteArray(rng.integers(50, 220, (500, 500), dtype="uint8"))
    ds = None
    out = str(tmp_path / "completed_repair")
    options = dict(selection_oids=[1], imagery_raster=raster, dpi=100,
                   meta={"org": "测试单位", "elevation_datum": "1985国家高程基准"})
    prepared = O.run(nine_parcels, out, task_action="prepare", **options)
    assert prepared["出图"]["status"] == "PREPARED"
    assert not prepared["出图"]["outputs"]
    assert len(prepared["预览图层文件"]) == 7
    assert all(Path(path).is_file() for path in prepared["预览图层文件"])
    state = T.read_task(out)
    first = O.run(nine_parcels, out, task_action="export",
        review_confirmation={"fingerprint": state["input_fingerprint"], "operator": "测试审核人"}, **options)
    image = first["影像逐单元"]["U01"]
    assert image["pixel_qa"]["coverage_status"] == "PASS"
    outputs = first["出图"]["outputs"]
    assert len(outputs) == 2 and not any(row["status"] == "FAIL" for row in outputs)
    Path(outputs[0]["jpg"]).write_bytes(b"corrupted")
    monkeypatch.setattr(G, "grid_design", lambda *_args, **_kw: pytest.fail("采样阶段不得重算"))
    repaired = O.run(nine_parcels, out, task_action="resume", **options)
    rows = repaired["出图"]["outputs"]
    assert rows[0].get("reused") is not True, rows[0]
    assert all(row.get("reused") is True for row in rows[1:])
    assert Path(rows[0]["jpg"]).read_bytes().startswith(b"\xff\xd8")
    assert T.read_task(out)["status"] == "COMPLETED"


def test_auto_atlas_preview_exports_images_then_confirmation_reuses_design(nine_parcels, tmp_path):
    from osgeo import gdal, osr
    import numpy as np
    raster = str(tmp_path / "preview_dom.tif")
    ds = gdal.GetDriverByName("GTiff").Create(raster, 800, 800, 3, gdal.GDT_Byte)
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(4525)
    ds.SetProjection(sr.ExportToWkt())
    ds.SetGeoTransform((36499700, 1, 0, 2550500, 0, -1))
    rng = np.random.default_rng(21)
    for band in range(1, 4):
        ds.GetRasterBand(band).WriteArray(rng.integers(30, 220, (800, 800), dtype="uint8"))
    ds = None
    output = str(tmp_path / "preview_atlas")
    options = dict(selection_oids=[1], imagery_raster=raster, dpi=100,
                   meta={"org": "图册测试单位", "elevation_datum": "1985国家高程基准"})
    preview = O.run(nine_parcels, output, task_action="preview", **options)
    state = T.read_task(output)
    assert state["status"] == "PREPARED" and "设计确认" not in preview
    assert len(preview["出图"]["outputs"]) == 2
    assert all(row["preview_only"] for row in preview["出图"]["outputs"])
    assert all(Path(row["jpg"]).is_file() for row in preview["出图"]["outputs"])
    # 10-31 起图面默认不显示【设计预览】；预览身份由 preview_only 承载
    assert all("设计预览" not in row["title"] for row in preview["出图"]["outputs"])
    assert sorted(row["scale"] for row in preview["出图"]["outputs"]) == [2000, 10000]
    assert next(row for row in preview["出图"]["outputs"] if row["kind"] == "geo")["inset_audit"]["covers_all"]
    sampling_path = state["phases"]["sampling_prepared"]["detail"]["grid"]["sample"]
    preview_bytes = Path(preview["出图"]["outputs"][0]["jpg"]).read_bytes()
    confirmed = O.run(nine_parcels, output, task_action="export",
        review_confirmation={"operator": "测试确认人", "fingerprint": state["input_fingerprint"]}, **options)
    assert T.read_task(output)["phases"]["sampling_prepared"]["detail"]["grid"]["sample"] == sampling_path
    # 制图人/审核人已不作必需项；只缺高程基准时正式图仍按既有规则降级为预览（测试数据未给高程基准）
    assert not any(row["preview_only"] for row in confirmed["出图"]["outputs"])
    assert Path(preview["出图"]["outputs"][0]["jpg"]).read_bytes() == preview_bytes


def test_workflow_accepts_external_soil_polygons_without_losing_parcel_link(nine_parcels, tmp_path):
    gdb = str(Path(nine_parcels).parent)
    soil = gdb + "/soil"
    sr = arcpy.Describe(nine_parcels).spatialReference
    arcpy.management.CreateFeatureclass(gdb, "soil", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(soil, "TZMC", "TEXT", field_length=30)
    geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(36500000, 2550000), arcpy.Point(36500040, 2550000),
        arcpy.Point(36500040, 2550080), arcpy.Point(36500000, 2550080)]), sr)
    with arcpy.da.InsertCursor(soil, ["SHAPE@", "TZMC"]) as cursor:
        cursor.insertRow([geometry, "土种A"])
    # 源图层已有单元时，接入因素资料必须重新划候选，不能沿用旧单元掩盖差异。
    report = O.run(nine_parcels, str(tmp_path / "overlay_workflow"), selection_oids=[1], make_maps=False,
        factor_sources={"soil_species": {"path": soil, "field": "TZMC"}})
    assert report["因素叠加"]["parts"] == 2
    assert report["评价单元数"] == 2
    state = T.read_task(tmp_path / "overlay_workflow")
    assign = state["phases"]["data_prepared"]["detail"]["assign"]
    assert {row[0] for row in arcpy.da.SearchCursor(assign, ["SRC_DKBH"])} == {"P01"}


def test_prepare_requires_review_before_export_and_assigns_system_numbers(nine_parcels, tmp_path):
    output = str(tmp_path / "staged")
    options = dict(selection_oids=[1], make_maps=False, project_number="F99000220261461")
    prepared = O.run(nine_parcels, output, task_action="prepare", **options)
    state = T.read_task(output)
    assert state["status"] == "PREPARED"
    assert prepared["单元明细"][0]["unit"] == "F99000220261461E0001"
    with pytest.raises(ValueError, match="确认"):
        O.run(nine_parcels, output, task_action="export", **options)
    result = O.run(nine_parcels, output, task_action="export",
        review_confirmation={"fingerprint": state["input_fingerprint"], "operator": "测试审核人"}, **options)
    assert result["设计确认"]["operator"] == "测试审核人"
    assert T.read_task(output)["status"] == "COMPLETED"


def test_missing_imagery_keeps_viewable_exports_but_fails_formal_sample_map(nine_parcels, tmp_path, monkeypatch):
    from acceptance import imagery as I
    from acceptance.map_validation import verify_export_artifact

    def no_imagery(_out_folder, _wkid, **kwargs):
        return {"label": kwargs.get("label", "project"), "output": None,
                "provider": kwargs.get("provider", "demo"), "coverage_status": "FAIL",
                "pixel_qa": {"status": "FAIL", "reason": "未提供影像"},
                "source_gsd_m": None, "source_gsd_status": "UNVERIFIED",
                "positional_accuracy": {"value_m": None, "status": "UNVERIFIED"}}

    monkeypatch.setattr(I, "prepare_imagery", no_imagery)
    output = str(tmp_path / "no_imagery_workflow")
    options = {"selection_oids": [1], "project_number": "F99000220261461",
               "make_maps": True, "meta": {"org": "无影像预览测试单位"}}
    prepared = O.run(nine_parcels, output, task_action="prepare", **options)
    state = T.read_task(output)
    assert state["status"] == "PREPARED"
    assert prepared["出图"]["status"] == "PREPARED"

    from acceptance import workflow as W
    plan = state["phases"]["maps_built"]["detail"]["plan"]
    exported = W._worker(O._worker_python(), "acceptance.map_export", plan)

    sample = next(row for row in exported["outputs"] if row.get("kind") == "sample")
    assert sample["preview_only"] is True
    assert sample["deliverable_status"] == "PREVIEW_ONLY"
    assert sample["status"] == "FAIL"
    assert next(row for row in sample["checks"] if row["item"] == "采样图影像底图")["status"] == "FAIL"
    for ext in ("jpg", "png", "pdf"):
        assert Path(sample[ext]).is_file()
        assert verify_export_artifact(sample[ext], ext)["status"] != "FAIL"
    assert exported["status"] == "FAIL"
    with pytest.raises(ValueError, match="预览存在技术失败项"):
        O.run(nine_parcels, output, task_action="export",
            review_confirmation={"fingerprint": state["input_fingerprint"], "operator": "测试审核人"}, **options)


def test_large_project_builds_index_and_clear_main_sheets(nine_parcels, tmp_path):
    import json
    from osgeo import gdal, osr
    sr = arcpy.Describe(nine_parcels).spatialReference
    with arcpy.da.UpdateCursor(nine_parcels, ["OID@", "SHAPE@"]) as cursor:
        for oid, geometry in cursor:
            if oid == 9:
                geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(36510000, 2550000), arcpy.Point(36510080, 2550000),
                    arcpy.Point(36510080, 2550080), arcpy.Point(36510000, 2550080)]), sr)
                cursor.updateRow([oid, geometry])
    raster = str(tmp_path / "large_test_dom.tif")
    dataset = gdal.GetDriverByName("GTiff").Create(raster, 12000, 1000, 3, gdal.GDT_Byte, options=["COMPRESS=LZW"])
    projection = osr.SpatialReference()
    projection.ImportFromEPSG(4525)
    dataset.SetProjection(projection.ExportToWkt())
    dataset.SetGeoTransform((36499500, 1, 0, 2550500, 0, -1))
    for band in range(1, 4): dataset.GetRasterBand(band).Fill(150)
    dataset = None
    out = str(tmp_path / "large_sheets")
    report = O.run(nine_parcels, out, task_action="prepare", selection_oids=[1, 9],
        imagery_raster=raster, meta={"org": "分幅测试单位"})
    state = T.read_task(out)
    with open(state["phases"]["maps_built"]["detail"]["plan"], encoding="utf-8") as stream:
        plan = json.load(stream)
    sheets = [target for target in plan["targets"] if target["kind"] == "geo"]
    assert len(sheets) >= 2
    assert all(target["scale"] <= 10000 for target in sheets)
    assert any(target["kind"] == "index" for target in plan["targets"])
    assert report["主分幅"]


def test_seven_sampling_groups_still_plan_nine_original_parcel_sheets(nine_parcels, tmp_path):
    from acceptance import workflow
    output = str(tmp_path / "groups_atlas")
    report = O.run(nine_parcels, output, make_maps=False, sampling_groups=[
        {"group_id": "SG_12", "parcels": ["P01", "P02"], "basis": "测试合组"},
        {"group_id": "SG_34", "parcels": ["P03", "P04"], "basis": "测试合组"}])
    data = T.read_task(output)["phases"]["data_prepared"]["detail"]
    targets = workflow._atlas_plan(arcpy, data, "DKBH", 1)
    assert report["评价单元数"] == 7 and report["网格"]["设计点"] == 21
    assert len([t for t in targets if t["kind"] == "sample"]) == 9


def test_oversized_original_parcel_keeps_identity_through_real_unit_splitting(nine_parcels, tmp_path):
    from acceptance import workflow
    gdb = str(Path(nine_parcels).parent)
    source = gdb + "/large_original"
    sr = arcpy.Describe(nine_parcels).spatialReference
    arcpy.management.CreateFeatureclass(gdb, "large_original", "POLYGON", spatial_reference=sr)
    arcpy.management.AddField(source, "DKBH", "TEXT", field_length=30)
    geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(36500000, 2550000), arcpy.Point(36500400, 2550000),
        arcpy.Point(36500400, 2550200), arcpy.Point(36500000, 2550200)]), sr)
    with arcpy.da.InsertCursor(source, ["DKBH", "SHAPE@"]) as cursor:
        cursor.insertRow(["39", geometry])
    output = str(tmp_path / "large_original_atlas")
    report = O.run(source, output, make_maps=False)
    data = T.read_task(output)["phases"]["data_prepared"]["detail"]
    targets = workflow._atlas_plan(arcpy, data, "DKBH", 1)
    samples = [t for t in targets if t["kind"] == "sample"]
    assert report["评价单元数"] >= 3 and len(samples) == 1
    assert samples[0]["parcel"] == "39" and len(samples[0]["parcel_ids"]) >= 3
    assert int(arcpy.management.GetCount(source)[0]) == 1


def test_bulk_hard_failure_cannot_be_aggregated_as_technical_pass(nine_parcels, tmp_path, monkeypatch):
    from acceptance import bulk_design as B
    original = B.prepare_bulk_candidates
    def bad_bulk(*args, **kwargs):
        result = original(*args, **kwargs)
        result["qa"]["status"] = "FAIL"
        return result
    monkeypatch.setattr(B, "prepare_bulk_candidates", bad_bulk)
    result = O.run(nine_parcels, str(tmp_path / "bad_bulk"), make_maps=False, selection_oids=[1])
    assert result["技术状态"] == "FAIL"


def test_per_unit_dom_choices_skip_online_and_preserve_sources(nine_parcels, tmp_path, monkeypatch):
    from osgeo import gdal, osr
    from acceptance import imagery as I
    paths = []
    for index in (1, 2):
        path = str(tmp_path / "DOM_{}.tif".format(index))
        dataset = gdal.GetDriverByName("GTiff").Create(path, 500, 500, 3, gdal.GDT_Byte, options=["COMPRESS=LZW"])
        sr = osr.SpatialReference()
        sr.ImportFromEPSG(4525)
        dataset.SetProjection(sr.ExportToWkt())
        dataset.SetGeoTransform((36499900, 1, 0, 2550400, 0, -1))
        for band in range(1, 4): dataset.GetRasterBand(band).Fill(80 + index * 20 + band)
        dataset = None
        paths.append(path)
    original = I.prepare_imagery
    def local_only(*args, **kwargs):
        assert kwargs.get("raster"), "全部单元已选本地DOM，不得访问在线服务"
        return original(*args, **kwargs)
    monkeypatch.setattr(I, "prepare_imagery", local_only)
    report = O.run(nine_parcels, str(tmp_path / "unit_images"), task_action="prepare", selection_oids=[1, 2],
        unit_imagery_sources={"U01": {"raster": paths[0]}, "U02": {"raster": paths[1]}}, meta={"org": "多来源测试单位"})
    assert report["影像逐单元"]["U01"]["source"] != report["影像逐单元"]["U02"]["source"]
    assert report["影像"]["provider"] == "mixed_units"
    assert set(report["影像"]["unit_sources"]) == {"U01", "U02"}
    assert all(record["coverage_status"] == "PASS" for record in report["影像逐单元"].values())


def test_resume_repairs_corrupt_shp_without_replacing_design(nine_parcels,tmp_path):
    output=str(tmp_path/'shp_repair')
    O.run(nine_parcels,output,make_maps=False,selection_oids=[1])
    before=T.read_task(output)['phases']['sampling_prepared']['detail']
    old_shp=before['shapefiles']['sample']
    with arcpy.da.UpdateCursor(old_shp,['POINT_ID']) as cursor:
        next(cursor);cursor.updateRow(['WRONG_ID'])
    result=O.run(nine_parcels,output,make_maps=False,selection_oids=[1],task_action='resume')
    after=T.read_task(output)['phases']['sampling_prepared']['detail']
    assert after['gdb']==before['gdb'] and after['design_signature']==before['design_signature']
    assert after['shapefiles']['sample']!=old_shp
    assert G.verify_shapefile_identifiers(arcpy,after['grid']['sample'],after['shapefiles']['sample'])['status']=='PASS'
    assert 'WRONG_ID' in {row[0] for row in arcpy.da.SearchCursor(old_shp,['POINT_ID'])}
    assert result['SHP图层']['sample']==after['shapefiles']['sample']
