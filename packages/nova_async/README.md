# nova_async

取消/等待机制族的家（结构标准 `docs/crosscutting/structure-modeling.md` §二
升格出口的落地）：词汇在 `nova_protocol`（`AbortSignal` / `AbortController` /
`AbortedError` / `is_aborted`——被契约注解锚定，留枢纽），机制在这里。

成员：

- `abort.py`——可中断等待原语族：`AbortScope` / `await_with_abort`（anyio
  CancelScope 桥的真杀执行层）、`await_with_signal`（asyncio 原生竞速型）、
  `abortable_sleep` / `any_signal` / `operation_signal`；
- `task_tracker.py`——`TaskTracker` 属主级在途任务台账。

纪律：可中断等待原语单点归本族，禁第三份实现
（`docs/crosscutting/concurrency.md` §二）。
