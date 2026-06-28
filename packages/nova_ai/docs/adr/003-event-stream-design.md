# ADR-003: EventStream 为什么从 asyncio.Queue 改回直接握手模式

## 状态

已接受

## 背景

`EventStream` 是 `nova_ai` 流式处理的核心基础设施。生产者（`api_impls/` 中的协议实现）通过 `push()` 推送事件，消费者通过 `async for` 异步迭代消费事件。

TypeScript 原版使用**直接握手模式**（direct handoff）：

- 消费者等待时，生产者直接把事件交给消费者，不经过队列
- 消费者未等待时，事件入队缓冲
- `end()` 通过回调唤醒所有等待中的消费者

最初的 Python 移植版本改用了 `asyncio.Queue` + 60 秒超时轮询：

```python
# 旧实现（已废弃）
self._queue: asyncio.Queue[T] = asyncio.Queue()
event = await asyncio.wait_for(self._queue.get(), timeout=60)
```

这引入了多个问题，最终回退到与 TS 原版一致的直接握手模式。

## 决策

使用 `deque` + `asyncio.Future` 等待者列表的直接握手模式，不用 `asyncio.Queue`。

## 理由

### 1. 消除超时风险

旧实现中 `end()` 只设置 `_done = True`，不唤醒正在 `await queue.get()` 的消费者。如果消费者在 `end()` 被调用时正在等待，它会阻塞到 60 秒超时。

直接握手模式中 `end()` 给所有等待者的 Future `set_result(_END_SENTINEL)`，消费者立即被唤醒，**零延迟、零超时**。

### 2. 生产者消费者直接握手

```python
def push(self, event: T) -> None:
    # 如果有等待中的消费者，直接交付
    while self._waiting:
        waiter = self._waiting.popleft()
        if not waiter.done():
            waiter.set_result(event)
            return
    # 否则入队缓冲
    self._queue.append(event)
```

事件零拷贝传递，不需要线程安全包装（`call_soon_threadsafe`）。

### 3. 无事件循环依赖的实例化

旧实现在 `__init__` 中创建 `asyncio.Future()`，要求调用时必须有运行中的事件循环。直接握手模式把 Future 的创建延迟到 `result()` 或 `__anext__` 被调用时，**允许在同步代码中预创建 `EventStream`**。

### 4. 与 TS 原版行为一致

`nova_ai` 是 TypeScript 项目 `pi` 的 Python 移植。核心基础设施应保持行为一致，降低跨语言维护的认知负担。

## 设计细节

### 等待者清理

```python
waiter = loop.create_future()
self._waiting.append(waiter)
try:
    item = await waiter
    if item is _END_SENTINEL:
        raise StopAsyncIteration
    return item
finally:
    try:
        self._waiting.remove(waiter)
    except ValueError:
        pass
```

`finally` 确保 `CancelledError` 时 waiter 从 `_waiting` 中移除，避免内存泄漏和 `push/end` 操作已取消的 Future。

### 取消语义

`CancelledError` **必须原样抛出**，不能转成 `StopAsyncIteration`。否则外层 `asyncio.timeout()` 或 `task.cancel()` 的取消信号会被吞掉，调用方永远不知道迭代是被取消的。

```python
except asyncio.CancelledError:
    # 从 waiting 中清理后原样抛出
    self._waiting.remove(waiter)
    raise
```

### end() 兜底

如果 `end()` 被调用时没有完成事件触发过，也没有显式传 result，`result()` 不应永久挂起。设置 `RuntimeError` 异常：

```python
def end(self, result: Optional[R] = None, exc: Optional[BaseException] = None):
    # ...
    elif not self._final_result_set:
        self._set_exception(RuntimeError("EventStream ended without result"))
```

## 后果

- **正面**：零延迟交付、无超时风险、可在同步环境实例化、与 TS 行为一致
- **负面**：`deque` 不是线程安全的，但 `EventStream` 设计为单协程消费，这符合 asyncio 的协作式多任务模型

## 相关代码

- `src/nova_ai/streaming/event_stream.py`
- TypeScript 原版：`/root/pi/packages/ai/src/utils/event-stream.ts`
