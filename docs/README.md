# Nova 建模文档体系总图

> 本页是 `docs/` 的唯一索引点：坐标系定义、分工矩阵、状态约定、立项与引用纪律。
> 任何标准类改动先在本页矩阵找家；找不到家 = 要先改本页，不是直接开新篇。

## 坐标系（三轴，不重不漏）

拿任何一个零件/任何一篇文档，三个正交问题各问一遍，必有一个且只有一个家：

| 轴 | 判定问题 | 覆盖 |
|---|---|---|
| **纵线**（`lifecycle/`） | 它在数据流的哪一段？ | 一次 agent 运行的数据生命周期：输入 → 装配 → 循环 → 编排 → 执行环境 → 输出归约 |
| **横切**（`crosscutting/`） | 换一个段，这条纪律还成立吗？ | 每段通用的纪律面：形状 / 诊断与观测 / 并发与取消 / 配置 / 安全 / 持久化 / 测试 |
| **边界**（`boundary/`） | 它是内核与外部世界的交接面吗？ | 线上契约（协议层）、宿主（客户端） |

三轴之外是**工程层**（不参与坐标）：`architecture.md`（仓库事实描述）与
`development.md`（命令操作）——它们描述"怎么跑"，不规定"必须怎么做"。

## 分工矩阵（全貌：每面/每段的确切家）

### 纵线 `lifecycle/`（按段立篇）

| 段 | 家 | 状态/理由 |
|---|---|---|
| 输入 | 不立篇 | 队列/steering 语义归 `nova_harness` 包级文档与 `boundary/protocol-modeling.md` 方法表——纪律薄，无跨模块规则 |
| 装配 | `lifecycle/assembly.md` | 【待补】上下文装配总图——装配链顺序（persona→系统提示→工具广告→transform→compaction）、"model-visible" 纪律（注入有界/大注入评审）、孤儿 tool call 边界修复与合成 ID 稳定（prompt 缓存亲和）、compaction 家族生命周期 |
| 循环 | 不立篇 | 错误三通道归 `crosscutting/diagnostics-modeling.md` §三；并行门与取消归并发面；turn 生命周期归 `nova_agent` 包级文档 |
| 编排 | 不立篇 | WIP（bundle subagent 单形态）——等有第二形态再立档 |
| 执行环境 | 不立篇 | `packages/nova-exec-server/PROTOCOL.md` + `packages/nova_harness/examples/executor-integration.md` |
| 输出归约 | `lifecycle/reduction.md` | 【待补】归约纪律——消息形↔item 形双正典分工、live 通知与历史重建共用构造器（防两路漂移）、显式吞掉写理由、持久化准入口径；素材源 `packages/nova_harness/examples/server-item-layer-design.md` |

### 横切 `crosscutting/`（按面立篇）

| 面 | 家 | 状态 |
|---|---|---|
| 形状 | `data-modeling.md` | 【现状】（住所/选型/序列化/演化/开放集/取消三海拔/契约测试） |
| 结构 | `structure-modeling.md` | 【待补】住所三层/依赖方向/禁止清单/结构核对（fitness functions）——类型住所归 `data-modeling.md` §一不重复，本面管模块与包 |
| 诊断与观测 | `diagnostics-modeling.md` | 【现状】落地波次 1/2 完成；波次 3-5【待补】（见其 §十四） |
| 并发与取消 | `concurrency.md` | 【现状】取消原语族/token 树/两阶段中止/超时治理三层/死因分型/竞速裁决/任务台账；取消三海拔引用 `data-modeling.md` §七不重复 |
| 配置 | `configuration.md` | 【待补】（codex 调查改判立篇——配置是层栈对象不是值）：层栈与优先级（global/project/CLI/SDK/会话条目）、合并语义、逐键 provenance【目标态】、strict 未知键、schema 导出【待补】、旗标生命周期【目标态】、项目配置安全 denylist（trust 门） |
| 安全 | `security.md` | 【现状】清单篇；升格标准篇【待补】——威胁模型按受众分级（模型/子进程/日志/repr/落盘）、出口分类学（落盘/RPC/弹窗/日志/终端各自的清洗要求）、防线分层（值层脱敏→出口清洗→环境排除→命令审批→终端消毒）、验收测试 |
| 持久化 | 不立篇 | 双载分工："怎么落盘"归 `data-modeling.md` §三/§四；"什么落不落"归 `diagnostics-modeling.md` §六（含"先落盘后发声"顺序契约） |
| 测试 | `testing-modeling.md` | 【现状】（CI 门禁差距见其 §十四挂账） |
| 语言与本地化 | `localization.md` | 【目标态定稿】呈现语言归前端、语义锚点先行、诊断原文英文；语言包与三门禁【待补】 |

