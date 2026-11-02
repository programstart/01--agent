# 第 1 题：Agent 整体架构

> 文档版本：0.1
>
> 更新日期：2026-11-01
>
> 状态：已按本架构实现，LangGraph 1.2.19 自动化测试通过
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [技术栈](technology-stack.md) · [数据类型](data-model.md)

## 1. 架构目标

Agent 使用 LangGraph 实现受控 ReAct 工作流：

```text
模型选择动作
   ↓
执行确定性工具
   ↓
观察结果写回 Graph State
   ↓
模型调整下一步或结束
```

架构的核心原则：

- 模型负责选择下一步，不负责直接操作数据库；
- 工具负责产生审核、排期和保存事实；
- Runtime 和 Guard 负责执行边界、步数和停止条件；
- Finalizer 根据可信状态生成最终结果；
- 第一阶段只写草稿，不生成正式排期或发布记录；
- 不记录模型内部思维过程，只记录题目要求的公开决策依据。

## 2. 技术基线

- Python 3.11+
- LangGraph `StateGraph`、条件边和显式结束节点作为 ReAct 执行骨架
- 自定义 Graph State、Tool Wrapper、Guard 和 Trace
- Pydantic 2
- SQLite
- argparse CLI
- pytest
- 真实模型适配器与离线 Mock

实际使用的 LangGraph 1.2.19 支持自定义状态、条件边和回调。项目选择 `StateGraph`，把业务 RunState、动态合法动作、预算和无进展检测直接映射到图节点，工具契约和验收标准保持不变。

## 3. 整体分层

```text
┌────────────────────────────────────────────┐
│ CLI                                        │
│ 素材 ID、请求时间、时区、查询边界           │
└────────────────────┬───────────────────────┘
                     ↓
┌────────────────────────────────────────────┐
│ Request Normalizer                         │
│ 参数校验、时间标准化、幂等键、RunRequest    │
└────────────────────┬───────────────────────┘
                     ↓
┌────────────────────────────────────────────┐
│ LangGraph ReAct Runtime                    │
│ Graph State / Model Adapter / Tool Nodes   │
│ Guard / Routing / Trace Mapper             │
└───────┬──────────────┬──────────────┬──────┘
        ↓              ↓              ↓
   真实模型       四个业务工具      离线 Mock
                       ↓
┌────────────────────────────────────────────┐
│ Domain Services 和 Repository Layer         │
│ 素材、规则、排期、草稿                       │
└────────────────────┬───────────────────────┘
                     ↓
┌────────────────────────────────────────────┐
│ SQLite + JSON Trace + examples/             │
└────────────────────────────────────────────┘
```

## 4. 组件职责

| 组件 | 主要职责 | 不允许做的事 |
| --- | --- | --- |
| CLI | 接收可复现的运行参数 | 不决定审核或排期 |
| Request Normalizer | 校验、标准化、生成幂等键和初始状态 | 不接受模型改写后的可信参数 |
| LangGraph Runtime | 执行 ReAct 循环、路由和状态更新 | 不直接实现业务规则 |
| Model Adapter | 真实模型或 Mock 的动作选择 | 不访问 SQLite，不修改工具结果 |
| Tool Wrapper | 参数校验、前置条件、Trace 和执行工具 | 不绕过领域服务 |
| Domain Services | 审核、30 分钟区间冲突和可保存性判断 | 不让模型自由判断事实 |
| Repository | 数据读取和草稿事务写入 | 不向模型暴露数据库对象 |
| Guard | 步数、重试、无进展和动作合法性 | 不依赖模型自行停止 |
| Finalizer | 从可信状态生成结构化结果 | 不采用模型宣称的成功状态 |
| Trace Writer | 输出公开执行记录 | 不记录隐藏思维、完整正文或密钥 |

## 5. LangGraph 工作流

```text
START
  ↓
prepare_run
  ↓
model_decide
  ├── read_material
  │       ↓
  │   validate_and_execute → guard → model_decide
  │
  ├── check_audit_rules
  │       ↓
  │   validate_and_execute → guard → model_decide
  │
  ├── query_schedule_slots
  │       ↓
  │   validate_and_execute → guard → model_decide
  │
  ├── save_schedule_draft
  │       ↓
  │   validate_and_execute → guard → model_decide
  │
  └── no_tool_decision
          ↓
      finalize

guard ──达到上限/无进展──► incomplete
runtime_exception ──────► failed
```

### `prepare_run`

- 接收规范化后的 `RunRequest`；
- 初始化 `RunState`；
- 查询是否已有相同幂等键的草稿；
- 不向用户或模型宣称任何保存结果；
- 进入 `model_decide`。

### `model_decide`

- 向模型提供目标、精简状态、剩余预算和合法动作；
- 模型选择一个业务工具，或不调用工具并结束本轮；
- 模型不能输出最终审核状态或发布状态；
- 运行时不信任模型提供的工具结果。

