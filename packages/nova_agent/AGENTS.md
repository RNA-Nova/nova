<!-- AGENTS.md - nova_agent 包级指南 -->

# nova_agent —— Agent 核心框架包级指南

> 本文件面向 AI Coding Agent。只收录本包特有的职责、边界与生命周期契约；跨包通用规则见 `docs/crosscutting/`。

## 包职责与边界

`nova_agent` 是事件驱动的异步 Agent 内核，位于 `nova_protocol` / `nova_async` / `nova_ai` 之上、`nova_harness` 之下。

- 负责 run/turn/tool 状态机、转录循环、steering/follow-up 队列、工具执行、执行型 hook 和事件生产。
- `agent.py` 提供长期存在的 `Agent`；`run.py` 提供单次运行的 `AgentRun` 句柄与权威事件流。
- `agent_loop/` 是内部执行引擎，不再提供无生产消费方的低层 facade。
- `types/` 只放内聚的 Agent 公共运行时与 hook 类型；跨领域序列化词汇归 `nova_protocol`，取消原语归 `nova_async`，不得复制第二份。
- 模型服务代理由 `nova_gateway` / `nova_gateway_client` 独立承载；通用取消机制归 `nova_async`，Agent 私有的终态与运行状态不得上移。

通用取消与资源收尾原则见 `docs/crosscutting/concurrency.md`，测试通则见 `docs/crosscutting/testing-modeling.md`。本文件只定义这些原则在本包内的具体化。

## 生命周期参照

- 循环、消息、工具调度和高低层 API 以 pi `packages/agent` 为主要参照。
- provider/task 所有权参考 Codex：`core/src/client_common.rs` 的 `consumer_dropped`（正常终态与提前离开分野）、`core/src/tools/parallel.rs` 的完成优先与 `AbortOnDropHandle`（放弃时取消并 join）。
- run 的领域状态与中止终态参考 Codex `core/src/state/turn.rs` 的 `RunningTask` 和 `core/src/tasks/mod.rs` 的 `handle_task_abort`；通用建类判据只引用顶层并发标准，不在本包另造口径。
- 公平读写门是本包有意保留的并发增强，不机械退回 pi 的“遇 sequential 整批串行”。

## AgentRun 与事件模型【现状】

### 启动与 settle

`prompt()` / `continue_()` 是普通同步工厂，返回 `AgentRun`：

- 返回前完成 active run 登记、事件流创建、settle Task 创建和 `AbortSignal` 绑定；
- `run.task` 是完整 settle Task；`run.events` 是本次 run 的权威、单消费者事件流；`run.signal` 是本次取消信号；
- `run.abort()` 先协作取消，超过 100ms 未收尾则硬取消并告警；
- `await run` 等待完整 settle；`run.events.result()` 表示事件终态并返回本次新消息，两者不得混用。

不再需要 `start_prompt()` / `start_continuation()`、`agent.events`、`subscribe_terminal()` 或低层 facade。直接取消 Task 属于硬取消；首步前取消由 done callback 同步补齐终态并清理 active run。所有事件、失败处理和 settle 回调都绑定具体 `AgentRun`，旧 run 不得污染新 run。

### 事件生产与观察消费

`run.events` 包含正常完成、Provider 失败、abort、外部 Task 取消和首步前取消的完整事件序列，包括 `agent_end`。事件生产不等待任何观察者，不存在第二种控制面终态。

直接 SDK 使用者可经 `agent.subscribe(listener)` 长期观察多个 run。每个观察者独立消费、独立异常和取消；慢或失败的观察者不影响 run。Harness 不使用该适配器，而是直接消费 `run.events`。

需要“处理完成后才能继续”的干预（例如消息改写）使用显式 `on_message_end` 执行 hook，不混入观察订阅。观察型扩展和第三方消费者归 Harness 的独立工作队列，不归 Agent。

### Provider 收尾矩阵

`DoneEvent` 与 `ErrorEvent` 都属于协议上的正常终态；只有外部取消、流迭代异常、执行型 hook 异常等提前退出才叫 abandonment。

