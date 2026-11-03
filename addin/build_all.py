"""一条命令完成插件「编译 → 打包 → 安装 → 校验」。

今天手工跑了三轮 build_roslyn → package → 关闭 Pro → RegisterAddIn → 重启 → verify_installed，
每次都容易漏步骤（Pro 没关、装了旧包、哈希没核）。本脚本把这几步固定下来。

用法（仓库根目录或本目录均可）：
  py -3.12 land_survey_toolkit/addin/build_all.py                # 编译+打包+安装
  py -3.12 land_survey_toolkit/addin/build_all.py --skip-build    # 只打包+安装（改的是 py/配置）
  py -3.12 land_survey_toolkit/addin/build_all.py --no-install    # 只编译打包
  py -3.12 land_survey_toolkit/addin/build_all.py --kill-pro      # 自动关闭 Pro 再安装
  py -3.12 land_survey_toolkit/addin/build_all.py --version 1.77  # 改 DAML 版本后构建

安装后必须重启 ArcGIS Pro 才会加载新包；脚本最后会提示运行 verify_installed.py。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ADDIN = Path(__file__).resolve().parent
TOOLKIT = ADDIN.parent
DAML = ADDIN / "AcceptanceAddin" / "Config.daml"
ADDIN_GUID = "{83A6E23E-58CE-4B92-BC7A-7C5012A8BC31}"


def run(cmd: list[str], cwd: Path) -> int:
    print(">> " + " ".join(str(item) for item in cmd))
    return subprocess.call([str(item) for item in cmd], cwd=str(cwd))


def address_of(pro_home: str | None) -> Path:
    """定位 RegisterAddIn.exe：--pro-home > ARCGIS_PRO_HOME > 注册表 InstallDir。

    不再兜底猜盘符：硬编码安装路径在别人的机器上必然错，找不到时由调用方
    报错并提示用 --pro-home 指定。
    """
    if pro_home:
        return Path(pro_home) / "bin" / "RegisterAddIn.exe"
    if os.environ.get("ARCGIS_PRO_HOME"):
        return Path(os.environ["ARCGIS_PRO_HOME"]) / "bin" / "RegisterAddIn.exe"
    try:
        import winreg
    except ImportError:
        return Path("RegisterAddIn.exe")
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\ESRI\ArcGISPro") as key:
                try:
                    install, _ = winreg.QueryValueEx(key, "InstallDir")
                except OSError:
                    continue
                if str(install).strip():
                    return Path(str(install)) / "bin" / "RegisterAddIn.exe"
        except OSError:
            continue
    return Path("RegisterAddIn.exe")


def pro_running() -> list[str]:
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq ArcGISPro.exe", "/FO", "CSV", "/NH"],
            text=True, errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in output.splitlines() if "ArcGISPro.exe" in line]


def bump_version(version: str) -> str:
    """改 DAML 版本；返回原文，构建失败时由调用方回滚。"""
    text = DAML.read_text(encoding="utf-8")
    updated, count = re.subn(r'(<AddInInfo[^>]*\bversion=")([^"]+)(")', r"\g<1>" + version + r"\g<3>", text)
    if count != 1:
        raise SystemExit(f"Config.daml 里没找到唯一的 version 属性（匹配 {count} 处）")
    DAML.write_text(updated, encoding="utf-8")
    print(f">> DAML 版本已改为 {version}")
    return text


def restore_daml(text: str) -> None:
    DAML.write_text(text, encoding="utf-8")
    print(">> 构建失败，已把 Config.daml 版本改回原值")


def current_version() -> str:
    match = re.search(r'<AddInInfo[^>]*\bversion="([^"]+)"', DAML.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("Config.daml 缺少 version 属性")
    return match.group(1)


def latest_package() -> Path:
    """优先用 build-receipt 记录的包，避免按 mtime 误选 dist 里别人遗留的测试包。"""
    receipt = ADDIN / "dist" / "build-receipt.json"
    if receipt.is_file():
        try:
            import json
            data = json.loads(receipt.read_text(encoding="utf-8-sig"))
            candidate = Path(data.get("path", ""))
            if candidate.is_file():
                return candidate
        except (OSError, ValueError):
            pass
    packages = sorted((ADDIN / "dist").glob("*.esriAddInX"),
                      key=lambda item: item.stat().st_mtime, reverse=True)
    if not packages:
        raise SystemExit("dist 目录没有安装包")
    return packages[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def documents_dir() -> Path:
    """复用 verify_installed 的实现，保证「安装目录解析」只有一份。"""
    from verify_installed import documents_dir as shared
    return shared()


def install(package: Path, register: Path) -> Path:
    expected = (documents_dir() / "ArcGIS" / "AddIns" / "ArcGISPro" / ADDIN_GUID / package.name)
    code = subprocess.call([str(register), str(package), "/s"], shell=False)
    if code != 0:
        raise SystemExit(f"RegisterAddIn 退出码 {code}")
    deadline = time.time() + 20
    source = sha256(package)
    while time.time() < deadline:
        if expected.is_file() and sha256(expected) == source:
            print(f">> 已安装并核对哈希：{expected.name}")
            return expected
        time.sleep(0.5)
    raise SystemExit(f"安装后未在用户目录找到与包哈希一致的文件：{expected}")


def verify(installed: Path) -> None:
    script = ADDIN / "verify_installed.py"
    if not script.is_file():
        print("!! 找不到 verify_installed.py，跳过安装态校验")
        return
    code = run([sys.executable, str(script)], ADDIN)
    if code != 0:
        print("!! 安装态校验未通过：ArcGIS Pro 需要重启后才会加载新包，重启后再跑一次本校验")
    elif not pro_running():
        print("!! 缓存与包一致，但 Pro 未运行；启动 Pro 后请再跑一次 verify_installed.py")


def pro_python() -> Path:
    """定位 ArcGIS Pro 自带 Python（评估集与 arcpy 需要）。"""
    candidates = []
    if os.environ.get("ARCGIS_PRO_HOME"):
        candidates.append(Path(os.environ["ARCGIS_PRO_HOME"]) / "bin" / "Python" / "envs" /
                          "arcgispro-py3" / "python.exe")
    register = address_of(None)
    if register.name.lower() == "registeraddin.exe":
        # <InstallDir>/bin/RegisterAddIn.exe → <InstallDir>/bin/Python/envs/arcgispro-py3/python.exe
        candidates.append(register.parent / "Python" / "envs" / "arcgispro-py3" / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path("python.exe")


def _pytest_summary(python: Path, targets: list[str], timeout: int = 2400) -> dict:
    """跑一次 pytest 并抓摘要行；返回可直接写进收据的字典。"""
    cmd = [str(python), "-m", "pytest", *targets, "-q"]
    print(">> 门禁：" + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=str(TOOLKIT), capture_output=True, text=True,
                              errors="replace", timeout=timeout)
        stdout = proc.stdout or ""
        code = proc.returncode
    except subprocess.TimeoutExpired:
        stdout, code = "", 124
    lines = [line.strip() for line in stdout.splitlines()
             if ("passed" in line or "failed" in line or "error" in line) and line.strip()]
    summary = lines[-1] if lines else ""
    print("   " + (summary or ("exit=" + str(code))))
    return {"python": str(python), "targets": targets, "exit": code, "summary": summary[-240:]}


def write_gate_receipt(suites: list[dict], ok: bool, extras: dict | None = None) -> Path:
    """把门禁结果写进 dist/gate-receipt.json，作为发布依据。"""
    receipt = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": ok,
        "suites": suites,
    }
    if extras:
        receipt.update(extras)
    path = ADDIN / "dist" / "gate-receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f">> 门禁收据：{path}")
    return path


def run_gate() -> int:
    """发布门禁：系统 Python + Pro Python 双轨测试，再跑评估集。

    本机 pytest 会把依赖 arcpy 的用例 skip 掉，所以单靠一轨不足以发布；
    双轨都过（且 Pro 轨真实执行了几何用例）才允许打包。
    """
    python = pro_python()
    if not python.is_file():
        raise SystemExit("--gate 需要 ArcGIS Pro 自带 Python（arcpy）；找不到时请去掉 --gate")
    script = TOOLKIT / "scripts" / "eval_suite.py"
    if not script.is_file():
        raise SystemExit(f"--gate 找不到评估脚本：{script}")

    suites = []
    # 轨 1：系统 Python 全量（快，覆盖纯函数与契约）
    suites.append(dict(suite="system", **_pytest_summary(Path(sys.executable), ["tests/"])))
    # 轨 2：Pro Python 真实几何（arcpy；系统轨会 skip 的部分；只选单文件可独立跑且耗时可接受的用例）
    pro_targets = ["tests/test_unit_builder.py", "tests/test_grid_sampling.py"]
    suites.append(dict(suite="pro", **_pytest_summary(python, pro_targets, timeout=900)))

    failed = [row for row in suites if row["exit"] != 0]
    if failed:
        write_gate_receipt(suites, ok=False)
        print("!! 门禁未通过：" + "、".join(row["suite"] for row in failed))
        return 1

    print(">> 门禁：评估集（合成样例 + 真实项目）…")
    eval_code = run([str(python), str(script)], TOOLKIT)
    suites.append({"suite": "eval", "python": str(python), "targets": ["scripts/eval_suite.py"],
                   "exit": eval_code, "summary": "eval_suite"})
    write_gate_receipt(suites, ok=(eval_code == 0))
    return 0 if eval_code == 0 else 1


def build_package(args) -> int:
    """编译 + 打包；失败时把 DAML 版本改回原值。返回退出码。"""
    original = bump_version(args.version) if args.version else ""
    if args.pro_home:
        os.environ["ARCGIS_PRO_HOME"] = args.pro_home  # 让 build_roslyn 也用同一个 Pro 目录
    try:
        if not args.skip_build and run([sys.executable, "build_roslyn.py"], ADDIN) != 0:
            if original:
                restore_daml(original)
            return 1
        package_script = "package.py"
        package_args = [sys.executable, package_script]
        if getattr(args, "allow_dirty", False):
            package_args.append("--allow-dirty")
        if run(package_args, ADDIN) != 0:
            if original:
                restore_daml(original)
            return 1
    except SystemExit:
        if original:
            restore_daml(original)
        raise
    return 0


def install_package(package: Path, args) -> int:
    """校验安装器 → 处理运行中的 Pro → 安装 → 校验安装态。"""
    # 先解析安装器，避免 --pro-home 写错时已经先把 Pro 关掉
    register = address_of(args.pro_home)
    if not register.is_file():
        raise SystemExit(f"找不到 RegisterAddIn.exe：{register}（用 --pro-home 指定）")

    running = pro_running()
    if running and not args.kill_pro:
        raise SystemExit("ArcGIS Pro 正在运行，请先保存并关闭，或加 --kill-pro 自动关闭")
    if running and args.kill_pro:
        print(">> 将强制关闭 ArcGIS Pro（未保存内容会丢失）…")
        subprocess.call(["taskkill", "/IM", "ArcGISPro.exe", "/F"], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        for _ in range(20):  # 最多等 10 秒确认退出
            time.sleep(0.5)
            if not pro_running():
                break
        if pro_running():
            raise SystemExit("ArcGIS Pro 仍未退出，安装中止；请手动关闭后重试")

    installed = install(package, register)
    verify(installed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="编译、打包、安装并校验统一验收插件")
    parser.add_argument("--skip-build", action="store_true", help="跳过 C# 编译，仅打包（改 Python/配置时用）")
    parser.add_argument("--no-install", action="store_true", help="只编译打包，不安装")
    parser.add_argument("--kill-pro", action="store_true", help="安装前自动关闭 ArcGIS Pro")
    parser.add_argument("--pro-home", default=os.environ.get("ARCGIS_PRO_HOME"), help="ArcGIS Pro 安装目录")
    parser.add_argument("--version", default="", help="构建前把 DAML 版本改成指定值")
    parser.add_argument("--gate", action="store_true", help="打包前跑评估集，全部通过才继续")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="工作区有未提交改动也允许打包（发行包收据会记录 dirty 清单）")
    args = parser.parse_args()

    if args.version and args.skip_build:
        print("!! --version 搭配 --skip-build：DLL 里的程序集版本仍是旧的，只改 DAML 与包名")
    if args.gate and run_gate() != 0:
        print("!! 评估集未全过，已中止打包；先修实现，不要靠改期望值过门禁", file=sys.stderr)
        return 1
    if build_package(args) != 0:
        return 1

    package = latest_package()
    print(f">> 安装包：{package.name}  {package.stat().st_size} B  SHA-256 {sha256(package)}")

    if args.no_install:
        print(f">> 版本 {current_version()}；已跳过安装（--no-install）")
        return 0

    code = install_package(package, args)
    print(f">> 完成：版本 {current_version()}，包 {package.name}")
    print(">> 下一步：启动 ArcGIS Pro（会加载新包），再跑 addin/verify_installed.py 复核")
    return code


if __name__ == "__main__":
    sys.exit(main())
