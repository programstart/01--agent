# 第 1 题：时段查询工具设计

> 设计文档版本：0.2
>
> 工具接口版本：0.2
>
> 更新日期：2026-11-01
>
> 适用范围：仅用于“素材审核与发布排期 Agent”
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [数据类型](data-model.md) · [草稿保存工具](save-schedule-draft-tool.md)

## 1. 设计结论

`query_schedule_slots` 使用连续时段而不是离散发布时间点：

- 每个发布请求默认占用 `30` 分钟；
- 时段按半开区间 `[开始时间, 开始时间 + 30 分钟)` 表示；
- 两个时段发生真实重叠时判定冲突；
- 首尾相接不算冲突，例如 `18:00–18:30` 与 `18:30–19:00` 可以相邻；
- 发生冲突后，从用户原请求时间开始以 `30` 分钟为步频向后查找候选；
- 时间统一转换为 UTC 后比较；
- 已确认排期和已发布记录是硬占用，有效草稿是软占用；
- 当前请求自身的草稿在查询中排除；
- 工具只查询，不预占、不保存草稿、不发布内容；
- 保存草稿时必须在数据库事务内重新执行相同的区间冲突检查。

## 2. 工具职责

### 负责

- 校验素材 ID、请求时间、时区和查询边界；
- 将本地时间标准化为 UTC；
- 查询同一 `scope_id` 下与目标区间重叠的有效占用；
- 区分正式排期和有效草稿；
- 排除当前请求自身草稿；
- 以 30 分钟为默认步频查找未来替代时段；
- 返回冲突记录、时段结束时间和有限数量候选；
- 返回结构化错误，避免将查询失败当作可用。

### 不负责

- 修改、取消或移动已有排期；
- 保存草稿；
- 预占候选时段；
- 自动确认替代时段；
- 执行审核规则；
- 发布内容；
- 让模型自行编造候选时间。

## 3. 时段模型

### 3.1 半开区间

一个占用时段由以下字段确定：

- `scope_id`；
- `publish_at_utc`；
- `duration_minutes`。

本地结束时间为：

```text
occupied_until = publish_at + duration_minutes
```

默认 `duration_minutes=30`。半开区间意味着结束时刻本身不属于该占用：

```text
18:00–18:30
18:30–19:00
```

以上两个时段首尾相接，不冲突。

### 3.2 冲突公式

两个时段 `[A.start, A.end)` 和 `[B.start, B.end)` 重叠，当且仅当：

```text
A.start < B.end
且 B.start < A.end
```

假设已有 `S001` 为 `18:00–18:30`：

| 新请求 | 新请求占用 | 结果 |
| --- | --- | --- |
| 18:00 | 18:00–18:30 | 冲突 |
| 17:45 | 17:45–18:15 | 冲突 |
| 18:15 | 18:15–18:45 | 冲突 |
| 18:30 | 18:30–19:00 | 不冲突，首尾相接 |
| 19:00 | 19:00–19:30 | 不冲突 |
| 11 月 2 日 18:00 | 次日 18:00–18:30 | 不冲突 |

### 3.3 有效状态

正式排期：

- `CONFIRMED`：硬占用；
- `PUBLISHED`：硬占用；
- `CANCELLED`：不占用。

草稿：

- `READY`：有效软占用；
- `PENDING_CONFIRMATION`：有效软占用。

不同素材不能创建相互重叠的有效草稿，也不能覆盖正式排期。

### 3.4 自身排除

运行时根据原始请求生成可信 `idempotency_key`，查询时排除相同键对应的草稿，避免“自己和自己冲突”。模型不能传入任意 `draft_id` 隐藏其他占用。

## 4. 替代时段

### 4.1 默认配置

- 单次占用：30 分钟；
- 向后查找步频：30 分钟；
- 第一个候选：`requested_at + 30 分钟`；
- 搜索方向：只向未来；
- 查询边界：默认原请求所在当地日期的 `23:59`；
- 最多返回：3 个候选项。

