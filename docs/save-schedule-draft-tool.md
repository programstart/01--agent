# 第 1 题：排期草稿保存工具设计

> 设计文档版本：0.2
>
> 工具接口版本：0.2
>
> 更新日期：2026-11-01
>
> 适用范围：仅用于“素材审核与发布排期 Agent”
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [数据类型](data-model.md) · [规则引擎 v2](rule-engine-v2.md) · [时段查询工具](schedule-query-tool.md)

## 1. 设计结论

排期草稿保存工具采用以下方案：

- 工具名为 `save_schedule_draft`；
- 它是第一阶段唯一允许写入业务数据的工具；
- 模型选择保存动作和候选发布时段开始时间；
- Agent 运行时补齐原始请求、审核结果、时段检查结果和幂等键；
- 工具只写入 `drafts` 草稿表，不写入正式排期表；
- 保存前在数据库事务内重新检查目标 30 分钟区间是否重叠；
- 使用唯一幂等键防止相同请求产生多份草稿；
- 相同请求重复保存时返回原草稿 ID；
- 正常使用用户原始时段且无审核风险时，不产生额外待确认项；
- 使用替代时段或存在审核警告时，草稿标记 `PENDING_CONFIRMATION`；
- 所有草稿都不等于正式确认，也不等于已发布；
- 第一阶段不实现草稿确认、正式排期或真实发布工具。

## 2. 工具职责

### 负责

- 校验模型提交的素材、建议时段开始时间和时区；
- 验证模型选择的开始时间来自当前运行的有效方案；
- 校验审核和时段查询前置状态；
- 在事务内重新检查目标 30 分钟区间；
- 执行幂等保存；
- 记录用户原始要求和建议时段；
- 记录审核结果、风险原因和待确认项；
- 明确区分保存状态、确认状态和发布状态；
- 返回新草稿、已有草稿或明确失败。

### 不负责

- 修改素材或审核规则；
- 执行真实发布；
- 写入 `CONFIRMED` 或 `PUBLISHED` 正式排期；
- 自动确认替代时段；
- 自动绕过审核阻断；
- 覆盖或删除已有草稿；
- 在保存失败时宣称成功；
- 由模型自行生成幂等键或确认状态。

## 3. 保存前置条件

工具只有同时满足以下条件才允许写入草稿：

1. 素材读取状态为 `SUCCESS`；
2. 审核状态为 `PASS` 或 `WARNING`；
3. 审核状态不是 `NEEDS_INFO`、`BLOCKED` 或 `ERROR`；
4. 时段查询已经执行，状态为 `AVAILABLE` 或 `CONFLICT_WITH_ALTERNATIVE`；
5. 模型提交的建议开始时间等于用户原请求，或存在于本次时段查询返回的替代时段中；
6. 目标区间通过保存事务内的再次重叠检查。

任一前置条件不满足时，工具返回 `REJECTED` 或 `INVALID_ARGUMENT`，不得写入草稿。

## 4. 幂等键

`idempotency_key` 必须由 Agent 运行时生成，不能由模型提供。建议使用以下稳定业务字段的规范化结果：

- `scope_id`；
- 用户或工作区；
- `asset_id`；
- 用户原始请求的 `requested_at_utc`；
- 请求动作类型 `CREATE_SCHEDULE_DRAFT`。

模型生成的替代时段、说明、Trace 和草稿 ID 不参与幂等键。

相同幂等键已经存在草稿时，返回原草稿，状态为 `EXISTING`，不新增记录。即使用模型本次推荐了另一个替代时段，也优先返回原草稿。用户明确要求更换方案时，应视为新的请求动作，第一阶段不自动覆盖旧草稿。

## 5. 草稿数据模型

