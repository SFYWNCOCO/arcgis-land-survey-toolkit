# ArcGIS Pro 补充耕地验收 Add-In 验证记录

## 2026-11-01 去冗余与当前成果校验候选 1.78

- 修复 AI 交付检查读取根目录旧报告的问题：与工作台共用当前状态与报告校验，按 `task_state.json.report_path` 读取本轮报告及同目录检查清单；运行 ID、输入指纹、设计修订或目录边界不符时返回无法核对，不回退历史报告。
- 删除未调用的四个 Python 辅助函数、无引用常量、C# 字段/局部变量及采样界面私有方法中的不可达动作分支。保留实际使用的地图移动函数和 Python 命令入口；相同布局文字函数移入既有样式模块，流程参数直接绑定实际执行函数的签名，未新增生产代码文件或历史兼容分支。
- 系统 Python 3.12 回归 `811 passed, 26 skipped`；最终 C# 收据/交付定向 `12 passed`。Pro Python 3.9 契约回归 `79 passed`，最后一处无效赋值删除后的出图/样式回归 `53 passed, 1 skipped`。跳过项不计为通过；相关报告身份与历史回退用例已先复现失败。
- Roslyn 原生编译通过，1.78 候选安装包 208 项读回校验通过；安装包及源码包的路径和 SHA-256 分别见 `dist/build-receipt.json`、`dist/bundle-receipt.json`。本轮差量、基线、日志与独立审核内容绑定保存在工作区 `temp/arcgis-dedup-20261101/` 及当前可回查会话。
- 未安装候选包，未验证 Pro 界面点击或在线服务，也未提交 Git 或发布。离线回归与原生编译不代表这些实机环节已通过。

## 2026-11-01 源码统一候选 1.77

- 132 个组件文件由 `ARCGIS/pro_ai_bridge/` 移入 `land_survey_toolkit/ai/`，编辑前逐文件 SHA-256 一致；构建脚本进一步移用为项目根 `build.py`，无旧路径回退代码。更新构建、源码包、测试、文档与桌面 MCP 启动路径；历史收据内容保留。
- 系统 Python 3.12 主回归 `799 passed, 26 skipped`；最终边界及目录契约定向 `17 passed`。Pro Python 3.9 最终目录/插件/边界契约 `41 passed`；ArcPy 3.1.5 使用全新临时 GDB、工程和 PNG 通过 2 项实测，包含完整演示出图入口与有效坐标系检查。
- 修复缺失采样单元漏检、影像 CRS 未计入验收、小样本索引类型、无效步长、缺瓦片继续出图、错误 ArcPy API 与吞掉导出异常。原 `factoryCode` 写法在真实输出中得到 32648，撤回此前“成果缺少 CRS”的判断；改用显式构造以消除歧义。
- Roslyn 原生编译通过，包内 208 项读回验证通过，唯一 DockPane 为 AcceptanceWorkspacePane；1.77 候选安装包及完整源码包路径/哈希以 `dist/build-receipt.json`、`dist/bundle-receipt.json` 为准。
- 当前无运行中的 Pro 实例；没有安装候选包、停止现有进程或声明 UI/在线服务联调通过。MCP stdio 握手在新路径通过，桌面配置将在下次连接时使用新路径。
- 独立复审结果与最终内容指纹在当前可回查会话及工作区 `temp/arcgis-consolidation-20261101/` 绑定；未提交 Git 或发布。

## 2026-11-01 工作台稳定性候选1.76

- 新增内部任务协调和只读成果校验，复用 Python 单写 `task_state.json`；每轮独立运行ID，500ms观察阶段，启动早期停止、安全边界停止、迟到终态保护和按指纹恢复。采样、AI/MCP、在线预览、资料扫描、人工审图及点调整入口接入占用控制；原生工具对话框在忙碌时拒绝新开，用户绕过插件手动运行的 Pro 工具不属于全局拦截范围。
- 新增实际GP环境检查和脱敏诊断；Win32 2按桥接/运行程序/成果场景分类，HTTP鉴权与配额及忙碌返回结构化代码。技术处理、视觉复核、业务审核分别记录；视觉失败不使有效GIS导出缓存失效，正式导出仍保留原交付检查和人工确认。
- 系统 Python 3.12.10 最终邻域回归 `779 passed, 25 skipped`；新增C#核心状态、坏收据、密钥脱敏及服务错误定向 `30 passed`。Pro Python 3.9.16 下恢复及状态集 `37 passed`，包含真实GDB、取消恢复、预览、设计确认导出、损坏图件和SHP重建。实际生成的图件报告经最终C#读取为技术完成、业务待审且可打开。
- 候选包208项、983707字节，SHA-256 `CBFC4A99AB60F09340479264522E68842C88CD548CDD336ABB2D982C8AF53828`；Roslyn / Pro3.1程序集编译成功。标准注册器安装并读回同哈希，重新启动Pro后加载DLL与构建DLL同为 `9FF2AB67E9D3D839E23A054988E59C93CFF26BCF53C12BD55C203104F9831F65`。最终包任务冒烟1项通过，实际返回 `JOB_BUSY`、只读查询可用、早期停止落为 `CANCELLED`。此前同轮候选另完成7项真实宿主集成与100次页签切换，7字草稿保留。
- 安装缓存中的 `CheckWorkbenchEnvironment` 经标准动态GP调用成功，读回Python3.9.16、Pro3.1.5、ArcPy可用及输出父目录可写；仅创建并移除权限检查临时目录，未生成业务成果。指定自定义工具箱别名的一次导入失败，使用工具箱返回的标准动态入口后通过。
- 桌面截图捕获报 `FrameArrived timed out`，坐标点击报 `coordinate input geometry is unavailable`。UIA已读到固定底栏及按钮状态，原生API与可编辑草稿读写可用；完整鼠标点击链、停止反馈300ms、100次实际关闭/重开和视觉布局仍未通过实机验收，不能据API切换100次宣称这些项通过。
- 本轮使用新建合成工程与新输出目录；实际项目数据、现场采样与正式影像精度未作为本轮测试结论。独立审核及最终内容指纹绑定在当前可回查会话中；未提交Git或对外发布。
- 复核证据暂保留在工作区 `temp/arcgis-stability-20261101/`：源码基线与最终差量、测试日志、合成图件与宿主收据；不包含真实密钥。原型准备脚本和本轮被替代的候选包在收尾清理。

## 2026-10-29 统一工作台1.68 AI连续执行轮数

- 用户反馈24轮限制不足；将 `ModelClient.MaxTurns` 从24提高到100逻辑轮。每轮最多16次工具调用、HTTP最多3次尝试，已有超时、取消、SSE释放、历史压缩、上下文长度保护、工具回执和“继续”续跑逻辑保持有效。提前完成/取消/超时仍会结束，不强制模型空跑100轮。
- 先将回归目标改为100轮，用旧CoreHarness复现失败（实际24轮）；修改后变绿。独立复核另跑3项：HTTP真实请求精确停在100轮、两项连续任务共200轮且第二项从请求索引100开始、20k工具结果经过100轮后仍受上下文长度保护，3项通过。
- 修复独立审查发现的版本漂移：运行时 `get_status`、Add-In DAML、实机测试版本预期及当前README/测试说明全部为1.68。独立审查通过，审查者 `/root/audit_workflow`；包 SHA-256 `D824B2999A04C78EFAD9E681E1798ADC696FC70D9987B32AEFB4DA434054FAC6`。
- AI/MCP：152 passed、7 skipped；Add-In契约：24 passed；Roslyn构建通过。发行包201项、939916字节，ZIP完整性通过，源码成员差异0，包内DLL与构建DLL一致。标准安装器已安装，安装副本SHA-256与包一致；需要重启ArcGIS Pro加载。
- 最终审核指纹：`ModelClient.cs` DC4EF2EA3B61954C77EA0909ECF15DC9560F50DCD2C5F4CB6BE39812BBE53CED；`MapActions.cs` BB5B3ABDB885795B42AD654C1CFBCC7ECC8890868526958EB386FA797FCDFC05；`test_core.py` BBDA5845FAC3188635DB99D998D41D1BECA530CC93BDFE1E30917F38B8E874C9；`test_live.py` 8C1609BE76EAC766171153935D88854CA039BC93863DF8B029EB6ABED3A4BE87；`Config.daml` EB1E0BB976966743FA06760B23EC633B8C1D3D5C93CC5BA30BF334F65439F397。其余文档与完整指纹见 `docs/unified-acceptance-release-status.json#latest_update.independent_review.reviewed_files_sha256`。
- 未验证项：未在第三方在线模型服务上执行100轮长任务；安装后未重启ArcGIS Pro验证加载及画面。100轮测试结果不等同于线上模型或Pro UI实测。


## 2026-10-29 统一工作台1.66 P0 验收 AI 工具

- 新增 `RunAcceptanceDefault` GP 薄包装（`GridSampling.pyt`）+ AI 工具 `acceptance_check`（只读预检）、`acceptance_preview`（按当前选择/定义查询生成预览，写操作需 confirm）、`acceptance_delivery`（交付自检）；tools.json 46 项（44 项插件内执行）。内部复用 `oneclick.run(task_action=check/preview)`、`GpJob`、`task_state`、`triage`，未复制 31 参数。
- 修复：GPFeatureLayer 源路径 `file:///D:/...` 不识别 → 转 Windows 路径；`acceptance_check` schema 补 `use_selection`；预览默认读取已配置天地图 Key 并走 tianditu 影像；`activate_map` 的 `MapView` 访问改为 null-safe。
- Pro 桥接实测：`acceptance_check` 2.1 秒成功；`acceptance_preview` 通过 job_id 运行 3 分 32 秒成功，未被 80s 桥接截断；`acceptance_delivery` 返回 `NEEDS_REVIEW`、0 FAIL/7 NEEDS_REVIEW/2 图，阻塞项含四因素、系统项目编号、报备影像位置精度、外业表格与人工复核。
- 单地块 headless 预览：2 幅图（1 位置图 + 1 采点图），`一键成果报告.json`、`检查清单.json`、`本次运行摘要.html` 齐全；技术状态 PASS，出图 NEEDS_REVIEW。
- P1：工具按请求裁剪（core/sampling/analysis/full，规则匹配，无 LLM 判定）；核心档 23 项/7,055 字符，较完整档 46 项/17,167 字符降约 59%；采样档 29 项/10,168 字符。模型请求改用 `stream=true` 并解析 SSE，非 SSE 自动回退 JSON；天地图快照按 `wkid+extent(0.1m)+Key哈希` 缓存复用。工具裁剪、SSE、缓存键均有契约测试；Pro 实机缓存命中未单独复测。补充：429/5xx 两次退避重试、失败/取消时保留本轮历史、复用静态 HttpClient、get_status 移出 Gate、list_jobs 的 messages 截断到 512 字符、轮询空闲 5s/运行中 1.5s。
- P2：天地图快照默认叠加影像注记（`tianditu_image_labels`，`--labels` 可关；缓存键含 labels）；真实地块 1 快照可见“定良村”“S315”。新增 `acceptance_snapshots` 批量快照：逐地块 z18、注记、Lanczos，输出 TIFF 与 `批量快照清单.json`；2 地块实测 PASS（0.547 m）。正式导出前在采样页校验编制单位、制图人、设计确认人和设计核对勾选，缺项只提示不导出。
- 清晰度实测：同一位置 z18 返回有效 JPEG（10382 B），z19/z20 返回同一张 4769 B 占位图“该区域无影像”，因此天地图公开影像真实上限为 z18（约 0.55 m）；更清晰只能靠逐地块 z18、Lanczos/注记，或改用项目 DOM/无人机影像。
- 发行包 1.66 共 201 条目、936849 字节，SHA256 `51EEB31037ED04D43D7754EB77A70C090514A901EEC4DC2E7DA8DE798A4CBEDF`；已安装并核对哈希。回归：land 533 passed/16 skipped；AI 144 passed/7 skipped。

## 2026-10-28 统一工作台1.66 AI连续执行、天地图入图与出图注记

- AI核心：模型轮数8→24，达到上限保留历史并提示“回复继续”；单轮工具调用8→16，超出只执行前16个；历史按80k字符裁剪而非整体清空；工具结果上限16k→8k。CoreHarness新增24轮、续跑、批量超限、历史裁剪与天地图路由/模板测试，AI全套`138 passed, 7 skipped`。
- 天地图入图：新增`add_tianditu_basemap`/`remove_tianditu_basemap`，复用本机回环代理把天地图WMTS转成XYZ模板`{z}/{y}/{x}.jpg`，Key不进入图层URL；`save_project`检测到“（会话底图）”会拒绝保存。tools.json为43项（41项插件内执行）。
- 代理实测：天地图实际返回PNG但Content-Type标为`image/jpg`，旧代理按Content-Type校验JPEG头误判502；改为按文件头识别PNG/JPEG后，真实坐标瓦片返回200（PNG/JPEG）。并发8→2、单请求超时20→30s；直连实测也返回200，`TileClient`改为`UseProxy=false`（只影响天地图瓦片，不改全局代理）。
- Pro实机控制：`mode=live` 可创建“天地图影像（会话底图）”及两个TiledServiceLayer、Pro持续取瓦片，但画布仍空白（WGS84测试地图）；直连后仍空白，说明不是系统代理问题，更像Pro的Web Tiled Layer渲染/投影问题。
- `mode=raster`（默认回退）：按当前地图范围调用`acceptance/imagery.py`下载并镶嵌天地图瓦片，输出本地TIFF后作为RasterLayer加载；Pro实测成功显示影像（截图`temp/pro_tool_raster_fresh.png`）。真实地块1（DIKUAI_NO=1）z18快照叠加边界与3个设计点见`temp/pro_parcel1_composed.png`；快照保存在`%LOCALAPPDATA%/ProAiBridge/snapshots`，最多保留5份；`remove_tianditu_basemap`同时移除会话底图与本地快照。
- 出图：`map_export.py`原先无条件隐藏“影像来源”且未消费`imagery_text`；现改为页脚两行（来源/时相/精度 + 输出像元，6pt），显示“制图/审核”，新增元数据门禁（缺编制单位/制图人/审核人/高程基准→preview_only）。按今天`统一出图验收_20261028/render/render_plan.json`重出11张到`out/统一出图验收_20261028/重出_20261028_210301/`，比例尺全部PASS；8张报件图文字项无FAIL、占位符PASS、采样图影像来源PASS；3张总览为补充索引，被检查脚本按图名判为unknown，不属两张报件图。
- 验收工具全套`533 passed, 16 skipped`；AI全套`139 passed, 7 skipped`。发行包1.66共200条目、923294字节，SHA256`E3D83E0D88DEEE06B00C46ECEE6BEF04AEE24171EE0C7CA29FB670FF7138ACD7`；已用Pro注册器安装并对注册副本哈希复核一致。`mode=raster` 已在Pro实测出图；`mode=live` 仍空白，作为实验模式保留。
- 基线说明：26号`out/courseware_atlas_all_parcels_20261025/人工合组成果_20261026/map/`为14张旧试样；25号`数据对齐成果/`更旧。本次误用的25号重出目录已删除，旧成果未覆盖。

