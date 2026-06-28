# nova_agent

`nova_agent` 是 Nova monorepo 的底层 Agent 框架包。

## 职责

- **Agent 核心**：`Agent` 类维护状态、工具、消息队列与事件订阅。
- **异步循环**：`agent_loop()` 实现 "用户消息 -> LLM 流式响应 -> 工具调用 -> 工具结果 -> 下一轮" 的完整循环。
- **事件系统**：所有状态变更通过 `AgentEvent` 家族广播给订阅者。
- **工具校验**：基于 `jsonschema` 的工具参数校验与缓存。
- **信号控制**：`AbortSignal` 支持取消长时间运行的操作。

## 安装

```bash
cd packages/nova_agent
poetry install
```

## 运行测试

```bash
# 单元测试
pytest -m "not integration"

# 真实模型集成测试（需配置 VOLCENGINE_API_KEY）
pytest tests/test_integration_agent.py
```

## 示例

详见 [`examples/`](./examples) 目录下的 Jupyter Notebook：

- `01-quickstart.ipynb` — `Agent` 基本用法
- `02-custom-tools.ipynb` — 自定义工具
- `03-hooks.ipynb` — Hook 机制
- `04-steering-follow-up.ipynb` — Steering / Follow-up 队列
- `05-abort-continue.ipynb` — 中断与继续
- `06-event-stream.ipynb` — 低层事件流 + mock stream
- `07-tool-validation.ipynb` — 工具参数验证

## 主要依赖

- `nova-ai`（运行时 import）
- `pydantic ^2.0`
- `jsonschema ^4.0`
