# Nova 并发与取消建模标准

> 坐标：横切·并发与取消（体系总图与状态三档约定见 `docs/README.md`）。

> 本页管**等待的治理**：取消原语族、token 树所有权、超时治理三层、死因分型、取消竞速裁决、任务台账。类型的住所与序列化归 `docs/crosscutting/data-modeling.md`；诊断分类学（kind / 警告 / 持久化表）归 `docs/crosscutting/diagnostics-modeling.md`；模块与包的住所归 `docs/crosscutting/structure-modeling.md`；测试细则归 `docs/crosscutting/testing-modeling.md`。
> 状态：取消原语与机制件【现状】；Agent run 与 proxy 的所有权接入及回归覆盖【现状】；Agent 终态与权威事件流、失败监督与有界收尾【现状】；通用建类判据即日起作为设计门禁，其余编排层闭环【待补】。
> 测试要求：见末节。

**总纲**：每个等待都要回答三问——**能叫停吗、叫停后收尸吗、死了知道怎么死的吗**。取消是词汇不是形状：取消语义经 `AbortSignal`/`AbortedError`/终态事件流动，不在数据形状上挂 token 字段（`docs/crosscutting/data-modeling.md` §七，本页引用不重复其三海拔模型）。

## 一、三平台取消模型（设计依据存档）

- **codex（Rust/tokio）**：`select!` 竞速 + drop 触发 abort/通知；协作取消仍须各等待点监听 token，领域清理与终态发布由属主显式完成，**不是编译器保证异步收尸**；`async-utils` 一机制一 crate（`or_cancel`）；`ExecExpiration` 死因分型；**平台优先**（token/台账来自 tokio-util，自建只补空缺）。
- **TS（pi）**：`AbortSignal` 递进 fetch 内部——平台 HTTP 原语自带急停；但 JS 无结构化任务取消，非 fetch 场景只能往数据形状挂 token 字段（`AuthPrompt.signal` 病例）。
- **Python（我们）**：SDK 无请求级取消句柄（平台税）；执行层经 `AbortScope`（anyio `CancelScope` 的桥）达成**真杀**，词汇层仍自家 `AbortSignal`（TS 对齐）——沟通用 token、执行用 scope。

## 二、取消原语族（单点清单）

| 件 | 住所 | 职责 |
|---|---|---|
| `AbortSignal` / `AbortController` / `AbortedError` / `is_aborted` | `nova_async/signal.py`（由 `nova_protocol/signal.py` 迁入，已完成） | 取消词汇（TS 对齐；与等待机制同包，不再锚定数据枢纽） |
| `AbortScope` / `await_with_abort` | `nova_async/abort.py` | **真杀执行层**（anyio `CancelScope` 的桥）：signal 触发即取消 scope，块内一切等待（connect→TTFB→逐事件→锁等待）真死；scope 在块边界吞取消，归因在块后翻译（`raise_if_aborted`）；**清理段必须在块外**（level-triggered 语义下块内 await 会持续抛取消）。`await_with_abort` 为一次性糖 |
| `abortable_sleep` / `any_signal` / `operation_signal` / `await_with_signal` | `nova_async/abort.py` | 可中断退避 / 信号组合 / 操作内信号兜底 / asyncio 原生竞速型可中断等待 |
| `TaskTracker` | `nova_async/task_tracker.py` | 属主级动态任务批：`close()` 落闸 → `cancel_all()` 举旗 → `wait()` 排水；`close/wait` 对位 tokio-util `TaskTracker`，`cancel_all` 是 Nova 为收摊补的硬地板，`async with` 把三段固定组合；单 task 的 drop-abort 才是 `AbortOnDropHandle`，两者不得混同。`cancel_all()`/`wait()` 跳过当前任务（台账内任务可安全发起收摊——等自己=死锁由实现消除） |
| `retry_provider_request` | `nova_ai/api_impls/_shared/retry.py` | 传输重试策略（退避序列走 `backoff_delay_ms`，退避可被 signal 打断；取消的执行层归 `AbortScope`——调用方把 retry 段整个包进 scope） |
| `KeyedLockFactory` / `in_memory_keyed_locks` / `default_keyed_locks` | `nova_async/keyed_lock.py`（文件锁 adapter 留 `nova_harness/config/auth/refresh_locks.py`） | per-provider 编排锁：同 provider 的 refresh/logout/login 串行（防轮转 token 双刷、防刷新与登出串号），不同 provider 互不阻塞 |
| `backoff_delay_ms` | `nova_async/backoff.py` | 时间族·退避序列：无状态函数（attempt 由消费方持有）；jitter 取 pi/OpenAI SDK 的 down-only（与 codex 对称 ±10% 有意分歧——传输层镜像 SDK 语义优先）；可中断性归 `abortable_sleep`，本件不掺 signal |

