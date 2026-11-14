# Nova 数据建模标准

> 坐标：横切·形状（体系总图与状态三档约定见 `docs/README.md`）。

> 类型定义的住所、选型、序列化、演化、开放集、测试全套约定。速览见根 AGENTS.md「数据建模速览」。

> 总纲：**终点拓扑——共享领域数据枢纽 + 按边界的协议包 + 行为机制与组件内部类型各自为政**。
> 状态：**【现状】**（共享纯数据、边界协议、行为机制与组件内部类型已完成分址；新增类型按本标准判定）。
> 两个坐标决定每个类型的命运：**语义所有权**（它的变化由谁的业务驱动）与**消费边界**（消费者是否跨领域、是否跨进程/时间、是否形成依赖环）。
> 配套两原则：**单一正典源**（每个类型全仓只有一个定义点）；**公共入口消费**（枢纽类型一律 `
from nova_protocol import X`，业务包类型从其语义所有方的公共入口导入，不经过中转门面）。

## 一、住所（placement）

> 本节只管**类型**的住所；模块/包的住所、依赖方向与结构核对归 `docs/crosscutting/structure-modeling.md`。

**总规则一句话：跨领域且需要统一正典的纯数据词汇进 `nova_protocol`；其余按语义所有权归位。**

新类型按序四问；任一命中即停止：

| # | 问题 | 答“是”的去向 | 例 |
|---|---|---|---|
| 1 | 只属于一个语义领域，或只是某行为的私有入参/中间态吗？ | 住领域所属包，**即使被多个下游包使用也不上升** | `StreamOptions`、executor 配置物化、loader 中间态 |
| 2 | 含 Callable、服务 Protocol、状态、I/O、异步原语或独立算法吗？ | 住行为/机制所属包 | `ProviderAuth`、`AbortSignal`、execpolicy、`redact_secrets` |
| 3 | 是某外部契约的特定版本面吗？ | 住对应边界协议包 | `nova_server/protocol` 的 RPC 形状、`nova_exec_protocol` 的 wire DTO |
| 4 | 有真实跨领域消费方，且是需要统一正典的纯数据/值对象吗？ | 住 `nova_protocol` | `Model`、`AgentEvent`、`Credential`、`NovaDiagnostic` |

“跨包使用”“会序列化”“纯函数”单独任一都不足以进枢纽。由行为产生的数据仍可在满足第 4 条时入住（如 `AuthResult`）；只有行为私有入参、运行时容器和中间态被第 1 条排除。

终点全景：

| 层 | 内容 | 纪律 |
|---|---|---|
| **`nova_protocol` 共享数据枢纽** | `NovaBaseModel`、跨域 ID/别名/诊断；LLM 消息/内容/模型/用量/流式事件/枚举/compat；模型目录字段包与 `ModelsStoreEntry`；图像与分类词汇；`identity.py`；`agent_events.py` 的 AgentEvent 家族、AgentMessage 开放集与 `AgentToolResult`；Auth 公共数据（`Credential`/`RedactedString`/`AuthResult`/`AuthCheck`/Auth 交互数据） | 依赖仅 pydantic；零 I/O、零兄弟包依赖、零行为例外。只放形状 + 平凡不变量/序列化转换；**不设行为登记制** |
| **边界协议包**（一条边界一个） | `nova_server/protocol`（包内）：前端 RPC 方法表/信封/握手/错误码/Schema + 线上 item/notification；独立 `nova_exec_protocol`：executor 版本化 wire DTO；Rust 正典 `nova-exec-server/PROTOCOL.md` 独立演进 | 线上唯一海关；外部协议不进入共享数据枢纽。`nova_exec_protocol` 独立是因为多方直接消费、独立版本与 Python/Rust 金标 |
| **机制与组件内部类型** | `nova_async`：完整取消词汇与等待机制；`nova_ai/auth`：Auth 行为定义、服务 Protocol、Callable 配置、流程私有异常；`nova_ai/utils/sanitize.py`：出口清洗；`nova_exec_policy`：Shell/策略/权限/配置物化；`nova-exec-server-client`：连接期环境类型；`nova_harness`：Session 与审批 UI 词汇；各包运行时 State/Context/StreamOptions/UIContext | 留在语义所有方，不上溢、不复制；跨包正常向下 import，不为“共享”额外拆包 |

