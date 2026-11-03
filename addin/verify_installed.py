"""安装态自检：比对 Pro 程序集缓存与已安装 .esriAddInX，发现“装的是新版、跑的是旧版”。

用法：
    py -3.12 addin/verify_installed.py            # 人类可读
    py -3.12 addin/verify_installed.py --json     # 机器可读

返回码：0 全部一致；1 有差异或找不到安装包；2 找不到缓存（Pro 尚未启动过）。
Pro 只在启动时刷新 AssemblyCache，所以安装后必须重启 Pro 再跑本检查。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path

ADDIN_ID = "{83A6E23E-58CE-4B92-BC7A-7C5012A8BC31}"


def documents_dir() -> Path:
    """取「我的文档」，跟随 OneDrive/组策略重定向（RegisterAddIn 也写到这里）。"""
    CSIDL_PERSONAL = 5
    buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                                             wintypes.DWORD, wintypes.LPWSTR]
        shell32.SHGetFolderPathW.restype = ctypes.c_long
        if shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    except OSError:
        pass
    print("!! 无法读取系统「我的文档」路径，回退到 USERPROFILE\\Documents；如目录被重定向可能误报",
          file=sys.stderr)
    return Path(os.environ.get("USERPROFILE", "")) / "Documents"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def installed_package():
    """用户级安装目录里最新的 .esriAddInX（RegisterAddIn 会把包复制到这里）。"""
    root = documents_dir() / "ArcGIS" / "AddIns" / "ArcGISPro" / ADDIN_ID
    packages = sorted(root.glob("*.esriAddInX"), key=lambda path: path.stat().st_mtime, reverse=True)
    return packages[0] if packages else None


def assembly_cache():
    local = os.environ.get("LOCALAPPDATA") or str(Path(os.path.expanduser("~")) / "AppData" / "Local")
    return Path(local) / "ESRI" / "ArcGISPro" / "AssemblyCache" / ADDIN_ID


def compare(package: Path, cache: Path):
    """逐条目比对包内 Install/ 与缓存（按相对路径，递归含 acceptance/*.py 等子目录）。

    只有同名顶层文件比对会漏掉 Python 引擎（装的新包、跑的旧 py）。
    """
    rows = []
    cache_files = {}
    for path in cache.rglob("*"):
        if path.is_file():
            # Pro 运行脚本时会在缓存目录生成 __pycache__/*.pyc；这是运行期产物，不是安装差异。
            if "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
                continue
            cache_files[path.relative_to(cache).as_posix()] = path
    with zipfile.ZipFile(package) as archive:
        by_rel = {}
        for name in archive.namelist():
            if name.startswith("Install/"):
                by_rel[name[len("Install/"):].replace("\\", "/")] = name
        for rel, entry in sorted(by_rel.items()):
            package_sha = _sha(archive.read(entry))
            path = cache_files.get(rel)
            if path is None:
                rows.append({"file": rel, "cache_sha256": None, "package_sha256": package_sha,
                             "status": "MISSING_IN_CACHE"})
                continue
            cache_sha = _sha(path.read_bytes())
            rows.append({"file": rel, "cache_sha256": cache_sha, "package_sha256": package_sha,
                         "status": "MATCH" if cache_sha == package_sha else "STALE"})
        for rel, path in sorted(cache_files.items()):
            if rel not in by_rel:
                rows.append({"file": rel, "cache_sha256": _sha(path.read_bytes()),
                             "package_sha256": None, "status": "NOT_IN_PACKAGE"})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="检查 Pro 程序集缓存是否与已安装包一致")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    package = installed_package()
    cache = assembly_cache()
    if package is None:
        print("未找到已安装的 .esriAddInX：{}".format(
            documents_dir() / "ArcGIS/AddIns/ArcGISPro" / ADDIN_ID))
        return 1
    if not cache.is_dir():
        print("未找到程序集缓存（Pro 尚未启动过）：{}".format(cache))
        return 2

    rows = compare(package, cache)
    stale = [row for row in rows if row["status"] != "MATCH"]
    payload = {"package": str(package), "cache": str(cache), "files": rows,
               "checked": len(rows), "stale": len(stale), "status": "STALE" if stale else "MATCH"}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("安装包：{}".format(package.name))
        print("缓存：{}".format(cache))
        print("比对条目：{}（逐文件，含 acceptance/*.py 等子目录）".format(len(rows)))
        for row in stale[:20]:
            print("  {:<48} {}".format(row["file"], row["status"]))
        if len(stale) > 20:
            print("  …还有 {} 项差异".format(len(stale) - 20))
        if stale:
            print("结论：缓存与安装包不一致（{} 项 / 共 {} 项）。请重启 ArcGIS Pro 让其刷新缓存后再检查。".format(
                len(stale), len(rows)))
        else:
            print("结论：{} 项全部一致（含 Python 引擎文件）。".format(len(rows)))
            print("结论：缓存与安装包一致。")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
