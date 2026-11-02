# 素材审核与发布排期 Agent

> 题号：1
>
> 题目：素材审核与发布排期 Agent
>
> 第一阶段状态：CLI 可运行，自动化测试和真实模型路径已验证

第一阶段实现一个基于 LangGraph 的受控 ReAct Agent：

```text
模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束
```

系统只保存排期草稿，不创建正式排期，也不执行真实发布。

规则审核采用统一 v2 引擎，同时支持结构化规则和自然语言语义规则。关键词或正则未命中不会被当作整体内容合规。

## 提交信息

| 字段 | 状态 |
| --- | --- |
| 题号 | 1 |
| GitHub URL | https://github.com/programstart/01--agent |
| 最终 commit SHA | 最终提交后在考试提交表单中填写 |
| 仓库可见性 | 按考试要求向指定评审账号开放 |
| 实际用时 | 约 4 小时 30 分钟 |

## 环境

- Python 3.11+
- Windows PowerShell 示例使用项目虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 离线运行

默认使用确定性 Mock，仅用于验证工具流程和验收路径，不代表真实模型决策效果。

```powershell
.\.venv\Scripts\python.exe run.py `
  --asset-id M01 `
  --requested-at "2026-11-01T18:00:00+08:00" `
  --timezone "Asia/Shanghai" `
  --model mock
```

每个发布请求默认占用 30 分钟。`M01 18:00` 对应 `18:00–18:30`，与预置排期重叠；系统按 30 分钟步频建议 `18:30–19:00`，生成待确认草稿，且 `publication_status` 为 `NOT_PUBLISHED`。

正常路径可将时间改为 `20:00`：

```powershell
.\.venv\Scripts\python.exe run.py `
  --asset-id M01 `
  --requested-at "2026-11-01T20:00:00+08:00" `
  --timezone "Asia/Shanghai" `
  --model mock
```

结果会输出到终端，Trace 默认写入 `examples/traces/<run_id>.json`。

## 排期时段配置

默认每个发布请求占用 30 分钟，冲突后也按 30 分钟步频向后查找。可以通过环境变量或 CLI 参数调整：

```powershell
$env:OCCUPANCY_MINUTES = "30"
$env:SLOT_STEP_MINUTES = "30"

# 等价 CLI 参数
--occupancy-minutes 30 --slot-step-minutes 30
```

## 真实模型

真实模型通过 LangChain ChatOpenAI 适配器接入，需要设置：

```powershell
$env:OPENAI_API_KEY = "<your-key>"
$env:OPENAI_MODEL = "<tool-calling-model>"
# 可选：兼容 OpenAI 接口的服务地址
$env:OPENAI_BASE_URL = "https://example.invalid/v1"
```

然后使用：

```powershell
.\.venv\Scripts\python.exe run.py `
  --asset-id M01 `
  --requested-at "2026-11-01T20:00:00+08:00" `
  --timezone "Asia/Shanghai" `
  --model live
```

真实模型调用需要可用的 API 凭据；未配置时请使用 Mock。

本次开发还在本地凭据可用时完成了真实模型正常路径、时段冲突路径、整体语义阻断和语义合规验证。实际输出与 Trace 保存在 `examples/06-live-normal/`、`07-live-conflict/`、`08-live-semantic-block/` 和 `09-live-semantic-pass/`。

本仓库不包含真实模型凭据；运行时凭据只允许通过环境变量或未跟踪的本地配置提供。

## 评审入口

建议按以下顺序评审：

1. [01-requirements.md](docs/01-requirements.md)：范围、状态和验收标准；
2. [02-solution.md](docs/02-solution.md)：LangGraph 调用流程、动态决策和技术取舍；
3. [Agent 整体架构](docs/agent-architecture.md)：组件边界和数据流；
4. [03-evaluation.md](docs/03-evaluation.md)：测试、实际结果、失败和限制；
5. [examples/README.md](examples/README.md)：九组输入、输出与 Trace；
6. [04-ai-usage.md](docs/04-ai-usage.md)：AI 使用范围和人工待办；
7. [05-retrospective.md](docs/05-retrospective.md)：关键问题、学习收获和下一步。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

当前结果：`30 passed`。覆盖正常、30 分钟区间冲突、首尾相接、非整点 30 分钟步频、事务内区间冲突、资料不足、结构化规则阻断、自然语言语义阻断、语义不确定、遗漏/重复/未知规则、伪造证据、旧数据库迁移、保存失败、执行上限、无进展停止和 CLI 入口。

## 交付材料

- [src/](src/)：Agent、LangGraph 图、工具、Repository、CLI
- [tests/](tests/)：自动化测试
- [examples/](examples/)：[九组实际输入、输出和 Trace](examples/README.md)，包含四条真实模型路径
- [data/](data/)：运行后生成的本地合成数据库，数据库文件被 Git 忽略
- [docs/](docs/)：[需求](docs/01-requirements.md)、[方案](docs/02-solution.md)、[评估](docs/03-evaluation.md)、[AI 使用](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)

补充设计文档：

- [数据类型](docs/data-model.md)
- [技术栈](docs/technology-stack.md)
- [Agent 架构](docs/agent-architecture.md)
- [规则引擎 v2](docs/rule-engine-v2.md)
- 四个工具契约位于 `docs/` 下对应的 Markdown 文件

## 运行结果说明

- `READY`：草稿符合用户原始要求且无额外待处理项；
- `PENDING_CONFIRMATION`：使用了替代时段或存在非阻断风险；
- `SAVED`：草稿事务提交成功；
- `EXISTING`：相同幂等请求复用了原草稿；
- `STORAGE_ERROR`：草稿未保存，不能返回成功；
- `NOT_PUBLISHED`：第一阶段固定状态。

`READY` 不是正式排期确认，更不是发布。完整限制见 [03-evaluation.md](docs/03-evaluation.md)。
