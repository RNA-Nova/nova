# 开发日志

记录 `nova_agent` 的关键变更和设计演进。

## 2026-06

### Abort / Terminate 语义与 TS 对齐

- 修复 `Agent.abort()` 因 `AbortSignal.__bool__` 导致的失效问题。
- 移除 `tools.py` 中因 `signal.aborted` 和 immediate 错误强制 `terminate=True` 的逻辑。
- 现在仅当所有 tool result 显式设置 `terminate=True` 时才终止工具阶段。
- abort 后 loop 会继续一次 assistant turn，底层 stream 因 signal 返回 `stop_reason="aborted"` 后结束。
- 最终 `state.messages[-1].role == "assistant"`，与 TS 一致。

### 事件对象拷贝

- `agent_loop/loop.py` 对 `message_start` / `message_update` 使用 `model_copy()` 浅拷贝。
- 保证上层 listener 拿到的事件对象稳定，不受后续 mutate 影响。
- `nova_ai` provider 层仍保留 `deepcopy(output)`，处理 EventStream 缓冲场景。

### 集成测试扩展

- `tests/test_integration_agent.py` 扩展到 23 函数（46 用例，限定 v4 两个模型）。
- 覆盖 lifecycle、队列、工具、abort、hooks、continue 等场景。

### 文档同步

- 初始化 `docs/` 目录，包含架构设计、使用指南、ADR 等文档。
- 将本地文档同步到飞书知识库，建立层级结构。
