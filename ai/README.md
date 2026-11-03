# 补充耕地验收工作台的 AI / MCP 组件

本组件已合入 **补充耕地验收工作台**（当前版本以 `../addin/dist/build-receipt.json` 为准），不再分发独立原生插件。唯一安装包由 `../addin/package.py` 生成，使用验收插件原GUID，包含一个 `AcceptanceAddin.dll` 和统一工作台。当前使用说明见 [统一工作台](../docs/2026-10-28-unified-acceptance.md)。历史独立版本及记录仅用于回退与溯源。

在 Pro 的“补充耕地验收 → 打开工作台”进入，切换“AI助手”和“采样与出图”。AI页支持兼容Chat Completions的服务、模型目录、中文对话、可展开工具收据和后台任务结果加载。助手默认按合理的低风险选择继续工作，不反复让用户从多个方案里挑选；仅对会改变GIS结果或涉及覆盖、删除、发布的问题追问，现有人类确认仍有效。天地图瓦片通过本机回环代理加载，密钥留在加密配置与插件内存中，不注入浏览器页面。输入 `/` 查看命令；`/sampling` 切换采样页；Enter发送、Shift+Enter换行。切换页签保留草稿和表单，不等于已执行采样。

底图设置与采样页共享天地图凭证，只保存实际编辑的字段；高德JS Key/安全密钥与POI Web服务Key用途不同。无密钥时不会冒充在线加载成功。GCJ-02预览不作为CGCS2000正式成果。

`tools.json` 是53项MCP工具的唯一目录，其中2项由Python客户端发现/选择Pro实例，51项由统一插件执行。新增 `acceptance_snapshots`（按地块批量天地图快照，z18+注记+Lanczos，输出清单）。新增 `acceptance_check`（只读预检）、`acceptance_preview`（按当前选择生成预览，job_id）、`acceptance_delivery`（交付自检，列 FAIL/NEEDS_REVIEW）。新增 `add_tianditu_basemap`（默认 `mode=raster`：按当前地图范围下载天地图瓦片并镶嵌为本地栅格，稳定显示；`mode=live` 尝试 XYZ 会话底图，部分 Pro 环境可能空白）/ `remove_tianditu_basemap`（移除会话底图与本地快照）。Key 不进入图层地址或工具返回。保留原MCP入口 `mcp_server.py` 和命名管道协议，DSH只需重连刷新目录；无需重设账户。新增 `open_acceptance_workspace` 支持工作台页面导航，原 `open_assistant` 定位AI页。工程、地图守卫、GP确认与禁止任意代码执行保持有效。

**1.82 底图四类与 AI 绘图**：`list_basemap_sources` 现列出 6 类来源——天地图影像/矢量/地形晕渲（CGCS2000/Web Mercator，可按地图范围生成本地快照或尝试会话底图）、高德卫星/矢量（GCJ-02，本地快照前逐像素纠偏到目标 CGCS2000 投影）、高德行政区划区图（县/乡界线 + 驻地注记，参考界线，正式报件须三调/民政官方界线）。`add_basemap` 统一加载，`remove_basemap` 只移除受管底图，`calibrate_basemap_offset` 只读给出范围内 GCJ-02 偏移与变化量（公开近似算法，不能替代控制点配准）。`open_basemap_preview` 增加地形预览；区图不支持浏览器预览。绘图新增 `draw_features`（GeoJSON 点/线/面；不带 confirm 只预览，带 confirm 写入指定要素图层或“AI绘制（临时）”图形层）、`list_drawings`、`clear_drawings`（只按本进程登记批次撤销，不按 where 删除业务要素）。`add_basemap`/`remove_basemap` 只增删助手自有底图图层，不写业务数据，因此无 `confirm` 参数（写工具确认约定的已知例外）；`draw_features`/`clear_drawings` 的写入与删除必须 `confirm=true`。设计、实测边界与未验证项见 [底图四类与 AI 绘图（1.82）](../docs/2026-11-03-底图四类与AI绘图.md)。

模型与底图设置分别位于 `%LOCALAPPDATA%/ProAiBridge/settings.json`、`basemaps.json`，密钥使用Windows当前用户加密。这些是统一产品的组件配置位置，不再代表另一份插件。切换模型后隔离对话；停止请求不回滚已完成GIS操作，GP任务需要单独取消。

## 命令行入口（Agent / 人工）

`pro_bridge_cli.py` 是同一桥的命令行客户端，只依赖标准库，用于把「发现实例 → 带守卫调工具 → 轮询任务 → 读报告摘要」合成一次调用，避免每步一次工具往返：

```powershell
python ai/pro_bridge_cli.py list
python ai/pro_bridge_cli.py run check --layer <图斑图层> --all
python ai/pro_bridge_cli.py run preview --layer <图斑图层> --all --out-dir <成果目录>
python ai/pro_bridge_cli.py run delivery --out-dir <成果目录>
python ai/pro_bridge_cli.py report <一键成果报告.json>
```

地图上下文工具会自动带上 `get_status` 的工程/地图身份；写操作（`confirm=true`）不自动重试，超时只提示先查任务。`--pid` 放在子命令之后；多实例时必须指定。`run` 退出码：0=通过或需复核，1=业务 FAIL/任务失败，2=参数或连接错误；`run preview` 遇到检查清单 FAIL 会返回 1，但预览图件通常已经生成，不能据此判定没出图。

开发入口：

```powershell
py -3.12 build.py
py -3.12 build.py --harness
py -3.12 -m pytest ai/tests -q
py -3.12 build.py --bundle
```

项目根 build.py 是统一开发入口，默认构建和完整包均复用 addin 构建器；核心测试harness仍可独立运行。`addin/*.cs`由验收csproj和Roslyn构建器链接编译，保持单一实现。已移除独立DAML、Module与程序集版本声明；安装器核对统一包后归档旧独立AI注册包，配置与业务数据保留。

上游思路与MIT许可见 [NOTICE](NOTICE.md)、[LICENSE](LICENSE)、[上游许可](LICENSE-upstream.txt)。现阶段验证平台为Windows / ArcGIS Pro3.1.5 / .NET6；天地图四类WMTS瓦片已做在线连通性抽测，高德与阿里云未联调，DeepSeek真实对话也未在线回归。键鼠与地图精度等实际限制以统一验证记录为准。
