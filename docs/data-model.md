# 第 1 题：第一阶段数据类型

> 文档版本：0.3
>
> 更新日期：2026-11-01
>
> 适用范围：素材审核与发布排期 Agent 第一阶段
>
> 状态：类型契约已实现并通过测试
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [技术栈](technology-stack.md) · [Agent 架构](agent-architecture.md)
>
> 命名说明：`03-evaluation.md` 编号保留给最终交付规范中的评估文档，本文件使用无编号文件名。

## 1. 类型约定

第一阶段使用 Python 3.11+，运行时数据使用类型化对象，SQLite 负责持久化。

| 概念 | Python 运行时类型 | SQLite 类型 | 约定 |
| --- | --- | --- | --- |
| 标识 | `str` | `TEXT` | 素材、规则、排期、草稿和运行 ID |
| UTC 时间 | 带 UTC 时区的 `datetime` | `TEXT` | RFC 3339，分钟精度，秒固定为 `00` |
| 本地时间 | 带 IANA 时区的 `datetime` | `TEXT` | 仅用于展示和当地日期计算 |
| 时区 | `str` | `TEXT` | 合法 IANA 名称，例如 `Asia/Shanghai` |
| 状态 | 字符串枚举 | `TEXT` | 使用稳定英文值，不保存中文展示名称 |
| JSON 对象 | `dict[str, JsonValue]` | `TEXT` | 写入时序列化，读取时严格校验 |
| JSON 数组 | `list[JsonValue]` | `TEXT` | 写入时序列化，读取时严格校验 |
| 布尔值 | `bool` | `INTEGER` | SQLite 保存 `0/1` |
| 数量、优先级 | `int` | `INTEGER` | 不使用浮点数 |
| 缺失值 | `None` | `NULL` | 不能用空字符串冒充缺失 |

时间进入工具 JSON 后使用 RFC 3339 字符串；进入领域对象和计算逻辑后转换为带时区的 `datetime`。所有时间比较都以 UTC 为准。

`JsonValue` 只允许：

- `str`
- `int`
- `float`
- `bool`
- `None`
- `list[JsonValue]`
- `dict[str, JsonValue]`

## 2. 状态枚举

### 2.1 素材读取

**MaterialReadStatus**

- `SUCCESS`
- `INCOMPLETE`
- `NOT_FOUND`
- `INVALID_ARGUMENT`
- `STORAGE_ERROR`

**SourceStatus**

- `pending_review`
- `approved`
- `rejected`
- `archived`

`SourceStatus` 与本次审核结果无关，不能直接当作 `AuditStatus`。

### 2.2 审核

**AuditStatus**

- `PASS`
- `WARNING`
- `NEEDS_INFO`
- `NEEDS_REVIEW`
- `BLOCKED`

**AuditCheckStatus**

- `PASS`
- `WARNING`
- `NEEDS_INFO`
- `NEEDS_REVIEW`
- `BLOCKED`
- `ERROR`

`AuditStatus` 表示业务审核结论，`AuditCheckStatus` 表示规则检查工具的完整返回状态。

**RuleSeverity**

- `WARNING`
- `BLOCKING`

**RuleKind**

- `STRUCTURED`
- `SEMANTIC`

**RuleOperator**

- `REQUIRED`
- `EQUALS`
- `IN`
- `CONTAINS_ANY`
- `BEFORE`
- `AFTER`
- `REGEX`

**RuleOutcome**

- `PASSED`
- `VIOLATED`
- `NOT_APPLICABLE`
- `UNCERTAIN`
- `MISSING`
- `ERROR`

**SemanticOutcome**

- `PASSED`
- `VIOLATED`
- `NOT_APPLICABLE`
- `UNCERTAIN`

### 2.3 排期

**ScheduleQueryStatus**

- `AVAILABLE`
- `CONFLICT_WITH_ALTERNATIVE`
- `NO_FEASIBLE_SLOT`
- `INVALID_ARGUMENT`
- `STORAGE_ERROR`

