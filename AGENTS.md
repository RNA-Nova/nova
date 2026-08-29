<!-- AGENTS.md - Nova Monorepo 项目指南 -->

# Nova —— LLM Agent 构建框架（Monorepo）

> 本文件面向 AI Coding Agent 编写。如果你不了解本项目，请从这里开始阅读。

## 项目概览

Nova 是一个用于构建大语言模型（LLM）智能体的 **Python 单体仓库（monorepo）**。项目采用分层架构，将 LLM 提供商抽象、Agent 核心框架、高阶 SDK、专用 Agent 定义与 TUI 前端拆分为独立的子包，便于按需组合与独立迭代。

- **目标语言**：Python `>=3.12,<3.14`
- **项目语言**：代码注释与文档主要使用**中文**
- **当前阶段**：Alpha（版本 `0.1.0`，其中 `nova-coding-agent` bundle 为 `1.0.0`）
- **License**：MIT
- **作者**：Liujinming

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python `>=3.12,<3.14`；`nova-client` 前端额外需要 Node.js `>=22.19.0` |
| 包管理器 | **Poetry**（各子包独立管理）；`nova-client` 同时使用 **npm** |
| 格式化 | `black`（目标语法版本 `py312`） |
| Import 排序 | `isort`（`profile = "black"`） |
| 序列化 | `pydantic` v2（`BaseModel`） |
| 异步运行时 | `asyncio` |
| 开发依赖 | `pre-commit`、`pytest`、`pytest-asyncio`、`sniffio` |
| 其他关键依赖 | `openai`、`json-repair`、`jsonschema`、`pyyaml`、`filelock`、`tomli` |

**未使用** Mypy、Tox、Makefile、Docker 或 CI/CD（GitHub Actions / GitLab CI）。仓库中也没有 `poetry.lock`、`.pre-commit-config.yaml` 或 GitHub Actions 工作流。

---

## Monorepo 结构与包依赖关系

```
nova/
├── packages/
│   ├── nova_ai/            # 统一的 LLM 提供商抽象层
│   ├── nova_agent/         # 事件驱动的异步 Agent 框架（源码包名为 nova_agent）
│   ├── nova-harness/       # 运行时伞目录（前后端两半区同居）
│   │   ├── backend/        # 高阶 Agent SDK（会话、压缩、工具链、RPC 服务器、Project Trust、UI 桥接；py dist `nova-harness`，import `nova_harness`）
│   │   └── frontend/       # 前端运行时（TS 厚应用层 + 内置 TUI 宿主 modes/tui；npm 包名 `nova-client`）
│   ├── nova_coding_agent/  # 官方编程 Agent bundle 与本地文件系统工具
│   ├── nova_executor/      # 通用执行后端（Rust：进程/文件/PTY/三平台沙箱，JSON-RPC over WS）
│   ├── nova-executor-client/   # executor 的 Python SDK（只做连接的薄客户端）
│   ├── nova_team/          # 主从多智能体团队配置（早期 WIP，暂无 pyproject.toml）
│   └── nova_web_ui/        # Web UI 占位目录（当前为空）
├── README.md
├── CHANGELOG.md
├── .gitignore
└── AGENTS.md               # 本文件
```

### 运行时依赖层次（自下而上）

