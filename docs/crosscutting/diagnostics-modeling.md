# Nova 诊断与可观测性建模标准

> 坐标：横切·诊断与观测（体系总图与状态三档约定见 `docs/README.md`）。

> 本文建模的不是"错误"一个点，而是**系统如何向受众报告自身状态**的两族成员：**诊断**（diagnostics——错误、警告、通知、弃用、重试态、装配诊断；用户与开发者面，LSP/rustc/TypeScript/.NET 的 severity 分级传统）与**观测**（observability——日志、遥测；机器面，OTel 传统）。
> 类型的住所与表示选型见 `docs/crosscutting/data-modeling.md`（本文新增词汇全部遵守）；测试细则见 `docs/crosscutting/testing-modeling.md`；取消的三海拔见 `docs/crosscutting/data-modeling.md` §七（本文引用不重复建模）。
> 骨架以 codex 为第一参考（统一性落在**协议词汇层** + 归一单点 + 错误携带数据 + 持久化策略单点 + 敏感载荷不落盘）；提示面保留 pi 的"错误消息化"传统（用户可读文案挂在 `AssistantMessage` 上，随会话落盘、可回放）。
> 状态：**目标态定稿**。已落地部分见末节「现状注记」；未落地部分按「落地波次」推进——但**新增任何诊断类词汇即日起按本文设计**，不等波次。

**总纲**：诊断与观测家族只有一个分类学枢纽（`nova_protocol` 词汇）与一个归一单点（`normalize_error`）；每个成员按"**受众 × 通道 × 寿命**"三坐标各就各位，统一性落在词汇层，不落在类型层。

## 一、铁律（七条）

1. **统一性落在词汇层，不落在类型层**：不要求各子系统共用异常基类或诊断基类；统一的是过线/落盘那一刻的词汇。
2. **分类一次，边界完成**：`normalize_error` 是全栈唯一错误分类函数；下游（重试、压缩恢复、UI、遥测）只读 `kind`，**禁止再次对消息文案做正则/子串匹配**——pi"文案即分类依据"的隐性耦合到此断绝。
3. **取消永远不是错误**：`AbortedError` / `LoginCancelledError` 不进任何诊断分类学，走自己的通道（`StopReason.ABORTED` / 交互收尾）。
4. **一诊断一通道**：每类诊断有且仅有一个通道；禁止同一诊断双渠道重复（警告不许既 `logger.warning` 又发事件；错误不许既上消息又发事件）。codex 的 tracing 与 `WarningEvent` 严格分家即此意。
5. **瞬态不落盘，终态必落盘**：持久化策略见第六节，全家族共用一张表，不各自为政。
6. **敏感不落盘**："活投递不落盘"靠表示选型达成（只住 dataclass，不进 Pydantic——codex `#[serde(skip)]` 三件套对位），不靠自觉 skip。
7. **可行动文案**：用户面诊断必须告诉受众下一步怎么办（codex `EnvVarError.instructions` 对位；二进制缺失附 brew/apt 指引是既有先例）。

## 二、家族全景（一诊断一通道总表）

| 成员类别 | 受众 | 通道 | 词汇 | 落盘 |
|---|---|---|---|---|
| 终态错误（LLM/turn 失败） | 用户 | `AssistantMessage`（`stop_reason=error` + `error_kind`） | `NovaErrorKind` | ✓ 随消息树 |
| 工具错误（喂模型） | 模型 | `ToolResultMessage`（`is_error`） | 文本即词汇 | ✓ 随消息树 |
| 工具致命错误 | 用户 | `FatalToolError` → 终态错误消息 | `NovaErrorKind` | ✓ 随消息树 |
| 取消终态 | 用户 | `AssistantMessage`（`stop_reason=aborted`） | 三海拔（§七） | ✓ 随消息树 |
| 重试进行中（will_retry） | 用户 | `AutoRetryStartEvent` / `AutoRetryEndEvent`（既有） | — | ✗ |
| 警告 | 用户 | `WarningEvent`（事件总线） | `WarningKind` | ✗（禁 `append_entry`） |
| 弃用通知 | 用户 | `WarningEvent(kind=DEPRECATION)` | — | ✗ |
| 信息通知（命令/扩展回执） | 用户 | UI 原语 `notify_message`（包词汇，既有） | — | ✗ |
| 状态行 / 进度 | 用户 | `set_status` / loader（**UI 态，不是事件**） | — | ✗ |
| 装配诊断（启动/加载期批量） | 用户+开发者 | `NovaDiagnostic[]` 收集 → 启动后转 WarningEvent 呈现 | `WarningKind` | ✗ |
| 开发者日志 | 开发者 | `logger`（分级纪律） | — | 日志文件（独立面） |
| 遥测 | 机器 | 免载荷上报单点 | `NovaErrorKind`/`WarningKind` 复用 | 遥测后端 |
| RPC 客户端错误 | 机器 | 标准 JSON-RPC 码 + `data.errorKind` | `NovaErrorKind` | — |
| 控制面请求（审批/交互/输入） | 用户 | 反向原语 / RPC 方法（请求-应答） | `docs/crosscutting/data-modeling.md` §七 | 按控制面规则 |

