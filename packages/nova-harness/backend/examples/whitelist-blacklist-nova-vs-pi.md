# 黑白名单机制全景对比：nova vs pi

> **状态：本文 §5 诊断的弱点已修复（2026-08，资源与权限体系重构）**——
> 空值三态统一、yaml `!` 排除、settings 补 tools/user_tools/personas 键、
> `subagents` 死字段删除均已落地；实施定案见同目录
> `resource-permission-refactor.md`。以下对比保留作历史分析。
>
> 本文盘点两个系统中所有"允许/排除"机制的点位、层级与语义。
> 数据截至 2026-08，两侧均已核实到代码行（引用见各节）。
>
> - pi 侧源码：`pi/packages/coding-agent/src/`
> - nova 侧源码：`nova_harness/src/nova_harness/core/`、`nova_coding_agent/`

---

## 1. 体系总览

| | pi | nova |
|---|---|---|
| 层级数 | 两级：SDK/CLI 层 + 用户 settings 层 | 三级：SDK 层 + **agent.yaml 组合层（独有）** + settings 层，外加运行时面板层 |
| 名单语法 | **统一四前缀 pattern**：plain 包含 / `!` 排除 glob / `+` 强制包含 / `-` 强制排除（`package-manager.ts:697-793`） | 逐字段独立名单，无排除前缀语法 |
| 名单单位 | **文件路径 glob**（可裁包内单个文件） | **注册名**（最小单位一个工具/扩展/命令） |
| 空值语义 | 三态：缺席=全放、**空数组=全禁**、非空=按 pattern（`package-manager.ts:2166`） | 三种并存：`tools` 空=全禁；`extensions`/`user_tools`/`commands`/`skills` 空=全放；SDK 层 None=不设防 |

---

## 2. 总表（资源 × 层级）

### nova

| 资源 | SDK 传入层 | agent.yaml 组合层 | settings 用户层 | 运行时层 | 安全层 |
|---|---|---|---|---|---|
| tools | ✅ 白+黑 | ✅ 白（激活集，未声明=空集） | ❌ | ✅ /tools 面板绝对集（持久化到会话分支条目） | trust 门 project 包工具 |
| extensions | ❌ | ✅ 白（空=全放） | ❌ | ❌ | trust 门 project 扩展 |
| user_tools | ❌ | ✅ 白（空=全放） | ❌ | ❌ | trust 门 project |
| commands | ❌ | ✅ 白（空=全放） | ✅ 黑（`disabled_commands`） | ❌ | — |
| skills | ❌ | ✅ 仅裁包内（空=全放） | ❌ | ❌ | trust 门 project |
| prompts | ❌ | ❌ | ❌ | ❌ | trust 门 project |
| subagents | ❌ | ⚠️ 死字段（解析无人消费，已定删除） | ❌ | ✅ `agent_scope` 参数（每次调用选目录） | trust 发现期门控 |
| 前端 UI 资产 | — | — | ❌ | ❌ | trust 过滤 project 包（`partitionByTrust`） |

### pi

| 资源 | SDK/CLI 层 | settings 用户层 | 运行时层 | 安全层 |
|---|---|---|---|---|
| tools | ✅ `tools`/`excludeTools`/`noTools`（`sdk.ts:50-66`）；CLI `--tools`/`--exclude-tools`（`args.ts:257-259`） | ❌ | ✅ `setActiveTools` 扩展 API（不持久化到会话） | trust 门 project 包 |
| extensions | ✅ CLI 附加路径 | ✅ settings `extensions` 四前缀 pattern（`settings-manager.ts:77-111`） | ❌ | trust 门 project |
| user_tools | （无此资源类型） | — | — | — |
| commands | ❌ | ❌ | ❌ | — |
| skills | ✅ CLI 附加路径 | ✅ settings `skills` 四前缀 pattern；作者侧 frontmatter `disableModelInvocation` | ❌ | trust 门 project |
| prompts | ✅ CLI 附加路径 | ✅ settings `prompts` 四前缀 pattern | ❌ | trust 门 project |
| themes | ✅ CLI 附加路径 | ✅ settings `themes` 四前缀 pattern | ❌ | trust 门 project |
| subagents | ❌ | ❌ | ✅ `agentScope` 参数 + `confirmProjectAgents` 执行前弹窗 | **不走 trust**（弹窗替代） |
| 组合层 | — | **pi 无 agent 组合声明概念** | — | — |

---

## 3. 逐资源对比要点

### 3.1 tools（唯一两侧多层都有的资源）