第一阶段 `drafts` 表至少包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | TEXT，主键 | 草稿唯一 ID |
| `idempotency_key` | TEXT，唯一 | 请求幂等键 |
| `asset_id` | TEXT | 对应素材 |
| `scope_id` | TEXT | 第一阶段固定为 `global` |
| `requested_at_utc` | TEXT | 用户原始要求，UTC |
| `requested_at_local` | TEXT | 用户原始要求，本地时间 |
| `proposed_at_utc` | TEXT | 草稿建议时段开始，UTC |
| `proposed_at_local` | TEXT | 草稿建议时段开始，本地时间 |
| `duration_minutes` | INTEGER | 占用分钟数，默认 30 |
| `timezone` | TEXT | IANA 时区 |
| `audit_status` | TEXT | `PASS` 或 `WARNING` |
| `risk_rule_ids_json` | TEXT | 命中规则 ID 集合 |
| `risk_reasons_json` | TEXT | 风险原因集合 |
| `draft_status` | TEXT | `READY` 或 `PENDING_CONFIRMATION` |
| `requires_confirmation` | INTEGER | 是否需要用户进一步处理 |
| `confirmation_reasons_json` | TEXT | 待确认原因集合 |
| `publication_status` | TEXT | 固定为 `NOT_PUBLISHED` |
| `created_at` | TEXT | 草稿创建时间 |

时间统一到分钟精度，并保存 UTC 和本地展示值。

## 6. 草稿状态

### `READY`

使用用户原始开始时间，且审核结果为 `PASS`、对应 30 分钟区间无冲突时，草稿状态为 `READY`。

此时：

- `requires_confirmation=false`；
- `confirmation_reasons=[]`；
- `publication_status=NOT_PUBLISHED`。

`READY` 只表示草稿满足当前用户请求，不表示正式排期已确认，也不表示已发布。

### `PENDING_CONFIRMATION`

使用模型建议的替代时段，或审核结果为 `WARNING` 时，草稿状态为 `PENDING_CONFIRMATION`。

确认原因使用稳定枚举：

- `ALTERNATIVE_TIME`：建议开始时间不同于用户原请求；
- `RULE_WARNING`：审核存在非阻断风险。

一个草稿可以同时包含多个确认原因。

### 第一阶段不生成的状态

- `CONFIRMED`：正式排期确认；
- `PUBLISHED`：真实发布完成；
- `CANCELLED`：取消操作不在当前工具范围。

## 7. 工具输入结构

### 模型可见参数