## 2026-10-28 天地图预览修复与统一工作台1.63

- 修复浏览器直接跨域请求天地图WMTS导致瓦片图像加载失败：改为插件本机回环代理获取瓦片，浏览器只请求本地同源地址；Key不再写入预览HTML。增加来源/图层/缩放级别/行列号/同源校验、并发上限、响应大小和图片格式检查、短期缓存。
- 用当前用户加密配置中的Key按原故障预览中心进行代理实取，响应为有效JPEG；1920×935视口Zoom16对`img`和`cia`抽测72张，全部返回HTTP200及有效JPEG/PNG。页面HTML未包含Key或天地图域名。另做代理路由非法来源、范围和参数测试。
- AI/MCP全套`110 passed, 7 skipped`；包契约与交付包定向`13 passed`。Roslyn原生构建成功，Release DLL为348160字节。ArcGIS Pro当前用户已关闭；标准安装器注册1.63成功，发行包和注册副本SHA-256均为`8CE20A236C17FB767D5434BADD9F22192743BDE7922AD18268593584762FED14`，安装器回报需重启生效。
- 发行包199项、889836字节。Pro本轮未重新启动，故新DLL在实际Ribbon加载后的视觉瓦片显示仍须启动Pro复核；72张视口抽测不代表所有地区和缩放级别均已覆盖，也不提供影像定位精度证明。

## 2026-10-28 统一工作台1.62 对话推进优化

- DeepSeek/兼容Chat Completions系统提示改为“推进优先”：低风险细节取合理默认继续，避免反复列多个方案要求用户选；先以图层URI/数据源消歧，真正影响GIS结果的缺项只追问一个问题。覆盖、删除、发布和现有执行确认保护不变。
- 新增请求契约回归；先在旧提示下失败，更新后通过。AI/MCP全套`109 passed, 7 skipped`（47.25秒）；发行包构建后又跑包契约`2 passed`。本地HTTP测试确认策略进入`system`消息，未使用真实DeepSeek凭证或消耗线上额度。
- 统一包1.62共199条目、886096字节，SHA256 `7574F6D94B2D2814E2505F6EEF59165D0ED29285C8B9DCA4D1552BF78C80F7B4`，安装注册副本哈希一致，`AcceptanceAddin.dll` 339968字节。Pro当时未运行，安装回报`RestartRequired=true`；须启动/重启Pro后实际模型才能载入新提示。
- 用户提供的天地图Key已通过当前用户DPAPI加密配置读回匹配；同一Zoom10瓦片坐标（广西附近）分别请求`vec`、`cva`、`img`、`cia` WMTS，各返回HTTP200及有效PNG/JPEG图片。此抽测证明该Key可取该处四类瓦片，不证明全区覆盖、所有级别或任何位置精度。

## 2026-10-28 统一工作台1.61

- 单一GUID/AcceptanceAddin.dll/标准DockPane，AI助手与采样视图只创建一次并在页签间切换；47项通用MCP及原实时地块通道在同一进程工作。原ProAiBridge独立DAML、Module、程序集声明退出当前构建，源码通过链接编译，PS构建/打包入口转交唯一Python实现。
- 独立审查修复共享Key被旧表单覆盖、未保存Key草稿被刷新覆盖、选择集/定义查询旧快照风险；实际执行前同时检查源工程/地图。共享凭证与范围快照正反测试通过。增加采样字段的可访问名称。
- 108项AI组件、478项验收工具通过；最终包7项真实Pro回归通过（45.48秒），包括两通道同进程同地图、实际选中页匹配导航、原地图/出图/投影/空间连接/基准转换与体检定位。默认另跳过7项现场、14项需ArcPy环境项，不把跳过计为通过。
- 最终1.61包199条目，SHA256=69ae59e8f2b19069fa6fce0941c5e47073cb789f723bdbd13fc23187c02d8e4d；实际加载AcceptanceAddin.dll哈希da40ad222432925f311ff3ff58bde9a5f349f7dd1bf60596d60c5d6a5978e03c与构建相同，未加载ProAiBridge.dll。旧独立0.7注册文件已移至_archive/unified-acceptance-20261028，原配置未删除。
- Pro Python3.9.16从最终包动态调用GridSamplingOneClick/check，读取合成投影面要素1条，坐标/编号/范围/目录与D/E/F模板检查PASS，GP状态4/最高严重度0，按check契约不创建成果目录。检查模式返回消息而非成果JSON，探针按实际契约验收。
- 合成测试工程temp/unified-acceptance-v161-live-03/BridgeSmoke.aprx（PID35396）已核对身份、无活动任务并保存dirty=false后关闭；仅关闭本任务自建进程。UIA可读两个页面及字段；截图FrameArrived超时且可访问性回读存在缓存不一致，物理键鼠、IME/滚动仍未完整验收。无API凭证，在线底图和商业模型未新增实测。原9地块21点业务成果未修改，未提交Git。

## 2026-10-27 Add-In 1.60 底图服务扩展

- 复用1.59 C# DLL，保留POI与批量矢量功能；更新imagery.py、GridSampling.pyt及两份授权XYZ/WMTS模板。新增天地图矢量与两种注记；自定义源强制声明EPSG3857、256瓦片、全球西北原点和标准零级分辨率，GCJ/BD/未知基准及不符矩阵均拒绝。
- 全套478通过、14跳过（15.01秒）；同时修正1.58/1.59新增Ribbon入口未纳入旧契约测试的问题。包64条目，包内Python、Toolbox、模板与源文件一致，安装副本哈希3a86d3f8a4184e02cae5e87d32680f1825ee0e6d04194924f18102cc46e6c838一致。
- 在Pro Python3.9.16实际读取最终包模板并ImportToolbox成功；无服务凭证，未运行实际取图。AI侧栏0.7另外提供高德/天地图浏览器预览，不能替代正式DOM与精度证明。未操作旧21点业务成果，未验证Ribbon物理点击。

## 2026-10-25 Add-In 1.49 实时地图实机验证

- 清理前当前用户实际注册的是 Add-In 1.33；已将安装包移入工作区 `_archive/20261024_superseded_builds/`，当前注册目录仅有 1.49：`SupplementaryCultivatedLandAcceptance_1.49_20261025_000201.esriAddInX`，SHA-256 `DD974434674153BDA39CF52D2112FBCC0D18BF5385C523142B8C1739B9793D29`。ArcGIS Pro 进程已加载 `AcceptanceAddin.dll`。外部交付包说明写 1.31，但其 `Config.daml` 标记 1.30；原包保存在工作区归档中。
- 在 Pro 当前活动工程（窗口标题 `MyProject6`，活动地图“地图”）实测 `live_status`、`live_list_layers`、`live_search_parcel("39")` 和 `live_zoom_to_parcel(..., confirm=True)` 均返回 `PASS`。编号 39 在“示例区2025年度第一批耕地恢复”图层的 `DIKUAI_NO` 字段唯一命中，OID 0，并完成选中/缩放。此次没有切换到工作区内搬移的 `成果图.aprx`。
- 实测发现 Python MCP 客户端在同步写管道前先切换 `PIPE_NOWAIT`，导致 `WriteFile` 失败（Win32 0）；调整为先写请求、再切换非阻塞读取后，Python 3.12 客户端的状态、图层、查询和缩放调用均通过。`tests/test_live_mcp_server.py`：9 passed。
- 本记录只验收了实时 MCP 与地块定位链。功能区/侧栏按钮的手工点击、搬移后的 `成果图.aprx` 在 Pro 中加载、首次使用人员操作、正式影像证据和外业采样仍待单独验收；当前地图返回成功不代表业务成果已正式通过。

## 2026-10-24 三度带地面间距与恢复修正版 1.43

- 网格采样新增 CGCS2000 三度高斯—克吕格米制 CRS 门槛：核对 GCS 基准、投影类型、中央经线及假东距；不接受经纬度、Web Mercator、UTM、六度带或矛盾参数。网格15米间距和最远点排序改用 ArcPy `GEODESIC` 地面距离，报告附坐标核验元数据。Pro 实际 WKID 4524采样通过、4550参数读回通过；错误投影/带号反例均拒绝。
- 修复 `map_export.py` 的重复 `__main__` 入口。损坏图件恢复用例曾证明旧逻辑同一调用连续执行两遍、把刚重出的 JPG 标成复用；修正后恢复收据区分重出/复用，Pro 定向采样/恢复/外业测试 **24 passed**。
- ArcGIS Pro 3.1.5 / Python 3.9.16 全量 `pytest tests`：**478 passed、3 skipped**。其中新增 CRS 逻辑11项及 ArcPy 网格集成断言；项目原始影像与地块资料未改写。
- Add-In 1.43：81项，SHA-256 `157981BD1EF8C229C1906B53A55ED92C8A2A728B1FF05B85F709C35627B7A774`；完整 MCP 测试包 `dist/采样助手_1.43_完整MCP测试包_20261024_204859_592311.zip`，219 项、1,250,465 bytes，SHA-256 `29C884B4987E08E9824ED791B56710D002F298ADD37C5D14D709319A98D8974F`。构建器重开 ZIP，CRC、所有成员哈希与运行文件清单通过；`test_mcp_server.py` 16项包含于通过的全量回归。包内无 `testdata/`、`out/` 或项目数据。
- 去冗余：`package.ps1` 改为只在 `dist/` 写一个规范包，不再在 `AcceptanceAddin/bin/Release` 额外复制。打包前先有失败契约测试，修改后 Add-In契约/包构建定向14项通过。此前19个 `bin/Release` 构建包的逐项来源与哈希已核对后移入 `addin/_archive/20261024_bin_release_duplicates/`。
- 本次清理：活动 `dist/` 收敛为当前1.43安装包和完整MCP测试包；18个旧顶层安装/测试包及4个历史测试结果目录（948个文件）移至 `_archive/20261024_superseded_builds/`，逐文件SHA-256读回通过。另将41个 Roslyn 编译临时目录（114个文件）归档并逐项校验；成功构建现在自动清理临时工作区，失败时保留诊断收据。归档是移出活动目录并保留原件，没有释放磁盘空间。
- C# 未改，复用已有 DLL；测试包尚未安装到当前 Pro/现场电脑，没有完成界面点击、实时管道、移机及新手验收，因此阶段3和业务验收仍未通过。

## 2026-10-24 MCP 路径隔离版 1.42

- 离线 MCP 与 Pro worker 共用 `arcgis_mcp/path_policy.py`，分开 `ARCGIS_MCP_READ_ROOTS` 与 `ARCGIS_MCP_WRITE_ROOTS`；旧 `ARCGIS_MCP_ROOTS` 仅影响读取。源目录/工程按只读范围检查，输出按可写范围检查，写入根同时允许读取本次成果。
- 全量 `pytest tests`：**419 passed、8 skipped**。新增跨根正反测试、server/worker 角色测试和便携包构建测试。Pro Python 3.9.16 导入 MCP server、worker 与包构建器并执行路径正反冒烟通过；Pro Python 已有 pytest。
- Add-In 1.42：212 项；SHA-256 `F9BD52C5B569062DCB5E4518FC95D182E40C3BAC20103783103CF2A8695E23DB`。完整 MCP 测试包：`../_archive/20261024_superseded_builds/采样助手_1.42_完整MCP测试包_20261024_200126_228901.zip`；219 项，1,492,054 bytes，SHA-256 `F4F6FBDA329E721AFF5BA8B18F382D1063691826333FA2603A138B1F3A94B192`。清单校验通过，解包后的 MCP 运行资源测试为 16 passed；包内未包含用户数据、输出目录或 `testdata/`。
- 运行/安装边界：未把候选 Add-In 安装进 Pro；未在当前 ArcGIS Pro 地图上点击定位/缩放；离线 GP 的完整界面执行、实时命名管道和现场新手验收仍未实测。 Pro 当前可见窗口在起始页，未打开地块工程。

## 2026-10-24 外业踏勘 ZIP 质检与影像精度门槛版 1.41

