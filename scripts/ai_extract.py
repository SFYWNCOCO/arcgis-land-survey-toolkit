# -*- coding: utf-8 -*-
"""Thin CLI around ArcGIS Pro Image Analyst AI/segmentation tools.

Default mode is dry-run: it prints the exact geoprocessing call.  Add --run to
execute with ArcGIS Pro Python.  The script never invents model paths.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lsplan import load_profile  # noqa: E402


def segment_plan(profile: dict, scene: str, in_raster: str, out_raster: str, overrides: dict) -> dict:
    p = dict(profile["segmentation_profiles"][scene])
    for key in ("spectral_detail", "spatial_detail", "min_segment_size", "max_segment_size", "band_indexes"):
        if overrides.get(key) is not None:
            p[key] = overrides[key]
    return {
        "tool": "SegmentMeanShift_ia",
        "in_raster": in_raster,
        "out_raster": out_raster,
        **p,
    }


def extract_plan(profile: dict, in_raster: str, out_location: str, out_prefix: str, model: str, confidence: float | None) -> dict:
    p = dict(profile["ai_defaults"]["extract_features"])
    if confidence is not None:
        p["confidence_threshold"] = confidence
    return {
        "tool": "ExtractFeaturesUsingAIModels_ia",
        "in_raster": in_raster,
        "mode": p["mode"],
        "out_location": out_location,
        "out_prefix": out_prefix,
        "pretrained_models": model,
        "confidence_threshold": p["confidence_threshold"],
        "regularize_parcels": p["regularize_parcels"],
        "smoothing_tolerance": p["smoothing_tolerance"],
        "dangle_length": p["dangle_length"],
        "regularization_method": p["regularization_method"],
    }


def detect_plan(profile: dict, in_raster: str, out_features: str, model: str, confidence: float | None, max_overlap: float | None) -> dict:
    p = dict(profile["ai_defaults"]["detect_objects"])
    if confidence is not None:
        p["confidence_threshold"] = confidence
    if max_overlap is not None:
        p["max_overlap_ratio"] = max_overlap
    return {
        "tool": "DetectObjectsUsingDeepLearning_ia",
        "in_raster": in_raster,
        "out_detected_objects": out_features,
        "in_model_definition": model,
        "nms": p["nms"],
        "max_overlap_ratio": p["max_overlap_ratio"],
        "confidence_score_field": "Confidence",
        "class_value_field": "Class",
    }


def classify_plan(in_raster: str, out_raster: str, model: str) -> dict:
    return {
        "tool": "ClassifyPixelsUsingDeepLearning_ia",
        "in_raster": in_raster,
        "out_classified_raster": out_raster,
        "in_model_definition": model,
    }


def run_arcpy(plan: dict) -> None:
    import arcpy
    tool = plan["tool"]
    if tool == "SegmentMeanShift_ia":
        arcpy.ia.SegmentMeanShift(plan["in_raster"], plan["out_raster"], plan["spectral_detail"],
                                  plan["spatial_detail"], plan["min_segment_size"],
                                  plan.get("band_indexes"), plan["max_segment_size"])
    elif tool == "ExtractFeaturesUsingAIModels_ia":
        arcpy.ia.ExtractFeaturesUsingAIModels(
            plan["in_raster"], plan["mode"], plan["out_location"], plan["out_prefix"],
            None, plan["pretrained_models"], None, plan["confidence_threshold"],
            False, False, None, None, plan["smoothing_tolerance"], plan["dangle_length"],
            None, None, plan["regularize_parcels"], None, None, None,
            plan["regularization_method"], None)
    elif tool == "DetectObjectsUsingDeepLearning_ia":
        arcpy.ia.DetectObjectsUsingDeepLearning(
            plan["in_raster"], plan["out_detected_objects"], plan["in_model_definition"],
            None, plan["nms"], plan["confidence_score_field"], plan["class_value_field"],
            plan["max_overlap_ratio"], "PROCESS_AS_MOSAICKED_IMAGE")
    elif tool == "ClassifyPixelsUsingDeepLearning_ia":
        arcpy.ia.ClassifyPixelsUsingDeepLearning(
            plan["in_raster"], plan["out_classified_raster"], plan["in_model_definition"],
            None, "PROCESS_AS_MOSAICKED_IMAGE", None, None)
    else:
        raise ValueError(f"unsupported tool: {tool}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.path.join(ROOT, "config", "standard_profile.json"))
    ap.add_argument("--mode", choices=("segment", "extract-features", "detect-objects", "classify-pixels"))
    ap.add_argument("--scene", choices=("building", "woodland", "road", "cultivated", "water"))
    ap.add_argument("--in-raster")
    ap.add_argument("--out")
    ap.add_argument("--out-prefix")
    ap.add_argument("--model")
    ap.add_argument("--confidence", type=float)
    ap.add_argument("--max-overlap", type=float)
    ap.add_argument("--spectral-detail", type=float)
    ap.add_argument("--spatial-detail", type=int)
    ap.add_argument("--min-segment-size", type=int)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    profile = load_profile(a.profile)
    if a.selftest:
        plan = segment_plan(profile, "cultivated", "in.tif", "out.tif", {})
        assert plan["tool"] == "SegmentMeanShift_ia"
        assert plan["min_segment_size"] == 60
        assert extract_plan(profile, "in.tif", "D:/out", "demo", "model.dlpk", None)["mode"] == "Infer and Postprocess"
        print("AI_EXTRACT_SELFTEST PASS")
        return 0
    if not a.mode or not a.in_raster:
        ap.error("--mode and --in-raster are required unless --selftest")
    if a.mode == "segment":
        if not (a.scene and a.out):
            ap.error("segment requires --scene and --out")
        plan = segment_plan(profile, a.scene, a.in_raster, a.out, {
            "spectral_detail": a.spectral_detail, "spatial_detail": a.spatial_detail,
            "min_segment_size": a.min_segment_size,
        })
    elif a.mode == "extract-features":
        if not (a.out and a.out_prefix and a.model):
            ap.error("extract-features requires --out, --out-prefix and --model")
        plan = extract_plan(profile, a.in_raster, a.out, a.out_prefix, a.model, a.confidence)
    elif a.mode == "detect-objects":
        if not (a.out and a.model):
            ap.error("detect-objects requires --out and --model")
        plan = detect_plan(profile, a.in_raster, a.out, a.model, a.confidence, a.max_overlap)
    else:
        if not (a.out and a.model):
            ap.error("classify-pixels requires --out and --model")
        plan = classify_plan(a.in_raster, a.out, a.model)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if a.run:
        try:
            import arcpy  # noqa: F401
        except ImportError as exc:
            raise SystemExit("--run 必须使用 ArcGIS Pro Python 环境") from exc
        run_arcpy(plan)
        print("AI_EXTRACT_COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())