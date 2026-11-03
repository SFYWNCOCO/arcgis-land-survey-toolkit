"""Source-backed acceptance rule references; unresolved items stay manual."""

GUIDE = "广西补充耕地质量验收作业指导书 v1.1（2026-08-17）"
RULES = {
    "evaluation_unit": {"source": GUIDE, "clause": "6.1.2–6.1.3③", "page": "印刷页8/PDF页14", "max_mu": 50},
    "composite_sampling_design": {"source": GUIDE, "clause": "6.1.4", "page": "印刷页8/PDF页14", "min_subsites": 3, "per_parcel": 1, "layout": "均匀网格"},
    "composite_sample_collection": {"source": GUIDE, "clause": "6.2.4.3(1)", "page": "印刷页16/PDF页22", "min_spacing_m": 15, "small_parcel_exception": "覆盖图斑并避开田边", "depth_cm": [0, 20]},
    "bulk_density": {"source": GUIDE, "clause": "6.2.4.2–6.2.4.3(2)", "page": "印刷页16/PDF页22", "cores_per_unit": 3, "profile_buffer_m": 2, "parallel_spacing_m": 2, "ring_cm3": 100, "sample_id_prefix": "RZ"},
    "delivery": {"source": GUIDE, "clause": "10.1–10.4、附录C/G/I", "page": "印刷页38–39、50–53"},
    "coordinate_reference": {"source": GUIDE, "clause": "5.1、5.3", "page": "印刷页5–6", "horizontal": "CGCS2000；三度带按项目资料确认", "vertical": "1985国家高程基准"},
}


def status_for_manual_review(reason):
    return {"status": "NEEDS_REVIEW", "reason": reason}


def unit_area_status(area_mu, max_mu=50.0, context="review"):
    """50 亩口径唯一入口。

    - 新划分（context="design"）：超限自动拆；拆后仍超 -> FAIL。
    - 已有/最终成果复核（context="review"）：规范写“原则上”，超限 -> NEEDS_REVIEW。
    """
    try:
        area = float(area_mu)
    except (TypeError, ValueError):
        return "UNVERIFIED"
    if area <= float(max_mu) + 1e-9:
        return "PASS"
    return "FAIL" if context == "design" else "NEEDS_REVIEW"
