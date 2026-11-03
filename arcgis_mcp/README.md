# arcgis-pro MCP（离线 arcpy）

把 ArcGIS Pro 的 Python/arcpy 包成 MCP 工具，供 Claude Code 等本机 agent 调用。
**它不连接 Pro 的实时界面，也不点按钮**；它跑的是同一套 `acceptance` 业务代码，
用于批量运行、读回报告、检查图层/字段/CRS、导出布局和跑测试。

## 工具

| 工具 | 读写 | 说明 |
|---|---|---|
| `workspace_status` | 只读 | 检查 Pro Python、worker、模板、配置、vendor、最新安装包 |
| `pro_info` | 只读 | Pro/arcpy/Python 版本 |
| `describe_dataset` | 只读 | 数据集类型、CRS、范围、要素数、字段；栅格像元/波段 |
| `list_project` | 只读 | .aprx 的地图、图层、布局、定义查询 |
| `run_oneclick` | 写 | 跑一键流程；options 白名单；需 `allow_write=True` |
| `read_run` | 只读 | 读 task_state、报告摘要、检查清单、外业表格清单 |
| `export_layout` | 写 | 导出布局 PDF/PNG/JPG；需 `allow_write=True` |
| `run_tests` | 写 | Pro Python 下跑 `tests/`；需 `allow_write=True` |

## 注册

系统 Python 3.12 已装 `mcp`；Pro Python 只负责执行 arcpy worker。

```bash
claude mcp add arcgis-pro -- "C:/Users/<user>/AppData/Local/Programs/Python/Python312/python.exe" "D:/work/arcgis_mcp/server.py"
```

或写入项目 `.mcp.json`：

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:/Users/<user>/AppData/Local/Programs/Python/Python312/python.exe",
      "args": ["D:/work/arcgis_mcp/server.py"]
    }
  }
}
```

改完配置需重启 agent/会话才能加载。

## 路径范围

- `ARCGIS_MCP_READ_ROOTS` 设置输入数据和工程的只读目录，多个目录用分号分隔。默认只包含工具包目录；项目数据目录用环境变量显式加入。
- `ARCGIS_MCP_WRITE_ROOTS` 设置允许生成成果的目录，多个目录用分号分隔。默认仅为 `工具包/out`。
- 写入目录同时允许只读查看，便于 `read_run` 读取刚生成的成果；只读目录不会因此获得写权限。
- 旧配置 `ARCGIS_MCP_ROOTS` 继续作为只读目录使用，不再扩大写入范围。要把其他目录作为输出位置，显式加入 `ARCGIS_MCP_WRITE_ROOTS`。
- `workspace_status` 会分别返回 `read_roots` 与 `write_roots`。本机配置在系统环境变量中设置并重启 MCP 会话后生效。

## 典型用法

```text
workspace_status()
pro_info()
describe_dataset("D:/data/示例区2025年度第一批耕地恢复.shp")
run_oneclick(source=..., out_dir=".../mcp_run_01", options={"make_maps": False, "meta": {...}}, allow_write=True)
read_run(".../mcp_run_01")
```

## 安全边界

- stdio 传输，不开端口。
- worker 只接受白名单 `action`；没有 `eval/exec`、没有任意 Python 入口。
- 输入和工程路径必须落在只读范围，输出路径必须落在单独配置的可写范围；默认不授权整个工作区上级目录或 `%TEMP%`。
- `run_oneclick`、`export_layout`、`run_tests` 必须显式 `allow_write=True`。
- 不接受天地图 key；密钥仍由 ArcGIS Pro 插件本机加密保存。
- 每次调用写 `logs/arcgis_mcp.jsonl`（动作/状态/耗时，不记密钥）。
- 仍不能替代：现场采样、检测、专家审核、签字、CMA、照片真实性；也不能证明侧栏按钮绑定。

## 当前 Pro 实时地图桥接（候选 1.41）

`live_server.py` 与 Pro Add-In 1.41 配合，可读取当前活动地图、列出图层、按编号精确查询，并在明确确认且唯一命中时选中/缩放地块。缩放确认同时在 Python 工具和 Add-In 协议端检查；命名管道连接、请求读取、响应读取和写回均受时限/长度约束，不开放网络端口，不执行任意代码，也不编辑要素。

实时桥接需先安装并重启 Pro 加载候选 1.41，再将 `live_server.py` 注册为 stdio MCP server。候选方案、命令及逐项验收步骤见 [`docs/plans/2026-10-24-live-pro-mcp-bridge.md`](../docs/plans/2026-10-24-live-pro-mcp-bridge.md)。旧 `server.py` 连接离线 arcpy worker；worker 明确返回 `FAIL` 或非零退出码时会抛出 MCP 工具错误，避免失败被识别成普通成功。

MCP 侧的 20 秒客户端截止时间可以结束无响应调用；ArcGIS Pro 3.1 的 QueuedTask 工作本身没有在当前实现中强制取消。若 Pro 正在执行的地图任务卡住，服务端实例可能要等 Pro 任务结束后才能继续接收请求；仍需在现场注入卡顿和断连验证。

## 故障排查

- MCP 调用 worker 超时：server 已加 `stdin=DEVNULL` + `CREATE_NO_WINDOW`，避免无控制台上下文挂起；若仍超时，先手动跑 worker 复现，再查 Pro 许可证/杀软。
- 输入路径被拒：把其父目录加入 `ARCGIS_MCP_READ_ROOTS`（分号分隔）。输出路径被拒：只把成果目录加入 `ARCGIS_MCP_WRITE_ROOTS`，不要放宽到整个磁盘。
- `mcp` 包未安装：系统 Python 执行 `pip install "mcp<2"`；Pro Python 不需要装 mcp。

## 验证

```bash
# 系统 Python：MCP 工具函数与路径校验
python -m pytest tests/test_mcp_server.py -q

# Pro Python：worker 冒烟（只读）
"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -I arcgis_mcp/worker.py request.json result.json
```
