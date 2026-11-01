# nova_async

取消/等待机制族的家（结构标准 `docs/crosscutting/structure-modeling.md` §二
升格出口的落地）：词汇在 `nova_protocol`（`AbortSignal` / `AbortController` /
`AbortedError` / `is_aborted`——被契约注解锚定，留枢纽），机制在这里。

成员：

- `abort.py`——可中断等待原语族：`AbortScope` / `await_with_abort`（anyio
  CancelScope 桥的真杀执行层）、`await_with_signal`（asyncio 原生竞速型）、
  `abortable_sleep` / `any_signal` / `operation_signal`；
- `task_tracker.py`——`TaskTracker` 属主级在途任务台账（`async with`
  三段式收摊；`cancel_all()`/`wait()` 跳过当前任务）；
- `keyed_lock.py`——per-key 编排锁：`KeyedLockFactory` 协议 +
  `in_memory_keyed_locks` 进程内实现 + `default_keyed_locks` 兜底
  （文件锁 adapter 住 `nova_harness/config/auth/refresh_locks.py`）；
- `backoff.py`——`backoff_delay_ms` 指数退避序列（无状态函数，
  down-only jitter；可中断性归 `abortable_sleep`）；
- `deadline.py`——`Deadline` 单调时钟预算值对象（`remaining_ms` /
  `remaining_s` / `expired`；具体预算策略留消费方）。

纪律：可中断等待原语单点归本族，禁第三份实现
（`docs/crosscutting/concurrency.md` §二）。
