# 已有单元复核与地块导航实施批次

目标：已有单元与自动划分结果接受同样的数据复核，长图斑编号保留完整，定位窗口提供明确范围的前后翻查和真实问题定位。1.33 已交付包保持原样。

顺序：先写数据反例及31字符图斑的真实Pro用例，再补已有单元独立检查和字段传播；接入报告与失效阶段；随后连接窗口的范围快照、前后按钮及问题列表；最后运行邻域测试、Pro流程及SDK编译。写入仅限插件与本批测试/说明，修改前已经读取目标原文。

边界：四因素冲突、跨项目单元、重复图斑号和几何重叠属于数据失败；缺资料、面积超原则值50亩与集中性按适用模态保留复核，不机械当作规范硬失败。不覆盖源库、不自动改用户既有单元；不匹配的QA编号不得任意跳转。问题读取绑定本次报告与预览图层身份。

主要风险：面积累加舍入、二维包围盒误判实际重叠、相同点号跨项目误定位、SHP字段限长。分别用精确面积累计、实际几何相交、范围身份及导出读回反例验证。未知项不记作通过。

## 2026-10-24 阶段验收收据

- 自动验收：`test_locator_navigation.py`、`test_existing_unit_qa.py`、`test_existing_units_pro.py`、`test_addin_contract.py` 合计 21 passed / 1 skipped。跳过项为依赖 ArcPy 的 Pro 数据测试，在系统 Python 3.12 中 `importorskip('arcpy')`；这不是通过证据。
- 编译：Pro 3.1.5 SDK assemblies + Roslyn 编译成功；DLL SHA-256 `EAB3A8FB171ACAD92956F66F6D3597D4DF9F7EF2B701B1948A718B96217874C2`。
- 打包：候选版本 1.34，78 个包内条目；包内 DLL 哈希与编译输出一致；未发现凭证文件名条目。Add-In SHA-256 `12BE0C4355AD7845C84AB38A9A6A6FB638DC2A5FCACFAD73E26621B6ED56DC4C`。
- 包路径：`addin/_archive/20261024_superseded_builds/采样助手_1.34_定位质检候选版_20261024.zip`；现场验收表含 A1–A9，默认均为“未测”。
- 实机：ArcGIS Pro 桌面自动化两次启动均未返回可操作窗口（`launched app did not expose a targetable window`）；未安装、未点击、未缩放。不得据代码测试代替人工Pro实测。
- Gate：**NEEDS_REVIEW / 等待现场验收**。在收到带截图或日志的 CSV 前，不进入任务恢复与新手向导阶段。

## 2026-10-24 候选包 1.35 与追加验收收据

- 依据修正：`acceptance/triage.py` 的报备影像依据改为结构化来源 `system_review_technical_requirements`，物理页 (21, 62, 78)，作用范围标为适用系统报备环节；`test_triage.py` 新增映射断言。先运行新测试得到缺少来源映射的预期失败，再修复。
- 系统 Python 3.12 定向回归：27 passed / 1 skipped。ArcGIS Pro 3.1.5 Python 3.9.16 运行 `test_existing_units_pro.py`：7 passed，0 failed/skip，84.99 秒；XML 收据在 `temp/locator_existing_units_pro_20261024.xml`。
- 1.35 Roslyn DLL SHA-256：`A5E88B450698172CE843E70FCF12DA323DA1D8CF8F94B2197AF17EFDB2DDAD55`。Add-In SHA-256：`0B92E75060CB58A17E3176439D555D7CAF7332D602EEA637BC12F311332CE9E0`。
- 更新包：`addin/_archive/20261024_superseded_builds/采样助手_1.35_定位质检候选版_验收收据_20261024.zip`；包含27/1系统测试XML、7/0 Pro数据测试XML及9项人工点击验收表。1.34版本保留。
- 用原39号预览报告重新生成摘要并读回来源：`《补充耕地系统审查技术要求》物理页 21、62、78（适用系统报备环节）`；新清单在 `D:/work/高标准/验收预览_天地图_39号图斑_20261024/修正依据复核_20261024/检查清单.json`，原 `检查清单.json` 未覆盖。
- Pro UI：进程可见但窗口激活和捕获失败；本轮未安装、未点击。**当前阶段仍 NEEDS_REVIEW**，必须补现场截图/记录后才进入下一阶段。
