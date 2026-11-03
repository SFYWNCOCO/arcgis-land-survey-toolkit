# GIS 开源项目对标与本插件痛点

核查日期：2026-10-20。目标环境：ArcGIS Pro 3.1.5 / SDK 3.1 / Python 3.9.16。对比对象用于发现工程模式，不替代广西《补充耕地质量验收作业指导书（试行）》v1.1，也不改变其判据。GitHub issue/discussion 仅视为个案线索，不据此推断发生率。

## 开源项目对照

| 项目 | 用途、许可与兼容性 | 可迁移做法 | 观察到的限制或问题 |
|---|---|---|---|
| [Esri ArcGIS Pro SDK Community Samples](https://github.com/Esri/arcgis-pro-sdk-community-samples) | ArcGIS Pro Add-In / C#；Apache-2.0。当前主分支面向 Pro 3.7；只以 [3.1.0.41834 样例快照](https://github.com/Esri/arcgis-pro-sdk-community-samples/tree/3.1.0.41834)作为 Pro 3.1 参考，仍需本机 SDK 验证。 | Ribbon、DAML、Geoprocessing 对话框、停靠窗格、进度与日志反馈。 | 最新样例不能直接证明与 Pro 3.1.5 兼容；必须用目标版 SDK 构建和实际启动验收。 |
| [USGS GeMS Tools for ArcGIS Pro](https://github.com/doi-usgs/gems-tools-pro) | Python 3 地理处理工具箱，用于 GeMS 地质数据库创建、编辑、元数据和验证；仓库提供 CC0 许可说明。 | 数据字典/字段域驱动的 schema 检查、批量元数据检查、把建库规则放进可重复运行的工具箱。 | 目标是地质制图，不是耕地验收；GeMS schema、域值和质量规则均不可搬用。 |
| [QGIS Processing](https://docs.qgis.org/3.44/en/docs/user_manual/processing/index.html) | QGIS 算法框架，支持工具箱、参数、运行历史/日志、模型和批处理；QGIS 主项目为 GPL-2.0-or-later。 | 可复跑的参数记录、历史记录、可读日志、批处理和分步工作流组织。 | PyQGIS/provider 代码属于不同运行时与授权体系；本插件只借鉴界面/流程设计，不直接嵌入 QGIS 代码。 |
| [QField](https://github.com/opengisch/QField) | QGIS 外业采集应用，GPL-2.0-or-later；用于离线地图、表单、GNSS 和现场附件。 | 采样/踏勘表单分阶段保存、照片与点位关联、离线期间保留未同步记录。 | [外接 GNSS 断连](https://github.com/opengisch/QField/issues/7873)及[大数据量性能](https://github.com/opengisch/QField/issues/7747)是用户报告案例，不代表普遍问题；设备、协议、网络和数据规模必须分开记录。 |
| [Mergin Maps QGIS Plugin](https://github.com/MerginMaps/qgis-plugin) | 离线项目分发、同步、差异与冲突处理；QGIS 插件 GPL-3.0，Cloud 服务专有许可，CE 服务为 AGPL-3.0/商业双许可，组件许可各自核对。 | 导入/外业前后 schema 指纹，变更清单，冲突件保留和变更差异复核。 | 官方说明[图层 schema 变更需按项目发布流程](https://merginmaps.com/docs/manage/deploy-new-project/)；云端账号/同步不适用于本地 ArcGIS Pro 插件。 |
| [QuickOSM](https://github.com/QuickOSM/QuickOSM) | QGIS 插件，通过 Overpass API 查询 OSM；仓库 GPL-2.0。 | 遥感目录/在线服务调用时，区分客户端截止、网络失败和服务端响应，并留下可复现的请求参数。 | [Issue #402](https://github.com/QuickOSM/QuickOSM/issues/402)说明网络 timeout 与 Overpass 查询 timeout 容易混淆；它是具体环境个案，OSM 也不能替代正式项目资料。 |
| [Semi-Automatic Classification Plugin (SCP)](https://github.com/semiautomaticgit/SemiAutomaticClassificationPlugin) | QGIS 遥感预处理与分类插件；代码声明 GPL-3.0-or-later。 | 数据产品/波段/波段顺序、比例因子、掩膜、训练样本和结果精度的显式核验。 | [B8/B8A 顺序讨论](https://github.com/semiautomaticgit/SemiAutomaticClassificationPlugin/discussions/411)及[预览与正式分类结果问题](https://github.com/semiautomaticgit/SemiAutomaticClassificationPlugin/issues/420)提示输入映射需要测试；分类结果不能作为广西质量等级判据。 |
| [GDAL COG](https://gdal.org/en/stable/drivers/raster/cog.html) | COG 读写驱动；GDAL 源码总体为 MIT-style，具体依赖须单独核许可。 | 核验 HTTP Range、IFD/瓦片/overview/mask 布局，日志保留 GDAL 版本、失败块和服务响应。 | 格式满足 COG 布局不等于每个 GDAL 版本、HTTP 服务或压缩/掩膜组合均可稳定读取。GDAL [Issue #9563](https://github.com/OSGeo/gdal/issues/9563)是已修复的特定 S3/mask/JPEG 场景，不能推定为本地 `TIFFReadEncodedTile()` 同一根因。 |
| [CAD to GIS Studio](https://github.com/ricbencar/cad-to-gis-convert) | DXF/DWG 转 GIS；MIT。DWG 需外部转换器；项目默认 CRS 为葡萄牙 EPSG:3763。 | CAD 处理前显式确认单位/坐标参数；输出后回读要素数、CRS、几何并生成 QA 记录。 | 外部转换依赖和默认 CRS 不适合广西；PRJ/WKID 赋值仍不等于控制点配准，广西项目参数须来自测量资料。 |
| [sgsR](https://github.com/tgoodbody/sgsR) | R 语言结构化/分层抽样工具；GPL-3.0。研究对象为森林/遥感辅助抽样。 | 比较系统、分层和约束抽样的可追踪参数与候选点生成思路。 | 不能复制其样本数量、间距、可达性参数作为广西验收规则；首版只参考算法组织。 |

## 与本插件的差距映射

| 优先级 | 本插件现状 | 可借鉴的工程改进 | 验收边界 |
|---|---|---|---|
| P0：Pro 实机闭环 | Add-In 1.20 的源码/包校验有记录；新版 Ribbon 加载、按钮定位、GP 参数界面和按钮触发的实际运行未完成桌面验收。 | Esri 3.1 SDK 样例的控件映射检查；按 QGIS Processing 的思路记录每次工具参数/日志。 | 必须在 Pro 3.1.5 中安装并打开工程，点验全部按钮；ArcPy 直接调用不算 Ribbon 验收。 |
| P1：成果内容与证据链 | 汇交工具可检查目录、部分 GDB schema 和候选文件；图面比例尺/内容、签章、照片真实性、CMA 资质、跨成果一致性仍要人工审。 | GeMS 的 schema 驱动验证；Mergin 的变更/冲突记录；QField 的现场附件与采集记录关联。 | 自动检查只报告可计算项；现场真实性、签章、资质和专家意见保持 `NEEDS_REVIEW`。 |
| P1：CAD/RTK 与外业 | 真实业务 CAD 控制点/残差、RTK 原始观测/高程转换、现场采样与检测证据尚未取得或验证。 | CAD 转换后读回几何/CRS/要素数；QField 外业证据关联模式。 | 不猜 CRS、不以坐标文件声明代替配准和基准核验。 |
| P2：报告与规范图件 | 正式报告和广西规范图件尚未自动生成；模拟报告只验证结构，不是验收成果。 | QGIS 模型/日志的可重复步骤；CAD/GIS 清单与 QA 回读。 | 报告目录、字段和图件要求以广西 v1.1 为准，签章和专业结论由责任人确认。 |
| P2：遥感稳定性 | Sentinel-2 小样和部分离线处理有记录；完整 AOI 在线 COG 读取失败过，根因尚未确定。 | GDAL COG 结构/Range 诊断；SCP 显式列出波段映射、比例因子和掩膜状态。 | 影像仅用于核查线索；未完整读取不得报告成功，也不得从影像自动给出验收等级。 |

## 首轮开发决定

1. 先在 ArcGIS Pro 3.1.5 完成真实安装、功能区加载、全部 10 个按钮映射与关键 GP 参数验证；随后用合成项目运行建库、采样质检和汇交检查。
2. 每次运行收据记录 Pro/Add-In 版本、安装包 SHA-256、DAML 按钮至 Python Toolbox 工具映射、测试结果、运行日志和截图路径；结果状态限定 `PASS`、`FAIL`、`NEEDS_REVIEW`、`UNVERIFIED`。人工复核未清零时不写整体 `PASS`。
3. 公开项目用于工程模式对照，不复制第三方代码；若后续拟复用代码，先锁定 commit 并检查该文件及其依赖许可证。尤其不将 GPL 项目代码并入当前 Esri Add-In，除非完成许可审查。
4. WMTS 只走本机回环代理转本地栅格（`mode=raster`）；`mode=live` 在 Pro 3.1.5 画布空白，官方文档未给出 Add Data from Path 的加载路径，不再投入。

## 来源与版本锚点

- Esri 3.1 示例、CAD to GIS Studio、sgsR 的历史提交/许可锚点见本文件先前核查条目及对应仓库 LICENSE；运行时引用 API 以本机 Pro 3.1.5 / SDK 3.1 为准。
- [USGS GeMS Tools 许可](https://github.com/doi-usgs/gems-tools-pro/blob/master/LICENSE.md)；[QGIS 许可](https://github.com/qgis/QGIS/blob/master/COPYING)；[QField 许可](https://docs.qfield.org/get-started/license/)；[Mergin Maps 组件许可](https://merginmaps.com/docs/misc/licensing/)；[QuickOSM 许可](https://github.com/QuickOSM/QuickOSM/blob/main/LICENSE)；[SCP 代码许可声明](https://github.com/semiautomaticgit/SemiAutomaticClassificationPlugin/blob/master/semiautomaticclassificationplugin.py)；[GDAL 许可说明](https://gdal.org/en/stable/license.html)。
- 用户提供的广西 v1.1 作业指导书原件是业务规则真源。公开采购公告、国外插件、旧规及工程复核报告仅作流程/软件模式参考，不能代替该真源。
