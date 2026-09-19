# Nova 架构与 Monorepo 结构

> 本文档描述仓库的包结构与各子包内部组织（「系统长什么样」）。工作规则见根 AGENTS.md，数据建模见 docs/data-modeling.md，命令见 docs/development.md。

## Monorepo 结构与包依赖关系

```
nova/
├── packages/
│   ├── nova_ai/            # 统一的 LLM 提供商抽象层
│   ├── nova_agent/         # 事件驱动的异步 Agent 框架
│   ├── nova_harness/       # 高阶 Agent SDK（会话、压缩、工具链、包管理、RuntimeManager）
│   ├── nova_server/        # JSON-RPC 协议服务层（protocol/transport/reduction/client 家族/exec）
│   ├── nova_client/        # 前端运行时（TS 厚应用层 + 内置 TUI 宿主；npm 包）
│   ├── nova_executor/      # 通用执行后端（Rust：进程/文件/PTY/三平台沙箱，JSON-RPC over stdio/WS）
│   ├── nova-exec-server-client/   # executor 的 Python 薄客户端（连接 + 配置发现 + 物化）
│   └── nova-agent-rs/      # Rust 侧实验性代码（非发布路径）
├── bundles/
│   └── nova_coding_agent/  # 官方编程 Agent bundle 与本地文件系统工具（官方包住 bundles/，框架住 packages/）
├── docs/                   # 专题文档（本文件、data-modeling.md、development.md、security.md）
├── AGENTS.md               # 索引与随身规则
├── README.md
├── CHANGELOG.md
└── .gitignore
```

### 运行时依赖层次（自下而上）

1. **`nova_ai`** —— 最底层。提供多厂商（OpenAI、Anthropic、Google、Volcengine、GitHub Copilot 等）统一的流式调用、模型注册表、鉴权、消息类型与兼容性层。当前仅有 `api_impls/openai_completions/` 包一个完整实现。
2. **`nova_agent`** —— 核心框架。基于 `nova_ai` 的模型能力，提供 `Agent` 类、事件订阅/发布、`agent_loop` 异步循环、生命周期管理、工具校验与执行。
3. **`nova_harness`** —— 高阶 SDK。基于 `nova_ai` + `nova_agent`，封装 `AgentSession`、会话树（分支/fork/导航）、上下文压缩（Compaction）、资源加载、设置持久化、模型注册表覆盖、内置工具链、包管理器 CLI、Project Trust 门控与 `ExtensionUIContext` UI 桥接。
   manager 层（读自由写独占、互不调用、编排在 AgentSession）：`AgentManager`（agents 注册表视图 + 当前角色旋钮 + 默认解析链 + CapabilitySelection 汇集 + yaml 写回——`/agent save` 落地，包来源影子写 user 级）、`ToolsManager`（工具裁决单点）、`PersonaManager`（persona 装配 + override）、`SystemPromptManager`（纯渲染，激活工具含 `subagent` 时注入 `# Available Agents` 委派菜单）、`SettingsManager`（settings 唯一写门）。
4. **`nova_server`** —— 协议服务层。基于 `nova_harness`，提供 JSON-RPC 服务器（8 域方法表 + 签名推导形状 + schema 导出）、传输（stdio/WS/memory）、归约层（事件 → wire 条目）、client 家族（in_process/remote）与 headless exec。
5. **`nova_coding_agent`** —— 官方 bundle。同时是一个可 import 的 Python 包，提供 `coding_agent` 等 5 个 Agent 组合声明（含 scout/planner/reviewer/worker 子代理）、八个扩展（`session_commands`/`permission_gate`/`plan_mode`/`tools_panel`/`interactive_shell`/`confirm_destructive`/`subagent_gate`/`executor_switch`）、10 个本地工具（bash、edit、find、grep、ls、question、read、subagent、todo、write）以及 `bash` 用户工具（`user_tools/`，LLM 工具与会话 bash 共享同一引擎）。
   **bash 与六个 fs 工具（read/write/edit/ls/find/grep）均可随后端切换**（执行期读 `BackendSelection` 模式格；question/todo/subagent 不触碰执行环境不在切换面）——进程走 ExecutorBashOperations，文件系统走 FileSystemLayer 双实现（本地/远程同一实现类），详见 `packages/nova_harness/examples/executor-integration.md`。
