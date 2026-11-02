# 审核规则引擎 v2 设计

> 文档版本：0.3
>
> 工具接口版本：0.3
>
> 更新日期：2026-11-01
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [数据类型](data-model.md)

## 1. 总体原则

规则引擎统一支持两类规则：

| 类型 | 执行方式 | 适用问题 |
| --- | --- | --- |
| `STRUCTURED` | 程序确定性执行 | 字段、集合、日期、关键词和正则等明确条件 |
| `SEMANTIC` | 结构化 Chat 模型或 NLI 审核器 | 整体语义、暗示、误导和上下文表达 |

两类规则输出同一个 `RuleEvaluation` 结构，并由同一个聚合器生成最终状态。不存在旧规则引擎与新语义引擎并行的运行路径。

原则是：

```text
程序保证确定事实和硬边界；
模型判断无法规则化的语义；
程序校验模型输出并决定最终业务状态。
```

关键词未命中只能说明对应结构化规则没有命中，不能证明整体合规。

### 1.1 改造影响范围

本次为整体替换，不保留旧引擎与 v2 引擎并行运行的双路径。

| 影响位置 | 旧实现 | 当前统一实现 | 兼容处理 |
| --- | --- | --- | --- |
| 数据类型 | 分离的规则命中、缺失字段和错误字段 | `AuditCheckResult.rule_results: list[RuleEvaluation]` | 调用方必须统一读取 `rule_results` 和 `summary` |
| SQLite | 旧审核规则表 | `audit_rules` 增加 `rule_kind`、`policy_text`，结构化条件独立存放 | 初始化时检测旧表并整体重建，`PRAGMA user_version = 2` |
| Rule Repository | 只读取固定字段规则 | 同时读取结构化与语义规则元数据 | 规则等级、适用平台和生效时间始终来自数据库 |
| 服务层 | 单一规则服务 | `RuleEngine` 统一执行两类规则并校验语义结果 | 不再存在第二套审核入口 |
| Agent State | 旧审核结果结构 | `RunState.audit_result: AuditCheckResult` | Runtime、Tool Executor、Finalizer 均只消费统一结果 |
| 模型观察 | 模型只接收关键词命中 | 模型接收规则 ID、类型、等级、结论、原因、有限证据和摘要 | 完整正文只进入隔离的语义审核调用 |
| 测试 | 只覆盖关键词和字段 | 覆盖语义正反例、不确定、遗漏、重复、未知规则、伪造证据和执行失败 | 任一批次异常均不能形成 `PASS` |
| 示例与 Trace | 含旧 `matched_rule_ids` 等字段 | 重新生成 v0.3 输出，只记录 `rule_results` | 旧示例不再作为验收证据 |

影响边界：素材读取、时段查询和草稿保存的主要协议不变；审核工具的调用参数保持兼容，返回结构升级为 `contract_version = 0.3`。

## 2. 规则存储

### `audit_rules`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | TEXT，主键 | 规则 ID |
| `rule_name` | TEXT | 规则名称 |
| `enabled` | INTEGER | 是否启用 |
| `rule_kind` | TEXT | `STRUCTURED` 或 `SEMANTIC` |
| `severity` | TEXT | `WARNING` 或 `BLOCKING` |
| `applies_to_json` | TEXT | 适用平台或素材类型 |
| `policy_text` | TEXT，可空 | 自然语言规则原文 |
| `reason` | TEXT | 命中后的风险原因 |
| `priority` | INTEGER | 结果顺序和解释优先级 |
| `rule_version` | INTEGER | 规则版本 |
| `effective_from` | TEXT，可空 | 生效时间 |
| `effective_to` | TEXT，可空 | 失效时间 |

### `audit_rule_conditions`

只用于结构化规则：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | TEXT | 所属规则 |
| `clause_index` | INTEGER | 条件顺序 |
| `field_path` | TEXT | 白名单字段路径 |
| `operator` | TEXT | 检查操作符 |
| `expected_value_json` | TEXT | 比较值 |

同一结构化规则的多个条件使用 AND 语义。

旧规则表如果没有 `rule_kind`，数据库初始化时会被整体替换为 v2 结构。现有规则来自可重建的合成数据，不保留旧运行时路径。

## 3. 已配置规则

### 结构化规则

- `R001`：素材标题必填，`BLOCKING`；
- `R002`：权利归属必填，`BLOCKING`；
- `R003`：来源状态必须允许发布，`BLOCKING`；
- `R004`：正文不得包含明确禁止的收益承诺关键词，`BLOCKING`；
- `R005`：需要人工确认的宣传表达，`WARNING`。

### 语义规则

- `R006`：素材不得直接或间接暗示本金不会减少、收益有保证或不存在风险，`BLOCKING`。

`R006` 用于覆盖没有原禁用词、但整体表达构成保本收益暗示的情况。

## 4. 结构化规则执行

1. 从 `audit_rules` 和 `audit_rule_conditions` 加载启用规则；
2. 按生效时间和适用范围过滤；
3. 使用白名单字段路径解析素材或请求字段；
4. 逐个条件执行操作符；
5. 多个条件按 AND 聚合；
6. 任一条件确认违规时，规则结果为 `VIOLATED`；
7. 没有违规但存在缺失输入时，结果为 `MISSING`；
8. 配置或执行失败时，结果为 `ERROR`。

支持的操作符：