**执行层单轨**：一切可取消的等待（网络请求、登录流程、auth 解析、refresh 的 provider 操作、流式消费段、编排锁等待）统一用 `AbortScope`/`await_with_abort`（真杀）。`CredentialStore` 已同步化（不在取消面），不存在第二种取消原语。**清理段（关流/关 client）一律在 scope 块外**。

纪律：原语**禁第三份实现**（`docs/crosscutting/data-modeling.md` §七）；`CancelledError` 只许出现在台账硬地板（`cancel_all` 收摊语义），用户取消一律 `AbortedError`——两词汇永不混用。

## 三、取消的所有权与中止流水线

- **token 树**：session → turn → tool 派生（子 controller / `AbortSignal.any` 组合）；父死子随，子死不及父。
- **两阶段中止**：优雅取消走 signal → `AbortedError`（允许收尾路径执行）；收摊掀桌走 `TaskTracker.cancel_all()`（关停路径的硬地板）。
- **收尾不发布**：取消/超时路径的流收尾只补块闭合（end 事件不重不漏）与终态事件，**不发布 `DoneEvent`**。
- **先落盘后发声**：持久化顺序契约归 `docs/crosscutting/diagnostics-modeling.md` §六（引用不重复）。

### 任务作用域与代际

任务按生命周期归属，不按包名寻找总账：session 级后台任务归会话属主；run 级任务归一次 prompt/continue 的属主；request 级 provider/transport 归单次请求；tool batch 级工具与 update 归当前工具批。子作用域必须先于父作用域收尾，不要求所有地方共用一本 `TaskTracker`。

### 概念分件与建类判据

“任务”不是单一建模单位。以下五件事可以同居一个属主，但不得因为都涉及 task 就合成全局管理器：

| 概念 | 只负责 | 不得冒充 |
|---|---|---|
| 执行句柄（`asyncio.Task` / JoinHandle） | 等待、强制取消、观察执行结束 | 领域终态 |
| 取消作用域（`AbortController` / token 树） | 取消传播与作用域边界 | 任务登记处 |
| 动态任务批（`TaskTracker`） | `spawn / close / cancel_all / wait` | run 代际、消息归属、业务终态机 |
| 领域运行实例（Agent run、provider response、单次工具调用） | 身份、上下文、终态裁决、领域清理 | 通用任务容器 |
| 等待登记处（pending RPC / approval / callback） | 按 ID 关联请求与等待者 | 必然拥有执行 task 的总账 |

建类按新增不变量判断，不按“这里出现了 task”判断：

- 静态、词法作用域内的并发直接 `await` / `TaskGroup`，不建类；
- 单个后台 task 且无附加领域状态，由现有属主直接持有 task 与取消句柄；
- 数量动态、语义同质的任务批复用 `TaskTracker`，不换个领域名再包一层 `XxxTaskManager`；
- 只有出现 task 之外的新不变量——run 身份/代际、终态恰好一次、完成与取消竞速、领域上下文或专门清理——才建立 `AgentRun` / `ResponseStream` 式领域类型，名字表达领域而非“管理 task”；
- `nova_async` 只收语义跨领域一致、有真实复用者的机制件，不收 Agent、Provider 或 RPC 的业务状态。

### 生命周期通用不变量

- **接受先于返回**：公开启动接口返回“已接受”前必须完成运行登记；若必须先返回调度句柄，则另给明确的 started 等待原语。调用方不得靠 `sleep(0)` 猜启动是否发生。
- **终态分三层**：内部终态归约、权威事件流终态、观察消费者送达是三件事。每个已接受的运行必须恰好有一个内部终态和一次权威事件流终态；观察消费者独立执行，不得改变前两层，也不得用第二种控制面概念重复表达同一终态。
- **后台失败有归宿**：运行期必须有人观察后台 task 的失败，并传给调用者、使所属作用域失败或进入受控降级；shutdown 时的 `wait()` 不等于运行期监督，禁止静默 detach。
- **关闭与登记互斥**：`close()` 与新任务登记必须共用临界区或等价的防起跑机制；关闭顺序统一为停止接纳 → 取消子作用域 → 有界排水 → 必要时硬取消 → 资源/领域清理 → 权威事件流终态发布 → 登记清理与 idle。