1. **`nova_ai`** —— 最底层。提供多厂商（OpenAI、Anthropic、Google、Volcengine、GitHub Copilot 等）统一的流式调用、模型注册表、鉴权、消息类型与兼容性层。当前仅有 `api_impls/openai_completions.py` 一个完整实现。
2. **`nova_agent`（源码包 `nova_agent`）** —— 核心框架。基于 `nova_ai` 的模型能力，提供 `Agent` 类、事件订阅/发布、`agent_loop` 异步循环、生命周期管理、工具校验与执行。
3. **`nova_harness`** —— 高阶 SDK。基于 `nova_ai` + `nova_agent`，封装 `AgentSession`、会话树（分支/fork/导航）、上下文压缩（Compaction）、资源加载、设置持久化、模型注册表覆盖、内置工具链、JSON-RPC 服务器、包管理器 CLI、Project Trust 门控与 `ExtensionUIContext` / RPC UI 桥接。manager 层（读自由写独占、互不调用、编排在 AgentSession）：`AgentManager`（agents 注册表视图 + 当前角色旋钮 + 默认解析链 + CapabilitySelection 汇集 + yaml 写回——`/agent save` 落地，包来源影子写 user 级）、`ToolsManager`（工具裁决单点）、`PersonaManager`（persona 装配 + override）、`SystemPromptManager`（纯渲染，激活工具含 `subagent` 时注入 `# Available Agents` 委派菜单）、`SettingsManager`（settings 唯一写门）。
4. **`nova_coding_agent`** —— 官方 bundle。同时是一个可 import 的 Python 包，依赖 `nova-ai`、`nova-agent`、`nova-harness`（均声明为 Poetry path 依赖），提供 `coding_agent` 等 5 个 Agent 组合声明（含 scout/planner/reviewer/worker 子代理）、`session_commands`/`permission_gate`（tool_call 拦截：bash 危险命令询问、写保护路径拦截）/`plan_mode`（只读规划模式：/plan 切换 + 写工具禁用 + bash 白名单 + 计划跟踪）/`tools_panel`（/tools 工具开关面板 + append_entry 持久化）/`interactive_shell`（user_bash 拦截：vim/htop 类交互命令终端让位）/`confirm_destructive`（session_before_switch/fork 确认）/`subagent_gate`（subagent 委派自治权检查点：per-agent 允许一次/本会话始终允许/取消，headless 放行，`subagent_allow` 条目持久化分支安全）/`executor_switch`（/executor 执行后端切换——local/executor 沙箱/远程端点/SSH 主机，executor_backend 条目持久化）八个扩展、10 个本地工具（bash、edit、find、grep、ls、question、read、subagent、todo、write）以及 `bash` 用户工具（`user_tools/`，LLM 工具与会话 bash 共享同一引擎）。**bash 与六个 fs 工具（read/write/edit/ls/find/grep）均可随后端切换**（执行期读 `BackendSelection` 模式格；question/todo/subagent 不触碰执行环境不在切换面）——进程走 ExecutorBashOperations，文件系统走 FileSystemLayer 双实现（本地/远程同一实现类），详见 `nova-harness/backend/examples/executor-integration.md`。
5. **`nova-client`** —— 前端运行时（架构 2.0 第二层，骨架已按 v3.1 设计一次成型）。TypeScript 厚应用层，子系统：`wire/`（client 传输 + capabilities 契约 major/minor 握手与能力位 + bridge 反向原语路由）、`bus.ts`（观察式事件脊柱，mirror 特权订阅）、`mirror/`（会话镜像：mapping 纯函数归约 + store 状态容器 + types 呈现词汇）、`presentation/`（blocks 声明式块词汇（开放集 + validateBlock schema 校验）+ slots 注册表（tool/entry/region/block/editor/command/shortcut/autocomplete/dialog 九族键）+ extension-api 扩展 UI API（ctx 纪律：只收"后端够不着的宿主原语"——对话框五件/编辑器/剪贴板/setStatus/onTerminalInput/主题/`events.on` 事件观察口/`runInteractive` 终端让位/`setTitle`/`notifyDesktop`/`setFooter`+`setHeader` 整件替换/`setWorking*` loader 三旋钮；后端方法的访问面就是 invoke 全量生成方法表，不手写包装域）+ theme-json 主题契约）、`packages/`（pkgList 索引 + npm 自愈 + 更新提醒）、`resources/`（呈现资源层：discovery 统一发现（frontend 半区：<host>/index.ts、tools|user_tools 渲染器、themes——镜像约定；外加散养根 `~/.nova/agent/frontend/tui/` 与 `<cwd>/.nova/frontend/tui/`——tools/dialogs/index.ts 三类资产，user 恒可信、project 过 trust 门）+ trust 编排层过滤 + loader jiti 管线——mtime 缓存/preview 钩子/耗时观测/diagnostics 含覆盖碰撞）、`settings/`（UISettings 扩展设置 + UIStateStore 扩展 KV——Node 层存储，不进后端 settings）、`keymap/`（键位能力子系统——keybindings.json 加载/三级合并机械 + 保留键位对账（宿主默认表经 create 注入），pi core/keybindings.ts 对位：能力归运行时层，宿主只消费；TUI 默认键位表在 `modes/tui/keymap/tables.ts`——机械上移、方言留下）、`export/`（会话导出 HTML：pi 三件套模板直搬 + 数据零映射（线上 camel 天然同构）+ vendored marked/highlight.js，宿主无关、主题注入）。`RuntimeHost` 接口与进程内实现同居 `runtime.ts`（M3 WS 宿主落地时再立 `hosts/` 目录）。**TUI 是包内的一种宿主形态**：`src/modes/tui/`（原 `nova-tui` 包合并而来——app.ts 纯装配根 + controllers/ 编排层（editor/keymap/dialogs/transcript/status/theme/settings（18 项面板）/export/share/foreground（前台任务取消登记处——Esc 域级路由一环）/terminal（OSC 0 标题 + OSC 9;4 进度 + turn 结束桌面通知 OSC 9/777/99）/startup（启动编排）+ 前端自持导航选择器（sessions/fork/models/scoped-models——per-item 动作键与面板交互反向原语表达不了的选择器直调 RPC 自渲染，已整体迁入官方 bundle frontend/tui/ 段 slash/ 目录——扩展机制 dogfood，官方与第三方同权；宿主只留 runCommand 推命令通道，后端同名命令保留 headless 回退）+ components/{transcript,dialogs,pickers,status,layout}（含 form 表单对话框、searchable 通用选择器、RegionHost/OverlayHost 区域与浮层宿主）+ blocks/ 块适配层（官方五块经 ExtensionUIAPI builtin 注册 + schema 校验）+ builtin/ 内建扩展（/packages 包面板——dogfood 验收）+ themes/ 主题系统（dark/light 内建 + 用户目录 + 包内 frontend/themes 三源 + /theme 预览）+ utils/（clipboard/terminal-guard），基于 `@earendil-works/pi-tui` 渲染（overlay 经其 showOverlay），`bin.nova` 入口），与将来的 Web UI 共享运行时主体。线上契约类型经 `nova_harness.rpc.protocol.schema_export` 构建期导出（`protocol/nova-wire.schema.json` + `src/protocol/nova-wire.gen.ts`，pytest 漂移测试保鲜），mapping/store/wire 全部基于生成类型。已真实跑通：对官方 bundle 与 B 型包完成 frontend/ 渲染器发现→加载→渲染（含组件形态）。WebSocket 宿主（M3）、全量 UI 扩展宿主（M4）见 `docs/design.md` §16。纯 npm 包（无 Python 源码；运行时仍需 Python 环境中可导入 `nova_harness`）。
6. **`nova_team`** —— 团队编排（WIP）。提供 `TeamDefinitor`，支持主从多智能体挂载配置与两级存储（项目级 / 全局）。**尚未配置 `pyproject.toml`**，不可独立安装。
7. **`nova_web_ui`** —— 当前为空目录，仅为未来 Web UI 占位。

> **依赖声明现状**：
> - 各 Python 子包的 `[tool.poetry.dependencies]` **只声明各自的第三方依赖**，不再声明兄弟包的 path 依赖（pip 对"同一包同时被 editable 与非 editable path 依赖引用"会报 ResolutionImpossible；互依关系到发布时再恢复）。
> - 统一安装的单一事实源是根 `pyproject.toml` 的 `[tool.pixi.pypi-dependencies]`（四个 editable path 包 + 第三方依赖并集，`pixi install -e dev` 一把求解）；服务器部署脚本同理按此清单安装。
> - `nova-client` 为纯 npm 包；运行 TUI 时需确保 `nova_harness`（连带 `nova_ai`、`nova_agent`）已在同一 Python 环境中可导入（`NOVA_PYTHON` 可指定后端解释器，dev 用 pixi 环境 python）。

---

## 各子包详细结构

### `nova_ai`（源码包 `nova_ai`）

位于 `packages/nova_ai/src/nova_ai/`：

- `types/` —— 全部共享类型（契约层，无运行时行为）：枚举（`enums.py`）、内容（`content.py`）、消息（`messages.py`）、模型与用量（`model.py`）、兼容性配置（`compat.py`）、流选项（`stream_options.py`，dataclass）、流式事件（`events.py`）、Auth 类型（`auth.py`）、共享别名（`aliases.py`）、`NovaBaseModel` 基类（`base_model.py`）
- `gateway/` —— `Models` 集合（`models.py`）、`Provider` 运行时单元与 `create_provider`（`provider.py`）、`ModelsStore`（`store.py`）；鉴权解析在请求时完成（runtime override → stored credential → 环境变量链 → OAuth 刷新）
- `providers/` —— 内置厂商定义（`volcengine`、`moonshotai`、`moonshotai_cn`、`kimi_coding`），各含静态模型数据与 provider 工厂；`all.py` 提供 `builtin_models()`
- `auth/` —— `AuthContext`、credential store 协议、`resolve_provider_auth`、`env_api_key_auth` 等辅助，以及 `oauth/`（`codex`、`kimi` OAuth 流程与登录页）
- `api_impls/` —— API 协议实现：`openai_completions.py`（当前唯一完整实现）
- `streaming.py` —— `AssistantMessageEventStream` 与流式调用入口
- `utils/` —— 环境变量、JSON 解析、消息转换、流选项、Unicode 代理项清理、上下文溢出检测、模型工具函数等

