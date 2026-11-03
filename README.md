# 广西补充耕地验收工作台

## 外业表格与标签

附录 D/E/F 与样品标签采用用户提供的原表格式，模板集中在 `templates/`。每单元生成三张 Word 表格、一个混合样标签和三个容重平行样标签；标签保留采样地点、日期、人员、深度、土壤名称和编号栏，每页四张。现场值与签字留空，设计预填说明放在原表备注或编号栏内；D 表说明页保留，不额外生成提示页。地块超过四个、分样点超过七个时沿用原表行结构扩展。

项目名称或行政位置变化、模板或填表代码变化后，续跑会重做表格；缺少标签或 Word 文件损坏也会重做。自定义 `--template-dir` 须同时包含 D/E/F 三个模板和 `表单_样品标签.docx`。预填完成不代表实采、检测或鉴定完成。

## 界面改版（2026-11-02）

侧栏统一了视觉系统（色板、字阶、细分隔线、主次按钮、状态语义色、浅/深主题自适应），工作台外壳、采样面板、AI 侧栏、地块定位与高德密钥弹窗改为同一套扁平化组件；AI 工具调用改为执行行 + 可展开明细，写操作统一走风格化确认弹窗。离线渲染图与验证边界见 [插件 UI 改版说明](docs/2026-11-02-插件UI改版说明.md)；外部项目如何把 AI 接到图层/绘图操作、本项目差距与路线见 [AI 控制绘图调研](docs/2026-11-02-AI控制绘图与插件UI调研.md)。

## 底图四类与 AI 绘图（1.82）

AI 侧栏与 MCP 新增统一 `add_basemap`：天地图影像/矢量/地形晕渲（按当前地图范围生成 CGCS2000 本地快照，或尝试会话底图）、高德卫星/矢量（GCJ-02 逐像素纠偏后快照）、高德行政区划区图（县/乡界线+驻地，参考界线）。`calibrate_basemap_offset` 只读给出范围内 GCJ-02 偏移与变化量；`draw_features`/`list_drawings`/`clear_drawings` 提供点/线/面预览、确认写入与按批次撤销。高德与区图为参考底图，正式报件仍须项目 DOM 与三调/民政官方界线；Pro 桌面内实点未做，实测边界与未验证项见 [底图四类与 AI 绘图（1.82）](docs/2026-11-03-底图四类与AI绘图.md)。

## 图斑合并与分割（1.81）

在地图选择图斑，展开“编辑图斑”并填写操作人：“合并所选”处理相邻图斑；“画线分割”要求只选一块，从边界外穿过图斑并双击结束。候选用蓝色边界显示，核对后点“确认使用”；“取消候选”恢复原显示，“撤销上次编辑”回到未变化的上一来源。

每次生成新的工作副本，源数据和旧任务不改写。PE_ID是临时设计编号，PE_ORIG保留原地块关联；变更图斑的旧编号、冲突属性及旧面积字段清空，PE_M2提供当前椭球面积。旧正式单元全部重建，受影响的采样分组/合并图幅及旧单元点数、逐幅比例尺会失效，并在确认前列出。新的采样预览须重新生成，正式编号、属性与业务适用性仍待人工复核。

自动检查面积与覆盖守恒、重叠、部件、几何及源/候选内容。项目/因素冲突、不相邻、分割无效、源数据或候选在确认前变化时拒绝应用。地图坐标系须与图斑一致；只支持一块切成两块单部件图斑。撤销记录保留在当前工作台会话，操作时工程、地图、来源及内容须仍匹配；原版本和编辑回执保留在工程的“图斑编辑”目录。已确认但界面加载失败的版本可返回原上下文重试，重试先只读核验回执及源/候选内容。

Agent与脚本可调用`ParcelTools.pyt`的`ParcelEditPreview`及`ParcelEditResolve`，或`acceptance.parcel_edit.preview/resolve`，使用返回的manifest与token执行确认/取消/撤销；确认后输入自动识别PE_ID。回执中的几何PASS不代表业务验收通过。

## 采样与出图（1.80）

默认只选图斑并点“生成预览”。影像与字段采用自动识别，改动时再展开“设置”；本地影像、服务地址和密钥仅按所选来源显示。图件生成后才显示预览、“调整图件”和“确认并导出”，没有可用成果时隐藏打开与继续按钮。