6. **`nova_client`** —— 前端运行时。TypeScript 厚应用层，TUI 是包内的一种宿主形态；与将来的 Web 宿主共享运行时主体。纯 npm 包（无 Python 源码；运行时需 Python 环境中可导入 `nova_harness`，`NOVA_PYTHON` 可指定后端解释器）。
7. **`nova_executor` + `nova-exec-server-client`** —— 通用执行后端与其 Python 客户端（见下文专节）。

> **依赖声明现状**：
> - 各 Python 子包的 `[tool.poetry.dependencies]` **只声明各自的第三方依赖**，不再声明兄弟包的 path 依赖（pip 对"同一包同时被 editable 与非 editable path 依赖引用"会报 ResolutionImpossible；互依关系到发布时再恢复）。
> - 统一安装的单一事实源是根 `pyproject.toml` 的 `[tool.pixi.pypi-dependencies]`（六个 editable path 包 + 第三方依赖并集，`pixi install -e dev` 一把求解）；服务器部署脚本同理按此清单安装。

---

## 各子包详细结构

### `nova_ai`（源码包 `nova_ai`）

位于 `packages/nova_ai/src/nova_ai/`：

- `gateway/` —— `Models` 集合（`models.py`）、`Provider` 运行时单元与 `create_provider`（`provider.py`）、`ModelsStore`（`store.py`）；鉴权解析在请求时完成（runtime override → stored credential → 环境变量链 → OAuth 刷新）
- `providers/` —— 内置厂商定义（`volcengine`、`moonshotai`、`moonshotai_cn`、`kimi_coding`），各含静态模型数据与 provider 工厂；`all.py` 提供 `builtin_models()`
- `auth/` —— `AuthContext`、credential store 协议、`resolve_provider_auth`、`env_api_key_auth` 等辅助，以及 `oauth/`（OpenAI Codex、Kimi 的 OAuth 流程与登录页）
- `api_impls/` —— API 协议实现：`openai_completions/` 包（当前唯一完整实现）
- `streaming.py` —— `AssistantMessageEventStream` 与流式调用入口
- `stream_options.py` —— AI 调用面参数包（`StreamOptions`/`SimpleStreamOptions`/`ThinkingBudgets` 等，dataclass）
- `utils/` —— 环境变量、JSON 解析、消息转换、流选项、Unicode 代理项清理、上下文溢出检测、模型工具函数等

> 类型词汇（messages/content/model/events/auth 等）已迁 `nova_protocol` 词汇枢纽——本包只剩行为。见 docs/data-modeling.md「住所」。

**Auth 层三层治理（终态）**：流程层（本包 `auth/`）只管状态机与密钥（PKCE/state/换 token/轮询），收货需求经 `AuthInteraction.acquire_authorization_code` 声明；接线层（`nova_harness/config/auth/`）装配收货通道（宿主监听 `host:listenOnce` / 本地一次性监听 / 手动粘贴框）与竞速仲裁；渲染与用户本机执行（开浏览器 `host:openUrl`、收货、输码）归前端。开浏览器永远不后端代开。

开发期 `docs/`（架构设计/ADR/devlog 等）已随 0.1.x 封版移除——设计文档以包内 `README.md` 为准；历史版本可在 git 历史中查阅。

### `nova_agent`（源码包 `nova_agent`）

位于 `packages/nova_agent/src/nova_agent/`：

- `agent.py` —— `Agent` 类，封装状态管理、事件订阅、消息队列与生命周期
- `agent_loop/` —— 核心异步循环包
  - `facade.py` —— 对外暴露的 `agent_loop()` / `agent_loop_continue()` / `run_agent_loop()` / `run_agent_loop_continue()`
  - `loop.py` —— 循环内部实现
  - `tools.py` —— 循环中的工具执行相关逻辑
- `types/` —— 完整事件类型体系、Agent 状态、上下文、工具、钩子上下文与结果等
- `signal.py` —— `AbortSignal` / `AbortController` 异步取消信号
- `stream_fn.py` —— 宿主（如 nova_harness）安装默认模型运行时 stream 函数的注入点
- `utils.py` —— 工具调用校验与参数验证（基于 `jsonschema`）

