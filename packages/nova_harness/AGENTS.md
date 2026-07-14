<!-- From: /root/nova/packages/nova_harness/AGENTS.md -->
# nova_harness — Agent SDK 项目指南

> 本文件面向 AI Coding Agent 编写。如果你不了解本项目，请从这里开始阅读。

## 项目概览

`nova_harness` 是 Nova monorepo 中的高阶 Agent SDK，建立在 `nova_ai` + `nova_agent` 之上。它为 LLM 驱动的智能体提供：

- **AgentSession**：封装底层 `Agent`，提供自动重试、模型切换、会话持久化。
- **会话树管理**：支持分支（branch）、fork、导航与会话统计。
- **上下文压缩（Compaction）**：通过 LLM 生成摘要，自动或手动缩减 token 占用。
- **资源加载**：提示词模板、扩展发现加载、扩展事件总线、诊断与资源冲突检测。
- **设置持久化**：本地 JSON 存储用户设置与模型配置（支持全局/项目级作用域）。
- **工具链**：内置工具的注册与运行时白名单控制。
- **UI 抽象与模式化前端**：`UIContext` 采用能力发现模型——底层统一走 `request(method, params)`（需响应）和 `notify(method, params)`（fire-and-forget），高层 convenience methods（`select`/`confirm`/`input`/`editor`/`custom`/`notify_message`/`set_status`/`set_working_message`/`set_working_visible` 等）基于它们实现，且只使用普通 `dict` 参数，不依赖任何传输层 schema。运行模式 `ExtensionMode` 分为 `print` / `rpc` / `websocket`，直接按底层传输协议区分：`print` 使用 `NoOpUIContext` 降级；`rpc` 当前通过 JSON-RPC over stdio 实现（`modes/rpc/`，主要由 TUI 等终端前端使用）；`websocket` 未来通过 WebSocket 实现（`modes/websocket/` 占位，供浏览器/IDE 等前端使用）。UI 能力抽象接口放在 `core/types/ui/`（被 core 运行时作为依赖契约），`NoOpUIContext` 放在 `core/ui/noop.py`，具体 Pydantic schema 与传输实现放在对应模式包中。前端通过 `extension/ui/capabilities` 上报支持的能力；反向事件走 `extension/ui/event`，状态同步走 `extension/ui/state`，组件注册走 `extension/ui/register_components`。未支持的方法优雅降级。
- **Project Trust**：项目级信任门控，决定在加载 `<cwd>/.nova` 下的 settings、extensions、skills 等资源前是否信任该项目；支持 `--trust` 覆盖、扩展裁决、持久化记录、默认策略与 UI 弹窗确认。

项目语言：**Python 3.9–3.12**，注释与文档主要使用**中文**。

---

## 技术栈与构建

### 包管理器
使用 **Poetry** 管理依赖与构建。

```bash
# 安装依赖（包含 dev 依赖）
cd packages/nova_harness
poetry install

# 仅安装生产依赖
poetry install --no-dev
```

### 构建与发布
```bash
# 打包（生成 wheel / sdist）
poetry build

# 发布（如需要）
poetry publish
```

### 关键配置
- `pyproject.toml`：Poetry 配置、依赖、Black / isort 规则。
- 无 `setup.py` / `setup.cfg`，使用 `poetry-core` 作为构建后端。
- 无 CI / GitHub Actions 配置，无 `Makefile`。

---

## 代码风格

- **Formatter**：`black`（目标 Python 3.11 语法）。
- **Import 排序**：`isort`（profile = "black"）。
- **Pre-commit**：项目配置了 `pre-commit` 依赖，但当前仓库中未看到 `.pre-commit-config.yaml`，如有需要可自行添加。

手动格式化命令：

```bash
poetry run black src/
poetry run isort src/
```

### 命名与注释规范
- 类名使用 `PascalCase`，函数/变量使用 `snake_case`。
- 常量使用 `UPPER_CASE`（如 `APP_NAME = "nova"`）。
- 代码注释以**中文**为主，保持与现有代码一致。
- **数据建模**：按是否跨越 JSON/文件/RPC 边界选择技术栈。
  - **Pydantic v2 (`NovaBaseModel`)**：用于配置持久化、会话 JSONL、包 manifest、资源诊断报告、前后端 UI 契约、RPC payload 等需要 schema 校验与序列化的 JSON 边界类型。使用原生 `model_dump()` / `model_validate()` / `model_dump_json()` / `model_validate_json()`。
  - **`dataclass`**：用于运行时内部对象、事件 payload、含 `Callable`/服务实例/异常的依赖容器。避免对不可序列化对象触发 Pydantic 校验，并减少运行时开销。
  - 不教条地“优先 dataclass”或“优先 Pydantic”，决策唯一依据是类型是否跨越 JSON 边界。

### 类型系统说明

- `NovaBaseModel.model_dump()` 默认 `mode="json"`，Enum 字段会序列化为字符串。
- 枚举字段在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。
- **不要**在 `dataclass` 类型上调用 `.model_dump()` / `.model_validate()`；纯运行时对象直接通过属性访问，必要时使用 `dataclasses.asdict()` 或手动序列化。
- **不要**对运行时容器（如 `AgentSessionConfig`、`AgentSessionServices`、`ToolDefinition`）使用 Pydantic 序列化，它们可能持有服务实例、`Callable` 等不可 JSON 化的对象。