包内包含详细的 `docs/` 目录，记录架构设计、开发日志、架构决策记录（ADR）、使用与维护指南、代码约定和 API 参考。

### `nova_agent`（源码包 `nova_agent`）

位于 `packages/nova_agent/src/nova_agent/`：

- `agent.py` —— `Agent` 类，封装状态管理、事件订阅、消息队列与生命周期
- `agent_loop/` —— 核心异步循环包
  - `facade.py` —— 对外暴露的 `agent_loop()` / `agent_loop_continue()` / `run_agent_loop()` / `run_agent_loop_continue()`
  - `loop.py` —— 循环内部实现
  - `tools.py` —— 循环中的工具执行相关逻辑
- `types/` —— 完整事件类型体系、Agent 状态、上下文、工具、钩子上下文与结果等
- `signal.py` —— `AbortSignal` / `AbortController` 异步取消信号
- `utils.py` —— 工具调用校验与参数验证（基于 `jsonschema`）

### `nova_harness`（源码包 `nova_harness`）

位于 `packages/nova-harness/backend/src/nova_harness/`：

- `cli/` —— 所有 CLI 入口：`nova-harness`（`main.py`）、`nova-pkg`（`package.py`）
- `modes/` —— 运行模式
  - `print/` —— 非交互式命令行运行模式（`nova-harness run`）
  - `rpc/` —— JSON-RPC 服务器运行模式（装配入口 `cli.py`；服务器本体归 `server/`：`RpcServer` + 连接层 `Connection`/`ConnectionRegistry` + `RoutingUIContext` + `MethodRegistry` + 归约层 `reduction/`，传输经 `StdioTransport` 接入，`OutputGuard` 归 `core/utils/`）

  （**WS 接入已翻案归 Python** `rpc`——连接化重构后 stdio/WS 同为连接来源，鉴权三守则见 `transport/websocket.py`；设计修订记录见 `packages/nova-harness/backend/examples/nova_architecture_2.0.md` 文首）
- `core/agent_session/` —— `AgentSession` 运行时核心、`AgentSessionRuntime`、`AgentSessionServices`、领域控制器（user_tools、compaction、events、model、queue、retry、stats、tools、tree）
- `core/harness/` —— 高阶能力：会话持久化与树管理、上下文压缩、系统提示词构建、skills、Project Trust、用户工具（`user_tools/`：仅 UserToolManager 注册中心——框架不内置任何用户工具，具体工具由包经 `[tool.nova] user_tools` 类目分发；消息回载注册表在 `harness/session/message_types.py`；设计见 `examples/user_tools_design.md`）
- `core/resources/` —— 资源发现与加载（`loader.py` 与 `loaders/` 下的 agent_config、extensions、prompt_templates、skills、tools）
- `package/` —— Agent / tool / bundle / skill / extension 包管理器核心（manager facade + install/ 安装世界 + resolve/ 运行时世界 + source/ source 领域 + manifest / validation / scaffold / utils）。安装事实以 `*.dist-info/`（PEP 610 风格）为权威快照，副本推导兜底
- `core/config/` —— settings、auth storage、路径默认值、配置解析
- `core/model/` —— 模型域：注册表运行时（`ModelRuntime`、store/composer）、模型解析（`resolver.py`）、provider attribution
- `core/types/` —— 统一 Pydantic / dataclass 类型层
- `core/utils/` —— 通用工具（含遥测、HTTP 空闲超时、二进制解析）
- `core/extensions/` —— 扩展系统：API、loader、runner、wrapper、types

### `nova_coding_agent`（bundle + Python 包）

位于 `packages/nova_coding_agent/`，三段式结构（素材/组合分层）：**Python 半区在 `backend/`**（执行体 + 文本素材），**TS 半区在 `frontend/`**（自含 TS 子包），**组合层在 `agents/`**（角色选配 yaml，与两半区平级）：

- `agents/coding_agent.yaml` —— **Agent 组合声明**（纯选配零内容附着）：元数据（name 缺省=文件名）+ `persona`（人格条目列表——能相对 yaml 解析为文件/目录的按路径装配（文件逐列或目录递归字典序展开），否则按注册名查 persona 注册表；顺序即组装顺序，会话期由 PersonaManager 装配）+ 能力名单（`tools` 激活集、`extensions`/`user_tools` 白名单（空=全允许）、`commands` 命令允许集（空=全放）、`skills` 包内裁剪名单（空=全放、非空仅裁包内）——名单字段统一三态：键缺席=全放、显式空列表=全禁、支持 `!` 排除）。`model:` 字段 = 人格默认模型（初始模型解析链 tier 4：CLI/scoped 之后、settings 默认之前；无鉴权/未知 provider 静默落回）。同目录另有 **subagent 四件套**组合声明：`scout.yaml`（侦察）/ `planner.yaml`（只读规划）/ `reviewer.yaml`（评审）/ `worker.yaml`（全能力执行——显式不含 subagent 防递归），供 `subagent` 工具按名调用。**只有 agents，没有 subagents**——yaml 的 `subagents` 死字段已删除，可委派名单即会话注册表全量（无主从划分）
- `backend/personas/` —— 人格文本资源（persona 升格后为正式资源类目：`coding/core.md` 主人格 + `subagents/{scout,planner,reviewer,worker}.md` 子代理人格；命名 = 相对 personas 根去 .md，如 `coding/core`；经 `[tool.nova] personas` 类目分发，与 `prompts/` 用户模板分源——身份文本 vs 命令宏不同概念）
- `backend/tools/` —— 10 个本地工具，**单文件形态**（`bash.py` 即工具，元数据为 `Tool` 类属性）：
  - `bash.py` / `edit.py` / `find.py` / `grep.py` / `ls.py` / `question.py` / `read.py` / `subagent.py` / `todo.py` / `write.py`
  - `question.py`：交互式询问工具（`ToolExecContext.ui` 首个消费者——能力门控双路径：`dialog:question` 已注册走包侧单框（选项+内联自由输入，组件在 `frontend/tui/dialogs/question.ts`），否则基线两步降级（select_items→input）；支持单问 `question`+`options` 或多问 `questions`（1~4 问，pi questionnaire 对位——多问经 tab 条分页一次提交 `{answers}`，降级路径逐问串行）；`execution_mode="sequential"`）
  - `subagent.py`：三模式（single/parallel/chain）子代理委派——**消费会话 agents 注册表**（`ToolExecContext.agents` 快照按名查表，工具侧零发现管线；未知名报错列可用名含 source 标签）；on_update 聚合回调携带全量结果列表（parallel 含 `exit_code=-1` 运行中占位），执行引擎在 `backend/nova_coding_agent/subagent/`；执行前确认归 `subagent_gate` 扩展，激活时系统提示词注入 `# Available Agents` 菜单（AgentManager 供数）
  - `todo.py`：全量替换语义的清单工具（零服务端状态——状态单一事实源是会话里最新工具结果的 details，分支安全天然成立）
