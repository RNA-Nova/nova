# Nova 测试建模标准

> 坐标：横切·测试（体系总图与状态三档约定见 `docs/README.md`）。

> 类型定义的住所与选型见 `docs/crosscutting/data-modeling.md`；本文是其对偶——**测试的分类、住所、假缝、数据、断言、环境与工具栈**全套约定，全仓通用（非某个包的局部规则）。
> 骨架以 codex 为第一参考（住所/假缝/契约套件/差分/注入时钟/重试分层/CI 调度），辅以 pi（IO 出口间谍/真实现测试件/示例保鲜/hermetic 环境），工具栈为 2026 现状（ruff/respx/inline-snapshot/pytest-asyncio 1.3）。

**总纲**：测试是**行为契约的居所**。每个测试锁定一条对外承诺——输入→输出、状态转移、错误语义、不变量——不锁定实现路径。力度按风险分层，假只在接缝，断言锁契约，环境默认密封，**能真不假**。

---

## 一、测试分类学（先认清测试的种类）

每个测试必须能归入一类；类别决定它的住所、力度与运行时机：

| 类别 | 定义 | 住所 | 运行时机 |
|---|---|---|---|
| **单元** | 单模块的行为契约（L0–L1 对象） | 镜像目录 | 默认全跑 |
| **契约（conformance）** | Protocol 接缝的实现无关契约 | `tests/contracts/` | 默认全跑 |
| **差分（parity）** | 同一能力的多实现产出一致 | 镜像目录（`parity` 标记可选） | 默认全跑 |
| **属性（property）** | 任意合法输入上的不变量（hypothesis） | 镜像目录 | 默认全跑 |
| **快照** | 形状类输出的漂移捕获（inline-snapshot） | 镜像目录 | 默认全跑 |
| **冒烟** | 胶水/装配的"线接上了"（L3） | 镜像目录 | 默认全跑 |
| **回归** | 历史 bug 的行为锁（带出处） | 镜像目录 | 默认全跑 |
| **集成（integration）** | 真实外部依赖（API/服务器/进程） | `tests/integration/` | `integration` 标记，定时/手动 |
| **评测（eval）** | LLM 行为质量（模型裁判） | 独立包（未建，后议） | 手动 |

## 二、测试住所

### 目录组织

- **目录镜像 src**：`tests/<子包>/test_<模块>.py` 与被测模块一一对应（src 有 `gateway/`，tests 就有 `gateway/`；src 根级模块对应 `tests/` 根级文件）。模块的不变量测试住对应测试文件，e2e/跨组件流程才进 `tests/integration/`。
- **禁 `unit/` 杂物间**：`unit/` 不是镜像目录，是"其余一切"的避难所——出现即意味着镜像断了，废止（存量逐批搬迁）。
- **合法的"非镜像"目录只有四个**：`contracts/`、`integration/`、`fixtures/`（测试资源文件）、`_helpers/`（跨文件共用件）——其余一律镜像。
- **conftest 分层**：`tests/conftest.py` = 全包环境（网络离线类密封）；`tests/<子包>/conftest.py` = 该域 fixture。conftest 里只放环境与小 fixture，不放测试。

### 命名

- **文件**：`test_<module>.py`（模块同名）；超大模块按切面拆 `test_<module>_<aspect>.py`（aspect 是模块内关注点名）；目录包建同名子目录（`providers/kimi_coding/` ↔ `tests/providers/kimi_coding/test_catalog.py`）。
- **类**：`Test<被测对象>` 或 `Test<被测行为族>`；测试 helper 一律 `_` 前缀（防 pytest 误收集）。
- **函数**：`test_<行为承诺>`——说行为不说实现；失败路径的命名即承诺（`test_failure_not_cached_next_call_retries`）。
- **禁含糊名**：`new`/`types`/`misc`/`utils` 式无指代命名；同名文件只允许出现在不同镜像路径上。

### 密度