- 新增只读 ZIP 检查模块与工具箱入口，面向外业采集包的 GeoJSON/Shapefile 跨格式冲突、坐标来源、照片引用和字段完整度；报告始终将业务验收记为 `NOT_EVALUATED`。项目 DOM 的 `.source.json` 旁置元数据可将 GSD 和位置精度证明引用接入影像清单。
- 修正报备影像门槛：0.2 m 输出像元、重采样值或在线层级不再作为位置精度通过证据；需记录状态为 `VERIFIED`、精度数值严格小于 0.2 m，并附证明引用。证明文件内容与适用范围仍需人工核验。
- 修复地块定位器在没有业务编号字段时回退 OID 造成误定位；OID 搜索须在界面明确选择“对象ID（技术定位）”。离线 MCP 检查 worker `FAIL`/非零退出码；实时 MCP 两端校验缩放确认并对连接、I/O 增加 20 秒期限与长度限制。
- 全量 `pytest tests`：**411 passed、8 skipped**。Pro 3.1.5 Python 3.9 对旁置影像精度元数据接入执行冒烟通过；ArcPy 导入工具箱并读取到 `InspectFieldSurveyBundle_scqa`。C# 经 Roslyn 回退重新构建成功，DLL SHA-256：`8445C1EDD7A1F7F95E6997BF9A03D96B3BE4271861EA0BDA0B0C78AA9D529EC5`。
- Add-In 1.41：81 项；SHA-256 `16FEC48317F708200D24648B86FD9ED7921E14F41317E0B4027D2C9C087B1B59`。完整现场测试包：`../_archive/20261024_superseded_builds/采样助手_1.41_外业质检与实时MCP测试包_20261024.zip`；SHA-256 `C58D510C562EC98F43381ADB32B5BADBBEFA5EE802853A409E417091C588604`。包内组件哈希读回一致；候选包未安装到 Pro。
- 实际 ArcPy 工具执行冒烟未完成：单独导入工具箱并列出新工具成功，但同一合成测试进程随后调用工具箱时 Pro GP 偶发返回“未找到工具箱文件”；因此只记工具箱识别通过，不记工具执行通过。另未对当前 Pro 会话做实时管道、缩放或 GUI 点击验收。
- 用户所附模拟 ZIP 的内容已在前序只读分析中确认是湖南长沙的两条点记录，不含地块面、影像和完整照片；原临时文件路径本轮已不可访问，因此没有对原包生成并读回 JSON 报告。

## 2026-10-24 图例左下与字号优化版 1.40

