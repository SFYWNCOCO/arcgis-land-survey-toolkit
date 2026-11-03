"""Build the unified workbench, its source bundle, or the shared C# test harness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "ai"
UNIFIED = ROOT.parent / "addin"
WORK = ROOT / "build"
CSC = Path(
    os.environ.get(
        "CSC_PATH",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe",
    )
)
RUNTIME = Path(os.environ.get("DOTNET_SHARED", r"C:\Program Files\dotnet\shared"))


def compile_csharp(harness=True):
    if not harness:
        subprocess.run([sys.executable, str(UNIFIED / "build_roslyn.py")], check=True)
        return (
            UNIFIED
            / "AcceptanceAddin/bin/Release/net6.0-windows/win10-x64/AcceptanceAddin.dll"
        )
    WORK.mkdir(exist_ok=True)
    refs = {}
    versions = []
    for framework in ("Microsoft.NETCore.App", "Microsoft.WindowsDesktop.App"):
        versions_for_framework = sorted(
            (p for p in (RUNTIME / framework).glob("6.*") if p.is_dir()),
            key=lambda p: tuple(int(x) for x in p.name.split(".")),
        )
        if not versions_for_framework:
            raise RuntimeError(f".NET 6 runtime missing: {framework}")
        directory = versions_for_framework[-1]
        versions.append(directory.name)
        for dll in directory.glob("*.dll"):
            if "Native" in dll.name or dll.name.startswith("api-ms-"):
                continue
            if dll.name.startswith(("System.", "Microsoft.")) or dll.name in {
                "mscorlib.dll",
                "netstandard.dll",
                "WindowsBase.dll",
                "PresentationCore.dll",
                "PresentationFramework.dll",
                "System.Xaml.dll",
                "Accessibility.dll",
                "UIAutomationClient.dll",
                "UIAutomationTypes.dll",
                "DirectWriteForwarder.dll",
            }:
                refs[dll.name] = dll
    sources = [
        ROOT / "addin" / name
        for name in (
            "ToolProtocol.cs",
            "Settings.cs",
            "ModelClient.cs",
            "PaneState.cs",
            "AnalysisPlan.cs",
            "GpJob.cs",
            "BasemapPreview.cs",
        )
    ]
    sources.append(ROOT / "tests/CoreHarness.cs")
    sources += sorted((UNIFIED / 'AcceptanceAddin').glob('Workbench*.cs'))
    output = WORK / "CoreHarness.dll"
    args = [
        "/nostdlib",
        "/utf8output",
        "/target:exe",
        "/platform:x64",
        "/langversion:latest",
        f'/out:"{output}"',
    ]
    args += [f'/reference:"{path}"' for path in refs.values()]
    args += [f'"{path}"' for path in sources]
    response = WORK / "harness.rsp"
    response.write_text("\n".join(args), encoding="utf-8")
    proc = subprocess.run(
        [str(CSC), "@" + str(response)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise RuntimeError(proc.stdout + proc.stderr)
    shutil.copy2(ROOT / "tools.json", WORK / "tools.json")
    (WORK / "CoreHarness.runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": "net6.0",
                    "frameworks": [
                        {"name": "Microsoft.NETCore.App", "version": versions[0]},
                        {
                            "name": "Microsoft.WindowsDesktop.App",
                            "version": versions[1],
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    print("CORE_HARNESS_OK", output)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", action="store_true")
    parser.add_argument("--bundle", action="store_true")
    opts = parser.parse_args()
    if opts.harness:
        compile_csharp()
    elif opts.bundle:
        subprocess.run(
            [sys.executable, str(UNIFIED / "package.py"), "--bundle"], check=True
        )
    else:
        compile_csharp(False)
        subprocess.run([sys.executable, str(UNIFIED / "package.py")], check=True)