新加图层或切换地图会自动更新资料列表；选中图斑显示为透明填充，点位、边界与网格排在影像上方。“全部图斑”不受地图临时点选影响；选择集或定义查询模式仍校验对应范围变化。上次任务只在同工程、同来源恢复。

1. 选择图斑并生成预览。按图框自动安排总览、位置图与逐地块采样图；在线天地图模式的位置图使用矢量底图与注记，本地模式沿用已选来源；采样图优先使用选定DOM，否则使用所选在线影像。
2. 在图件列表查看当前比例尺。输入分母并点“应用比例尺”，只重绘当前图；“自动”恢复规划值。会裁切地块的尺度直接拒绝，扩大图框时重新检查底图覆盖，必要时从已选来源补取，失败保留原报告。
3. “重绘当前图／重绘整套图”保留地块、单元、网格和设计点，输出到任务内的新目录。“停止”在图幅边界生效；运行中显示当前图幅进度。
4. “整套图件”打开浏览目录，可查看逐幅JPG/PNG/PDF和合并PDF。重新渲染会使视觉审核与设计确认失效，正式导出仍须再次核对并填写真实签审信息。

图层按问题、采样点、候选点、地块边界、网格、辅助资料、底图排序，导出前读回检查。默认不显示框内坐标网线，采样方格用细淡灰线。比例尺与DPI不构成原始GSD、位置精度或业务验收证明。

源码、构建与测试统一在本项目内：`ai/` 保存 AI/MCP 组件，`build.py` 是开发构建入口；原生安装包仍由 `addin/` 生成。运行 `py -3.12 build.py` 构建，`--harness` 构建 C# 核心测试程序，`--bundle` 生成完整源码包。实时 MCP 入口为 `ai/mcp_server.py`，命令行客户端为 `ai/pro_bridge_cli.py`。

**选择图斑，一键生成整套预览图。** 版本以 `docs/release-latest.json` 为准（`addin/dist/build-receipt.json` 为本地构建产物，不随仓库发布）（后者随构建自动更新，不手写）。 自动读取项目参数和已存密钥，先规划总览、空间分区及逐原始地块采点图，再取底图和排版；采样合组不减少逐地块图件。输出目录、比例尺与分幅自动安排，侧栏可切图、放大和浏览整套缩略图；技术设置折叠，预览前不强求单位或签审信息。正式导出仍需核对设计并补齐真实信息。[统一使用说明](docs/2026-10-28-unified-acceptance.md)。

**底图：** 已使用本机授权天地图实测在线取图；位置图接矢量与地名注记，采点图接影像。支持本地DOM离线出图。PNG调色板颜色与透明注记按RGBA处理；源分辨率、拍摄日期和位置精度仍待来源资料核实，GCJ-02/未知基准不能直接当CGCS2000成果。

**1.59功能记录：矢量文件夹批量接入；1.58：项目范围 POI 参考检索；1.57：人工采样分组。** 批量接入逐源记录要素数和结果，生成新 FileGDB，不改源数据、不猜坐标系；POI 使用需自行配置高德 Web 服务 Key，输出图层仅供位置参考，不进入采样、验收或报件判定。1.57 项目参数支持sampling_groups（临时SG_键、原始地块号、确认依据及可选项目绑定），保留原始边界、编号和正式单元字段。每组至少3点且每块至少1点，已知项目/因素冲突拒绝，缺资料、面积和集中性仍待专业复核；人工组不自动产生正式评价单元编号。分组变化使数据及下游阶段失效重建，既有加密要求仍取较大点数。地图图层输入按catalogPath查找项目参数。当前本批22+111、30+31分别3点，9地块共21点；成果与证据见out/courseware_atlas_all_parcels_20261025/人工合组成果_20261026。AI桥接0.6的基础体检与本插件完整地块质检范围不同，二者不能替代业务审核。