边界三条（易混处点名）：

- **状态显示 ≠ 诊断**：footer 状态行、loader、进度百分比是持续 UI 态，由 `set_status`/loader 通道表达，**不立事件、不进本家族的持久化讨论**；
- **扩展/命令的回执 ≠ 运行时诊断**：slash 命令、扩展操作的用户回执走既有 UI 原语 `notify_message`（包词汇，定义权归包）；**核心运行时与扩展要发布的"警告级"诊断走 `WarningEvent`**（harness 事件联合接入后，扩展经会话事件 sink 发射）——来源不同、通道不同是正当分层，不是双渠道重复；
- **重试中不是警告**：will_retry 语义的瞬态由 `AutoRetryStart/EndEvent` 表达（codex `StreamErrorEvent` + `will_retry:true` 对位），不发 WarningEvent。

## 三、错误（终态诊断）

### `NovaErrorKind`——用户面错误分类学（15 变体，住 `nova_protocol`）

`str, Enum`，内存保持枚举成员；线值 camelCase（随 `StopReason.TOOL_USE = "toolUse"` 先例）。

| 变体 | 线值 | 来源 | retryable |
|---|---|---|---|
| `HTTP_CONNECTION_FAILED` | `httpConnectionFailed` | TransportError（沿 `__cause__` 链找） | ✓ |
| `STREAM_DISCONNECTED` | `streamDisconnected` | 流中途断开 | ✓ |
| `REQUEST_TIMEOUT` | `requestTimeout` | 408 / 引擎自判超时（SSE idle 看门狗、OAuth 刷新帽、deadline 预算） | ✓ |
| `OVERLOADED` | `overloaded` | 409 / 529 / overloaded 词 | ✓ |
| `RATE_LIMITED` | `rateLimited` | 429 / SSE rate_limit | ✓ |
| `UPSTREAM_ERROR` | `upstreamError` | 其余 5xx | ✓ |
| `UNAUTHORIZED` | `unauthorized` | 401/403 | |
| `LOGIN_REQUIRED` | `loginRequired` | 无凭据 | |
| `USAGE_LIMIT_EXCEEDED` | `usageLimitExceeded` | quota/billing 词 | |
| `BAD_REQUEST` | `badRequest` | 400 | |
| `CONTEXT_OVERFLOW` | `contextOverflow` | 溢出词表（常披 400/413 外衣） | |
| `MODEL_NOT_FOUND` | `modelNotFound` | 404 | |
| `SANDBOX_DENIED` | `sandboxDenied` | executor 沙箱拒绝 | |
| `EXECUTOR_UNAVAILABLE` | `executorUnavailable` | 执行后端不可达 | |
| `INTERNAL` | `internal` | 兜底（含 bug） | |

同文件挂 `RETRYABLE_ERROR_KINDS = frozenset({六个 ✓})`——**可重试性从 kind 推导**，覆盖 SDK 重试状态码全集（408/409/429/5xx/连接层；409 归 `OVERLOADED`，语义取"服务端请求退避"）。遥测与线上共用这一套免载荷枚举（比 codex 少建一套 `CodexErrKind`——Python 枚举序列化免费）。

### `AssistantMessage.error_kind`

```python
error_kind: Optional[NovaErrorKind] = None   # stop_reason == error 时填充
```