### `nova_harness`（源码包 `nova_harness`）

位于 `packages/nova_harness/src/nova_harness/`：

- `app/` —— CLI 入口层：`main.py`（`nova-harness` 命令，当前为入口占位）、`package.py`（`nova-pkg` 包管理器 CLI）
- `core/agent_session/` —— `AgentSession` 运行时核心、`AgentSessionRuntime`、`AgentSessionServices` 与领域控制器 `controllers/`（compaction、events、model、queue、retry、slash_input、stats、tools、tree、user_tools）
- `core/domains/` —— 领域能力：`agents/`（多 agent 注册表）、`compaction/`（上下文压缩与分支摘要）、`persona/`、`system_prompt/`、`skills.py`、`tools/`（工具裁决）、`user_tools/`（UserToolManager 注册中心——框架不内置用户工具，由包经 `[tool.nova] user_tools` 类目分发）
- `core/runtime_manager/` —— 会话工厂与装配（`assembly.py`、`factory.py`、`manager.py`）
- `core/utils/` —— 通用工具（二进制解析、子进程、HTTP 空闲超时、OutputGuard、遥测等）
- `sessions/` —— 会话**持久化账本**域：`manager.py`、listing/builders/cache_stats、消息回载注册表 `message_types.py`；与运行时会话分离（运行时归 core/agent_session，账本归 sessions）
- `types/` —— 共享词汇层：跨域契约 `protocols.py`、自定义消息 `messages.py`、各域类型子包（`session/` `ui/` `resources/` `extensions/` `package/` `config/` `model/` `compaction/` `project_trust.py`）。领域私有类型就近放各域，不在此聚合
- `events/` —— 会话事件联合（`AgentSessionEvent` 等）与监听器类型
- `resources/` —— 资源发现与加载（`loader.py` 与 `loaders/` 下的 agent_config、extensions、personas、prompt_templates、skills、tools、user_tools、context_files）+ Project Trust 门控（`project_trust/`：信任决策、resolver 读取门控、`trust_store.py` 持久化）
- `package/` —— Agent / tool / bundle / skill / extension 包管理器核心（manager facade + install/ 安装世界 + resolve/ 运行时世界 + source/ source 领域 + manifest / validation / scaffold / utils + `binaries/`）。安装事实以 `*.dist-info/`（PEP 610 风格）为权威快照，副本推导兜底
- `config/` —— settings、auth storage、路径默认值、配置解析（含 `auth/`、`settings/`、`storage/`）
- `model/` —— 模型域：注册表运行时（`ModelRuntime`、store/composer）、模型解析（`resolver.py`）、provider attribution
- `extensions/` —— 扩展系统：API、loader、runner、event_bus、`types/`

> JSON-RPC 服务器宿主已独立为 `packages/nova_server`（见下节），不在本包内。

### `nova_server`（源码包 `nova_server`）

位于 `packages/nova_server/src/nova_server/`：

- `server.py` —— `RpcServer` + 连接层 `Connection`/`ConnectionRegistry`
- `protocol/` —— `methods/`（auth/model/package/resources/session/settings/system/state/user_tools 九域 + `shapes.py` 线上形状声明——dispatch 校验 / schema 导出 / 能力位三方共吃）+ `router.py` + `jsonrpc.py` + `serialize.py` + `schema_export.py`（前端 gen 工件导出）+ `errors.py`
- `transport/` —— stdio / WebSocket / memory 三传输（WS 鉴权三守则见 `transport/websocket.py`）
- `reduction/` —— 事件 → wire 条目归约（`orchestrator.py`、`mapping.py`、`entries.py`）
- `types/` —— wire 条目词汇（`items.py` 的 `NovaItem`/`WireItem` 判别联合 + `CustomItem` 兜底；`notifications.py`）
- `ui_context.py` —— `RoutingUIContext`（跨连接反向原语路由）
- `rpc/` —— `nova-server` CLI 装配入口；`exec/` —— headless 运行（协议客户端形态）
- `client/` —— client 家族：`base.py` / `in_process.py` / `remote.py`

### `nova_coding_agent`（bundle + Python 包）

