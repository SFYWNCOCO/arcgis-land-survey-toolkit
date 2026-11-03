# 来源与实现范围

本项目参考 [pedralcg/arcmap-mcp](https://github.com/pedralcg/arcmap-mcp) 的“外部 MCP + GIS 内部桥接”结构，感谢 Pedro Alcoba Gómez。

ArcMap 的 ArcObjects/.NET Framework/Python 2.7 执行层不能用于 ArcGIS Pro。本项目在 Pro SDK 3.1/.NET 6 上独立实现地图操作、WPF 侧栏、兼容模型 API 和 MCP 适配；不是原项目官方发行版，也不是其全部工具的一对一移植。

上游 MIT 许可文本保存在 `LICENSE-upstream.txt`。本项目的源代码采用 MIT 许可，见 `LICENSE`。不包含 ArcGIS Pro 或 Esri SDK 的二进制分发，使用时需自行安装并拥有可用的 Pro 许可。