可选字段：旧会话 JSONL 零迁移加载；随消息落盘，回放时 UI 一致分派。`ErrorEvent` **不动**——kind 骑在它携带的消息里。`http_status` 不上线（住运行时 `NormalizedError` 供遥测；将来有线上消费方再加，属 minor）。

### 归一枢纽（nova_ai）

`NormalizedError`（`dataclass(frozen=True, kw_only=True)`——异常与 `Callable` 同级，不进 Pydantic）：

| 字段 | 语义 |
|---|---|
| `kind: NovaErrorKind` | 分类（唯一真相） |
| `message: str` | 用户可读单行；过 2KB 截断（codex `ERROR_MESSAGE_UI_MAX_BYTES` 对位） |
| `retryable: bool` | 由 `RETRYABLE_ERROR_KINDS` 推导，不独立判定 |
| `retry_after_ms: Optional[int]` | 服务端要求的退避（codex `retry_delay` 对位——外挂元数据，不入枚举） |
| `http_status: Optional[int]` | 遥测/诊断 |
| `domain: Optional[str]` | 子系统归属（`ModelsError.code` 等）——"谁报的"，排查用 |
| `cause: Optional[BaseException]` | 诊断链——**不上线不落盘**（铁律 6） |

`normalize_error(error, *, domain=None)`——全栈唯一分类函数，六级阶梯（逐级单测）：

1. **自声明**：`getattr(exc, "nova_error_kind")`——领域异常自带；
2. **溢出词表**：body+message 命中 `OVERFLOW_PATTERNS`（且未被 `NON_OVERFLOW_PATTERNS` 否决）→ `CONTEXT_OVERFLOW`（**先于一切**——溢出常披 400/413 外衣）；
3. **限额词表**：insufficient_quota / usage limit / billing / balance / budget / quota → `USAGE_LIMIT_EXCEEDED`（**先于状态码**——429 的"额度用尽"与"临时限流"在此分流，codex 的 429 usage_limit_reached 特判同款；每个 pattern 带出处注释）；
4. **SDK 错误类 → 状态码 → 传输链**（`utils/errors.py` 探针升格）：先 `isinstance` 命中已知 SDK 错误类（openai/anthropic 的 `APITimeoutError`→REQUEST_TIMEOUT、`APIConnectionError`→HTTP_CONNECTION_FAILED、`AuthenticationError`/`PermissionDeniedError`→UNAUTHORIZED、`RateLimitError`→RATE_LIMITED 等——SDK 的"子类 + `status_code` 属性"分层是 Python 正典，直接消费其分类成果）；未命中再按状态码：401/403→UNAUTHORIZED、404→MODEL_NOT_FOUND、408→REQUEST_TIMEOUT、409/529→OVERLOADED、429→RATE_LIMITED（+`retry_after_ms`）、400→BAD_REQUEST、其余 5xx→UPSTREAM_ERROR；无状态码沿 `__cause__` 链找 TransportError（超时→REQUEST_TIMEOUT，其余→HTTP_CONNECTION_FAILED）；
5. **限流/超载文案**：无结构信息时 overloaded → `OVERLOADED`、rate limit / too many requests → `RATE_LIMITED`；
6. `INTERNAL`。

**词表收编清单**（现有消息匹配点全部迁入，消费方改读 kind）：`RetryController.is_retryable_error` 的子串清单、会话压缩恢复的溢出判定、`nova_ai/utils/overflow.py` 与重试词表。

**转换点**：两个 api_impls 的 `_stream.py` catch 块 + `gateway/streams.py` 的
setup 路径（`create_setup_error_message`——装配期失败同样归一上 kind）共三处——
异常归一后 `error_kind`/`retry_after_ms` 上 `AssistantMessage`。refresh errors
字典保持原始异常（领域层），呈现层再归一。

## 四、警告与弃用（`WarningEvent`）

```python
class WarningKind(str, Enum):
    GENERAL = "general"          # 运行警告（传输降级等）
    CONFIG = "config"            # 配置缺失/迁移/校验
    CAPABILITY = "capability"    # 能力降级（二进制缺失/无站刷新/fallback 模型）
    DEPRECATION = "deprecation"  # 弃用通知

class WarningEvent(NovaBaseModel):
    type: Literal["warning"] = "warning"
    kind: WarningKind = WarningKind.GENERAL
    message: str
```

