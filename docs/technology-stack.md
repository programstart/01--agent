# 第 1 题：技术栈与框架取舍

> 文档版本：0.2
>
> 更新日期：2026-11-01
>
> 状态：已安装并用于第一阶段、规则引擎 v2 与 30 分钟区间排期实现
>
> 相关文档：[需求说明](01-requirements.md) · [方案说明](02-solution.md) · [数据类型](data-model.md) · [Agent 架构](agent-architecture.md) · [规则引擎 v2](rule-engine-v2.md)

## 1. 最终技术栈

| 领域 | 选择 | 职责 |
| --- | --- | --- |
| 语言 | Python 3.12.4 | 业务逻辑、工具和 CLI |
| Agent 工作流 | LangGraph 1.2.19 | ReAct 循环、状态、条件路由和工具执行 |
| 工具与模型消息抽象 | LangChain Core 1.7.3 | 定义可调用工具和模型消息 |
| 数据校验 | Pydantic 2.14.2 | 工具参数、状态和输出结构校验 |
| 数据库 | SQLite | 素材、规则、正式排期和草稿 |
| 数据库访问 | Python `sqlite3` | 参数化查询和事务 |
| 模型调用 | 支持结构化输出的 Chat 模型适配器 | ReAct 动作选择和隔离的语义规则评估 |
| 离线测试模型 | 自定义 Mock Model Adapter | 确定性的工具流程和故障测试 |
| CLI | Python `argparse` | 可复制的运行入口 |
| 测试 | pytest 9.2.0 | 工具、图和验收场景测试 |
| Trace | 结构化 JSON 文件 | 保存公开决策和工具执行记录 |
| 配置 | 环境变量、`tzdata 2026.6` 与 `.env.example` | 模型地址、模型名称、30 分钟占用、30 分钟替代步频、数据库路径和 IANA 时区 |
| 依赖声明 | `requirements.txt` | 固定运行依赖版本 |

所有直接依赖已固定到 `requirements.txt`。模型适配层使用 LangChain OpenAI 1.7.3。结构化规则由程序执行，自然语言规则在模型可用时使用同一模型供应商的独立结构化审核调用，且其输出必须经过程序证据校验。

## 2. LangGraph 使用方式

项目选择 LangGraph 实现工作流，采用以下组合：

- 使用 LangGraph `StateGraph`、条件边和显式结束节点实现 ReAct 主循环；
- 使用自定义 Graph State 扩展素材、审核、排期和草稿字段；
- 四个业务工具由统一 `ToolExecutor` 执行，LangGraph 条件边负责选择和执行路由；
- 使用 Tool Wrapper 记录调用参数、结果和错误；
- 使用自定义 Guard 检查模型步骤、工具调用次数和无进展；
- 使用 LangGraph 条件和结束路由；
- 使用回调或事件处理转换成题目要求的 Trace；
- 不记录模型内部思维过程，只记录简要决策依据。

整体循环：

```text
模型选择动作
   ↓
LangGraph 路由到合法工具
   ↓
执行工具
   ↓
观察结果写回 Graph State
   ↓
Guard 检查预算和进展
   ↓
模型选择下一步或结束
```

该循环必须体现模型根据工具结果改变下一步，而不是只执行固定流水线。

所选 LangGraph 1.2.19 支持以下扩展点：

- 自定义 State Schema；
- 动态系统提示词；
- 动态可用工具集合；
- 工具调用前后拦截；
- 回调或事件输出。

实际实现选择 `StateGraph`，因为本题需要把业务 RunState、动态合法动作、预算和无进展检测直接映射到图节点与条件路由。

## 3. 为什么选择 LangGraph

选择 LangGraph 的原因：

1. 对框架更熟悉，能够降低第一阶段的实现和调试风险。
2. 状态和条件路由可以显式表达 Agent 决策过程。
3. 工具调用、循环和结束路径可以集中管理。
4. LangGraph 能配合自定义工具包装、回调、Guard 和 Mock，适合题目要求。
5. 后续增加草稿确认、重规划和更多工具时，可以在现有图上扩展。

