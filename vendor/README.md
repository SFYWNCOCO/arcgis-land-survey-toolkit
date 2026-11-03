# vendor

- `docx/`：python-docx 1.2.0（MIT），复制自本机 `site-packages`。
- `LICENSE-python-docx.txt`：上游许可证。

用途：ArcGIS Pro 3.1.5 的 `arcgispro-py3` 未安装 python-docx；工具包在导入失败时把本目录加入 `sys.path`，避免要求用户改 Pro 环境。python-docx 仅依赖 `lxml` 与 `typing_extensions`，Pro Python 已具备。

升级方式：在系统 Python 安装新版后，重新复制 `docx` 包并保留本说明与许可证；不得手工改上游源码。