**ScheduleEntryStatus**

- `CONFIRMED`
- `PUBLISHED`
- `CANCELLED`

**OccupancyKind**

- `HARD`
- `SOFT`

**ScheduleRecordType**

- `SCHEDULE`
- `DRAFT`

### 2.4 草稿

**DraftStatus**

- `READY`
- `PENDING_CONFIRMATION`

**DraftSaveStatus**

- `NOT_ATTEMPTED`
- `SAVED`
- `EXISTING`
- `REJECTED`
- `INVALID_ARGUMENT`
- `STORAGE_ERROR`

`NOT_ATTEMPTED` 只用于最终运行报告，表示保存工具没有被调用；`save_schedule_draft` 本身不会返回该状态。

**ConfirmationReason**

- `ALTERNATIVE_TIME`
- `RULE_WARNING`

**PublicationStatus**

- `NOT_PUBLISHED`

`DraftStatus=READY` 只表示草稿没有额外异常需要处理，不代表正式排期确认或发布。

### 2.5 Agent 运行

**RunStatus**

- `RUNNING`
- `COMPLETED`
- `INCOMPLETE`
- `FAILED`

**ToolName**

- `read_material`
- `check_audit_rules`
- `query_schedule_slots`
- `save_schedule_draft`

**AgentMessageRole**

- `SYSTEM`
- `USER`
- `ASSISTANT`
- `TOOL`

## 3. 持久化实体

### 3.1 素材 `MaterialRecord`

| 字段 | Python 类型 | SQLite 类型 | 说明 |
| --- | --- | --- | --- |
| `asset_id` | `str` | `TEXT PRIMARY KEY` | 素材唯一 ID |
| `title` | `str \| None` | `TEXT NULL` | 标题 |
| `platform` | `str \| None` | `TEXT NULL` | 平台 |
| `source_status` | `SourceStatus` | `TEXT` | 素材库来源状态 |
| `content` | `str \| None` | `TEXT NULL` | 完整正文，仅服务端使用 |
| `metadata` | `dict[str, JsonValue]` | `TEXT` | 扩展元数据 |
| `version` | `int \| None` | `INTEGER NULL` | 可选诊断版本，不是必填 |
| `updated_at` | `datetime \| None` | `TEXT NULL` | 最后更新时间 |

工具向模型返回 `content_excerpt` 和 `content_truncated`，这两个是派生字段，不单独存数据库。

### 3.2 审核规则 `AuditRuleRecord`

| 字段 | Python 类型 | SQLite 类型 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | `str` | `TEXT PRIMARY KEY` | 规则 ID |
| `rule_name` | `str` | `TEXT NOT NULL` | 规则名称 |
| `enabled` | `bool` | `INTEGER NOT NULL` | 是否启用 |
| `rule_kind` | `RuleKind` | `TEXT NOT NULL` | 结构化或语义规则 |
| `severity` | `RuleSeverity` | `TEXT NOT NULL` | 警告或阻断 |
| `applies_to` | `list[str] \| None` | `TEXT NULL` | 适用平台或类型 |
| `conditions` | `list[StructuredRuleCondition]` | 独立条件表 | 结构化规则的检查条件 |
| `policy_text` | `str \| None` | `TEXT NULL` | 自然语言规则原文 |
| `reason` | `str` | `TEXT NOT NULL` | 命中原因 |
| `priority` | `int` | `INTEGER NOT NULL` | 展示排序 |
| `rule_version` | `int` | `INTEGER NOT NULL` | 规则版本 |
| `effective_from` | `datetime \| None` | `TEXT NULL` | 开始生效 |
| `effective_to` | `datetime \| None` | `TEXT NULL` | 停止生效 |

结构化规则的条件保存在 `audit_rule_conditions`：

| 字段 | Python 类型 | SQLite 类型 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | `str` | `TEXT` | 所属规则 |
| `clause_index` | `int` | `INTEGER` | 条件顺序 |
| `field_path` | `str` | `TEXT` | 白名单字段路径 |
| `operator` | `RuleOperator` | `TEXT` | 检查操作符 |
| `expected_value` | `JsonValue \| None` | `TEXT NULL` | 比较值 |