- 接入 agent/harness 事件联合；前端渲 ⚠ 黄字；呈现层按 `(kind, message)` 会话内去重（去重只在呈现层做，事件本体不抑制）；
- **弃用不另立事件**（codex 有独立 `DeprecationNoticeEvent`，我们收进 `WarningKind.DEPRECATION`——词汇更少、语义等价；将来若有 Rust 协议层出货，按其独立事件形态走第七节映射对齐）；
- **弃用双轨**：给**包的下游开发者**的 Python API 弃用走 stdlib `warnings.warn(DeprecationWarning)`（可过滤、once-per-location 去重内建——stdlib warnings 是代码级通道）；给**终端用户**的产品/功能弃用走 `WarningEvent(kind=DEPRECATION)`。两者受众不同，永不混用；
- Guardian 类自动审批评审警告：将来落地时以 `WarningKind.GENERAL` + 消息前缀承载，不复制 codex 的 `GuardianWarning` 变体（approval 体系未定形，词汇不超前）。

## 五、装配诊断（`NovaDiagnostic`，批模式）

与 WarningEvent **同一形状、两种节奏**：流模式 = 运行时即发（WarningEvent）；批模式 = 装配期收集、启动后一次性呈现（pi `AgentSessionRuntimeDiagnostic[]` + `deduplicateDiagnostics` 对位）。

```python
@dataclass(frozen=True, kw_only=True)
class NovaDiagnostic:
    severity: Literal["info", "warning", "error"]
    kind: WarningKind
    message: str
```

**标准装置：`DiagnosticsCollector`**（`nova_ai/gateway/diagnostics.py`——词汇/装置分离：词汇住 `nova_protocol`，装置带行为故随首个消费方；harness 装配路径复用时经 `nova_ai` 导入）——所有"警告即数据"场景的统一收集器，双通道：

- `items`：**无条件收集**——归属本次操作、随结果返回、无听众不丢（headless/槽位空缺时警告依然存活）；
- `on_diagnostic`：**可选槽位**（注入型 hook）——append 时同步外发，由组合根在装配期填入桥函数（如 harness 转 `WarningEvent` 上会话事件）；缺省 `None` 即纯批模式。槽回调异常只记日志不外抛（对齐 `on_update` 纪律：诊断通道永远不能炸掉业务操作）。

判型规则：消费方**够不着总线**（库层/装配期）或**需要操作级归属**时用这个装置；已有总线且无需结果归属的场景直接发射 WarningEvent。

- **收集点**：会话/运行时装配（settings 迁移、包/资源加载、二进制探测、agents/prompts 加载）与刷新路径（模型目录 refresh 的 `RefreshModelsContext.diagnostics`）——**收集返回给调用方，不在装配层打印**（pi "Runtime creation returns diagnostics to the caller instead of printing or exiting" 对位）；
- **呈现时机**：会话启动完成后批量转 WarningEvent（severity=error 的按错误呈现）；批内按 `(severity, kind, message)` 去重；
- dataclass 非落盘件，遵守铁律 5/6。

## 六、持久化策略（全家族一张表）

**消息树持久，事件总线瞬态**——一句话规则。**顺序契约：先落盘后发声**（durability-before-notification，codex `handle_task_abort` 注释钉死的同款——客户端收到事件会同步重读落盘数据，所以事件必须在落盘成功之后发；中止/恢复等涉及重读的流程一律遵守）。落盘白名单：正常消息流、终态错误消息（含 `error_kind`）、取消终态消息、工具结果消息、控制面条目（`append_entry` 既有白名单：plan 状态 / tool-panel / subagent_allow / executor_backend / agent / persona_override 等）。其余一切成员（WarningEvent、diagnostics、retry 事件、通知、进度、遥测、日志）**禁止 `append_entry`**——codex `rollout/policy.rs`"过程性事件不落盘"对位，我们靠通道分层天然达成：瞬态走事件总线，根本没有进树的路径。

## 七、与 codex Rust 协议词汇的对齐（投影表存档）