**1.49历史交互记录：** 在1.48地块编号防误搜基础上，把主导出按钮、运行状态及继续/取消/成果打开/摘要入口固定在侧栏底部，运行日志按需展开；用户滚动四步内容时仍能看到主操作。新增界面结构契约通过；1.49 ZIP 内 sidecar 已完成 stdio 初始化与 tools/list 握手，四个实时工具可见。当前仍收到 Win32 2，因本机 Pro 安装1.33，不能视为实时联机通过。Python 3.12.10全量451通过、8跳过；Pro Python 3.9.16下Add-In契约15项和定位4项通过（27项C#断言）；ArcGIS Pro 3.1.5 Roslyn构建及60项内容核验通过。安装包与完整 MCP 测试包不随源码仓库发布，见 Releases 或自行构建。ArcGIS Pro界面点击、侧栏视觉验收、另一台电脑移机和新手现场验收尚未完成；当前安装1.33不含管道桥接。详细门槛见[逐阶段路线图](docs/plans/2026-10-24-逐阶段实施与验收路线图.md)。

**历史能力记录：** 1.33 曾实测接通天地图，真实地块动态 GP 预览取得20张瓦片、约0.55米下载像元；该像元值不代表位置精度达标。旧 1.33 天地图测试包与1.32现场测试包及其测试材料已归档，可按[1.33实测记录](docs/采样助手1.33-在线影像实测.md)追溯。

本仓库当前主线是面向广西《补充耕地质量验收作业指导书》v1.1 的 ArcGIS Pro 功能区插件和 Python 核心。插件源码、安装包、规则依据、模拟工程和真实遥感探针分别见 [Add-In说明](addin/README.md)、[遥感流程说明](docs/remote-sensing-workflow.md)、[Sentinel真实数据差距报告](docs/real-data-gap-analysis.md)、[荔浦真实数据包实测](docs/lipu-real-data-gap-analysis.md) 与 `testdata/real_sentinel2_wuming_probe/`。

**MCP（离线 arcpy）**：`arcgis_mcp/server.py` 把 Pro Python/arcpy 包成 stdio MCP，可查数据集/工程、跑一键流程、读报告、导出布局、跑测试；不接实时 Pro 界面、不点按钮。仓库未附带含本机路径的 `.mcp.json`，按下方示例手动注册后重启会话：
`claude mcp add arcgis-pro -- "C:/Users/<user>/AppData/Local/Programs/Python/Python312/python.exe" "D:/work/arcgis_mcp/server.py"`

**新手全流程（2026-10-24 新增）**：一键流程现在同时生成「2 图 2 表 2 标签」——两张报件图、附录 D 踏勘表、附录 E 采样记录表、混合样/容重标签；运行结束写 `本次运行摘要.html` 与 `检查清单.json`，每条 FAIL/NEEDS_REVIEW 带下一步。外业回来后用 `scripts/evaluate_acceptance.py` 跑符合性评价、等级评价、三口径核算并生成初鉴报告底稿；`scripts/verify_rule_configs.py` 机械核验权重/区间/阈值。培训材料在 [docs/courseware/](docs/courseware/00-课程总览.md)，界面截图须实拍，未拍到的标“待补截图”。Pro 侧栏点击链仍需按 [验收清单](docs/验收清单-侧栏点击链.md) 人工实测，未实测前不得宣称“新手可用”。

**先确认样例边界：** 真实数据探针包含武鸣附近 Sentinel-2 L2A 的真实 B04/B08/SCL 影像裁剪，用于验证 ArcPy 处理链；叠加的评价单元是合成几何，不能作为真实项目范围或验收依据。仓库中的完整项目工程也属于模拟数据。当前未取得公开的广西真实项目全套 CAD、RTK 外业记录、实验室检测和签审成果，插件不能据此宣称正式项目验收完成。

**验收业务不要套用下方旧国土调查制图参数。** 后续 `standard_profile.json`、`lsplan.py`、`bootstrap_project.py`、`qa_delivery.py` 和 `ingest_cad_rtk.py` 是独立的旧版双尺度国土调查制图脚手架；其 200/400/600 m² 面积阈值、制图精度和符号要求不构成补充耕地质量验收规则。验收规则以用户提供的广西 v1.1 指导书及项目正式资料为准。

**本轮报告修订边界：** Word 版修订模板与五单元合成样例（未随源码仓库发布）是独立文档，没有替换已有完整模拟项目包的报告。完整模拟项目仍为 `REVIEW_REQUIRED`：旧包需进一步核对 GDB 混合样 `SAMPLE_ID`、逐点容重台账和照片类别数量，再同步报告、CSV、图件和附件；现有样品/图斑/影像均为模拟或示例内容。