不设数字指标——密度由分层（第三节）决定：**公开行为每条至少一例、失败路径具名、胶水一处冒烟即止**。一个模块该有多少测试，看它的行为承诺有多少条，不看行数。

### helper 与 e2e 设施

- **helper 就近**：单文件专用 helper 住文件内；跨文件共用件住 `tests/_helpers/` 或目录级 conftest；**生产包内不写 test_support 模块**（测试设施只住 `tests/`；发布 testing 子路径等第三方契约套件需求出现再议）。
- **e2e harness 是一等设施**：撑起多个 e2e 用例的装配代码（会话/存储/假服务器的组合构建器）当基础设施养在 `tests/_helpers/`（codex `core/tests/common/` 对位），不散落各测试文件。

## 三、分层与力度（写多少）

| 层 | 对象 | 力度 |
|---|---|---|
| L0 纯函数/引擎 | 修正引擎、翻译器、params 构建 | **穷举**：畸形、空值、合并优先级、命中/未命中 |
| L1 状态机/治理 | TTL/304/世代/缓存/发布闸/重试 | 每个状态转移一个用例，**失败路径必须具名** |
| L2 契约/词汇 | pydantic 模型、wire 形状 | roundtrip + 漂移门禁，**不测静态值** |
| L3 胶水/装配 | 工厂、转发、flag 接线 | 一处冒烟，只锁"线接上了" |
| L4 集成/e2e | 真实 API、真服务器 | `integration` 标记，默认不跑 |

**可测性是设计反馈**：行为覆盖不到公开面，说明公开面设计有问题——改设计，不改测试去迁就。

## 四、假缝与真实性阶梯

**真实性阶梯（偏好序，从高到低）**：真实实现（tempdir 上的真 FileStore、in-memory 真类）→ 公开接缝假实现（Protocol 边界，带调用记录）→ transport 层拦截（respx，请求全真跑）→ 函数缝 patch（`monkeypatch`，仅限全局 IO 出口）。**能真不假**——只有真实路径不可能/慢/非确定时才降级（codex 用真 `FileModelsCache` + tempdir 而非 mock 缓存，即此阶梯）。

1. **合法假缝只有两类**：组件公开接缝（`CatalogService`/`ModelsStore`/`CredentialStore` 等 Protocol 边界）；全局 IO 出口（`utils/http` 的 `request_with_abort`/`fetch_json`）。
2. **禁止的假**：patch 私有 helper；patch 第三方库内部；为绕难测性 patch 掉被测逻辑本身。
3. **关键网络路径必须有 HTTP 级用例**（respx transport 拦截，URL/headers/序列化/响应解析全真跑——codex wiremock 层对位）。
   - **假 LLM 服务器是共享设施不是逐文件手搓**：`tests/_helpers/fake_llm.py`——`RequestCapture`（恰好一次守卫 + 请求体断言入口，codex `single_request()` 对位）+ 双方言 SSE 构造器（openai 经 respx / anthropic 经 httpx2.MockTransport——httpx2 命名分叉 respx 拦不住）。**模板**：`tests/api_impls/test_openai_wire.py`、`tests/api_impls/anthropic_messages/test_wire.py`——断言永远两面：产出的消息（"回来的"）与捕获的请求体（"发出去的"）。真实 API 无法定向复现的场景（精确错误帧/特定 finish_reason/传输断裂）一律走这里，不许靠集成测试撞运气。
4. **契约套件**：Protocol 接缝的每个实现跑同一份契约测试（`tests/contracts/`，fixture 参数化遍历实现工厂）；新增实现的及格线 = 进 params 且全绿。
5. **差分测试**：同一能力的双实现（executor 各后端、FileSystemLayer 双实现、print/rpc 双模式）对同一输入产出一致。

## 五、测试数据建模