---

## 项目结构

```
nova_harness/
├── pyproject.toml              # Poetry 配置、依赖、工具设置
├── README.md                   # 面向人类开发者的简介
├── CHANGELOG.md                # 变更日志（当前为空）
├── .gitignore                  # 忽略 pycache、venv、poetry.lock、本地会话等
└── src/nova_harness/
    ├── __init__.py             # 包入口，对外暴露 sdk 与 runtime 核心符号
    ├── main.py                 # 应用主入口
    ├── cli/                    # 所有 CLI 入口与参数解析
    │   ├── __init__.py         # 公开 main（转发自 cli/main.py）
    │   ├── main.py             # nova-harness 主入口
    │   └── package.py          # nova-pkg 包管理器入口
    ├── modes/                  # 运行模式（按前端形态/传输协议划分）
    │   ├── rpc/                # JSON-RPC over stdio 实现（主要由 TUI 等终端前端使用）
    │   │   ├── cli.py          # nova-harness-rpc 入口
    │   │   ├── server.py       # JSON-RPC server 与消息总分发
    │   │   ├── methods.py      # JSON-RPC 方法实现
    │   │   ├── ui.py           # UI 原语 inbound 路由与协议助手
    │   │   ├── ui_context.py   # RpcUIContext（UIContext 的 JSON-RPC 实现）
    │   │   ├── primitives.py   # RPC 模式下 UI 原语的 Pydantic schema
    │   │   ├── types.py        # RPC/UI 原语类型 re-export
    │   │   ├── transport.py    # stdio NDJSON 传输层
    │   │   ├── protocol.py     # JSON-RPC 协议封装
    │   │   ├── events.py       # Agent 事件序列化
    │   │   ├── errors.py       # RPC 错误类型
    │   │   └── output_guard.py # 保护 stdout 不被非协议写入
    │   ├── print/              # Print 模式：非交互式命令行运行
    │   │   ├── __init__.py     # 公开 PrintRunner / run_print_mode
    │   │   ├── cli.py          # nova-harness run 子命令入口
    │   │   └── runner.py       # PrintRunner：text / json 两种输出形态
    │   └── websocket/          # WebSocket 模式占位（未来供浏览器/IDE 等前端使用）
    │       └── __init__.py     # 扩展点说明
    └── core/                   # 所有运行时实现与内部基础设施
        ├── __init__.py         # 公开运行时核心符号
        ├── sdk.py              # 对外 SDK 工厂函数
        ├── agent_session/      # AgentSession 运行时核心
        │   ├── agent.py        # AgentSession 类
        │   ├── runtime.py      # AgentSessionRuntime
        │   ├── services.py     # AgentSessionServices
        │   ├── controllers/    # 领域控制器（bash、compaction、events、model 等）
        │   └── extensions/     # 扩展系统（api、runner、context）
        ├── harness/            # 高阶 SDK 能力
        │   ├── session/        # 会话持久化与树管理
        │   ├── compaction/     # 上下文压缩与分支总结
        │   ├── system_prompt/  # 系统提示词构建
        │   ├── project_trust/  # 项目信任门控
        │   │   ├── __init__.py   # 公共 API 导出
        │   │   ├── project_trust.py# 决策逻辑
        │   │   └── trust_store.py# trust.json 持久化
        │   └── skills.py       # Skill 管理与命令展开
        ├── resources/          # 资源发现与加载
        │   ├── loader.py       # ResourceLoader 抽象基类与 DefaultResourceLoader
        │   └── loaders/        # 资源加载器（agent_config、extensions、prompt_templates、skills、tools）
        ├── package/            # Agent / tool / bundle / skill 包管理核心
        │   ├── manager.py      # PackageManager facade（安装 + 资源解析入口）
        │   ├── coordinator.py  # 资源解析协调：settings / 已安装包 / 自动安装兜底
        │   ├── installer.py    # PackageInstaller facade
        │   ├── install/        # 安装子系统
        │   │   ├── operation.py    # InstallOperation（普通/editable 安装共享流程）
        │   │   ├── lifecycle.py    # PackageLifecycle（update / uninstall）
        │   │   ├── query.py        # PackageQuery（list / info / validate）
        │   │   ├── helpers.py      # 纯 helper 函数
        │   ├── backend/        # Python 包/依赖安装后端（pip/uv/pixi）
        │   │   ├── python.py
        │   │   └── binaries.py
        │   ├── metadata/       # 包元数据读取与校验
        │   │   ├── entries.py
        │   │   ├── pyproject.py
        │   │   └── validation.py
        │   ├── resolver/       # 运行时资源路径解析
        │   │   ├── resolver.py
        │   │   ├── discovery.py
        │   │   ├── patterns.py
        │   │   └── metadata.py
        │   ├── source/         # source spec 解析与获取
        │   │   ├── spec.py
        │   │   └── fetcher.py
        │   ├── scaffold.py     # 初始化 [tool.nova] 段脚手架
        │   └── fs.py           # 文件系统辅助函数
        ├── config/             # 配置层：settings、model registry、auth storage、路径默认值
        │   ├── defaults.py
        │   ├── resolve.py
        │   ├── storage/        # 通用存储后端抽象
        │   ├── settings/       # 设置管理
        │   ├── auth/           # 鉴权 / API key 存储
        │   └── model_registry/ # 模型注册表
        ├── types/              # 统一类型层：所有跨模块/模块内数据类型与事件 payload
        │   ├── agent/            # Agent 定义相关类型
        │   │   ├── config.py     # AgentConfig / DynamicContext / ToolInfo / Section
        │   │   └── model.py      # ScopedModelConfig / ModelCycleResult
        │   ├── config/           # 鉴权、模型注册表、设置
        │   ├── session/          # 会话生命周期、条目、树、状态
        │   ├── runtime/          # 运行时执行对象（Bash、工具、运行时诊断）
        │   ├── resources/        # 资源加载相关类型
        │   ├── extensions/       # 扩展协议类型
        │   ├── events/           # 事件常量与 payload（dataclass）
        │   ├── messages.py       # 消息类型
        │   ├── compaction.py     # 上下文压缩
        │   ├── skills.py         # Skill 相关
        │   ├── project_trust.py  # Project Trust 决策
        │   ├── package_manager.py# 包管理器（PackageSource / NovaManifest / PackageManifest 等）
        │   ├── ui/                 # UI 能力抽象类型（UIContext / UIResponse）
│   ├── ui/noop.py          # 无 UI 时的空实现 NoOpUIContext
        │   └── __init__.py       # 说明文档，不做大规模顶层重导出
        └── utils/              # 通用工具
```