## 验收插件使用

1. 安装包 `.esriAddInX` 不随源码仓库发布：从本仓库 Releases 下载，或按 [构建说明](addin/README-build.md) 自行构建；安装后重启 ArcGIS Pro 3.1.x，在“补充耕地验收”功能区使用。
2. 先运行“新建验收项目与数据库”，行政区、项目编号和 WKID 必须来自正式项目资料。
3. 检查矢量/CAD/RTK 源坐标、轴序、单位、控制点和转换方法；导入操作生成新数据，保留原始来源。
4. 混合样和容重工具只生成可编辑设计点。采样员须按评价单元同质性、图斑覆盖、均匀布点、15 m 间距及小图斑现场条件核对；设计点不能替代 RTK 实测点。
5. 遥感工具可用两期红光/NIR及可选 SCL 生成 NDVI 差值线索。产品时相、配准、云影、比例因子和投影转换仍须核实；不输出质量等级或验收结论。
6. 对数据包中的 TIF/IMG/ASC/JP2/VRT，先运行“遥感数据包预检”，查看波段、分辨率、范围、NoData、坐标系和伴随文件；自定义或未知坐标系必须按原始元数据/控制资料确认后才可继续。
7. 质检与汇交结果为结构初检/文件初筛。附录 C/G、数据库内容、图件版式、文字报告、附件适用性、照片类别及真实性、签字和 CMA 能力范围都要按指导书复核。

严格边界及已验证范围见 [插件说明](addin/README.md)、[验证记录](addin/verification/verification-record.md)、[真实数据审计差距与修复记录](docs/真实数据审计差距与修复记录_2026-10-20.md)、[报告样本研究说明](docs/报告样本研究与模板编制说明_2026-10-20.md)、初鉴报告编制模板 和 合成演示报告。该历史验证收据对应 **Add-In 1.20**、37项、SHA-256 `48EBA61DCFF4053978C8814E8FC25B87E9194EB8D02E0A05215A47A0C2ACCA53`。本轮系统Python 3.12全项目154项测试通过，其中采样质检及工具箱契约定向30项通过；ArcGIS Pro 3.1.5 Python 3.9.16本轮未能调用，因此未执行Pro环境全量回归或ArcPy工具箱现场运行。最新质检源码只统计唯一且编号正确、空间关联有效的点；小图斑审核JSON按评价单元、点位对和各自图斑精确关联，证据完整仍标记“待专业判断”，不会自动批准。该审核JSON已修正为可选输入，且包内工具箱源码已读回确认；Add-In包结构及核心源码哈希已校验，Pro Ribbon 实际交互未复测。荔浦案例包用于数据接入与遥感兼容性研究，不是补充耕地验收项目成果；在线 Sentinel COG tile 读取仍有已复现故障，真实项目 CAD/RTK、外业和正式报告成果仍需分别核验。初鉴模板按广西v1.1附录L和第10章修订；演示内容全部为合成资料，不包含真实检测等级或验收结论。DOCX结构及HTML预览已检查，Word原生分页尚未核验。

## 旧版双尺度国土调查制图脚手架（与验收插件分开）

下方文件仅用于其原有的国土调查/工程制图场景，不得作为补充耕地验收自动判定依据。

## 1. 目录