codex 的 Rust 协议层（`EventMsg` 家族）含 `Error/Warning/GuardianWarning/StreamError/DeprecationNotice/TurnComplete` 与 `CodexErrorInfo` 18 变体。仓内曾有过该层的 Rust 端口存档（`nova-agent-rs/protocol-core-agent`——**已随 execpolicy/shell_command_tools 完成 py 金标对跑后整体删除**；py 词汇在 `nova_protocol`）。**将来若再有 Rust 协议层出货，`NovaErrorKind` 按下表投影**（单向投影，data-modeling §四"对外续命用投影"同款）：

| NovaErrorKind | CodexErrorInfo |
|---|---|
| contextOverflow | ContextWindowExceeded |
| usageLimitExceeded | UsageLimitExceeded |
| rateLimited | RateLimitExceeded |
| overloaded | ServerOverloaded |
| httpConnectionFailed | HttpConnectionFailed |
| streamDisconnected | ResponseStreamDisconnected |
| upstreamError | InternalServerError |
| unauthorized | Unauthorized |
| badRequest | BadRequest |
| sandboxDenied | SandboxError |
| internal（及其余无对位者） | Other |

我们多出的 `loginRequired`/`modelNotFound`/`executorUnavailable`/`requestTimeout` 投影进 `Other`（消息保真）；codex 侧多出的 `SessionBudgetExceeded`/`CyberPolicy`/`MisalignmentPolicyViolation` 等不回投（无消费方）。

## 八、日志（开发者通道）

- `logger` 分级照旧（DEBUG/INFO/WARNING/ERROR）= 开发者通道，进日志文件，**永不替代用户通道**；
- 分诊规则：**用户需要据此行动的**必须升格到用户通道（WarningEvent/错误消息），纯诊断留 logger——铁律 4 的执法面：升格后 logger 可同记一行（带 `exc_info`），但不许只记日志不上报；
- 遥测见下节，日志不承担分析职责（codex tracing 与 analytics 分家对位）。

## 九、遥测（机器通道）

> 状态：【目标态·有意缓建】现状零运行期遥测面（归一产物 `http_status`/`domain` 只产不消，`TelemetryFn` 缝无消费方）——缓建理由：遥测是可观测性不是正确性，且消费方（运营形态）尚不存在。**触发线（任一即立项）**：① 服务器/桌面端形态立项；② 出现"想看错误分布而不得"的真实排障需求；③ 诊断波次 4/5 启动时顺带接线。

- **终态错误上报单点**：`kind + http_status + domain`，**消息免载荷**（codex `track_turn_codex_error` 对位，隐私面——刻意严于 OTel 默认 `record_exception` 带 message 的做法）。对齐 OTel GenAI semconv：`kind` 即 `error.type`（低基数分类器的天然出口）、`http_status` 即 `http.status_code`、`domain` 挂 `nova.error.domain`；span status 只用 ERROR/UNSET；
- 警告上报复用 `WarningKind`；取消不计错误指标（铁律 3 在遥测面的投影）；
- 分类学只有一套（`NovaErrorKind`），遥测不另建免载荷枚举。

## 十、领域层纪律

- **自声明 kind**：`ModelsError` 构造加可选 `kind` 参数（`code` 域归属保留——code 是"谁报的"，kind 是"发生了什么"）；`ExecutorError` 层级按子类映射挂 `nova_error_kind`；
- **工具错误两通道**（codex `FunctionCallError.RespondToModel/Fatal` 对位）：默认异常 → 模型可见错误结果（现状不动）；**`FatalToolError`**（新，住 `nova_protocol`，包作者可 import）→ agent_loop 特判，终止 turn 出终态错误消息；
- **错误携带数据**（codex `SandboxErr::Denied` 对位）：executor `ProcessError` 带结构化输出（exit code/stdout/stderr），工具层格式化为模型可见文本——审批升级/模型可见/UI 三路消费同一载荷；
- **设计期绊线白名单**（内建异常 + 响亮消息，**不进任何诊断分类学**，import 期炸给开发者看）：catalog `api_for` raise、shard 对账、生成器校验；
- **传输层不动**：`_shared/retry.py` 保持结构嗅探（信息最全处，先于归一运行）；传输重试耗尽冒最后一个错误的自然 kind（不立 RetryLimit 变体）。