位于 `bundles/nova_coding_agent/`，三段式结构（素材/组合分层）：**Python 半区在 `backend/`**（执行体 + 文本素材），**TS 半区在 `frontend/`**（自含 TS 子包），**组合层在 `agents/`**（角色选配 yaml，与两半区平级）：

- `agents/coding_agent.yaml` —— **Agent 组合声明**（纯选配零内容附着）：元数据（name 缺省=文件名）+ `persona`（人格条目列表——能相对 yaml 解析为文件/目录的按路径装配（文件逐列或目录递归字典序展开），否则按注册名查 persona 注册表；顺序即组装顺序，会话期由 PersonaManager 装配）+ 能力名单（`tools` 激活集、`extensions`/`user_tools` 白名单（空=全允许）、`commands` 命令允许集（空=全放）、`skills` 包内裁剪名单（空=全放、非空仅裁包内）——名单字段统一三态：键缺席=全放、显式空列表=全禁、支持 `!` 排除）。
  `model:` 字段 = 人格默认模型（初始模型解析链 tier 4：CLI/scoped 之后、settings 默认之前；无鉴权/未知 provider 静默落回）。
  同目录另有 **subagent 四件套**组合声明：`scout.yaml`（侦察）/ `planner.yaml`（只读规划）/ `reviewer.yaml`（评审）/ `worker.yaml`（全能力执行——显式不含 subagent 防递归），供 `subagent` 工具按名调用。
  **只有 agents，没有 subagents**——yaml 的 `subagents` 死字段已删除，可委派名单即会话注册表全量（无主从划分）
- `backend/personas/` —— 人格文本资源（persona 为正式资源类目：`coding/core.md` 主人格 + `subagents/{scout,planner,reviewer,worker}.md` 子代理人格；命名 = 相对 personas 根去 .md，如 `coding/core`；经 `[tool.nova] personas` 类目分发，与 `prompts/` 用户模板分源——身份文本 vs 命令宏不同概念）
- `backend/tools/` —— 10 个本地工具，**单文件形态**（`bash.py` 即工具，元数据为 `Tool` 类属性）：
  - `bash.py` / `edit.py` / `find.py` / `grep.py` / `ls.py` / `question.py` / `read.py` / `subagent.py` / `todo.py` / `write.py`
  - `question.py`：交互式询问工具（`ToolExecContext.ui` 首个消费者——能力门控双路径：`dialog:question` 已注册走包侧单框（选项+内联自由输入，组件在 `frontend/tui/dialogs/question.ts`），否则基线两步降级（select_items→input）；支持单问 `question`+`options` 或多问 `questions`（1~4 问——多问经 tab 条分页一次提交 `{answers}`，降级路径逐问串行）；`execution_mode="sequential"`）
  - `subagent.py`：三模式（single/parallel/chain）子代理委派——**消费会话 agents 注册表**（`ToolExecContext.agents` 快照按名查表，工具侧零发现管线；未知名报错列可用名含 source 标签）；on_update 聚合回调携带全量结果列表（parallel 含 `exit_code=-1` 运行中占位），执行引擎在 `backend/nova_coding_agent/subagent/`；执行前确认归 `subagent_gate` 扩展，激活时系统提示词注入 `# Available Agents` 菜单（AgentManager 供数）
  - `todo.py`：全量替换语义的清单工具（零服务端状态——状态单一事实源是会话里最新工具结果的 details，分支安全天然成立）