模型目录使用 `Model` / `ImageModel` / `ClassifierModel` 的判别联合 `AnyModel`（`ModelType=chat|image|classifier`），旧 chat 缺省 `type` 在联合入口归一；非流式图像与分类结果分别使用独立的 `AssistantImages` / `ClassifierResult`，不混入 `AssistantMessage` 的聊天流。

**两种正典形态并列但分家**：消息形（LLM 上下文 + 落盘）住 `nova_protocol`；item 形（呈现 + 线上通信）住 `nova_server/protocol`（`items.py`/`notifications.py`）。`reduction` 是翻译**行为**，住 `nova_server`，两路共用同一构造器防漂移。Session JSONL entries 住 `nova_harness/sessions/`，不再执行迁入枢纽的旧计划。

**分址结果（无 shim）**：取消词汇归 `nova_async`；Auth 行为与出口清洗归 `nova_ai`；executor wire 与策略分别归 `nova_exec_protocol`、`nova_exec_policy`；Agent 公共类型保留在单一 `nova_agent/types` 领域且已拆除混合 `base.py`；Harness 类型归还 sessions/resources/package/config/compaction/extensions/ui 等领域；Server item/notification 归 `nova_server/protocol`。旧聚合门面、集中 `nova_harness/types` 与行为白名单均已删除，不保留 re-export 过渡产物。后续搬家仍须原子完成旧路径归零、相关套件与契约金标。

## 二、表示选型