- `backend/prompts/` —— 用户模板：`debug.md` / `refactor.md` + subagent 工作流三件套（`implement.md` = scout→planner→worker、`scout-and-plan.md`、`implement-and-review.md`，`$@` 占位）
- `backend/user_tools/bash.py` —— `bash` 用户工具（单文件，暴露 `UserTool` 类）
- `backend/extensions/` —— 八个扩展：`session_commands.py`（21 个 slash 命令，含 `/help` 命令清单、`/todos` 清单查看、`/scoped-models` 池列出、`/persona` 人格切换与 `/agent` 角色切换/保存——选择器/直切 + `persona_override`/`agent` 条目持久化 + session_start/session_tree 分支恢复；`/agent save`/`save-as <name>` 把当前生效状态物化回组合声明 yaml——包来源影子写 user 级）/ `permission_gate.py`（tool_call 拦截：bash 危险命令询问、写保护路径拦截）/ `plan_mode.py`（Claude Code 风只读规划模式——`/plan` 切换 + ctrl+alt+p + `--plan` 旗标，edit/write 从激活集移除、bash 限只读白名单（tool_call 拦截），"Plan:" 编号计划提取与 [DONE:n] 进度跟踪，footer 状态条（`set_status` 命名通知：⏸ plan / 📋 n/m），状态经 append_entry 持久化）/ `tools_panel.py`（`/tools` 工具开关面板——`dialog:tools` 复选面板或文本回退，`set_active_tools` 绝对集应用 + `tool-panel` 条目持久化，session_start/session_tree 从分支最新条目恢复）/ `interactive_shell.py`（user_bash 拦截：vim/htop/less/ssh 等 14 程序集或 `i ` 前缀强制——`dialog:interactive-shell` 终端让位执行，无能力回 `(interactive commands require TUI)`）/ `confirm_destructive.py`（`session_before_switch`/`session_before_fork` 确认门——有 UI 且当前会话非空时 confirm，选否经类型化结果 `cancel=True` 取消切换）/ `subagent_gate.py`（subagent 委派自治权检查点——tool_call 拦截逐名裁决：允许一次/本会话始终允许（`subagent_allow` 条目持久化、分支恢复）/取消拦截；headless 直接放行）/ `executor_switch.py`（`/executor` 执行后端切换——选择器/直切 local/远程端点/SSH 主机（`remote user@host [远程目录]` 裸目标直输、选择器"＋ 添加远程主机"入口），三通道分离：`executor_backend` 会话条目管记忆（分支恢复，含 `remote_cwd`/`remote_shell`）+ bundle runtime 格管执行（bash 引擎执行期直读）+ notice 管用户回执 + `refresh_system_prompt` 触发环境段重建（`<cwd>` 渲染执行 cwd）；SSH 供给归 `nova_coding_agent/executor/provision.py`——密钥优先 + 首连终端让位输一次密码装管理密钥、二进制按平台缓存 scp 上传、`-tt`+`exec` 单 ssh 进程承载隧道（连接断即 SIGHUP 回收远程，零孤儿）、token 现生成不落盘、远程执行 cwd 缺省为会话隔离工作区（`<远程家目录>/.nova/agent/executor/workspaces/<session-id>`，显式目录 `test -d` 校验并随端点记忆）、供给成功自动登记 settings `executor.endpoints`（`register_executor_endpoint` 写门）、`/executor forget` 移除、隧道死亡懒重供给）
- `frontend/tui/dialogs/` —— **包侧自定义对话框**（`dialog:*` slot）：`question.ts`（question 工具单框——选项 + 内联自由输入；多问形态 `questions` 分派对位 pi questionnaire：tab 条分页 + 全答完提交 `{answers}`；注册即触发 system/capabilities 重宣告，后端 `has_capability("dialog:question")` 放行）/ `tools.ts`（工具开关面板——`[x]` 复选行 + `{active: [name...]}` 提交，pi tools.ts 的 SettingsList 对位）/ `interactive-shell.ts`（终端让位——setImmediate 异步挂起 TUI、spawnSync 交互命令、恢复后 `{exitCode}` 回执，pi interactive-shell.ts 对位）
- `frontend/tui/tools/` —— **TS 渲染器（组件形态）**：`bash.ts`（终端风）/ `edit.ts`（diff 风，消费引擎预生成的 patch）/ `read.ts`（文件风）/ `write.ts` / `find.ts` / `grep.ts` / `ls.ts` / `todo.ts`（清单卡片）/ `subagent.ts`（三模式：流式占位 ⏳、usage 行、工具调用格式化、展开态 Markdown 终输出）——**返回活 pi-tui 组件**（渲染器契约双形态：`NovaBlock[] | Component`，判别在消费点；组件经 `input.env` 取色/取主题；**输入即线上归约成品 `input.item`**（ToolCallItem——服务器归约，前端无中间卡片模型））；`tools/<tool>.ts` 文件名即工具名。**镜像约定**：前端段镜像后端资源类型目录（`tools/`、`user_tools/`、`extensions/`——位置即语义）；渲染器目录是纯发现域（一文件一工具、默认导出渲染函数，可选 `preview` 命名导出做执行前只读预览）——辅助模块归 `tui/lib/`（如 `edit-preview.ts` 匹配引擎），测试归 `frontend/tests/`（发现逻辑跳过 `*.test.ts`）
- `frontend/tui/index.ts` —— 扩展入口（ExtensionUIAPI 工厂：/tree、/todos、/model、/scoped-models、/resume、/fork 命令 UI 注册——其组件与编排在 `tui/extensions/session_commands/slash/{tree,todos,model,scoped-models,resume,fork}/`，镜像后端扩展归属；通用选择器件（searchable/selector/hints）经 `nova-client/modes/tui/*` 子路径共享宿主单例，不复制；后端同名命令保留 headless 回退）
- `frontend/package.json` —— npm 清单（`pretty-ms`/`diff` 依赖 + typescript devDep）：nova-pkg 安装第 4 阶段（npm ci/install）的触发点（A 型探测 `<包根>/frontend/package.json`；B 型包根即前端半区，探测包根 `package.json`）；`tsconfig.json` 供开发期类型检查
- `backend/nova_coding_agent/` —— bundle 自身的 Python 包（poetry `packages` 段 `from = "backend"`，import 路径 `nova_coding_agent.xxx` 不变），供 tools 共享辅助模块（`tools_common/`：路径/队列/截断/输出累加/shell 解析等工具基建 + **`fs_layer.py`**——`FileSystemLayer` 统一 fs 原语（全 async，read/write/edit/ls/find/grep 六个 operations 实现参数化在它上面，本地/远程同一实现类）+ `operations.py`（per-tool operations 协议与实现；`bash/`：bash 执行引擎与消息类型，LLM bash 工具与会话 bash 共享；`executor/`：执行后端接入——manager（客户端生命周期 + SSH 隧道 + atexit 清理）/ provision（SSH 供给 + rg 探测）/ backend（ExecutorBashOperations）/ **fs_layer（ExecutorFileSystemLayer）** / **process_runner（grep/find 的 spawn 缝——本地 asyncio 子进程 / 远程 process/start 无壳 argv 直启，远程 rg 路径随供给探测）** / runtime（BackendSelection 模式格 + `backend_file_layer` + `backend_process_runner` + `resolve_backend_path`——六工具执行期解析远程 fs 层与路径，相对→remote_cwd、~→remote_home））；`ui_primitives.py`：**UI 标准原语的官方定义点**（基线五件套词汇 + `set_status` 展示类词汇（footer 扩展状态行，pi `setStatus` 对位）+ `select`/`select_items`/`confirm`/`input`/`form`/`notify_message`/`set_status` 糖库——harness 的 `UIContext` 是零词汇泛型 transport，词汇定义权归包，设计见 `nova-client/docs/ui-primitives.md`）