- `backend/prompts/` —— 用户模板：`debug.md` / `refactor.md` + subagent 工作流三件套（`implement.md` = scout→planner→worker、`scout-and-plan.md`、`implement-and-review.md`，`$@` 占位）
- `backend/user_tools/bash.py` —— `bash` 用户工具（单文件，暴露 `UserTool` 类）
- `backend/extensions/` —— 八个扩展：
  - `session_commands.py`（21 个 slash 命令，含 `/help` 命令清单、`/todos` 清单查看、`/scoped-models` 池列出、`/persona` 人格切换与 `/agent` 角色切换/保存——选择器/直切 + `persona_override`/`agent` 条目持久化 + session_start/session_tree 分支恢复；`/agent save`/`save-as <name>` 把当前生效状态物化回组合声明 yaml——包来源影子写 user 级）
  - `permission_gate.py`（tool_call 拦截：bash 危险命令询问、写保护路径拦截）
  - `plan_mode.py`（只读规划模式——`/plan` 切换 + ctrl+alt+p + `--plan` 旗标，edit/write 从激活集移除、bash 限只读白名单（tool_call 拦截），"Plan:" 编号计划提取与 [DONE:n] 进度跟踪，footer 状态条（`set_status` 命名通知：⏸ plan / 📋 n/m），状态经 append_entry 持久化）
  - `tools_panel.py`（`/tools` 工具开关面板——`dialog:tools` 复选面板或文本回退，`set_active_tools` 绝对集应用 + `tool-panel` 条目持久化，session_start/session_tree 从分支最新条目恢复）
  - `interactive_shell.py`（user_bash 拦截：vim/htop/less/ssh 等 14 程序集或 `i ` 前缀强制——`dialog:interactive-shell` 终端让位执行，无能力回 `(interactive commands require TUI)`）
  - `confirm_destructive.py`（`session_before_switch`/`session_before_fork` 确认门——有 UI 且当前会话非空时 confirm，选否经类型化结果 `cancel=True` 取消切换）
  - `subagent_gate.py`（subagent 委派自治权检查点——tool_call 拦截逐名裁决：允许一次/本会话始终允许（`subagent_allow` 条目持久化、分支恢复）/取消拦截；headless 直接放行）
  - `executor_switch.py`（`/executor` 执行后端切换——选择器/直切 local/远程端点/SSH 主机（`remote user@host [远程目录]` 裸目标直输、选择器"＋ 添加远程主机"入口），三通道分离：`executor_backend` 会话条目管记忆（分支恢复，含 `remote_cwd`/`remote_shell`）+ bundle runtime 格管执行（bash 引擎执行期直读）+ notice 管用户回执 + `refresh_system_prompt` 触发环境段重建（`<cwd>` 渲染执行 cwd）；SSH 供给归 `nova_coding_agent/executor/provision.py`——密钥优先 + 首连终端让位输一次密码装管理密钥、二进制按平台缓存 scp 上传、`-tt`+`exec` 单 ssh 进程承载隧道（连接断即 SIGHUP 回收远程，零孤儿）、token 现生成不落盘、远程执行 cwd 缺省为会话隔离工作区（`<远程家目录>/.nova/agent/executor/workspaces/<session-id>`，显式目录 `test -d` 校验并随端点记忆）、供给成功自动登记 settings `executor.endpoints`（`register_executor_endpoint` 写门）、`/executor forget` 移除、隧道死亡懒重供给；**策略面**：settings `executor.sandbox` 档位（read-only/workspace-write）经 `executor/policy.py` 的 SpawnPolicy 挂上模式格、随 `process/start` 下发（策略归 Nova 设置组装、执行归 executor，作用目录三态见 `packages/nova_harness/examples/executor-integration.md` §八），networkProxy 键等网络沙箱批次再定）
- `frontend/tui/dialogs/` —— **包侧自定义对话框**（`dialog:*` slot）：`question.ts`（question 工具单框——选项 + 内联自由输入；多问形态 `questions`：tab 条分页 + 全答完提交 `{answers}`；注册即触发 system/capabilities 重宣告，后端 `has_capability("dialog:question")` 放行）/ `tools.ts`（工具开关面板——`[x]` 复选行 + `{active: [name...]}` 提交）/ `interactive-shell.ts`（终端让位——setImmediate 异步挂起 TUI、spawnSync 交互命令、恢复后 `{exitCode}` 回执）
- `frontend/tui/tools/` —— **TS 渲染器（组件形态）**：`bash.ts`（终端风）/ `edit.ts`（diff 风，消费引擎预生成的 patch）/ `read.ts`（文件风）/ `write.ts` / `find.ts` / `grep.ts` / `ls.ts` / `todo.ts`（清单卡片）/ `subagent.ts`（三模式：流式占位 ⏳、usage 行、工具调用格式化、展开态 Markdown 终输出）——**返回活 pi-tui 组件**（渲染器契约双形态：`NovaBlock[] | Component`，判别在消费点；组件经 `input.env` 取色/取主题；**输入即线上归约成品 `input.item`**（ToolCallItem——服务器归约，前端无中间卡片模型））；
  `tools/<tool>.ts` 文件名即工具名。
  **镜像约定**：前端段镜像后端资源类型目录（`tools/`、`user_tools/`、`extensions/`——位置即语义）；
  渲染器目录是纯发现域（一文件一工具、默认导出渲染函数，可选 `preview` 命名导出做执行前只读预览）——辅助模块归 `tui/lib/`（如 `edit-preview.ts` 匹配引擎），测试归 `frontend/tests/`（发现逻辑跳过 `*.test.ts`）