- 调整 `visual_layout_spec`：Legend 左边距约0.18 cm、下边距约0.15 cm，贴近横向主图框左下角；图例标题11.5 pt、类别11 pt；点号9.5 pt、单元编号8.5 pt、页图标题18 pt、比例尺12 pt、页脚注记10–10.5 pt。区位小图说明缩短为“区位示意（界线待核）”以避免文字被裁切。
- 在 map_worker 新建工程时设置图例位置与点号/单元编号；map_export 另对已保存工程重设图例CIM字号和标注字号，保证旧工程重导出也生效。
- ArcGIS Pro 3.1.5 从上一轮工程和渲染计划实际重导出3幅图，目录为 `D:\work\高标准\验收预览_天地图_39号图斑_图例左下字体优化_20261024\map\`。人工复看 JPG 预览，图例位于图框左下，标题与分类文字层级清楚；区位小图标题完整显示。
- 输出任务总体和3幅图仍为 `NEEDS_REVIEW`，无出图 `FAIL`。影像原始 GSD/位置精度和正式区位地理要素仍缺；本次没有重新运行全套 pytest，ArcGIS Pro 实际导出与视觉复核已完成。
- Add-In版本更新至1.40；安装包 `../_archive/20261024_superseded_builds/SupplementaryCultivatedLandAcceptance_1.40_20261024_185139.esriAddInX` 含79个归档项，源码哈希读回通过；复用现有DLL（C#未修改），未安装到 Pro。SHA-256：`6883E8C64291BE8C0EAD18C3D4108F4A68E49B191C868B7512C5B2F7DDF3FE69`。

## 2026-10-24 课件第90页版式复核与 Add-In 1.39

- 复核依据：重新查看广西作业指导课件 PDF 第90页的实际图面。两类图均为横向主图框；地理位置图另有区位小图，采样点位图以影像为底图，红色地块边界/点位、左下图例和右上指北针清晰表达。同步复查插件主链 `acceptance/map_worker.py` → `acceptance/map_export.py`，并对照独立 `ARCGIS/location_map/` 与旧演示 `ARCGIS/demo_map/compliant/`；插件实际出图仍以 land_survey_toolkit 主链为准。
- 修正：采样/地理位置地图统一使用 A4 横版页面（297×210 mm）与横幅主图框；标题和页脚重新排位；Legend 图例框通过现有 `graphicFrame.backgroundSymbol` 设置近不透明白底；样图比例尺保留计划值 1:2000，地理图按当前影像覆盖由 1:15000 适配至 1:8000。隐藏面积表和重复比例尺条，避免与课件样图结构冲突。
- ArcGIS Pro 3.1.5 真实链路：复制原始测试任务至新目录，执行 `map_worker` 完成建工程与计划，再由 `map_export` 导出。结果位于 `D:\work\高标准\验收预览_天地图_39号图斑_横版复核_20261024\`，共3幅 JPG/PNG/PDF；JPG/PNG 为3508×2480像素、300 DPI，PDF为单页A4横版。导出结果无自动 `FAIL`，但所有图幅状态为 `NEEDS_REVIEW`。
- 独立 `check_map_elements.py` 检查：两张主图的图名、制图单位、成图时间、投影、比例尺和图例等文字项通过；图上指北针已人工看图确认可见。JPG与PDF均可重新打开。检查收据为输出目录 `render/map_element_check.json`。
- 未通过的业务核验保持显式：天地图时相、原始 GSD 和位置精度证明缺失；地理位置图没有正式居民点/道路/水系/境界线图层；区位小图使用的 DataV 界线来源未核，标题注明“界线来源待核，不得报件”。因此这组图是版式测试预览，不是正式报件或验收通过结论。
- 全套测试 `396 passed, 8 skipped`；ArcGIS Pro Python 3.9 对44个 acceptance 脚本的语法编译通过；另在 Pro 中实测 `_freeze_legend` 的白底图例修改链。Add-In 版本 1.39 包含79个归档项；包内 `map_layout_style.py`、`map_worker.py`、`map_export.py` 和 `Config.daml` 均与源码字节一致。使用 `-SkipBuild` 复用既有 C# DLL（本轮未修改 C#），未安装到用户 Pro。
- 包路径：`../_archive/20261024_superseded_builds/SupplementaryCultivatedLandAcceptance_1.39_20261024_183043.esriAddInX`；SHA-256：`1F5700481E451FF970BAE6991C40C0E2998F8BCF41E2CFF47A8B8709DE4E5C4D`。

## 2026-10-24 版面优化与 1.37 测试包

- 采样图按地块范围与 A4 图框比例自动选比例尺；当前 39 号图斑预览从 1:2000 调整为 1:1000，点位、边界和地块在版心更清晰。影像来源、坐标、面积及比例尺说明同步放大；原始 GSD 和位置精度仍需资料证明。
- ArcGIS Pro 3.1.5 输出：`D:\work\高标准\验收预览_天地图_39号图斑_版面重排复核_20261024`，含 3 幅 PNG/JPG/PDF。文件尺寸 2480×3508 px；空间与版式自动检查均无 FAIL，结果保留 NEEDS_REVIEW（影像精度缺证及版面人工确认）。
- 全套 pytest：392 passed、8 skipped；ArcGIS Pro Python 3.9 `py_compile` 通过。
- 测试包：`../_archive/20261024_superseded_builds/SupplementaryCultivatedLandAcceptance_1.37_20261024_171841.esriAddInX`；79 项内容，打包脚本读回并核对源码 SHA-256。Add-In 未安装/注册，桌面功能区和现场流程仍待实机验收。
- 测试包 SHA-256：`a20c446aa2e85ff9b5040b69791c7415fee34fed17afeb216ff1b4d40d8fce52`。

日期：2026-10-19  
目标：ArcGIS Pro 3.1.5 / SDK assemblies 3.1 / ArcGIS Pro Python 3.9.16  
构建：私有 .NET SDK 6.0.427；`dotnet build` 0 错误、0 警告

## 已完成的核验

- Python 3.9 运行检查通过；ArcGIS Pro Python 工具箱识别 9 项工具。原“矢量复制”工具现在可接收要素类或 GeoJSON/Esri JSON 文件，混合几何会拆成点、线、面要素类。
- `.esriAddInX` 位于 `../_archive/20261024_superseded_builds/SupplementaryCultivatedLandAcceptance.esriAddInX`，12 项包内容包含 DAML、Python toolbox 和接入核心。C# 源码未变化，复用此前编译的 DLL；本轮没有重新编译 DLL。
- 修正动态 GP 执行时核心模块搜索路径丢失：Pro 将 Python Toolbox 的 `execute()` 放入生成脚本，顶层 `sys.path` 不会传入；改为设置核心路径环境变量，并在各工具 `execute()` 入口恢复模块路径。
- 直接通过 ArcPy 动态 GP 工具函数调用 GeoJSON 导入成功，输出 `Probe_POINT=18`、`Probe_LINE=4`、`Probe_POLYGON=3`。此验证运行源树中的工具箱，和安装包采用相同相对目录；功能区按钮仍须在重启后的 ArcGIS Pro 界面检查。
- Pro 首次启动后，功能区访问树未出现自定义标签；检查发现原 DAML 把 tab 放在模块外，未使用 Pro Add-In 结构。现已改成 `<modules><insertModule><tabs><tab>` 并将 groups/controls 纳入模块，已重新注册。新包 SHA-256：`66F0560577077E153A3954970B7A0D86DDE02F0F70903D684EC5A56D061B79FD`。安装位置：`%USERPROFILE%\Documents\ArcGIS\AddIns\ArcGISPro\{83A6E23E-58CE-4B92-BC7A-7C5012A8BC31}`。
- 已保存“广西补充耕地质量验收数据库配置.aprx”并重启 Pro，以加载新 DAML。重启后的 Pro 当前在“打开工程”对话框；自动化输入无法激活该对话框，因此尚未重新打开工程验证自定义功能区。此前打开旧配置时访问树未显示该标签；新标签须在重启并打开工程后复核。
- ArcPy 合成项目验证路径：`temp/addin_smoke_20261019_2110/999999模拟县/SIM-ADDIN-001`。WKID 4525 仅用于合成演示，不作为真实项目默认值。
- 合成项目建立空附录 G 图层、目录、质检报告和汇交清单；生成 3 个混合样候选点及 3 个容重候选点。质检为 2 项 PASS、0 项 FAIL、2 项 NEEDS_REVIEW；待审核项包括两个正式源图斑和现场证据。汇交目录检查对空目录保留 NEEDS_REVIEW。
- 混合样候选点要素名 `混合样分样点_复核`，容重候选点要素名 `容重平行样_设计`；两层均是设计点，未伪装为现场实采点。

## 尚未验证及限制

- ArcGIS Pro 已通过进程启动，但 Windows UI 截图通道两次超时；因此功能区标签是否在重启后可见、按钮能否在桌面交互打开工具箱尚未目视确认。重启 ArcGIS Pro 后应检查“补充耕地验收”功能区。
- 本机 VS Build Tools 缺少 .NET SDK resolver；Esri targets 的 `CodeTaskFactory` 在 .NET MSBuild 下不可用。首版采用 `dotnet build` 编译、PowerShell 打包、Pro `RegisterAddIn.exe` 注册；不改 VS/Pro 安装。
- 公开样例仅用于格式/导入链路验证，不能代替广西正式项目资料。RTK CSV 样例为 30 条记录，输出原始坐标点和投影点各 30 个；样例来自 This Ground，许可 CC BY 4.0，源坐标为 WGS 84，数据中标注 FIX/RTK。公开 GeoJSON 样例共 25 个混合几何，拆出点 18、线 4、面 3 个要素；源文件保留。ArcPy 操作结果摘要在 `verification/public-sample-validation.json`。
- 公开 CAD 样例是 NSW 政府发布的 GDA2020/MGA 空白制图表，未给出实际 MGA 分带/控制点；仅验证 DXF 可转换成 GDB，不据此确认空间定位。正式 CAD 的单位/带号/控制点仍须核对。RTK 的解状态、天线高、坐标轴序、高程类型及基准转换仍须按项目测量资料复核。
- 按钮对话框交互、重启后功能区是否可见、真实 CAD 空间定位及真实 RTK 项目基准仍未验证。动态 GP 模块路径问题已在当前 Pro 3.1.5 Python 环境通过独立 GP 调用修复并复验。
- 下载的公开样例和临时验证库仍留在 `temp/web_samples`；本轮清理该目录的递归删除操作被本机安全策略拦截，未改用其他方式删除。样例导入统计已保存在 `verification/public-sample-validation.json`。
- 当前自动质检只检查附录 G 评价单元层和 CRS；附录 C 字段/拓扑、50 亩面积、采样点空间 QA、实采回填、正式图件和报告生成仍未自动实现，须人工复核。

## 2026-10-20 质量修复与 ArcPy 实测

- 用例：`tests/test_acceptance_plugin_contract.py` 与 `tests/test_land_survey_toolkit.py`，14 项通过；覆盖附录 G 字段缺失、非面几何、错误 WKID、随机 TXT 不得冒充成果、狭小图斑采样与重复容重点位前置拦截。
- ArcGIS Pro Python 3.9.16 / ArcPy 3.1.5 实测脚本：`verification/arcgispro_smoke.py`。合成 FileGDB 的附录 G 54 个字段均通过；检查结果为 NEEDS_REVIEW（正式源图斑和现场证据仍须核实）；混合样 3 个、容重平行设计点 3 个均成功创建。所有点均标记为设计候选，窄图斑的 15 m 间距未满足并明确提示人工复核。数据仅用于软件烟雾验证，不是实际验收成果。
- 通过 ArcPy 3.1.5 实际导入 Python Toolbox；识别到 9 个工具：`CreateProject`、`ImportCAD`、`ImportVector`、`ImportRTK`、`InspectVector`、`CompositeSampling`、`BulkDensitySampling`、`QualityCheck`、`DeliveryCheck`。
- 本次完整烟雾结果：`temp/addin_smoke_20261020_03/smoke-result.json`；模拟 FileGDB 留在同目录。
- 修复范围：质检现在核对评价单元 Polygon、附录 G 字段类型/长度及目标 WKID；汇交检查只把匹配类别的文件列为候选并明确不判内容合格；采样在正式 15 m 网格点不足时细化生成覆盖点，并显式报告间距不足；容重数据空编号、重复编号、空坐标或错误几何在建输出前拒绝。
- 更新安装包已注册到当前用户目录。包 SHA-256：`0C7C0CECB380BB1FA06371D77E2E4E54EBF2AA2607770D4AC082C1827C7F1600`；安装目录包哈希一致。12 项归档条目读回正常，包含已更新 Python 核心。
- 本机当前未发现 .NET SDK，因此只重打包并复用了原有 `AcceptanceAddin.dll`；本轮 C# 源码和 DAML 未改动，不能把这次交付描述为重新编译验证。
- Pro UI 检查再次受阻：ArcGIS Pro 在“打开工程”对话框中，但当前电脑操作接口对该窗口返回“failed to activate captured window”，截图通道返回超时；不能确认重启后功能区和按钮是否实际显示/可用。需要在 Pro 中人工重启并打开样例工程复核。
- 两个早期合成尝试目录 `temp/addin_smoke_20261019_2100` 与 `temp/addin_smoke_20261019_2105` 留有未完成的临时输出。清理该两目录的递归删除命令被本机工具安全策略拦截，本轮未通过其他方式绕过。

## 2026-10-20 完整项目打开与更新包桌面核验

- 纠正前序工程选择错误：`广西补充耕地质量验收数据库配置.aprx` 是仅用于配置/建库的壳工程，不足以验证完整工作流。桌面验收现使用 `out/完整模拟项目/999999模拟县/SIM-999999-2026-001/ArcGIS工程/模拟补充耕地质量验收项目.aprx`。该项目数据全部是合成样例；底图服务不等于真实项目边界或验收依据。
- 通过 ArcGIS Pro 启动参数打开完整 `.aprx`，ArcGIS Pro 进程 `48624` 状态 Responding；窗口标题为“模拟补充耕地质量验收项目”。ArcPy 只读检查此前已确认其地图/布局、GDB 引用和图层均可加载。
- 使用 UI Automation 可访问树核对当前桌面 Add-In：自定义标签“补充耕地验收”存在；展开后存在“项目、数据接入、采样设计、质检与汇交”4 组及 8 个按钮。由于 Windows 截图仍返回 FrameArrived timeout，未将截图目视列为证据；通过可访问性树只证明控件已加载，尚未证明每个按钮都能成功打开对应 GP 对话框。
- 更新了 Python Toolbox/CAD/RTK 核心后，使用 `package.ps1 -SkipBuild` 将现有 SDK DLL 与最新 Python 源重新打包，并用 `install_addin.ps1` 注册当前用户 Add-In。新包 SHA-256：`0E01A746DA08E6A3E0C88E400FDDAC3A0CECC2C1B6103EE79B227B9C84B29C52`；dist 与安装目录哈希一致；包内 `acceptance/ingest.py` 与源码 SHA-256 一致；归档共 12 项。
- 关闭旧 Pro 进程后，用完整模拟项目重新启动 Pro；新进程已读取更新后的包并加载全部 4 组/8 按钮。C# Add-In 本轮未重新编译（SDK DLL 沿用此前构建件），因为本轮没有更改 C# 或 DAML。
- 本轮再次运行 `python -m pytest tests/test_acceptance_plugin_contract.py tests/test_land_survey_toolkit.py -q --tb=short`：28 项通过。RTK Pro 3.1.5/ArcPy 环境隔离与 XY/YX 案例由 `rtk_validation` 子任务完成实测；仍需按真实项目测量资料审核轴序、解状态、天线高及垂直基准。
- 桌面按钮的实际点击/GP 参数对话框还未验证：截图通道超时，Accessibility `click(element_index)` 返回 `coordinate input geometry is unavailable`。因此当前结论仅为 Add-In 标签、功能组和按钮已加载；命令映射已静态核对，GP 参数由 ArcPy 加载检查。未把这两类证据夸大成按钮端到端通过。

## 2026-10-20 遥感时相筛查首版

- 新增“遥感时相筛查（NDVI）”工具和 Ribbon“遥感核查”按钮。输入为前/后期红光与近红外单波段栅格、评价单元面/编号、日期、数据源及两期反射率比例/偏移；检查栅格坐标系、行列数、范围、像元大小和评价单元坐标系后计算 NDVI 差值。
- 输出为前/后期 NDVI 与差值 GeoTIFF、按评价单元汇总的 CSV、参数和来源 JSON。状态固定 `NEEDS_FIELD_REVIEW`，没有自动变化阈值，也不作为地类、土壤质量或验收结论。云/阴影检查采用操作人员确认，软件不宣称已自动验证掩膜质量。
- ArcGIS Pro 3.1.5 / ArcPy 3.9.16 合成验证：20×20、10 m 像元，两个评价单元各覆盖100像元；预期 U-1 NDVI差值均值0、U-2为+0.1，实测一致。完整 Python Toolbox 动态调用成功并产出 GeoTIFF、CSV、JSON，结果状态 `NEEDS_FIELD_REVIEW`。最终脚本及产物在 `temp/remote_sensing_gp_toolbox_20261020_final`，另有基础合成输入在 `temp/remote_sensing_smoke_20261020_run2`；均非卫星实景项目。
- 使用私有 .NET SDK 6.0.427 重编译成功：初次编译0错误、2个Windows平台兼容性警告；最终打包重建0错误、0警告。Add-In 版本升至0.2，包已读回24项、核对5组/9按钮、DAML命令类及工具箱文件并注册；最终 SHA-256 `1E0F6A6953D47E57BE05CD33ADBD7570C1C0C92A32324E08A458768C2F85FE7B`，用户级已注册包同哈希；包内 Python Toolbox SHA-256 与源码一致。
- 注册时 ArcGIS Pro 进程 27504 仍开着完整模拟工程，因此该会话仍使用启动时缓存的旧 Add-In。没有强制关闭可能含未保存更改的工程；新遥感按钮需要用户下次重启 Pro 后确认加载。源码工具箱 GP 实跑已通过，按钮实际点击/对话框仍未验证。
- 规范依据：广西指导书 v1.1 PDF第11页/印刷第5页将影像图列为“若有则提供”，PDF第44页/印刷第38页要求采样点位布置图以影像图为底图；没有规定 NDVI 合格阈值。工作流和局限见 `docs/remote-sensing-workflow.md`。

## 2026-10-20 真实遥感样包差距核查与 Add-In 0.3

- 公开真实数据：Element 84 Earth Search 的 Sentinel-2 C1 L2A，南宁武鸣附近 T48QZL，2025-11-25 S2C 与 2025-12-30 S2B；裁剪真实 B04/B08 10m 与 SCL 20m。样包位于 `testdata/real_sentinel2_wuming_probe`，总量小于 1MB，含 STAC Item JSON、SHA256SUMS、裁剪输入及插件输出。评价单元几何来自原模拟 GDB，仍是合成面。
- 真实产品测试发现原工程评价面 WKID 4524 与影像 WKID 32648 不同，工具拒绝自动猜转换；显式投影后的副本可以处理。这个人工/项目资料确认步骤仍保留。
- 遥感工具新增成对 SCL 输入，最近邻对齐至红/NIR 网格，排除类别 0、1、3、7、8、9、10、11，并在 CSV/JSON 记录类别分布与逐评价单元排除数。ArcGIS Pro 3.1.5 / ArcPy 3.9.16 通过真实数据实跑。
- 修正评价单元临时栅格化的锁定问题：改为 `in_memory` 工作区。测试先确认旧实现会在输出目录留下 scratch GDB；修正后打包版本的动态 GP 复测通过，断言无 scratch GDB 残留。
- Add-In 版本 0.3 已重打包并注册当前用户。包 SHA-256：`88A2EC5C42EC3E32B33629126B93E8A0B79F1B5AE0DD8B7688E49192EE2B563B`；dist 包与用户安装包哈希一致，归档 24 项。包内 `.pyt` 与核心文件 SHA-256 均与源码一致；通过从安装包解出的工具箱在 Pro 3.1.5 执行真实数据 GP，结果 `NEEDS_FIELD_REVIEW`。
- 本次用 `package.ps1 -SkipBuild` 沿用已编译 `AcceptanceAddin.dll`；本轮没有改 C# 命令代码，没有重编 DLL。当前 ArcGIS Pro 会话没有强制关闭，仍可能缓存上次启动时加载的 Add-In；版本 0.3 需下次重启 Pro 后再检查 Ribbon 控件。按钮实际点击 UI 本轮未复核。
- 自动化检查：`test_remote_sensing.py`、`test_acceptance_plugin_contract.py`、`test_land_survey_toolkit.py` 共 29 项通过。

## 2026-10-20 真实数据差距审计与汇交规则修正

- 对照广西 v1.1 第10.4(1)–(2)、6.2.5、附录I：CMA证书/附表、样品交接记录、检测原始记录属于“其他资料/适用性复核”，不应作为所有项目统一硬性缺件。`scripts/delivery_package.py` 已调整为提醒项；污染报告仍按污染风险条件选择，项目建设资料仅项目类要求。
- 汇交核对新增举证照片类别提示：每评价单元东南西北全景至少4张、近景至少2张、现场工作照至少1张、四分法至少1张；土钻断面和混合样照片按分样点，容重及侵入体/砾石照片按相应样点。类别和项目相关数量保持人工复核，不因任意JPG文件存在而判合格。
- 新增回归测试先复现旧逻辑将可选/适用性材料误报为 FAIL，再验证修复。相关插件与附录C、遥感测试合计 33 项通过。
- ArcGIS Pro Python 3.9.16 对 `out/完整模拟项目/999999模拟县/SIM-999999-2026-001` 只读执行汇交检查，状态 `REVIEW_REQUIRED`、0项硬失败、数据库检查无错误；四类其他资料均识别为已提交，照片类别仍待人工审阅。该项目与文件仍为模拟材料。
- 主 README 已更正为验收插件主线，并把旧版双尺度国土调查制图脚手架、200/400/600 m²阈值与本验收业务明确隔离。公开真实 Sentinel-2 样包只验证遥感处理链，当前仍缺真实广西验收 CAD/RTK、外业样品链和签审成套数据。
- Add-In DAML 版本升至 0.4，已用 `package.ps1 -SkipBuild` 重打包并注册到当前用户；沿用已有 DLL（本轮未改 C#）。包内 `Install/scripts/delivery_package.py` 已读回并确认包含新增复核项，dist 与用户级安装包 SHA-256 一致：`8746DF33208E19EC9F51FDCAA0DBA8089A8E2CE2B1ADA8025EAA24BF6F76312F`。ArcGIS Pro 正在运行的 AssemblyCache 仍为旧内容，因此未声称当前会话已加载 0.4；须在保存项目后重启 Pro 才能更新运行缓存。桌面按钮点击链路、项目迁移断链和完整真实 CAD/RTK流程仍未闭环。

## 2026-10-20 评价单元面积筛查与 Add-In 0.5

- 将规则表中的“评价单元原则上不大于50亩”接入数据库质检。使用 ArcPy `Geometry.getArea('GEODESIC', 'SQUAREMETERS')` 量算；空库或 CRS 未知标为 `UNVERIFIED`，超限标为 `NEEDS_REVIEW` 并给出单元编号/面积，不自动判定不合格。
- 新增边界测试：33333 m²可通过筛查；33334 m²产生待复核项。相关插件契约、附录C、遥感测试共 35 项通过。
- ArcGIS Pro 3.1.5 / ArcPy 3.9.16 在完整模拟项目实际核验：2个评价单元，面积筛查 `PASS`、无超限；完整质检仍 `NEEDS_REVIEW`。又从 Add-In 0.5 安装包解压工具箱并运行 `QualityCheck` GP 工具，结果一致。运行输出在 `temp/qa_area_pro_20261020/`。
- Add-In DAML升至0.5，重新打包并注册。dist 与用户级安装包 SHA-256 均为 `3850FBC68B1DCDB03A81C0B2A3E3A2F0B064333902202DFC469A795F78C7314D`；重打包复用旧 DLL，因为未修改C#。Pro当前进程仍在运行且缓存旧版，未关闭可能含未保存修改的项目，因此新版 Ribbon 按钮交互尚未验证。桌面 CUA 此轮返回 `apps:[]`，不能执行界面点击；使用 Pro Python 直接从0.5包解出的工具箱成功运行质检 GP。
## 2026-10-20 集成调试补充：RTK 编码、事务式建库与 Sentinel 阴影类

- 完整相关测试集：48 项通过（Appendix C、遥感筛查、插件契约、主工具箱）。
- ArcGIS Pro 3.1.5 / ArcPy 实际执行项目初始化，生成 1 个 GDB 要素类，读回 `project_manifest.json` 确认路径指向最终 GDB，初始化暂存目录无残留。失败清理与重试由故障注入单测覆盖。
- 在真实武鸣 Sentinel-2 栅格副本中前后期 SCL 各注入一个类别2像元，实际 ArcPy 筛查记录每期类别2排除 4 个 10m 像元；每个评价单元按空间相交计数（本次为一个单元4、另一个0）。原始样包没有改变。
- 修正 SCL 无效类别清单，增加类2阴影类；公开样本本身只有类别4/5，注入测试用于覆盖掩膜分支，不是实地阴影证据。
- 增加 GB18030/UTF-16 BOM、逗号/分号/制表符/竖线分隔符 RTK CSV 解析和格式审计信息；ArcGIS Pro 3.1.5 实际导入 GB18030 制表符格式的模拟 RTK 文件，2/2 点数、字段映射、源/目标 WKID 与高程字段读回正确，状态如预期保留为 `NEEDS_REVIEW`。这只是导入技术验证，不是真实RTK观测成果。初始化阶段改为同卷暂存后原子改名，失败只清理自身暂存目录。
- 当前 `.esriAddInX` 的 Ribbon 按钮实际点击与 GP 窗口交互仍未获得 UI 实证；不能据本记录宣称桌面端按钮操作已验收。
- 荔浦芋稻轮作公开数据包已用 ArcGIS Pro 3.1.5 实测，真实修仁乡边界（WKID 4525，1面）和土壤样点（WGS84，130点）通过插件矢量导入，源数/目标数、字段和WKID读回一致。遥感栅格存在 WKID=0 球面 ArcInfo 投影/未知坐标系，10 m 单期NDVI与专题栅格无法直接作为当前Sentinel双时相红光/NIR输入；详细问题与路线见 `docs/lipu-real-data-gap-analysis.md`。
- DAML版本升至0.6，包按源码重新生成并通过清单校验：24 项，包内 `acceptance/ingest.py`、`project.py`、`remote_sensing.py`、`sampling.py` 及工具箱均与当前源码 SHA-256 一致；dist 包 SHA-256 为 `6271725B9D3C8188BC6D864F28D9CCC694C2BFBFF935A8D9FC6D0FB18D15C6BC`。复用已编译 DLL（没有C#源码变更）；当前运行中的Pro会话尚未加载该包，Ribbon按钮点击链路仍未验证。

## 2026-10-20 遥感数据包预检工具与荔浦栅格实测

- 新增 `遥感数据包预检` Python Toolbox 工具，递归扫描 TIFF/TIFF/IMG/ASC/JP2/VRT，清单记录波段数、栅格尺寸、像元、NoData、范围、WKID、自定义 CRS WKT、旁置文件和读取错误；未知/自定义坐标系明确阻断，不会擅自赋值 EPSG 或改写源栅格。
- 测试先失败再实现；新增 2 个单测，覆盖已知/自定义/未知 CRS、旁置文件、JSON/CSV 读回和损坏栅格错误留痕。
- ArcGIS Pro 3.1.5 动态 GP 实测荔浦真实公开数据包，识别 8 个栅格：2 个 ASC 为无坐标系，6 个 TIFF 采用 WKID=0 的 Sphere ArcInfo Lambert Azimuthal Equal Area 自定义球面 CRS；均正确标记阻断，报告生成 CSV 与 JSON。实测清单见 `temp/real_data_lipu_20261020/raster_audit_result.json`，可复现输出见 `temp/real_data_lipu_20261020/raster_audit_gp_output/`。
- 完整相关测试集增至 50 项并通过；Add-In 包已升至 0.7，共26项。包内 `raster_catalog.py` 与工具箱 SHA-256 和源码逐项一致，缺件检查已列入 `package.ps1`。包 SHA-256：`0B7CE311AE6E1C160FAE347B6091B5599BDA73E51E5C919CB6592419D394E949`。
- 从 0.7 `.esriAddInX` 解包后，在 ArcGIS Pro 3.1.5 通过包内 `.pyt` 运行新预检工具；读回核心模块路径确在包内 `Install/acceptance/`，荔浦8个栅格分类结果与源码实跑一致（2无坐标系、6自定义坐标系）。
- 当前 Pro 会话仍运行旧加载缓存；包内 GP 工具实跑已完成，Ribbon按钮点按/窗口链路仍未通过桌面 UI 验收。

## 2026-10-20 遥感数据预检缺陷修复与 0.8 包验收

- 修复多波段 NoData 未列出、自定义 CRS 多波段漏提醒、单影像被误报为 CRS 已配对、CRF/FileGDB 栅格漏扫及 CSV 缺 WKT/范围字段；清单现在分别记录坐标系定义状态、待配对核验标记和多波段复核标记。
- 新增回归覆盖并按 RED→GREEN 修复；发现并修复 GDB 遍历重复计数后，相关测试集 18 项通过，CRF+FileGDB 合成清单数精确为 2。
- ArcGIS Pro 3.1.5 对荔浦实数据扫描 19 个栅格/图像候选：13 个缺坐标系定义（含 JPG、2 个 ASC），6 个 TIFF 有完整自定义 WKT；逐波段 NoData 与复核标记写入 JSON/CSV，源文件未改写。
- DAML 升至 0.8，复用既有 DLL；`package.ps1` 核对 26 项包内容、12 个工具元数据文件，包内遥感核心与源码字节一致。最终 dist SHA-256：`3186E1E6A3C6D4F2327A453673E051E63A5836B669C0355F83E525FD7B6AE1DA`。检查收据：`temp/real_data_lipu_20261020/addin_v08_validation_receipt.json`。
- 从 0.8 包解出工具箱，在 Pro Python 新进程运行预检 GP，加载路径确认指向包内 `Install/acceptance/raster_catalog.py`，分类结果与源码实跑一致。Pro 首次同进程解包后立即导入工具箱会报“未找到工具箱”，分进程后正常；后续正式部署仍需关注 Pro 的缓存加载与 Ribbon UI。
- 自定义 WKT NDVI 同 CRS 合成栅格/面数据验证仍为 16/16 像元、NDVI 均值变化 0.2；这不构成到 CGCS2000 的坐标转换依据。荔浦数据论文和本地元数据未提供自定义球面 CRS 到项目矢量基准的可靠转换参数。

## 2026-10-20 窄地块采样回退缺陷与 Add-In 1.0

- 真实 ArcPy 中 `Polygon.labelPoint` 返回 `Point`，不是带 `distanceTo` 的 `PointGeometry`。补充两块窄地块回归测试，先确认旧代码因 `AttributeError` 失败；随后将回退点包装为源坐标系下的 `PointGeometry`。
- 使用 ArcGIS Pro 3.1.5 自带 Python / ArcPy 在新建合成 FileGDB 中实跑两块 10 m × 0.1 m 窄面：成功写入2个点要素，几何类型均为 point；需3个但生成2个的短缺被标为 `NEEDS_REVIEW`。窄小图斑无法同时达到一般15 m间距与单元最低点数时须人工调整；输出仅为设计候选点。
- 相关 pytest：采样插件契约、项目工具包、附录C、栅格清单共52项通过。Pro实跑脚本和输出库在 `temp/sampling_narrow_arcpy_probe_20261020.py`、`temp/narrow_parcel_probe_20261020_v2.gdb`。
- DAML升至1.0。仅 Python 核心/测试与包资源有变化，复用既有 C# DLL；`package.ps1 -SkipBuild` 校验26项包内容及12个工具元数据文件，之后调用 `RegisterAddIn.exe` 注册当前用户。读回确认包版本1.0、包内 `sampling.py` 与源码字节一致，dist 与用户安装包 SHA-256 均为 `3C1CC719EF6661C702ECB17C906FA24F031BCE7CDB5D31A3156260195403E03B`。
- 当前 ArcGIS Pro 仍运行旧会话缓存；未关闭项目以免丢失未保存内容，因此功能区按钮加载和点击尚未验证。重启 Pro 后需做桌面端验收。

## 2026-10-20 荔浦真实边界 Sentinel 联跑与 Add-In 1.1

- 从 Earth Search STAC 查询到荔浦修仁附近两期 Sentinel-2 C1 L2A：2026-10-08 S2C 与 2026-10-13 S2B。小范围读取 B04/B08 10 m 与 SCL 20 m，使用 STAC 元数据的 scale 0.0001、offset -0.1；仅下载修仁区域窗口，未下载整景。
- ArcGIS Pro 3.1.5 / ArcPy 3.9.16 在案例包真实修仁乡边界上运行遥感核心。结果：1,194,932 个评价区像元，掩膜后 747,608 个共同有效像元，有效覆盖率 62.5649%；两期 SCL NoData 各395,725像元，因单景瓦片未完整覆盖乡界而保留 `NEEDS_FIELD_REVIEW`。该边界属于乡镇案例区，不是补充耕地验收项目地块。
- 实测发现总览曾把评价范围外的矩形栅格像元纳入统计。先写失败回归，再将输出 NDVI/差值栅格限制到评价单元，并把总览与分区统计统一，同时分开报告 SCL NoData 数和分类数。复跑读回：差值栅格有效像元747,608，评价单元外有效像元0，总览/分区有效像元相等，均值相同。相关遥感测试6项通过。
- 相关测试集：项目插件契约、核心工具、附录C、栅格预检、遥感核心共60项全部通过。
- DAML 更新至1.1；`package.ps1 -SkipBuild` 复用现有 C# DLL并重新打包 Python 核心。包内 `acceptance/remote_sensing.py` 与源码 SHA-256 一致；26个归档条目；包 SHA-256：`BE4604C7162A7FD0DEBB0D69303BEA2FA8184B03C10F92A9ABE156BCF53745B7`。已调用 `RegisterAddIn.exe` 注册当前用户，安装目录哈希与分发包一致。
- 当前 Pro 会话未重启，避免关闭用户未保存工程；因此新版 Ribbon 加载和按钮交互尚未复核。STAC 自动检索、多瓦片自动拼接仍是明确的遥感能力缺口；本次 5 天、不同平台的 NDVI 变化仅作管线示范，不作耕地质量或验收判定。

## 2026-10-20 Sentinel STAC 检索与相邻瓦片准备 Add-In 1.2

- 新增 `Sentinel2CatalogSearch`、`PrepareSentinel2Pair` 与核心 `acceptance/stac.py` / `acceptance/sentinel_prepare.py`。显式联网检索 Earth Search Sentinel-2 C1 L2A；记录 AOI 指纹、完整分页状态、项目波段元数据和日期/平台/处理基线组；ArcPy 计算每组瓦片足迹几何覆盖率。操作者选定两期后，GDAL `/vsicurl/` 镶嵌并裁切 B04/B08/SCL，按 NoData/重采样语义输出，再运行现有 NDVI+SCL 单元筛查。
- 修仁乡完整 AOI 10/01-10/20 检索实测2页、8景、4个日期组，QDG/RDH相邻瓦片各两景；小样 AOI足迹覆盖率组组100%。AOI几何会发送至公共目录，运行工具前需要确认；影像云量及几何覆盖不替代有效像元覆盖核验。
- 使用修仁边界瓦片交界处约3.14 km²的演示 AOI，选2026-10-08 S2C/2026-10-13 S2B，完成6个本地裁片及NDVI+SCL实际运行。读回10m B04/B08为200×200、Float32/NoData=-9999；20m SCL为100×100、UInt16/NoData=0；ArcPy报告WKID 32649。单位有效覆盖率98.4821%，两期SCL NoData各120像元，最终 `NEEDS_FIELD_REVIEW`。演示几何不是补充耕地验收单元。
- 全修仁乡边界准备试跑因公网 B08 COG Range读取长时间停滞而中断；未把半成品记作成功。中断时唯一运行目录仍残留部分临时文件，工具本身现对可捕获的异常/中断清理本轮目录；用户级 Pro 会话未重启，避免关闭可能存在的未保存工程。
- 测试集35项通过；ArcGIS Pro 3.1.5 / Python 3.9.16 动态加载工具箱13项并读取参数列表；目录检索与选景筛查实际运行成功。用私有 .NET SDK 6.0.427 构建Add-In，最终重建0错误、0警告。包30项，逐项核对新 Toolbox、`stac.py`、`sentinel_prepare.py` 和相关元数据 SHA-256；DAML版本1.2。`RegisterAddIn.exe` 已注册当前用户，分发包和安装包 SHA-256一致：`D205508F39B5C35A5B73AB8103286723CAA85D4B090C387EAA3F36741165A26B`。
- Pro当前会话尚未重启，因此新 Ribbon 按钮加载/点击链路仍需下次保存项目后复核。较大 AOI 在线 COG读取耗时是已知性能限制；目前可通过分区处理避免一次读入过大影像，NDVI结果始终需要现场人员复核，不构成质量等级或验收结论。

## 2026-10-20 采样规范映射、CAD空结果保护与报告版式修订（Add-In 1.3）

- 对照广西 v1.1 PDF 原文更正采样条款映射：混合样分样点数量/均匀网格依据 §6.1.4；0–20 cm、15 m 与小图斑处理依据 §6.2.4.3(1)；容重3个平行样和附近2 m/间距2 m/100 cm³依据 §6.2.4.2–§6.2.4.3(2)。附录 E 编号读回规则为混合样号等于评价单元编号、容重样号为 RZ+评价单元编号；设计点另用 POINT_ID 和 REP_NO 标识。
- 采样核心在两个入口显式要求 Projected CRS，并对线性换算值做有限且正数检查。ArcPy 实测 WKID 4326 的 metersPerUnit 属性不受支持，原实现通过缺属性一般已会拒绝；本改动是显式类型防护、错误提示与测试覆盖，不把它描述为已复现的原始错算。WKID 4525 读回 Projected、1.0 m/unit。
- CAD 转换后逐类统计输出要素数；无输出类或总数为0时标记 `EMPTY_NEEDS_REVIEW`，Python Toolbox 报错提示不得作为有效成果。补充核心单元测试。
- ArcGIS Pro 3.1.5 / ArcPy 3.9.16 新建合成 FileGDB烟雾实跑成功：附录 G 54字段均通过；混合样生成3个、容重样生成3个；读取样点行确认混合样编号 `SIM-UNIT-01`、点位ID独立且深度0–20 cm，容重编号 `RZSIM-UNIT-01`、REP_NO为1–3、环刀100 cm³。小模拟面无法满足15 m间距，按规则保留 `NEEDS_REVIEW`，未虚报PASS。输出位于 `temp/arcgispro_smoke_after_audit_20261020_retry1/`。原烟雾脚本先因过期的英文QA条目名称停止在汇总阶段；更新条目查找与属性读回断言后重跑成功，未将失败尝试目录记为有效结果。
- 纯 Python 插件测试45项通过，采样/接入/工具箱 `py_compile` 通过。Add-In DAML 升至1.3；包结构30项，包内 `sampling.py`、`ingest.py`、`rules.py` 和工具箱字节均与源码一致。因当前机器没有 .NET SDK，本次未重编 C# DLL（本轮 C# 源码未改），使用既有 DLL 重新装包。`RegisterAddIn.exe` 注册完成，dist 和用户安装包 SHA-256 同为 `6FEBF48351C84406AB78D1F3516696DC4E856AAB394B4AB6968BE711AAF3791E`。
- 报告支线重新排版了01县级报告和03市级报告的 DOCX/PDF。01为10页、03为7页，更新目录页码/页字段、表格版心与地图页；保持宋体正文、黑体标题、黑字白底及模拟/未实施说明。渲染检查收据在 `temp/report_repair_20261020/`。旧版01 `.doc` 未覆盖，若汇交包保留该文件，须标为历史格式或同步更新，避免误选旧版。
- Pro 当前活动会话没有重启，所以 1.3 Ribbon 按钮加载/点击仍未验证；实际 ArcPy 核心和合成工具箱链已验证。真实 CAD 控制点/RTK观测/垂直基准和业务现场证据仍需真实、获授权项目资料。

## 2026-10-20 RTK/CAD 源溯源及垂直基准标记（Add-In 1.4）

- CAD 文件及发现的 PRJ/WLD/world 配套文件、RTK CSV 均写入路径、字节数、UTC修改时间与 SHA-256；GP 工具结果中展示源哈希，接入 JSON 清单持久保存该结果。
- RTK 原坐标点和目标投影点写入 `Z_STATUS`、`Z_FIELD`、`VERT_REF`。有 Z 字段统一标记 `UNVERIFIED`，高程参考写为“未提供/未核实”；无 Z 字段标记 `NOT_PROVIDED`。不根据数值或水平 WKID 推断垂直基准。
- TDD新增3个来源指纹/垂直状态测试；接入相关测试5项通过，整体测试48项通过。ArcGIS Pro 3.1.5 合成 RTK CSV 实际导入2点，源文件哈希与CSV字节重算一致；原点层和投影点层均读回 `UNVERIFIED/Z/未提供/未核实`。ArcPy产生“未定义垂直坐标系”警告，和此风险标记一致；此运行不证明真实RTK高程基准已转换。
- Pro smoke 当前输出位于 `temp/arcgispro_smoke_rtk_audit_20261020/`。CAD 实体计数在假 ArcPy合约测试覆盖，公开荔浦包没有 CAD 文件，尚未对真实 DWG/DXF 做 ArcPy导入验证。
- Add-In DAML升至1.4。机器仍缺.NET SDK，未重编未改动的C# DLL；复用既有DLL打包并注册。此版本的新 Ribbon UI仍须重启Pro后桌面实点验证；真实项目区边界/控制点校核、垂直转换配置仍待正式测量资料。

## 2026-10-20 RTK 不可变源快照及失败回滚（Add-In 1.6）

- RTK 导入前创建源 CSV 的字节快照，写入项目目录旁的 `temp/`；SHA-256 验证通过后，Python 预检与 ArcPy `XYTableToPoint` 共用这一副本。成功及可捕获失败路径均清理快照，并在导入后再核对原始源 CSV 未变化，避免预检和几何生成读取到不同版本。考虑 ArcGIS 对编码、分隔符和 `schema.ini` 的读取规则，首版仅接受已在 Pro 3.1.5 验证的无 BOM UTF-8 逗号 CSV，并拒绝有同名 `schema.ini` 节配置的源文件；其他格式先另存为标准格式。
- 独立审查补出快照校验异常清理、删除被锁定时不得掩盖处理结果、ArcGIS 文本表的编码/`schema.ini`/分隔符解析差异三项风险；均已加入失败注入或拒绝规则。测试先以缺失实现/规则失败，再修复；`tests/test_ingest.py` 18 项、全量测试61项通过，ArcGIS Pro Python 3.9.16 下 `py_compile` 通过。最终代码在 ArcGIS Pro 3.1.5 合成冒烟中以无 BOM UTF-8 逗号 CSV 导入RTK 2点，附录 G 54字段结构 PASS；模拟采样分别生成3点，混合样因窄模拟图斑不满足15 m 间距仍为 NEEDS_REVIEW，不代表实地采样。收据位于 `temp/arcgispro_smoke_rtk_snapshot_20261020_final/`。
- Add-In DAML 升至1.6；最终打包归档30项，包内28个 Python/工具箱/脚本文件均与源码字节一致。因本轮未改 C# 且当前机器未配置可用 .NET SDK，使用既有 DLL 打包（未声称重编译）；当前用户注册包及分发包 SHA-256 相同：`838DB1A50118E98218831DF482A8E7DFF450C1C8D2920231E06173937FECA1DB`。
- ArcGIS Pro 活动会话未重启，Ribbon 界面加载/按钮点击尚未复核。真实项目 RTK 的控制点、固定解状态、天线高、水平/垂直基准和转换参数仍须由正式测量资料确认；快照保证输入一致，不验证测量成果本身。

## 2026-10-20 采样输出事务回滚与汇交检查环境隔离（Add-In 1.7）

- 代码审计发现混合样/容重候选点在字段创建或插入失败时可能留下半成品要素类，导致重试被同名冲突阻断。两个采样工具现仅回滚本次创建的输出；已有同名数据不动；清理失败时把原始 ArcPy 错误和清理错误一起报告。新增失败注入验证：创建后失败可删、已有输出不删、清理异常保留原始错误。
- 独立审计发现 `delivery_package._check_gdb` 会污染 ArcPy 进程级 `env.workspace`；现用 `EnvManager` 将 workspace 限定在要素类发现阶段。成功和列举异常两条测试均确认原值恢复；ArcGIS Pro 3.1.5 合成工程实际检查后也读回 `delivery_check_workspace_restored=true`。
- 全量纯 Python 测试66项通过，核心接入、采样、汇交、工具箱和冒烟脚本在 Pro Python 3.9.16 下 `py_compile` 通过。ArcGIS Pro 3.1.5 合成冒烟中附录 G 54字段 PASS、混合样3点、容重样3点、RTK2点；小模拟图斑不满足15 m 间距仍为 NEEDS_REVIEW，明确不代表实地采样。结果位于 `temp/arcgispro_smoke_addin17_audit_20261020/`。
- RTK 建点限制仍为 Pro 3.1.5 实测的无 BOM UTF-8 逗号 CSV；其他编码/分隔符及匹配的 `schema.ini` 配置在建点前拒绝。正式 Ribbon 点击仍待重启 Pro 实际核验；样例数据不替代真实补充耕地项目资料和现场证据。
- Add-In DAML 1.7 最终包30项，28个 Python/工具箱/脚本均与源码逐字节一致；本轮未改 C# 且无可用 .NET SDK，使用既有 DLL。`RegisterAddIn.exe` 已注册；dist 与用户安装包 SHA-256 一致：`7D00DEC8D73757A4C3BAFF3F9EA7A4272780C9CB32821EB9111E927CD3A9EDC1`。

## 2026-10-20 县级报告格式优先级与版本冲突提示（Add-In 1.8）

- 交付检查器以前按路径排序选报告，真实模拟汇交目录中同名旧 DOC 排在更新 DOCX 前。现在按调用方提供的扩展名顺序选候选；县级初鉴报告优先 DOCX，仍保留 DOC 作为兼容候选。
- DOC 与 DOCX 同名并存时，检查结果写入 `NEEDS_REVIEW` 项及待人工核验清单，列出实际选中的 DOCX 和其他候选，提示核对内容并归档/更新旧稿；无扩展名同名文件也单独进入待核验。检查器不自动比较不同文件内容，也不擅自删除文件。
- 全量测试70项通过；ArcGIS Pro 3.1.5 对 `out/六类情景样例_v6_交付/01_项目类` 的只读实检无结构失败，县级报告选择读回为 `.docx`，旧 `.doc` 并存进入人工复核；总状态保持 `REVIEW_REQUIRED`，不会被误报为交付 PASS。自动化回归覆盖无扩展名 + DOC + DOCX 并存，以及市级 DOC/DOCX/PDF 候选顺序与大写扩展名。
- ArcGIS Pro Python 3.9.16 `py_compile` 通过；合成 ArcPy 冒烟读回附录G 54字段 PASS、采样/RTK链可运行、`delivery_check_workspace_restored=true`，小图斑按规则为 `NEEDS_REVIEW`。运行有 ArcGIS 中文代码页表缺失和无垂直坐标系警告，前者不影响本次结构/属性读回，后者对应 RTK Z 基准未核实标记；最终收据为 `temp/arcgispro_smoke_addin18_final_20261020/`。
- Add-In DAML 升至1.8，最终 `package.ps1 -SkipBuild` 生成30项安装包并注册当前用户。包内28个 Python/工具箱/脚本文件与源码逐字节一致；dist 与注册目录 SHA-256 相同：`6ED7A6239C7F24439ACAA694E7EC95ED19B7EBF2E5993A6B084A301DA4C43E0C`。C# 源码未变且本机无可用 .NET SDK，本次复用既有 DLL，未声称重编。新版 Ribbon 交互仍需重启 ArcGIS Pro 后实点核验。

## 2026-10-20 单期连续专题栅格评价单元摘要（Add-In 1.9）

- 根据荔浦公开包实测差距，新增“单期专题栅格评价单元摘要”：仅接受单波段、文件型、方形像元连续栅格；评价单元须为面、编号唯一非空，CRS 必须通过 WKID 或规范化自定义 WKT 完全匹配；处理窗口默认上限500万像元。工具不猜坐标转换、不投影/重采样、不输出地类面积或质量等级。
- 通过 ArcGIS Pro 3.1.5 Python 工具箱动态加载读到 `ThematicRasterSummary_scqa`，并直接调用 GP 工具成功生成 CSV/JSON。4×2 合成栅格独立预期为8个单元中心像元、7个有效、1个NoData、有效比例87.5%、最小1、均值33/7、中位数5、最大8；统计状态为 `NEEDS_FIELD_REVIEW`。输入栅格处理前后 SHA-256相同，临时数据清理无告警；因栅格仅覆盖评价单元包络一部分，工具正确警告需人工复核范围覆盖。
- 对荔浦真实 `Xiuren_SOM_10m_g_kg.tif` 与 `Xiuren.shp` 运行 CRS 配对，按预期阻断（球面自定义WKT与CGCS2000 WKID 4525不一致），没有写出统计成果。公开包内目前没有可确认与专题栅格同 CRS 的矢量评价单元，需有可靠转换资料/控制点后才能对地块做真实摘要；本次不宣称完成真实地块分区分析。
- 独立代码审查发现 ArcPy 全局 `outputCoordinateSystem` 可能影响临时要素副本。现已在 `CopyFeatures` 和 `PolygonToRaster` 显式设置栅格 CRS、清空地理转换，并在复制时限定输入范围；栅格化前读回临时副本 CRS 和要素数，两项不符即停止。ArcGIS Pro 3.1.5 实测将调用方输出 CRS 预设为 WKID 4326 后运行，仍读回8个单元像元、7个有效像元、均值33/7；调用方环境恢复，源影像哈希不变。随后从已刷新到1.9的 ArcGIS Pro `AssemblyCache` 路径直接导入随插件安装的 Python Toolbox 并运行同一工具，工具列表含 `ThematicRasterSummary_scqa`，统计读回正确、GP 状态为成功、调用方输出 CRS 仍为4326。烟雾收据：`temp/arcgispro_smoke_addin19_envfix2_20261020/`。
- 全量测试76项通过；ArcGIS Pro Python 3.9.16 对新增核心、工具箱和烟雾脚本 `py_compile` 通过。真实荔浦 SOM 栅格与 `Xiuren.shp` 再次按预期因 CRS 不一致而拒绝，且未生成摘要文件；该公开包仍不能证明存在同 CRS 评价单元。
- DAML升至1.9，安装包共32项，逐项与源码/既有DLL比对32项一致；分发包和当前用户注册包 SHA-256相同：`18AF14DDCD121430490F9491A9366379AC3556BACB2E021F5A27CFE80A17A70A`。本次仅增加 Python核心、工具箱和元数据，C#未变；机器无可用.NET SDK，复用既有DLL。该功能已在GP工具箱入口验证；现有 Ribbon 遥感按钮的直接交互仍需重启 ArcGIS Pro 后复核。

## 2026-10-20 真实数据包图像分类优化与 Add-In 1.11

- 对照官方荔浦数据包复跑插件 `audit_raster_package`。旧结果把11张无空间参考/世界文件的JPG插图与2幅无 CRS 的ASC栅格都列为 `BLOCKED_UNKNOWN_CRS`。现将无常见世界文件的JPG/PNG/BMP标为 `REVIEW_IMAGE_NO_GEOREFERENCE`；带世界文件但缺少坐标参考的图像仍保持 `BLOCKED_UNKNOWN_CRS`，避免误放行空间影像。
- 新增两个回归案例并确认旧行为失败，再修改识别逻辑。`pytest -q tests/test_raster_catalog.py`：6项通过。ArcGIS Pro 3.1.5/ArcPy 从源码运行官方数据包得到19个候选：11个图像待核配准、2幅未知CRS栅格阻断、6幅完整自定义WKT TIFF待配对核验。
- 从 Add-In 1.11 包解出 `Install/acceptance/raster_catalog.py`，用 Pro Python 3.9.16 对官方包重跑，仍得到相同的19/11/2/6结果。压缩包 CRC 检查通过，34个包条目均与对应源码或复用 DLL 字节一致；分发包和用户注册包 SHA-256 相同：`A6795C0B187C435CA6805B7D6D358C71CF42C6BEF1CCBB0C467D841141DEB1BD`。真实包扫描结果：`temp/realdata_lipu/package_audit_v1_11/遥感数据包预检_20261020_120703_c2416969_结果.json`。
- DAML升至1.11；仅改 Python 核心与测试，复用已有 C# DLL。ArcGIS Pro 会话仍在运行旧缓存；已注册新版包，但 Ribbon/按钮视觉加载与交互须重启后验证，本次不记为已完成。
- 额外数据检查显示：官方包有6个XLSX共11张工作表，包含标题行和多行/合并表头，核心目前仅在汇交清单把它们识别为附件，没有结构化导入。河流线几何检查报告30个“短线段”问题，需人工看图确认。130个土壤样点与10米SOM栅格的试探取值仅24个为数值、106个为NoData；因WGS84到自定义球面WKT没有可用转换和控制点，该结果只作风险提示，不作空间覆盖或数据质量结论。上述项未在1.11中自动修复。

## 2026-10-20 容重设计点空间质检与 Add-In 1.12

- 新增“容重平行样设计点空间质检”GP工具，按广西 v1.1 核查每评价单元3个平行样、平行样序号唯一且为1–3、样品编号为`RZ+评价单元编号`、每个候选点距对应质地构型调查点不超过2 m。平行点间距偏离2 m时列为人工复核；0.0001 m 仅用于消除双精度坐标计算尾差，不作为测量精度或规范放宽值。
- 混合样和容重样空间质检都拒绝带选择集或定义查询的 ArcGIS 图层，避免搜索游标只读取子集；两个质检器在输入要素为空时改为 `FAIL` 并列明空输入。
- 按 TDD 新增回归用例，先看到错样品编号、空输入、带选择/查询判例失败，再修复。`python -m pytest tests -q`：95项通过；ArcGIS Pro 3.1.5 Python 3.9.16 对核心与工具箱 `py_compile` 通过。
- ArcGIS Pro 3.1.5 合成 GDB 实际运行设计点生成和新 QA GP 工具；3点样例读回数量3/3、2米间距PASS、距调查点违规0、总体`NEEDS_REVIEW`（保留现场人工复核）。把一个点移至距调查点3 m 后读回`FAIL`；带选择集与定义查询的图层均按预期拒绝。输出收据：`temp/arcgispro_density_qa_smoke_20261020/`。
- Add-In DAML 升至1.12，包内35项；`Install/toolboxes/SupplementaryCultivatedLandAcceptance.pyt`包含`BulkDensitySamplingQA`。分发包 SHA-256=`438DB11CB8E627403B46932BF674BCC92DB12B76487B00C86CD50F98B4A22270`，与当前用户注册包读回一致。系统只有.NET 6运行时、没有.NET SDK；本轮只改Python核心/工具箱，复用了未改动的既有DLL。新工具在 GP 工具箱入口实测可运行；没有新增 Ribbon 按钮，且当前 Pro 活动会话未重启，因此新版功能区视觉状态仍未验证。

### 1.12 后续源码同步核对

- 复核发现上方记录的 1.12 安装包早于两项最终 Python 修正，包内 `sampling_qa.py` 漏掉空选择集识别和非整数重复序号拦截。现已重新打包并注册；35 个归档条目逐项与源码核对无差异，分发包与当前用户注册包 SHA-256 均为 `6E5301746B27563F1B7117CF7D6838BFC8DC013A44B8E9FD84DF1F353FAF4916`。
- 全量测试 `98 passed`。ArcGIS Pro 3.1.5 / Python 3.9.16 实际运行容重设计和 GP 质检：正常样例3点、间距PASS；调查点3 m违规样例为FAIL；带选择集和定义查询的图层均被拒绝。Pro 未重启，Ribbon 视觉加载仍未验证；C# DLL仍为复用既有构建件。
- 打包门禁现递归枚举 `acceptance/` 下的全部 Python 模块，要求每个源码相对路径都存在于安装包，并逐个比较 SHA-256；源码同步版和重新注册版均通过。当前机器没有 .NET SDK，所以只对未修改的 DLL 复用打包，没有重编 C#；当前没有运行中的 Pro 进程，界面交互尚未验证。

## 2026-10-20 容重设计点 Ribbon 入口与 Add-In 1.13

- 新增“容重样点质检”独立功能区按钮，打开工具箱中的 `BulkDensitySamplingQA`；之前只有 GP 工具入口，采样负责人难以从 Ribbon 直接找到该检查。
- 按钮契约测试覆盖 DAML 控件 ID、组引用、C# 命令类及其工具箱映射；完整测试集复跑 `99 passed`。本次只读复核安装包含35项、ZIP CRC通过，所有源码映射无缺项/哈希差异；容重质检工具与 Ribbon 控件/命令均存在。
- 分发包与当前用户注册包 SHA-256 同为 `116b6367f8f9d4caf9d8b64de3fb270b21d4548e26b508f4705f185689a65d84`。注册器已对当前用户注册1.13。C# DLL 由本机 Roslyn 4.14 回退流程构建，输出 DLL SHA-256 为 `C9D66157B5D1C116566A7EC1994AE5FC2EE713AB29CEB88A14E1BF04AD4BF6F3`；该流程使用当前机器 .NET 6/Windows Desktop 运行时程序集和 ArcGIS Pro 3.1 引用，未使用 .NET SDK reference packs / Esri MSBuild targets，属于构建可行性回退，不能替代目标桌面运行验证。
- 当前没有运行中的 ArcGIS Pro 进程。新版 Ribbon 加载、按钮可见性及点击启动链尚未在桌面会话确认；GP 工具核心此前已在 ArcGIS Pro 3.1.5 实跑正常/违规样例。不能将注册成功或静态包校验描述为 UI 端到端验收。

## 2026-10-20 Sentinel 在线等待控制复测（未发布）

- 为大范围 Sentinel COG 长时间停滞问题，在工作区试加 GDAL HTTP连接/请求超时、重试、Warp进度回调和每波段截止时间；回归测试通过后以 ArcGIS Pro 3.1.5 Python 对真实修仁 QDG/RDH 小样目录再次运行。在线过程停在 `before_nir` 远程数据集打开阶段，等待超过一分钟时进度回调没有获得执行机会；随后终止该受控探针进程，发现运行目录遗留8字节/约1KB未完成 TIFF。
- 这证明进程内 HTTP 配置和 Warp 回调不足以对打开阶段提供可靠的硬截止，也说明强制终止本进程不能运行 `finally` 清理。该改动未打包，Add-In 仍是1.13（分发包/已注册包 SHA-256 `116b6367f8f9d4caf9d8b64de3fb270b21d4548e26b508f4705f185689a65d84`）。
- 工作区全量单测 `102 passed`，ArcGIS Pro Python 3.9.16 `py_compile` 通过；这些结果不覆盖远程打开阶段的阻塞。后续需要把每个波段的GDAL读取放入独立工作进程，由父进程实施硬超时、终止并清理本次目录，再以真实在线 COG和故障注入复核。
- 试验的隔离输出目录仍保留于 `temp/sentinel_network_guard_probe_20261020/`：递归清理请求被系统策略拒绝，未改用其他删除方式。

## 2026-10-20 Sentinel 波段进程隔离与 Add-In 1.14

- 将单波段 GDAL Warp 放入 `acceptance/sentinel_warp_worker.py` 独立工作进程；父进程轮询进度、实施每波段截止时间，超时后终止子进程，整组失败时只清理本次唯一 run 目录。工作进程各自配置 GDAL HTTP连接/请求超时和有限重试，不污染 ArcGIS Pro宿主进程的全局配置。
- 故障注入测试确认45秒硬截止可杀停滞进程；调用级测试确认超时后 run 目录清空。ArcGIS Pro 3.1.5 Python实际启动工作进程并读到六个波段进度。使用 OSR 默认轴序的第一次探针产生全NoData结果，判定为探针 WKT问题而非有效成果；改用 ArcPy 的 EPSG:32649 WKT后，45秒上限成功截止并清理目录。300秒正式单波段重试遇到真实 S3 COG `TIFFReadEncodedTile()` 读取错误，工具向用户返回失败且清理本次目录；本轮没有六波段成功产物，公网资产读取稳定性仍是已知限制。
- 纯 Python 全量测试 `104 passed`；ArcGIS Pro 3.1.5 / Python 3.9.16 对核心、工作进程和 Toolbox `py_compile` 通过。Add-In DAML升至1.14，36项安装包 ZIP CRC通过，14个核心 Python文件逐一比对无哈希差异；新工作进程模块在包内。分发包与当前用户注册包 SHA-256均为 `B3BB76B3F6742A9C99915195A30F7C458D69AAA634E7BF478709FF60F4E116AB`。C# Ribbon DLL未修改，本次复用已登记构建件。ArcGIS Pro桌面当前未运行，Ribbon加载/按钮点击仍未验证。
- 第一次非隔离网络探针残留目录 `temp/sentinel_network_guard_probe_20261020/` 尚保留：系统策略拒绝递归删除请求，本次未绕过该拒绝。

## 2026-10-20 附录 C 注 6 几何规则修正与 Add-In 1.15

- 对照广西作业指导书 v1.1 附录 C 注 6（PDF 第 50 页、印刷页 44），修正多部件图斑判定：由仅需复核改为 `FAIL`，将对应 BSM、条款来源和细缝连接人工检查动作写入质检报告。细缝没有规范数值阈值，工具不自造宽度门槛。
- 按 TDD 先新增两项断言并确认旧实现 2 项失败，再修复；附录 C、项目插件合同和 Add-In 契约定向集合 `44 passed`。ArcGIS Pro 3.1.5 Python 用真实 ArcPy `Polygon.isMultipart` 做行为探针：单部件状态 `NEEDS_REVIEW`，多部件状态 `FAIL`，并验证总质检同步失败。
- 参考项目语言审查与公开荔浦真实数据实测已汇总至 `docs/插件与项目研究报告_2026-10-20.md`；新增 `skills/guangxi-cultivated-land-acceptance/SKILL.md` 工作流技能。技能去重创建前检查通过，描述前置检查 10 个目标全达标。
- Add-In DAML 升至 1.15，36 项安装包源码哈希门禁通过；用户级注册包和 dist SHA-256 均为 `EFC59CF8F6C98637FADB65D41C3D2F085BA8E18B8D7BFC71C6ED2A047D785BBE`。复用未修改的 C# DLL。ArcGIS Pro 桌面未运行，Ribbon 加载和按钮交互仍待 UI 端到端验证。
- 远程 Sentinel 诊断维持未解决状态：失败 COG tile 的 HTTP Range 字节完整且可由 `tifffile` 解码，但 Pro 3.1.5 / GDAL 3.4.0e 的远程 tile 读取失败；`GDAL_HTTP_MULTIRANGE=SERIAL` 对照未修复。本次不发布“在线大范围读取成功”结论。

## 2026-10-20 汇交规则、采样过滤与 Sentinel 诊断留存（Add-In 1.16）

- 按广西作业指导书 v1.1 第10.3及附录 I 修正汇交检查：县级、市级均要求《补充耕地质量鉴定意见表.pdf》；市级报告/意见文件存在不再被误认为满足此独立表单要求。先添加市级缺表失败用例，再实现通用检查。
- 混合样和容重设计生成器现在拒绝选择集（包括空选择集）及定义查询，防止 `SearchCursor` 静默只遍历部分要素。过滤判定复用到设计生成与空间质检；新增生成器契约用例。
- Sentinel 工作进程失败后，保留最多256 KiB的脱敏日志尾部和最小 JSON 诊断记录于临时运行目录外；URL签名/令牌、Authorization值和 URL userinfo 会脱敏。成功时仍清理本轮日志。该变化提供诊断线索，不等于解决 GDAL S3 tile 解码失败。
- ArcGIS Pro 3.1.5 / Python 3.9.16 全量测试：`112 passed`。Add-In DAML 版本升至1.16；打包 36 项，内含更新后的 Sentinel、采样和汇交模块；`package.ps1 -SkipBuild` 复用未改动的 C# DLL。分发包与用户注册包 SHA-256 均为 `1CC211DEC439CF8E0BBED5F754573B0ED0C9D6608C9E6602FF2CAB3884B8C9A4`。随后启动 Pro 尝试核验1.16界面，但窗口采集连续两次超时；当前会话是否加载1.16、GP 按钮点击链均待桌面交互复核。
- 荔浦数据归档读回 SHA-256=`19CB2E34AABD1D98670ACE91F849C356C33A611D507D0F540CA1E879F5405CA3`，与来源清单一致；101个文件、解包总量147,292,122字节。来源 DOI `10.3974/geodb.2023.06.02.V1`，研究用途须按发布页引用，不把它作为补充耕地验收成果或重新分发原始包。

## 2026-10-20 WKID/建库事务、机构独立性核对与 Add-In 1.17

- 新增共享 `acceptance.crs` WKID 解析，拒绝布尔、小数、负数、零和浮点整数，避免 `int()` 隐式截断；应用到项目初始化、矢量/JSON/CAD/RTK导入和 FileGDB 建库。坐标系选择仍须来自项目测量资料，插件不猜带号。
- JSON 几何导入现在按类型统计源数据、核对ArcPy导入和投影后的数量，以唯一临时名完成全部转换后再发布；任一阶段失败会清理本次输出。FileGDB 建库失败会回滚本次创建的库和清单，且拒绝覆盖已存在清单。
- 按作业指导书 v1.1 §6.3.1，市级汇交核对县、市两阶段检测机构登记标识；同一机构标识判 `FAIL`。某阶段没有委托检测时要求项目内有书面依据，标记为有证据的“不适用比较”，不宣称存在规范豁免。机构差异检查不替代 CMA 证书/能力范围审核。
- 新增 WKID拒绝、JSON/普通矢量导入数量核对与分阶段失败回滚、FileGDB部分创建失败回滚、汇交检测机构同一/不同及未检测证据、容重设计输入空选择/定义查询等断言。ArcGIS Pro 3.1.5 / Python 3.9.16 全套测试 `139 passed`。
- Add-In DAML 版本升至1.17；重新打包时复用未改动的 C# DLL，安装包共37项并包含新增 `acceptance/crs.py`，打包脚本逐文件比对核心源码哈希。分发包和用户注册包 SHA-256 均为 `4119A607B202C391B4502BA296EC09951DAF54823D1AAE7C69A9C83394104B1A`。ArcGIS Pro 1.17界面加载和按钮端到端仍未由本轮桌面截图验证。

## 2026-10-20 真实数据接入与汇交审计修正（Add-In 1.18）

- 真实数据：在 ArcGIS Pro 3.1.5 / Python 3.9.16 对荔浦案例包完整跑栅格预检，发现19项、无读取错误：11张普通图片标记无配准信息待核，2幅ASC因坐标系未知而阻断，6幅GeoTIFF识别到自定义WKT但无WKID。输出清单位于 `temp/lipu_raster_catalog_495f19b1/`。数据包本身不是验收项目成果。
- 矢量接入：将 ArcGIS Pro `outputCoordinateSystem` 故意设置为 EPSG:4326，再导入真实荔浦修仁镇边界 Shapefile；输出读回 WKID 4525、1/1要素，保留源坐标系。相关输出在 `temp/real_data_lipu_20261020/crs_env_probe_b003fc97.gdb`。
- 代码更新：矢量输入拒绝活动选择集/定义查询；转换调用显式使用目标空间参考并读回核对；JSON和Shapefile源文件在导入前后核验SHA-256，Shapefile包含同名配套文件；清理失败时保留正式输出回滚错误；FileGDB创建异常时检查并回滚物理残留目录。汇交检查增加附录C明确值域并调用附录C自动质检；机构编号不同时改为 `NEEDS_REVIEW`，要求材料复核法人身份。
- ArcGIS Pro 3.1.5 / Python 3.9.16 全量测试：`144 passed`。Add-In DAML版本升至1.18；`.esriAddInX` 37项；分发包与当前用户注册包 SHA-256 均为 `A4D312E392941822142599D47EB539C7C26E0F6BE63BE7A09F165937500433F8`。复用了未改动的C# DLL。
- 限制：Pro桌面UI未在本轮可用窗口接口中暴露，故1.18 Ribbon加载与每个按钮的实际点击仍待桌面会话确认。真实业务CAD/RTK、现场采样、CMA检测及签章文件尚未取得。

## 2026-10-20 采样评价单元空间校验与 Add-In 1.19

- 广西 v1.1 §6.1.4、§6.2.4.3(1)、附录E：混合样设计现在必须提供评价单元面；生成器核对声明单元内的图斑归属，质检器要求图斑面、评价单元面和设计点坐标系一致，验证点位同时落入关联图斑和对应评价单元，并要求 `SAMPLE_ID` 与评价单元编号完全一致。
- 混合样空间计数仅纳入有效关联点。无效属性、未关联图斑、图斑外或评价单元外点分别报告，不会继续增加单元/图斑点数。小于一般15米间距的点对附有待签认例外记录字段；未填现场依据、覆盖/避边结论和审核人之前仍为 `NEEDS_REVIEW`。
- 容重生成对缺少质地构型调查点的评价单元写出 `NEEDS_PROFILE_POINT`，0个候选；QA对该评价单元计0/3并判 `FAIL`。
- ArcGIS Pro 3.1.5 / Python 3.9.16 全量测试 `150 passed`；`py_compile` 覆盖采样核心、ArcPy Toolbox 与烟雾脚本。标准全流程合成 GDB 输出 `NEEDS_REVIEW`，字段读回含 `SAMPLE_ID`/`POINT_ID`/0–20cm和容重 `RZ+UNIT_ID`/REP_NO/100cm³；空间QA确认单元及图斑点数、15m候选违规和人工状态。独立两单元 GDB 探针证实无调查点单元在生成端 `NEEDS_PROFILE_POINT`、QA端0/3 `FAIL`。
- Add-In DAML升至1.19；`package.ps1 -SkipBuild` 复用未改动 C# DLL。安装包37项；打包脚本逐文件核对 Python 核心 SHA-256，并确认工具箱来源文件与参数解析；注册器后用户目录读回包 SHA-256 与分发包一致：`91AA3774BFC985635420DED221E15B59F4622CF3FD35C1C2B957BB9AB96CB96F`。
- ArcGIS Pro桌面未运行，本轮未做新版Ribbon加载、GP按钮点击或对话框交互验收。公开荔浦数据仍只用于真实GIS格式/CRS/遥感接入研究，不代表补充耕地验收业务项目；正式现场、CAD/RTK和CMA材料仍未取得。

## 2026-10-20 Add-In 1.20 源码与安装包检查

- 采样空间质检改为只把唯一点编号、`SAMPLE_ID=评价单元编号`、空间上位于关联图斑和评价单元内的点计入单元/图斑覆盖数。质检JSON同时输出原始点数、空间关联点数和完全有效点数。
- 新增可选的小图斑间距审核JSON输入。审核记录按评价单元、两个点编号及各自图斑编号精确绑定，必须具有现场依据、全斑覆盖与避边确认、审核人、ISO日期及证据引用；未匹配记录判失败。记录完整仅标为`DOCUMENTED_PENDING_JUDGMENT`，总状态仍不自动转为通过。
- 独立复核发现审核JSON虽标注“可选”，但工具箱构造参数仍沿用`_p`的必填默认值。现显式设置`required="Optional"`，并增加工具箱契约断言检查可选属性与参数索引映射。
- 当前Windows系统Python 3.12：采样QA与工具箱契约定向30项通过；全项目154项通过。ArcGIS Pro Python 3.9.16本轮不可用，因此未重跑 Pro 全量测试或真实 ArcPy工具箱执行。
- `package.ps1 -SkipBuild` 复用了未改动 DLL，重新生成37项包并通过脚本逐文件核心哈希校验。Add-In包内读回应包含版本1.20、可选审核JSON参数和当前`acceptance/sampling_qa.py`源码；发布包SHA-256=`48EBA61DCFF4053978C8814E8FC25B87E9194EB8D02E0A05215A47A0C2ACCA53`。Pro Ribbon/GP实机交互仍未验证。

## 2026-10-20 合成演示报告

- 新增县级初鉴报告合成演示样例，按附录L和第10章串接数据库、图件、文字、附件以及证据索引；13个表格，所有面积、图斑、点位和检测值均明确标为合成数据。没有填写法定等级、真实现场结论、签名或验收意见。
- Python DOCX结构读回通过（76段、13表、关键章节与合成声明均存在）。捆绑 LibreOffice 不可用，`render_docx.py` 报 `soffice.exe was not found on PATH`；改用本地 `docx-preview` 0.4.1浏览器查看器检查7页，页框均为816×1056 px，发现并修正第2章超页，补入静态目录页码和Word自动页码域。该查看器不渲染Word页码域，因此预览页脚未显示页码；文档已设打开时更新域，并提示按F9刷新。浏览器HTML视觉检查通过，但不等同Word/ArcGIS文档引擎分页，Word原生最终分页仍未核验。
- 未将包安装或注册到用户环境；ArcGIS Pro Ribbon可视加载、GP参数呈现与按钮交互仍待桌面会话验证。模板Word的分页检查另见项目报告记录。

## 2026-10-25 地图出图实战与 Add-In 1.53

- 基于用户提供的 9 个地块和 27 个采样点，使用 ArcGIS Pro 3.1 ArcPy 重出 14 张图：总览 1 张（1:250,000）、地理位置图 4 张（1:20,000）、采样图 9 张（1:2,000）。总览比例和分区比例均未改变。
- 四边经纬度由 CGCS2000 高斯—克吕格坐标转换得到；总览和分区图按空间邻近关系显示全部单元编号，近邻标签升序分组。14 张图的经纬度/单元编号检查通过，JPG/PNG/PDF 文件读回无硬失败。
- 修正模板比例尺文字实际约 27 pt 的残留字体格式，改用统一正文样式；14 张 PDF 实际读回比例尺文字均为约 10.74 pt。中文为 Microsoft YaHei，英数字为 Arial。
- Add-In DAML 升至 1.53；包 60 项，复用未改动的 C# Release DLL，Python 核心逐文件 SHA-256 与源码一致。发行包和当前用户安装副本 SHA-256 均为 `C925B35D790C0B291E28D0A3E37FECA0365595DC7A0A2E447070C0B0E64DF165`。
- 实战出图整体状态为 `NEEDS_REVIEW`：天地图时相、原生 GSD、位置精度、图面地物来源和编制单位仍需项目资料核验。ArcGIS Pro 当前关闭；注册/安装文件哈希一致，但 Ribbon 实际加载和按钮交互需启动 Pro 后复核。本轮未跑插件测试套件。

## 2026-10-25 地图导出热修复与 Add-In 1.54

- 按用户意见把上边经度标签移入图廓、左侧纬度标签移离页面边缘；图面删除影像来源、时相和输出像元三项说明，元数据仍留在图件清单与 manifest。
- 基于 ArcGIS Pro 3.1 ArcPy 重出 14 张图；比例为总览 1:250,000、4 张地理位置图 1:10,000、9 张采样图 1:2,000，分区覆盖全部 9 个地块；位置图按用户纠正调整为 1:10,000，总览作为全区补充页。
- PDF 读回确认 14 张均为 A4 横版单页；图廓坐标检查通过，地理位置图编号排序检查通过，比例尺文字字号均为 10.74 pt。PDF 字体已嵌入；页内不再出现影像来源、时相、输出像元文字。
- Add-In DAML 升至 1.54；发行包 60 项，复用未改动的 C# Release DLL。发行包及当前用户安装副本 SHA-256 均为 `5A59B001B89150ECC85CBFEBE0ABCB839124D7391FEC34571520A0EA6E7B9875`。
- ArcGIS Pro 未运行，Ribbon 加载和按钮交互待启动后核验。全套图件仍为 `NEEDS_REVIEW`：影像时相、原生 GSD、位置精度及部分地物/制图单位资料未获项目证明；自动图框/段落检查也保留复核项。

## 2026-10-25 源编号、规则格心与 Add-In 1.56

- 当前交付为 `out/courseware_atlas_all_parcels_20261025/数据对齐成果`。9个地块使用原始DIKUAI_NO，正式PJDYBH保留15位文本字段且为空；不再把临时PJDY000X写成正式编号。20×20米同原点完整正方格，保留真实格心和IN_UNIT标记；27点均落在对应源地块及真实格心，最近离边6.011米、最小点间距19.997米。
- 14张A4横版图重出并读回：1张全区索引1:250000、4张位置图1:10000、9张采样图1:2000。每页12个经纬度注记位于内外图廓间，实际9pt；源编号、实际比例尺、完整公司名称、当天日期和1985基准文字检查通过。菱形点、浅灰细网格、真实中心点、线段比例尺、图例及底部信息分栏已看图核对。保留卫星影像，删除影像RGB图例；位置底图改用更细一级的z17缓存。
- 编制单位按用户提供的“某某检测有限公司”；导出日期强制采用当天并纳入缓存键。项目参数写入源目录与当前交付快照；参数合并保留既有单位及扩展元数据，GDB内要素类可找到GDB旁的参数文件。
- 新增DEM坡度与海拔工具：单波段高程、明确高程单位、局部投影与Horn坡度、点位双线性取样和地块像元统计；缺测返回空值，保留源图层，记录分辨率/覆盖率/方法与基准来源。用户没有DEM；1985仅记录为指定待核实基准，未进行垂直基准转换，未生成本项目真实高程/坡度。
- 新增点位经纬度表入口：标准点图层转换至CGCS2000（EPSG:4490），输出十进制度/度分秒、原始编号及可选DEM字段。实际27点CSV及Excel逐行读回一致，编号为文本，Excel冻结窗格C7；正式评价单元、海拔、坡度均为空。
- 定向验证：网格采样12项；图件样式14项、出图契约18项；项目参数与密度6项、Add-In契约16项；ArcGIS Pro Python独立运行DEM与点位调整13项通过。真实14页PDF/JPG/PNG结构及编号检查通过。DEM真实项目验证因缺数据尚未进行。
- C#使用Pro 3.1 SDK重新编译，DLL版本1.56.0.0；DAML1.56，17个按钮/唯一KeyTip。打包192项790746字节，逐条源码哈希核对；注册后安装副本SHA-256与发行包一致：`5C52BE08A8DF2CCF19F931F375285CC0BAC5A3DB3C88B6E20CC95936C18F87C6`。当前Pro进程未被关闭，功能区加载及新按钮实际点击需重启后确认。
- Pro 3.1重新打开含本批动态注记的保存布局会崩溃；已改为地图工程与独立导出图件交付。新进程重新打开当前APRX，2个地图均无断开的数据源，布局数0。旧试样保留历史标记，当前成果以数据对齐目录为准。


## 2026-10-26 人工采样组与AI体检（1.57）

- 项目参数sampling_groups支持用户确认的多地块采样设计组，保留原始面和正式编号；已知项目/因素冲突拒绝，0/False不作空值。配置可绑定源项目，避免换批同号误用。每组最低3点且每块至少1点，加密取大；面积与集中性等疑点保持待复核。
- 分组新增、修改、移除使data_prepared及下游失效；从已准备图层开新任务时，仅在新工作副本重算旧SAMP_GRP。Toolbox用catalogPath查找参数文件，支持地图图层名。
- TDD：新规则7例、失效3例、0/False两例分别红转绿；独立审查发现并修复续跑、自定义因素、最低点数与零值边界，复核无重要遗留。最终系统476 passed/14 skipped；ArcPy4项新用例通过（101.52秒），末轮2项工作流通过（101.89秒，含从已准备层重跑和移除组续跑）。解压最终包后真实ImportToolbox并以图层名调用检查成功，未靠直接类方法替代动态GP入口。
- 本批9面、7组、21设计点；22=1、111=2，30=1、31=2，其余各3。原几何WKB与源编号保持一致；正式PJDYBH仍空。22+111约34.84亩/2米；30+31约82.77亩/206米，后者面积及集中性继续列待核，不宣称正式单元合格。
- 另存“out/courseware_atlas_all_parcels_20261025/人工合组成果_20261026”：GDB、四套SHP、数值经纬度/文本编号Excel、CSV、14图42文件。4张图标题明确组共3点；9张采样图实际查询点数与数据库逐项一致。原地块完整质检在编号、几何、重叠、指定必填和椭球面积口径内未发现异常，不能替代验收。
- 1.57最终包SupplementaryCultivatedLandAcceptance_1.57_20261026_191713.esriAddInX，61条目，SHA256=B1D0DEC585946CCD694B5C0B4AFDD817534065439906CD9D705E40A1CCAF2EFD。复用既有C# DLL；包内Python与Toolbox逐文件对应，注册副本字节一致。此前测试实例加载的同一DLL哈希与最终包一致；最后Python更新注册后的Ribbon物理点击未验证，未改Windows策略。
- 配套AI桥接0.6：99项非现场、5项实机通过；基础体检返回问题OID/where，真实异常选择与缩放通过。与本插件完整地块质检检查范围不同；DSH需重新连接刷新工具，未改变账户或权限。
- 旧27点成果保留历史，current_delivery已指向21点成果。影像时相/原始GSD/位置精度、DEM、正式单元与现场代表性仍待核；不代签、不补造资料，不宣称移机包就绪。源码未提交或推送。作用域沿用写前内容基线，收据见pro_ai_bridge/docs/pro-v06-scope-receipt.json。