> **B 型纯 TS 包**（package.json 身份证，无 pyproject.toml；包根即前端半区——渲染器归 `tui/tools/<tool>.ts`、辅助件归 `tui/lib/`）：前后端作者解耦开发与发布的包形态（与 A 型并存；参考测试夹具 `nova_harness/tests/package/test_b_type_package.py`）。
- `backend/tests/` —— 单元测试（Python 侧），**镜像 backend/ 目录**：`tools/`（10 工具一文件一测）/ `extensions/`（7 扩展）/ `user_tools/` / `nova_coding_agent/`（镜像可导入包：`bash/` 引擎与消息、`subagent/` 引擎、`tools_common/` 七模块、`test_ui_primitives.py`）；TS 侧测试归 `frontend/tests/`（镜像 `tui/`：`tools/`、`dialogs/`、`lib/`、`extensions/session_commands/slash/<name>/`）

该 bundle 的 `pyproject.toml` 中 `[tool.nova]` 段声明：
- `agents = ["./agents/"]`（组合层目录——扫描其下 `*.yaml`）
- `tools = ["./backend/tools/bash.py", "./backend/tools/edit.py", "./backend/tools/find.py", "./backend/tools/grep.py", "./backend/tools/ls.py", "./backend/tools/question.py", "./backend/tools/read.py", "./backend/tools/subagent.py", "./backend/tools/todo.py", "./backend/tools/write.py"]`
- `extensions = ["./backend/extensions/confirm_destructive.py", "./backend/extensions/executor_switch.py", "./backend/extensions/interactive_shell.py", "./backend/extensions/permission_gate.py", "./backend/extensions/plan_mode.py", "./backend/extensions/session_commands.py", "./backend/extensions/subagent_gate.py", "./backend/extensions/tools_panel.py"]`
- `user_tools = ["./backend/user_tools/bash.py"]`
- `personas = ["./backend/personas/"]`（persona 资源类目——目录条目，loader 递归收 .md 命名）
- `auto_install_dependencies = true`
- `binary_dependencies = { rg = "ripgrep" }`
- `binary_managed_dependencies = ["fd"]`

> `[tool.nova]` 还可声明 **`requires = ["<包名>"]`**（包间依赖——非 Python/npm 依赖）：安装时校验被依赖 nova 包已安装（user/project 合并视图，任一 scope 命中即满足），缺失即拒绝并附安装提示；卸载时被其他包 `requires` 引用的包拒绝卸载。B 型纯 TS 包以 package.json 顶层 `"nova": {"requires": [...]}` 声明同一语义。v1 只做约束校验不做来源解析（无中心 registry）。
>
> `[tool.nova]` 可声明的资源类目为 `agents` / `tools` / `skills` / `extensions` / `prompts` / `user_tools` / `personas` 七类能力资源；`themes` 与 `ui_blocks` 已移出 Python 资源系统（归 Node 层 UI 资产）。
>
> 工具与用户工具的形态：**工具即代码，无元数据文件**；单文件优先、目录按需。
> - `tools/<name>.py`（单文件，推荐）或 `tools/<name>/executor.py`（目录形态，需同目录资产时使用）：暴露 `Tool` 类——元数据为类属性（`name` / `description` / `parameters` 必需，可选 `label` / `execution_mode` / `prepare_arguments` / `prompt_snippet` / `prompt_guidelines`），`__init__(context)` 注入 `ToolContext`（cwd / settings 只读视图——构造期不变量），执行为 `execute(tool_call_id, params, signal, on_update, ctx)`——`ctx` 为 `ToolExecContext`（当前模型 + `ui`/`has_ui` 执行期 UI 句柄——pi `ctx.ui` 对位：弹窗经反向原语到前端渲染，工具逻辑不出 Python；注入点经 `ScopedUIContext` 织入作用域归属（run/session——仲裁按归属清扫）与并行弹窗串行锁，headless 时 `has_ui=False` 安全降级。每次调用由框架经 `context_provider` 现取注入，对齐 pi `wrapToolDefinition`）；
> - `user_tools/<name>.py` 或 `user_tools/<name>/executor.py`：暴露 `UserTool` 类——元数据同为类属性（import 即可读，白名单/碰撞检测无需会话），`__init__(session)` 注入会话上下文，可选 `MESSAGE_TYPES` 类属性（加载时注册进消息回载注册表，包缺席时旧会话中该类型消息降级为不透明消息，数据不丢）。
> 两者与 tools 同一纪律：只来自已安装包，不走顶层自动发现/settings 条目。
>
> 扩展形态与 tools 同一纪律：**单文件优先、目录按需**。`extensions/<name>.py`（单文件，推荐——无资产纯逻辑扩展）；目录形态（`<name>/extension.py` 或 `<name>/__init__.py`）仅在需要同目录子模块/资产时使用——发现机制收集根级全部 `.py` 但不递归扩展目录，目录内部 helper 不会被误当扩展加载。
>
> 二进制依赖（性能加速用，可选）：
> - `binary_dependencies = { 命令名 = "PyPI包名" }`——wheel 可装的二进制（如 `rg = "ripgrep==15.1.0"` 平台 wheel，官方包建议 pin 版本保证可复现），安装时随 pip 依赖进入当前环境 `bin/`；
> - `binary_managed_dependencies = ["fd"]`——框架注册表自管理的二进制。**注册表只收"PyPI 覆盖不了"的官方必需二进制（一个二进制一个家，当前仅 fd）**，安装时按 `package/binaries/registry.json` 的 pin 版本 + sha256 下载到 `~/.nova/agent/bin/`（`NOVA_OFFLINE` 跳过下载仅警告；Linux 区分 glibc/musl，Alpine 走 musl 资产）；
> - `binary_system_dependencies = ["xx"]`——无自动安装渠道的系统二进制要求（如 docker 类守护进程），安装时校验存在性、缺失仅警告（不代装）；
> - 运行时经 `nova_harness.core.utils.binaries.resolve_binary()` 三级解析（env bin → nova bin → PATH，托管优先；PATH 层识别发行版别名如 `fdfind`）；spawn 子进程 env 会自动前置 nova bin + env bin，bash 里可直接命中托管二进制；
> - 工具消费端应按"二进制加速 + 纯 Python 兜底"设计（如 grep/find 的 fd → rg → Python 三级链），二进制缺失不影响可用性；缺失警告附带 brew/apt 安装指引。