`SEMANTIC` 规则使用 `policy_text`，结构化条件可以为空；`STRUCTURED` 规则必须有条件。旧 v1 表在初始化时整体迁移为 v2，不存在运行时双版本。

### 3.3 正式排期 `ScheduleEntryRecord`

| 字段 | Python 类型 | SQLite 类型 | 说明 |
| --- | --- | --- | --- |
| `schedule_id` | `str` | `TEXT PRIMARY KEY` | 排期记录 ID |
| `asset_id` | `str` | `TEXT NOT NULL` | 对应素材 |
| `scope_id` | `str` | `TEXT NOT NULL` | 第一阶段为 `global` |
| `publish_at_utc` | `datetime` | `TEXT NOT NULL` | 排期时段开始，UTC |
| `publish_at_local` | `datetime` | `TEXT NOT NULL` | 本地展示时间 |
| `duration_minutes` | `int` | `INTEGER NOT NULL DEFAULT 30` | 占用分钟数 |
| `timezone` | `str` | `TEXT NOT NULL` | IANA 时区 |
| `status` | `ScheduleEntryStatus` | `TEXT NOT NULL` | 正式记录状态 |
| `created_at` | `datetime` | `TEXT NOT NULL` | 创建时间 |

第一阶段只读取该表。`CONFIRMED` 和 `PUBLISHED` 是硬占用，`CANCELLED` 不占用。

### 3.4 排期草稿 `ScheduleDraftRecord`

| 字段 | Python 类型 | SQLite 类型 | 说明 |
| --- | --- | --- | --- |
| `draft_id` | `str` | `TEXT PRIMARY KEY` | 草稿 ID |
| `idempotency_key` | `str` | `TEXT NOT NULL UNIQUE` | 幂等键 |
| `asset_id` | `str` | `TEXT NOT NULL` | 对应素材 |
| `scope_id` | `str` | `TEXT NOT NULL` | 第一阶段为 `global` |
| `requested_at_utc` | `datetime` | `TEXT NOT NULL` | 用户原始要求，UTC |
| `requested_at_local` | `datetime` | `TEXT NOT NULL` | 用户原始要求，本地时间 |
| `proposed_at_utc` | `datetime` | `TEXT NOT NULL` | 草稿建议时段开始，UTC |
| `proposed_at_local` | `datetime` | `TEXT NOT NULL` | 草稿建议时段开始，本地时间 |
| `duration_minutes` | `int` | `INTEGER NOT NULL DEFAULT 30` | 草稿占用分钟数 |
| `timezone` | `str` | `TEXT NOT NULL` | IANA 时区 |
| `audit_status` | `AuditStatus` | `TEXT NOT NULL` | 草稿仅允许 `PASS` 或 `WARNING` |
| `risk_rule_ids` | `list[str]` | `TEXT NOT NULL` | JSON 数组 |
| `risk_reasons` | `list[str]` | `TEXT NOT NULL` | JSON 数组 |
| `draft_status` | `DraftStatus` | `TEXT NOT NULL` | `READY` 或 `PENDING_CONFIRMATION` |
| `requires_confirmation` | `bool` | `INTEGER NOT NULL` | 是否有额外确认原因 |
| `confirmation_reasons` | `list[ConfirmationReason]` | `TEXT NOT NULL` | JSON 数组 |
| `publication_status` | `PublicationStatus` | `TEXT NOT NULL` | 固定为 `NOT_PUBLISHED` |
| `created_at` | `datetime` | `TEXT NOT NULL` | 创建时间 |

第一阶段不保存 `CONFIRMED` 草稿状态。正式确认属于后续能力。

## 4. 通用值对象

### 4.1 工具错误 `ToolError`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `str` | 稳定错误码 |
| `message` | `str` | 面向 Agent 的简短说明 |
| `retryable` | `bool` | 是否允许有限重试 |