按以下顺序决策技术栈——先问可变性，再问边界与频率，最后按成本选表示；**表示与校验是两个正交轴**，不要为了"统一"而全用一种。

  1. **先问可变性**：对象创建后会被原地修改吗？可变 → **普通 class 或 `dataclass`**，禁用 Pydantic（校验与拷贝语义和可变运行时容器冲突）。例：`AgentState`（普通 class + property setter 做顶层数组拷贝）、`AgentContext`（被循环原地 append）、`AgentSessionServices`。**可变默认参数是硬红线**：dataclass 的可变容器必须 `field(default_factory=...)`（解释器对 dataclass 已强制，此条为成文）；普通 class 的 `__init__` 默认参数同样不得为可变字面量——那才是没人拦的真坑。
     - **文档化的可变例外**：流式累加块（`_stream.py` 的 `block.text += delta` 逐 chunk 原地追加）——每 chunk 拷贝不可接受，此类词汇类型保持可变并在标准中点名，不用 `frozen` 硬套。
  2. **再问边界与频率**：对象要跨进程（RPC / WebSocket）或持久化吗？需要就有人在边界校验，但**校验强度按数据频率分档**：
     - **低频契约对象**（settings、auth.json、models.json、包 manifest、RPC envelope、API 消息——数量少、边界单次校验、schema 收益主导）→ **Pydantic（`NovaBaseModel`）**，序列化与 schema 一体化，使用原生 `model_dump()` / `model_validate()`。例：`Model` / `Usage` / messages / Agent 事件。
     - **高频重放流**（会话 JSONL 逐行恢复、批量事件回放——一次几千行）→ 边界只做结构校验（JSON 可序列化级）+ 语义校验（reducer），内存工作表示用 dict + `TypedDict` 窄签名；**逐行 `model_validate` 是已量出的加载瓶颈**（novaharness 会话加载先例）。将来 codec 需要强 schema 时，在 codec 单点用 `TypeAdapter(TypedDict)`（pydantic 原生支持）或 `msgspec`（decode 快 6–15x），不进库层工作表示。（现状注记：高频重放的性能终态答案是 `model_construct()`——单一表示、跳过校验（见「序列化与线上形态」校验两制），**TypedDict 双表示不引入**；TypedDict 仅保留给规则 7 哑容器与将来 codec 的静态形状声明。）
  3. **校验只给不可信输入**：第三方产出的数据（工具返回值、用户配置、前端 payload）即使不直接序列化也可用 Pydantic，换取构造时尽早报错；框架内部自产自销的对象不做构造时校验。例：`AgentToolResult` 用 Pydantic 不是因为要序列化，而是工具作者是第三方。
     - **第三方对象的形状读取 = 边界归一**（防腐层纪律）：第三方对象（SDK 错误/chunk/响应——**未担保形状**：extra 残余、错误对象、跨 SDK 分叉处）的读取必经归一函数，中途禁 duck-read；归一函数住"对象族的既有边界家"（错误族归 `utils/errors.py`、chunk 族归各协议 `_stream.py`、HTTP 归 `utils/http.py`——无既有家才新建模块且须登记住所审计），注释必须带实证出处（SDK 源码/实测）。SDK 已声明字段（其模型定义担保的部分）直读不过层；归一只为未担保残余服务。
     - **永真式假设快死，环境变化才许防御**：形状/结构假设读不到 = bug——响亮失败或归一，禁静默降级；`getattr(第三方对象, field, 默认值)` 只许发生在边界归一函数内（F1 病例：三处 `error.headers` 读取对 SDK 错误恒盲——SDK 的头在 `.response.headers`，getattr 默认值让错误雪藏；真假件迁移实证照出）。
  4. **`Callable` / 服务实例 / 异常永远不进 Pydantic**：依赖容器、hook 上下文、运行时中间态一律 `dataclass` 或普通 class。例：`AgentLoopConfig`、`StreamOptions` 家族、`Provider`、`AgentSessionConfig`。
  5. **不可变值对象优先 `frozen=True`**：纯数据、无序列化需求的值对象用 `dataclass(frozen=True)` 在类型层面锁死不可变性，不靠自觉；多字段的查询/选项类再加 `kw_only=True` 防位置参数耦合。
  6. **union 必须可判别**：存在反序列化路径的 union 用 `Field(discriminator=...)` 显式判别，不依赖 smart-union 猜测。开放集（框架变体 + 包级兜底）用判别联合 + 兜底成员 + `union_mode="left_to_right"`（例：`nova_server/protocol/items.py` 的 `WireItem`——`SerializeAsAny` 只管序列化方向，校验方向必须显式可判别，否则 `model_validate` 按基类重建剥掉子类字段）。TypedDict 文档路径的判别联合不引 Pydantic——判别键（`type`）保持 Literal 字面量稳定即可，与 Pydantic 路径共享同一词汇，为将来任何 codec 校验留位。
  7. **哑容器不进 Pydantic**：透传载荷/中间态包装（需要原样持有任意内容——不校验、不重建、不转换）即使最终会上线也不用 Pydantic；纯数据透传用 `TypedDict` 声明形状，有行为/不变量的容器才用 `dataclass`。Pydantic 的"处理欲"对透明容器是害处。例：`JsonRpcMessage`——`result` 字段原样容纳模型实例/dict/None，序列化推迟到出货那一刻。
  8. **单道序列化**：生产侧（RPC handler 等）返回模型**实例**，dump 归传输/分派层单点出货；不在中间环节"先 dump 再 validate 再 dump"（双道打包会在重建时剥多态字段）。dispatch 出参对实例直通 dump_wire；声明了 result_model 却返回散装 dict → 契约违约报错（router.py 先例）。出货只走 `model_dump` / `dump_wire`（`NovaBaseModel.model_dump` 默认 `mode="json"`，Enum→value、datetime→ISO 已单点处理）——**禁止旁路** `json.dumps(model.__dict__)` 式手工打包，旁路会丢掉 json mode 语义。
  9. **RPC handler 签名即契约**：handler 签名必须类型化（`async def x(params: XxxParams) -> XxxResult`），体内一律属性访问（不散装取键）；注册表形状从签名注解自动推导（`register("x", x, domain=...)`——不重复声明 params_model/result_model）。形状模型集中在 `server/protocol/methods/shapes.py`；引用经 `shapes.` 模块前缀或模块级逐个 import，**禁止在函数内局部 import shapes**（`get_type_hints` 只查模块 globals——局部 import 会让推导静默失败）。shapes 需要引用 handler 侧类型时用 `if TYPE_CHECKING:` 块 + 字符串注解破环——不要为杜绝循环依赖而退回局部 import（future annotations 下运行时求值仍查模块 globals，局部 import 禁令与延迟求值互补、不冲突）。自由负载方法（无固定形状）注解保持 `Dict[str, Any]`，即不声明形状的语义。
  10. **声明 ≠ 校验（第四种表示：TypedDict）**：形状表示按成本选（规则 1/5/7），校验强度按信任边界选（规则 2/3）——两者正交。"有声明、零开销、不校验"的形状用 `typing.TypedDict` + 窄签名，**禁止拿 `Dict[str, Any]` 冒充已声明形状**（`Dict[str, Any]` 只保留给规则 9"无固定形状"的语义）；同一份 TypedDict 可以"库内零校验 + 边界 `TypeAdapter` 校验"两头用。行业锚点：openai-python 请求参数（[#1074](https://github.com/openai/openai-python/issues/1074) 显式拒绝 pydantic）、anthropic SDK `MessageParam`、LangGraph state 全部 TypedDict，解析产物/契约对象才 Pydantic。
  11. **回调/钩子签名同为契约（`Any` 纪律）**：`Callable` 的参数与返回值标注与数据形状同纪律——签名面的 `Any` 是"不声明形状"的语义声明，不是兼容性工具，更不是破环器。
     - **具体类型可得必标**：pi 对位 `onResponse: (response, model: TModel)`（payload 位才 `unknown`，model 位两个钩子都类型化）；codex 对位——回调参数全具体类型（全 workspace `dyn Any` 仅 8 处、自由负载 `Value` 作回调参数 0 处，编译器执法）。**同一对象在相邻签名间不得一处具体一处 `Any`**（本仓病例：`on_payload`/`on_response` 的第二参数同为 `Model` 对象却一标 `Model` 一标 `Any`——`Any` 让"钩子拿整对象"对类型系统隐形，安全面由此藏身）；
     - **破环禁拿 `Any`**：参数类型真实存在但直接 import 成环的，用 `if TYPE_CHECKING:` + 字符串注解（规则 9 先例）——环是模块组织问题，不许翻译成契约面的模糊；
     - **自由负载（规则 7 语义）才许 `Dict[str, Any]`/`Any`**，且须行内注释标注"自由负载"（pi 纪律的量化锚点：ai 包 `: any` 仅 2 处 vs `: unknown` 121 处——自由负载标记少而准）；
     - **返回值 `Any`** 仅许表达"可同步返回或返回 awaitable"双形态（pi `void | Promise<void>` 对位），并在注释里点明该语义；
     - **回调契约宜命名别名**：跨模块复用的回调签名定义 `TypeAlias`（codex `pub type Observer = Arc<dyn Fn(...)>` 对位；本仓先例 `KeyedLockFactory` / `UserToolFactory`）——签名一改全仓可见，不散落成匿名 `Callable`；
     - **执法**：评审按本条打回；机械执法归结构审计族（`structure-modeling.md` §五 fitness function）——公开签名面新增 `Callable[[…Any…]]` 须有上述型号之一的行内注释。

## 三、序列化与线上形态

- **双出口语义不同，永不混用**：`model_dump()`（持久化/内部——snake_case 字段名，磁盘存量兼容）与 `dump_wire()`（RPC 线上——`by_alias=True` camelCase）。持久化格式一个字节都不随线上重构变。
- **校验两制**：边界（JSONL 读入 / RPC 收参 / 包加载）一律 `model_validate()` 全量校验；内部自产自销一律 `model_construct()` 跳过校验。高频重放热路径靠 construct 达成零校验成本——**单一表示，不引 TypedDict 双表示**。
- **casing 三制，随契约主**：厂商 API 形状跟厂商（snake，OpenAI 兼容协议的固有形态）；nova 线上 camel（`dump_wire`）；nova 落盘 snake（`model_dump`）。**线上 camelCase 是基类内置**（`NovaBaseModel` 的 `alias_generator=to_camel` + `populate_by_name=True`，反序列化 snake/camel 双收），不逐字段手写 alias。
- **缺席=默认**：反序列化侧非必填字段必须带默认值——老数据/老客户端缺字段永远能读。
- **absent 卫生（渐进启用）**：线上 `Optional[X] = None` 的目标语义是"缺席"不是"显式 null"（TS 侧 `null ≠ undefined`，`in`/`Object.keys` 行为不同）。`dump_wire(exclude_none=True)` 作为开关提供：**新增线上类型/字段默认 absent 语义**；存量字段切换前先扫前端 `in` / `Object.keys` / 显式 null 消费点再逐批切；持久化 dump 永不开 `exclude_none`（这是"加 optional 字段 = minor"能成立的技术地基）。
- **确定性输出**：落盘/线上的 map 需要可 diff 时 `sort_keys=True`；Python dict 保插入序，字段声明顺序即输出顺序，声明时想清楚。
- **严格面 `extra="forbid"`**：不可信输入的严格形状（settings / manifest / 前端 payload 的闭合集）上 `ConfigDict(extra="forbid")`——拼错的键尽早炸，不静默吞（严格面才上 forbid；默认仍 ignore-extra 保前向兼容）。

## 四、契约与演化（双契约：线上 RPC + 落盘 JSONL）

- **minor / major 语义（两条契约共用）**：加 optional 字段 / 判别联合加成员 / 加 RPC 方法 / 加 entry 类型 = **minor**；删字段 / 改字段语义 / 改判别键 / 可选改必填 / 改字段类型 = **major**。major 升 `contractVersionMajor`（线上）或 `CURRENT_SESSION_VERSION`（落盘）并记 CHANGELOG。
- **开放集必须带兜底成员**：线上判别联合的未知变体以兜底成员吸收（`CustomItem` 模式）——旧代码读新数据不炸，前向兼容是契约义务不是前端自觉。
- **落盘契约版本化**：`CURRENT_SESSION_VERSION`（当前 = 3）不是装饰——读取路径必须有版本分派单点（哪怕当前只有 v3：先立 `version != CURRENT → migrate/明确拒绝` 的骨架），老版本会话文件留 fixture 做兼容测试。
- **对外续命用投影，不用双读**：内部模型演化后，旧外部词汇用**单向投影函数**续命（新内部形投影回旧事件词汇）——内部只养新形，旧协议是投影不是第二套读路径。
- **错误也是契约**：线上错误形状（`nova_server/protocol/errors.py` 的 code 与结构）同享 minor/major 演化规则。

## 五、标识符（语义 id 分档）

跨边界流动的 id 用语义类型，集中声明在 `nova_protocol/ids.py`：`SessionId` / `EntryId` / `RunId` / `ToolCallId`。分两档：

- **纯语义档（默认）**：`typing.NewType`——零运行时成本（线上 JSON、JSONL 落盘形态不变），靠 pyright 棘轮执法（`entry_id` 传给 `session_id` 形参在静态检查期爆炸）。**不加构造校验**（规则 3：id 自产自销）；
- **校验档（仅不可信来源）**：来自外部输入的路径/URL/id（用户粘贴的 git 地址、包源字符串）用 `NovaBaseModel` RootModel 或构造校验函数——在边界把非法值炸掉。可信内部流转一律走纯语义档。

推进走渐进棘轮：新代码签名必须用语义 id；存量按调用密度（`sessions/manager.py` → `core/agent_session` → shapes.py）逐目录清；边界处（JSONL 读入、RPC 校验后）一次性贴标。【待补】棘轮进度台账（哪些目录已清、哪批在飞）随推进成文，挂在 `docs/README.md` 矩阵的备注或本节。

## 六、开放集扩展（第三方词汇的合法进路）

框架词汇是**封闭联合**，第三方扩展经固定通道进，不散养：

- **判别联合 + 兜底成员**：框架变体枚举闭合；包级变体以 `NovaItem` 子类（住各 bundle）注册，线上以 `CustomItem` 兜底（`type` 开放字符串 + 额外字段透传，前端 `entry:<type>` 槽消费）；
- **命名空间式 type 名**：包级自定义 item/消息类型的 `type` 字符串必须带包命名空间前缀（`<包名>.<类型名>`，如 `bash.execution`、`web.search`）——防止第三方词汇与框架词汇及彼此撞名；
- **注册即合法**：自定义消息类经 `MESSAGE_TYPES` 类属性加载期注册进回载注册表（`nova_harness/sessions/message_types.py`），包缺席时旧会话中该类型消息降级为不透明消息，数据不丢；
- **第三方不动枢纽**：bundle 扩词永远走本节的开放集通道，不得向 `nova_protocol` 提交自己包专有的类型（枢纽只收跨组件共享词汇）。

## 七、控制面即词汇（演进方向）

取消、审批、打断这类控制流，**优先建模为消息/方法词汇**（事件联合的成员、RPC 方法），不在数据形状上挂可取消 token 字段——消息通道天然是控制总线。

**取消的三海拔模型**（对齐 codex `CancelErr` / `CodexErrorDetails` / `EventMsg::TurnAborted` 的分层）：

1. **原语层**：signal 中断一律抛 `AbortedError`（Exception 子类——可精确匹配、可带消息）。禁用两种假冒：裸 `asyncio.CancelledError`（那是"任务自身被取消"的 asyncio 内部语义，混用会干扰 Task/TaskGroup 的取消判定）与 `RuntimeError("...cancelled")` 式字符串假冒（会被泛型兜底误报成故障）。可中断等待原语单点归 `nova_async/abort.py`（`AbortScope` / `await_with_abort` / `await_with_signal` / `abortable_sleep` / `any_signal` / `operation_signal`），不复制第三份实现。
2. **领域层**：`except` 包装点必须先显式直通取消词汇，再进泛型兜底（`if isinstance(error, AbortedError): raise`——`auth/resolve.py` 先例）。取消落进 `ModelsError` 这类领域错误包装，就是把用户取消误报成故障。登录交互的"用户主动取消"用 `LoginCancelledError`（交互层词汇：宿主 Esc/关框/任务取消 → 流程优雅收尾），与传输层 abort 是两回事。
3. **线上层**：取消以事件/终态出网（`StopReason.ABORTED` + `AgentEndEvent`；codex 的 `EventMsg::TurnAborted` 同构），不在错误载荷里夹带字符串供下游嗅探。

现状注记：`AuthPrompt.signal` 死字段已删除——它在 TS 侧承载浏览器登录的 manual_code 竞速取消（JS 无结构化任务取消，只能给 prompt 挂 token）；nova 移植时竞速改走 `prompt_task.cancel()`（asyncio 原生取消），字段随之失效。**回来之门常开**：将来若需要"取消某个在飞 prompt 而不杀整个流程"的跨边界 per-prompt 取消，把它加回来是干净的 minor 演化（加 optional 字段）。`AuthInteraction.signal` 保留——它是 OAuth 设备码轮询的真实取消通道，属行为契约而非数据形状，随接口归 `nova_ai/auth`。取消词汇四件（`AbortSignal`/`AbortController`/`AbortedError`/`is_aborted`）与等待机制统一归 `nova_async`（`signal.py` + `abort.py`），不在枢纽另留一份。新增控制流交互时先按本节原则设计（能否建模为事件/方法/任务取消），token 字段是存量兼容手段不是新代码默认。

> 诊断与观测家族的完整建模（错误/警告/通知/诊断/日志/遥测的分类学、通道与持久化）见 `docs/crosscutting/diagnostics-modeling.md`——本节只管控制面与取消；**取消永远不是诊断分类学的成员**（该文铁律三）。

## 八、行为名分

公共数据类型与枢纽只放**形状 + 平凡不变量**（field validator / computed property / `to_*`·`from_*` 纯转换）——住户的方法加起来不改变任何外部状态。带 I/O、状态、编排的行为住消费模块，类型需要行为时以**消费方扩展函数**安置（行为函数写在消费方模块，不进公共数据枢纽）。

**终态无行为登记制**：枢纽不设白名单或“纯函数即可入住”的侧门。具体分流如下：

- `AbortSignal` / `AbortController` / `AbortedError` 与可中断等待统一归 `nova_async`；
- `ApiKeyAuth` / `OAuthAuth` / `ProviderAuth`、`AuthContext` / `CredentialStore` / `AuthInteraction` 等 Callable 配置与服务 Protocol 归 `nova_ai/auth`；
- `redact_secrets` 归 `nova_ai/utils/sanitize.py`；
- Shell 解析、execpolicy、权限编译与配置物化归 `nova_exec_policy`；
- `dump_agent_message` 这类不改变外部状态的公共序列化转换可留枢纽。

共享异常按语义归位：跨域控制/诊断词汇可住公共数据层；流程私有异常归行为包（如 `LoginCancelledError` 归 `nova_ai/auth`）；通用取消异常随 `nova_async`，不再以“异常”名义另设公共收容所。

Harness 的结构化消费接口按语义所有域分置：`config/protocol.py`、`model/protocol.py`、`sessions/protocol.py`、`resources/protocol.py`、`package/protocol.py`、`extensions/protocol.py` 及各 core 领域 `protocol.py`。UIContext 家族归 `ui/`；不得恢复跨领域集中 `types/protocols.py`。

## 九、文档纪律

- **公共类型必须有 docstring**：枢纽与边界包的每个公共类型/枚举一句话说明"它是什么、谁消费它"；
- **Optional 字段必须写明缺席语义**：`None` 时意味着什么、缺席时消费方该按什么处理（"not all providers emit it; consumers should ... when None"式）——optional 的语义不写明，演化时就是地雷；
- **不变量进 docstring，不靠口口相传**：跨模块不变量（"必须经 X 路径修改，否则 Y 会卡旧态"）写在类型/函数的 docstring 里。

## 十、契约测试

- **漂移测试常绿**：线上 schema（`nova-wire.schema.json`）与生成代码的漂移测试必须常绿（现状：`nova_server/tests/conformance/`）；
- **roundtrip 覆盖**：每个契约类型至少一例 `model_validate(model_dump())` 往返——判别联合是重点（防重建剥字段，规则 6）；
- **兼容 fixture**：老版本落盘文件（会话 JSONL / settings）留样，格式版本每升级一次跑一次读取测试；
- **三件套同 PR**：契约改动（shapes / schema / fixture）与其测试更新同 PR 落地，不后补；
- **纯洁性审计常绿**：`nova_protocol/tests/test_purity.py` 进 CI——零兄弟 import、零行为 IO、依赖最小，终态无行为白名单；另以 AST 对账公共 Callable 配置、服务 Protocol 与算法模块，防止从“纯数据”侧门重新入住。

## 附：静态检查与枚举

- **类型注解与静态检查**：全仓已大量使用类型注解；静态检查采用**渐进棘轮**——新增/重写模块必须过 pyright 基础档**零新增告警**（先从类型最全的新层做起），存量按目录逐次清零；`typecheck` 收进 pixi task。规则 10 的 TypedDict 窄签名与 NewType 语义 id 是纯静态约束，其价值由本条兑现。
- **枚举字段**：在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。