## 十一、文件组织（codex 对位）

纪律一句话：**类型类目内聚，不是概念家族内聚**——概念家族（第二节的 13 类成员）归本文档承载；文件布局按类型类目：事件跟事件住、错误机器一个可发现的文件、领域错误与子系统同居。

- **事件跟事件住**：`WarningEvent` 住 `agent_events.py`（定义先行、暂不入 `AgentEvent` 联合——联合接入归 harness/agent 波）。codex 同款：`WarningEvent` 住 `protocol.rs` 与全部 `EventMsg` 载荷同处，恰恰不住 `error.rs`；
- **错误机器一个可发现的文件**：`nova_ai/utils/errors.py`——归一枢纽（`normalize_error` + `NormalizedError` + 全部分类词表 + provider 错误体探针），对位 codex `protocol/src/error.rs`。codex 的 protocol crate 允许行为、转换函数与类型同 crate；我们枢纽纯洁性审计禁 httpx/SDK import，归一机器只能住行为包——**分散是约束不是错误，本文档承担"一站式可读"的补偿**；
- **领域错误与子系统同居**：`ModelsError`（`auth/resolve.py`）、`OAuthFlowError`（`auth/oauth/errors.py`）、`CatalogError`（`gateway/catalog.py`）、`ExecutorError`（nova-exec-server-client `errors.py`）——对位 codex `unified_exec/errors.rs`；
- **升格规则**：codex 把中心分类法要引用的领域类型升格进 protocol crate（`SandboxErr` 因 `CodexErrorDetails::Sandbox(#[from])` 被包装而入住）。我们的 `NovaErrorKind` 是扁平枚举、不包装领域错误——**升格触发条件不存在**，领域错误安心留在行为包；
- **命名规范**：**家族伞名取行业标准，模块名归内容**——本篇家族名 = **诊断（diagnostics）+ 观测（observability）**，分别对齐 LSP/rustc/TypeScript/.NET 的 severity 分级传统与 OTel 的机器通道传统（曾用名"报告体系"已退役：`reporting` 的行业默认联想是商业报表）；代码模块按内容命名且优先行业标准词汇——`NovaDiagnostic` 即 LSP 形状，模块故名 `diagnostics.py`。**禁用 `reporting` 这类自造总称命名代码模块**；
- **住户注记**：`NovaDiagnostic` 住 `nova_protocol/diagnostics.py`——非事件形状（装配诊断是诊断家族的批模式成员；frozen dataclass，枢纽中罕见的非 Pydantic 住户，理由见其模块 docstring）。

终态图：

```
nova_protocol/                    # 线上词汇（纯形状 + 登记制行为件）
├── enums.py                      # NovaErrorKind / WarningKind / RETRYABLE_ERROR_KINDS
├── events.py / agent_events.py   # ErrorEvent（既有）/ WarningEvent（定义先行，联合后补）
├── diagnostics.py                # NovaDiagnostic（装配诊断形状）
├── exec_server_*.py              # exec-server 词汇域六模块（config/environment/intent/policy/verdict/wire）
└── sanitize.py                   # redact_secrets 出口清洗器（登记制纯函数件）
nova_ai/                          # 行为层
├── utils/errors.py               # 归一枢纽（对位 codex error.rs）
├── auth/resolve.py               # ModelsError
├── auth/oauth/errors.py          # OAuthFlowError
├── gateway/catalog.py            # CatalogError
└── api_impls/_shared/retry.py    # 传输重试（结构嗅探，不动区）
docs/crosscutting/diagnostics-modeling.md  # 诊断家族地图（分散架构的可读性补偿）
```

## 十二、明确不做

附：Python 工具栈锚点（调研结论，设计依据存档）——