- **builder 函数构造测试数据**（`_model()`/`_fields()` 式：完整合法形状 + 具名默认 + 覆盖参数），禁止复制粘贴大 dict、禁止违反生产不变量的"残缺假模型"。
- **fixture 数据取真实样本**：payload 尽量来自真实响应（脱敏），不凭空虚构字段——假数据与现实漂移是集成 bug 的温床（pi 的模型 fixture 即真实形状）。
- **组合策略用全枚举矩阵**：模式/开关的组合空间（权限模式、三态名单、级别映射）一行一组态全枚举，防漏组合（codex `approval_matrix_covers_all_modes` 对位）。
- **数据驱动参数化，状态推进才循环**：同类输入用 `pytest.mark.parametrize`；测试体内的 for 循环断言是气味——例外是"逐步推进"语义本身（progressive parsing 类）。
- **fixture scope 纪律**：默认 function 级；class/module scope 只给纯只读重物，且不得持有事件循环资源（loop-scoped 资源跨用例共享是 flaky 温床）。
- **文件级 fixture 住 `tests/fixtures/`**，用 `tmp_path` 派生副本写入，不改原文件。

## 六、断言风格

- **值对象/模型 → 整体相等**（pydantic `==`；codex `pretty_assertions` 对位）；局部灵活用匹配器，不逐字段点名。
- **行为序列 → 锁调用次数 + 关键实参**（url/headers/validator/abort 语义），不锁偶然次序。
- **错误 → 锁异常类型 + 消息片段**，不锁完整文本（文本是呈现可自由改）；取消语义单独锁（`AbortedError` 精确类型，不被领域错误吞——对齐数据建模第七节的取消三海拔）。
- **形状类输出 → 快照或漂移门禁**，不逐字段硬扛；快照随 PR 提交，review 走 `--inline-snapshot=review`。
- **失败信息可行动**：默认 diff 不足以定位时，断言附上下文消息（预期状态 vs 实际状态的关键词）。
- 一个用例可锁一条完整行为链（pi 三连 refresh 同款务实）；同类输入用 `pytest.mark.parametrize` 表驱动，不复制用例。

## 七、异步与并发测试

- **竞速/中断用同步原语编排**（`asyncio.Event`/屏障/受控完成序），sleep 仅作 ≤0.05s 引导（测试"等待已开始"），不用 sleep 表达"等待完成"。
- 并发语义（世代守卫、inflight 去重、串行发布）**每个承诺一个用例**——并发 bug 不靠覆盖率撞出来。
- abort 语义全链路锁：举旗点、在飞中断、收尾不发布三处分别断言。

## 八、隔离与确定性

- **网络默认全禁**：双保险——conftest 把公开网络面离线（harness 站离线先例为通则），**默认套件加 pytest-socket 机械密封**（`--disable-socket --allow-unix-socket`；asyncio 自唤醒 socketpair 走 unix socket 需放行——nova_ai 先例）；真网络只住 `integration`（经 `pytest.mark.enable_socket` 显式解禁）。
- **环境密封**：测试永不触真实 `~/.nova`/HOME/环境变量——一律 `tmp_path` + in-memory + `monkeypatch`（自动回滚）。
- **时间**：新代码把**时钟/随机源当依赖注入**（codex `TimeProvider` 对位）；存量用 monkeypatch 封注入点；跨多调用点可引 time-machine；不睡觉（引导除外）。
- **事件循环**：一律 pytest-asyncio（strict）+ 显式标记——**全异步文件用模块级 `pytestmark = pytest.mark.asyncio`；混合文件对异步类/函数单独标**（模块级罩住同步函数会触发 PytestWarning）；**`asyncio.run` 禁入测试体**（脚本/示例除外）；nova_agent/nova_harness/nova_server 的 `auto` 模式为既有差异不扩散，逐步向显式 mark 收。
- **顺序无关**：任何测试不依赖执行顺序与共享进程状态（为将来 xdist 留门）。
- **平台差异显式化**：`sys.platform` 条件用 `pytest.mark.skipif` 写明理由，不藏隐式平台假设（codex 沙箱早退同款）。

## 九、不写清单

