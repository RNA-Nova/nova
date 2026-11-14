# Nova 结构建模标准

> 坐标：横切·结构（体系总图与状态三档约定见 `docs/README.md`）。

> 本页管**模块与包的住所（placement）、包内组织、依赖方向、结构核对机制**；类型的住所与表示选型归 `docs/crosscutting/data-modeling.md`（§一/§二，本页引用不重复）；包内行为细节归各包 `AGENTS.md`。
> 状态：**【现状】**（共享数据、边界协议、行为机制与组件类型已完成分址；后续新增结构按本页判定序执行）。
> 测试要求：见末节。

**总纲**：**一机制一家，granularity 跟真实消费与依赖方向，不跟体量或形态**——共享领域数据可以集中在 `nova_protocol`，行为与算法则按机制归位。独立 Python 包只在“包内安置会形成上行依赖、循环依赖、错误运行时依赖，或存在独立版本契约”时设立；否则使用业务包内模块。编译器管不到的地方（Python 无 crate 边界），用**结构核对测试（fitness function）**机械执法，不靠自觉。

## 一、行业锚点（设计依据存档）

- **反模式**：junk drawer / kitchen-sink——按形态归拢（“都是工具”）而非按机制归拢（“都是取消原语”）；长期失修即架构侵蚀（architecture erosion）。治理能力的行业名：**架构守护（architecture governance）**，核心纪律是**内聚（cohesion）**，机械核对叫 **fitness function**（*Building Evolutionary Architectures*）。
- **codex 实证（第一参考）**：`codex-protocol` 集中共享领域语言；`app-server-protocol` 与 `exec-server-protocol` 分别守外部版本边界；`execpolicy`、`async-utils` 等机制独立成 crate。我们学习的是这种**领域词汇 / 外部协议 / 行为机制**的分工，不是复刻其所有类型或 crate 数量。
- **包内组织实证**：Codex 不设统一的公共类型收容层。`core/tools/sandboxing.rs` 把审批类型、trait、默认值计算与沙箱辅助放在一起；`orchestrator.rs` 把结果类型放在编排器旁；`execpolicy/decision.rs` 让值对象跟随策略机制；`app-server-protocol/protocol/v2/` 按 thread/turn/item/config 等线上领域分文件。`config/src/types.rs` 与 `thread-store/src/types.rs` 则表明：只有当一个领域确有内聚的公共类型族时，才可集中成领域类型模块。结论是**行为域优先、类型跟随语义、公共面显式收窄、文件不按行数机械拆分**。
- **平台优先**：codex 的自建只补平台空缺——`CancellationToken`/`TaskTracker` 来自 tokio-util，`async-utils` 仅装平台没有的 `or_cancel`。我们的对应物：stdlib/asyncio 没有的才自建，建即按 §二选址；取消词汇与等待机制统一归 `nova_async`，不在数据枢纽另留一份。
- **Python 粒度换算**：Rust crate 边界在 Python 没有编译器等价物。默认边界是**包内模块 + 审计测试**；只有真实依赖需要时才升格为发行包。`nova_ai.catalog` 已因只有 `nova_ai` 一个生产消费者而收回 `nova_ai/catalog`，不得为形式对齐再次拆出。
- **工具锚点**：import-linter（Python 正典，layers / forbidden / independence 契约；Java ArchUnit、TS dependency-cruiser、Rust cargo workspace 对位）。审计条数超出手写维护力时引入 import-linter，不自造契约语言。

## 二、住所三层（新模块/新包的判定序）

按序三问，取第一个命中的层：

| 层 | 判定 | 住所 | 例 |
|---|---|---|---|
| 1 跨包行为件 | 平级或下层多个包真实消费；放任一业务包会形成上行/循环/重型运行时依赖 | **机制命名的独立叶子包**——名称即机制，永不叫 utils/common | `nova_async`（取消/等待）、`nova_exec_policy`（执行策略） |
| 2 包内族件 | 消费方 ≥2 个模块（同包），或虽有下游包消费但依赖方向天然向下 | **语义所属包内的族文件**（`utils/` / `_shared/` / 领域目录）——≥2 个真实消费方、一族一文件、docstring 首行声明族与纪律 | `nova_ai/utils/sanitize.py`（出口清洗）、`nova_ai/utils/http.py` |
| 3 单消费方件 | 只有 1 个真实消费方 | **消费方隔壁**——同文件私有函数或同目录兄弟模块，不进任何 utils/_shared | `_stream.py` 内局部 helper |

配套规则：