### `validate_and_execute`

- 使用 Pydantic 校验工具参数；
- 校验当前阶段是否允许该动作；
- 补齐运行时可信上下文；
- 执行对应业务工具；
- 将结构化结果写入 Graph State；
- 记录公开 Trace 事件。

### `guard`

- 更新模型步骤数和工具调用数；
- 检查重复动作、重复结果和无进展；
- 检查错误重试预算；
- 检查是否达到完成条件；
- 决定返回模型、结束或进入 `INCOMPLETE`。

### `finalize`

- 从可信 `RunState` 生成最终报告；
- 模型不能覆盖审核、保存或发布状态；
- 保存最终 Trace；
- 输出用户结果。

## 6. Graph State

本文中的 Graph State 对应数据类型文档中的 `RunState`，是 Agent 唯一的结构化数据总线，至少包含：

| 字段 | 生产者 | 消费者 |
| --- | --- | --- |
| `request` | Request Normalizer | 模型、工具、Finalizer |
| `existing_draft` | `prepare_run` | 模型、保存工具 |
| `material_result` | 素材读取工具 | 模型、规则工具、Finalizer |
| `audit_result` | 规则检查工具 | 模型、保存工具、Finalizer |
| `schedule_result` | 时段查询工具 | 模型、保存工具、Finalizer |
| `draft_result` | 草稿保存工具 | 模型、Finalizer |
| `pending_items` | Runtime 和 Guard | 模型、Finalizer |
| `step_count` | Guard | 路由 |
| `tool_call_count` | Guard | 路由 |
| `failed_retry_count` | Guard | 路由 |
| `last_action_signature` | Guard | 无进展检测 |
| `last_observation_signature` | Guard | 无进展检测 |
| `tool_history` | Tool Wrapper | Trace、Finalizer |
| `run_status` | Graph | Finalizer |
| `messages` | Model Adapter 和 Tool Wrapper | 模型上下文 |

模型只接收必要的状态摘要和工具观察，不接收完整的 SQLite 数据。

## 7. 数据流动

### 7.1 启动阶段

```text
CLI 原始参数
→ Pydantic 输入校验
→ 日期时间和 IANA 时区校验
→ 生成本地时间和 UTC 时间
→ 生成 run_id、workspace_id、scope_id 和幂等键
→ RunRequest
→ 初始 RunState
→ 查询已有草稿指针
→ LangGraph invoke
```

### 7.2 素材读取

```text
模型选择 read_material
→ Tool Wrapper 校验 asset_id
→ MaterialRepository 查询
→ MaterialReadResult
→ Graph State.material_result
→ ToolCallRecord
→ 下一轮模型观察
```

完整素材正文只在服务端可用，模型只看到元数据和有限内容摘录。

### 7.3 规则检查

```text
模型选择 check_audit_rules
→ 运行时读取可信素材快照
→ Rule Repository 获取适用的结构化与语义规则
→ 结构化执行器检查字段条件
→ 语义审核器检查整体表达
→ 程序校验规则 ID、严重等级和原文证据
→ AuditCheckResult
→ Graph State.audit_result
→ 下一轮模型观察
```

规则库内容不整体进入 Agent 上下文。Agent 只接收统一规则结果摘要、命中原因和有限证据。完整当前素材只在服务端交给隔离的语义审核调用，不交给负责选择 Agent 动作的提示词。

### 7.4 时段查询

```text
模型选择 query_schedule_slots
→ 校验请求时间和时区
→ 查询正式排期和有效草稿
→ 排除当前请求自身草稿
→ 判断 30 分钟半开区间是否重叠
→ 冲突时从原请求起每 30 分钟向后查找替代时段
→ ScheduleQueryResult
→ Graph State.schedule_result
→ 下一轮模型观察
```

该工具只查询，不预占，也不写入。

### 7.5 草稿保存

```text
模型选择 save_schedule_draft
→ 校验审核和时段前置状态
→ 校验建议开始时间来自原请求或允许候选
→ 开启 SQLite 写事务
→ 再次检查目标 30 分钟区间的正式排期和有效草稿
→ 检查/复用幂等草稿
→ 计算确认原因和草稿状态
→ 写入 drafts
→ 提交事务
→ SaveDraftResult
→ Graph State.draft_result
→ 下一轮模型或直接 Finalizer
```

该工具不写正式排期，也不执行发布。

## 8. ReAct 决策协议

模型只允许产生两类结果：

1. **工具动作**：调用一个已注册业务工具及其结构化参数；
2. **结束动作**：不再调用工具，由 Finalizer 根据当前状态决定完成或未完成。

工具动作仅包括：

- `read_material`
- `check_audit_rules`
- `query_schedule_slots`
- `save_schedule_draft`