- **静态定义值**（常量表、枚举存在性）——门禁与生成器负责；
- **已删除逻辑的反向测试**；
- **私有函数直测**；
- **mock 自身行为**；
- **为覆盖率数字而写的测试**；
- **重复第三方库自身行为的测试**（json/httpx/pydantic 自有测试）。

## 十、测试代码质量（测试也是代码）

- **DAMP 优先于 DRY**：测试代码重可读与自明，重复优于错误抽象；但公共构造走 builder/fixture（第五节），语义复制（整段流程复制）禁止。
- **测试体内无业务逻辑**：无分支、无条件断言、无 try/except 吞断言——分支需求转参数化或拆用例。
- **同门禁**：测试与生产同 lint/format（ruff）；**新增测试文件过 pyright 基础档**（存量棘轮——类型漂移在测试 helper 里同样是债）。
- **评审即验收**：测试评审看三件事——行为破坏时它红不红（变异心态）、断言是否锁在契约上、环境是否密封。

## 十一、回归、变异、flaky 与重试

- **每个 bug 修复带先红后绿的回归测试**：回退修复→红→恢复→绿，结果写进提交说明；回归测试标题/注释注明出处（issue/PR/回退验证）。
- **不引入 Python 突变测试工具**（生态不成熟）；人工回退验证已覆盖其核心价值。
- **重试三层立场**：本地零重试（flaky 必修）；CI **integration 腿**允许运行器级一次瞬时重试（live e2e 的模型非确定性是固有噪音——pytest 侧为 CI 命令行里的 `--reruns 1`，**只许出现在该命令行，不进配置文件、不进默认套件**）；单元测试任何环境禁重试。
- **flaky 处置**：发现即修或即删，不留"已知 flaky"灰色地带；CI 统计重复失败的用例强制问责。
- **挂死防护**：pytest-timeout 全包默认 30s，进程级用例显式放大并注明理由（codex slow-timeout 对位）。

## 十二、集成测试与外部依赖

- **默认套件零网络密封**（条款八优先于一切便利）：e2e 不混入默认套件——密封是第一性的，pi 的"skipIf 混入默认套件"我们不采用。
- **三分节奏**：
  1. 默认套件：全密封，CI 门禁；
  2. `integration` 标记（特性契约 e2e）：本地手动入口文档化（`pixi run -e dev test-integration`）；**不进 CI**（codex 原话理由：CI 保持确定且免费；key 进 secrets 有泄漏面）；
  3. **巡检脚本**（全模型可用性扫描等运维面）：归 `scripts/` + pixi task（如 `check-live`），报告制输出，**不占测试套件名额**（codex 无此物——模型可用性靠目录治理与生产使用；pi 同）。
- **组织形态（pi 对位）**：一特性一文件（`test_<feature>_e2e.py`——流式契约/思考回放/缓存亲和/schema 兼容/错误面），**不按模型扫描**；探针选型来自注册表——每 provider 选 1 个最便宜/最新的当代模型（成本排序 + 代际优先，pi `selectOneCasePerProvider` 对位）。
- **凭据链**：`tests/integration/_helpers/` 统一解析——env 优先、`~/.nova/agent/auth.json` 兜底、OAuth 过期经自家 resolve 链刷新（pi `test/oauth.ts` 对位）；不得在各文件里散装读 env。
- **门**：缺凭据 `skip` 并给理由（不静默失败、不假装通过）；marker 在各包 pyproject 注册。
- **断言松于单测**：锁协议形态（字段存在/类型/事件序/错误类型），不锁供应商可变内容（价格/模型清单/文本内容）。
- **全栈冒烟**（codex live_cli 对位）：CLI/桌面级 live 冒烟最多 1-2 个，opt-in 标记，CI 不跑。

## 十三、工具栈定型