例如原请求 `18:00–18:30` 与 `S001` 冲突：

1. 检查 `18:30–19:00`，与已有区间首尾相接，可用；
2. 如果该时段被占用，再检查 `19:00–19:30`；
3. 再检查 `19:30–20:00`；
4. 返回按时间排序的最多 3 个可用候选。

对于非整点请求，步频仍从原请求开始。例如 `18:15` 冲突后依次检查 `18:45`、`19:15`、`19:45`。

### 4.2 候选校验

每个候选必须同时满足：

1. 开始时间不晚于 `search_until`；
2. 与所有正式排期不重叠；
3. 与其他有效草稿不重叠；
4. 不与当前请求自身草稿重叠；
5. 返回时标记 `requires_confirmation=true`。

工具只返回候选，不替用户确认最终时间。

## 5. 数据持久化

### 5.1 `schedule_entries`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schedule_id` | TEXT，主键 | 正式排期 ID |
| `asset_id` | TEXT | 素材 ID |
| `scope_id` | TEXT | 当前固定为 `global` |
| `publish_at_utc` | TEXT | UTC 开始时间 |
| `publish_at_local` | TEXT | 本地开始时间 |
| `duration_minutes` | INTEGER | 占用分钟数，默认 30 |
| `timezone` | TEXT | IANA 时区 |
| `status` | TEXT | 排期状态 |
| `created_at` | TEXT | 创建时间 |

### 5.2 `drafts`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | TEXT，主键 | 草稿 ID |
| `idempotency_key` | TEXT，唯一 | 请求幂等键 |
| `asset_id` | TEXT | 素材 ID |
| `scope_id` | TEXT | 当前固定为 `global` |
| `requested_at_utc` | TEXT | 用户原始要求 |
| `requested_at_local` | TEXT | 本地原始要求 |
| `proposed_at_utc` | TEXT | 建议时段 UTC 开始 |
| `proposed_at_local` | TEXT | 建议时段本地开始 |
| `duration_minutes` | INTEGER | 占用分钟数，默认 30 |
| `timezone` | TEXT | IANA 时区 |
| `draft_status` | TEXT | 草稿状态 |

数据库初始化会把旧的“只有开始时刻”表增加 `duration_minutes` 列，已有记录默认迁移为 30 分钟。当前数据库版本为 `PRAGMA user_version=3`。

## 6. 工具输入

模型可见参数：

