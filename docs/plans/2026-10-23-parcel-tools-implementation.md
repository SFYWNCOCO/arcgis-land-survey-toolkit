# Parcel Tools Implementation Plan

> 执行技能：executing-plans；在当前共享工作树中增量实施，保留原有未提交工作。

**Goal:** 在现有 ArcGIS Pro 插件中实现地块定位、质检、整理和批量制图。

**Architecture:** C# 无模式定位窗口直接使用 ArcGIS Pro SDK。其余按钮打开独立 Python Toolbox，ArcPy 核心处理复制数据与报告，所有输出拒绝覆盖。

**Tech Stack:** ArcGIS Pro 3.1.5 / .NET 6 WPF / ArcPy / Python 标准库。

---

### Task 1: 定位窗口
- 新增 addin/AcceptanceAddin/ParcelLocatorWindow.cs，Commands/ParcelButtons.cs。
- 修改 Config.daml 新增分组和按钮；ToolboxLocator.cs 支持工具箱文件名。
- csproj、build_roslyn.ps1 增加 ArcGIS.Core / Mapping 引用。
- 验证精确查询转义、重复结果、无活动地图与字段不存在提示；Roslyn 编译验证当前 SDK 契约。

### Task 2: 数据处理核心
- 新增 acceptance/parcel_tools.py：面积算法、质检、复制整理和导出函数。
- 新增 tests/test_parcel_tools.py：口径一致、反向错误对照、输出冲突、整理失败清理、源不变。
- 使用 Pro Python 对真实 Shapefile 执行 pytest 与真实运行收据。

### Task 3: 工具箱与制图
- 新增 toolboxes/ParcelTools.pyt，明确参数依赖、面积单位和必填字段可选。
- 制图使用 Pro 内置 A4 横向 .pagx，运行时创建临时模板并补入元信息；自定义模板也验证地图框等元素。
- GP 动态导入并实际调用工具，确认生成成果数量。

### Task 4: 打包和交付
- 修改 package.ps1、csproj 打包新工具箱；更新版本和 README。
- 运行 addin/package.ps1 -UseRoslynFallback，读包检查新 DLL/工具箱/核心哈希。
- 真实运行输出放 temp/，交付包复制到用户目录的新文件名；不覆盖旧包。
- 更新 docs/parcel-tools.md 记录操作步骤、实测证据和 UI 验证限制。