### `nova-client`（含内置 TUI 宿主）

运行时主体位于 `packages/nova-harness/frontend/src/`（wire/、bus.ts、mirror/、presentation/、packages/、extensions/、runtime.ts，见上文第 5 条）；TUI 作为一种宿主形态位于 `src/modes/tui/`：

- `main.ts` —— `nova` CLI 入口（commander，bin 指向 `dist/modes/tui/main.js`）
- `app.ts` —— `NovaTuiApp` 薄壳：槽位布局、生命周期、全局键位（Esc 域级路由/ctrl-c 双击/ctrl-d 空退/ctrl+o 展开）
- `components/` —— `transcript/`（消息与工具卡片视图）、`dialogs/`（五件套 controller + auth 等待框 + form 表单）、`pickers/`（searchable 通用选择器 + tree/sessions 专用选择器）、`status/`（loader/footer）、`layout/`（welcome/resources/RegionHost/OverlayHost）
- `blocks/` —— 声明式块的终端渲染（diff 词级高亮等）
- `themes/` —— 配色（pi dark 语义色）

构建命令（npm）：
```bash
cd packages/nova-harness/frontend
npm install
npm run build   # tsc -> dist/
npm run tui     # tsx 直接运行 TUI（src/modes/tui/main.ts）
npm start       # node 运行编译产物（dist/modes/tui/main.js）
npm link        # 全局注册 `nova` 命令
```

### `nova_team`（源码包 `nova_team`）

位于 `packages/nova_team/src/nova_team/team/`：

- `definitor.py` —— `TeamDefinitor`，动态合并配置、状态修改与保存
- `types.py` —— `SubagentMountEntry`、`MasterMountEntry` 等 dataclass
- `storage/` —— 两级存储后端抽象：`base.py`、`file.py`（基于 `filelock`）、`memory.py`、`types.py`

该包**没有 `pyproject.toml`**，也未声明 Poetry 依赖，属于早期开发状态。

### `nova_executor`（Rust 通用执行后端）与 `nova-executor-client`（Python SDK）

- **定位**：编程无绑定的通用执行后端——进程/文件系统/PTY + 三平台沙箱（macOS Seatbelt、Linux bwrap+landlock、Windows restricted token）+ managed network sandbox，JSON-RPC over stdio / WebSocket（stdio 为主：CLI/桌面/SSH 隧道场景；WS 用于回环与将来服务器托管）。fs 含大文件流式端点 `fs/readStream`（服务端推送，支持平台沙箱）/ `fs/writeStream`（客户端分片推）。**协议即产品**：线上契约在 `packages/nova_executor/PROTOCOL.md`（v1.0），任何语言照文档可实现客户端。
- **边界（重要）**：executor 不知道 agent/模型/工具/会话概念。已移除：模型 API 层（原 executor-codex-api）、agent 配置体系（executor-config）、Rust 侧工具注册处（executor-extension-items）、`capabilityRoots/discoverV1` 端点。**不要在 executor 里重新引入这些概念**——工具契约在 Nova 包体系（Python），正确接法是在 `nova_coding_agent` 的 bash 引擎后面挂 executor 实现（本地 subprocess ↔ executor 同缝切换）。
- **`nova-executor-client`**：`ExecutorClient` 薄客户端（process/fs/pty + errors），"只做连接"。initialize 时做 `protocolVersion` major 匹配。传输双形态：`WebSocketTransport` + `StdioTransport`（spawn 子进程 NDJSON，command 参数化——本地/SSH 同一实现）；`TransportPool` 多连接按通道路由（控制面/数据面分离，大文件流不阻塞工具调用）。已删除其自带的 Tool/Plugin/ExecutorBackend 三件套（与 Nova 契约冲突），不要恢复。
- 鉴权：executor 只做本地回环（stdio / WS 回环承载），**无入站鉴权**；对外暴露与鉴权归上层中继层（未落地），不归 executor。
- 构建/测试：`cargo build --workspace` / `cargo test --workspace`（在 `packages/nova_executor` 下）。

---

## 构建与开发命令

> 仓库已改用 **pixi** 作为统一的环境管理工具。根目录 `pyproject.toml` 中定义了 workspace，子包通过 editable path 依赖一次性安装。

### 环境初始化（pixi）

```bash
# 安装 pixi（如尚未安装）
curl -fsSL https://pixi.sh/install.sh | bash

# 安装默认环境（仅运行时依赖）
pixi install

# 安装开发环境（包含 black / isort / pytest / pytest-asyncio 等）
pixi install --environment dev
```

### 常用 pixi 任务

```bash
# 运行测试（在每个子包目录下独立执行，避免 tests 包名冲突）
pixi run -e dev test-ai
pixi run -e dev test-agent
pixi run -e dev test-harness
pixi run -e dev test-coding
pixi run -e dev test-all

# 格式化全部 Python 源码
pixi run -e dev format

# 直接调用已安装 CLI
pixi run -e dev nova-pkg list
pixi run -e dev nova-harness run
```

### 手动在子包内运行测试

```bash
cd packages/<子包名>
pixi run -e dev pytest tests -m "not integration"
```

### 格式化

```bash
pixi run -e dev black packages/<子包名>/src/
pixi run -e dev isort packages/<子包名>/src/
```