- `config/standard_profile.json`：唯一标准真源，含双尺度、CGCS2000 分带、最小上图面积、边界精度、图层/字段、分割参数、拓扑规则和制图规则。
- `config/grade_thresholds.json`：耕地质量**监测**指标分级阈值（耕地监测函〔2019〕30号），带文号、证据与逐项 verified 标记；未核阈值照算但结果标 UNVERIFIED。
- `config/grade_thresholds_reference.json`：官方取得但**不适用广西**的省级对照表（福建省），用于核对 26 项指标的名称/单位/方向与五级结构；`usable_for_guangxi: false`。
- `config/region_counties.json`：县 → 二级农业区 → 大区映射，抄自作业指导书附录 J，111 个县与地理数据双向零差异。
- `config/style_color_freeze.csv`：三调符号颜色冻结表。当前只有表头，必须从正式符号库填入 RGB 后才能通过颜色验收。
- `acceptance/grading.py`：实测值 → 1—5 级分级核心（正向/负向/区间三逻辑，纯 Python 可脱机测）。**产出是监测分级值，不是耕地质量等级**，不得代入验收评价，也不得把分级占比当"质量等级构成"。
- `scripts/grade_assign.py`：分级赋值命令行（写入 `FJ_<缩写>` 字段，附回读校验与统计报告）。`--check` 可只体检阈值配置，不需要 arcpy。
- `lsplan.py`：不依赖 arcpy 的纯 Python 规则与自检。
- `scripts/bootstrap_project.py`：创建 `.gdb`、要素数据集、12 个图层、公共字段、域、拓扑和 QC 表。
- `acceptance/ingest.py`：矢量/GeoJSON/Esri JSON、CAD、RTK CSV 接入；混合几何 GeoJSON 按几何类型拆分。
- `scripts/ingest_cad_rtk.py`：DMS 转十进制、RTK CSV 转点、CAD 转地理数据库。
- `scripts/qa_delivery.py`：图层、CRS、最小面积、拓扑、颜色合同的交付质检。
- `scripts/ai_extract.py`：ArcGIS Pro AI/分割工具的薄封装；默认只打印调用参数，加 `--run` 才执行。
- `tests/`：插件与工具包的纯 Python 回归测试（pytest，`python -m pytest tests -q`）。

## 2. 环境

- ArcGIS Pro 3.1.5 已确认存在 Image Analyst 工具：`ExtractFeaturesUsingAIModels_ia`、`DetectObjectsUsingDeepLearning_ia`、`ClassifyPixelsUsingDeepLearning_ia`、`SegmentMeanShift_ia`、`TrainRandomTreesClassifier_ia`。
- ArcGIS 建库/转换/质检使用 ArcGIS Pro Python：

```powershell
"D:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
```

- 纯规则、测试和 dry-run 可用系统 `python`。

## 3. 标准口径

- 法定成果比例尺：1:10000；工程细部比例尺：1:2000。
- 水平基准：CGCS2000；高程基准：1985 国家高程基准；投影：高斯-克吕格 3° 分带。广西项目按中心经度选择 EPSG:4523/4524/4525。
- 最小上图面积：建设用地和设施农用地 200 m²；农用地（不含设施农用地）400 m²；其他地类 600 m²；荒漠地区不得低于 1500 m²。
- 边界精度：明显界线图上 0.3 mm；不明显界线图上 1.0 mm；补测地物点平地/丘陵 0.5 mm、山地 1.0 mm，限差为中误差 2 倍。
- DOM 精度：1:2000 平地/丘陵 1.2 m、山地 1.6 m；1:10000 平地/丘陵 5.0 m、山地 7.5 m。
- 广西 205 号正文如给出更严格规定，以广西规定为准；本工具包的参数从 `standard_profile.json` 读取，不需要改代码。

## 4. 运行顺序

### 4.1 规则自检

```powershell
python lsplan.py
python scripts\bootstrap_project.py --selftest
python scripts\ingest_cad_rtk.py --selftest
python scripts\qa_delivery.py --selftest
python scripts\ai_extract.py --selftest
```

### 4.2 建库预演与建库

```powershell
python scripts\bootstrap_project.py --dry-run --out-gdb D:\work\land_survey.gdb
& "D:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\bootstrap_project.py --center-lon 109.8 --out-gdb D:\work\land_survey.gdb
```

脚本默认拒绝覆盖已有 `.gdb`；确认覆盖时才加 `--overwrite`。

1:2000 工程库为默认模式；建立 1:10000 法定库时加 `--scale legal`。

### 4.3 RTK DMS 转换

输入 CSV 使用列：`LON_D,LON_M,LON_S,LON_HEMI,LAT_D,LAT_M,LAT_S,LAT_HEMI`。

```powershell
python scripts\ingest_cad_rtk.py --mode dms-csv --src rtk_dms.csv --dst rtk_dd.csv
```

### 4.4 RTK 点入库

```powershell
& "D:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\ingest_cad_rtk.py --mode rtk --src rtk_dd.csv --out-gdb D:\work\land_survey.gdb --center-lon 109.8 --lon-field LONGITUDE_DD --lat-field LATITUDE_DD
```