- `REQUIRED`
- `EQUALS`
- `IN`
- `CONTAINS_ANY`
- `BEFORE`
- `AFTER`
- `REGEX`

正则仅用于格式类结构化规则，不能替代自然语言语义审核。

## 5. 语义规则执行

语义审核器接收：

- 规则 ID；
- 规则名称；
- 自然语言规则原文；
- 规则严重等级；
- 当前素材的完整正文；
- 素材平台和可信元数据。

每轮语义审核会同时评估所有当前适用的 `SEMANTIC` 规则，并要求每个规则只返回一个结果。

语义模型输出：

- `PASSED`
- `VIOLATED`
- `NOT_APPLICABLE`
- `UNCERTAIN`

每个结果包含：

- `rule_id`
- `reason`
- `evidence_spans`
- 可选 `confidence`

### 程序校验

程序必须检查：

1. 返回的规则 ID 是否来自当前适用规则；
2. 每条语义规则是否恰好返回一次；
3. 是否存在未知规则或重复结果；重复规则的该条结果强制为 `ERROR`，未知规则使整个批次失败；
4. 声称 `VIOLATED` 时是否提供原文证据；
5. 证据片段是否逐字存在于素材正文中；
6. 风险等级是否使用规则库配置，而不是模型输出。

以下情况不能返回 `PASS`：

- 语义模型不可用；
- 模型遗漏规则；
- 同一规则返回多个结果；
- 返回未知规则；
- 违规结果没有证据；
- 证据片段与素材不匹配；
- 模型输出无法通过结构校验。

## 6. 统一结果结构

每个适用规则产生一个 `RuleEvaluation`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | string | 规则 ID |
| `rule_name` | string | 规则名称 |
| `rule_kind` | string | `STRUCTURED` 或 `SEMANTIC` |
| `severity` | string | `WARNING` 或 `BLOCKING` |
| `outcome` | string | `PASSED`、`VIOLATED`、`NOT_APPLICABLE`、`UNCERTAIN`、`MISSING` 或 `ERROR` |
| `reason` | string | 风险或判断原因 |
| `evidence_spans` | array | 来自素材的原文证据 |
| `missing_fields` | array | 缺失输入 |
| `confidence` | number 或 null | 语义判断置信度，不参与硬约束授权 |
| `evaluator` | string | 执行器标识 |
| `error_code` | string 或 null | 结构化错误码 |

## 7. 工具输入

工具名：`check_audit_rules`

```json
{
  "asset_id": "M07",
  "requested_at": "2026-11-01T20:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

工具不接收模型生成的规则、风险等级或审核状态。

## 8. 工具输出

```json
{
  "status": "BLOCKED",
  "rule_results": [
    {
      "rule_id": "R006",
      "rule_name": "禁止整体暗示保本收益",
      "rule_kind": "SEMANTIC",
      "severity": "BLOCKING",
      "outcome": "VIOLATED",
      "reason": "素材直接暗示本金不会减少，构成整体保本收益暗示。",
      "evidence_spans": [
        "你的本金都不会减少"
      ],
      "missing_fields": [],
      "confidence": 0.99,
      "evaluator": "live-semantic",
      "error_code": null
    }
  ],
  "summary": {
    "applicable_rule_count": 6,
    "structured_rule_count": 5,
    "semantic_rule_count": 1,
    "checked_rule_count": 6,
    "passed_rule_count": 5,
    "warning_violation_count": 0,
    "blocking_violation_count": 1,
    "uncertain_rule_count": 0,
    "missing_rule_count": 0,
    "error_rule_count": 0
  },
  "error": null,
  "meta": {
    "contract_version": "0.3",
    "rule_set_version": "phase1-v2",
    "evidence_limit_chars": 200,
    "semantic_evaluator": "live-semantic"
  }
}
```

`rule_results` 是唯一规则结果来源。调用方不得再读取旧 `matched_rules`、`missing_fields` 或 `rule_errors` 字段。

## 9. 状态聚合

1. 任一 `BLOCKING` 规则为 `VIOLATED`：`BLOCKED`；
2. 否则存在规则执行错误：`ERROR`；
3. 否则规则依赖资料缺失：`NEEDS_INFO`；
4. 否则存在语义结果不确定：`NEEDS_REVIEW`；
5. 否则只有 `WARNING` 规则违规：`WARNING`；
6. 所有适用规则成功执行且无违规：`PASS`。

`BLOCKED`、`NEEDS_INFO`、`NEEDS_REVIEW` 和 `ERROR` 都禁止保存排期草稿。

## 10. Trace

Trace 记录：

- 工具名和参数；
- 适用、已检查的结构化和语义规则数；
- 命中规则 ID、类型、等级、原因和证据；
- 不确定规则和缺失字段；
- 错误码；
- 语义审核器模式。

不记录模型内部思维。完整素材只由服务端语义审核器使用，不进入 Agent 的公开提示词。

## 11. 测试基线

- 结构化规则通过、警告、阻断和缺失资料；
- 无关键词命中但整体语义违规；
- 明确风险提示的正常文本不被误拦截；
- 语义不确定返回 `NEEDS_REVIEW`；
- 语义审核器失败不能返回 `PASS`；
- 模型遗漏规则、重复返回规则或返回未知规则；
- 模型伪造证据时拒绝；
- 旧规则数据库迁移到 v2；
- 审核阻断后不得查询或保存排期；
- 正常与冲突排期路径回归。
