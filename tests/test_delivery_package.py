import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from delivery_package import _check_gdb, _file_candidates, _filespec, _lab_chain_check, check_package


class _Environment:
    def __init__(self, arcpy, **values):
        self.arcpy = arcpy
        self.values = values
        self.previous = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = getattr(self.arcpy.env, key)
            setattr(self.arcpy.env, key, value)
        return self

    def __exit__(self, *_args):
        for key, value in self.previous.items():
            setattr(self.arcpy.env, key, value)
        return False


def _fake_arcpy(list_feature_classes):
    arcpy = SimpleNamespace()
    arcpy.env = SimpleNamespace(workspace="ORIGINAL_WORKSPACE")
    arcpy.Exists = lambda _path: True
    arcpy.EnvManager = lambda **kwargs: _Environment(arcpy, **kwargs)
    arcpy.ListFeatureClasses = list_feature_classes
    arcpy.ListDatasets = lambda *_args: []
    return arcpy


def test_check_gdb_restores_arcgis_workspace_after_success(monkeypatch, tmp_path):
    arcpy = _fake_arcpy(lambda **_kwargs: [])
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)

    issues = _check_gdb(tmp_path / "delivery.gdb", 4525)

    assert issues  # Empty test GDB reports missing required layers.
    assert arcpy.env.workspace == "ORIGINAL_WORKSPACE"


def test_check_gdb_restores_arcgis_workspace_after_arcpy_error(monkeypatch, tmp_path):
    def fail_list(**_kwargs):
        raise RuntimeError("simulated ArcPy listing failure")

    arcpy = _fake_arcpy(fail_list)
    monkeypatch.setitem(sys.modules, "arcpy", arcpy)

    with pytest.raises(RuntimeError, match="simulated ArcPy listing failure"):
        _check_gdb(tmp_path / "delivery.gdb", 4525)

    assert arcpy.env.workspace == "ORIGINAL_WORKSPACE"


def test_filespec_respects_extension_preference_for_legacy_report(tmp_path):
    old_doc = tmp_path / "县级报告.doc"
    current_docx = tmp_path / "县级报告.docx"
    old_doc.write_text("旧稿", encoding="utf-8")
    current_docx.write_text("新稿", encoding="utf-8")

    chosen = _filespec(tmp_path, "县级报告", extensions=(".docx", ".doc"))
    candidates = _file_candidates(tmp_path, "县级报告", extensions=(".docx", ".doc"))

    assert chosen == current_docx
    assert candidates == [current_docx, old_doc]


def test_file_candidates_skip_extensionless_stem_when_extensions_are_required(tmp_path):
    extensionless = tmp_path / "县级报告"
    old_doc = tmp_path / "县级报告.doc"
    current_docx = tmp_path / "县级报告.docx"
    extensionless.write_text("无扩展名文件", encoding="utf-8")
    old_doc.write_text("旧稿", encoding="utf-8")
    current_docx.write_text("新稿", encoding="utf-8")

    chosen = _filespec(tmp_path, "县级报告", extensions=(".docx", ".doc"))
    candidates = _file_candidates(tmp_path, "县级报告", extensions=(".docx", ".doc"))

    assert chosen == current_docx
    assert candidates == [current_docx, old_doc]


def test_county_report_check_flags_extensionless_duplicate_and_keeps_docx_priority(tmp_path):
    root = tmp_path / "delivery"
    report_dir = root / "文字成果"
    report_dir.mkdir(parents=True)
    (root / "delivery_manifest.json").write_text(
        '{"project_id":"P1","delivery_level":"county_initial",'
        '"project_type":"non_project","pollution_report_required":false,'
        '"reevaluation":false,"manual_reviews":{}}',
        encoding="utf-8",
    )
    (report_dir / "县级补充耕地质量初鉴报告").write_text("无扩展名", encoding="utf-8")
    (report_dir / "县级补充耕地质量初鉴报告.doc").write_text("旧稿", encoding="utf-8")
    (report_dir / "县级补充耕地质量初鉴报告.docx").write_text("新稿", encoding="utf-8")

    report = check_package(root)

    report_check = next(
        row for row in report["checked_files"]
        if row["item"] == "文字成果/县级补充耕地质量初鉴报告"
    )
    assert report_check["path"].endswith("县级补充耕地质量初鉴报告.docx")
    assert any(row["item"] == "县级报告无扩展名同名文件" for row in report["checked_files"])
    assert any("无扩展名同名文件" in item for item in report["pending_manual_reviews"])
    assert any("DOC/DOCX 同名版本" in item for item in report["pending_manual_reviews"])


