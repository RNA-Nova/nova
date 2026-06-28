# ADR-001：Abort / Terminate 语义与 TS 对齐

## 状态

已采纳，已实现。

## 背景

Python 版 `nova_agent` 最初对 abort 的处理比 TypeScript 版更激进：

- 一旦 `signal.aborted`，工具执行阶段直接设置 `terminate=True`。
- immediate 错误结果也手动设置 `terminate=True`。

这导致 abort 后 `state.messages` 常以 `toolResult` 结尾，而 TS 版会以 `assistant` 消息结尾。

## 决策

将 Python 的 `should_terminate_tool_batch` 改为与 TS 一致：

```python
def _should_terminate_tool_batch(finalized_calls):
    return len(finalized_calls) > 0 and all(
        getattr(finalized.result, "terminate", None) is True
        for finalized in finalized_calls
    )
```

- 不再因 `signal.aborted` 强制 `terminate=True`。
- immediate 错误结果不再手动设置 `terminate=True`。
- abort 后 loop 继续尝试一次 assistant turn，provider stream 因 signal 返回 `stop_reason="aborted"`，然后正常结束。

## 结果

- abort 后最终消息角色为 `assistant`。
- 单元测试和集成测试全部通过。
- 与 TS 行为保持一致。

## 影响

mock stream 测试需要确保 abort/block 后返回后续 assistant 响应，否则会无限循环。