provider 管自己的资源，消费者管消费关系：正常完成须确认 driver 收尾；外部取消、监听器异常等提前退出（abandonment）时，消费者先通知 provider 清理，再 join，无响应时才硬取消。每次 run 都有独立身份——旧 run 不得清理或污染新 run；被 abort/replace 接管的 run 不再走正常完成。

### 分层终态与排水

`result()` 返回 ≠ driver 完成 ≠ transport 关闭 ≠ 子任务全部排水 ≠ `agent_end` ≠ run idle。`close()` 只停止接纳新任务，不等于取消；`cancel_all()` 只发起取消，不等于收尾；`wait()` 才是排水。close 后晚到的 update/future 不得起跑。

终态线性化为：provider 收尾 → 工具批收尾 → 终态消息与内部终态归约 → 权威事件流终态发布 → 按 run 身份清理 → 公开 idle。观察消费者在流外独立收敛，不参与 Agent 的关键路径；硬取消也不得缺内部终态、权威事件流终态或完整 settle。

【现状】工具 update observer 异常转成工具错误结果；观察者异常不影响 run。sequential 临界区为 execute + update 排干 + after（终态产生），`tool_execution_end` 与 toolResult 通知在门外。`prompt()` / `continue_()` 同步登记并返回 `AgentRun`，无需再包 `asyncio.create_task()`。abort 先协作取消，超过 100ms 未收尾则硬取消并告警，不把清理超时伪装成全部成功。

Agent 专属的终态、观察订阅与 hard abort 契约见 `packages/nova_agent/AGENTS.md`【现状】。本页只保留跨组件的取消与资源收尾原则，不收录单个包的内部状态机。

## 四、store 的同步引擎纪律（编排永远不进引擎）

**store 是同步引擎，manager 是 async 编排——编排（网络、去重、复查、重试、锁内回调）永远不许漏进引擎。** 同步无 await 点 ⇒ 取消在结构上打不断写入，store 整体不在取消面内（codex `storage.rs` vs `manager.rs` 分层；本条的实证解剖与事故史见本节末）。

- **引擎侧**：`CredentialStore` 类协议（终态归 `nova_ai/auth`，由 `nova_protocol/auth.py` 迁出）四件套 `read/list/write/delete` 全同步、瞬时、无 signal 参数；写盘走 temp+rename 原子写（撕读/崩溃半写不存在）；全局文件锁只护"读-改-写"微秒临界区，**永不跨网络**；
- **编排侧**：per-provider 锁（`KeyedLockFactory`，住 `nova_async/keyed_lock.py`）串行同 provider 的 refresh/logout/login（防轮转 token 双刷、防刷新与登出串号——pi 契约语义），不同 provider 互不阻塞；网络段包 `AbortScope`；锁等待同样可被 scope 真杀；
- **判别测试（新 store 组件评审用）**：① 方法签名里有 `await` 吗？② 锁/临界区里有网络或长操作吗？任一答"是" = 编排漏进引擎，打回；
- **现行例外**：`ModelsStore`（`gateway/store.py`）保留 async 形状——无锁无网络，零 await 协程无可中断点，运行时无害；签名形状与 `CredentialStore` 有意不齐（留待需要时同步化）；
- **反例存档（已消灭的病灶）**：`modify(async回调)` 把 OAuth 刷新关进全局文件锁（秒级）——全局阻塞 + async store 被拖进取消面（race 分流、"后台完成"、login 两段式等管理成本随之全部退役）。

## 五、超时治理三层

