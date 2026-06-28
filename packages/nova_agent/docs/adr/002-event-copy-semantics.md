# ADR-002：事件对象拷贝语义

## 状态

已采纳，已实现。

## 背景

`nova_ai` provider 层在流式过程中会对 `output` 做 `deepcopy`，保证每个事件的 `partial` 是快照。

但 `nova_agent.agent_loop` 原先在转发 provider 事件时，直接 emit 原对象，导致：

- 上层 listener 拿到的事件对象可能被后续内部 mutate 覆盖。
- 特别是 `message_update` 事件，如果 listener 消费慢，所有事件的 `message` 都会变成最终状态。

## 决策

在 `agent_loop/loop.py` 的 `_stream_assistant_response` 中：

- `message_start` 使用 `partial_message.model_copy()`。
- `message_update` 使用 `partial_message.model_copy()`。
- `message_end` 使用原始 final_message（来自 provider 的 deepcopy，本身已是快照）。

## 结果

- Agent 层 listener 拿到的事件对象稳定。
- 与 TS `agent-loop.ts` 中 `{ ...partialMessage }` 的浅拷贝语义一致。
- Provider 层保留深拷贝以处理自己的 EventStream 缓冲问题。

## 注意事项

`model_copy()` 是浅拷贝，事件 `message` 的内部字段（如 `content` 列表）仍与 provider 快照共享。由于 provider 不再 mutate 已发出的 deepcopy，这不会导致问题。