- **跨包不等于独立成包**：下游包直接 import 上游公共 API 是正常依赖，不得为了“共享”再抽包。`nova_agent`、`nova_harness`、`nova_server` 使用 `nova_protocol` 的公共词汇即属此类。
- **升格入口**：包内行为件被**平级或下层第二个包**真实消费，且继续放原包会形成错误依赖时，才迁往机制叶子包。迁移必须原子完成：挪文件 + 全仓 import 一次改完 + 旧路径引用归零，不留 re-export。
- **外部协议单独判定**：带独立协议版本、被多个实现直接消费的线上 DTO 归边界协议包。当前只有 `nova_exec_protocol` 需要独立；`nova_server/protocol` 的 Python 消费方均在 `nova_server` 内，TS 走生成物，故留包内。
- **纯函数不自动升格**：Shell 解析、策略求值、权限编译等因属于独立执行策略机制而归 `nova_exec_policy`；`redact_secrets` 的实际生产消费只有 AI 与下游 Agent，故归 `nova_ai/utils/sanitize.py`，不设 `nova_security`。
- **不预建未来包**：`nova_ai_protocol` / `nova_agent_protocol` / `nova_server_protocol` / `nova_security` 均无真实独立消费理由，不设立。

## 三、包内组织（领域内聚优先）

### 1. 判定顺序

新文件或新类型按以下顺序决定包内住所：

1. **只服务一个函数/类/模块** → 使用点同文件；
2. **服务同一行为域的多个模块** → 该行为域的公共模块，类型、trait、纯函数与实现可以同文件保持内聚；
3. **本身就是单一、内聚的公共类型领域** → 可用领域内 `types/` 或 `types.py` 集中表达公共 API；
4. **属于外部版本化线上契约** → `protocol/`；
5. **属于跨领域共享的纯数据正典** → 全局 `nova_protocol`；
6. **被平级包共享且形成错误依赖的行为机制** → 按 §二第 1 层独立。

下游包使用某个领域类型时，直接从该领域的公共模块导入，**不因为消费方跨包就把类型上移**。公共与私有由领域模块和包根门面表达，不依赖抽象的 `contracts/` 目录。

### 2. 领域目录的组织

- **类型与行为同域**：公共数据、Protocol、ABC、hook、配置、异常与行为实现可以在同一领域目录内分文件内聚，不要求“类型一层、行为另一层”。
- **领域内聚的 `types/` / `types.py`**：只有当模块本身就是单一公共类型领域时才允许存在，例如 `nova_agent/types` 集中表达 Agent 运行时与扩展 API，`nova_harness/sessions/types.py` 只聚合会话公共数据。它是领域模块，不是全包类型的默认入口。
- **跨领域混合类型层**：多个变更理由不同的领域不得集中成 `types/`、`contracts/` 等公共收容层。原 `nova_harness/types` 覆盖 Session、资源、包管理、配置、压缩、UI 等独立领域，已按消费方完成收编，不得恢复。
- **领域公共模块**：`definition.py` 放公共行为定义，`ports.py` 放宿主接口，`entries.py` 放落盘条目，`models.py` 仅限明确的数据模型；名称必须指向领域，不设 `base.py`、`common.py`、`misc.py`。
- **`protocol/`（可选）**：只放外部版本化线上契约——JSON-RPC、wire DTO、方法、通知、握手、版本、Schema 及其 item。普通 Python API 不叫 protocol。
- **`utils/` / `_shared/`**：分别放同包共享函数族与子系统私有实现，不装包级公共类型；单消费方函数与类型留在使用点。
- **不强制统一目录**：`nova_ai` 按 gateway/auth/catalog 等领域组织，`nova_async` 按机制分文件，`nova_exec_policy` 按策略与权限分目录。

### 3. 目标结构正例

- `nova_agent/types/`：作为**单一、内聚的 Agent 公共运行时与行为类型领域保留**——`context.py` / `state.py` / `tool.py` / `hooks.py` / `tool_execution.py`；`base.py` 的混合别名按真实消费点拆解。无需改名或为了分散而分散。
- `nova_harness`：`sessions/`（entries/tree/config/messages + 持久化行为）/ `resources/`（资源公共类型 + loader）/ `package/`（包类型 + 安装解析）/ `config/`（设置与鉴权配置）/ `core/domains/compaction/`（压缩类型与行为）/ `ui/`（UI 契约与桥接）/ `core/agent_session/controllers/execution.py`（审批与执行编排词汇）。原 `types/` 按真实消费方完整收编。
- `nova_ai/auth`：`definitions.py` 管 Provider 行为定义与输入，`ports.py` 管宿主接口与流程取消异常。
- `nova_server/protocol`：`methods/` + `items.py` + `notifications.py` + `jsonrpc.py` + `router.py` + `serialize.py` + `schema_export.py`。线上 item 与 RPC 同属一个版本边界，不另设平行类型中心；`reduction/` 仍在外侧负责翻译。
- `nova_exec_protocol`：只有 `wire.py`；`nova_exec_policy`：`intent.py` / `config.py` / `verdict.py` / `policy/` / `permissions/`，不再额外套公共类型目录。

### 4. 公共面与 Python 导入副作用

- **模块默认不挂包根门面**；`__init__.py` 只导出稳定公共 API，业务包不得中转 `nova_protocol` 类型。类型与行为同居的 Harness 领域入口保持零急切 re-export，消费方直连叶子模块或本领域 `types.py` 聚合，避免导入数据时反向拉起整套运行时。
- `from nova_agent.types import AgentState` 会先执行 `nova_agent/__init__.py`，再执行 `types/__init__.py`——**领域类型模块不天然承诺轻量依赖**。包根 eager import 必须克制；真正需要轻量消费的共享数据才放 `nova_protocol`。
- 测试专用 helper 不挂公共门面；可复用测试设施归 `testkit/` 或 tests。
- 不用局部 import 解决循环依赖；优先重新归位，确需延迟求值时使用 `TYPE_CHECKING`。
- 文件是否拆分不看行数，看独立消费者与独立变化理由。