对于 `nova_coding_agent`，Python 代码全部位于 `backend/` 半区，整体格式化：
```bash
pixi run -e dev black packages/nova_coding_agent/backend
pixi run -e dev isort packages/nova_coding_agent/backend
```

### 构建与发布

```bash
cd packages/<子包名>
pixi run -e dev python -m build      # 生成 wheel / sdist
# poetry publish    # 如需发布到 PyPI（仍保留 poetry 配置）
```

### Poetry 兼容说明

各子包仍保留 `pyproject.toml` 中的 Poetry 配置，可作为 pixi 不可用时的回退：

```bash
cd packages/<子包名>
poetry install
poetry run pytest tests -m "not integration"
```

### 可执行脚本（由 `nova_harness` 注册）

安装 `nova_harness` 后，环境中会新增以下命令：

```bash
nova-harness run          # 非交互式运行已安装 agent
nova-harness-rpc          # 启动 JSON-RPC over stdio 服务器
nova-pkg list             # 列出已安装的包/定义/工具
nova-pkg install <path>      # 支持 path:/git:/npm: 三种源（npm 源支持精确版本、^/~ range、x-range/通配段、比较器集、|| 并集与 hyphen range，省略 = latest）
nova-pkg uninstall <name>
nova-pkg update <name>
nova-pkg info <name>
nova-pkg validate <path>
nova-pkg init             # 根据当前目录结构生成 [tool.nova] 段
```

### `nova-client` 专属命令

```bash
cd packages/nova-harness/frontend
npm install
npm run build      # TypeScript 编译到 dist/
npm test           # tsx --test（呈现映射单测）
npm run tui        # tsx 直接运行 TUI
npm start          # node 运行编译产物
npm link           # 全局注册 `nova` 命令
```

---

## 代码风格指南

- **类名**：`PascalCase`
- **函数 / 变量**：`snake_case`
- **常量**：`UPPER_CASE`（如 `APP_NAME = "nova"`）
- **导入排序**：使用 `isort`，配置为 `profile = "black"`、`multi_line_output = 3`、`include_trailing_comma = true`
- **格式化**：`black`，目标版本 `py312`
- **注释与文档字符串**：以**中文**为主，保持与现有代码一致
- **数据建模**：按以下顺序决策技术栈，不要为了"统一"而全用一种。
  1. **先问可变性**：对象创建后会被原地修改吗？可变 → **普通 class 或 `dataclass`**，禁用 Pydantic（校验与拷贝语义和可变运行时容器冲突）。例：`AgentState`（普通 class + property setter 做顶层数组拷贝）、`AgentContext`（被循环原地 append）、`AgentSessionServices`。
  2. **再问序列化**：对象需要跨进程（RPC / WebSocket）或持久化（会话 JSONL、settings、auth.json、models.json、包 manifest）吗？需要 → **Pydantic（`NovaBaseModel`）**，序列化与 schema 一体化，使用原生 `model_dump()` / `model_validate()`。例：`Model` / `Usage` / messages / Agent 事件。
  3. **校验只给不可信输入**：第三方产出的数据（工具返回值、用户配置、前端 payload）即使不直接序列化也可用 Pydantic，换取构造时尽早报错；框架内部自产自销的对象不做构造时校验。例：`AgentToolResult` 用 Pydantic 不是因为要序列化，而是工具作者是第三方。
  4. **`Callable` / 服务实例 / 异常永远不进 Pydantic**：依赖容器、hook 上下文、运行时中间态一律 `dataclass` 或普通 class。例：`AgentLoopConfig`、`StreamOptions` 家族、`Provider`、`AgentSessionConfig`。
  5. **不可变值对象优先 `frozen=True`**：纯数据、无序列化需求的值对象用 `dataclass(frozen=True)` 在类型层面锁死不可变性，不靠自觉。
  6. **union 必须可判别**：存在反序列化路径的 union 用 `Field(discriminator=...)` 显式判别，不依赖 smart-union 猜测。开放集（框架变体 + 包级兜底）用判别联合 + 兜底成员 + `union_mode="left_to_right"`（例：`server/types/items.py` 的 `WireItem`——`SerializeAsAny` 只管序列化方向，校验方向必须显式可判别，否则 `model_validate` 按基类重建剥掉子类字段）。
  7. **哑容器不进 Pydantic**：传输信封/中间态包装（需要原样持有任意内容——不校验、不重建、不转换）即使最终会上线，也用 `dataclass`。Pydantic 的"处理欲"对透明容器是害处。例：`JsonRpcMessage`——`result` 字段原样容纳模型实例/dict/None，序列化推迟到出货那一刻。
  8. **单道序列化**：生产侧（RPC handler 等）返回模型**实例**，dump 归传输/分派层单点出货；不在中间环节"先 dump 再 validate 再 dump"（双道打包会在重建时剥多态字段）。dispatch 出参对实例直通 dump_wire；声明了 result_model 却返回散装 dict → 契约违约报错（router.py 先例）。
  9. **RPC handler 签名即契约**：handler 签名必须类型化（`async def x(params: XxxParams) -> XxxResult`），体内一律属性访问（不散装取键）；注册表形状从签名注解自动推导（`register("x", x, domain=...)`——不重复声明 params_model/result_model）。形状模型集中在 `server/protocol/methods/shapes.py`；引用经 `shapes.` 模块前缀或模块级逐个 import，**禁止在函数内局部 import shapes**（`get_type_hints` 只查模块 globals——局部 import 会让推导静默失败）。自由负载方法（无固定形状）注解保持 `Dict[str, Any]`，即不声明形状的语义。
- **类型注解**：代码中已大量使用类型注解，但未配置 `mypy` 静态检查
- **枚举字段**：在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。

---

## 测试说明

- 所有包含 `pyproject.toml` 的子包均已将 `pytest` 声明为开发依赖。
- Python 测试目录结构：
  - `packages/nova_ai/tests/`
  - `packages/nova_agent/tests/`
  - `packages/nova-harness/backend/tests/`
  - `packages/nova_coding_agent/backend/tests/`
- **TS 测试（node:test + tsx）**：统一收在包根 `tests/`（**镜像 src 子路径**——`tests/modes/tui/controllers/keymap.test.ts` ↔ `src/modes/tui/controllers/keymap.ts`）——
  - `packages/nova-harness/frontend/`：`npm test`（`tsx --test "tests/**/*.test.ts"`）
  - `packages/nova_coding_agent/`：Python 侧 pytest + TS 侧 `npm test`（`tsx --test "tests/**/*.test.ts"`，渲染器与其算法测试，如 `tests/tools/edit.test.ts` 与 `tests/lib/edit-preview.test.ts`）；`npm run typecheck` 单独类型检查