| 槽位 | 选型 | 说明 |
|---|---|---|
| 运行器 | pytest + pytest-asyncio ≥1.3（strict + 显式 mark） | — |
| HTTP mock | **respx**（transport 层） | 不引 vcr 录放 |
| 快照 | **inline-snapshot**（codex insta 对位） | 首批消费者出现时引入 |
| 属性测试 | **hypothesis**（容错解析/roundtrip 点状用） | Nova 序列化密集域的增量选项，不铺开 |
| 时间 | **time-machine** | 首批消费者出现时引入；不引 freezegun |
| 防挂死 | **pytest-timeout** | 全包默认 30s |
| lint/格式化 | **ruff**（check + format）【目标态】 | 现行 black/isort；ruff 接管单独批次迁移 |
| 覆盖率 | coverage.py + pytest-cov 本地可见；CI 用 diff-cover 卡改动行棘轮 | **不设全仓绝对指标**（pi/codex 均不卡） |
| 并行 | 暂不上 xdist（套件 <60s） | 超门槛再议 |
| 静态检查 | pyright 棘轮（src + 新增测试文件） | — |
| 性能基准 | 不进测试套件 | 性能回归靠 profiling 脚本，不设 CI 基准门禁 |

依赖纪律：全部进根 `pyproject.toml` 的 `[tool.pixi.feature.dev.pypi-dependencies]`；**无消费者的工具不提前安装**。

## 十四、CI 门禁与套件预算

- **目标态**：七套件 pytest 腿（protocol/ai/agent/harness/server/executor-client/bundle）+ ruff check + ruff format --check + pyright + 非集成矩阵；integration 不上 CI；lock 一致性（`pixi install --locked`）与生成物漂移门禁保留。
- **现状缺口**：`ci.yml` 只有 executor(rs/py) + bundle + blob 门禁——五包 pytest 腿与静态门禁未上，补腿第一优先。
- **套件时长预算**：单包非集成套件目标 <60s（当前 nova_ai ~18s / harness ~7s）；超预算先查测试分层错位（集成混入默认套件），再谈分片。
- **资源调度**（codex nextest test-groups 对位）：进程级/资源争抢型测试用 marker 分组（如 `process_heavy`），CI 侧限流或串行（`-m`/`-k` 分片），不与库单测抢并发。

## 十五、文档与示例保鲜

- **示例代码进测试**：`examples/*.py` 至少过编译级冒烟（pi "documentation example compiles and works" 对位）；
- **卫生检查脚本化**：lock 一致性、生成物漂移、AGENTS.md 与代码结构对账，收进 pixi task + CI 门禁，不靠自觉。

## 十六、维护与退役

- **测试随功能同生共死**：删功能必删其测试（codex"不为已删逻辑写测试"的对偶——不留已删逻辑的测试）；
- **改语义必改测试**：行为承诺变更时，旧测试的修改与实现同 PR；
- **测试文件也适用住所条款**：模块搬家/改名时测试同步搬迁，不留孤儿测试文件。

---

## 附：与 codex/pi 的对位速查

| 本标准 | codex | pi |
|---|---|---|
| 住所镜像 + helper 就近 | `*_tests.rs` 贴源码 + `tests/common` | `test/` 镜像 src |
| 真实性阶梯 | 真 FileModelsCache + tempdir → trait 假实现 → wiremock | 真 InMemory 实现 → 全局 fetch 间谍 |
| 契约套件 | `file_cache_implements_models_cache_contract` | `telemetry/testing/conformance.ts` |
| 差分测试 | `compact_remote_parity.rs` | — |
| 整体相等断言 | `pretty_assertions` | — |
| 注入时钟 | `TimeProvider` 构造注入 | `vi.useFakeTimers` |
| 重试三层 | `nextest.toml retries=1` + slow-timeout/test-groups | vitest 零重试 |
| 示例保鲜 | — | documentation example tests |
| 快照 | insta（125 文件） | 极少（12 处） |
| 覆盖率 | 无门禁 | 无门禁 |
| 卫生门禁 | cargo-deny / codespell / bazel lock | check-pinned-deps / ts-imports / shrinkwrap |
| 评测层 | — | `packages/evals`（自建 vitest-evals） |
