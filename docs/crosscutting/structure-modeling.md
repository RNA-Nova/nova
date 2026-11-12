# Nova 结构建模标准

> 坐标：横切·结构（体系总图与状态三档约定见 `docs/README.md`）。

> 本页管**模块与包的住所（placement）、依赖方向、结构核对机制**；类型的住所与表示选型归 `docs/crosscutting/data-modeling.md`（§一/§二，本页引用不重复）；包内行为细节归各包 `AGENTS.md`。
> 状态：**待补**（设计定稿；落地进度以 `docs/README.md` 矩阵为准）。
> 测试要求：见末节。

**总纲**：**一机制一家，granularity 跟机制不跟体量**——86 行的原语也配拥有独立住所，"太小不值得"是杂物间的开端。住所即依赖裁决：谁能看见谁是显式设计，不是默认可达。编译器管不到的地方（Python 无 crate 边界），用**结构核对测试（fitness function）**机械执法，不靠自觉。

## 一、行业锚点（设计依据存档）

- **反模式**：junk drawer / kitchen-sink——按形态归拢（"都是工具"）而非按机制归拢（"都是取消原语"）；长期失修即架构侵蚀（architecture erosion）。治理能力的行业名：**架构守护（architecture governance）**，核心纪律是**内聚（cohesion）**，机械核对叫 **fitness function**（*Building Evolutionary Architectures*）。
- **codex 实证（第一参考）**：无 `common/` crate；跨 crate 共享走**机制级微 crate**（`async-utils` 仅 `or_cancel` 一件、86 行；`arg0`/`build-info`/`ansi-escape` 同款一机制一 crate）；crate 内 `util.rs` 全仓仅 6 个、crate 边界外不可见——住所纪律由编译器与 workspace 免费执法。
- **平台优先**：codex 的自建只补平台空缺——`CancellationToken`/`TaskTracker` 来自 tokio-util（成熟平台库），`async-utils` 仅装平台没有的 `or_cancel`。我们的对应物：stdlib/asyncio 没有的才自建，建即按 §二选址登记；词汇类被契约注解锚定的件（`signal.py`）不随机制搬家——词汇与机制分居（tokio-util vs async-utils 对位）。
- **Python 粒度换算**：Rust 的 crate 边界在 Python 没有编译器等价物；**我们的"微 crate" = 单模块 + 登记制 + 审计测试**，不是新建包——包脚手架成本（pixi 接线 / CI 腿 / import 清扫）远高于 crate，粒度自然降一档。
- **工具锚点**：import-linter（Python 正典，layers / forbidden / independence 契约；Java ArchUnit、TS dependency-cruiser、Rust cargo workspace 对位）。审计条数超出手写维护力时引入 import-linter，不自造契约语言。

## 二、住所三层（新模块/新包的判定序）

按序三问，取第一个命中的层：

| 层 | 判定 | 住所 | 例 |
|---|---|---|---|
| 1 跨包行为件 | 消费方跨 ≥2 个包 | **nova_protocol 登记制**（基础原语族——全仓签名通用、零业务语义、仅 stdlib） | `signal.py`（取消原语）、`sanitize.py`（出口清洗） |
| 2 包内族件 | 消费方 ≥2 个模块（同包） | **包级族文件**（`utils/` 或 `_shared/`）——三条件：**≥2 个真实消费方**（看 import 不看"将来可能"）/ **一族一文件** / **docstring 首行声明族与纪律** | `nova_ai/utils/http.py`（HTTP 薄封装原语族）、`errors.py`（归一枢纽） |
| 3 单消费方件 | 只有 1 个真实消费方 | **消费方隔壁**——同文件私有函数或同目录兄弟模块，不进任何 utils/_shared | `_stream.py` 内局部 helper |

配套规则：

