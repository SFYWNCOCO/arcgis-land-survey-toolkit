# 参与贡献

感谢提交改进。本仓库是「广西补充耕地验收工作台」的公开源码快照（ArcGIS Pro 插件 + Python/MCP 核心），任何人都可以 Fork 后提交 Pull Request。

## 提交 PR 的步骤

1. 点击仓库右上角 **Fork**，克隆自己的 Fork。
2. 从 `main` 拉出特性分支：`git checkout -b fix/<简述>` 或 `feat/<简述>`。
3. 修改并本地验证（见下）。
4. 推送到 Fork，向本仓库 `main` 发起 Pull Request，说明动机、改动范围与验证方式。

## 本地验证

```bash
python -m pytest tests -q
```

- 纯 Python 测试无需 ArcGIS Pro；涉及 arcpy/gdal 的用例会自动 skip。
- C# 插件（`addin/`）需要 .NET 6 与 Pro SDK，见 `addin/README-build.md`；未构建时 `tests/test_addin_contract.py` 中依赖 DLL 的 1 项会失败，属环境原因。
- 改动涉及地图/出图行为时，请附上实际验证证据（截图或运行回执），并注明未验证范围。

## 约定

- 一个 PR 只做一件事；不夹带无关格式化或重命名。
- 不要提交真实项目数据、客户项目号、地块坐标、带密级标注的图件或本机绝对路径；示例一律用合成数据。
- 行为边界（PASS/FAIL/NEEDS_REVIEW 含义）不得因改动而放宽；未验证项保留 UNVERIFIED，不写成已完成。
- 文档用中文；新增功能同步更新 `README.md` 与对应 `docs/` 说明。

## 合并

- 默认使用 Squash merge；如你的改动需要保留多个原子提交，请在 PR 描述里说明。
- 维护者会审阅正确性、数据安全和边界声明，必要时要求补充验证。