> 注：官方 `subagent` 工具的核心实现已随 bundle 移动到 `nova_coding_agent` 包的 `nova_coding_agent/subagent/`，不再位于 `nova_harness` 内部。
---

## 核心模块职责

### 1. `core/sdk.py` — 入口工厂
提供 `create_agent_session(options)` 异步函数，负责：
- 解析/创建 `agent_dir`（默认 `~/.nova/agent`）与 `session_dir`。
- 初始化 `SessionManager`、`SettingsManager`、`ModelRegistry`、`AuthStorage`，并封装为 `AgentSessionServices`。
- 解析初始模型：优先恢复现有会话上下文中的模型，其次 settings 默认模型，最后 fallback 到 `volcengine/deepseek-v3-2-251201`。
- 构建 `Agent` 实例（来自 `nova_agent`）；Agent 层的扩展 hook 由 `AgentSession` 在初始化时直接绑定到它自己创建的 `ExtensionRunner`。
- 将 `AgentSessionServices` 解包为扁平字段注入 `AgentSessionConfig`，创建 `AgentSession`；`AgentSessionRuntime` 仍持有 `AgentSessionServices`。
- 调用 `session.bind_extensions()` 触发扩展 `session_start` 生命周期。
- 包装为 `AgentSessionRuntime` 返回。

### 2. `core/agent_session/services.py` — AgentSessionServices
**cwd 绑定的运行时服务容器**（`@dataclass`），只有一个职责：把创建 session 所需的服务实例集中到一起，供 `AgentSessionRuntime` 持有和复用。它不是 JSON 边界类型，因此不序列化。

包含：
- `session_manager`、`settings_manager`
- `model_registry`、`resource_loader`、`system_prompt_manager`
- `cwd`、`agent_dir`、`auth_storage`、`diagnostics`

`AgentSession` 持有从 services 解包出来的扁平依赖；`ExtensionRunner` 直接接收这些扁平依赖，不再要求 `AgentSession` 保存整个 services 对象。

### 3. `core/types/session/config.py` — AgentSessionConfig
与 TypeScript 参考实现对齐的**运行时扁平配置**（`@dataclass`），不含可序列化 JSON 的边界类型：
- `agent`、`session_manager`、`settings_manager`
- `cwd`、`system_prompt_manager`、`resource_loader`、`model_registry`
- `scoped_models`、`initial_active_tool_names`、`base_tools_override`
- `extension_runner_ref`：可选的可变引用，AgentSession 创建 runner 后写回，供外部获取当前 runner
- `session_start_event`：会话启动事件，由 `AgentSession.bind_extensions()` 发出

> `agent_dir`、`auth_storage` 等服务性字段保留在 `AgentSessionServices` 中，不进入此 config。

`AgentSessionRuntime` 在创建新 session 时，把 `AgentSessionServices` 解包成此 config。

