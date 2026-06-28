# 测试指南

`nova_agent` 使用 `pytest` + `pytest-asyncio` 进行测试。

## 单元测试

不依赖真实模型，使用 mock stream function：

```bash
cd packages/nova_agent
PYTHONPATH=src python -m pytest tests/test_agent_loop.py tests/test_agent.py -q
```

## 集成测试

调用真实 DeepSeek 模型（通过 Volcengine Ark），需要设置环境变量：

```bash
export VOLCENGINE_API_KEY=xxx
PYTHONPATH=src python -m pytest tests/test_integration_agent.py -q
```

集成测试默认限定在两个模型：

- `deepseek-v4-flash-260425`
- `deepseek-v4-pro-260425`

## 跳过集成测试

```bash
PYTHONPATH=src python -m pytest tests/ -q -m "not integration"
```

## mock stream 要点

测试时常用自定义 `stream_fn`：

```python
async def stream_fn(model, context, options):
    return EventStream(...)
```

注意：
- abort / block 测试需要让 mock stream 在异常后仍返回 assistant 响应，否则 loop 会无限循环。
- 工具调用测试需要返回包含 `ToolCall` 的 `AssistantMessage`。

## 生成事件流日志

```bash
PYTHONPATH=src python tests/generate_event_flow_log.py
```

会更新 `tests/EVENT_FLOW_LOG.md`，记录各种场景下的事件顺序。
