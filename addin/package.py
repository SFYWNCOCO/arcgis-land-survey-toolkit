"""打包 .esriAddInX（Python 版，替代 package.ps1）。

同样的校验：DAML 按钮 ID 唯一、命令类在 C# 源码中真实存在、KeyTip 不重复、
引用按钮都存在；打包后逐项读回校验条目与哈希。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

ADDIN = Path(__file__).resolve().parent
TOOLKIT = ADDIN.parent
PROJECT = ADDIN / "AcceptanceAddin"
DAML = PROJECT / "Config.daml"
DLL = (
    PROJECT / "bin" / "Release" / "net6.0-windows" / "win10-x64" / "AcceptanceAddin.dll"
)
TOOLBOXES = TOOLKIT / "toolboxes"
ACCEPTANCE = TOOLKIT / "acceptance"
SCRIPTS = TOOLKIT / "scripts"
TEMPLATES = TOOLKIT / "templates"
VENDOR = TOOLKIT / "vendor"
CONFIG = TOOLKIT / "config"
DIST = ADDIN / "dist" / "SupplementaryCultivatedLandAcceptance.esriAddInX"
TOOLBOX_METADATA = (
    "GridSampling.pyt.xml",
    "SupplementaryCultivatedLandAcceptance.pyt.xml",
)


def local(tag):
    return tag.split("}")[-1]


def validate():
    text = DAML.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    controls = {}
    keytips = {}
    for node in root.iter():
        if local(node.tag) == "button" and node.get("id"):
            cid = node.get("id")
            if cid in controls:
                raise SystemExit("DAML 控件 ID 重复：" + cid)
            controls[cid] = node.get("className")
            key = (node.get("keytip") or "").upper()
            if key:
                if key in keytips:
                    raise SystemExit(
                        f"KeyTip 重复：{key}（{keytips[key]} / {cid}）"
                    )
                keytips[key] = cid
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in PROJECT.rglob("*.cs")
        if "bin" not in p.parts and "obj" not in p.parts
    )
    for cid, cls in controls.items():
        if not re.search(r"\bclass\s+" + re.escape(cls) + r"\b", sources):
            raise SystemExit(f"DAML 命令类未在 C# 源码中定义：{cls}（{cid}）")
    for node in root.iter():
        if local(node.tag) == "button" and node.get("refID") and node.get("refID") not in controls:
            raise SystemExit("功能区引用了不存在的控件：" + node.get("refID"))
    panes = [n.get("className") for n in root.iter() if local(n.tag) == "dockPane"]
    for cls in panes:
        if not re.search(r"\bclass\s+" + re.escape(cls) + r"\b", sources):
            raise SystemExit("DAML 面板类未在 C# 源码中定义：" + cls)
    return {"controls": len(controls), "keytips": len(keytips), "dockPanes": panes}


def entries():
    if not DLL.is_file():
        raise SystemExit("未找到 DLL，请先运行 build_roslyn.py：" + str(DLL))
    items = [(DAML, "Config.daml"), (DLL, "Install/AcceptanceAddin.dll")]
    items.append((TOOLKIT / "ai/tools.json", "Install/tools.json"))
    for filename, target in (
        ("NOTICE.md", "NOTICE-pro-ai-bridge.md"),
        ("LICENSE", "LICENSE-pro-ai-bridge.txt"),
        ("LICENSE-upstream.txt", "LICENSE-arcmap-mcp.txt"),
    ):
        items.append((TOOLKIT / "ai" / filename, "Install/" + target))
    for pyt in sorted(TOOLBOXES.glob("*.pyt")):
        items.append((pyt, "Install/toolboxes/" + pyt.name))
    for name in TOOLBOX_METADATA:
        meta = TOOLBOXES / name
        if meta.is_file():
            items.append((meta, "Install/toolboxes/" + meta.name))
    for src in sorted(ACCEPTANCE.rglob("*.py")):
        if "__pycache__" in src.parts:
            continue
        rel = src.relative_to(ACCEPTANCE).as_posix()
        items.append((src, "Install/acceptance/" + rel))
    for src in sorted(TEMPLATES.iterdir()):
        if src.is_file():
            items.append((src, "Install/templates/" + src.name))
    for src in sorted(VENDOR.rglob("*")):
        if src.is_file() and "__pycache__" not in src.parts and src.suffix != ".pyc":
            items.append((src, "Install/vendor/" + src.relative_to(VENDOR).as_posix()))
    for src in sorted(CONFIG.rglob("*")):
        if src.is_file() and "__pycache__" not in src.parts:
            items.append((src, "Install/config/" + src.relative_to(CONFIG).as_posix()))
    for name in (
        "setup_quality_gdb.py",
        "delivery_package.py",
        "build_field_forms.py",
        "evaluate_acceptance.py",
        "verify_rule_configs.py",
        "field_package.py",
        "photo_geotag.py",
        "field_return_qa.py",
    ):
        path = SCRIPTS / name
        if path.is_file():
            items.append((path, "Install/scripts/" + name))
    return items


def source_state():
    """读取 git 提交与工作区脏清单：用于发布收据和脏树拦截。

    返回 (commit, dirty)。git 不可用时 dirty 为 ["<git 不可用>"]，按脏处理（fail-closed）。
    """
    def git(*args):
        try:
            proc = subprocess.run(["git", *args], cwd=str(TOOLKIT), capture_output=True,
                                  text=True, errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return proc.returncode, proc.stdout.strip()

    code, commit = git("rev-parse", "HEAD")
    commit = commit.splitlines()[0] if code == 0 and commit else ""
    code, status = git("status", "--porcelain", "--", ".")
    if code != 0:
        return commit, ["<git 不可用>"]
    dirty = [line for line in status.splitlines() if line.strip()]
    return commit, dirty


def build(allow_dirty=False):
    global DIST
    info = validate()
    commit, dirty = source_state()
    if dirty and not allow_dirty:
        print("PACKAGE_BLOCKED：工作区有未提交改动（{} 项），拒绝打包".format(len(dirty)))
        for line in dirty[:10]:
            print("  " + line)
        if len(dirty) > 10:
            print("  …还有 {} 项".format(len(dirty) - 10))
        print("确认要打包请加 --allow-dirty；收据会记录 git 提交与 dirty 清单")
        raise SystemExit(2)
    items = entries()
    version = next(
        node.get("version")
        for node in ET.parse(DAML).iter()
        if local(node.tag) == "AddInInfo"
    )
    DIST = (
        ADDIN
        / "dist"
        / (
            "SupplementaryCultivatedLandAcceptance_{}_{}.esriAddInX".format(
                version, datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            )
        )
    )
    DIST.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(DIST, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in items:
            zf.write(src, arc)
    with zipfile.ZipFile(DIST) as zf:
        names = zf.namelist()
        bad = zf.testzip()
        if bad:
            raise SystemExit("压缩包校验失败：" + bad)
        for src, arc in items:
            if (
                hashlib.sha256(zf.read(arc)).digest()
                != hashlib.sha256(src.read_bytes()).digest()
            ):
                raise SystemExit("压缩包内容与源码不一致：" + arc)
    required = [
        "Config.daml",
        "Install/AcceptanceAddin.dll",
        "Install/toolboxes/GridSampling.pyt",
        "Install/toolboxes/GridSampling.pyt.xml",
        "Install/toolboxes/SupplementaryCultivatedLandAcceptance.pyt.xml",
        "Install/acceptance/oneclick.py",
        "Install/acceptance/grid_sampling.py",
        "Install/acceptance/unit_builder.py",
        "Install/acceptance/imagery.py",
        "Install/acceptance/map_worker.py",
        "Install/acceptance/map_export.py",
        "Install/acceptance/dem.py",
        "Install/acceptance/point_adjustment.py",
        "Install/acceptance/field_survey_bundle.py",
        "Install/acceptance/forms.py",
        "Install/acceptance/triage.py",
        "Install/acceptance/poi_reference.py",
        "Install/acceptance/ingest.py",
        "Install/vendor/docx/__init__.py",
        "Install/vendor/LICENSE-python-docx.txt",
        "Install/config/region_counties.json",
        "Install/scripts/build_field_forms.py",
        "Install/templates/表单_附录D_实地踏勘表.docx",
        "Install/templates/表单_附录E_土壤样品采集记录表.docx",
        "Install/templates/表单_附录F_补充耕地质量鉴定意见表.docx",
        "Install/templates/表单_样品标签.docx",
        "Install/templates/初鉴报告_附录L_模板.docx",
        "Install/templates/补充耕地鉴定图_A4.pagx",
    ]
    missing = [r for r in required if r not in names]
    if missing:
        raise SystemExit("压缩包缺条目：" + "、".join(missing))
    digest = hashlib.sha256(DIST.read_bytes()).hexdigest().upper()
    size = DIST.stat().st_size
    dll_sha256 = hashlib.sha256(DLL.read_bytes()).hexdigest().upper()
    receipt = {
        "path": str(DIST),
        "entries": len(names),
        "bytes": size,
        "sha256": digest,
        "dll_sha256": dll_sha256,
        "dll_bytes": DLL.stat().st_size,
        "version": version,
        "source_commit": commit,
        "source_dirty_count": len(dirty),
        "source_dirty": dirty[:20],
    }
    (DIST.parent / "build-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    release = dict(receipt)
    release["built_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    release["source"] = "addin/dist/build-receipt.json"
    (TOOLKIT / "docs" / "release-latest.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("PACKAGE_OK", DIST)
    print(f"entries={len(names)} bytes={size} sha256={digest}")
    print(
        "controls={} keytips={} dockPanes={}".format(
            info["controls"], info["keytips"], info["dockPanes"]
        )
    )
    return receipt


def bundle():
    receipt = json.loads(
        (ADDIN / "dist/build-receipt.json").read_text(encoding="utf-8")
    )
    package = Path(receipt["path"])
    if hashlib.sha256(package.read_bytes()).hexdigest().upper() != receipt["sha256"]:
        raise ValueError("发行包与构建收据不一致，不能生成完整包")
    with zipfile.ZipFile(package) as z:
        if z.testzip() or any(
            z.read(name) != path.read_bytes() for path, name in entries()
        ):
            raise ValueError("发行包内容已偏离当前构建，不能生成完整包")
    files = {TOOLKIT / "README.md", package, ADDIN / "dist/build-receipt.json"}
    for folder in (
        "acceptance",
        "arcgis_mcp",
        "toolboxes",
        "templates",
        "config",
        "vendor",
        "tests",
    ):
        files.update(
            p
            for p in (TOOLKIT / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    files.update(p for p in (TOOLKIT / "scripts").glob("*.py"))
    files.update(
        p for p in ADDIN.glob("*") if p.is_file() and p.suffix in (".py", ".ps1", ".md")
    )
    files.update(
        p
        for p in PROJECT.rglob("*")
        if p.is_file()
        and not {"bin", "obj"} & set(p.parts)
        and p.suffix in (".cs", ".csproj", ".daml")
    )
    bridge = TOOLKIT / "ai"
    files.add(TOOLKIT / "build.py")
    files.add(bridge / "LICENSE")
    files.update(
        p
        for p in bridge.iterdir()
        if p.is_file() and p.suffix in (".py", ".json", ".txt", ".md", ".ps1")
    )
    for folder in ("addin", "tests"):
        files.update(
            p
            for p in (bridge / folder).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    files.update(p for p in (TOOLKIT / "docs").glob("*unified*") if p.is_file())
    files.update(p for p in (TOOLKIT / "docs/plans").glob("*unified*") if p.is_file())
    target = (
        ADDIN
        / "dist"
        / (
            "CultivatedLandAcceptance_{}_full_{}.zip".format(
                receipt["version"], datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            )
        )
    )
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(files):
            z.write(path, "ARCGIS/" + path.relative_to(TOOLKIT.parent).as_posix())
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        assert all(
            z.read("ARCGIS/" + path.relative_to(TOOLKIT.parent).as_posix()) == path.read_bytes()
            for path in files
        )
    result = dict(
        path=str(target),
        files=len(files),
        bytes=target.stat().st_size,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    (ADDIN / "dist/bundle-receipt.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="工作区有未提交改动也允许打包（收据记录 dirty 清单）")
    options = parser.parse_args()
    sys.exit(0 if (bundle() if options.bundle else build(options.allow_dirty)) else 1)
