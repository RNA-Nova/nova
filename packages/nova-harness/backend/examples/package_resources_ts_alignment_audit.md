# 包管理与资源加载：Python（Nova）vs TS（pi）逐层对照审计

> 审计范围：从**包管理**到**资源加载结束**的完整链路。
> 方法：逐文件通读两侧实现后逐项对照，非抽样。
>
> - Python 侧（Nova）：`packages/nova-harness/backend/src/nova_harness/package/**`、`core/resources/**`、`core/harness/skills.py`、`core/config/settings/manager.py`（包相关段）
> - TS 侧（pi）：`packages/coding-agent/src/core/package-manager.ts`、`resource-loader.ts`、`skills.ts`、`prompt-templates.ts`、`source-info.ts`、`extensions/loader.ts`、`settings-manager.ts`（包相关段）

---

## 1. 架构总览

| 维度 | TS（pi） | Python（Nova） |
|---|---|---|
| 包管理器 | `DefaultPackageManager` 单类（2650 行单文件），安装+解析一体 | `PackageManager` facade → `install/`（PackageInstaller×2 scope）+ `resolve/`（PackageResolver）+ `source/`（spec/resolver）三层拆分 |
| 资源加载器 | `DefaultResourceLoader` 单类，内部直接调 skills/prompt-templates/extensions loader | `DefaultResourceLoader`（ABC + 默认实现），调度 `resources/loaders/` 下六个独立 loader |
| 资源类型 | extensions / skills / prompts / **themes**（4 类） | extensions / skills / prompts / **tools / agents**（5 类；themes 已移出，归 Node 层 UI 资产） |
| 安装后端 | npm/bun/pnpm（npm 源）、git（git 源）、本地路径（原地引用） | uv 优先/pip 兜底（Python 依赖）、git（git 源）、本地路径（复制或 editable symlink） |
| 安装事实记录 | 无专门记录，从磁盘（node_modules/package.json）推导 | sibling `*.dist-info/`（PEP 610 风格 `direct_url.json` + `package_name` + `installed_at`），缺失时磁盘推导兜底 |
| settings 形态 | `packages: (string \| {source, autoload, extensions, skills, prompts, themes})[]` | `packages: (str \| {source, editable, autoload, extensions, skills, prompts, tools, agents})[]` |

一句话：**管道形状完全同构**（settings → 包解析 → 优先级合并 → 按类型加载 → 诊断），差异集中在生态必然层（npm vs pip、TS 文件即扩展 vs Python 包结构）与 Nova 特有的两类资源（tools/agents）。

---

## 2. 包源（Source）层对照

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 源类型 | `npm:` / git（URL、SCP、host 简写）/ local path | `path:`（默认）/ git（URL、SCP、host 简写） | ✅ 对齐（npm 源为生态差异，见 §6.1） |
| git ref 语法 | `@ref`，取 path 部分第一个 `@` | 同（`_split_git_path_and_ref`） | ✅ 对齐 |
| package identity | `npm:<name>` / `git:<host>/<path>` / `local:<abs>` | `git:<host>/<path>` / `local:<abs>` | ✅ 对齐（无 npm 分支） |
| identity 忽略 ref/写法差异 | ✅（SSH/HTTPS 归一） | ✅（host/repo_path 归一） | ✅ |
| settings 持久化时相对化 | `normalizePackageSourceForSettings`（相对 base_dir） | `normalize_package_source_for_settings`（同） | ✅ |
| editable | ❌ 无此概念（local 源原地引用即"天然 editable"） | ✅ path 源专有：symlink + `pip -e`，dict spec `editable: true` 或 CLI `--editable` | 🐍 Python 特有（生态差异，见 §6.3） |
| temporary scope | ✅ 完整物化链（`tmp/extensions`、hash 目录、git 临时刷新、npm 临时安装） | ❌ 已删除物化链，仅保留 `SourceScope.TEMPORARY` 作为 additional 路径的来源标签 | 📌 有意拍板 |
| 路径逃逸防护 | `resolveManagedPath`（拒绝逃出安装根） | `resolve_managed_path`（同，且不跟随 symlink 以兼容 editable） | ✅ 对齐 |