### 4. `core/agent_session/agent.py` — AgentSession
专注于**单一会话**内的运行时逻辑：
- 直接持有 `_session_manager`、`_settings_manager`、`_model_registry`、`_resource_loader`、`_system_prompt_manager`、`_cwd`。
- 在初始化时从 `ResourceLoader` 创建 `ExtensionRunner`，并把 Agent 层的扩展 hook（`before_tool_call` / `after_tool_call` / `transform_context` / `on_payload` / `on_response` / `prepare_next_turn` / `should_stop_after_turn`）直接绑定到 runner。
- `bind_extensions()`：与 TS `AgentSession.bindExtensions()` 对齐，绑定扩展上下文、发出 `session_start`，并调用 `resources_discover` 把扩展贡献的 skill/prompt/theme 路径合并到 `ResourceLoader`。
- `execute_command()` / `prompt()` 中的 slash command 解析：支持扩展通过 `/command args` 执行自定义命令；`get_slash_commands()` 同时暴露扩展命令与 `skill:name` 命令。
- `/skill:name` 命令展开：`prompt()`、`steer()`、`follow_up()` 在启用模板展开时，通过 `core.harness.skills.expand_skill_command()` 把 `/skill:name args` 展开为 XML skill block。
- `reload_extensions()`：关闭当前 runner 并重新加载扩展，保留 flag 值。
- 系统提示词重建委托给 `SystemPromptManager`：在初始化、`change_agent()`、`set_active_tools_by_name()`、`reload()` 时触发 `_rebuild_system_prompt()`。
- `prompt()` 每轮允许扩展通过 `before_agent_start` 事件临时覆盖系统提示词（仅影响当轮）。
- 事件订阅/发布（`subscribe` / `_emit`）。
- 工具注册与白名单（`set_active_tools_by_name`）。
- 消息处理：`prompt`、`steer`、`follow_up`、`send_custom_message`。
- 模型管理：`set_model`、`cycle_model`。
- 思考级别：`set_thinking_level`、`cycle_thinking_level`。
- 自动压缩：`_check_compaction`、`_run_auto_compaction`。
- 自动重试：`_handle_retryable_error`。

**不包含**会话切换、fork、导航等生命周期方法。

### 5. `core/agent_session/runtime.py` — AgentSessionRuntime
管理 AgentSession 的**生命周期与替换**：
- 持有 `AgentSessionServices` 和创建 session 的 factory。
- `new_session`：创建新会话并替换当前 session。
- `switch_session`：切换到指定会话文件。
- `fork`：在指定条目处 fork。
- `navigate_tree`：在会话树中导航，可选生成分支摘要。
- `dispose`：释放当前 session 资源。

切换时会先 teardown 旧 session（abort + dispose），再由 factory 通过 `AgentSessionServices` 创建新 session。

### 6. `core/harness/session/manager.py` — SessionManager
- 会话持久化为 **JSONL** 文件，存储在 `~/.nova/agent/sessions/--<cwd>--/` 下。
- 支持分支（`branch`、`branch_with_summary`）、fork（`create_branched_session`）。
- 条目类型：`message`、`thinking_level_change`、`model_change`、`active_tools_change`、`compaction`、`branch_summary`、`leaf`、`label`、`custom`、`custom_message`、`session_info`。
- 序列化保持 **Python 惯用的 snake_case**（如 `parent_id`、`first_kept_entry_id`、`active_tool_names`、`target_id`），文件版本为 `3`，ID 使用 generate_session_id 前 8 位（与 TS 行为一致但字段命名符合 Python 规范）。
- `leaf` entry 持久化当前 leaf 指针；`active_tools_change` 持久化激活工具列表并在 `SessionContext` 中透出。

### 7. `core/harness/compaction/compaction.py` — 上下文压缩
- 基于 token 估算（字符数 / 4 的启发式算法）与模型 `context_window` 判断是否触发压缩。
- 使用 LLM 生成结构化摘要（`_SUMMARIZATION_PROMPT`），支持增量更新（`_UPDATE_SUMMARIZATION_PROMPT`）。
- 提取文件操作（read/modified）并附在摘要中。

### 8. `core/harness/system_prompt/` + `core/resources/loaders/` — 系统提示词
- `core/resources/loaders/agent_config.py` 负责 Agent 配置文件读取（`agent.yaml`、`description.md`、`sections/`），并按 Nova 资源优先级（全局 -> 项目级）加载 Agent 配置。
- `core/resources/loader.py` 只负责调度：在 `reload()` 中依次调用 `core/resources/loaders` 下的 `agent_config`、`prompt_templates`、`extensions`、`skills`，自身不再包含具体加载逻辑。
- `SystemPromptManager` 运行时维护当前选中的 agent、默认激活工具、扩展工具注入与激活工具白名单。
- `SystemPromptBuilder`（`core/harness/system_prompt/builder.py`）把 Agent 配置与工具白名单渲染成最终系统提示词字符串；支持通过 `append_system_prompt` 追加额外内容。
- 当当前激活工具包含 `read` 时，`SystemPromptManager` 会把可用 skill 列表（`disable_model_invocation=False`）以 XML 格式追加到系统提示词末尾。
- 系统提示词在以下场景重建：会话初始化、切换 agent、改变激活工具集、`AgentSession.reload()`。

> **`core/resources/loaders/` 的定位**：它是各业务模块 loader 的 resource 级调用层。业务模块（如 `core/harness/system_prompt`、`core/extensions`）自己实现文件格式解析，`core/resources/loaders/` 负责按 Nova 的全局/项目优先级、去重、扩展上下文组装等规则调用它们，并把结果交给 `DefaultResourceLoader`。