最终报告中的审核、草稿和发布状态不由模型自由生成。模型可提供用户解释，但权威字段来自工具和 Runtime。

## 9. 模型上下文

每轮模型输入包括：

- 当前目标；
- 用户原始发布时间和时区；
- 当前已完成的工具观察；
- 尚未解决的问题；
- 当前允许的动作；
- 必填参数结构；
- 剩余模型步骤和工具调用预算；
- 禁止越过审核、确认和发布边界的要求。

不包含：

- 整个素材库；
- 整个规则库；
- 其他用户数据；
- 完整长正文；
- 数据库连接和 SQL；
- 真实密钥；
- 模型内部思维过程。

工具输出进入消息上下文前必须有长度上限和脱敏。

## 10. Guard 与停止策略

Guard 独立于模型执行：

- 最多 8 个模型步骤；
- 最多 6 次业务工具调用；
- 工具失败最多重试 1 次；
- 当前阶段不允许的动作直接拒绝；
- 相同工具、参数和结果的重复调用计为无进展；
- 连续无进展时不继续循环；
- 达到上限时进入 `INCOMPLETE`；
- 不可恢复异常进入 `FAILED`；
- 只有合法保存成功或得到明确业务结论时才进入 `COMPLETED`。

业务前置条件由 Tool Wrapper 再次执行，不能只依赖模型遵守提示。

## 11. 三条主要路径

### 正常路径

```text
read_material → check_audit_rules → query_schedule_slots
→ save_schedule_draft → finalize
```

结果：

- 审核 `PASS`；
- 原始 30 分钟时段可用；
- 草稿 `READY`；
- `requires_confirmation=false`；
- `publication_status=NOT_PUBLISHED`。

### 冲突路径

```text
read_material → check_audit_rules
→ query_schedule_slots（冲突且有替代）
→ save_schedule_draft（替代时段）
→ finalize
```

结果：

- 草稿 `PENDING_CONFIRMATION`；
- `confirmation_reasons=[ALTERNATIVE_TIME]`；
- 原请求开始时间和建议开始时间同时保留，双方均按 30 分钟占用；
- 未确认、未发布。

### 异常路径

- 素材不存在或资料不足：停止并请求补充；
- 审核阻断：不查询时段、不保存；
- 无替代时段：返回 `NO_FEASIBLE_SLOT`；
- 保存冲突或失败：返回明确失败；
- 达到步数或工具上限：返回 `INCOMPLETE`。

## 12. 最终报告与 Trace

Finalizer 从 `RunState` 生成 `RunReport`：

- 素材 ID；
- 审核状态和风险原因；
- 用户原始开始时间和默认 30 分钟占用；
- 建议开始时间和占用时长；
- 确认原因；
- 草稿 ID；
- 草稿状态和保存状态；
- 发布状态；
- 运行状态与未完成原因。

每轮 Trace 至少记录：

- `run_id`；
- 步骤编号；
- 模型选择的动作；
- 工具名和规范化参数；
- 工具结果状态和摘要；
- 错误码；
- 路由后的下一步；
- 简要决策依据；
- 当前预算；
- 最终状态。

不记录模型隐藏思维过程、完整素材正文、完整规则实现、数据库连接、密钥或隐私数据。

## 13. 数据写入边界

| 数据源 | Agent 权限 |
| --- | --- |
| 素材表 | 只读 |
| 规则表 | 只读 |
| 正式排期表 | 只读 |
| 草稿表 | 只读、写入、幂等复用 |
| Trace 文件 | 新建并写入本次运行 |

LangGraph 的运行检查点不写入业务数据库。第一阶段只需要单次运行状态，不引入生产级持久化 Checkpointer。

## 14. 离线 Mock

Mock Model Adapter 与真实模型实现同一接口，返回预定义工具动作序列，用于：

- 正常路径测试；
- 冲突路径测试；
- 规则阻断测试；
- 保存失败测试；
- 步数上限和无进展测试。

Mock 只验证流程，不能证明真实模型的决策效果。模型可用时必须至少完成一次真实模型端到端演练，并在评估文档中区分二者。

## 15. 计划模块边界

| 模块 | 责任 |
| --- | --- |
| `agent/` | LangGraph 图、状态、路由和 Guard |
| `models/` | 真实模型适配器和 Mock |
| `tools/` | 四工具包装与业务调用 |
| `services/` | 规则执行、时段判断和草稿状态计算 |
| `repositories/` | SQLite 数据访问 |
| `db/` | 连接、建表和种子数据 |
| `tracing/` | LangGraph 事件转公开 Trace |
| `main.py` | CLI 入口和结果输出 |

## 16. 第一阶段明确不做

- UI；
- 多 Agent；
- 子图；
- 分布式 Checkpointer；
- 真实发布；
- 自动确认草稿；
- 多素材批量冲突优化；
- 未固定版本的框架依赖。