---

## 3. 安装 / 卸载 / 更新 / 列出对照

### 3.1 安装

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 物化策略 | npm → `<scope>/npm/node_modules`；git → `<scope>/git/<host>/<path>`；local → **不物化**（原地引用） | path → `<scope>/packages/path/<name>/`（复制；editable 为 symlink）；git → `<scope>/packages/git/<host>/<path>/` | 🐍 形态差异（Python 必须复制/链接：依赖安装需要稳定快照，原源删除后仍可用） |
| 依赖安装 | git 包含 package.json 时 `npm install --omit=dev` | 解析 `pyproject.toml`（Poetry/PEP 621）+ `requirements.txt` + `setup.cfg` 回退 → uv/pip 装入当前 Python 环境 | 🐍 生态对应物 |
| 依赖冲突预检 | ❌（npm 自己报错） | ✅ 安装前 dry-run 预检 | 🐍 增强 |
| 包自安装 | ❌（npm 包天然可被 require） | ✅ 含 `name + build-system` 时 `--no-deps` 装入 Python 环境（executor 共享 helper） | 🐍 生态必然 |
| git clone 原子性 | 直接 clone 到目标，失败留残骸 | 临时目录 clone + move，失败不留残骸 | 🐍 增强 |
| 同名覆盖 | npm 源 npm 自己处理；git 按 host/path 自然去重 | path 源同 scope 同名后装覆盖先装（对齐 pip），并清理旧 dist-info/孤儿 Python 包 | ✅ 语义对齐 |
| 递归自复制防护 | 无（local 不复制） | 源目录包含安装目标时直接报错 | 🐍 复制模式的必要防护 |
| 安装遥测 | ❌（enableInstallTelemetry 用于版本更新 ping，非包安装） | ✅ `report_install_telemetry(event="package_install")` | 🐍 差异（小） |
| 安装目录 .gitignore | ✅ `ensureGitIgnore`（`*` + `!.gitignore`） | ✅ 同（`packages/.gitignore`） | ✅ 对齐 |
| 云同步排除标记 | ✅ `markPathIgnoredByCloudSync` | ❌ | 🔻 小差距（见 §7） |

### 3.2 卸载

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 消歧 | 按 source 解析 | 包名优先，名字查不到才按 source（`looks_like_source`） | 🐍 增强（裸名与 cwd 目录同名时不被劫持） |
| 跨 scope | `remove(source, {local})` 单 scope | 默认双 scope 搜索卸载；共享 Python 包按引用计数最后卸载 | 🐍 共享 Python 环境的必然产物 |
| git 缓存清理 | `rmSync` + `pruneEmptyGitParents` | `safe_remove` + `_prune_empty_parents` | ✅ 对齐 |
| editable 卸载 | 不适用 | 只删 symlink，保留原源 | 🐍 特有 |

### 3.3 更新

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 更新范围 | settings 配置的包；pinned npm 跳过；git 全量（reconcile ref） | 单包：按名/按源重装（继承旧 spec 的 filters/editable）；`update_all`：仅 git 源（path 无远端概念） | ✅ 对齐 |
| npm 版本检查短路 | ✅ `shouldUpdateNpmSource`（npm view） | 不适用 | — |
| git up-to-date 短路 | ✅（HEAD == target 跳过 reset） | ✅（`_git_head_matches`，同） | ✅ |
| 在线 fetch 失败 | 硬错误 | 硬错误（离线才回退本地 ref） | ✅ |
| 并发 | npm 批量/scope + git 并发 4 | **串行**（共享 Python 环境，pip/uv 并发写同一环境不安全） | 📌 有意差异（文档化） |
| 部分失败 | 抛错 | `PackageUpdateError` 携带成功/失败明细 | 🐍 增强 |

