"""用 Roslyn 直接编译插件（Python 版构建脚本，替代 build_roslyn.ps1）。

原 PowerShell 脚本依赖 here-string，本机 PS 5.1 解析失败；
这里用同样的参考集与编译参数直出 Release DLL，参数与 ps1 保持一致。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ADDIN = Path(__file__).resolve().parent
TOOLKIT = ADDIN.parent
PROJECT = ADDIN / "AcceptanceAddin"
AI_SOURCES = TOOLKIT / "ai" / "addin"
PRO_INSTALL = Path(os.environ.get("ARCGIS_PRO_HOME", r"D:\Program Files\ArcGIS\Pro"))
CSC = Path(os.environ.get('CSC_PATH', r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe"))
SHARED = Path(r"C:\Program Files\dotnet\shared")

METADATA = """using System.Reflection;
[assembly: AssemblyCompany("AcceptanceAddin")]
[assembly: AssemblyConfiguration("Release")]
[assembly: AssemblyFileVersion("{assembly_version}")]
[assembly: AssemblyInformationalVersion("{addin_version}")]
[assembly: AssemblyProduct("AcceptanceAddin")]
[assembly: AssemblyTitle("AcceptanceAddin")]
[assembly: AssemblyVersion("{assembly_version}")]
[assembly: System.Runtime.Versioning.TargetPlatform("Windows7.0")]
[assembly: System.Runtime.Versioning.SupportedOSPlatform("Windows7.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETCoreApp,Version=v6.0", FrameworkDisplayName = "")]
"""

REFERENCES = [
    "bin/ArcGIS.Desktop.Framework.dll",
    "bin/Extensions/DesktopExtensions/ArcGIS.Desktop.Extensions.dll",
    "bin/Extensions/Core/ArcGIS.Desktop.Core.dll",
    "bin/ArcGIS.Core.dll",
    "bin/Extensions/Mapping/ArcGIS.Desktop.Mapping.dll",
    "bin/Extensions/Editing/ArcGIS.Desktop.Editing.dll",
    "bin/Extensions/Catalog/ArcGIS.Desktop.Catalog.dll",
    "bin/Extensions/Layout/ArcGIS.Desktop.Layouts.dll",
]


def release_metadata(config_path=None):
    root = ET.parse(config_path or PROJECT / "Config.daml").getroot()
    info = next(node for node in root.iter() if node.tag.split("}")[-1] == "AddInInfo")
    version = info.get("version", "")
    parts = version.split(".")
    if not 1 <= len(parts) <= 4 or any(not part.isdigit() or int(part) > 65534 for part in parts):
        raise ValueError("DAML版本不能转换为程序集版本：" + version)
    assembly = ".".join(str(int(part)) for part in parts + ["0"] * (4 - len(parts)))
    return METADATA.format(assembly_version=assembly, addin_version=version)


def main():
    if not CSC.is_file():
        raise SystemExit("找不到 Roslyn csc.exe：{}".format(CSC))
    for rel in REFERENCES:
        if not (PRO_INSTALL / rel).is_file():
            raise SystemExit("找不到 ArcGIS Pro SDK 程序集：{}".format(PRO_INSTALL / rel))

    runtime_dirs = [SHARED / "Microsoft.NETCore.App" / "6.0.8",
                    SHARED / "Microsoft.WindowsDesktop.App" / "6.0.8"]
    refs = {}
    for directory in runtime_dirs:
        if not directory.is_dir():
            raise SystemExit("缺少 .NET 6 运行时目录：{}".format(directory))
        for dll in directory.glob("*.dll"):
            # 运行时目录里混有原生 DLL（api-ms-*、*.Native.*），csc 不能当程序集引用
            name = dll.name
            if name.startswith("api-ms-") or "Native" in name:
                continue
            if not (name.startswith("System.") or name.startswith("Microsoft.")
                    or name in {"mscorlib.dll", "netstandard.dll", "WindowsBase.dll",
                                "PresentationCore.dll", "PresentationFramework.dll",
                                "System.Xaml.dll", "Accessibility.dll", "UIAutomationClient.dll",
                                "UIAutomationTypes.dll", "DirectWriteForwarder.dll"}):
                continue
            refs[name] = str(dll)
    for rel in REFERENCES:
        refs[Path(rel).name] = str(PRO_INSTALL / rel)

    sources = [str(p) for p in PROJECT.rglob("*.cs")
               if "bin" not in p.parts and "obj" not in p.parts]
    sources += [str(p) for p in sorted(AI_SOURCES.glob('*.cs'))]
    if not sources:
        raise SystemExit("没有找到 .cs 源文件")

    work = Path(tempfile.mkdtemp(prefix="roslyn_build_"))
    succeeded = False
    try:
        metadata = work / "AssemblyMetadata.cs"
        metadata.write_text(release_metadata(), encoding="ascii")
        sources.append(str(metadata))
        output = work / "AcceptanceAddin.dll"
        response = work / "compile.rsp"
        args = ["/nostdlib", "/target:library", "/platform:x64", "/langversion:latest",
                "/deterministic", "/out:" + str(output)]
        # 中文源码必须显式指定 UTF-8，否则界面文字会乱码
        args.append("/utf8output")
        args += ['/reference:"{}"'.format(path) for path in sorted(refs.values())]
        args += ['"{}"'.format(path) for path in sources]
        response.write_text("\n".join(args), encoding="utf-8")

        proc = subprocess.run([str(CSC), "@" + str(response)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not output.is_file():
            print(proc.stdout[-4000:])
            print(proc.stderr[-4000:])
            raise SystemExit("Roslyn 编译失败")
        warnings = [line for line in (proc.stdout or "").splitlines() if "warning" in line.lower()]
        if warnings:
            print("编译警告 {} 条（前5）：".format(len(warnings)))
            for line in warnings[:5]:
                print("  ", line.strip()[:200])

        target = PROJECT / "bin" / "Release" / "net6.0-windows" / "win10-x64" / "AcceptanceAddin.dll"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, target)
        size = target.stat().st_size
        digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        succeeded = True
        print("ROSLYN_BUILD_OK", target, size, "bytes", "sha256=" + digest)
        print(json.dumps({"dll": str(target), "bytes": size, "sha256": digest, "sources": len(sources),
                          "references": len(refs)}, ensure_ascii=False))
    finally:
        if succeeded:
            expected_parent = Path(tempfile.gettempdir()).resolve()
            resolved_work = work.resolve()
            if resolved_work.parent != expected_parent or not resolved_work.name.startswith("roslyn_build_"):
                raise RuntimeError("Roslyn 临时目录超出预期位置，拒绝清理：{}".format(resolved_work))
            shutil.rmtree(work)
        elif work.exists():
            print("BUILD_RECEIPT", work)


if __name__ == "__main__":
    sys.exit(main())