- `frontend/tui/index.ts` —— 扩展入口（ExtensionUIAPI 工厂：/tree、/todos、/model、/scoped-models、/resume、/fork 命令 UI 注册——其组件与编排在 `tui/extensions/session_commands/slash/{tree,todos,model,scoped-models,resume,fork}/`，镜像后端扩展归属；通用选择器件（searchable/selector/hints）经 `nova-client` 的 `modes/tui/*` 子路径共享宿主单例，不复制；后端同名命令保留 headless 回退）
- `frontend/package.json` —— npm 清单（`pretty-ms`/`diff` 依赖 + typescript devDep）：nova-pkg 安装第 4 阶段（npm ci/install）的触发点（A 型探测 `<包根>/frontend/package.json`；B 型包根即前端半区，探测包根 `package.json`）；`tsconfig.json` 供开发期类型检查
- `backend/nova_coding_agent/` —— bundle 自身的 Python 包（poetry `packages` 段 `from = "backend"`，import 路径 `nova_coding_agent.xxx` 不变），供 tools 共享辅助模块（`tools_common/`：路径/队列/截断/输出累加/shell 解析等工具基建 + **`fs_layer.py`**——`FileSystemLayer` 统一 fs 原语（全 async，read/write/edit/ls/find/grep 六个 operations 实现参数化在它上面，本地/远程同一实现类）+ `operations.py`（per-tool operations 协议与实现；`bash/`：bash 执行引擎与消息类型，LLM bash 工具与会话 bash 共享；`executor/`：执行后端接入——manager（客户端生命周期 + SSH 隧道 + atexit 清理）/ provision（SSH 供给 + rg 探测）/ backend（ExecutorBashOperations）/ **fs_layer（ExecutorFileSystemLayer）** / **process_runner（grep/find 的 spawn 缝——本地 asyncio 子进程 / 远程 process/start 无壳 argv 直启，远程 rg 路径随供给探测）** / runtime（BackendSelection 模式格 + `backend_file_layer` + `backend_process_runner` + `resolve_backend_path`——六工具执行期解析远程 fs 层与路径，相对→remote_cwd、~→remote_home））；`ui_primitives.py`：**UI 标准原语的官方定义点**（基线五件套词汇 + `set_status` 展示类词汇（footer 扩展状态行）+ `select`/`select_items`/`confirm`/`input`/`form`/`notify_message`/`set_status` 糖库——harness 的 `UIContext` 是零词汇泛型 transport，词汇定义权归包，设计见 `packages/nova_client/docs/ui-primitives.md`）

> **B 型纯 TS 包**（package.json 身份证，无 pyproject.toml；包根即前端半区——渲染器归 `tui/tools/<tool>.ts`、辅助件归 `tui/lib/`）：前后端作者解耦开发与发布的包形态（与 A 型并存；参考测试夹具 `nova_harness/tests/package/test_b_type_package.py`）。
- `backend/tests/` —— 单元测试（Python 侧），**镜像 backend/ 目录**：`tools/`（10 工具一文件一测）/ `extensions/` / `user_tools/` / `nova_coding_agent/`（镜像可导入包：`bash/` 引擎与消息、`subagent/` 引擎、`tools_common/` 模块、`test_ui_primitives.py`）；TS 侧测试归 `frontend/tests/`（镜像 `tui/`：`tools/`、`dialogs/`、`lib/`、`extensions/session_commands/slash/<name>/`）

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
> - `tools/<name>.py`（单文件，推荐）或 `tools/<name>/executor.py`（目录形态，需同目录资产时使用）：暴露 `Tool` 类——元数据为类属性（`name` / `description` / `parameters` 必需，可选 `label` / `execution_mode` / `prepare_arguments` / `prompt_snippet` / `prompt_guidelines`），`__init__(context)` 注入 `ToolContext`（cwd / settings 只读视图——构造期不变量），执行为 `execute(tool_call_id, params, signal, on_update, ctx)`——`ctx` 为 `ToolExecContext`（当前模型 + `ui`/`has_ui` 执行期 UI 句柄：弹窗经反向原语到前端渲染，工具逻辑不出 Python；注入点经 `ScopedUIContext` 织入作用域归属（run/session——仲裁按归属清扫）与并行弹窗串行锁，headless 时 `has_ui=False` 安全降级。每次调用由框架经 `context_provider` 现取注入）；
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
> - 工具消费端应按"二进制加速 + 纯 Python 兜底"设计（如 grep/find 的 fd → rg → Python 三级链），二进制缺失不影响可用性；缺失警告附带安装指引。

