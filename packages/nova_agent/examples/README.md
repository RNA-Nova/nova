# nova_agent 示例

本目录包含可直接在 Jupyter 中运行的示例 Notebook，覆盖 `Agent` 类、自定义工具、Hook、队列、事件流与工具验证等核心能力。

## 环境要求

- Python >= 3.9
- 已安装 `nova_agent` 与 `nova_ai`：

```bash
cd packages/nova_agent
poetry install
```

- 配置 API Key（示例默认使用 Volcengine）：

```bash
export VOLCENGINE_API_KEY="3b631f71-6bd6-464a-9abc-b0e8d19f25d7"
# 或
# export OPENAI_API_KEY=""  # 本地未设置，已注释
```

## Notebook 列表

| 文件 | 主题 |
|------|------|
| `01-quickstart.ipynb` | `Agent` 基本用法：创建、订阅事件、发起 prompt、查看状态 |
| `02-custom-tools.ipynb` | 继承 `AgentTool` 实现自定义工具并注册到 Agent |
| `03-hooks.ipynb` | `before_tool_call` / `after_tool_call` / `should_stop_after_turn` / `prepare_next_turn` 用法 |
| `04-steering-follow-up.ipynb` | `steer()` 中断运行、`follow_up()` 空闲后继续、队列模式切换 |
| `05-abort-continue.ipynb` | `abort()` 取消当前 run、`continue_()` 从队列或上下文继续 |
| `06-event-stream.ipynb` | 低层 `agent_loop` + `AgentEventStream` 用法，并附离线 mock stream 示例 |
| `07-tool-validation.ipynb` | 使用 `validate_tool_call` / `validate_tool_arguments`，可离线运行 |

## 运行方式

```bash
cd packages/nova_agent
jupyter lab examples/
```

打开任意 Notebook 后按顺序执行单元格即可。`07-tool-validation.ipynb` 与 `06-event-stream.ipynb` 中的 mock 部分不依赖真实 API Key，其余 Notebook 需要设置环境变量。