### 10. `core/types/ui/` + `core/ui/noop.py` + `modes/` — UI 能力抽象与模式化前端
- `core/types/ui/primitives.py` 定义 `UIResponse` 与 `ExtensionMode`；`core/types/ui/context.py` 定义 `UIContext` 抽象接口（含 convenience methods）。所有 UI 交互统一走 `request(method, params)`（需响应）和 `notify(method, params)`（fire-and-forget）；convenience methods（`select`/`confirm`/`input`/`editor`/`custom`/`notify_message`/`set_status`/...）基于二者实现，且只使用普通 `dict` 参数，**不依赖**任何传输层 Pydantic schema。由于 `core/sdk.py`、`core/types/session/config.py`、`core/extensions/`、`core/harness/project_trust/` 等大量 core 模块都依赖 `UIContext`/`UIResponse` 作为契约，抽象接口放在统一类型层；`NoOpUIContext` 空实现放在 `core/ui/noop.py`。
- `ExtensionMode` 直接按底层传输协议区分：
  - `print`：无交互式 UI，使用 `core.ui.noop.NoOpUIContext`。
  - `rpc`：JSON-RPC over stdio，当前由 TUI 等终端前端使用。
  - `websocket`：WebSocket 连接，未来由浏览器/IDE 等前端使用（`modes/websocket/` 占位）。
- `modes/rpc/` 是 **rpc 模式** 的传输实现（TUI 等终端前端使用）：
  - `modes/rpc/primitives.py` 定义 JSON-RPC over stdio 所需的 Pydantic params/response schema 与标准 method 集合，参考 TypeScript `coding-agent` 的 `ExtensionUIContext` 接口，包括对话框、自定义组件、状态/工作指示、布局/widget、编辑器控制、主题、工具输出、终端输入订阅等。
  - `modes/rpc/ui_context.py` 提供 `RpcUIContext`：把 `UIContext` 的 request/notify 桥接到 JSON-RPC。
  - `modes/rpc/ui.py` 提供 `UIRouter`：在 RPC server 中集中处理所有来自前端的 UI inbound 方法（`extension/ui/response`、`extension/ui/capabilities`、`extension/ui/register_components`、`extension/ui/event`、`extension/ui/state`），使 `server.py` 只做通用消息分发。
  - `modes/rpc/types.py` re-export `modes/rpc/primitives.py` 中的类型，供 RPC 层内部统一引用。
- `modes/websocket/` 是 **websocket 模式** 的占位包：未来在此实现 `WebSocketUIContext`（基于 WebSocket 的 `UIContext`）和 `WebSocketServer`。WebSocket 前端可与 RPC 前端共享部分 UI method 语义，但 schema 和传输细节由本包自行定义。
- 标准 request/response 原语在 JSON-RPC 下使用独立 method（如 `extension/ui/select`、`extension/ui/editor`），response 统一走 `extension/ui/response`；notify 原语使用独立 notification（如 `extension/ui/notify`、`extension/ui/setStatus`）；自定义原语回退到 `extension/ui/request`。同时支持反向事件 `extension/ui/event`、状态同步 `extension/ui/state` 和组件注册 `extension/ui/register_components`。前端通过 `extension/ui/capabilities` 上报支持的能力，未支持方法优雅降级。
### 11. `core/extensions/` / `core/types/extensions/` / `core/resources/loaders/extensions.py` — 扩展系统
- 扩展系统按 TypeScript `coding-agent/src/core/extensions/` 重新设计，不保留旧版兼容性：
  - `core/types/extensions/`：扩展类型统一入口（`Extension`、`ExtensionRuntime`、`ExtensionAPI`、`ExtensionCommand`、`ExtensionFlag`、`ExtensionShortcut`、`LoadedExtensionsResult`、`MessageRenderer`、`SourceInfo` 等），按子主题拆分到多个模块并通过 `__init__.py` 统一导出。
  - `core/extensions/api.py`：`NovaExtensionAPI` 实现扩展工厂接收的 API（`on` / `registerCommand` / `registerShortcut` / `registerFlag` / `registerMessageRenderer` / `getFlag` / action 委托 / `registerProvider` 等）。
  - `core/extensions/loader.py`：`ExtensionLoader` 与 `load_extensions()` 负责扩展发现、模块加载与工厂执行。
  - `core/extensions/runner.py`：`ExtensionRunner` 负责扩展生命周期、事件分发、上下文创建、action 绑定与 provider 注册队列刷新。