1. **整体相（connect/pool）**：`CONNECT_TIMEOUT_S = 10s` 工程上限（`nova_ai/api_impls/_shared/timeouts.py`）——机制底线，**策略层关不掉**（harness 的 "disabled" 只关 read/idle 相，管不到它；历史上的"disabled 翻成约 24.8 天"已退役）。
2. **SSE idle 相**：用户旋钮 `StreamOptions.timeout` 三态（`None` = 引擎默认 `DEFAULT_STREAM_TIMEOUT_S = 300s`；正数 = 耐心；`0` = 显式关看门狗）+ `idle_guarded` 逐事件计时。处死抛 `RequestTimeoutError`——流不可续传，重来整请求归回合层裁决（`RetryController` 读 kind，`REQUEST_TIMEOUT ∈ RETRYABLE_ERROR_KINDS`）——这是 codex 断流重连在我们语义下的等价物。
3. **deadline 预算**：`Deadline` 值对象（`nova_async/deadline.py`——升格件，带 `remaining_s` 秒视图供 `asyncio.timeout`/`wait_for` 消费），`deadline - now` 逐级下发剩余时间——refresh 整轮缺省 90s（策略件 `DEFAULT_REFRESH_DEADLINE_MS` 留 `nova_ai/gateway/models.py`）；OAuth 刷新 15s 帽走 `asyncio.timeout` 独立通道。

**贯穿原则**：服务端指引优先于一切本地预算——`Retry-After` 家族经 `retry_after_ms` 链直达回合层（归 `docs/crosscutting/diagnostics-modeling.md` §三）。

## 六、死因分型（超时 ≠ 取消）

| 死因 | 词汇 | 语义 | 诊断面 |
|---|---|---|---|
| done | `StopReason` 正常终态 | 完成 | — |
| aborted | `AbortedError` | **用户**判死 | 不进诊断分类学（铁律三） |
| timeout | `RequestTimeoutError`（`TimeoutError` 子类 + `REQUEST_TIMEOUT` 自声明） | **系统**判死 | 可重试家族 |
| error | 归一 kind（`normalize_error` 单点） | 故障 | `NovaErrorKind` |

- **禁 timeout-signal 伪装**：`AbortSignal.timeout` 并入取消通道 = 幽灵取消（pi `resolve.ts:151` 与旧 nova `resolve.py` 同款病例）；超时一律走 `asyncio.timeout` 独立通道、抛独立类型。
- **取消竞速裁决**：race 两边同刻就绪时**完成优先**（completion-wins）——已完成的工作不丢弃，且 Python 事件循环观测不到真同时。Codex 并非全局“取消优先”：通用 `async-utils` 不写 `biased`，`tools/parallel` 即使取消分支先就绪也检查 terminal outcome 与 task 完成状态；局部 `select!` 的 `biased` 只表达轮询顺序，不得反推领域终态可丢。

## 七、明确不做

- **断流重连**：SSE 生成流不可续传——idle 处死 + 回合重试即等价物；
- **stale 熔断、死连接扫墓、TCP keepalive**：未观察到病症——有病再立项（生产事故史存档备查，不作设计输入）；
- `CancelledError` 当用户取消词汇；
- `AbortSignal.timeout` 作为超时的实现手段（原语保留对齐 TS，超时路径一律 `asyncio.timeout`）；
- 新增代码用 `asyncio.wait_for`：超时一律 `asyncio.timeout` 结构化（3.11+）。存量白名单：SDK `transport/base.py` 的 per-request 回执 Future——超时须取消 pending future 本身（撤单语义），注释在案。

## 八、测试要求

- 竞速/中断一律 Event 受控完成序，**禁 sleep 表达等待完成**（`docs/crosscutting/testing-modeling.md` §七）；
- abort 语义三处分别锁：**举旗点**（pre-aborted 不发请求）、**在飞真杀**（create 阶段与流中各一例——op 亲见 CancelledError）、**收尾不发布**（无 `DoneEvent`）；
- 启动契约锁“返回已接受时登记已完成”，并在返回后立即 abort/wait，不用调度睡眠掩盖启动窗口；
- 动态任务批锁 `close()` 与登记竞速、关闭后晚到任务不起跑、后台失败在运行期被属主观察；
- 已接受运行分别覆盖正常完成、失败、协作取消与硬取消：内部终态和权威事件流终态恰好一次；永久阻塞或失败的观察者不得改变 run 结果、Provider 生命周期或完整 settle；
- 超时必锁双断言：`kind == REQUEST_TIMEOUT` + **不冒名 aborted**；
- scope 纪律锁两例：**cleanup 在块外正常执行**（level-triggered 下块内 await 持续抛取消）、**预取消信号首 checkpoint 即死**；
- 住所审计常绿（`docs/crosscutting/structure-modeling.md` §五）。