### 5. 第三方、生成物与测试

- **开放集基座归框架，具体扩展词汇归扩展包**：`CustomAgentMessage` 基座归 `nova_protocol`，`CustomItem` 基座归 `nova_server/protocol`；bundle 自定义消息/item/payload 与注册实现留 bundle。
- 手写公共类型与生成代码不得混放；`models_generated.py`、provider `models.py` 和目录 JSON 继续按 Catalog 生成边界管理。
- 测试目录镜像被测模块而非类型形态：`types/state.py` → `tests/types/test_state.py`，`tools/execution.py` → `tests/tools/test_execution.py`，`sessions/entries.py` → `tests/sessions/test_entries.py`，`protocol/items.py` → `tests/protocol/test_items.py`。
- 协议金标与版本 fixture 跟协议包走；`testkit/` 不是包公共 API。

## 四、依赖方向

- **共享数据与主线**：`nova_protocol`（共享领域纯数据，零兄弟包依赖）< `nova_ai`（含包内 `catalog/`）< `nova_agent` < `nova_harness` < `nova_server` < `nova_coding_agent`（bundle）；`nova_async` 是独立取消/等待机制包，供各运行时向下消费。
- **executor 子图**：`nova_exec_protocol` 只放版本化 wire DTO；`nova_exec_policy` 依赖 `nova_exec_protocol` 与 `nova_protocol`；`nova-exec-server-client` 依赖 `nova_exec_protocol`、`nova_exec_policy`、`nova_async`；`nova_harness` 在上方组合 client、协议与策略。client 保持连接层，不把策略算法塞回去。
- **包内目录方向**：`nova_ai.catalog` 只依赖 `nova_protocol` 与自身，不得 import AI 运行时，由 `packages/nova_ai/tests/catalog/test_structure.py` 机械执法。
- **只许向下**：任何模块不得 import 上层（如 `utils/` 不得 import `gateway/`、`providers/`）；族文件之间避免互引，必须互引时族内也按向下排序。
- **破环归位**：循环依赖是设计错误；合法破环走 `typing.Protocol` 反向契约或事件通道（形状纪律归 `docs/crosscutting/data-modeling.md`），不靠局部 import 苟过去。

## 五、禁止清单

- **禁杂物间**：`nova_common` / `shared_utils` / `misc.py` / `helpers.py` 一律不立；跨领域混合的 `types/` / `contracts/` 公共类型收容层同样不立（单一内聚领域的 `types/` 与 `tests/contracts/` 契约测试套件不受此限）；
- **禁行为登记制**：`nova_protocol` 不设 signal、sanitize、exec 算法等行为白名单；行为必须直接归机制所属包；
- **禁跨包私拉**：行为件越包经模块路径 import 私有实现；公共契约必须从语义所有方的公开入口导入；
- **禁第三份原语实现**：可中断等待原语单点归 `nova_async`（`docs/crosscutting/data-modeling.md` §七）；
- **禁“太小合并”与“共享就拆包”**：机制不因行少而并家，也不因被两个下游包使用就多建发行包；
- **禁上行依赖与局部 import 破环**（§四）。

## 六、结构核对（fitness functions）

- **枢纽纯洁性审计**（`nova_protocol/tests/test_purity.py`）：零兄弟 import、零行为 IO、依赖最小；终态不设行为白名单，公共 Callable 配置、服务 Protocol 与算法模块均不得入住。
- **包级住所审计**：每个行为包一测试——`utils/`（及 `_shared/`）逐模块断言 docstring 首行族声明存在 + 登记清单对账（清单住测试内）；**未登记的新模块即红**。
- **依赖方向审计**：包级 import 扫描断言 §四拓扑——上行与跨级私拉即红。
- **机制族单点核对**：取消/等待机制件单点归 `nova_async`——旧路径（`nova_ai/utils/abort`、`nova_protocol.task_tracker`、`nova_protocol.signal`、`nova_ai.auth.refresh_locks`、`nova_ai.gateway.deadline`）引用归零；exec wire 与策略旧路径 `nova_protocol.exec_server_*` 引用归零。
- **包内住所审计**：`protocol/` 只含外部版本契约；公共类型必须能指出语义所属领域，单模块私有类型不得上移至公共目录。

## 七、测试要求

- 住所审计与依赖方向审计常绿，进各包默认套件；
- 升格搬家必带**旧路径引用归零**断言（grep 级或 import 扫描）；
- 族文件消费方数与住所层对账（第 2 层件的第二消费方消失时降回第 3 层——住所随消费实况演进，不留空挂）；
- 独立协议包必须有版本、roundtrip 与跨实现金标；仅包内边界协议必须有 Schema 漂移测试。