- `ResourceLoader`（具体为 `DefaultResourceLoader`）创建并持有扩展间事件总线 `event_bus`，所有扩展共享同一个 bus；`LoadedExtensionsResult` 返回 `extensions`、`errors`、`runtime`，调用方通过 `ResourceLoader.event_bus` 获取总线。
- `AgentSession` 在初始化时从 `ResourceLoader.get_extensions()` 读取扩展和 `runtime`，从 `ResourceLoader.event_bus` 读取事件总线，传给 `ExtensionRunner`；`ExtensionRunner` 不再自己创建默认 event bus。
- `ExtensionRunner` 提供 `emit_error`、`has_handlers`、`get_command`、`get_registered_commands`、`get_flags`、`get_flag_values`、`set_flag_value`、`get_shortcuts`、`get_shortcut_diagnostics`、`emit_resources_discover` 等运行时 API。
- `get_shortcuts(resolved_keybindings=None)` 保留 keybinding 冲突检测入口；当前实现记录扩展快捷键之间的冲突诊断。
- `NovaExtensionAPI` 提供 `exec(command, args, options?)` 执行 shell 命令并返回 `ExecResult`；`set_model(model)` 返回 `bool`（缺少 API key 时返回 `False`）；`get_active_tools()` / `get_all_tools()` / `set_active_tools()` 用于查询与切换工具激活状态；`get_commands()` 返回 `SlashCommandInfo[]`。
- `NovaExtensionAPI.register_command` / `register_shortcut` / `register_flag` 采用 TS 风格的 `(name/key, options)` 调用。
- `session_start` 由 `AgentSession.bind_extensions()` 发出（与 TS 参考实现一致），随后触发 `resources_discover`。
- 支持事件：`session_start` / `session_shutdown` / `session_before_compact` / `session_compact` / `session_before_tree` / `session_tree`，以及 `prepare_next_turn` / `should_stop_after_turn`，`AgentEvent` 桥接、`tool_call` / `tool_result` / `context` / `input` / `before_provider_request` / `after_provider_response` 等 hook。
- 扩展可通过 `NovaExtensionAPI`（`nova`）注册 provider、命令、快捷键、flag、消息渲染器；**工具统一走 package tool 路径，不再通过扩展注册**。
- 扩展目录：项目级 `<cwd>/.nova/extensions/`、全局 `~/.nova/agent/extensions/`，以及 `Settings.extensions` 显式配置的路径。
- `ExtensionContext` 暴露 `ui`（`UIContext`）、`mode`（`"print"` / `"rpc"` / `"websocket"`，由 `ExtensionRunner.mode` 决定）以及 `is_project_trusted()` 等上下文 action，扩展可据此决定是否执行需要用户交互或高权限的操作。
- `ExtensionRunner` 在 `create_context()` 时注入当前 `ui_context` 与 runtime 状态；`project_trust` 事件允许扩展参与信任裁决。
- `DefaultResourceLoader` 已深度接入 `PackageManager`：`AgentSessionServices.create()` 会默认构造 `PackageManager` 并注入到 `DefaultResourceLoaderOptions.package_manager`。`PackageManager` 是统一 facade，内部聚合 `PackageInstaller` 与 `PackageResolver`；传入后，`reload()` 会把解析结果作为扩展 / skill / prompt / theme / tool / agent 的唯一来源，关闭子加载器的默认目录扫描，避免重复发现；`extend_resources()` 贡献的临时路径仍会与解析结果合并，并统一去重。`DefaultResourceLoaderOptions.install_missing_packages` 控制 `PackageManager.resolve_resources()` 发现 settings 中配置的 package 缺失时是否自动调用 installer 安装，默认在 `AgentSessionServices.create()` 中开启。

> **与 TS 的差异**：`message_renderers` / `shortcuts` / `user_bash` 尚未被 UI 层消费；`themes` 加载为占位；工具注册未实现 `allowedToolNames` / `excludedToolNames`、prompt snippets/guidelines 等细粒度控制。`UIContext` 保留了与 TS 类似的高层 convenience methods，但底层统一为 `request`/`notify` + 能力发现模型，且 core 层不依赖任何具体传输协议的原语 schema；RPC 模式的原语 schema 集中在 `modes/rpc/primitives.py`，WebSocket 模式未来在 `modes/websocket/` 中定义。

### 12. `core/config/model_registry/registry.py` — ModelRegistry
- 加载内置模型（来自 `nova_ai`）与自定义模型（`models.json`）。
- 支持 provider 级别覆盖（`base_url`、`headers`、`api_key`）与 per-model 覆盖。
- 动态 provider 注册（`register_provider` / `unregister_provider`）。

### 13. `core/config/settings/manager.py` — SettingsManager
- 双层设置：**全局**（`~/.nova/agent/settings.json`）与 **项目级**（`<cwd>/.nova/settings.json`）。
- 字段级 dirty tracking，延迟写入（`flush()`）。
- 涵盖 retry、compaction、terminal、image 等设置。

---

## 测试

当前 `tests/` 目录已包含覆盖以下模块的测试：

- `tests/core/agent_session/`：AgentSession 生命周期、事件、工具、扩展、消息队列。
- `tests/core/package/`：包管理器安装/卸载/列表/校验，以及 agent/tool/skill/extension/bundle 五种类型。
- `tests/core/resources/`：资源加载器（agent 配置、skills、prompt templates、tools、loader）。
- `tests/core/harness/`：system prompt、compaction、session 管理。
- `tests/core/types_tests/`：Pydantic / dataclass 类型序列化与构造。
- `tests/modes/rpc/`：RPC 模式方法。
- `tests/core/subagent/`：官方 subagent 工具（已转为 package tool）。
- `tests/core/harness/project_trust/`：Project Trust 决策与存储。
- `tests/modes/rpc/test_output_guard.py`：stdout 保护。
- `tests/modes/rpc/test_ui_context.py`：RPC UI 桥接。
- `tests/core/test_main.py`：CLI 主入口。
- `test_harness_smoke.py`：基础冒烟测试，含真实 LLM 集成测试（需 `VOLCENGINE_API_KEY`）。