### `nova_client`（含内置 TUI 宿主）

运行时主体位于 `packages/nova_client/src/`；TUI 作为一种宿主形态位于 `src/modes/tui/`：

- 运行时子系统：`wire/`（client 传输 + capabilities 契约 major/minor 握手与能力位 + bridge 反向原语路由）、`bus.ts`（观察式事件脊柱，mirror 特权订阅）、`mirror/`（会话镜像：mapping 纯函数归约 + store 状态容器 + types 呈现词汇）、`presentation/`（blocks 声明式块词汇（开放集 + validateBlock schema 校验）+ slots 注册表（tool/entry/region/block/editor/command/shortcut/autocomplete/dialog 九族键）+ extension-api 扩展 UI API + theme-json 主题契约）、`packages/`（pkgList 索引 + npm 自愈 + 更新提醒）、`resources/`（呈现资源层：discovery 统一发现 + trust 编排层过滤 + loader jiti 管线）、`settings/`（UISettings 扩展设置 + UIStateStore 扩展 KV——Node 层存储，不进后端 settings）、`keymap/`（键位能力子系统——keybindings.json 加载/三级合并 + 保留键位对账；TUI 默认键位表在 `modes/tui/keymap/tables.ts`）、`export/`（会话导出 HTML——模板三件套 + vendored marked/highlight.js，宿主无关、主题注入）
- 扩展 UI API 的 ctx 纪律：只收"后端够不着的宿主原语"（对话框五件/编辑器/剪贴板/setStatus/onTerminalInput/主题/`events.on` 事件观察口/`runInteractive` 终端让位/setTitle/notifyDesktop/setFooter+setHeader 整件替换/setWorking* loader 三旋钮）；后端方法的访问面就是 invoke 全量生成方法表，不手写包装域
- `RuntimeHost` 接口与进程内实现同居 `runtime.ts`（WS 宿主落地时再立 `hosts/` 目录）
- TUI 宿主（`src/modes/tui/`）：app.ts 纯装配根 + controllers/ 编排层（editor/keymap/dialogs/transcript/status/theme/settings/export/share/foreground/terminal/startup）+ components/{transcript,dialogs,pickers,status,layout} + blocks/ 块适配层 + builtin/ 内建扩展（/packages 包面板）+ themes/ 主题系统（dark/light 内建 + 用户目录 + 包内 frontend/themes 三源 + /theme 预览）+ utils/（clipboard/terminal-guard），基于 `@earendil-works/pi-tui` 渲染（overlay 经其 showOverlay），`bin.nova` 入口
- 线上契约类型经 `nova_server.protocol.schema_export` 构建期导出（`protocol/nova-wire.schema.json` + `src/protocol/nova-wire.gen.ts`，pytest 漂移测试保鲜），mapping/store/wire 全部基于生成类型

构建命令（npm）：
```bash
cd packages/nova_client
npm install
npm run build   # tsc -> dist/
npm run tui     # tsx 直接运行 TUI（src/modes/tui/main.ts）
npm start       # node 运行编译产物（dist/modes/tui/main.js）
npm link        # 全局注册 `nova` 命令
```

### `nova_executor`（Rust 通用执行后端）与 `nova-exec-server-client`（Python SDK）

