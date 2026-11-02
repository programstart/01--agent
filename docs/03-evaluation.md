# 第 1 题：第一阶段评估记录

> 更新日期：2026-11-01
>
> 测试环境：Windows、Python 3.12.4
>
> 结果：自动化测试 30 项全部通过

## 1. 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src run.py scripts\generate_examples.py
```

实际测试结果：

```text
30 passed
No broken requirements found.
```

原始命令结果副本：[examples/test-results.txt](../examples/test-results.txt)。

测试包含工具单元测试、统一规则引擎测试、语义证据校验、旧数据库迁移、LangGraph 路径测试、CLI 端到端测试、幂等测试、预算限制测试和真实模型配置失败测试。

## 2. 验收覆盖

| 验收项 | 自动化证据 | 实际运行材料 |
| --- | --- | --- |
| AC-01 正常排期 | `test_normal_path_saves_ready_draft`、`test_cli_mock_path` | [正常输出](../examples/01-normal/output.json)、[Trace](../examples/01-normal/trace.json) |
| AC-02 素材不存在 | `test_material_not_found`、`test_material_not_found_does_not_save` | 单元和 Agent 测试 |
| AC-03 素材信息不足 | `test_material_incomplete_and_rule_needs_info_do_not_save` | [资料不足输出](../examples/03-missing-data/output.json)、[Trace](../examples/03-missing-data/trace.json) |
| AC-04 非阻断规则风险 | `test_rule_check_pass_warning_block_and_missing_info`、`test_blocked_and_warning_paths` | 测试结果 `WARNING → PENDING_CONFIRMATION` |
| AC-05 阻断规则 | `test_rule_check_pass_warning_block_and_missing_info` | [规则阻断输出](../examples/04-rule-blocked/output.json)、[Trace](../examples/04-rule-blocked/trace.json) |
| AC-06 时段冲突与替代 | `test_schedule_query_detects_conflict_and_returns_alternative`、`test_schedule_interval_uses_adjacent_slot_and_30_minute_step`、`test_conflict_path_saves_alternative_pending_draft`、`test_draft_save_transaction_rejects_overlapping_interval` | [冲突输出](../examples/02-conflict/output.json)、[Trace](../examples/02-conflict/trace.json) |
| AC-07 无可行时段 | `test_schedule_query_available_and_no_slot` | 单元测试 |
| AC-08 重复提交幂等 | `test_duplicate_request_reuses_single_draft` | 测试验证草稿数保持 1 |
| AC-09 草稿保存失败 | `test_save_failure_is_explicit_and_rolls_back` | [保存失败输出](../examples/05-save-failure/output.json)、[Trace](../examples/05-save-failure/trace.json) |
| AC-10 执行上限 | `test_tool_budget_stops_with_incomplete`、`test_repeated_action_and_observation_stop_as_no_progress` | 测试验证 `INCOMPLETE` |
| AC-11 无关键词的整体语义违规 | `test_semantic_rule_catches_whole_content_without_keyword_match`、`test_semantic_violation_blocks_draft` | [真实模型语义阻断输出](../examples/08-live-semantic-block/output.json)、[Trace](../examples/08-live-semantic-block/trace.json) |
| AC-12 风险提示正常表达 | `test_semantic_rule_accepts_explicit_risk_statement` | [真实模型语义合规输出](../examples/09-live-semantic-pass/output.json)、[Trace](../examples/09-live-semantic-pass/trace.json) |
| AC-13 语义输出不完整或无效 | `test_uncertain_semantic_result_requires_review`、`test_invalid_semantic_evidence_is_rejected`、`test_missing_semantic_result_cannot_pass`、`test_duplicate_semantic_result_cannot_pass`、`test_unknown_semantic_result_cannot_pass`、`test_semantic_evaluator_failure_cannot_pass` | 测试验证不返回 `PASS` |

## 3. ReAct 路径证据

正常路径的实际模型动作序列：

```text
read_material
→ check_audit_rules
→ query_schedule_slots
→ save_schedule_draft
→ finish
```

冲突路径中，已有排期占用 `18:00–18:30`，`query_schedule_slots` 返回从 `18:30` 开始的 30 分钟候选；模型改为保存 `18:30–19:00` 替代草稿，并保留原始 18:00。该差异来自工具观察结果，不是固定输入分支。

两份 Trace 都包含：

- 步骤编号；
- 模型动作；
- 工具参数；
- 工具结果摘要；
- 下一路由；
- 简短决策依据；
- 模型和工具预算。

## 4. 实际结果摘要

| 场景 | 审核 | 建议开始时间 | 草稿 | 发布 |
| --- | --- | --- | --- | --- |
| 正常 | `PASS` | 20:00 | `READY / SAVED` | `NOT_PUBLISHED` |
| 冲突 | `PASS` | 18:30 | `PENDING_CONFIRMATION / SAVED` | `NOT_PUBLISHED` |
| 资料不足 | 未形成审核结论 | 无 | `NOT_ATTEMPTED` | `NOT_PUBLISHED` |
| 规则阻断 | `BLOCKED` | 无 | `NOT_ATTEMPTED` | `NOT_PUBLISHED` |
| 保存失败 | `PASS` | 20:00 | `STORAGE_ERROR` | `NOT_PUBLISHED` |
| 整体语义违规 | `BLOCKED` | 无 | `NOT_ATTEMPTED` | `NOT_PUBLISHED` |
| 明确风险提示 | `PASS` | 20:00 | `READY / SAVED` | `NOT_PUBLISHED` |

## 5. 真实模型与 Mock 边界

自动化测试使用离线 Mock，保证结果稳定。随后使用用户提供的本地模型凭据执行了四条真实模型路径：

- [真实模型正常路径输出](../examples/06-live-normal/output.json)与 [Trace](../examples/06-live-normal/trace.json)；
- [真实模型冲突路径输出](../examples/07-live-conflict/output.json)与 [Trace](../examples/07-live-conflict/trace.json)；
- [真实模型语义阻断输出](../examples/08-live-semantic-block/output.json)与 [Trace](../examples/08-live-semantic-block/trace.json)；
- [真实模型语义合规输出](../examples/09-live-semantic-pass/output.json)与 [Trace](../examples/09-live-semantic-pass/trace.json)。

正常路径中，模型依次选择素材读取、规则检查、时段查询、草稿保存和结束。冲突路径中，模型观察到 `CONFLICT_WITH_ALTERNATIVE` 后选择保存 `18:30–19:00` 替代草稿，并正确保留 18:00 原始要求。

语义验证中，M07 没有命中固定禁用词，但“本金都不会减少”被真实语义规则 R006 判为整体保本收益暗示，最终 `BLOCKED`。M08 明确说明“不承诺本金不受损失”，语义规则通过，证明规则不是简单拦截所有风险描述。

首次真实模型运行曾因上下文只显示“已用工具 0 次”而误判没有预算，提前选择结束。运行时将其识别为 `INCOMPLETE`。修正为显示剩余预算、严格工具参数和结束条件后，两类路径均成功。

Mock 只证明确定性流程；真实模型运行证明当前模型在给定提示词下能够完成正常、冲突和语义规则路径，但不代表其他模型或生产流量下的稳定性。凭据、服务地址和模型名称未写入项目文件或 Trace。

## 6. 已知限制

- 每次只处理一个素材和一个发布时段；
- 默认所有请求占用 30 分钟，暂不支持不同素材类型的独立时长；
- 只读取预置合成正式排期；
- 不实现最终确认或真实发布；
- 不自动迁移过期草稿；
- 多素材同时竞争重叠时段时不做自动优先级分配；
- 真实模型只验证了单个模型的正常、冲突和两条语义规则路径，未覆盖生产规模或模型切换。
- 语义规则依赖模型判断，已覆盖一组正例和一组反例，但未形成大规模标注集；
- 当前单个素材正文可完整交给语义审核器；超长素材的分块与全局语义聚合尚未实现。
