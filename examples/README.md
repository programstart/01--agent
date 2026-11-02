# 第一阶段运行示例

所有示例使用合成 SQLite 数据。01—05 使用离线 Mock 验证确定性业务路径；06—09 使用真实对话模型验证 ReAct 决策和语义规则。

自动化测试结果见 [test-results.txt](test-results.txt)。

| 场景 | 输入 | 实际输出 | Trace | 结果 |
| --- | --- | --- | --- | --- |
| 正常保存草稿 | [input.json](01-normal/input.json) | [output.json](01-normal/output.json) | [trace.json](01-normal/trace.json) | 20:00 草稿 `READY`，未发布 |
| 时间冲突 | [input.json](02-conflict/input.json) | [output.json](02-conflict/output.json) | [trace.json](02-conflict/trace.json) | 原要求 18:00 保留，30 分钟占用冲突后建议 18:30，待确认 |
| 素材资料不足 | [input.json](03-missing-data/input.json) | [output.json](03-missing-data/output.json) | [trace.json](03-missing-data/trace.json) | 缺少标题，不保存草稿 |
| 规则阻断 | [input.json](04-rule-blocked/input.json) | [output.json](04-rule-blocked/output.json) | [trace.json](04-rule-blocked/trace.json) | 命中禁止产品承诺规则，不排期 |
| 草稿保存失败 | [input.json](05-save-failure/input.json) | [output.json](05-save-failure/output.json) | [trace.json](05-save-failure/trace.json) | 明确返回 `STORAGE_ERROR`，不保存 |
| 真实模型正常路径 | [input.json](06-live-normal/input.json) | [output.json](06-live-normal/output.json) | [trace.json](06-live-normal/trace.json) | 模型完成四工具循环并显式 `finish` |
| 真实模型冲突路径 | [input.json](07-live-conflict/input.json) | [output.json](07-live-conflict/output.json) | [trace.json](07-live-conflict/trace.json) | 模型根据观察改选 18:30–19:00，保存待确认草稿 |
| 真实模型语义阻断 | [input.json](08-live-semantic-block/input.json) | [output.json](08-live-semantic-block/output.json) | [trace.json](08-live-semantic-block/trace.json) | 无原禁用词但整体暗示保本，规则 R006 阻断 |
| 真实模型语义合规 | [input.json](09-live-semantic-pass/input.json) | [output.json](09-live-semantic-pass/output.json) | [trace.json](09-live-semantic-pass/trace.json) | 明确提示市场波动，语义规则通过并保存草稿 |

## 重新生成

```powershell
.\.venv\Scripts\python.exe scripts\generate_examples.py
```

生成脚本会为 Mock 场景创建独立的临时 SQLite 数据库，运行真实 CLI 入口和 LangGraph 图，保存输出与 Trace，然后清除临时数据库。

真实模型场景 06—09 使用本地环境中的模型凭据运行，凭据没有写入项目或 Trace。

`output.json` 中的 `trace_path` 指向本次运行生成的原始 Trace 文件；`trace.json` 是稳定命名的同名副本，便于文档引用。