运行方式：

```bash
cd packages/nova_harness
poetry run pytest

# 仅运行真实 API 集成测试
poetry run pytest -m integration

# 生成覆盖率报告
poetry run pytest --cov=nova_harness --cov-report=html
```

---

## 安全配置与敏感信息

### API Key 存储
- 用户认证存储在 `~/.nova/agent/auth.json`，由 `AuthStorage` 管理。
- `models.json` 中可配置 `api_key`，支持环境变量引用（如 `"${ENV_VAR}"`）。
- 解析逻辑在 `core/config/resolve.py` 中实现。

### 会话文件
- 会话历史以 **JSONL** 明文存储，可能包含敏感代码片段或输出。
- 存储路径：`~/.nova/agent/sessions/--<cwd>--/`。
- `.gitignore` 已忽略 `sessions/` 与 `*.session`。

### 文件操作安全
- Agent 配置加载器读取 `sections/` 章节时目前未校验路径；生产环境应补充对 `..` 与绝对路径的校验。

---

## Agent 配置与 Subagent

### Agent 配置 frontmatter

`description.md` 支持可选 YAML frontmatter，用于声明 agent 运行时需要的能力：

```markdown
---
model: claude-haiku-4-5
subagents: []
tools: [read, grep, find, ls, bash]
skills: []
extensions: []
---

You are a scout...
```

frontmatter 中可覆盖的字段与 `agent.yaml` 含义相同：`model`、`tools`、`subagents`、`skills`、`extensions`。其中 `subagents` 与 `tools` 中的 `subagent` 工具需配合使用：前者限定可委托的目标 agent 白名单，后者启用委托能力。

Agent 名称取自目录名；描述取自 frontmatter 之后的 Markdown 正文。`name` 和 `description` 写在 frontmatter 中不会被读取。

### Agent 单一配置文件

Agent 目录下应放置统一的 `agent.yaml` 配置文件，集中声明所有资源白名单：

```
agents/<agent>/
├── description.md
├── agent.yaml           # 元数据 + 资源白名单
└── sections/            # 系统提示词片段
```

`agent.yaml` 示例：

```yaml
# 元数据
name: coding_agent
version: "1.0.0"
description: Nova coding agent with local file system tools and subagent delegation
author: nova

# 默认模型，可被 description.md frontmatter 中的 model 覆盖
model: openai/gpt-4o

# 工具白名单：只有列出的工具才会被注册到当前 agent
# 每项可以是字符串（tool name），也可以是带 description 的对象
tools:
  - read
  - write
  - edit
  - bash
  - grep
  - find
  - ls
  # 启用 subagent 工具后，当前 agent 才能委托任务给其他 agent
  - subagent

# 子 agent 白名单：声明 agents/ 目录下哪些 agent 可作为子智能体被调用
# 只有同时启用 subagent 工具并在这里列出名称，委托功能才真正可用
subagents:
  - scout
  - planner
  - reviewer

# Skill 白名单：允许注入到系统提示词的 skill 名称列表
skills:
  - python_best_practices
  - git_workflows

# Extension 白名单：允许挂载到当前会话的 extension 名称列表
extensions:
  - session_commands
```

字段说明：

- `name` / `version` / `description` / `author`：元数据，仅用于展示和包管理。
- `model`：该 agent 倾向使用的模型标识（可选）。当前会被记录到 `AgentConfig.model`，但**不会**自动作为会话默认模型；会话模型仍由调用方、设置或 SDK 默认策略决定。
- `tools`：工具白名单（可选），与 `description.md` frontmatter 中的 `tools` 合并，按 name 去重。
- `subagents`：子 agent 白名单（可选），与 `description.md` frontmatter 中的 `subagents` 合并。注意：必须同时在 `tools` 中启用 `subagent` 工具，委托能力才会生效。
- `skills` / `extensions`：skill 和 extension 白名单（可选），与 frontmatter 同名字段合并去重。

- `description.md` 的 YAML frontmatter 仍可作为**增量覆盖**：frontmatter 中的 `tools` / `skills` / `extensions` / `subagents` / `model` 会覆盖 `agent.yaml` 中的同名字段，用于项目级自定义。
- `skills` / `extensions` / `subagents` 未声明时默认为空列表。`subagents` 为空表示未声明任何可用子 agent。
- `tools` 未声明时可用工具为空。

解析逻辑位于 `core/resources/loaders/agent_config.py`；运行时过滤位于：

- skills：`core/harness/system_prompt/manager.py`（系统提示词注入）和 `core/agent_session/agent.py`（`/skill:name` 命令展开）。
- extensions：`core/agent_session/agent.py` 初始化 `ExtensionRunner` 时按白名单过滤。
- tools：仍由 `core/agent_session/controllers/tools.py` 按 `SystemPromptManager` 的激活工具集注册。

### Bundle 与 Python package

bundle 可以只包含 tools、agents、skills、extensions 的任意组合。Bundle 通过根目录的 `pyproject.toml` 声明 Nova 资源：