- **升格入口**：包内件一经**第二个包** import，立即搬家进登记制（原子批：挪文件 + 全仓 import 一次改完 + 旧路径引用归零）——不留"跨包经模块路径 import"的先例（`task_tracker` 为按本条升格的首个成员）。
- **升格出口**：登记族成员持续增多 → 升格为**机制命名的独立叶子包**（命名即族名；**永不命名为 utils / common**），枢纽恢复绝对纯形状。**触发线（硬）**：异步族为例——`abort.py` 出现第一个包外 import，或登记制异步族成员超过 4 件——任一触发即建包；未触发不预建。**已落地**：取消/等待机制族已升格为 `packages/nova_async`（`abort.py` 六件 `AbortScope`/`await_with_abort`/`await_with_signal`/`abortable_sleep`/`any_signal`/`operation_signal` + `task_tracker.py` 的 `TaskTracker` + `keyed_lock.py` 三件 `KeyedLockFactory`/`in_memory_keyed_locks`/`default_keyed_locks` + `backoff.py` 的 `backoff_delay_ms` + `deadline.py` 的 `Deadline`——词汇 `signal.py` 锚定枢纽不动，机制件全在 nova_async）。
- **算法件随行**：输入输出都是枢纽词汇的纯函数（`exec_server_intent.py`、`exec_server_policy.py` 等）同样登记，与运行时原语分族（`docs/crosscutting/data-modeling.md` §八）。

## 三、依赖方向

- **分层拓扑**：`nova_protocol`（形状 + 登记原语）< `nova_async` / `nova_catalog`（两个独立叶子：取消/等待机制族、模型目录构建与纯算法）< `nova_ai` < `nova_agent` < `nova_harness` < `nova_server` < `nova_coding_agent`（bundle）；`nova-exec-server-client` 与 `nova_ai` 平级；`nova_client` 在 Node 侧仅经线上契约消费，不进 Python 依赖图；executor Rust 宇宙独立演进。`nova_catalog` 不得 import `nova_ai`，由 `packages/nova_catalog/tests/test_structure.py` 机械执法。
- **只许向下**：任何模块不得 import 上层（如 `utils/` 不得 import `gateway/`、`providers/`）；族文件之间避免互引，必须互引时族内也按向下排序。
- **破环归位**：循环依赖是设计错误；合法破环走 `typing.Protocol` 反向契约或事件通道（形状纪律归 `docs/crosscutting/data-modeling.md`），不靠局部 import 苟过去。

## 四、禁止清单

- **禁杂物间**：`nova_common` / `shared_utils` / `misc.py` / `helpers.py` 式住所一律不立；
- **禁跨包私拉**：行为件越包经模块路径 import（见 §二升格入口）；
- **禁第三份原语实现**：可中断等待原语单点归取消原语族（`docs/crosscutting/data-modeling.md` §七）；
- **禁"太小合并"**：机制不因行少而并家（§一 granularity）；
- **禁上行依赖与局部 import 破环**（§三）。

## 五、结构核对（fitness functions）

- **枢纽纯洁性审计**（`nova_protocol/tests/test_purity.py`）：零兄弟 import、零行为 IO、依赖最小；登记白名单与 §二第 1 层名单**两处同步**（审计测试 + `docs/crosscutting/data-modeling.md` §八）。
- **包级住所审计**：每个行为包一测试——`utils/`（及 `_shared/`）逐模块断言 docstring 首行族声明存在 + 登记清单对账（清单住测试内）；**未登记的新模块即红**。
- **依赖方向审计**：包级 import 扫描断言 §三拓扑——上行与跨级私拉即红。
- **机制族单点核对**：取消/等待机制件单点归 `nova_async`（§二升格出口已落地）——旧路径（`nova_ai/utils/abort`、`nova_protocol.task_tracker`、`nova_protocol.signal.await_with_signal`、`nova_ai.auth.refresh_locks`、`nova_ai.gateway.deadline`）引用归零，升格批次以 grep 级断言验收（§六第二条；已机械化为 AST 扫描断言，住 `packages/nova_async/tests/test_structure.py`）。

## 六、测试要求

- 住所审计与依赖方向审计常绿，进各包默认套件；
- 升格搬家必带**旧路径引用归零**断言（grep 级或 import 扫描）；
- 族文件消费方数与住所层对账（第 2 层件的第二消费方消失时降回第 3 层——住所随消费实况演进，不留空挂）。
