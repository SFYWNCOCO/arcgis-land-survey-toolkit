import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from acceptance.project import create_project
from setup_quality_gdb import create_quality_gdb


@pytest.mark.parametrize("epsg", [True, 4525.9, 4525.0, "4525.9", 0, -1])
def test_project_rejects_invalid_wkid_before_creating_directories(tmp_path, epsg):
    root = tmp_path / "projects"

    with pytest.raises(ValueError, match="WKID|正整数"):
        create_project(root, "451234", "测试县", "P-001", epsg)

    assert not root.exists()


def _fake_arcpy_for_setup(monkeypatch, fail_fields=False, fail_gdb_creation=False):
    existing = set()

    def create_gdb(parent, name, _version):
        path = Path(parent) / name
        path.mkdir()
        existing.add(str(path))
        if fail_gdb_creation:
            raise RuntimeError("simulated partial CreateFileGDB failure")

    def create_feature_class(gdb, name, *_args, **_kwargs):
        existing.add(str(Path(gdb) / name))

    def add_fields(_feature_class, _fields):
        if fail_fields:
            raise RuntimeError("simulated AddFields failure")

    def delete(path):
        path = str(path)
        if Path(path).is_dir():
            shutil.rmtree(path)
        else:
            Path(path).unlink(missing_ok=True)
        existing.discard(path)

    fake = SimpleNamespace(
        Exists=lambda path: str(path) in existing or Path(path).exists(),
        SpatialReference=lambda wkid: SimpleNamespace(name="Synthetic CRS", factoryCode=wkid),
        management=SimpleNamespace(
            CreateFileGDB=create_gdb, CreateFeatureclass=create_feature_class,
            AddFields=add_fields, Delete=delete,
        ),
    )
    monkeypatch.setitem(sys.modules, "arcpy", fake)
    return fake


def test_quality_gdb_setup_rolls_back_partial_database_after_failure(tmp_path, monkeypatch):
    _fake_arcpy_for_setup(monkeypatch, fail_fields=True)
    gdb = tmp_path / "quality.gdb"

    with pytest.raises(RuntimeError, match="AddFields failure"):
        create_quality_gdb(gdb, 4525)

    assert not gdb.exists()
    assert not (tmp_path / "quality_setup.json").exists()


def test_quality_gdb_setup_rolls_back_partial_filegdb_creation(tmp_path, monkeypatch):
    _fake_arcpy_for_setup(monkeypatch, fail_gdb_creation=True)
    gdb = tmp_path / "quality.gdb"

    with pytest.raises(RuntimeError, match="partial CreateFileGDB"):
        create_quality_gdb(gdb, 4525)

    assert not gdb.exists()


def test_quality_gdb_setup_does_not_overwrite_existing_manifest(tmp_path, monkeypatch):
    _fake_arcpy_for_setup(monkeypatch)
    gdb = tmp_path / "quality.gdb"
    manifest = tmp_path / "quality_setup.json"
    manifest.write_text("keep existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="清单已存在"):
        create_quality_gdb(gdb, 4525)

    assert not gdb.exists()
    assert manifest.read_text(encoding="utf-8") == "keep existing"