### 3.4 列出 / 检查 / 校验

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 配置列表 | `listConfiguredPackages`（settings + installedPath） | `list_configured_packages`（同）+ `list()`（settings 权威 + 磁盘兜底，按 install_path 对齐去重）+ `list_with_resources()` + `info()` | 🐍 更强（dist-info 支撑） |
| 可用更新检查 | npm view + git ls-remote，并发 4，离线跳过，pinned 跳过 | git ls-remote，并发 4，离线跳过，pinned SHA 跳过（无 npm 对应物） | ✅（生态裁剪后对齐） |
| validate | ❌ | ✅ `nova-pkg validate`（git 源允许 clone 到缓存） | 🐍 独有 |
| scaffold | ❌ | ✅ `nova-pkg init` 生成 `[tool.nova]` 段 | 🐍 独有 |

---

## 4. 资源解析（resolve）层对照

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 优先级 rank | project-settings(0) < project-auto(1) < user-settings(2) < user-auto(3) < package(4) | 完全一致（`resource_precedence_rank`） | ✅ |
| 同路径合并 | 累积时 first-wins；输出时排序后按 canonical path 去重 | `_add_resource`：高 rank 覆盖低 rank，同 rank first-wins（key=resolved path） | ✅ 结果等价 |
| 跨 scope 去重 | project 赢；project `autoload=false` 条目作为 user 条目的 delta 保留两个 | `PackageSourceCollection.resolve`（同，含 delta 保留） | ✅ |
| 包过滤器 | `{extensions, skills, prompts, themes}` 数组：plain 包含 / `!` 排除 / `+` 强制包含 / `-` 强制排除；空数组=禁用该类型 | 同，资源类型为 `{extensions, skills, prompts, tools, agents}` | ✅（类型集不同属设计差异） |
| autoload=false delta | `findAutoloadDeltaBase` + `applyAutoloadDisabledPatterns`（只写被提及资源的 enabled 状态） | `apply_autoload_disabled_patterns`（同） | ✅ |
| 缺失包处理 | `onMissing` 回调（install/skip/error），缺省自动安装 | `on_missing` 回调 + `install_missing_packages` 开关；离线跳过 | ✅ |
| 自愈重装 | ❌（`existsSync` 即视为已安装） | ✅ 副本 + dist-info 双条件，幽灵/旧装触发一次自愈重装 | 🐍 增强 |
| manifest | `package.json` 的 `pi` 段（extensions/skills/prompts/themes，路径或 glob，支持 override patterns） | `pyproject.toml` 的 `[tool.nova]` 段（agents/tools/skills/extensions/prompts + `auto_install_dependencies`） | ✅ 机制对齐 |
| manifest 条目逃逸 | glob 限定包根内 | 绝对 glob 与 `..` 逃逸**直接报错** | 🐍 更严 |
| 约定目录 | 包根下 `extensions/ skills/ prompts/ themes/` | 包根下 `agents/ tools/ skills/ extensions/ prompts/` | ✅（类型集差异） |
| 顶层自动发现 | `.pi/{extensions,skills,prompts,themes}` + `~/.pi/agent/...` | `<cwd>/.nova/{agents,extensions,skills,prompts}` + `~/.nova/agent/...`；**tools 不自动发现**（只来自包） | ✅（tools/agents 属设计差异） |
| `.agents/skills` | project：祖先到 git root（排除 `~/.agents`）；user：`~/.agents/skills`；agents 模式（只收 SKILL.md）；逐组 base_dir | 完全一致 | ✅ |
| prompts 顶层平铺 / 包内递归 | 顶层 auto 只收当前层级 .md；包内/settings 目录条目递归 | 同（`collect_auto_prompt_entries` vs `collect_prompt_entries`） | ✅ |
| settings 直接条目 | plain 路径收集 + patterns 过滤；**不做 glob 展开**（含 `*`/`?` 的条目只当过滤 pattern） | plain 路径 + **glob 展开**（相对 base.glob、绝对 glob 模块展开）+ patterns 过滤 | 🐍 增强 |
| 直接条目指向目录 | 按资源类型递归收集 | 同（`_collect_files_from_directory` → `RESOURCE_DISCOVERY`） | ✅（本轮修复了未导入 bug，见 §8） |
| 仅 override patterns 的直接条目 | plain 为空 → 由 auto-discovery 阶段生效 | 同（注释明示） | ✅ |
| ignore 规则 | `.gitignore/.ignore/.fdignore`，逐目录前缀化，递归 | 同（pathspec，支持嵌套反选） | ✅ |
| 解析诊断 | 主要在 loader 层产出；包解析失败走 onMissing/异常 | resolver 即产出 `ResolvedPaths.diagnostics`（非法 spec、源不可解析），并透传到 `ResourceLoader.get_diagnostics()` | 🐍 增强 |
| trust 门控 | 解析前 assert project trusted（读 project 存储/设置） | 读取侧门控（不信任则不解析 project scope）；**写操作不检查**（装/卸是主动行为） | 📌 有意差异（文档化） |

