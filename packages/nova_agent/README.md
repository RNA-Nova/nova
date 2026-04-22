# nova_agent / pi_agent

`nova_agent` 是 Nova monorepo 的底层 Agent 框架包，核心实现位于 `pi_agent` 目录。

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

## 主要依赖

- `nova-ai`（本地路径依赖）
- `mashumaro >= 3.0`
- `jsonschema ^4.0`