- **pi**：SDK 三件套 `tools`（allow）+ `excludeTools`（deny，allow 之后应用）+ `noTools: "all"|"builtin"`（默认集抑制——pi 默认自带 read/bash/edit/write 四内置工具，需要开关压制默认值）。运行时 `setActiveTools` 为扩展 API，示例 `tools.ts` 是 SettingsList 面板，不持久化到会话。
- **nova**：四层漏斗——SDK `tools`/`exclude_tools`（deny 先判、allow 后判，`manager.py:110-118`）→ agent.yaml `tools` 白名单（闸 3）→ 注册表 → /tools 面板激活集（持久化 `tool-panel` 条目，分支安全恢复）。
- **关键差异**：nova 无 `noTools`（框架零内置工具，默认集本为空，无需压制）；nova 的 /tools 面板选择**持久化进会话分支**，pi 不持久化。

### 3.2 extensions

- **pi**：settings 层四前缀 pattern，按文件路径裁（可裁包内单个文件）。
- **nova**：仅组合层按注册名白名单；用户层无法关扩展。

### 3.3 commands

- **pi**：无命令级 allow/deny（grep `disabledCommands` 零命中）。
- **nova**：唯一黑白双层——组合层 `commands` 允许集（白）+ settings `disabled_commands`（黑），先白后黑。

### 3.4 skills

- **pi**：settings pattern 可裁任意来源 skill。
- **nova**：组合层 `skills` **仅裁包内**（用户级/项目级永远裁不到——"skill 是文本不是能力"的定案，属有意分歧非缺失）；`disable_model_invocation` 与 pi 同义。

### 3.5 prompts / themes

- **pi**：均可经 settings pattern 裁剪。
- **nova**：prompts 全放（文本资源不定名单）；themes 归 Node 层无名单。

### 3.6 subagents

- **pi**：无名单；运行时 `agentScope`（user/project/both）+ `confirmProjectAgents` 执行前弹窗；**不走 trust 门控**。
- **nova**：`agent.yaml` 的 `subagents` 字段解析后无人消费（死字段，已定删除）；实际约束 = `agent_scope` 参数 + trust 发现期门控（不信任的项目源不进发现结果，模型连名字都看不到）+ worker.yaml 靠 `tools` 不含 subagent 防递归（名单复用，非独立机制）。拦截点比 pi 早（发现期 vs 执行前）。

### 3.7 安全门控（trust）

- 两侧同构：项目不被信任时项目域资源**发现期整体排除**。
- pi 门面：settings/SYSTEM.md/APPEND_SYSTEM.md/扩展/包；nova 更全：+skills/prompts/上下文文件（AGENTS.md 链）/`.nova/agents`/前端 UI 资产。
- 存储：pi `settingsManager.projectTrusted`；nova `trust.json`（ProjectTrustStore，按 cwd 记录——本身可看作"项目路径的黑白名单"）。

---

## 4. 语义规则对比

| 维度 | pi | nova |
|---|---|---|
| 空值 | 三态：缺席=全放 / 空数组=全禁 / 非空=按 pattern | 三种并存：`tools` 空=全禁；`extensions`/`user_tools`/`commands`/`skills` 空=全放；SDK None=不设防 |
| 排除表达 | `!` 前缀任意打洞；`+`/`-` 强制级覆盖 | 仅两处黑名单：SDK `exclude_tools`、settings `disabled_commands`；其余纯白名单，表达不了"全放减一" |
| 裁决顺序 | allow → exclude（`excludeTools` 后应用） | excluded → allowed → agent.yaml 白名单（`manager.py:110-122`） |
| 粒度 | 文件路径 glob（可裁包内单文件） | 注册名（最小单位一个工具/扩展/命令） |

---

## 5. 诊断

**nova 独有的（设计意图，非差距）**：

1. agent.yaml 组合层——角色系统的核心，pi 无对应概念；
2. /tools 面板选择持久化到会话分支（pi 不持久化）；
3. commands 黑白双层；
4. skills 仅裁包内的来源分治。

**nova 的现实弱点**：

1. 空值语义不统一（`tools` 空=全禁 vs 其余空=全放），包作者需硬背；
2. 无排除前缀语法，组合层表达不了"全放减一"（SDK 层有 `exclude_tools` 但没下沉到 yaml）；
3. settings 用户层近乎空转（仅 `disabled_commands` 一根独苗）——pi 的用户层四资源 pattern 是用户侧主力开关；
4. `subagents` 死字段（已定论删除，见变更记录）。

**对齐方向（候选，未定案）**：空值语义三态化（缺席=全放 / 显式空=全禁 / 非空=名单）；组合层引入 `!` 排除前缀；settings 用户层补齐资源开关。`+`/`-` 强制级是 pi 为"覆盖包 manifest 默认裁剪"设计的，nova 的包 manifest 无默认裁剪，用不到四级。