| 路径 | 允许动作 | 禁止动作 |
|---|---|---|
| 正常终态，driver 已结束 | 直接采用最终消息 | 不得再发取消 |
| 正常终态，driver 仍在清理 | 只 join；清理不响应时才硬 cancel driver，并保留原最终消息 | 不得 `abort()` 本请求，不得把消息改成 aborted |
| abandonment | 先 `abort()` 本请求 → 有界 join → 硬 cancel → 最多再交付一次调度 | 不得只发信号不 join，不得无界等待取消抵抗的 driver，不得让清理异常覆盖最终响应 |

100ms 只可作 driver 清理兜底，不是模型响应超时，也不得把正常终态改写成 aborted。实现位于 `agent_loop/response.py`，与回合调度分离。

### 终态事件矩阵

每个 run 私有记录 `turn_end_seen` / `agent_end_seen`（事件产生即登记，不等待观察者成功）与本次新消息列表。每类终态事件最多一次；失败处理按发生阶段补齐，不得重新生成一整套生命周期。

| 失败位置 | 允许的收尾 |
|---|---|
| 尚未到 `turn_end` | 可生成失败 assistant，并发送一次完整失败序列 |
| `turn_end` 执行型 hook 失败 | 不新建 assistant、不重发 `turn_end`，只用本次已有消息补一次 `agent_end` |
| `agent_end` 执行型 hook 失败 | 不再生成或重发外部事件；权威事件流中的本次终态仍有效 |

实现位于 `run.py` 的 `AgentRun`，与 `Agent` 的公共状态和观察消费解耦。

### 硬 abort 与任务作用域

watchdog 超时后强制取消 task，但仍须完成内部终态、一次 `run.events` 的 `agent_end`、task 清理和 `wait_for_idle()` 落定。100ms 只是清理预算，与 SSE idle 超时无关。

工具批与单次 `run_tool_call` 都在独立任务作用域内执行：先关事件闸，再 `close → cancel_all → 有界 wait`。取消抵抗的工具不得拖住父 run，迟到事件在工具作用域和 Agent run 身份两层被丢弃。

### 住所纪律

上述状态归 `AgentRun`，不新建 `nova_async.AgentRunManager`。`nova_async` 只提供通用取消原语；只有出现第二个真实消费者时，才抽通用 join/cancel 小件。

## 测试要求【现状】

- **provider 终态矩阵**：DoneEvent 与 ErrorEvent 各配 driver 已结束/仍在清理两例；正常终态不得 abort，abandonment 必须先 abort 再 join。取消抵抗的 driver 必须有硬取消后有界返回用例。
- **观察者隔离**：观察者慢、失败、退订均不改变 run 结果或 Provider 生命周期；每个观察者独立保序，不要求跨观察者交错顺序。
- **run 终态矩阵**：正常完成、失败、协作取消、外部 Task 取消与硬取消分别断言权威事件流中的终态恰好一次。`wait_for_idle()` 返回时 active run 已清理。
- **跨代与取消抵抗**：串行/并行工具吞取消后不得拖住下一代 run；工具批返回后的迟到事件必须为零；旧 run 不得清理新 run。
- **执行型 hook**：`on_message_end` 的干预保持顺序，并与观察事件分流。
- **转录与工具覆盖**：pending SystemMessage 的工具声明合并与重写；after hook 不计入工具耗时；`tool_execution_end` 完成序与 toolResult 源序分别断言。
- 竞态与中断一律用 Event 编排，禁止用 sleep 表达等待完成。默认套件密封，真实 provider 用例归 `tests/integration/`。

## 开发门禁

```bash
pixi run -e dev test-agent
pixi run -e dev pyright packages/nova_agent/src/nova_agent packages/nova_agent/tests
pixi run -e dev black --check packages/nova_agent/src packages/nova_agent/tests
pixi run -e dev isort --check-only packages/nova_agent/src packages/nova_agent/tests
pixi run -e dev test-agent-integration
```

上述契约均由默认非集成套件与真实 provider 集成测试共同守护。