- **定位**：编程无绑定的通用执行后端——进程/文件系统/PTY + 三平台沙箱（macOS Seatbelt、Linux bwrap+landlock、Windows restricted token）+ managed network sandbox，JSON-RPC over stdio / WebSocket（stdio 为主：CLI/桌面/SSH 隧道场景；WS 用于回环与将来服务器托管）。fs 含大文件流式端点 `fs/readStream`（服务端推送，支持平台沙箱）/ `fs/writeStream`（客户端分片推）。**协议即产品**：线上契约在 `packages/nova-exec-server/PROTOCOL.md`，任何语言照文档可实现客户端。
- **边界（重要）**：executor 不知道 agent/模型/工具/会话概念。已移除：模型 API 层、agent 配置体系、Rust 侧工具注册处、`capabilityRoots/discoverV1` 端点。**不要在 executor 里重新引入这些概念**——工具契约在 Nova 包体系（Python），正确接法是在 `nova_coding_agent` 的 bash 引擎后面挂 executor 实现（本地 subprocess ↔ executor 同缝切换）。
- **`nova-exec-server-client`**：executor 栈的客户端运行时（连接 + 发现 + 物化）。
  连接面：`ExecutorClient`（process/fs/pty + errors），initialize 时做 `protocolVersion` major 匹配；
  传输双形态 `WebSocketTransport` + `StdioTransport`（spawn 子进程 NDJSON，command 参数化——本地/SSH 同一实现）；
  `TransportPool` 多连接按通道路由（控制面/数据面分离，大文件流不阻塞工具调用）。
  **发现/物化面（config.py/policy.py）**：执行策略词汇（沙箱套餐/网络代理/审批档）归 executor 栈自持——读 executor 配置根（user 层 `~/.nova/executor/config.toml` TOML + project 层 `<cwd>/.nova/settings.json` 的 `executor` 段，**project 层仅在 trust 布尔为真时读**；合并语义：表深合并、列表/标量整体覆盖），物化展开成线上协议对象（`resolve_execution_policy`——套餐名永不上线）。
  harness settings 不携带执行词汇（存量 `executor.*` 键迁移属后续批次）。
  **多 executor 环境注册表（config.py/environments.py）**：`[[environments]]` 词汇 + 校验 + 默认解析链（`resolve_environment`）+ `ExecutorClient.from_environment` 构造（含 local 内建环境、"none" 禁用默认；选择/切换编排归调用方，将来归 bundle 扩展）。
  已删除其自带的 Tool/Plugin/ExecutorBackend 三件套（与 Nova 契约冲突），不要恢复。
- 鉴权：executor 只做本地回环（stdio / WS 回环承载），**无入站鉴权**；对外暴露与鉴权归上层中继层（未落地），不归 executor。
- 构建/测试：`cargo build --workspace` / `cargo test --workspace`（在 `packages/nova-exec-server` 下）。

---

## 路径约定（前后端分治）

> 全文见 `packages/nova_client/docs/frontend-backend-separation.md`。

  - 全局配置根目录默认：`~/.nova/agent`（settings/auth/trust/models/sessions/packages/logs/bin 等**后端状态**平级保留）
  - 后端散养资源：`<base>/backend/{extensions,skills,prompts,personas}`（user 级 base = `~/.nova/agent`，项目级 base = `<cwd>/.nova`）；`agents/` 两半共享平级保留（`<base>/agents`）；旧位目录在会话服务装配时自动迁移（mv 语义、幂等、新位已有内容不合并不覆盖）
  - 前端域（按宿主分级）：`~/.nova/agent/frontend/tui/`（settings.json / state/ / keybindings.json / themes/ / debug/ + 散养 `tools/`、`dialogs/`、`index.ts`——扫描能力）；项目级 `<cwd>/.nova/frontend/tui/` 同构（散养资产过 trust 门）；前端旧位（ui-settings.json/ui-state/keybindings.json/themes）由 TUI 启动时自动迁移
  - 项目级配置目录：`<cwd>/.nova`（`.nova/settings.json` 不动）
  - 会话目录：`~/.nova/agent/sessions/--<cwd>--/`
  - Project Trust 记录：`~/.nova/agent/trust.json`