`code` 具体取值由各工具契约定义。错误消息不包含 SQL、堆栈、密钥或数据库连接。

### 4.2 数据完整性 `DataCompleteness`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `complete` | `bool` | 基础字段是否完整 |
| `missing_fields` | `list[str]` | 缺失字段路径 |

### 4.3 素材视图 `MaterialView`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `asset_id` | `str` | 素材 ID |
| `title` | `str \| None` | 标题 |
| `platform` | `str \| None` | 平台 |
| `source_status` | `SourceStatus \| None` | 来源状态 |
| `metadata` | `dict[str, JsonValue] \| None` | 已解析元数据 |
| `content_excerpt` | `str \| None` | 最多 1000 字符 |
| `content_truncated` | `bool` | 正文是否被截断 |
| `updated_at` | `datetime \| None` | 可选更新时间 |

### 4.4 规则结果值对象

**RuleEvaluation**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | `str` | 规则 ID |
| `rule_name` | `str` | 规则名称 |
| `rule_kind` | `RuleKind` | 结构化或语义 |
| `severity` | `RuleSeverity` | 规则库配置的严重等级 |
| `outcome` | `RuleOutcome` | 统一执行结果 |
| `reason` | `str` | 风险或判断原因 |
| `evidence_spans` | `list[str]` | 素材原文证据 |
| `missing_fields` | `list[str]` | 缺失输入 |
| `confidence` | `float \| None` | 语义置信度，不作为硬授权 |
| `evaluator` | `str` | 实际执行器 |
| `error_code` | `str \| None` | 结构化错误码 |

**SemanticRuleJudgment**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | `str` | 被评估的规则 |
| `outcome` | `SemanticOutcome` | 语义结论 |
| `reason` | `str` | 判断原因 |
| `evidence_spans` | `list[str]` | 模型提供的原文证据 |
| `confidence` | `float \| None` | 可选置信度 |

**SemanticEvaluationBatch**

| 字段 | 类型 |
| --- | --- |
| `judgments` | `list[SemanticRuleJudgment]` |

**AuditSummary**

| 字段 | 类型 |
| --- | --- |
| `applicable_rule_count` | `int` |
| `structured_rule_count` | `int` |
| `semantic_rule_count` | `int` |
| `checked_rule_count` | `int` |
| `passed_rule_count` | `int` |
| `warning_violation_count` | `int` |
| `blocking_violation_count` | `int` |
| `uncertain_rule_count` | `int` |
| `missing_rule_count` | `int` |
| `error_rule_count` | `int` |

### 4.5 时段结果值对象

**ConflictRecord**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `record_type` | `ScheduleRecordType` | 正式排期或草稿 |
| `record_id` | `str` | 记录 ID |
| `asset_id` | `str` | 占用素材 |
| `publish_at` | `datetime` | 本地时间 |
| `publish_at_utc` | `datetime` | UTC 时间 |
| `occupied_until` | `datetime` | 本地结束时间 |
| `occupied_until_utc` | `datetime` | UTC 结束时间 |
| `duration_minutes` | `int` | 占用分钟数 |
| `status` | `str` | 记录状态 |
| `occupancy` | `OccupancyKind` | 硬占用或软占用 |

`status` 在这里允许 `CONFIRMED`、`PUBLISHED`、`READY`、`PENDING_CONFIRMATION`；这四个状态都参与当前有效冲突检查。

**AlternativeSlot**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `publish_at` | `datetime` | 本地时间 |
| `publish_at_utc` | `datetime` | UTC 时间 |
| `occupied_until` | `datetime` | 本地结束时间 |
| `occupied_until_utc` | `datetime` | UTC 结束时间 |
| `duration_minutes` | `int` | 占用分钟数 |
| `timezone` | `str` | IANA 时区 |
| `requires_confirmation` | `bool` | 第一阶段固定为 `true` |

