# 外业导航、采样质量、影像精度检核与版式配置实施批次

日期：2026-10-31。范围：设计点导航导出、采样质量指标、RTK 检查点影像精度记录、出图版式可配置。不改设计点位置、不改规范判定、不改已有通过模块的默认行为。

## 目标态

- 外业包 `scripts/field_package.py` 除 GeoPackage、`设计点清单.csv` 外，新增 `设计点导航.kml`、`设计点导航.gpx`、`设计点定位.csv`（均为 WGS84 / EPSG:4326）；`package_manifest.json` 记录三类文件条数。
- 采样点清单 `采样点清单.csv`（`point_adjustment.write_point_csv`）新增 `经度_WGS84_导航`、`纬度_WGS84_导航`、`LON`、`LAT` 四列；`LON/LAT` 同时修复文档中 `photo_geotag.py` 依赖该列但清单从未产出的契约缺口。
- 网格采样报告每个评价单元新增 `采样质量`：设计点数、最近邻最小/最大/均值（米）、覆盖半径（米）、均匀性（0–1）、`需人工调点` 及原因列表。

## 口径

| 指标 | 定义 | 口径限制 |
|---|---|---|
| 最近邻距离 | 同单元每个设计点到最近其他设计点的欧氏距离（投影米） | 与既有 15 m 间距检查同口径，均为平面距离；与 `GEODESIC` 结果可能有微小系统差 |
| 覆盖半径 | 单元边界探针（按格长采样、含格角）到最近设计点的最大距离 | 只说明分布盲区，不代表采样代表性 |
| 均匀性 | 1 / (1 + 覆盖半径 ÷ 最近邻均值) | 无量纲相对值，只用于单元之间或新旧算法之间比较，无规范阈值 |
| 需人工调点 | 最近邻 < 最小间距、点数 < 2、设计点离单元边界 < 避边距离 | 是复核提示，不是规范 FAIL；原 `达标/间距达标/避边达标` 字段不变 |

## 文件

- 新增 `acceptance/field_navigation.py`：KML 2.2 / GPX 1.1 / CSV 纯函数写入与校验；`load_design_points` 用 ArcPy 读设计点并投影到 WGS84。
- 新增 `acceptance/sampling_metrics.py`：`boundary_probes`、`apply_area_weights`、`coverage_radius`、`nearest_distances`、`unit_quality`，纯标准库。
- 修改 `scripts/field_package.py`：`build()` 调 `write_all()`。
- 修改 `acceptance/grid_sampling.py`：新增 `_boundary_ring`、`_unit_quality`，单元报告加 `采样质量`。
- 修改 `acceptance/point_adjustment.py`：`write_point_csv` 增加 WGS84 列；补投影失败报错与临时文件清理。
- 新增测试：`tests/test_field_navigation.py`、`tests/test_sampling_metrics.py`（系统 Python 可跑）。

## 验证

系统 Python 3.12.10：`python -m pytest tests/ -q` → 555 passed / 14 skipped（新增 17 项全过）。金丝雀：已知点 (108.5°E, 22.5°N) 在 KML/GPX/CSV 中原样出现；越界坐标三种格式均报错且不留文件；单点单元 `需人工调点=True` 且最小距离为 None。

## 残留

- Pro Python 3.9 下 ArcPy 用例（`test_grid_sampling.py`、`test_point_adjustment.py`、`field_package.build()` 真实运行）本轮未跑：本机 PATH 无 ArcGIS Pro Python；跳过不算通过。
- KML/GPX 未在奥维/手机地图实机导入验证。
- 均匀性无规范阈值，只作比较线索，不得写进报件结论。


## 第二批：影像位置精度检核（第 3 项）

- 新增 `acceptance/accuracy_check.py`：检查点与影像同名点残差；配对方式 `id`（编号一一对应，缺配对报错）或 `nearest`（最近点，可设最大距离，超限报错）；RMSE / X、Y 向 RMSE / 最大残差 / 超限点号；无检查点记 `UNTESTABLE` 而不是 0。
- 生成一页 HTML 精度记录 + 同名 JSON；已存在拒绝覆盖；来源、GSD、配对方式、时相说明全部写入记录。
- 主工具箱新增 `13 影像位置精度检核（RTK检查点残差）`：图层模式（选编号字段）或 CSV 模式（成对两个文件）。
- 口径：残差＝检查点−影像同名点（米，平面）。`平面中误差_m` 现按平面 RMSE `√(ΣΔ²/n)` 记录；若业务方要按等权中误差 `√(ΣΔ²/(2n))`，须另行确认后改公式。同名点是否成立须人审，记录不自动证明满足报件门槛。

