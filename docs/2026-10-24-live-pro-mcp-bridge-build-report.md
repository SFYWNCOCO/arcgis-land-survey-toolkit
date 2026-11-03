# 实时 Pro MCP 桥接 1.36 候选构建记录

日期：2026-10-24。范围：把“按编号搜索/定位”扩展为 MCP 可见的本机 ArcGIS Pro 实时接口，仍处于阶段3候选验收，不推进阶段4。

## 已完成

- Add-In 在 Pro 内开启受限命名管道服务；MCP sidecar 使用 stdio 传递结构化 JSON。
- 提供 `live_status`、`live_list_layers`、`live_search_parcel`、`live_zoom_to_parcel` 四个工具。搜索精确匹配；多条命中不会自动缩放；缩放须 `confirm=true` 且仅唯一命中时执行。
- MCP 契约测试先红后绿；全量 `tests/`：382 passed、8 skipped。
- Pro SDK Roslyn 回退构建成功；Add-In 包 78 项，ZIP CRC 全部通过；MCP server 通过 stdio 初始化和 `tools/list` 握手，读回四个工具名。
- 汇总测试包包含 Add-In、`live_server.py`、操作说明和组件哈希。

## 产物与校验值

- 完整测试包：`addin/_archive/20261024_superseded_builds/采样助手_1.36_实时MCP操作候选_20261024.zip`
- Add-In：`addin/_archive/20261024_superseded_builds/SupplementaryCultivatedLandAcceptance_1.36_20261024_163023.esriAddInX`
- Add-In SHA-256：`1956729E668D17F917012A37CBAA160C30C555A8BB6E1CDB2394509084485AB1`
- 测试包 SHA-256：`0370A6688BDFECC6E4D413A926F22B1B5668ECEAD622B9669B71895D48262BD3`
- Add-In DLL SHA-256：`CAB243FE5BD9E9D9126079C361B66C1E93CE1DA9BB8D4AC1639527D9707DCD76`

## 尚未通过的现场项

在 1.36 候选构建阶段，Pro 尚未重启加载该版本；MCP 握手只证明工具注册和协议清单有效，未证明管道已连通当前 Pro，也未执行当前地图查询、选中或缩放。因此当时的阶段3未通过。当前实机回核见下文。

可查看此前的39号图斑验收预览：

- `D:/work/高标准/验收预览_天地图_39号图斑_20261024/map/地理位置图.png`
- `D:/work/高标准/验收预览_天地图_39号图斑_20261024/map/采样点位布置图.png`

两张均为预览图。天地图取图覆盖与像元间距已查，但原始GSD、位置精度、时相和使用条件未核；地貌、土种、补充来源资料缺失，故不能作为正式验收通过证明。

## 2026-10-24 后续实机回核

- 当前 Pro 会话仍运行于 MyProject1，实际安装的 Add-In 文件为 1.33；本地 live_status 查询返回 Win32 2，确认实时桥接尚未启动。1.33 早于实时桥接最低版本 1.36。
- 1.47 安装包和测试包未在当前 Pro 安装或重启；由用户安排现场验收后再执行现场 MCP 联机检查。
- 本轮修正 sidecar 的连接失败提示，并将 test_live_mcp_server.py 纳入完整 MCP 测试包；Python 3.12.10 与 Pro Python 3.9.16 定向回归各 29 项通过。自动测试未覆盖真实 Pro 管道连接，阶段3仍未通过。

## 2026-10-24 1.48 候选包更新

- 修复全图层模式下显式选择自定义编号字段时，字段缺失图层静默回退到默认字段导致误搜的问题；定位状态现在区分“未包含所选字段”“缺少可识别的业务编号字段”和“缺少对象ID字段”。
- Python 3.12.10 全量回归 450 通过、8 跳过；定位行为在 Python 3.12.10 与 ArcGIS Pro Python 3.9.16 下各 4 项测试通过，覆盖 27 项 C# 行为断言。ArcGIS Pro 3.1.5 Roslyn Add-In 构建通过，安装包 60 项。
- 当前 Add-In 1.48 与便携 MCP 包均未安装或重启当前 Pro；本机仍为 1.33，真实管道联机和桌面按钮验收仍待现场操作。

## 2026-10-25 1.49 侧栏主操作可见性候选

- 将确认导出的主按钮、运行状态与任务操作区固定在 DockPane 底部；步骤表单仍可滚动，详细日志折叠显示。新增界面结构契约先红后绿，源代码在 Roslyn Add-In 中重新构建。
- Python 3.12.10 全量回归 451 通过、8 跳过；ArcGIS Pro Python 3.9.16 下 Add-In 契约15项、地块定位4项通过。真实 Pro 侧栏布局尚未运行验收。
- 当前 Add-In 1.49 与便携 MCP 包尚未安装到正在运行的 Pro；当前 Add-In 仍为1.33，命名管道联机与功能区点击保持未验收。

## 2026-10-25 1.49 MCP 包内 stdio 握手

- 从完整 MCP 测试 ZIP 临时解出 `MCPRuntime/arcgis_mcp/live_server.py` 并以 stdio 启动；MCP `initialize` 成功，协议版本 `2024-11-05`，`tools/list` 返回 `live_status`、`live_list_layers`、`live_search_parcel`、`live_zoom_to_parcel`。
- 调用 `live_status` 时，sidecar 返回可读的 Win32 2 错误与 Add-In 安装/重启提示。当前 Pro 进程仍是旧安装包1.33，因此该错误是本机状态回执；实际管道连通和 Pro 地图操作仍未验收。
