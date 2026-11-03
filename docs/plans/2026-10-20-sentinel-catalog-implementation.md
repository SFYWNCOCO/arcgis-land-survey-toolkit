# Sentinel-2 检索与影像准备实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 ArcGIS Pro 工具箱中加入显式 STAC 目录检索和已选 Sentinel-2 时相窗口裁切/镶嵌，自动检查足迹与 NoData 并将准备结果交给现有 NDVI 核心。

**Architecture:** 新建 `acceptance/stac.py` 管理请求、响应校验、资产白名单与候选日期组；扩展遥感核心负责通过 GDAL COG 窗口读取、输出统一网格并调用现有 NDVI 核心；Python Toolbox 提供检索与准备两个交互工具。所有外部请求由操作者显式启动，保留源数据和人工选景流程。

**Tech Stack:** ArcGIS Pro 3.1.5 / ArcPy 3.9.16、Python 3.9、GDAL、标准库 urllib/json/hashlib、Python Toolbox、pytest。

---

### Task 1: 建立 STAC 协议核心

**Files:**
- Create: `acceptance/stac.py`
- Create: `tests/test_stac.py`

**Step 1: 为 Item 解析、必需红光/NIR/SCL资产、反射率 scale/offset、NoData、日期组和不可信资产主机写失败测试。**

**Step 2: 仅运行 `py -3.12 -m pytest tests/test_stac.py -q`，确认缺失接口/校验导致测试失败。**

**Step 3: 实现纯 JSON 解析、固定 Earth Search 搜索入口、允许的 HTTPS S3 资产 host、请求超时、响应大小/类型校验、日期组构造和分页上限。**

**Step 4: 重跑该测试文件；再对真实前期/后期 STAC Item JSON 执行只读解析，确认得到 B04/B08/SCL、比例参数和处理基线。**

### Task 2: AOI 检索与日期组覆盖评估

**Files:**
- Modify: `acceptance/stac.py`
- Modify: `toolboxes/SupplementaryCultivatedLandAcceptance.pyt`
- Create: `toolboxes/SupplementaryCultivatedLandAcceptance.Sentinel2CatalogSearch.pyt.xml`
- Modify: `tests/test_stac.py`

**Step 1: 写输入检查测试：非面 AOI、未知 CRS、结束早于开始、云量越界和请求过宽均拒绝。**

**Step 2: 运行相关测试确认失败；实现 ArcPy AOI→WGS84 目录检索几何、查询前警告文本、日期/平台/基线组合键、相邻瓦片覆盖率/景级云量候选清单。**

**Step 3: 检索结果同时写 JSON 与 CSV；报告保留 AOI 指纹、来源、请求时间、候选影像、瓦片、景级云量、资产元数据、覆盖率和限制，不自动选择日期。**

**Step 4: 测试标准页与分页页、空结果、超时/HTTP 错误；用 Pro 3.1.5 实际 AOI 检索荔浦修仁，确认返回 2026-10-08 QDG/RDH 等邻接瓦片并读回覆盖率。**

### Task 3: 选景镶嵌与像元准备

**Files:**
- Modify: `acceptance/stac.py`
- Modify: `acceptance/remote_sensing.py`
- Create: `tests/test_stac_prepare.py`

**Step 1: 写失败测试：日期组不存在、检索 AOI 指纹不同、缺一个瓦段、比例参数缺失、跨平台未经确认、像元预算超限和不安全 asset URL 均拒绝。**

**Step 2: 实现从被选日期/平台/基线组读取全部相邻瓦片，限制到同一 AOI，按可靠的评价单元 CRS 生成固定10 m红光/NIR网格和最近邻20 m SCL网格。**

**Step 3: 使用 `gdal.Open('/vsicurl/...')` 后将本地 GDAL 数据集交给 `gdal.Warp`，设定源NoData/目标NoData、数据类型、压缩和确定输出网格；仅裁 AOI窗口。**

**Step 4: 输出到本次唯一新建目录，读回 CRS、范围、分辨率、波段数、NoData、文件大小与SHA-256；异常时仅清理本次创建的文件。**

**Step 5: 将实际资产日期、平台、基线、景级云量、瓦片清单、scale/offset、重采样方式、网格参数、足迹覆盖和 NoData 诊断写入 pair manifest。**

### Task 4: 工具箱工作流与复用 NDVI 核心

**Files:**
- Modify: `toolboxes/SupplementaryCultivatedLandAcceptance.pyt`
- Create: `toolboxes/SupplementaryCultivatedLandAcceptance.PrepareSentinel2Pair.pyt.xml`
- Modify: `tests/test_acceptance_plugin_contract.py`

**Step 1: 为候选日期选择列表、相同 AOI、跨平台确认、结果状态与源数据不变写工具契约测试。**

**Step 2: 将 `Sentinel2CatalogSearch` 与 `PrepareSentinel2Pair` 登记到 Toolbox，搜索输出可直接作为准备输入；日期列表由候选 JSON 生成。**

**Step 3: 准备工具调用 `run_ndvi_screening`，传入候选资产从 STAC 解析的比例/偏移、SCL 与源溯源信息；输出影像裁片、NDVI栅格、CSV、JSON与清晰的 `NEEDS_FIELD_REVIEW` 消息。**

**Step 4: 从 ArcGIS Pro 3.1.5 Python 环境动态加载 `.pyt` 并逐项核对工具参数、验证消息、实际运行结果及读回产物。**

### Task 5: 整体回归、Add-In 打包与文档

**Files:**
- Modify: `addin/AcceptanceAddin/Config.daml`
- Modify: `docs/remote-sensing-workflow.md`
- Modify: `docs/lipu-real-data-gap-analysis.md`
- Modify: `addin/verification/verification-record.md`

**Step 1: 运行遥感、工具箱、栅格目录和插件契约相关测试集，确保旧手工影像流程保持可用。**

**Step 2: 在真实修仁 AOI 下载/裁切两期所选日期的相邻瓦片，核对最终输出覆盖完整度、SCL NoData、10m/20m网格、NDVI统计和文件校验值；确认网络失败不会遗留半成品。**

**Step 3: DAML 更新至1.2，调用 `package.ps1 -SkipBuild`，逐个比较包内 `.pyt`、元数据、`acceptance/stac.py` 与源码 SHA-256；之后注册当前用户包并读回安装哈希。**

**Step 4: 若宿主没有 ArcGIS Pro SDK/.NET 6 SDK 构建能力，不增加新的 C# Ribbon 命令，也不宣称 Ribbon 新按钮完成；通过已注册的工具箱入口交付并记录当前 UI 加载限制。**

**Step 5: 更新操作手册和 Pro 实测收据，明确外部 AOI 请求、目录许可、时相选择、足迹覆盖、差异平台提示、GDAL读数及验收结论边界。**