### 边界 `boundary/`（按表面立篇）

| 表面 | 家 | 状态 |
|---|---|---|
| 线上契约（JSON-RPC） | `protocol-modeling.md` | 【现状】+ 待补清单见其 §十一 |
| 宿主（TUI/Web 客户端） | 不立篇 | 归 `packages/nova_client/docs/` 四篇（design / frontend-backend-separation / tui-design / ui-primitives） |
| executor 线 | 不立篇 | `packages/nova-exec-server/PROTOCOL.md`（Rust 宇宙独立演进） |

## 状态约定（三档，全仓统一词汇）

每篇文首标注，含义只以本页为准：

- **【现状】**：已建成且有测试锁定——可以依赖；
- **【待补】**：设计已定、排期未做——按设计施工即可；
- **【目标态】**：方向已定、设计未定——动手前先补设计。

## 纪律（五条）

1. **一篇一律**：每篇只管一个面/段/表面，文首写清"管什么、不管什么"（边界引用邻居篇，不复述）；
2. **单一事实源**：跨篇只引用不重复——同一规则全仓只有一个成文点（例：取消三海拔唯一定义在 `crosscutting/data-modeling.md` §七）；
3. **立篇判据**：只有跨模块、跨段的纪律才立篇；段内行为归包级 `AGENTS.md`。立项先改本页矩阵；
4. **速览同步**：根 `AGENTS.md` 的速览只许是全文标准的子集；标准改动全文先行、速览同 PR 跟随；
5. **引用一律仓根相对路径**（`docs/...`），不写 `../` 相对跳。

## 演进与保鲜（体系自身的规矩）

1. **立项流程**：先改本页矩阵（登记家与理由）→ 按文首四段式成文（坐标行 / 管什么·不管什么 / 状态档 / 测试要求）→ 落地后矩阵转【现状】；
2. **旧篇退役**：内容被吸收或失效 → 删除 + 全仓引用清扫（CHANGELOG 历史叙述除外）；**被活代码引用的篇先改代码注释再删**；
3. **examples/ 边界**：`docs/` = 现行规范（随代码演进刷新）；`packages/nova_harness/examples/` = 现行设计文档与判决记录（8 篇，2026-10 清理后）。图纸若仍是现行真理且有持续读者 → 升格进 docs/ 对应轴；
4. **包级文档分工**：包级 `AGENTS.md`/`README` 写包内行为、结构、命令；跨包纪律只引用 `docs/` 不复述；冲突时 `docs/` 优先；
5. **宿主篇纳入【待补】**：`nova_client/docs/` 四篇补坐标行 + 套状态约定 + 进本页矩阵；
6. **保鲜门禁【目标态】**：文档↔代码对账脚本化——结构对账（`architecture.md` 包树 vs 磁盘实况）、数字对账（方法数/扩展名单 vs 实况）、引用路径存在性检查；收进 pixi task + CI，漂移即红。

## 读序建议

- 新人：`README.md`（本页）→ `architecture.md` → `crosscutting/data-modeling.md`；
- 动类型/序列化：`crosscutting/data-modeling.md`；
- 动线上契约（方法/事件/item）：`boundary/protocol-modeling.md`；
- 加错误/警告/诊断/通知：`crosscutting/diagnostics-modeling.md`；
- 写/重构测试：`crosscutting/testing-modeling.md`；
- 动模块/包结构、加 `utils/`/族文件：`crosscutting/structure-modeling.md`；
- 动用户面文案/多语言/注释语言：`crosscutting/localization.md`；
- 碰鉴权/文件/凭据：`crosscutting/security.md`。
