"""Prepare operator-selected Sentinel-2 date groups as AOI-clipped local rasters."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

from acceptance.stac import validate_asset_href


MAX_OUTPUT_PIXELS = 15_000_000
HTTP_TIMEOUT_SECONDS = 60
HTTP_CONNECT_TIMEOUT_SECONDS = 15
HTTP_MAX_RETRIES = 2
HTTP_RETRY_DELAY_SECONDS = 2
HTTP_MULTIRANGE_MODE = "SERIAL"
WARP_TIMEOUT_SECONDS = 300
MAX_DIAGNOSTIC_LOG_BYTES = 256 * 1024


def _redact_diagnostic_text(value):
    """Remove credentials from bounded GDAL output before it leaves a run folder."""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    text = re.sub(
        r"(?i)([?&](?:x-amz-signature|x-amz-credential|x-amz-security-token|"
        r"access_token|token|signature|sig|credential)=)[^&\s]+",
        r"\1[REDACTED]", text,
    )
    text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text


def _persist_worker_diagnostics(log_path, run_dir, phase, role, error, returncode):
    """Keep a small sanitized failure record outside the disposable run folder."""
    with open(log_path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - MAX_DIAGNOSTIC_LOG_BYTES), os.SEEK_SET)
        log_bytes = stream.read(MAX_DIAGNOSTIC_LOG_BYTES)
    log_text = _redact_diagnostic_text(log_bytes)
    root = os.path.join(os.path.dirname(os.path.abspath(run_dir)), "diagnostics")
    os.makedirs(root, exist_ok=True)
    stem = "{}_{}_{}_{}".format(os.path.basename(run_dir), phase, role, uuid.uuid4().hex[:8])
    diagnostic_log = os.path.join(root, stem + ".log")
    record_path = os.path.join(root, stem + ".json")
    with open(diagnostic_log, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(log_text)
    record = {
        "phase": phase,
        "role": role,
        "status": "FAILED",
        "error_type": type(error).__name__,
        "error": _redact_diagnostic_text(str(error)),
        "worker_returncode": returncode,
        "log_file": os.path.basename(diagnostic_log),
        "log_truncated": size > MAX_DIAGNOSTIC_LOG_BYTES,
        "log_bytes_retained": len(log_bytes),
    }
    with open(record_path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record_path


def candidate_key(group):
    return "{}|{}|{}".format(
        group["acquisition_date"], group["platform"], group.get("processing_baseline") or "(无基线)"
    )


def load_selected_groups(catalog_path, before_key, after_key, aoi_sha256,
                         allow_platform_mismatch=False, max_pixels=MAX_OUTPUT_PIXELS):
    with open(catalog_path, "r", encoding="utf-8") as stream:
        catalog = json.load(stream)
    if catalog.get("search_complete") is not True:
        raise ValueError("目录检索分页不完整，禁止准备影像。")
    if catalog.get("aoi_sha256") != aoi_sha256:
        raise ValueError("当前评价区与目录检索 AOI 指纹不同。")
    groups = catalog.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("目录文件中没有可用候选日期组。")
    by_key = {candidate_key(group): group for group in groups}
    if before_key not in by_key or after_key not in by_key:
        raise ValueError("所选日期组不在目录检索结果中。")
    before, after = by_key[before_key], by_key[after_key]
    if before["acquisition_date"] == after["acquisition_date"]:
        raise ValueError("前期与后期必须选择不同日期。")
    if before["acquisition_date"] > after["acquisition_date"]:
        raise ValueError("前期日期必须早于后期日期。")
    mixed = before["platform"] != after["platform"] or before.get("processing_baseline") != after.get("processing_baseline")
    if mixed and not allow_platform_mismatch:
        raise ValueError("前后期卫星平台或处理基线不同；勾选明确确认后才可继续。")
    if len(before.get("items", [])) < 1 or len(after.get("items", [])) < 1:
        raise ValueError("选定日期组没有可处理的 Item。")
    if max_pixels < 1 or max_pixels > MAX_OUTPUT_PIXELS:
        raise ValueError("像元上限必须在 1 到 {} 之间。".format(MAX_OUTPUT_PIXELS))
    for group in (before, after):
        for item in group["items"]:
            for role in ("red", "nir", "scl"):
                asset = (item.get("assets") or {}).get(role)
                if not isinstance(asset, dict):
                    raise ValueError("{}缺少{}资产。".format(item.get("item_id", "Item"), role))
                validate_asset_href(asset.get("href"))
                if role in ("red", "nir"):
                    if asset.get("scale") is None or asset.get("offset") is None:
                        raise ValueError("{}资产缺少 scale/offset。".format(role))
    return catalog, before, after


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _temporary_gdal_config(gdal, values):
    """Apply request limits only during this synchronous preparation run."""
    previous = {key: gdal.GetConfigOption(key) for key in values}
    try:
        for key, value in values.items():
            gdal.SetConfigOption(key, str(value))
        yield
    finally:
        for key, value in previous.items():
            gdal.SetConfigOption(key, value)


def _make_warp_progress(role, timeout_seconds, progress=None, clock=time.monotonic):
    started = clock()
    state = {"timed_out": False, "last_percent": -1}

    def callback(complete, _message, _data):
        if clock() - started >= timeout_seconds:
            state["timed_out"] = True
            return 0
        percent = max(0, min(100, int(float(complete) * 100)))
        if progress is not None and percent != state["last_percent"]:
            progress(role, percent)
        state["last_percent"] = percent
        return 1

    return callback, state


def _run_isolated_warp(task, run_dir, timeout_seconds, progress=None, popen_factory=None,
                       clock=time.monotonic, sleeper=time.sleep):
    """Run one GDAL warp out of process so a stuck native open can be killed."""
    phase, role = task["phase"], task["role"]
    suffix = "{}_{}".format(phase, role)
    paths = {key: os.path.join(run_dir, "worker_{}_{}.json".format(suffix, key))
             for key in ("task", "result", "progress", "error")}
    log_path = os.path.join(run_dir, "worker_{}_{}.log".format(phase, role))
    task = dict(task, timeout_seconds=timeout_seconds, **{key + "_path": value for key, value in paths.items()})
    with open(paths["task"], "w", encoding="utf-8") as stream:
        json.dump(task, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    factory = popen_factory or subprocess.Popen
    launch = [sys.executable, "-m", "acceptance.sentinel_warp_worker", paths["task"]]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = None
    try:
        with open(log_path, "wb") as log:
            process = factory(launch, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, creationflags=flags)
            deadline = clock() + max(0, timeout_seconds)
            last_progress = None
            while process.poll() is None:
                if clock() >= deadline:
                    process.kill()
                    process.wait(timeout=10)
                    raise TimeoutError("{}期{}波段超过{}秒；父进程已终止GDAL工作进程。".format(
                        phase, role, timeout_seconds))
                try:
                    with open(paths["progress"], "r", encoding="utf-8") as stream:
                        update = json.load(stream)
                    marker = (update.get("role"), update.get("percent"))
                    if progress is not None and marker != last_progress:
                        progress("{}_{}".format(phase, role), int(update["percent"]))
                    last_progress = marker
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                sleeper(0.25)
            if process.returncode != 0:
                try:
                    with open(paths["error"], "r", encoding="utf-8") as stream:
                        error = json.load(stream)
                    message = error.get("message", "未知错误")
                except (OSError, ValueError):
                    message = "工作进程退出码 {}；日志：{}".format(process.returncode, log_path)
                raise RuntimeError("{}期{}波段处理失败：{}".format(phase, role, message))
        with open(paths["result"], "r", encoding="utf-8") as stream:
            return json.load(stream)
    except Exception as exc:
        try:
            diagnostic_path = _persist_worker_diagnostics(
                log_path, run_dir, phase, role, exc,
                getattr(process, "returncode", None),
            )
        except Exception as diagnostic_error:
            message = "{}；诊断日志保存失败：{}: {}".format(
                exc, type(diagnostic_error).__name__, diagnostic_error,
            )
        else:
            message = "{}；诊断记录：{}".format(exc, diagnostic_path)
        raise type(exc)(message) from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for path in list(paths.values()) + [log_path]:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def estimate_pixel_count(extent, resolution=10):
    width = float(extent[2]) - float(extent[0])
    height = float(extent[3]) - float(extent[1])
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("评价区范围无效，无法估算影像像元预算。")
    return int(math.ceil(width / resolution) * math.ceil(height / resolution))


def _same_parameters(group, role):
    first = group["items"][0]["assets"][role]
    for item in group["items"][1:]:
        current = item["assets"][role]
        for key in ("scale", "offset", "nodata", "gsd_m"):
            if current.get(key) != first.get(key):
                raise ValueError("同一日期组的{}瓦片{}元数据不一致，不能安全镶嵌。".format(role, key))
    return first


def _warp_role(gdal, group, role, output_path, cutline_path, target_wkt, resolution, resampling,
               timeout_seconds=WARP_TIMEOUT_SECONDS, progress=None):
    first = _same_parameters(group, role)
    sources = []
    for item in group["items"]:
        asset = item["assets"][role]
        validate_asset_href(asset["href"])
        sources.append("/vsicurl/" + asset["href"])
    output_type = gdal.GDT_UInt16 if role == "scl" else gdal.GDT_Float32
    destination_nodata = 0 if role == "scl" else -9999.0
    predictor = "2" if role == "scl" else "3"
    progress_callback, progress_state = _make_warp_progress(role, timeout_seconds, progress)
    started = time.monotonic()
    options = gdal.WarpOptions(
        format="GTiff", dstSRS=target_wkt, cutlineDSName=cutline_path,
        cropToCutline=True, targetAlignedPixels=True,
        xRes=resolution, yRes=resolution, resampleAlg=resampling,
        srcNodata=first["nodata"], dstNodata=destination_nodata, outputType=output_type,
        multithread=False, warpMemoryLimit=256,
        callback=progress_callback,
        creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=" + predictor, "BIGTIFF=IF_SAFER"],
    )
    try:
        dataset = gdal.Warp(output_path, sources, options=options)
    except Exception as exc:
        if progress_state["timed_out"]:
            raise TimeoutError("{}波段在线读取/裁切超过{}秒，已中止本次准备。".format(role, timeout_seconds)) from exc
        raise
    if progress_state["timed_out"]:
        dataset = None
        raise TimeoutError("{}波段在线读取/裁切超过{}秒，已中止本次准备。".format(role, timeout_seconds))
    if dataset is None:
        raise RuntimeError("GDAL Warp 未能生成{}影像。".format(role))
    dataset.FlushCache()
    info = {
        "width": int(dataset.RasterXSize), "height": int(dataset.RasterYSize),
        "geotransform": list(dataset.GetGeoTransform()),
        "projection": dataset.GetProjection(),
        "nodata": dataset.GetRasterBand(1).GetNoDataValue(),
        "data_type": gdal.GetDataTypeName(dataset.GetRasterBand(1).DataType),
    }
    dataset = None
    if info["width"] < 1 or info["height"] < 1 or not info["projection"]:
        raise RuntimeError("{}影像输出尺寸或坐标系无效。".format(role))
    if not os.path.isfile(output_path) or os.path.getsize(output_path) < 1024:
        raise RuntimeError("{}影像文件未完整写出。".format(role))
    info.update({"path": output_path, "scale": first.get("scale"), "offset": first.get("offset"),
                 "sha256": _file_sha256(output_path), "size_bytes": os.path.getsize(output_path),
                 "resampling": resampling, "elapsed_seconds": round(time.monotonic() - started, 2)})
    return info


def prepare_pair(catalog_path, before_key, after_key, aoi_sha256, cutline_geojson,
                 target_wkt, output_root, allow_platform_mismatch=False,
                 max_pixels=MAX_OUTPUT_PIXELS, estimated_pixels=None,
                 max_warp_seconds=WARP_TIMEOUT_SECONDS, progress=None):
    """Create one run folder and six clipped rasters; cleans only its own folder on failure."""
    catalog, before, after = load_selected_groups(
        catalog_path, before_key, after_key, aoi_sha256,
        allow_platform_mismatch=allow_platform_mismatch, max_pixels=max_pixels,
    )
    if not target_wkt or not str(target_wkt).strip():
        raise ValueError("評價區目標坐標系缺少 WKT 定義。")
    if estimated_pixels is not None and estimated_pixels > max_pixels:
        raise ValueError("評價區保守估算約{}個10米像元，超過预算{}，未创建输出。".format(estimated_pixels, max_pixels))
    os.makedirs(output_root, exist_ok=True)
    run_dir = os.path.join(output_root, "sentinel2_pair_" + uuid.uuid4().hex[:12])
    os.mkdir(run_dir)
    cutline_path = os.path.join(run_dir, "aoi_wgs84.geojson")
    created = [run_dir]
    try:
        with open(cutline_path, "w", encoding="utf-8") as stream:
            json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": cutline_geojson}]}, stream, ensure_ascii=False)
        products = {}
        for phase, group in (("before", before), ("after", after)):
            products[phase] = {}
            for role, resolution, resampling in (("red", 10, "bilinear"), ("nir", 10, "bilinear"), ("scl", 20, "near")):
                path = os.path.join(run_dir, "{}_{}.tif".format(phase, role))
                if progress is not None:
                    progress("{}_{}".format(phase, role), 0)
                products[phase][role] = _run_isolated_warp({
                    "phase": phase, "group": group, "role": role, "output_path": path,
                    "cutline_path": cutline_path, "target_wkt": target_wkt,
                    "resolution": resolution, "resampling": resampling,
                }, run_dir, max_warp_seconds, progress=progress)
        base = products["before"]["red"]
        expected_10m = (base["width"], base["height"], base["geotransform"], base["projection"])
        for phase in ("before", "after"):
            for role in ("red", "nir"):
                image = products[phase][role]
                actual = (image["width"], image["height"], image["geotransform"], image["projection"])
                if actual != expected_10m:
                    raise ValueError("前后期红光/NIR输出网格不一致：{} {}。".format(phase, role))
            scl = products[phase]["scl"]
            if abs(abs(scl["geotransform"][1]) - 20) > 1e-6 or abs(abs(scl["geotransform"][5]) - 20) > 1e-6:
                raise ValueError("{}期SCL输出分辨率不是20米。".format(phase))
        before_scl, after_scl = products["before"]["scl"], products["after"]["scl"]
        if (before_scl["width"], before_scl["height"], before_scl["geotransform"], before_scl["projection"]) != (after_scl["width"], after_scl["height"], after_scl["geotransform"], after_scl["projection"]):
            raise ValueError("前后期SCL输出网格不一致。")
        pixel_count = base["width"] * base["height"]
        if pixel_count > max_pixels:
            raise ValueError("裁切范围约{}个10米像元，超过预算{}。".format(pixel_count, max_pixels))
        manifest = {
            "schema_version": "1.0", "status": "PREPARED_NEEDS_FIELD_REVIEW",
            "catalog_source": os.path.abspath(catalog_path), "aoi_sha256": aoi_sha256,
            "catalog_sha256": _file_sha256(catalog_path),
            "target_wkt": target_wkt, "estimated_10m_pixels": pixel_count,
            "max_10m_pixels": max_pixels,
            "network_policy": {"connect_timeout_seconds": HTTP_CONNECT_TIMEOUT_SECONDS,
                               "request_timeout_seconds": HTTP_TIMEOUT_SECONDS,
                               "max_retries": HTTP_MAX_RETRIES,
                               "retry_delay_seconds": HTTP_RETRY_DELAY_SECONDS,
                               "multirange_mode": HTTP_MULTIRANGE_MODE,
                               "per_raster_warp_timeout_seconds": max_warp_seconds},
            "before": {"candidate_key": candidate_key(before), "platform": before["platform"], "processing_baseline": before.get("processing_baseline"), "item_ids": before["item_ids"], "tiles": before["tiles"], "cloud_min_pct": before["cloud_cover_min_pct"], "cloud_max_pct": before["cloud_cover_max_pct"], "geometric_footprint_coverage_pct": before.get("geometric_footprint_coverage_pct"), "products": products["before"]},
            "after": {"candidate_key": candidate_key(after), "platform": after["platform"], "processing_baseline": after.get("processing_baseline"), "item_ids": after["item_ids"], "tiles": after["tiles"], "cloud_min_pct": after["cloud_cover_min_pct"], "cloud_max_pct": after["cloud_cover_max_pct"], "geometric_footprint_coverage_pct": after.get("geometric_footprint_coverage_pct"), "products": products["after"]},
            "platform_or_baseline_mismatch_confirmed": (before["platform"] != after["platform"] or before.get("processing_baseline") != after.get("processing_baseline")),
            "notes": ["足迹与有效像元覆盖须复核；云量为景级元数据。", "SCL使用最近邻，B04/B08使用双线性重采样。", "输出仅供遥感变化线索筛查，不是质量验收结论。"],
        }
        manifest_path = os.path.join(run_dir, "pair_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return {"run_dir": run_dir, "manifest": manifest_path, "products": products,
                "pixel_count": pixel_count, "before": before, "after": after}
    except BaseException:
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