- 真实 API 集成测试已用 `pytest.mark.integration` 标记；`nova_ai` 与 `nova_harness` 的集成测试需要 `VOLCENGINE_API_KEY` 等环境变量。
- 已通过 pixi 安装 dev 环境并验证：`nova_ai` 458 个、`nova_agent` 105 个、`nova_harness` 1303 个、`nova_coding_agent` 236 个非集成测试全部通过；修改关键逻辑后应在对应子包内运行测试并确认结果。

运行方式：

```bash
# 使用 pixi（推荐）
pixi run -e dev test-ai
pixi run -e dev test-agent
pixi run -e dev test-harness
pixi run -e dev test-coding

# 手动在子包内运行
cd packages/<子包名>
pixi run -e dev pytest tests
pixi run -e dev pytest tests -m "not integration"    # 跳过真实 API 调用
pixi run -e dev pytest tests --cov=<包名> --cov-report=html

# Poetry 兼容方式
cd packages/<子包名>
poetry run pytest tests -m "not integration"
```

---

## 安全注意事项

1. **API Key 存储**
   - `nova_harness` 的鉴权信息保存在 `~/.nova/agent/auth.json`，由 `AuthStorage` 管理。
   - `models.json` 中的 `api_key` / header 值按 TS 语义解析：`$VAR` / `${VAR}` 为环境变量引用（缺失即报错并指明变量名），`!cmd` 前缀执行 shell 命令取输出，`$$`/`$!` 转义，其余一律按字面量；解析逻辑在 `core/config/resolve.py`，鉴权在请求时经 nova_ai 的 auth 链完成，不写入 `Model` 对象。
   - `nova_ai` 层不持久化密钥，全部通过环境变量按 `provider` 名称映射读取。

2. **会话数据**
   - 会话历史以 **JSONL 明文**存储在 `~/.nova/agent/sessions/--<cwd>--/` 下，可能包含敏感代码片段或输出。
   - 根目录 `.gitignore` 已忽略 `sessions/` 与 `*.session`。

3. **文件操作安全**
   - Agent 配置加载器（`core/resources/loaders/agent_config.py`）只读取 agent 目录内的固定文件（`agent.yaml`、`description.md`、`sections/*.md`），不接受外部传入的任意路径，无路径逃逸面。
   - 扩展发现（`package/resolve/discovery.py`）只收集根级 `.py` 文件与合法扩展目录，不递归非扩展目录，避免辅助模块被当扩展加载执行。

4. **Project Trust**
   - `~/.nova/agent/trust.json` 保存用户对项目文件夹的信任决策；扩展可通过 `project_trust` 事件参与裁决。
   - 无 UI 的 headless/RPC 模式默认信任存在 `.nova` 资源的项目，以保持向后兼容；有 UI 的前端（如 `nova-client` 的 TUI 宿主）会弹出确认对话框。
   - **trust 只存在于运行时**：会话启动决议 + resolver 读取门控。`nova-pkg` 包管理不做 trust 检查（装/卸包是主动行为），也没有 `trust`/`untrust` 子命令。

5. **敏感信息**
   - 历史 notebook 文件 `packages/nova_agent/src/test.ipynb` 已删除。新增示例 `packages/nova-harness/backend/examples/` 中不应包含真实 API Key。

---

## 开发惯例与给 AI Agent 的提示

- **修改前请先确认所属子包**：不同子包有独立的 `pyproject.toml` 与依赖，不要混用。
- **环境管理**：仓库使用根目录 `pyproject.toml` 中的 `[tool.pixi.*]` 作为统一 workspace。新增或调整依赖时，优先在根 `pyproject.toml` 中声明，以便所有子包共享同一环境。
- **不要假设测试一定通过**：`nova_ai` 与 `nova_agent` 非集成测试当前通过；`nova_harness` 存在若干既有失败用例。修改关键逻辑后建议手动验证或补充测试。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文，与现有代码一致。
- **序列化层**：新增数据类先按上文"数据建模"的决策顺序选型；选用 Pydantic 时一律继承 `NovaBaseModel`（基于 `pydantic.BaseModel`）。
- **依赖新增**：
  - 若新增**第三方库**，优先在根 `pyproject.toml` 的 `[tool.pixi.pypi-dependencies]`（运行时）或 `[tool.pixi.feature.dev.pypi-dependencies]`（开发时）中声明，然后执行 `pixi install -e dev`。
  - 各子包仍保留 Poetry 配置作为兼容；如使用 Poetry，需在对应子包 `pyproject.toml` 的 `[tool.poetry.dependencies]` 中声明并执行 `poetry lock`（如有 lock 文件）。
- **路径约定**（前后端分治 §9，全文见 `packages/nova-harness/frontend/docs/frontend-backend-separation.md`）：
  - 全局配置根目录默认：`~/.nova/agent`（settings/auth/trust/models/sessions/packages/logs/bin 等**后端状态**平级保留）
  - 后端散养资源：`<base>/backend/{extensions,skills,prompts,personas}`（user 级 base = `~/.nova/agent`，项目级 base = `<cwd>/.nova`）；`agents/` 两半共享平级保留（`<base>/agents`）；旧位目录在会话服务装配时自动迁移（mv 语义、幂等、新位已有内容不合并不覆盖）
  - 前端域（按宿主分级）：`~/.nova/agent/frontend/tui/`（settings.json / state/ / keybindings.json / themes/ / debug/ + 散养 `tools/`、`dialogs/`、`index.ts`——扫描能力）；项目级 `<cwd>/.nova/frontend/tui/` 同构（散养资产过 trust 门）；前端旧位（ui-settings.json/ui-state/keybindings.json/themes）由 TUI 启动时自动迁移
  - 项目级配置目录：`<cwd>/.nova`（`.nova/settings.json` 不动）
  - 会话目录：`~/.nova/agent/sessions/--<cwd>--/`
  - Project Trust 记录：`~/.nova/agent/trust.json`
- **`nova_team`** 为早期实现，修改时请保持最小侵入，避免破坏上层 `nova_harness` 的既有接口。
- **子包级 AGENTS.md**：`nova_ai` 与 `nova_harness` 各自包含更详细的包级指南，深入修改这两个包时建议优先阅读对应文件。
- **新增 Agent / 工具 / 扩展**：参考 `nova_coding_agent` 的 `[tool.nova]` 段与目录结构；使用 `nova-pkg init` 可自动生成该段。

---

## 版本与变更

- 当前版本：`0.1.0`（Alpha）；`nova-coding-agent` bundle 版本为 `1.0.0`
- 变更日志：根目录 `CHANGELOG.md` 记录了仓库级变更；各子包的 `CHANGELOG.md` 目前为空。