## 第二批：出图版式可配置（第 6 项）

- 新增 `acceptance/map_profile.py`：读取 `{"layout": {"pagx", "overview_title", "unit_title", "title_with_unit"}}`；未知字段、类型错误、自定义 .pagx 不存在都报错，不静默回退默认版式。
- `map_export._install_courseware_layout` 按 `plan.meta.layout_profile` 选版式；`map_worker` 的总览/单元图名走 `title_for`。
- `GridSampling.pyt` 的 `GridSamplingOneClick` 末尾新增可选参数 `layout_profile`（DEFile），输出参数索引 30→31；之前的参数编号不变。
- 落位坐标、字体、图号/编制单位/制图人/审核人/高程基准原本已经可配（参数或版式文件），本次未改。

## 第二批验证

系统 Python 3.12.10：`python -m pytest tests/ -q` → **574 passed / 14 skipped**（第一批 555 → 第二批 574，新增 19 项：精度 10、版式 5、接线 4）。
金丝雀：已知 0.3 m 残差算成 0.3 且判 FAIL，注入“状态恒 PASS”缺陷时渲染结果变 PASS（对照可见）；配置版式时导入的是配置的 .pagx，配置路径不存在时在删除旧版式前就报错。

## 第二批残留

- Pro Python 下未运行：`ImageryAccuracyCheck` 工具箱、`load_points` 读图层、自定义 .pagx 在 Pro 中真实导入、`title_for` 进入真实出图。
- 无真实 RTK 检查点与影像同名点资料，精度链只做了合成数据验证。
- 自定义版式必须含主图框，否则按现有错误停止；未做版式兼容性预检。
- 业务方是否接受该精度记录作为报备证明尚未确认。


## Pro 3.1.5 实机实测（2026-10-31，`D:/Program Files/ArcGIS/Pro`，Python 3.9.16 / ArcPy 3.1.5）

实测方式：Pro Python 下运行 pytest + 直接驱动真实 ArcPy 与 GP 工具箱 + 真实出图（后台实例 `arcpy.mp`）。

| 项目 | 结果 |
|---|---|
| Pro Python 全量 pytest | 618 passed / 2 skipped / 12 failed / 24 errors；**按文件单跑全部转绿**（test_parcel_tools 9 passed/2 skipped、test_unit_builder 8、test_point_adjustment 4、test_workflow_recovery 14 passed/1 仍是下方已知问题）。全量串跑红是 FileGDB 并发/共享 temp 环境问题，非本次代码 |
| 网格采样 15 项（含真实 ArcPy） | 全过 |
| 端到端脚本 | 网格采样 → 采样点清单（WGS84/LON/LAT）→ 外业包 KML 3 / GPX 3 / CSV 3 → 精度检核 RMSE 0.15 PASS |
| 一键流程（真实 Pro 出图） | prepare 76.5s / preview 80.4s / export 80.2s，4 幅 JPG/PNG/PDF 生成，技术状态 PASS；图名渲染为配置值 `补充耕地鉴定（配置图名）（地块P1）`（已截图核对） |
| 精度检核工具（`13 影像位置精度检核`） | CSV 模式 RMSE 0.2016 → FAIL、超限 RTK-02；图层模式 RMSE 0.0849 → PASS |
| 工具箱加载 | 主工具箱 19 个工具可注册，新工具在列 |

实测发现的缺陷（均已修复并补回归）：
1. `itertools.pairwise` 为 3.10+ API，Pro 3.9 下 ImportError → 改用 `zip(ring, ring[1:])`。
2. `arcpy.Array` 没有 `isClosed` 属性，边界环恒空 → 去掉该判断。
3. 单格单元无相邻格距参照，覆盖半径/均匀性整项退回“未计算” → 单点单元按包围盒短边取探针，均匀性按覆盖半径换算。
4. 逐图斑图名硬编码，配置被忽略 → 改走 `title_for`。
5. `GPString` 参数用 `_p(...)` 后设置筛选列表报 `FilterObject: 列表值与参数的数据类型不匹配` → 改为先取变量再设 `filter.list`。
6. 图层面误填 CSV 路径时误报“必须选编号字段” → CSV 路径按 CSV 读取并提示。
7. 设计预览图导出后丢失 `【设计预览】` 前缀：`map_presentation_options` 默认 `show_preview_label=False` 被 `display_title` 当作“隐藏预览标识” → 仅显式配置 False 且非预览图时才去前缀。

