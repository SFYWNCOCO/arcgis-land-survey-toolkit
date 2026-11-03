# Agent 工程实践落地（2026-11-01）

对照 `agent-engineering-practices` 的六步闭环，本批只做能在本机验证的四件事，不做框架。

## 一、结果定义

| 结果 | 可观察判据 |
|---|---|
| 任务成功 | 一键成果报告 `技术状态` 非 FAIL，检查清单无 FAIL，账本记 `status` 与检查计数 |
| 任务部分完成 | `技术状态=PASS` 但 `业务审核状态=NEEDS_REVIEW`（缺四因素、影像精度未核实等） |
| 任务失败 | 阶段抛异常，账本记 `FAILED` + 错误摘要；任务状态文件为 FAILED |
| 人工接管点 | 设计确认（导出前必须填确认人）、验收预览确认、交付自检签字 |

## 二、运行账本（`acceptance/run_ledger.py`）

每次 `oneclick.run`（check/prepare/preview/export）追加一条 JSONL 到
`%LOCALAPPDATA%/SamplingAssistant/ledger.jsonl`，字段固定：

`ts、action、status、business_status、run_id、input_fingerprint、task_dir、units、parcels、points、checks{PASS/NEEDS_REVIEW/FAIL/UNVERIFIED}、elapsed_s、manual_confirmed、error`

- 接入点：`oneclick.run` 的 check 分支、成功返回前、异常分支各一处；账本异常全吞，不影响主流程。
- 查看：`python -c "from acceptance.run_ledger import summarize; print(summarize())"`。
- 实测：本机已有 14 条（13 check / 1 preview），`check` P95 0.1s。
- 不记密钥、影像内容和完整绝对路径（任务目录只留目录名）。

## 三、评估集（`scripts/eval_suite.py`）

现场生成样例，不依赖外部数据；必须用 Pro 自带 Python（arcpy）：

| 样例 | 期望 | 用途 |
|---|---|---|
| clean_3parcels | PASS | 正常 3 图斑 |
| duplicate_ids | FAIL | 重复编号必须被拦 |
| null_geometry | FAIL | 空几何必须被拦 |
| missing_id_field | FAIL | 无编号字段必须被拦 |
| oversize_unit（`--prepare`） | FAIL | 超 50 亩单元 |
| real_project（`--real`） | PASS | 真实项目只读检查 |

输出 `out/eval/eval-<时间>.json|.md`（通过率、逐样例期望/实际/耗时）。

**评估集抓到的真缺陷**：首轮 4 例里 2 例期望 FAIL 实际 PASS —— `preflight` 只查坐标系、
编号字段、范围、成果目录、模板，**不查编号重复与空几何**（课件 p26 要求核图斑矢量合规性）。
已补 `_quality_checks`（编号空值/重复、空几何），修复后评估 5/5（含真实示例区项目）。

## 四、发布门禁（`addin/build_all.py --gate`）

打包前用 Pro Python 跑评估集，全过才允许打包：

```powershell
py -3.12 land_survey_toolkit\addin\build_all.py --gate --skip-build --no-install
```

- 正向：4/4 通过 → 继续打包（实测 1.78，209 条目）。
- 负向对照：把 `clean_3parcels` 期望改成 FAIL → 3/4，`--gate` 返回 1 并中止打包（已验证）。

## 五、工具面摘要（`scripts/tool_surface.py`）

从 `ai/tools.json` 自动生成 `docs/tool-surface.md`：47 个工具中只读 16、写 31
（地图 8/25，工程 8/6）；`--check` 校验每个工具都有 `context` 与 `readOnlyHint`（当前 0 缺失）。
写工具清单单独成节，供调用前确认副作用。

## 六、尚未覆盖

- 模型侧指标（token、缓存命中、模型选择）未接入：本工具链不经过大模型，只有 Agent 会话层有；
- 并发与容量（多 Pro 实例、批量并行）未统计；
- 人工接管只记「是否已确认设计」，未统计接管原因与耗时；
- 评估集目前 5 例，样本少，不作趋势结论；扩样时保持"样例来源与版本"记录。