脚本先以 WGS84 建 `RTK_Points_WGS84`，再投影为 CGCS2000 高斯-克吕格点层。椭球高到 1985 国家高程必须由项目批准的大地水准面模型转换，脚本不猜测高程改正数。

### 4.5 CAD 入库

```powershell
& "D:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\ingest_cad_rtk.py --mode cad --src D:\data\project.dwg --out-gdb D:\work\land_survey.gdb
```

CAD 地理配准、控制点残差和单位校核在 ArcGIS Pro 的“地理配准”工作流中完成；本脚本只做无歧义的 CAD 转 GDB。
RTK 模式在 `LAND_SURVEY` 要素数据集中生成：
- `RTK_Points_WGS84`：原始 WGS84 点；
- `RTK_Points_CGCS2000_GK`：转换后的 CGCS2000 高斯-克吕格点。

正式图层 `RTK_Points` 作为工程控制点/检查点台账，投影点经字段映射和人工复核后追加进入；脚本不直接把 RTK 点当作最终地类边界。

CAD 模式生成 `CAD_Converted` 要素数据集或图层组；配准后的多段线再按项目规则追加到 `CAD_Polyline`，原 CAD 文件保持只读。

### 4.6 交付质检

```powershell
& "D:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" scripts\qa_delivery.py --gdb D:\work\land_survey.gdb --epsg 4525 --report D:\work\qa_report.json
```

`FAIL` 必须修复；`UNVERIFIED` 表示颜色合同或拓扑等尚无可核实输入，不得在正式报件中当作通过。

## 5. ArcGIS Pro 手工步骤

以下步骤必须结合项目实际数据在 ArcGIS Pro 中执行：

1. 用 `镶嵌数据集` 整理影像并检查传感器、时相、GSD 和坐标系统。
2. 用 `Segment Mean Shift` 按 `standard_profile.json` 的场景参数试分割，人工复核后锁定参数。
3. 用 `Training Samples Manager` 采集面样本，训练 Random Trees/SVM/Maximum Likelihood，生成混淆矩阵。
4. 用 `ExtractFeaturesUsingAIModels`、`Detect Objects Using Deep Learning` 和 `Classify Pixels Using Deep Learning` 提取建筑、道路、林地、耕地、水体、电线杆。
5. 在编辑环境中执行修复几何、捕捉、平面化、合并、闭合、重建面，并验证拓扑。
6. 用地理配准选项卡导入 CAD 控制点，残差目标为 1:2000 ≤ 0.5 m、1:10000 ≤ 2.5 m；单点不得超过 2 倍 RMSE。
7. 加载三调专用 `.stylx`，把各地类 RGB 填入 `style_color_freeze.csv`，再设置斜线/网格、线型、点符号和 Maplex 标注避让。
8. 导出矢量 PDF，检查字体、图例、指北针、比例尺、坐标系、高程基准、编制单位和日期。

## 6. 验收

- 运行测试：`python -m pytest tests -q`。
- 运行 `qa_delivery.py`：图层齐全、CRS 一致、法定面层无低于最小上图面积的要素、拓扑无残留错误。
- `style_color_freeze.csv` 至少包含全部法定地类的官方 RGB；每通道容差 ±5。
- 标注重叠率不超过 15%，导出 PDF 字体嵌入，DPI 不低于 300。
- 未提供实际 GDB、CAD、RTK 和正式符号库前，颜色、地理配准残差、标注重叠和现场变化认定属于未验证项，不得写成已完成。

## 7. 许可证

- 本仓库根目录采用 MIT 许可，见 LICENSE。
- i/ 子目录另有 MIT 许可（i/LICENSE）与上游 MIT 许可（i/LICENSE-upstream.txt、i/NOTICE.md）。
- endor/docx/ 为 python-docx（MIT），见 endor/LICENSE-python-docx.txt。
- docs/、	emplates/ 中涉及广西补充耕地质量验收作业指导书与附录 D–F 原表的内容来自用户提供的公开资料；	estdata/real_sentinel2_wuming_probe/ 含 Copernicus Sentinel-2 开放数据（来源与日期见该目录 README）。再分发前请自行确认授权范围。