仍未通过/未做：
- `test_workflow_recovery::test_auto_atlas_preview...` 的 `inset_audit.covers_all` 断言：`map_presentation_options` 默认 `show_inset=False`，区位小图不生成，`inset_audit` 为 None。实测确认非本次改动引入（状态属并行开发），需产品口径决定默认值，未擅自改。
- `test_map_layout_style::test_project_presentation_controls_do_not_change_review_status` 与新口径冲突（该用例要求预览图按配置隐藏前缀，与第 7 条修复相反），待口径确认后择一。
- GP 导入会在 `toolboxes/` 生成通配 `*.pyt.xml` 桩；实测中一度误删 `GridSampling.pyt.xml` 与 `SupplementaryCultivatedLandAcceptance.pyt.xml`，已从 `addin/.../Install/toolboxes/` 与 1.33 归档包恢复，契约测试 26 项复跑通过。
- 插件安装包未重建（Config.daml 仍 1.68）；本轮验证走 Pro Python 直驱，不是安装后点击链。


## 口径确认（2026-10-31 用户）

- **区位小图默认开**：`map_presentation_options` 的 `show_inset` 默认 True；可用 `meta.presentation.show_inset=false` 关闭。
- **【设计预览】前缀的处理**：用户希望预览图不显示。实测该口径在当前架构下不可行，原因：预览与正式导出共用一份渲染缓存，`render_key` 与 `_reusable_export` 都基于图面标题计算；去掉预览前缀后两次渲染键相同，正式导出会复用预览缓存，导致正式图被标 `preview_only=True`（Pro 下 `test_auto_atlas_preview...` 第 184 行实测失败）。共用一份图面就无法区分“预览/正式”。当前保持：**预览图显示【设计预览】，正式导出自动去掉**。若要彻底不显示，需要改造导出缓存键（另立批次）。


## 收尾更正（2026-11-01）

- 上节“Pro 下按文件单跑全部转绿”**不准确**：串跑时 `test_workflow_recovery.py` 有 2 项失败是稳定的独立失败，不是并发环境问题：
  - `test_auto_atlas_preview...:185`、`test_completed_entry_repairs...:149`：正式导出复用缓存后 `reused`/`preview_only` 断言不成立。这两项在本次改动前即如此，未定位根因，未修复。
- 预览/正式图缓存隔离已修：`_reusable_export(row, key, target)` 增加 preview 身份比对（两条复用路径都传 target），加桩测试。
- 正确行为说明：图面元数据缺制图人/审核人/高程基准时，正式导出会被标 `preview_only` 并加【设计预览】（`_missing_metadata`），这是既有设计，不是缺陷。
- Pro 实跑确认：元数据齐全时正式图为 `补充耕地质量鉴定采样点位布置图（地块1）`、preview_only=False；预览图为 `【设计预览】…`。
- 系统 Python 3.12：`python -m pytest tests/ -q` → 582 passed / 16 skipped。


## 既有缺陷收口（2026-11-01）

用户确认口径：**制图人/审核人不作必需项**；区位小图默认开。

修复：
1. `REQUIRED_METADATA` 由四项（编制单位、制图人、审核人、高程基准）改为两项（编制单位、高程基准）。图上仍显示“制图：未提供”，但不再据此降级为预览图。
2. `_render_key` 排除 `preview_only` / `title` / `deliverable_status`：键只描述“画什么”，否则同一次执行里先加前缀再比对会算出不同键。
3. 导出结果新增 `ink` 字段（图面是否带【设计预览】），`_reusable_export` 改按 `ink` 比对复用，不再比对调用方传入的 `preview_only`。
4. 导出结果显式写出 `reused`（布尔），此前仅在复用时才有该键，重渲染时为空。

测试数据修正：`test_completed_entry_repairs…` 与 `test_auto_atlas_preview…` 的 meta 原只给 `org`，按既有规则正式图必然降级为预览，与用例断言矛盾；补入 `elevation_datum` 后两例在 Pro 3.1.5 下通过。

验证：系统 Python 3.12 `583 passed / 16 skipped`；Pro Python 3.9.16 两例 `2 passed`；Pro 全量结果见下。


### Pro 全量串跑失败的真实原因（2026-11-01）

上一节把 Pro 全量串跑的 `12 failed / 24 errors` 归为“共享 temp 环境问题”，**这个判断是错的**。实测错误是 `ImportError: cannot import name 'gp' from 'arcpy'`：`tests/test_map_export_contract.py` 的 `test_missing_map_metadata_is_reported_for_preview_only` 直接写 `sys.modules['arcpy'] = 假模块` 且未还原，污染了后续所有需要真实 arcpy 的用例。改为 `monkeypatch.setitem` 后：

