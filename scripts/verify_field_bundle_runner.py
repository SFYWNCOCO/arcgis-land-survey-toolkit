"""现场包的双击自检入口；只从包内加载核心，不依赖开发目录。"""
from pathlib import Path
import json
import runpy
import sys
import time
import uuid


def main():
    root = Path(__file__).resolve().parent.parent
    packages = list(root.glob("*.esriAddInX"))
    if len(packages) != 1:
        raise RuntimeError("测试包根目录必须只有一份 .esriAddInX 安装包")
    demo = root / "离线测试项目"
    with (demo / "测试项目说明.json").open(encoding="utf-8") as stream:
        metadata = json.load(stream)
    source = demo / Path(metadata["source"]).name
    dom = demo / Path(metadata["dom"]).name
    if not source.is_file() or not dom.is_file():
        raise FileNotFoundError("离线测试资料不完整，请完整解压测试包")
    output = root / ("自动检查结果_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    sys.argv = [str(root / "tools" / "verify_packaged_offline.py"), "--package", str(packages[0]),
                "--source", str(source), "--dom", str(dom), "--out", str(output)]
    print("正在离线检查安装包、中文路径、图斑、工作副本和三种图件。", flush=True)
    runpy.run_path(sys.argv[0], run_name="__main__")
    print("离线技术自检通过。结果目录：" + str(output), flush=True)
    print("合成资料仅用于测试；影像精度和业务内容仍需现场核实。", flush=True)


if __name__ == "__main__":
    main()