```json
{
  "asset_id": "M01",
  "requested_at": "2026-11-01T18:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "search_until": "2026-11-01T23:59:00+08:00"
}
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `asset_id` | string | 是 | 待排期素材 |
| `requested_at` | string | 是 | RFC 3339，分钟精度 |
| `timezone` | string | 是 | 合法 IANA 时区 |
| `search_until` | string | 否 | 替代候选查询截止 |

运行时注入参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `idempotency_key` | 必填 | 排除当前请求自身草稿 |
| `scope_id` | `global` | 排期范围 |
| `occupancy_minutes` | `30` | 新请求占用时长 |
| `slot_step_minutes` | `30` | 冲突后的向后查找步频 |
| `max_alternatives` | `3` | 最多候选数 |

## 7. 工具输出

```json
{
  "status": "CONFLICT_WITH_ALTERNATIVE",
  "requested_at": "2026-11-01T18:00:00+08:00",
  "requested_at_utc": "2026-11-01T10:00:00Z",
  "timezone": "Asia/Shanghai",
  "scope_id": "global",
  "conflicts": [
    {
      "record_type": "SCHEDULE",
      "record_id": "S001",
      "asset_id": "M00",
      "publish_at": "2026-11-01T18:00:00+08:00",
      "publish_at_utc": "2026-11-01T10:00:00Z",
      "occupied_until": "2026-11-01T18:30:00+08:00",
      "occupied_until_utc": "2026-11-01T10:30:00Z",
      "duration_minutes": 30,
      "status": "CONFIRMED",
      "occupancy": "HARD"
    }
  ],
  "alternatives": [
    {
      "publish_at": "2026-11-01T18:30:00+08:00",
      "publish_at_utc": "2026-11-01T10:30:00Z",
      "occupied_until": "2026-11-01T19:00:00+08:00",
      "occupied_until_utc": "2026-11-01T11:00:00Z",
      "duration_minutes": 30,
      "timezone": "Asia/Shanghai",
      "requires_confirmation": true
    }
  ],
  "error": null,
  "meta": {
    "contract_version": "0.2",
    "query_mode": "interval_overlap",
    "occupancy_minutes": 30,
    "slot_step_minutes": 30,
    "max_alternatives": 3,
    "search_until": "2026-11-01T23:59:00+08:00"
  }
}
```

## 8. 状态定义

| 状态 | 含义 | 后续动作 |
| --- | --- | --- |
| `AVAILABLE` | 原请求区间没有重叠 | 可保存原始时段草稿 |
| `CONFLICT_WITH_ALTERNATIVE` | 原区间冲突，但存在可用候选 | 建议候选并标记待确认 |
| `NO_FEASIBLE_SLOT` | 原区间冲突且无候选 | 返回 `INCOMPLETE` |
| `INVALID_ARGUMENT` | 参数不合法 | 请求修正，不保存 |
| `STORAGE_ERROR` | 数据源不可用 | 可有限重试，最终失败 |

## 9. 查询执行流程

1. 校验 ID、时间、时区和查询边界。
2. 将请求开始时间转换为 UTC。
3. 计算请求结束时间：`requested_at + occupancy_minutes`。
4. 批量读取搜索范围内可能与本区间重叠的正式排期和有效草稿。
5. 排除当前幂等键对应草稿。
6. 用半开区间公式判断原请求是否冲突。
7. 无冲突时返回 `AVAILABLE`。
8. 有冲突时，从 `requested_at + slot_step_minutes` 开始生成候选。
9. 对每个候选执行相同区间重叠检查。
10. 返回最多 3 个候选，或返回 `NO_FEASIBLE_SLOT`。

## 10. 查询与保存的事务边界

查询不是预占。查询后到保存前，其他请求可能占用候选时段，因此 `save_schedule_draft` 必须在 `BEGIN IMMEDIATE` 事务内：

1. 使用相同的 30 分钟区间规则；
2. 重新检查正式排期；
3. 重新检查其他有效草稿；
4. 排除当前幂等键；
5. 仅在无重叠时写入草稿；
6. 冲突时回滚并返回 `SLOT_ALREADY_OCCUPIED`。

## 11. 错误处理

| 状态 | 错误码 | 处理 |
| --- | --- | --- |
| `INVALID_ARGUMENT` | `INVALID_REQUESTED_AT` | 修正请求时间 |
| `INVALID_ARGUMENT` | `INVALID_TIMEZONE` | 修正时区 |
| `INVALID_ARGUMENT` | `INVALID_SEARCH_WINDOW` | 修正查询截止 |
| `STORAGE_ERROR` | `SCHEDULE_STORAGE_UNAVAILABLE` | 最多重试一次 |

## 12. 测试基线

1. `18:00–18:30` 与 `18:00–18:30` 冲突。
2. `17:45–18:15` 与 `18:00–18:30` 冲突。
3. `18:15–18:45` 与 `18:00–18:30` 冲突。
4. `18:30–19:00` 与 `18:00–18:30` 不冲突。
5. 不同日期或不同 UTC 时区表示按 UTC 判断。
6. `18:00` 冲突后默认候选从 `18:30` 开始。
7. 非整点 `18:15` 冲突后候选为 `18:45`、`19:15` 等。
8. 候选最多返回 3 个并保持时间顺序。
9. 有效草稿和确认排期都参与区间冲突。
10. 保存事务能拒绝“查询时可用、保存前被占用”的重叠区间。
11. 旧数据库自动增加 `duration_minutes=30`。
12. 查询工具本身不写入数据库。