- Pro Python 3.9.16 全量：**657 passed / 3 skipped / 0 failed**（1393 s）。
- 系统 Python 3.12 全量：**583 passed / 16 skipped**。

至此本轮改动在 Pro 下全绿。


## 安装包 1.69 与 Pro 实机验收（2026-11-01）

构建：`build_roslyn.py`（Roslyn，DLL 428544 B）→ `package.py`：
- `addin/dist/SupplementaryCultivatedLandAcceptance_1.69_20261101_055305_635067.esriAddInX`
- 208 条目 / 963833 B / SHA-256 `73B458A81365817CE31BE8F29545FC542A056066D2AA26750E3395463CDF0620`
- 新增入包：`acceptance/sampling_metrics.py`、`field_navigation.py`、`accuracy_check.py`、`map_profile.py`；`scripts/field_package.py`、`photo_geotag.py`、`field_return_qa.py`

安装：`RegisterAddIn.exe "<包>" /s` → 用户级目录写入成功，哈希一致；旧 1.68 被替换。

Pro 3.1.5 实机（打开真实工程 `temp/pro_live_20261031/full/任务_v2/render/成果图.aprx`）：
| 项 | 证据 |
|---|---|
| 插件加载 | 功能区出现“补充耕地验收”“采样与出图”“补充耕地验收工作台”三个选项卡；右侧工作台面板正常显示当前工程与地图 |
| 功能区按钮 | “补充耕地验收”标签下呈现：打开工作台、一键出图、快速定位地块、新建项目与数据库、矢量/GeoJSON接入、矢量批量接入、CAD接入、RTK CSV接入、数据库质检、汇交检查、更多工具 |
| 按钮执行 | 点“一键出图” → 右侧切换到采样向导（选图斑/查图出图/确认交付）；点“刷新地图资料” → 经 `ToolboxLocator` 执行 `DiscoverProjectData`，状态更新为“已使用保存的天地图密钥，图幅和比例尺已自动安排”，未出现“插件自带 Python 工具箱未找到” |
| 新工具 | `13 影像位置精度检核` 为 GP 工具箱工具、无功能区按钮；其 UI 对话框未在本轮点开，但同一目录下的 `GridSampling.pyt` 已由工作台成功调用，且该工具已通过 Pro Python 实跑（CSV 模式 RMSE 0.2016→FAIL；图层模式 0.0849→PASS） |

限制：合成鼠标点击在功能区上不稳定（选项卡会回退），最终以 UIA Invoke 完成；这属于自动化限制，不代表界面故障。


### 1.70：为精度检核增加功能区入口（2026-11-01）

问题：`13 影像位置精度检核` 只是 GP 工具箱工具，功能区与命令搜索都找不到它（Pro 命令搜索不索引 add-in 工具箱）。

改动：
- `Commands/WizardButtons.cs` 新增 `ImageryAccuracyButton` → `ToolboxLocator.Open("ImageryAccuracyCheck", "SupplementaryCultivatedLandAcceptance.pyt")`
- `Config.daml`：在“更多工具”菜单加入 `AcceptanceAddin_ImageryAccuracyButton`，caption「影像精度检核」，keytip Y；版本 1.69 → 1.70

构建与安装：
- `SupplementaryCultivatedLandAcceptance_1.70_20261101_080410_934514.esriAddInX`，208 条目 / 963911 B / SHA-256 `1825AA49…966E29DB`
- 控件 22 个、keytip 22 个（无重复）；RegisterAddIn 安装成功，旧 1.69 被替换

Pro 3.1.5 实机验证：
- “补充耕地验收”功能区 → 更多工具菜单中出现「影像精度检核 (Y)」
- 点击后右侧打开「地理处理」面板：`13 影像位置精度检核（RTK检查点残差）`，参数齐全（检查点图层/影像同名点图层/两组 CSV/编号字段/X、Y、编号字段/配对方式=按编号一一对应/nearest 最大距离/影像来源/原始 GSD/HTML 输出/JSON 输出/运行按钮）
- 截图：`temp/pro_live_20261031/evidence/pro_acc3.png`

未完成：在 UI 里填参数并点“运行”。Pro 的 GP 面板文件路径输入框不暴露 UIA ValuePattern，坐标点击又会错行（截图 `pro_filled.png` 可见只有一格被错误填入），本轮未取得“UI 内执行成功”的证据；该工具的计算逻辑已由 Pro Python 实跑（RMSE 0.2016→FAIL、0.0849→PASS）覆盖。