def test_city_report_keeps_declared_extension_priority_case_insensitively(tmp_path):
    (tmp_path / "市级报告.PDF").write_text("PDF", encoding="utf-8")
    (tmp_path / "市级报告.DOCX").write_text("DOCX", encoding="utf-8")
    (tmp_path / "市级报告.DOC").write_text("DOC", encoding="utf-8")

    candidates = _file_candidates(
        tmp_path, "市级报告", extensions=(".doc", ".docx", ".pdf")
    )

    assert [path.suffix.upper() for path in candidates] == [".DOC", ".DOCX", ".PDF"]


def test_city_final_requires_common_quality_opinion_form(tmp_path):
    report_dir = tmp_path / "文字成果"
    report_dir.mkdir()
    (tmp_path / "delivery_manifest.json").write_text(
        '{"project_id":"P1","delivery_level":"city_final",'
        '"project_type":"non_project","pollution_report_required":false,'
        '"reevaluation":false,"manual_reviews":{}}',
        encoding="utf-8",
    )
    (report_dir / "市级补充耕地质量鉴定报告.docx").write_text("报告", encoding="utf-8")
    (report_dir / "市级补充耕地质量鉴定意见.docx").write_text("意见", encoding="utf-8")

    report = check_package(tmp_path)

    opinion_form = next(
        row for row in report["checked_files"]
        if row["item"] == "文字成果/补充耕地质量鉴定意见表.pdf"
    )
    assert opinion_form["status"] == "MISSING"
    assert report["status"] == "FAIL"


def test_lab_chain_requires_different_institutions_when_both_stages_test(tmp_path):
    manifest = {
        "delivery_level": "city_final",
        "lab_chain": {
            "county_initial": {"performed": True, "institution_id": "91450100ABC"},
            "city_final": {"performed": True, "institution_id": "91450100abc"},
        },
    }

    result = _lab_chain_check(tmp_path, manifest)

    assert result["status"] == "FAIL"
    assert "6.3.1" in result["detail"]


def test_lab_chain_review_is_city_only_and_never_duplicated(tmp_path):
    city_root = tmp_path / "city"
    city_root.mkdir()
    (city_root / "delivery_manifest.json").write_text(json.dumps({
        "project_id": "P1", "delivery_level": "city_final",
        "manual_reviews": {"lab_institution_independence_reviewed": False},
    }), encoding="utf-8")
    city_result = check_package(city_root)
    assert city_result["pending_manual_reviews"].count("lab_institution_independence_reviewed") == 1

    county_root = tmp_path / "county"
    county_root.mkdir()
    (county_root / "delivery_manifest.json").write_text(json.dumps({
        "project_id": "P1", "delivery_level": "county_initial",
        "manual_reviews": {"lab_institution_independence_reviewed": False},
    }), encoding="utf-8")
    county_result = check_package(county_root)
    assert "lab_institution_independence_reviewed" not in county_result["pending_manual_reviews"]


def test_lab_chain_marks_distinct_institution_ids_for_document_review(tmp_path):
    manifest = {
        "delivery_level": "city_final",
        "lab_chain": {
            "county_initial": {"performed": True, "institution_id": "91450100ABC"},
            "city_final": {"performed": True, "institution_id": "91450100XYZ"},
        },
    }

    result = _lab_chain_check(tmp_path, manifest)

    assert result["status"] == "NEEDS_REVIEW"
    assert "官方登记资料" in result["detail"]


def test_lab_chain_requires_evidence_when_city_did_not_test(tmp_path):
    manifest = {
        "delivery_level": "city_final",
        "lab_chain": {
            "county_initial": {"performed": True, "institution_id": "91450100ABC"},
            "city_final": {"performed": False, "non_testing_evidence": "附件/市级不重复检测说明.pdf"},
        },
    }

    missing = _lab_chain_check(tmp_path, manifest)
    assert missing["status"] == "NEEDS_REVIEW"

    evidence = tmp_path / "附件" / "市级不重复检测说明.pdf"
    evidence.parent.mkdir()
    evidence.write_bytes(b"case-specific evidence")
    documented = _lab_chain_check(tmp_path, manifest)
    assert documented["status"] == "NOT_APPLICABLE_WITH_EVIDENCE"
