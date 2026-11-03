# ArcGIS Pro 实时地图 MCP 桥接（当前候选版 1.49）

## 目标

让 MCP 读取当前 ArcGIS Pro 活动地图并按图斑编号定位，使用户可直接看到 Pro 选中并缩放到目标地块。原 `arcgis_mcp/server.py` 仍是离线 arcpy worker；实时功能由 Pro 内加载的 Add-In 提供命名管道服务，MCP stdio sidecar 只做协议转发。

## 暴露操作

| MCP 工具 | 行为 | 风险控制 |
|---|---|---|
| `live_status` | 返回活动地图名、进程号和图层数 | 只读 |
| `live_list_layers` | 返回当前地图图层名称、URI、类型和可见性 | 只读 |
| `live_search_parcel` | 按编号字段精确检索，重复号列出候选 | 只读，不改地图状态 |
| `live_zoom_to_parcel` | 唯一命中后选中并缩放到图斑 | 要求 `confirm=true`；多结果拒绝缩放 |

查询只允许真实图层字段、字符串/整型编号和受限长度；响应不返回要素属性或几何，不提供任意代码、数据编辑、删除或文件系统工具。命名管道只允许当前 Windows 用户连接。MCP sidecar 使用 stdio，不监听网络端口。

## 使用前条件

1. 在 ArcGIS Pro 3.1 中安装包含实时桥接的 Add-In（功能最低版本为 1.36；当前候选为 1.49），并重启 Pro 让插件模块加载实时桥接。
2. MCP 运行环境需有 `mcp<2`。将 `arcgis_mcp/live_server.py` 作为 stdio MCP server 注册到本机 agent。
3. 当前会话的 MCP 运行器不支持从工作区热加载新 server；候选源代码和安装包可测试，但本会话不能因此宣称已连上当前 Pro。

Claude Code 注册示例（以安装有 `mcp` 包的 Python 3.12 为例）：

```powershell
claude mcp add arcgis-pro-live -- "C:/Users/<user>/AppData/Local/Programs/Python/Python312/python.exe" "D:/work/arcgis_mcp/live_server.py"
```

## 验收顺序

1. `live_status` 返回活动地图名、进程号；没有活动地图时返回 `NEEDS_MAP`。
2. `live_list_layers` 与 Pro 内容窗格的图层数及名称一致。
3. `live_search_parcel` 对已知编号返回 OID 和图层 URI；错误编号返回 `NOT_FOUND`；重号返回全部候选。
4. `live_zoom_to_parcel` 在 `confirm=true` 且唯一命中时，Pro 中可见目标被选中并缩放；不确认、重号、地图切换时不得缩放。
5. 通过另一会话 MCP server 做联机验收后，保存截图、调用记录及结论；在此之前仍是“候选代码已构建”，不把静态/合成测试记为 Pro 现场通过。

## 本机只读回核（2026-10-24）

- ArcGIS Pro 窗口 MyProject1 仍在运行；当前 Windows 用户的 Add-In 安装目录中实际文件为 SupplementaryCultivatedLandAcceptance_1.33_20261024_084307.esriAddInX。Pro 界面可见采样助手 GP 参数窗格。
- 调用本地实时 sidecar 的 live_status 返回 Win32 2，当前未启动命名管道；1.33 早于实时 MCP 桥接功能最低版本 1.36。不能据此宣称 MCP 已连上 Pro。
- 当前 1.49 测试包尚未安装到正在运行的 Pro。用户计划安排现场验收，因此保留当前项目会话，不在本轮安装或重启 Pro。
- 从 1.49 测试 ZIP 启动的 sidecar 已完成 MCP stdio `initialize` 和 `tools/list`，读回四个实时工具；`live_status` 返回 Win32 2 的预期诊断。该证据不代表命名管道已连通。
- Windows 窗口截图通道两次超时，未保存界面截图；当前状态证据来自窗口可访问性树、已安装包文件名及实时桥接错误码。