### 4.6 草稿视图 `DraftView`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | `str` | 草稿 ID |
| `asset_id` | `str` | 素材 ID |
| `scope_id` | `str` | 排期范围 |
| `requested_at` | `datetime` | 用户原始要求 |
| `proposed_at` | `datetime` | 草稿建议时段开始 |
| `duration_minutes` | `int` | 默认 30 |
| `timezone` | `str` | IANA 时区 |
| `audit_status` | `AuditStatus` | 只允许 `PASS` 或 `WARNING` |
| `risk_rule_ids` | `list[str]` | 命中规则 ID |
| `risk_reasons` | `list[str]` | 风险原因 |
| `draft_status` | `DraftStatus` | 草稿状态 |
| `requires_confirmation` | `bool` | 是否有额外确认原因 |
| `confirmation_reasons` | `list[ConfirmationReason]` | 待确认原因 |
| `publication_status` | `PublicationStatus` | 固定为 `NOT_PUBLISHED` |
| `created_at` | `datetime` | 创建时间 |

## 5. 工具输入输出类型

### 5.1 素材读取

**MaterialReadInput**

| 字段 | 类型 |
| --- | --- |
| `asset_id` | `str` |

**MaterialReadResult**

| 字段 | 类型 |
| --- | --- |
| `status` | `MaterialReadStatus` |
| `material` | `MaterialView \| None` |
| `data_completeness` | `DataCompleteness \| None` |
| `error` | `ToolError \| None` |
| `meta` | `dict[str, JsonValue]` |

详细契约见 [素材读取工具](material-read-tool.md)。

### 5.2 审核规则检查

**AuditCheckInput**

| 字段 | 类型 |
| --- | --- |
| `asset_id` | `str` |
| `requested_at` | `datetime` |
| `timezone` | `str` |

**AuditCheckResult**

| 字段 | 类型 |
| --- | --- |
| `status` | `AuditCheckStatus` |
| `rule_results` | `list[RuleEvaluation]` |
| `summary` | `AuditSummary` |
| `error` | `ToolError \| None` |
| `meta` | `dict[str, JsonValue]` |

`AuditCheckStatus=ERROR` 表示工具没有形成可靠审核结论，不能转换成业务状态 `PASS`。

详细契约见 [规则引擎 v2](rule-engine-v2.md)。

### 5.3 时段查询

**ScheduleQueryInput**

| 字段 | 类型 |
| --- | --- |
| `asset_id` | `str` |
| `requested_at` | `datetime` |
| `timezone` | `str` |
| `search_until` | `datetime \| None` |

**ScheduleQueryContext**

| 字段 | 类型 |
| --- | --- |
| `idempotency_key` | `str` |
| `scope_id` | `str` |
| `occupancy_minutes` | `int` |
| `slot_step_minutes` | `int` |
| `max_alternatives` | `int` |

`ScheduleQueryContext` 由运行时注入，不允许模型修改。

**ScheduleQueryResult**

| 字段 | 类型 |
| --- | --- |
| `status` | `ScheduleQueryStatus` |
| `requested_at` | `datetime` |
| `requested_at_utc` | `datetime` |
| `timezone` | `str` |
| `scope_id` | `str` |
| `conflicts` | `list[ConflictRecord]` |
| `alternatives` | `list[AlternativeSlot]` |
| `error` | `ToolError \| None` |
| `meta` | `dict[str, JsonValue]` |

详细契约见 [时段查询工具](schedule-query-tool.md)。

### 5.4 草稿保存

**SaveDraftInput**

| 字段 | 类型 |
| --- | --- |
| `asset_id` | `str` |
| `proposed_at` | `datetime` |
| `timezone` | `str` |

**SaveDraftContext**

| 字段 | 类型 |
| --- | --- |
| `run_id` | `str` |
| `idempotency_key` | `str` |
| `scope_id` | `str` |
| `requested_at` | `datetime` |
| `duration_minutes` | `int` |
| `audit_status` | `AuditStatus` |
| `risk_rule_ids` | `list[str]` |
| `risk_reasons` | `list[str]` |
| `schedule_check_status` | `ScheduleQueryStatus` |
| `allowed_alternatives` | `list[datetime]` |

