# Pro AI Bridge 0.4 Task Results Implementation Plan

> 按已加载的 executing-plans 步骤逐项执行；用户已确认本轮设计与自主测试。

**Goal:** 让用户看清 GP 任务时间与输出，并将成功的要素结果明确加载到当前选定地图。

**Architecture:** 从 MapActions 原位提取 GpJob 到独立文件，供真实插件与 C# harness 共用；删除内嵌旧实现。复用既有 GP、地图门禁和元数据读取，新增 load_job_output，按任务保存的输出路径加载，重复调用返回已存在图层。侧栏选择任务后显示耗时/输出及加载按钮；不自动重试写操作。

**Tech Stack:** ArcGIS Pro SDK 3.1 / .NET 6 / WPF / Python MCP SDK。

1. **任务状态**：先在 tests/CoreHarness.cs、tests/test_core.py 加失败测试，覆盖排队/运行/结束时间、单调耗时、成功输出、计数无输出、失败/取消不可加载、结束状态不可覆盖。新增 addin/GpJob.cs，因为任务生命周期需独立于 SDK 执行器测试；实际入口为 MapActions.StartJob。build.py harness 引用它，MapActions 移除旧类。
2. **结果加载**：tools.json 新增 load_job_output(job_id,confirm=true)，地图上下文必需。MapActions 校验任务工程、成功状态、白名单输出类型；在同次 UI 捕获目标地图，MCT 验证 GDB/SHP 数据，已有同源图层直接返回，否则创建。不能把 GetCount 返回的数字当路径，失败/取消输出不可加载。纯逻辑先失败后实现，再真实 Pro 测试重复加载、跨工程/地图拒绝和结果要素数。
3. **侧栏**：ChatPane 新增加载所选结果按钮，显示开始/结束、耗时、输出，失败/取消/无结果时禁用。DirectAsync 携带选中任务所属工程和当前地图，执行器再次检查上下文，避免旧列表作用于新工程。UI 与 MCP 共用同一动作，不依赖桌面捕获修复。
4. **验证与发布**：运行现有与新增 C#、MCP/stdio测试、Ruff、原生编译；新合成工程跑真实工作流及输出加载。按 requesting-code-review 独立审查并修复重要发现。版本原位升0.4.0，旧包保留；更新README、TEST-REPORT和收据，安装并核对SHA256，完整包逐项读回。桌面捕获未修复、共享范围检查受并发影响时均如实记录。

共享根目录不切换分支、不提交他人改动。临时探针仅在 temp；此前删除被拒的目录不重试清理。任务历史本轮仍仅在进程内，不引入自动恢复或自动执行。

验证命令（项目目录）：`py -3.12 build.py --harness`、`py -3.12 -m pytest tests -q`、`py -3.12 -m ruff check .`、`py -3.12 build.py`。现场测试先用 Pro Python 运行 tests/prepare_live.py 到新的空 temp 目录，再设置 PRO_AI_LIVE/PRO_AI_TEST_DIR 执行 tests/test_live.py。最终 `py -3.12 build.py --bundle` 不重编已实测 DLL。