---

## 5. 资源加载（load）层对照

### 5.1 共用机制

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 单通道 additional 路径 | `additionalExtensionPaths` 等 → CLI/temporary 层，skills/prompts/themes 排在 resolver 之后 | `additional_*_paths`（CLI `--skill`/`--prompt-template` 与 SDK 注入共用），同层位 | ✅（已拍板单通道） |
| 扩展贡献资源 | `extendResources`（skill/prompt/theme 路径 + metadata），reload 清空后由扩展重新贡献 | `extend_resources`（skill/prompt 路径；无 themes），同生命周期 | ✅（无 themes 属拍板） |
| SourceInfo 合成 | `getDefaultSourceInfoForPath`（标准资源根下→user/project；否则 temporary） | `default_source_info_for_path`（同） | ✅ |
| SourceInfo 前缀匹配 | `findSourceInfoForPath`（精确→前缀） | `find_source_info_for_path`（同） | ✅ |
| pre-trust 两阶段加载 | `loadProjectTrustExtensions` → 信任裁决 → `reload` 复用 preloaded（按 resolved path + inline 实例整体复用） | 同（`load_project_trust_extensions` + `reload(pre_trust_extensions=...)`） | ✅ |
| override 回调 | extensions/skills/prompts/themes/agentsFiles/systemPrompt 等 8 个 | extensions/skills/prompts/agents/**context_files** 5 个 | ✅ 已闭环（其余 3 个不做，理由见 §7.2） |
| no* 开关 | noExtensions/noSkills/noPromptTemplates/noThemes/noContextFiles | no_extensions/no_skills/no_prompt_templates/no_tools/no_context_files | ✅（无 themes，多 no_tools） |

### 5.2 skills

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 发现 | SKILL.md → skill 根停递归；pi 模式根层散装 .md 也算；agents 模式只收 SKILL.md | 同（`collect_skill_entries` + `load_skills_from_dir`） | ✅ |
| 解析产物路径 | ResolvedResource.path 指向 **SKILL.md 文件**，需 `mapSkillPath` 特判目录→文件 | path 指向 **skill 目录**，loader 直接接受 | 🐍 更简洁（无行为差异） |
| 校验 | name（小写/连字符/≤64）warning 照常加载；description 缺失拒载、超长 warning | 同（`validate_name`/`validate_description`，文案一致） | ✅ |
| `disable-model-invocation` | ✅ | ✅ | ✅ |
| 同名碰撞 | first-wins + collision 诊断 | 同 | ✅ |
| 系统提示词格式化 | `formatSkillsForPrompt`（XML） | `format_skills_for_prompt`（同） | ✅ |
| `/skill:name` 命令展开 | ✅（slash-commands 层） | ✅（`harness/skills.py`：expand/parse block/SkillManager） | ✅ |

### 5.3 prompts

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 模板解析 | frontmatter description/argument-hint；缺省取首行（60 字符截断） | 同 | ✅ |
| 参数替换 | `$1..$N`、`$@`/`$ARGUMENTS`、`${N:-default}`、`${@:N}`、`${@:N:L}` | 同（正则逐项对应） | ✅ |
| 同名碰撞 | `dedupePrompts` first-wins + 诊断 | `_dedupe_prompts` 同 | ✅ |
| 描述来源标签 | 无（description 原文） | description 尾部追加来源标签 `(user)`/`(project)`/`(path:x)` | 🐍 差异（UI 展示增强） |

### 5.4 extensions

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 发现 | 根级 `.ts/.js`；子目录 `index.ts/index.js` 或 package.json `pi.extensions`；不递归 | 根级 `.py`；子目录含 `extension.py`/`__init__.py` 或自身 `[tool.nova.extensions]`；不递归 | ✅ |
| 加载 | jiti 动态 import + 工厂函数 | `importlib` 动态加载 + 工厂函数 | ✅ 生态对应 |
| 模块缓存 | ✅ `extensionCache`（cwd + generation），reload 清除 | ❌（每次重新 import；Python import 机制不同） | 🔻 差异（见 §7） |
| 冲突检测 | tools + flags（扩展可注册 tool） | commands + flags（**扩展不能注册 tool**——tools 走包） | 📌 有意设计差异 |
| inline 工厂 | `extensionFactories` → `<inline:N>` 伪路径，preloaded 复用 | 同 | ✅ |
| 扩展注册 provider | ✅（pendingProviderRegistrations → bindCore 冲刷） | ✅（model_runtime 链路） | ✅ |

### 5.5 tools / agents（Nova 独有）

| 项 | TS | Python |
|---|---|---|
| tools 资源 | ❌ 工具内置（`core/tools/*` 硬编码），扩展可注册 tool | ✅ 完整链路：包内 `tools/<name>/`（`schema.json` + `executor.py`）→ resolver → `ToolLoader` → `DynamicTool`；同名碰撞诊断；`no_tools` 开关；settings 无 tools 直接条目、顶层不自动发现（只来自包） |
| agents 资源 | ❌ 无 agent 概念 | ✅ 完整链路：`agents/<name>/`（`agent.yaml`/`description.md`/`sections/`）→ resolver → `load_agent_config_from_dir` → AgentConfig（工具白名单/sections/子代理）；同名 first-wins + 诊断；settings `agents` 直接条目 + 顶层自动发现 |

### 5.6 context files

| 项 | TS | Python | 状态 |
|---|---|---|---|
| 文件名 | `AGENTS.md/AGENTS.MD/CLAUDE.md/CLAUDE.MD` | 同 | ✅ |
| 遍历 | 全局 agentDir + cwd 向上到**文件系统根**（不停 git root）；由远及近 | 同 | ✅ |
| 覆盖回调 | `agentsFilesOverride` | ❌（只有 `no_context_files` 开关） | 🔻 小差距 |

### 5.7 系统提示词文件

| 项 | TS | Python | 状态 |
|---|---|---|---|
| `SYSTEM.md` / `APPEND_SYSTEM.md` | ✅ discover（project 需信任，全局兜底）+ overrides | ❌ Nova 的系统提示词由 agent config（description/sections）体系构建，不走资源加载器 | 📌 设计差异（Nova 以 agent 为中心） |

---

## 6. 生态必然差异（不可也不应对齐）

1. **npm 源**：pi 有完整的 `npm:` 源链路（managed install root、版本 range 检查、`npm view` 可用更新、pnpm/bun 变体、peer-deps 规避、legacy global root 回退）。Python 生态没有对应的公开注册表语义，Nova 包源只有 path/git——Python 依赖本身走 PyPI（uv/pip），那是**包内依赖**层面的事，不是 Nova 包源。
2. **依赖安装目标**：pi 把 git 包的 node_modules 装在包目录内；Nova 把 Python 依赖装进 **Nova 自身运行的同一个 Python 环境**（user/project scope 只分组资源目录，不创建隔离环境）——因此才有引用计数卸载、串行更新、冲突预检这些 Nova 特有机制。
3. **editable**：pi 的 local 源原地引用、不复制；Nova 普通模式复制（稳定快照 + 原源删除后可用），开发模式用 symlink + `pip -e` 双通道。两种"开发模式"形态不同但目的一致。
4. **dist-info**：Python 侧对齐 pip 生态的安装事实快照（`direct_url.json` PEP 610）；pi 无记录、纯磁盘推导。
5. **themes**：pi 在包管理/资源层有 themes 全链路；Nova 已将其移出 Python 资源系统（归 Node 层 UI 资产），settings 亦无 `themes` 字段。
6. **扩展能力面**：pi 扩展可注册 tool/message renderer/entry renderer/shortcut（TS 与 UI 同进程）；Nova 扩展不能注册 tool（tools 走包），UI 渲染归 RPC 层——前后端分离的必然裁剪。
7. **getEnv() 的 Linux `/proc/self/environ` 兜底**：pi 为打包成 Bun 二进制后环境变量缺失准备的；Python 进程无此场景。

## 7. 剩余差距清单（已逐项闭环，附最终结论）

| # | 差距 | 结论 | 理由 |
|---|---|---|---|
| 1 | 扩展模块缓存（pi 有 cwd+generation 工厂缓存） | **不做** | ① pi 在 `reload()` 第一件事就是 `clearExtensionCache()`——热重载场景两边同样重新 import，无差异；② 两阶段信任加载 Nova 已用**实例复用**解决（preloaded 按 path 复用，工厂不重跑），比 pi 的工厂缓存更彻底；③ 剩余差异仅在单进程多会话：pi 共享工厂（模块状态共享），Nova 重 import（模块状态隔离）——隔离是特性不是缺陷。多会话成为高频用法且 import 开销成为实测瓶颈时再议 |
| 2 | override 回调少 3 个 | **已做 1 个，其余 2 个不做** | `context_files_override`（对齐 pi `agentsFilesOverride`）已实现：在 `no_context_files` 之后应用，可过滤/改写/注入。`themesOverride` 不做（themes 已移出 Python 资源系统）；`systemPromptOverride`/`appendSystemPromptOverride` 不做——Nova 系统提示词由 agent config（description/sections）构建，不走 resource loader，等价口子 `agents_override` 已存在 |
| 3 | 云同步排除标记 | **不做** | `~/.nova` 在 home 根目录，不在 iCloud/Dropbox 典型同步路径（Desktop/Documents）内；项目级 `.nova/packages` 落在被同步目录时后果仅是"多同步些文件"，无正确性问题；pi 的实现是平台特定 hack，投入产出比低，将来有真实反馈再照抄 |
| 4 | git 包更新后的依赖漂移 | **无需处理** | pi 在 `ensureGitRef` 后重跑 npm install；Nova 的 git 更新（`update()`）走完整重装（含依赖），install 路径内 `_git_update` 之后同样走完整 install——无缺口 |
| 5 | npm 式版本检查 | **无需处理** | Nova 包源无 registry 版本概念（git/path 源），`update_all` 只 reconcile git ref——生态差异 |

## 8. 本轮审计发现并已修复的问题

- **`resolver.py` 引用未导入的 `RESOURCE_DISCOVERY`（潜在 NameError）**：settings 直接资源条目（`extensions`/`skills`/`prompts`/`agents`）指向**目录**时，`_collect_files_from_directory` 触发 `NameError: RESOURCE_DISCOVERY`，整个 resolve 崩溃。测试未覆盖该路径（直接条目只测过文件与 glob）。
  - 修复：`resolve/resolver.py` 导入表补 `RESOURCE_DISCOVERY`。
  - 回归测试：`tests/core/package/resolver/test_resolver.py::test_direct_entries_directory_expands_by_resource_type`（user scope 扩展容器目录 + project scope skill 容器目录）。
  - 验证：`nova_harness` 全量 **1007 passed**（原 1006 + 1 新测试），5 deselected。

## 9. 结论

- **机制层对齐度高**：优先级、去重、filters、autoload delta、`.agents/skills`、ignore、override patterns、pre-trust 两阶段、additional 单通道、collision 诊断、SourceInfo 合成——逐项核对均一致或为等价实现。
- **差异三类可归**：生态必然（§6）、有意拍板（temporary 物化、CLI `-e`、trust 写侧不查、扩展不注册 tool、themes 归 Node）、Python 增强（dist-info、自愈重装、诊断前置、glob 展开、原子 clone、防自复制）。
- **无遗留对齐性 bug**；§7 的 5 项差距已逐项闭环——1 项实现（`context_files_override`），4 项明确"不做/无需处理"并附理由。