## 示例区真实数据测试（2026-11-01）

数据：`D:/work/高标准/示例区2025年度第一批耕地恢复.shp`（9 图斑，含真实排除/分组规则 `项目参数.json`）。

发现并修复的真缺陷：
- `_boundary_ring` 在真实多边形上崩溃：带洞/多环面的 `arcpy.Array` 含 `None` 占位点，`ring.getObject(k).X` 抛 `AttributeError: 'NoneType' object has no attribute 'X'`。修复为跳过空点，并在 `tests/test_grid_sampling.py` 加反例 `test_boundary_ring_skips_null_placeholder_points`（合成 3 点 + 1 个 None，断言环只含有效点）。此前 15 项网格采样测试全绿也没发现，因为测试数据都是无洞正方形。

其它实测结论：
- 天地图取图需要 key，`oneclick.run` 默认 `tianditu_key=None`；直接调用时必须显式传入（`acceptance.tianditu_snapshot._tianditu_key()` 可从桥接配置读取）。Add-In 侧由凭证库代填，不是缺陷。
- 精度检核在示例区 27 个真实设计点上跑通链路（检查点与影像点同坐标，RMSE=0，仅证明链路；不是精度实测）。


### 1.71：示例区真实数据修复后重打（2026-11-01）

- 1.70 是在 `_boundary_ring` 修复前构建的，包内含崩溃版代码；重新构建 1.71。
- `SupplementaryCultivatedLandAcceptance_1.71_20261101_090312_433037.esriAddInX`
  208 条目 / 964000 B / SHA-256 `607ADF01…BEE1AA69`
- 校验：包内 `Install/acceptance/grid_sampling.py` 与源码 SHA-256 一致；`boundary fix present: True`
- 安装：RegisterAddIn 成功，用户级目录哈希与回执一致；1.70 被替换。

示例区真实数据结果（`temp/pro_live_20261031/ty_task`）：
- 一键准备 766 s，技术状态 PASS，4 单元 / 6 图斑 / 6 设计点（已按项目规则排除 124、171、198 并合并 22+111）
- 采样质量：P_1 最近邻 63.2 m / 覆盖 128.8 m / 均匀性 0.351；P_39 28.3 / 40.2 / 0.413；SG_22_111 63.2 / 80.0 / 0.494；SG_30_31 134.2 / 193.0 / 0.531；均未触发“需人工调点”
- 区位小图：`INCLUDED`（默认开启生效）
- 影像：NEEDS_REVIEW（在线瓦片像元间距不作精度证明）

回归：系统 Python 583 passed / 16 skipped；Pro Python 全量 658 passed / 3 skipped / 0 failed（1097 s）。


## 三方专家评审（2026-11-01）

三份意见落盘：`temp/experts/survey.md`（测绘/影像精度）、`acceptance.md`（验收业务）、`delivery.md`（交付工程）。

### 已按其意见落实并验证的修正

1. **精度统计口径**（测绘 P3）：`statistics()` 增加 `min_check_points`（默认 20，标注为建议值非规范）；样本不足判 `INSUFFICIENT`，不再让 n=1/2 的 0 残差判 PASS。`nearest_correspondence` 禁止两个检查点共用同一影像点。测试先红后绿。
2. **质地构型选点**（业务 §三.2，细则 8.1.3.5 原文已核对）：“在每个评价单元内选择面积最大的图斑，在 7.2 布设的点位中选择最靠近该图斑中心位置的点位”。`prepare_bulk_candidates` 改为按此规则，缺图斑层时保留旧口径并在 REVIEW 中写明未落实。新增 `tests/test_bulk_design.py`，金丝雀验证过：关掉修复会选错点。
3. **安装态自检**（交付 P0）：新增 `addin/verify_installed.py`，比对 Pro `AssemblyCache` 与已安装包。
   金丝雀对照实测：安装 1.71 后未重启 Pro → 缓存 DLL 为 1.70 版，脚本报 `STALE`、退出码 1；
   启动一次 Pro 后重跑 → 全部 `MATCH`、退出码 0。结论：Pro 只在启动时刷新缓存，安装后必须重启并跑本检查。

### 未采纳/待定

- 业务与测绘均反对“制图人/审核人可选”，但这是用户 2026-11-01 明确决定，保留；风险已记录在此。
- 50 亩拆分优先按 7.1.1 四因素而非条带：需要改 unit_builder，尚未实施。
- 精确投入比例需用户裁定，见对话结论。