该选择不以“使用框架”本身作为加分理由。业务判断仍然由确定性的工具完成，模型负责在合法动作之间选择。

## 4. 设计取舍

### 收益

- 减少 Agent 主循环、消息传递和工具执行方面的重复实现；
- 使用显式状态和路由，便于调试与评审；
- 让正常、冲突、规则阻断和失败路径更容易记录；
- 可以复用 LangGraph 的回调和运行事件；
- 对熟悉该框架的开发者，整体开发速度更快。

### 成本

- 引入 LangGraph、LangChain Core 和模型适配依赖；
- 需要定义项目自己的 Graph State；
- 框架默认配置不能替代业务 Guard 和停止条件；
- 框架升级可能影响 API 或执行行为，因此必须固定版本；
- 需要把框架事件映射成题目要求的 Trace，不能直接展示框架内部信息。

### 控制方式

- 只保留单 Agent、单图和四个业务工具；
- 不使用多 Agent、子图或生产级分布式 Checkpointer；
- 业务步骤和工具预算由 `RunState` 独立计数；
- 审核、时段、草稿状态由本项目 DTO 管理；
- 不依赖框架内部隐藏提示词或默认提示格式；
- 工具与数据访问不直接暴露给模型。

## 5. 总体架构

```text
CLI
  ↓
Request Normalizer
  ↓
LangGraph ReAct Runtime
  ├── Model Adapter（真实模型 / Mock）
  ├── Graph State
  ├── Tool Wrapper
  ├── Guard / Stop Policy
  └── Trace Mapper
        ↓
  read_material
  check_audit_rules
  query_schedule_slots
  save_schedule_draft
        ↓
Repository Layer
        ↓
SQLite
```

## 6. 与题目要求的对应

| 题目要求 | 实现方式 |
| --- | --- |
| 模型选择动作 | LangGraph ReAct 主循环中的模型调用 |
| 执行工具 | LangGraph 路由到统一工具执行节点，调用四个确定性业务工具 |
| 观察结果 | 工具输出写入自定义 Graph State |
| 模型调整下一步 | 下一轮模型调用基于最新状态和观察 |
| 至少两条执行路径 | 正常草稿和冲突替代草稿路径 |
| 执行上限 | 自定义 `step_count` 和 `tool_call_count` Guard |
| 工具失败 | 结构化错误、有限重试和未完成状态 |
| 重复提交 | 草稿幂等键和数据库唯一约束 |
| 仅保存草稿 | 工具不写正式排期和发布状态 |

## 7. 第一阶段不使用

- React.js 或任何 Web 界面；
- TypeScript/Node Agent 后端；
- 多 Agent；
- 生产级部署；
- 分布式 Checkpointer；
- 真实发布平台；
- 向量数据库；
- 未固定的框架版本。

## 8. 实施时的版本记录要求

已记录实际运行版本：

- Python 3.12.4；
- LangGraph 1.2.19；
- LangChain Core 1.7.3；
- LangChain OpenAI 1.7.3；
- Pydantic 2.14.2；
- pytest 9.2.0；
- tzdata 2026.6；
- 模型名称与 API 地址由运行环境变量提供；
- 使用的 LangGraph 功能及其职责；
- Mock 与真实模型能力的边界。

`.env.example` 已列出可选变量且不包含真实密钥。离线 Mock 用于自动化测试；另使用用户提供的本地凭据完成了真实模型正常和冲突路径验证，凭据未写入项目。

## 9. 计划目录结构

```text
README.md
requirements.txt
.env.example
src/
  main.py
  agent/
    graph.py
    state.py
    routing.py
    guards.py
  models/
    client.py
    mock.py
  tools/
    material_read.py
    rule_check.py
    schedule_query.py
    draft_save.py
  repositories/
    materials.py
    rules.py
    schedules.py
    drafts.py
  db/
    connection.py
    schema.py
    seed.py
  tracing/
    writer.py
tests/
examples/
data/
docs/
```

实际目录可以在编码时按依赖规模微调，但工具、Agent 图、模型适配、数据访问和 Trace 的边界保持不变。