```json
{
  "asset_id": "M01",
  "proposed_at": "2026-11-01T18:30:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

| 参数 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `asset_id` | string | 是 | 去除首尾空格后非空，最长 64 个字符 | 当前素材 |
| `proposed_at` | string | 是 | RFC 3339，秒必须为 `00` | 草稿建议发布时段开始 |
| `timezone` | string | 是 | 合法 IANA 时区名称 | 本地展示和范围计算 |

模型不能传入幂等键、审核状态、风险原因、`requires_confirmation`、正式排期或发布状态，也不能传入任意数据库 ID 来覆盖其他草稿。

### 运行时可信参数

以下内容由 Agent 运行时从当前运行状态补齐，不作为模型自由输入：

| 参数 | 说明 |
| --- | --- |
| `idempotency_key` | 运行时生成 |
| `scope_id` | 第一阶段为 `global` |
| `requested_at` | 用户原始要求 |
| `duration_minutes` | 默认 30 分钟 |
| `audit_status` | 当前审核结果 |
| `risk_rule_ids` | 命中规则 ID |
| `risk_reasons` | 风险原因 |
| `schedule_check_status` | 当前时段检查状态 |
| `allowed_alternatives` | 最近一次查询允许的候选开始时间 |
| `run_id` | 当前 Agent 运行 ID |

运行时根据可信状态计算是否使用替代时段、是否需要确认、确认原因、草稿状态和幂等键。

## 8. 工具输出结构

```json
{
  "status": "SAVED",
  "draft": {
    "draft_id": "D001",
    "asset_id": "M01",
    "scope_id": "global",
    "requested_at": "2026-11-01T18:00:00+08:00",
    "proposed_at": "2026-11-01T18:30:00+08:00",
    "duration_minutes": 30,
    "timezone": "Asia/Shanghai",
    "audit_status": "PASS",
    "risk_rule_ids": [],
    "risk_reasons": [],
    "draft_status": "PENDING_CONFIRMATION",
    "requires_confirmation": true,
    "confirmation_reasons": ["ALTERNATIVE_TIME"],
    "publication_status": "NOT_PUBLISHED",
    "created_at": "<ISO-8601>"
  },
  "error": null,
  "meta": {
    "contract_version": "0.2",
    "idempotency_key_hash": "sha256:<摘要>",
    "save_mode": "transactional_draft_only",
    "occupancy_minutes": 30
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | `SAVED`、`EXISTING`、`REJECTED`、`INVALID_ARGUMENT` 或 `STORAGE_ERROR` |
| `draft` | object 或 null | 成功保存或复用草稿时返回；失败且无草稿时为 `null` |
| `error` | object 或 null | 失败原因；成功时为 `null` |
| `meta` | object | 接口版本、幂等键摘要、保存模式和占用时长 |

### `draft` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | string | 草稿 ID |
| `asset_id` | string | 素材 ID |
| `scope_id` | string | 排期范围 |
| `requested_at` | string | 用户原始要求 |
| `proposed_at` | string | 草稿建议时段开始 |
| `duration_minutes` | number | 占用分钟数 |
| `timezone` | string | 时区 |
| `audit_status` | string | `PASS` 或 `WARNING` |
| `risk_rule_ids` | array | 命中规则 ID |
| `risk_reasons` | array | 风险原因 |
| `draft_status` | string | `READY` 或 `PENDING_CONFIRMATION` |
| `requires_confirmation` | boolean | 是否存在需要用户处理的确认项 |
| `confirmation_reasons` | array | `ALTERNATIVE_TIME`、`RULE_WARNING` |
| `publication_status` | string | 第一阶段固定为 `NOT_PUBLISHED` |
| `created_at` | string | 草稿创建时间 |

### `error` 字段

```json
{
  "code": "SLOT_ALREADY_OCCUPIED",
  "message": "目标时段已被其他有效排期占用",
  "retryable": false
}
```

主要错误码：

| 状态 | 错误码 | 处理 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | `INVALID_ASSET_ID` | 请求用户提供合法素材 ID |
| `INVALID_ARGUMENT` | `INVALID_PROPOSED_AT` | 修正发布时间 |
| `INVALID_ARGUMENT` | `INVALID_TIMEZONE` | 修正时区 |
| `REJECTED` | `AUDIT_PRECHECK_FAILED` | 不保存草稿 |
| `REJECTED` | `SCHEDULE_CHECK_REQUIRED` | 先执行时段查询 |
| `REJECTED` | `PROPOSED_TIME_NOT_ALLOWED` | 只能保存原请求或本次候选中出现的时段 |
| `REJECTED` | `SLOT_ALREADY_OCCUPIED` | 重新查询或结束，不写入 |
| `REJECTED` | `IDEMPOTENCY_CONFLICT` | 相同请求已存在不同草稿，第一阶段不自动覆盖 |
| `STORAGE_ERROR` | `DRAFT_STORAGE_UNAVAILABLE` | 最多重试一次，仍失败则任务未完成 |

## 9. 保存执行流程

1. 校验模型提供的素材、建议时段开始时间和时区。
2. 从运行时读取可信审核和时段查询结果。
3. 检查审核和时段前置条件。
4. 校验建议开始时间来自原请求或最近一次查询返回的候选。
5. 在数据库中按幂等键查询已有草稿。
6. 如果已有草稿，返回 `EXISTING` 和原草稿。
7. 开启 SQLite 写事务。
8. 在事务内再次查询与目标 30 分钟区间重叠的正式排期和其他有效草稿。
9. 排除当前幂等键对应草稿。
10. 如果目标区间仍重叠，回滚并返回 `SLOT_ALREADY_OCCUPIED`。
11. 根据可信审核和时段结果计算草稿状态与确认原因。
12. 写入 `drafts`。
13. 提交事务。
14. 返回 `SAVED` 和草稿 ID。

任何写入异常都必须回滚，不能留下只有部分字段的草稿。

## 10. 正常与替代路径

### 正常路径

- 用户要求 18:00，默认占用 18:00–18:30；
- 该 30 分钟区间可用；
- 审核通过；
- 保存建议开始时间 18:00 和 `duration_minutes=30`；
- `draft_status=READY`；
- `requires_confirmation=false`；
- `confirmation_reasons=[]`；
- `publication_status=NOT_PUBLISHED`。

### 替代路径

- 用户要求 18:00，默认占用 18:00–18:30；
- 该区间与已有 18:00–18:30 冲突，18:30–19:00 可用；
- 模型选择保存 18:30；
- 保存原始要求 18:00、建议开始时间 18:30 和 30 分钟占用；
- `draft_status=PENDING_CONFIRMATION`；
- `requires_confirmation=true`；
- `confirmation_reasons=[ALTERNATIVE_TIME]`；
- `publication_status=NOT_PUBLISHED`。

`READY` 和 `PENDING_CONFIRMATION` 都不表示正式排期已确认。

## 11. 幂等与并发

- `idempotency_key` 必须有数据库唯一约束；
- 重复请求返回原草稿，不执行第二次插入；
- 所有“重新查询冲突并插入”的操作必须在同一个写事务内完成；
- 同一幂等键冲突时返回已有草稿；
- 不同幂等键竞争相互重叠的有效时段时，只允许一个草稿保存成功；
- SQLite 写事务应锁定写入阶段，避免两个并发请求同时通过冲突检查；
- 保存失败时保留原排期和已有草稿，不执行部分更新。

## 12. 确认状态边界

- 工具不提供用户确认能力；
- `requires_confirmation=false` 只表示没有额外异常需要处理；
- 它不代表草稿已经提升为正式排期，也不代表已经发布；
- `requires_confirmation=true` 表示需要用户在未来确认替代时段或审核风险；
- 第一阶段没有 `confirm_schedule_draft` 工具；
- 用户最终确认和正式排期属于后续迭代，不在草稿保存工具中顺带完成。

## 13. Trace 与安全

Trace 记录：

- 工具名和 `run_id`；
- 素材 ID；
- 原请求开始时间、建议开始时间和 30 分钟占用；
- 审核状态和时段查询状态；
- 保存结果和草稿 ID；
- 是否需要确认及确认原因；
- 错误码和重试次数。

Trace 不记录完整幂等键明文、完整素材正文、数据库连接、SQL、底层堆栈、密钥、隐私数据或模型内部思维过程。

安全约束：

- 只允许写 `drafts`；
- 禁止写 `schedule_entries`；
- 参数化数据库操作；
- 不信任模型提供的审核、确认或发布状态；
- 保存失败不能返回草稿 ID 并声称成功；
- 替代时段和规则警告不能被保存为 `READY`。

## 14. 实现后的测试基线

1. 正常路径保存原始时段，返回 `SAVED` 和 `READY`。
2. 使用替代时段时返回 `PENDING_CONFIRMATION`。
3. 审核警告时加入 `RULE_WARNING` 确认原因。
4. 审核 `BLOCKED` 或 `NEEDS_INFO` 时拒绝保存。
5. 未执行时段查询时拒绝保存。
6. 模型提交的候选不在查询结果中时拒绝保存。
7. 同一幂等键重复调用只保留一份草稿。
8. 两个并发请求竞争相互重叠的区间时，只有一个草稿保存成功。
9. 保存时目标区间已被确认排期占用，返回冲突且不写库。
10. 模拟数据库写入失败时事务回滚，不产生部分草稿。
11. 保存工具从不写入正式排期或发布状态。
12. `requires_confirmation=false` 的草稿仍保持 `NOT_PUBLISHED`。