- **SDK 异常正典**：openai/anthropic Python SDK 的 `APIError` 根 + `APIConnectionError`（无状态）/ `APIStatusError` 家族（带 `status_code` + `response`）——子类即分类；归一阶梯第 1/3 级直接消费其成果，不重复发明；
- **自建 typed 异常继承内建异常**（Azure SDK 指南同款：`CatalogError(RuntimeError)` 等），不另起 `Exception` 裸根；
- **不引 Result 类型**：Python 社区主流不采纳（stdlib Result 提案未通过）——边界"错误即数据"走指定通道（errors 字典 / diagnostics / 消息 / 工具结果），不泛化；
- **stdlib `warnings` / `logging` 都是开发者通道**（warnings 面向代码消费者、可过滤、once-per-location 去重内建；logging 面向运维）——**用户面诊断必须自有词汇**，这是 `WarningEvent` 存在的根本理由；
- **OTel GenAI semconv**：`error.type` 要求低基数分类器（kind 即天然出口）、`http.status_code` 标准属性、`gen_ai.*` 命名空间。

- 不立 `NoticeEvent`/`StreamErrorEvent` 新事件类型（信息回执有 UI 原语、重试中有 AutoRetry 事件，通道已足）；
- 不做 plan/credits/reset 富结构（codex `UsageLimitReachedError` 的 RateLimitSnapshot 是 ChatGPT 计划专属）；429 可解析的 reset 只进 `retry_after_ms`；
- 不做 turn status 机制（`affects_turn_status`——我们消息即记录）；
- 不搞数字错误码表；不强制全局异常基类；不动 JSON-RPC 标准码；不复制 `GuardianWarning` 变体（approval 未定形）；
- aborted 永远不进诊断分类学；设计期绊线不收编；存量裸 `raise ValueError/RuntimeError` 不全面收编（用户可见零星点位随触动随换 typed）。

## 十三、测试要求

按 `docs/crosscutting/testing-modeling.md` 标准执行，要点：

- **归一阶梯逐级单测**：自声明/溢出词表/各状态码（httpx2.MockTransport 注入传输错误）/限额词表/INTERNAL 兜底；
- **枚举与字段 roundtrip**：`error_kind` 随 AssistantMessage 往返；`WarningEvent` 判别联合 roundtrip；
- **兼容 fixture**：无 `error_kind` 的旧会话 JSONL 加载归零（None）；
- **持久化门禁**：WarningEvent/diagnostics 不进会话树的回归测试（铁律 5 的机械执法）；
- **漂移测试常绿**：wire schema 重导出随词汇变更同 PR；`NovaErrorKind`↔`CodexErrorInfo` 投影表有差分测试（第七节）；
- **消费改读回归**：RetryController 与压缩恢复从子串清单切到 kind 后，原有用例语义不漂移。

## 十四、落地波次与现状注记

1. **nova_protocol 词汇**：`NovaErrorKind` / `RETRYABLE_ERROR_KINDS` / `WarningKind` / `WarningEvent` / `error_kind` / `FatalToolError` / `NovaDiagnostic`；wire schema 重导出 + 漂移测试 + 旧 JSONL 兼容测试——**已落地**；
2. **nova_ai 归一枢纽**：`NormalizedError` + `normalize_error`（词表收编）；两个 `_stream.py` catch 接入——**已落地**；
3. **领域挂 kind + 消费改读**：`ModelsError` / `ExecutorError` 映射；RetryController 与压缩恢复改读 `error_kind`；自动重试接 `retry_after_ms`——**部分落地（2026-10）**：`AssistantMessage.retry_after_ms` 载体 + 三转换点抄录 + RetryController/压缩恢复改读 kind（子串清单删除）+ `LOGIN_REQUIRED` 产出 + anthropic 流内 error.type 映射 + catalog 传输保真（`CatalogOutcome.cause`）；残余：ExecutorError 映射（executor-client 侧）；
4. **FatalToolError 接 loop** + WarningEvent 入事件流 + logger 分诊升格 + 呈现层去重 + `NovaDiagnostic` 接装配层；
5. **遥测单点 + RPC `data.errorKind` + Rust 协议层投影**。

**现状注记（存量，不归波次）**：错误消息化（`AssistantMessage.error_message` + `ErrorEvent`）、provider 错误体标准化（`utils/errors.py`）、传输重试结构嗅探（`_shared/retry.py`）、refresh errors 字典、`ModelsError` 六域 code、取消三海拔、`ExecutorError` 七类层级、会话重试事件（`AutoRetryStart/EndEvent`）、信息回执通道（UI 原语 `notify_message` 糖 + `set_status`）、扩展错误事件（`ExtensionErrorEvent`）。