`SaveDraftContext` 由运行时注入。模型不能传入或修改其中的审核、风险和确认状态。

**SaveDraftResult**

| 字段 | 类型 |
| --- | --- |
| `status` | `DraftSaveStatus` |
| `draft` | `DraftView \| None` |
| `error` | `ToolError \| None` |
| `meta` | `dict[str, JsonValue]` |

详细契约见 [排期草稿保存工具](save-schedule-draft-tool.md)。

## 6. Agent 运行类型

### 6.1 Agent 消息 `AgentMessage`

该类型只用于运行中的 LangGraph 上下文，不持久化到业务数据库。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `role` | `AgentMessageRole` | 消息角色 |
| `content` | `str \| None` | 可见文本，不保存内部思维过程 |
| `tool_name` | `str \| None` | 工具消息对应工具 |
| `tool_call_id` | `str \| None` | 工具调用关联 ID |
| `arguments` | `dict[str, JsonValue] \| None` | 结构化工具参数 |
| `payload` | `dict[str, JsonValue] \| None` | 结构化工具结果 |
| `created_at` | `datetime` | 消息创建时间 |

### 6.2 运行请求 `RunRequest`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | `str` | 每次运行唯一 |
| `workspace_id` | `str` | 第一阶段为 `default` |
| `scope_id` | `str` | 第一阶段为 `global` |
| `asset_id` | `str` | 待处理素材 |
| `requested_at` | `datetime` | 用户原始请求 |
| `requested_at_utc` | `datetime` | 用户请求的标准化 UTC 开始时间 |
| `timezone` | `str` | IANA 时区 |
| `search_until` | `datetime \| None` | 可选查询边界 |
| `idempotency_key` | `str` | 运行时生成 |

`requested_at_utc`、`idempotency_key` 和同日默认查询边界由规范化器派生，不信任模型自由生成。

### 6.3 工具调用记录 `ToolCallRecord`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `step` | `int` | 从 1 开始 |
| `tool_name` | `ToolName` | 四选一 |
| `arguments` | `dict[str, JsonValue]` | 已脱敏参数 |
| `result_status` | `str` | 工具顶层状态 |
| `summary` | `dict[str, JsonValue]` | 结果摘要 |
| `error_code` | `str \| None` | 工具错误码 |
| `decision_basis` | `str \| None` | 简短公开决策依据 |
| `created_at` | `datetime` | 调用时间 |

### 6.4 运行状态 `RunState`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request` | `RunRequest` | 规范请求 |
| `existing_draft` | `DraftView \| None` | 预检查发现的同幂等键草稿 |
| `material_result` | `MaterialReadResult \| None` | 素材读取结果 |
| `audit_result` | `AuditCheckResult \| None` | 审核结果 |
| `schedule_result` | `ScheduleQueryResult \| None` | 时段结果 |
| `draft_result` | `SaveDraftResult \| None` | 草稿结果 |
| `step_count` | `int` | 模型步骤数 |
| `tool_call_count` | `int` | 业务工具调用数 |
| `failed_retry_count` | `int` | 允许范围内的失败重试次数 |
| `last_action_signature` | `str \| None` | 最近动作的规范化摘要 |
| `last_observation_signature` | `str \| None` | 最近观察摘要，用于无进展检测 |
| `run_status` | `RunStatus` | 当前运行状态 |
| `pending_items` | `list[ConfirmationReason]` | 尚未解决的确认原因 |
| `tool_history` | `list[ToolCallRecord]` | 公开 Trace |
| `messages` | `list[AgentMessage]` | LangGraph 模型消息上下文 |