```toml
[tool.poetry]
name = "nova-coding-agent"
version = "1.0.0"
description = "..."
authors = ["nova"]

[tool.nova]
agents = ["./agents/coding_agent"]
tools = ["./tools/bash", "./tools/read", "./tools/write"]
extensions = ["./extensions/session_commands"]
auto_install_dependencies = true
```

- `name` / `version` / `description` / `authors` 复用 Poetry 标准段；`[tool.nova]` 段声明资源路径与包行为。
- 只有当包声明了 `tools` 时，`nova-pkg` 才会安装包自身的 Python package。
- 该 Python package 仅供 tools 使用（例如 `nova_coding_agent.tools_common`）。
- agents、skills、extensions 必须是自包含的，不得依赖包的 Python package。
- `nova-pkg init` 会根据当前目录结构自动扫描并生成 `[tool.nova]` 段。

### 官方 Subagent 工具

`nova_coding_agent` 的 `tools/subagent/` 是 Nova 官方 subagent tool，让任意 agent 可以调用 `subagent` 工具委托任务给其他已安装 agent。核心实现位于 `nova_coding_agent` 包的 `nova_coding_agent/subagent/`，由 tool executor 调用。

安装方式：

`nova-coding-agent` 包已在 `pyproject.toml` 的 `[tool.nova]` 中声明 `tools` 包含 `./tools/subagent`，随 bundle 安装后即可使用。

在 agent 的 `agent.yaml` 中同时满足以下两点即可启用：

1. 在 `tools` 中加入 `subagent`（启用委托工具）：

```yaml
tools:
  - read
  - subagent
```

2. 在 `subagents` 中列出允许作为子智能体的 agent 名称（白名单）：

```yaml
subagents:
  - scout
  - planner
```

支持三种模式：

- **single**：`{ agent, task, cwd? }`
- **parallel**：`{ tasks: [...] }`，最多 8 个任务，并发 4 个
- **chain**：`{ chain: [...] }`，支持 `{previous}` 占位符传递上一步输出

并发与进程模型：

- 所有 subagent 调用共享全局并发信号量，默认同时最多运行 4 个；可通过环境变量 `NOVA_SUBAGENT_MAX_CONCURRENCY` 调整。
- 子 agent 运行通过 `nova-harness run` 子进程实现。

---

## 常见开发任务

### 新增或修改类型
1. **权威位置**：`core/types/` 下的对应分组。会话相关类型在 `core/types/session/`；运行时类型在 `core/types/runtime/`；Agent 配置在 `core/types/agent/`；资源类型在 `core/types/resources/`；扩展协议在 `core/types/extensions/`；事件在 `core/types/events/`。
2. 内部模块和测试都应从 `core/types/` 下的对应分组导入（如 `core.types.session`、`core.types.runtime`、`core.types.agent`、`core.types.config`、`core.types.resources`、`core.types.extensions`、`core.types.project_trust`），不要再走已删除的 `<module>/types.py`、`<module>/options.py` 或旧顶层文件。

### 添加新工具
1. 在 `~/.nova/agent/tools/`（全局）或 `<cwd>/.nova/tools/`（项目级）下创建新的 tool 目录，包含 `schema.json`（schema）和 `executor.py`（实现 `ToolExecutor` 类）。
2. 工具加载统一由 `core/resources/loaders/tools.py` 的 `ToolLoader` 完成；`DefaultResourceLoader.get_tools()` 在 `reload()` 时自动加载全局与项目级工具。
3. `core/agent_session/controllers/tools.py` 只保留运行时执行包装 `DynamicTool`，不再负责扫描加载。
4. 如需在 Agent 中默认激活，在对应 Agent 配置的 `agent.yaml` 的 `tools` 中加入 tool name。

### 修改会话条目类型
1. 更新 `core/types/session/entries.py` 中的 Pydantic 模型。
2. 在 `core/harness/session/manager.py` 中添加 `append_xxx()` 方法。
3. 在 `core/harness/session/utils.py` 的解析逻辑中处理新类型。
4. 在 `core/utils/messages.py` 的 `convert_to_llm()` 中映射到 LLM 消息。

### 调整压缩策略
- 修改 `core/harness/compaction/compaction.py` 中的 `estimate_tokens()` 启发式算法。
- 或调整 `core/config/settings/manager.py` 中的默认 `DEFAULT_COMPACTION_RESERVE_TOKENS` / `DEFAULT_COMPACTION_KEEP_RECENT_TOKENS`。

### 修改系统提示词结构
- 编辑 `core/harness/system_prompt/builder.py` 中的 `compose_system_prompt()` 与 `render_xxx()` 函数。
- 或修改 `core/resources/loaders/agent_config.py` 加载的文件命名规则。

---

## 依赖注意事项

- **`nova-ai`** 与 **`nova-agent`** 为本地路径依赖（monorepo 内其他包），不在 PyPI 上发布。
- `pydantic` 用于数据模型的序列化/反序列化，是类型系统的核心。

---

## 版本与作者

- 当前版本：`0.1.0`（Alpha）
- License：MIT
- 作者：Liujinming