### 6.5 最终运行报告 `RunReport`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | `str` | 运行 ID |
| `asset_id` | `str` | 素材 ID |
| `audit_status` | `AuditStatus \| None` | 审核状态 |
| `risk_reasons` | `list[str]` | 风险原因 |
| `requested_at` | `datetime` | 用户原始要求 |
| `proposed_at` | `datetime \| None` | 建议时段开始 |
| `occupancy_minutes` | `int` | 默认占用分钟数 |
| `requires_confirmation` | `bool` | 是否还有额外事项 |
| `confirmation_reasons` | `list[ConfirmationReason]` | 待确认原因 |
| `draft_id` | `str \| None` | 草稿 ID |
| `draft_status` | `DraftStatus \| None` | 草稿状态 |
| `draft_save_status` | `DraftSaveStatus` | 保存结果；未调用时为 `NOT_ATTEMPTED` |
| `publication_status` | `PublicationStatus` | 固定为 `NOT_PUBLISHED` |
| `run_status` | `RunStatus` | 完成、未完成或失败 |
| `unfinished_reason` | `str \| None` | 未完成原因 |
| `trace_path` | `str \| None` | 公开 Trace 文件路径 |
| `tool_steps` | `int` | 实际业务工具调用次数 |
| `model_steps` | `int` | 实际模型决策次数 |
| `model_mode` | `str` | `mock` 或 `live` |

## 7. 状态一致性约束

必须始终满足：

1. `requires_confirmation == (len(confirmation_reasons) > 0)`。
2. `DraftStatus=READY` 时，`requires_confirmation=false` 且 `confirmation_reasons=[]`。
3. `DraftStatus=PENDING_CONFIRMATION` 时，`requires_confirmation=true` 且至少有一个确认原因。
4. `ALTERNATIVE_TIME` 只能出现在 `proposed_at != requested_at` 时。
5. `RULE_WARNING` 只能出现在 `audit_status=WARNING` 时。
6. 草稿 `audit_status` 只允许 `PASS` 或 `WARNING`。
7. `CONFIRMED`、`NEEDS_INFO`、`BLOCKED` 不得写入草稿审核状态。
8. 第一阶段所有草稿 `publication_status=NOT_PUBLISHED`。
9. 草稿的 `requested_at` 和 `proposed_at` 必须保存在同一 IANA 时区，并按 UTC 比较。
10. 只有 `SAVED` 或 `EXISTING` 状态可以返回非空 `draft`。

## 8. 字段可信边界

| 来源 | 字段 |
| --- | --- |
| 模型可提供 | `asset_id`、`proposed_at`、`timezone` |
| 运行时必须补齐或覆盖 | 原始请求、UTC 时间、时区校验、幂等键、审核结果、风险原因、时段状态、允许候选、确认原因、草稿状态、发布状态 |
| 数据库提供 | 素材数据、规则、正式排期、已有草稿 |

工具执行器不能直接信任模型提供的审核结果、确认标志、发布标志或任意 ID。

## 9. 数据库约束

- `materials.asset_id` 为主键；
- `audit_rules.rule_id` 为主键；
- `schedule_entries.schedule_id` 为主键；
- `drafts.draft_id` 为主键；
- `drafts.idempotency_key` 唯一；
- 数据库结构版本为 `PRAGMA user_version=3`；
- 状态、时间精度、枚举和 JSON 结构在写入前校验；
- 区间是否冲突不能仅靠开始时间上的唯一约束，保存草稿时必须在事务中同时检查正式排期和其他有效草稿；
- `schedule_entries.duration_minutes` 和 `drafts.duration_minutes` 必须大于 0，已有旧记录在迁移时默认补为 30；
- 第一阶段不创建 `runs` 表，运行 Trace 写入 `examples/`；如果后续需要查询运行历史，再单独增加 `runs` 表。

## 10. 当前不引入的数据类型

第一阶段暂不使用：

- 不同素材类型各自的动态占用时长；
- 跨日长时段和全天占位；
- 多素材批量排期结果；
- 业务优先级；
- 草稿确认或正式发布工具；
- 独立的多轮对话内存。

当前已使用半开区间和 `duration_minutes` 表达 30 分钟占用；后续如果允许每条记录使用不同时长，只需沿用现有字段和区间冲突逻辑。
