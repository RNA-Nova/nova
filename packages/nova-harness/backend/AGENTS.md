<!-- From: /root/nova/packages/nova-harness/backend/AGENTS.md -->
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
- **UI 反向原语**：`UIContext` 是**泛型 transport（零词汇）**——只定义 `capabilities` / `has_capability` / `request(method, params)`（需响应）/ `notify(method, params)`（fire-and-forget），所有 method 均为自由字符串，harness 不持有任何交互词汇（`STANDARD_UI_METHODS`、便捷方法、params schema 全部移出）。**词汇定义权归包**：官方 bundle 的 `nova_coding_agent.ui_primitives` 定义基线四件套（select/confirm/input/notify）并提供糖库（`select()`/`confirm()`/`input()`/`notify_message()`），第三方包可自定义原语经同一通道（设计见 `nova-client/docs/ui-primitives.md`）。无 UI 的运行模式（print/headless）使用 `NoOpUIContext` 降级；有 UI 的模式通过 JSON-RPC over stdio 实现（`modes/rpc/`，由终端/Web 前端使用）。抽象接口统一在 `core/types/ui/context.py`（唯一 ABC），`NoOpUIContext` 在 `core/types/ui/noop.py`，`UIResponse` 在 `core/types/ui/primitives.py`。正向 UI 操作（组件/状态栏/编辑器/主题）归 Node 层 UI 管线（架构 2.0，见 `examples/nova_architecture_2.0.md`）。
- **Project Trust**：项目级信任门控，决定在加载 `<cwd>/.nova` 下的 settings、extensions、skills 等资源前是否信任该项目；支持 `--trust` 覆盖、扩展裁决、持久化记录、默认策略与 UI 弹窗确认。**trust 只存在于运行时**（会话启动决议 + `trust.json` 持久化 + resolver 读取门控）；包管理（`nova-pkg`）不介入信任决策——装/卸包是用户的主动行为，写操作不做 trust 检查。

项目语言：**Python 3.9–3.12**，注释与文档主要使用**中文**。

---

## 技术栈与构建

### 包管理器
使用 **Poetry** 管理依赖与构建。

```bash
# 安装依赖（包含 dev 依赖）
cd packages/nova-harness/backend
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
- **数据建模**：遵循根 `AGENTS.md` 的"数据建模"决策顺序（可变性 → 序列化 → 校验价值 → 禁用项），不为"统一"全用一种。本包典型归类：
  - **Pydantic v2（`NovaBaseModel`）**：需要跨进程或持久化的类型——配置持久化、会话 JSONL、包 manifest、资源诊断报告、前后端 UI 契约、RPC payload。使用原生 `model_dump()` / `model_validate()`。
  - **`dataclass`**：运行时内部对象、事件 payload、含 `Callable`/服务实例/异常的依赖容器（如 `AgentSessionConfig`、`AgentSessionServices`、`ToolDefinition`），构造零校验开销。

### 类型系统说明

- `NovaBaseModel.model_dump()` 默认 `mode="json"`，Enum 字段会序列化为字符串。
- 枚举字段在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。
- **不要**在 `dataclass` 类型上调用 `.model_dump()` / `.model_validate()`；纯运行时对象直接通过属性访问，必要时使用 `dataclasses.asdict()` 或手动序列化。

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
    │   ├── rpc/                # JSON-RPC 服务器运行模式（stdio / WebSocket 双传输）
    │   │   └── cli.py          # nova-harness-rpc 入口（--listen stdio://|ws://，
    │   │                       #   WS 鉴权 token 供给 + acceptor 装配）
    │   │                       # （server/连接层/方法表/事件广播在 server/；
    │   │                       #   OutputGuard 在 core/utils/）
    │   └── print/              # Print 模式：非交互式命令行运行
    │       ├── __init__.py     # 公开 PrintRunner / run_print_mode
    │       ├── cli.py          # nova-harness run 子命令入口
    │       └── runner.py       # PrintRunner：text / json 两种输出形态
    │                         # （WS 接入已翻案归 Python server，见 examples/nova_architecture_2.0.md 文首修订）
    ├── server/               # 接入层（与 core/ 平级——接入层不是业务核心；原 rpc/ 改名收录）
    │   ├── server.py         # RpcServer：连接注册表 + 事件广播（initialize 门 + seq/ts/sessionId 锚点）+ 归约器挂摘
    │   ├── connection.py     # Connection 一等公民（状态机/能力集/在飞表/有界出站队列+独立写泵）+ ConnectionRegistry
    │   ├── ui_context.py     # RoutingUIContext（反向原语按连接寻址 + 作用域仲裁）
    │   ├── types/            # 线上词汇：items.py（NovaItem 基类 + 框架变体 + NovaWireItem）+ notifications.py（item 三帧）
    │   ├── reduction/        # 归约：mapping.py（纯映射）+ orchestrator.py（SessionReducer 在飞状态机）
    │   ├── protocol/         # JSON-RPC 消息模型 + 方法路由 + schema 导出 + methods/ 方法表（8 域 76 方法）
    │   └── transport/        # Transport 抽象 + stdio/websocket/memory（WS 含 acceptor + 鉴权三守则）
    ├── package/              # 包管理（与 core/ 平级——生态管理域）
    │   ├── manager.py      # PackageManager facade：协调安装世界与运行时世界
    │   ├── install/        # 安装世界（写）
    │   │   ├── installer.py    # PackageInstaller：单 scope 安装/卸载/更新/列表
    │   │   ├── store.py        # 安装路径计算与已安装包推导（副本扫描，无元数据文件）
    │   │   ├── updates.py      # git 源更新可用性检查
    │   │   └── python_backend.py # Python 依赖/包安装后端（uv 优先，pip 兜底）
    │   ├── resolve/        # 运行时世界（读）
    │   │   ├── resolver.py     # PackageResolver：三来源解析 + 优先级裁决
    │   │   └── discovery.py    # 资源自动发现与 override 模式匹配（!/+/-）
    │   ├── source/         # source 领域（两个世界共享）
    │   │   ├── _semver.py      # npm semver 子集：版本解析/比较、range 匹配（^/~、x-range、比较器集、||、hyphen；纯 Python）
    │   │   ├── spec.py         # source spec 解析、package identity、跨 scope 去重
    │   │   └── resolver.py     # SourceResolver：source → 本地目录（git clone/pull、npm registry 下载）
    │   ├── manifest.py     # pyproject.toml 读取（Poetry / PEP 621 / [tool.nova]）
    │   ├── validation.py   # agent/tool/skill/extension 目录合法性判定
    │   ├── scaffold.py     # nova-pkg init：生成 [tool.nova] 段
    │   ├── binaries/       # 托管二进制注册表（registry.json + 三级解析）
    │   └── utils.py        # 离线模式、文件系统操作、ignore 规则
    └── core/                   # 业务核心（会话运行时与能力域）
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
        │   ├── agents/         # AgentManager：agents 注册表视图 + 当前角色旋钮 + yaml 写回
        │   ├── persona/        # PersonaManager：persona 装配 + override 旋钮
        │   ├── project_trust/  # 项目信任门控
        │   │   ├── __init__.py   # 公共 API 导出
        │   │   ├── project_trust.py# 决策逻辑
        │   │   └── trust_store.py# trust.json 持久化
        │   └── skills.py       # Skill 管理与命令展开
        ├── resources/          # 资源发现与加载
        │   ├── loader.py       # ResourceLoader 抽象基类与 DefaultResourceLoader
        │   └── loaders/        # 资源加载器（agent_config、extensions、prompt_templates、skills、personas、tools）
        ├── config/             # 配置层：settings、auth storage、路径默认值、目录布局迁移（migration.py——前后端分治 §9）
        │   ├── defaults.py
        │   ├── resolve.py
        │   ├── storage/        # 通用存储后端抽象
        │   ├── settings/       # 设置管理
        │   └── auth/           # 鉴权 / API key 存储
        ├── model/              # 模型域：注册表运行时、模型解析、provider attribution
        │   ├── store.py        # FileModelsStore（models-store.json 缓存）
        │   ├── composer.py     # provider 三层合成（内置 → models.json → 扩展注册）
        │   ├── runtime.py      # ModelRuntime：模型与鉴权的运行时
        │   ├── helpers.py      # 合成辅助
        │   ├── resolver.py     # 模型选择 / scope / thinking level 解析
        │   └── attribution.py  # provider attribution 头
        ├── types/              # 统一类型层：所有跨模块/模块内数据类型与事件 payload
        │   ├── resources/        # 资源类型（agents / skills / prompts / tools / context files / 诊断）
        │   ├── session/          # 会话生命周期、条目、树、状态、模型配置、运行时诊断
        │   ├── config/           # 设置
        │   ├── extensions/       # 扩展协议类型（含 process.py spawn hook 契约）
        │   ├── events/           # 事件常量与 payload（dataclass）
        │   ├── compaction/       # 上下文压缩
        │   ├── package/          # 包管理域类型（PackageSource / NovaManifest / PackageManifest 等）
        │   ├── ui/               # UI 能力抽象（UIContext / UIResponse / noop.py 空实现 NoOpUIContext）
        │   ├── model.py          # 模型注册表运行时类型
        │   ├── messages.py       # 消息类型
        │   ├── project_trust.py  # Project Trust 决策
        │   └── __init__.py       # 说明文档，不做大规模顶层重导出
        └── utils/              # 通用工具（遥测、HTTP 空闲超时、二进制解析、子进程等）
```

> 注：官方 `subagent` 工具的核心实现已随 bundle 移动到 `nova_coding_agent` 包的 `nova_coding_agent/subagent/`，不再位于 `nova_harness` 内部。
---

## 核心模块职责

### 1. `core/sdk.py` — 入口工厂
提供 `create_agent_session(options)` 异步函数，负责：
- 解析/创建 `agent_dir`（默认 `~/.nova/agent`）与 `session_dir`。
- 初始化 `SessionManager`、`SettingsManager`、`ModelRuntime`、`AuthStorage`，并封装为 `AgentSessionServices`。
- 解析初始模型：优先恢复现有会话上下文中的模型，其次 settings 默认模型，最后 fallback 到 `volcengine/deepseek-v3-2-251201`。
- 构建 `Agent` 实例（来自 `nova_agent`）；Agent 层的扩展 hook 由 `AgentSession` 在初始化时直接绑定到它自己创建的 `ExtensionRunner`。
- 将 `AgentSessionServices` 解包为扁平字段注入 `AgentSessionConfig`，创建 `AgentSession`；`AgentSessionRuntime` 仍持有 `AgentSessionServices`。
- 调用 `session.bind_extensions()` 触发扩展 `session_start` 生命周期。
- 包装为 `AgentSessionRuntime` 返回。

### 2. `core/agent_session/services.py` — AgentSessionServices
**cwd 绑定的运行时服务容器**（`@dataclass`），只有一个职责：把创建 session 所需的服务实例集中到一起，供 `AgentSessionRuntime` 持有和复用。它是纯运行时容器（持服务实例），因此不序列化。

包含：
- `session_manager`、`settings_manager`
- `model_runtime`、`resource_loader`、`system_prompt_manager`
- `cwd`、`agent_dir`、`auth_storage`、`diagnostics`

`AgentSession` 持有从 services 解包出来的扁平依赖；`ExtensionRunner` 直接接收这些扁平依赖，不再要求 `AgentSession` 保存整个 services 对象。

### 3. `core/types/session/config.py` — AgentSessionConfig
与 TypeScript 参考实现对齐的**运行时扁平配置**（`@dataclass`），不含可序列化 JSON 的边界类型：
- `agent`、`session_manager`、`settings_manager`
- `cwd`、`system_prompt_manager`、`resource_loader`、`model_runtime`
- `scoped_models`、`initial_active_tool_names`、`base_tools_override`
- `extension_runner_ref`：可选的可变引用，AgentSession 创建 runner 后写回，供外部获取当前 runner
- `session_start_event`：会话启动事件，由 `AgentSession.bind_extensions()` 发出

> `agent_dir`、`auth_storage` 等服务性字段保留在 `AgentSessionServices` 中，不进入此 config。

`AgentSessionRuntime` 在创建新 session 时，把 `AgentSessionServices` 解包成此 config。

### 4. `core/agent_session/agent.py` — AgentSession
专注于**单一会话**内的运行时逻辑：
- 直接持有 `_session_manager`、`_settings_manager`、`_model_runtime`、`_resource_loader`、`_system_prompt_manager`、`_cwd`。
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
- `core/resources/loaders/agent_config.py` 负责 Agent 组合声明读取（`agents/<name>.yaml` 单文件——**只解析不装配**：`persona:` 原始条目原样入 `AgentConfig.persona`），并按 Nova 资源优先级（全局 -> 项目级）加载。
- `core/harness/persona/`（`PersonaManager`）负责 persona 装配（路径引用→读文件+包根收敛校验；注册名→注册表查找）与 override 旋钮（内存态会话级人格切换）。
- `core/harness/agents/`（`AgentManager`）负责 agents 注册表活视图与当前角色旋钮（`change_agent` + 默认解析链：保持现状 > 显式 > 第一个可用 > base_agent）、可委派视图与 `# Available Agents` 菜单注入数据、CapabilitySelection 汇集（全资源域——tools 报告归 ToolsManager.refresh、persona 归 PersonaManager 装配、extensions/user_tools/commands/skills 归 AgentSession 过滤点，`_build_runtime` 重建统一收集处后经注入 provider 透出）、**yaml 写回**（`/agent save` 落地——包来源不可写，影子写 `<agent_dir>/agents/<name>.yaml`；user/project 来源就地写回；save-as 写新名 user 级；写盘后 `resource_loader.reload()` 生效）。
- `core/resources/loader.py` 只负责调度：在 `reload()` 中依次调用 `core/resources/loaders` 下的 `agent_config`、`prompt_templates`、`extensions`、`skills`、`personas`，自身不再包含具体加载逻辑。
- `SystemPromptManager` 纯渲染（config + 各 manager → 文本）：当前角色名经 AgentManager 活取（旋钮已乔迁），工具白名单经 ToolsManager，persona 装配经 PersonaManager；激活工具含 `subagent` 且注册表非空时注入 `# Available Agents` 委派菜单（name + source 标签 + description）。
- `SystemPromptBuilder`（`core/harness/system_prompt/builder.py`）把 Agent 配置与工具白名单渲染成最终系统提示词字符串；支持通过 `append_system_prompt` 追加额外内容。
- 当当前激活工具包含 `read` 时，`SystemPromptManager` 会把可用 skill 列表（`disable_model_invocation=False`）以 XML 格式追加到系统提示词末尾。
- 系统提示词在以下场景重建：会话初始化、切换 agent、改变激活工具集、`AgentSession.reload()`。

> **`core/resources/loaders/` 的定位**：它是各业务模块 loader 的 resource 级调用层。业务模块（如 `core/harness/system_prompt`、`core/extensions`）自己实现文件格式解析，`core/resources/loaders/` 负责按 Nova 的全局/项目优先级、去重、扩展上下文组装等规则调用它们，并把结果交给 `DefaultResourceLoader`。

### 10. `core/types/ui/` + `server/`（接入层）+ `modes/` — UI 反向原语与模式化前端（架构 2.0 收窄版）

> 架构 2.0（`examples/nova_architecture_2.0.md` 图纸；落地终态与 Node 层/复合包/多后端完整设计见 **`examples/nova_architecture_2.1.md`**）确立：Python 为纯 agent 运行时，**正向 UI 操作**（组件/状态栏/编辑器/主题/注册表/终端输入）全部归 Node 层 UI 管线。Python 侧只保留**反向原语**——后端运行时需要向用户请求输入/发出通知的通道（trust 询问、OAuth 引导、扩展询问），对应 RPC 协议四件套之一。

- `core/types/ui/primitives.py` 定义 `UIResponse`；`core/types/ui/context.py` 定义**唯一的** `UIContext` 抽象接口（泛型 transport：只有 `capabilities`/`has_capability`/`request`/`notify`，零词汇——交互词汇归包，见上文"UI 反向原语"条）。
- 运行模式不设独立类型（原 `ExtensionMode` 已移除）：扩展只需要两个信号——`has_ui`（有没有前端挂在通道上，构造期即知，trust 决议等早期裁决用它）与 `ui.capabilities`（前端支持哪些原语，连接后协商）。`NoOpUIContext`（`core/types/ui/noop.py`）表示无 UI 运行模式，全部安全 no-op。
- `server/` 是内聚的**接入层**（原 `rpc/` 改名收录——归约与 item 词汇入编后，"RPC" 已装不下它），内部分层：
  - `server/transport/`：通道层——`Transport` 抽象（`base.py`）+ `StdioTransport` / `MemoryTransport`；dict 消息怎么物理流动，不解析 JSON-RPC 语义。
  - `server/protocol/`：语义层——JSON-RPC 消息模型（`jsonrpc.py`/`errors.py`/`router.py`：注册表携带方法形状 `MethodShape`，分派前 params 模型校验）、事件直通序列化桥（`serialize.py`：Bus 2 事件 → `{type, data}` 信封，哑管道零呈现加工）、线上契约构建期导出（`schema_export.py`：事件/条目/item 类型 + 方法形状 + `CONTRACT_VERSION_MAJOR/MINOR`（major 不等硬拒、minor 加法放行）→ JSON Schema + TS 双工件，pytest 漂移测试保鲜）、方法形状声明（`methods/shapes.py`：方法级域/params/result 模型，校验/导出/能力位三方同源）、命令方法表（`methods/`，按域拆分：`session` 会话·队列·retry·reload·克隆/导出/导入 / `model` 模型发现·切换·scoped·思考级别 / `auth` 鉴权·login / `resources` skills·prompt templates / `settings` 设置读写（无会话可用） / `system` 命令·扩展 flags·扩展快捷键目录与回调 / `user_tools` / `package`）。`ui/response` 与 `system/capabilities` 由 `RpcServer` 分派前直管（按连接记账），不进方法表。全量方法清单见 `examples/rpc_capabilities.md`。
  - `server/types/`：线上词汇——`items.py`（NovaItem 基类 + 框架变体 + NovaWireItem 联合，item 纯线上/呈现形状，core 零感知）+ `notifications.py`（item_started/delta/completed 三帧）。
  - `server/reduction/`：归约层——`mapping.py`（无状态纯映射：消息/条目→item、apply_delta 合并规则）+ `orchestrator.py`（`SessionReducer` 在飞 item 状态机：订阅会话总线把内容事件归约为 item 帧，包级 `item_emission` 信封在此校验承接）。
  - `server/server.py` + `server/connection.py` + `server/ui_context.py`：组装器 `RpcServer`——连接注册表 + MethodRegistry + `RoutingUIContext` + State 的组合，含事件广播（initialize 门）与并发分派 + 归约器随会话挂摘；连接一等公民（`Connection`：状态机/能力集/在飞请求表/有界出站队列+独立写泵；读泵归服务器），背压按来源分流（stdio/memory 阻塞等位，WebSocket 慢消费者断连）；反向原语按连接寻址（`RoutingUIContext`：发起方优先——经 `current_connection` contextvar，无归属广播首响应胜出+败者收 ui/cancel；作用域仲裁：agent_end/session_replaced 按归属批量终结挂起请求）；cancelRequest 按连接隔离。
  - 依赖方向单向：transport ← protocol ← server（connection/ui_context 归 server 层）。
  - **RPC 循环线程三禁**（卡顿纪律，`RpcServer` 内置滞后探针兜底观测——超 100ms 漂移打 `rpc-stderr.log`）：①禁同步阻塞调用（`time.sleep`/同步 subprocess/大文件同步读写——逃生舱 `asyncio.to_thread`）；②禁大段 CPU（大会话全量序列化等重活分页或下线程）；③禁全局写锁类队头阻塞（出站一律走连接自有队列，写不穿 `Connection`）。入站背压：在飞 handler 超 `max_inflight`（默认 256）对请求回 `-32004 overloaded`、对通知丢弃。
- `modes/rpc/` 是 **rpc 模式** 的传输实现（TUI 等终端前端使用）；反向原语词汇 schema 已移出 harness（词汇定义权归包，见上文"UI 反向原语"条）。
- 反向通道：`ui/request`（后端→前端请求）与 `ui/response`（前端→后端应答）配对；`system/capabilities` 上报前端支持的原语子集，未支持方法优雅降级（`NoOpUIContext` 全部安全 no-op）。
- 包管理的 `ResourceType.UI_BLOCKS` 与 `THEMES` 资源类目已移除（enums/resolution/manifest/settings/discovery/resolver/loader 全链路）：主题与 UI 渲染资产归 Node 层 UI 管线（架构 2.0），Python 纯运行时的包资源只保留 extensions / skills / prompts / tools / agents 五类能力资源；工具与用户工具的 `ui_blocks` 声明/数据通道也已清除——工具结果只携带平铺结构化 `details`（纯数据），渲染形状归前端。
- **包管理的安装事实记录采用 dist-info 目录**（对齐 pip/uv 的 `*.dist-info/` 生态风格）：sibling 于已安装副本（`<name>.dist-info/`），安装时机制写入、之后只读——`direct_url.json`（PEP 610 格式：path 源为 `file://` 绝对路径 + editable，git 源为 remote URL + requested ref）、`package_name`（自安装判定快照）、`installed_at`。dist-info 为 source/editable/package_name 的**权威快照**（防副本篡改漂移），name/version/deps 始终读副本 `pyproject.toml`；dist-info 缺失时（旧安装）回退磁盘推导（symlink 判定 editable、重算 package_name）。settings 仍是 source 配置的唯一记录点（相对路径存储、可共享）；`packages/` 下自动写入 `.gitignore`（settings 可提交共享、安装产物不被追踪）；editable 配置的共享仅在包位于仓库内（monorepo）时有效。
### 11. `core/extensions/` / `core/types/extensions/` / `core/resources/loaders/extensions.py` — 扩展系统
- 扩展系统按 TypeScript `coding-agent/src/core/extensions/` 重新设计，不保留旧版兼容性：
  - `core/types/extensions/`：扩展类型统一入口（`Extension`、`ExtensionRuntime`、`ExtensionAPI`、`ExtensionCommand`、`ExtensionFlag`、`ExtensionShortcut`、`LoadedExtensionsResult`、`SourceInfo` 等），按子主题拆分到多个模块并通过 `__init__.py` 统一导出。
  - `core/extensions/api.py`：`NovaExtensionAPI` 是扩展工厂**装载时**收到的注册面（`on` / `registerCommand` / `registerShortcut` / `registerFlag` / `registerSpawnHook` / `getFlag` / `registerProvider` / `events`）——只做声明式注册；运行期动作（`send_message` / `exec` / `set_active_tools` / …）与环境感知统一走事件 handler 的 `ctx`（`ExtensionContext`）。分工判据：注册不依赖会话（装载期申报"我有什么"），动作没有活会话不成立（发给谁/改谁的注册表），故归代表活会话的 `ctx`。
  - `core/extensions/loader.py`：`ExtensionLoader` 与 `load_extensions()` 负责扩展发现、模块加载与工厂执行。
  - `core/extensions/runner.py`：`ExtensionRunner` 负责扩展生命周期、事件分发、上下文创建、action 绑定与 provider 注册队列刷新。
- `ResourceLoader`（具体为 `DefaultResourceLoader`）创建并持有扩展间事件总线 `event_bus`，所有扩展共享同一个 bus；`LoadedExtensionsResult` 返回 `extensions`、`errors`、`runtime`，调用方通过 `ResourceLoader.event_bus` 获取总线。
- `AgentSession` 在初始化时从 `ResourceLoader.get_extensions()` 读取扩展和 `runtime`，从 `ResourceLoader.event_bus` 读取事件总线，传给 `ExtensionRunner`；`ExtensionRunner` 不再自己创建默认 event bus。
- `ExtensionRunner` 提供 `emit_error`、`has_handlers`、`get_command`、`get_registered_commands`、`get_flags`、`get_flag_values`、`set_flag_value`、`get_shortcuts`、`get_shortcut_diagnostics`、`invoke_shortcut`、`emit_resources_discover` 等运行时 API。
- 扩展快捷键（`registerShortcut`）的 handler 是运行时代码（拿 `ExtensionContext`）：`get_shortcuts()` 收集注册表并裁决**扩展间**冲突（先注册者获胜，记诊断）；内置键位表、用户自定义与冲突裁决归前端（架构 2.0：键位绑定是前端状态），目录经 RPC `getShortcuts` 透出、前端键位捕获后经 `invokeShortcut` 回调执行。
- 扩展的运行期动作面在 `ExtensionContext`（事件 handler 的 `ctx`）上：`exec(command, args, options?)` 执行 shell 命令并返回 `ExecResult`；`set_model(model)` 返回 `bool`（缺少 API key 时返回 `False`）；`get_active_tools()` / `get_all_tools()` / `set_active_tools()` 查询与切换工具激活状态；`get_commands()` 返回 `SlashCommandInfo[]`；`send_message` / `send_user_message` / `append_entry` / `set_label` 等会话动作同在该处。
- `NovaExtensionAPI.register_command` / `register_shortcut` / `register_flag` 采用 TS 风格的 `(name/key, options)` 调用。
- `session_start` 由 `AgentSession.bind_extensions()` 发出（与 TS 参考实现一致），随后触发 `resources_discover`。
- 支持事件：`session_start` / `session_shutdown` / `session_before_compact` / `session_compact` / `session_before_tree` / `session_tree`，以及 `prepare_next_turn` / `should_stop_after_turn`，`AgentEvent` 桥接、`tool_call` / `tool_result` / `context` / `input` / `before_provider_request` / `after_provider_response` 等 hook。
- 扩展可通过 `NovaExtensionAPI`（`nova`）注册 provider、命令、快捷键、flag；**工具统一走 package tool 路径，不再通过扩展注册**。
- 扩展目录：项目级 `<cwd>/.nova/backend/extensions/`、全局 `~/.nova/agent/backend/extensions/`（前后端分治 §9 后端半区），以及 `Settings.extensions` 显式配置的路径。
- `ExtensionContext` 暴露 `ui`（`UIContext`）、`has_ui`（是否有前端挂在通道上，trust 决议等早期判断用它）以及 `is_project_trusted()` 等上下文 action，扩展可据此决定是否执行需要用户交互或高权限的操作。
- `ExtensionRunner` 在 `create_context()` 时注入当前 `ui_context` 与 runtime 状态；`project_trust` 事件允许扩展参与信任裁决。
- `DefaultResourceLoader` 已深度接入 `PackageManager`：`AgentSessionServices.create()` 会默认构造 `PackageManager` 并注入到 `DefaultResourceLoaderOptions.package_manager`。`PackageManager` 是统一 facade，内部聚合 `PackageInstaller` 与 `PackageResolver`；传入后，`reload()` 会把解析结果作为扩展 / skill / prompt / tool / agent 的唯一来源（settings 是唯一选择层——仅物化未写入 settings 的包不参与解析），关闭子加载器的默认目录扫描，避免重复发现；`extend_resources()` 贡献的临时路径仍会与解析结果合并，并统一去重。`DefaultResourceLoaderOptions.install_missing_packages` 控制 `PackageManager.resolve_resources()` 发现 settings 中配置的 package 缺失时是否自动调用 installer 安装，默认在 `AgentSessionServices.create()` 中开启。

> **与 TS 的差异**：`message_renderers` 已移除（渲染归 Node 层）；`shortcuts` 已按架构 2.0 接线（运行时持注册表与 handler，目录/回调走 RPC，键位绑定归前端）；`themes` 类目已从资源系统移除（架构 2.0 中归 Node 层 UI 资产，Python 纯运行时不再解析/加载主题；`ui_blocks` 包资源类目同）。`UIContext` 为泛型 transport（`request`/`notify` + 能力发现模型），不持有交互词汇——便捷方法与标准词汇归官方 bundle（`nova_coding_agent.ui_primitives`）。工具链路已定型：框架零内置、零预设名单，默认激活 = 注册表全部，过滤链为 denylist → allowlist → agent.yaml 白名单。

### 12. `core/model/` — ModelRuntime 与模型解析
- `runtime.py`：模型与鉴权的运行时（对齐 TS `core/model-runtime.ts` 的终态设计；TS 侧的 `ModelRegistry` 薄 facade 未移植，如需扩展只读视图再议）。
- provider 经 `composer.py` 三层合成（内置 → `models.json` → 扩展注册）；无覆盖时内置 provider 原样进入集合，保留其 OAuth/stream 行为。
- **credential-blind**：api_key 与 `Authorization` 头不写入 `Model`，请求时经内部 `Models.get_auth` 解析（runtime override → stored credential → models.json/extension key → 环境变量链 → OAuth 刷新）。
- `stream` / `stream_simple` / `complete` / `login` / `logout` / `check_auth` 等 Models 表面直接透传内部集合，调用方不触碰底层 `Models` 实例。
- **动态模型刷新**：组合 provider 保留 base 的 `refresh_models` 与扩展 `refresh_models_fn`；`refresh()`（async）做网络刷新并联动可用性快照，`AgentSessionServices.create()` 启动时以 15s 上限调用；`NOVA_OFFLINE` 时只读 `models-store.json` 缓存（`store.py` 的 `FileModelsStore`）。
- **扩展 OAuth**：`register_provider(..., oauth=ExtensionOAuthConfig)` 经 `adapt_oauth` 接入 auth 链，支持 `modify_models` 按 credential 改写模型列表。
- `get_available` / `has_configured_auth` 为同步快照；`refresh_availability()`（async）用 nova_ai auth 链精确刷新。
- runtime key 管理：`set_runtime_api_key` / `remove_runtime_api_key` / `list_credentials`（联动快照与刷新）；`is_using_oauth` / `get_provider_auth_status`；动态 provider 注册（重复注册按已定义字段合并）。

### 13. `core/config/settings/manager.py` — SettingsManager
- 双层设置：**全局**（`~/.nova/agent/settings.json`）与 **项目级**（`<cwd>/.nova/settings.json`）。
- 字段级 dirty tracking，延迟写入（`flush()`）。
- 涵盖 retry、compaction、terminal、image 等设置。

---

## 测试

当前 `tests/` 目录已包含覆盖以下模块的测试：

- `tests/core/agent_session/`：AgentSession 生命周期、事件、工具、扩展、消息队列。
- `tests/package/`：包管理器安装/卸载/列表/校验，以及 agent/tool/skill/extension/bundle 五种类型。
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
cd packages/nova-harness/backend
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
- `models.json` / `auth.json` 中的配置值按 TS 语义解析（`core/config/resolve.py`）：`$VAR` / `${VAR}` 为环境变量引用（任一缺失即整体解析失败并报出变量名），`!cmd` 前缀执行 shell 命令取输出，`$$` / `$!` 为转义，其余一律按字面量。

### 会话文件
- 会话历史以 **JSONL** 明文存储，可能包含敏感代码片段或输出。
- 存储路径：`~/.nova/agent/sessions/--<cwd>--/`。
- `.gitignore` 已忽略 `sessions/` 与 `*.session`。

### 文件操作安全
- Agent 组合声明的 `persona` 条目相对 yaml 文件解析（`..` 允许指向包内资源目录——安装即信任的包内资源；resolve 后逃逸包根的条目被诊断拒绝；项目级 agent yaml 受 trust 门控）。

---

## Agent 配置与 Subagent

### Agent 组合声明文件

Agent 是 `agents/<name>.yaml` 单文件——**纯组合声明**（三层模型：backend/ 与 frontend/ 是素材海，agents/ 是组合层）：

```
agents/
├── coding_agent.yaml      # 一个 agent 一份组合声明
└── scout.yaml
```

组合声明示例：

```yaml
# 元数据（name 缺省 = 文件名）
name: coding_agent
version: "1.0.0"
description: Nova coding agent with local file system tools and subagent delegation
author: nova

# 人格偏好（可选；不会自动作为会话默认模型）
model: openai/gpt-4o

# 人格文本组装：条目列表（顺序即组装顺序）。条目能相对本文件解析为
# 文件/目录的按路径装配（文件直读；目录递归收 .md 按相对路径字典序在
# 该位置展开；路径须收敛在包根内），否则按注册名查 persona 注册表。
# 装配在会话期由 PersonaManager 完成；解析失败/未命中产生诊断。
persona:
  - ../backend/personas/coding/role.md
  - ../backend/personas/coding/guide.md

# 工具激活集：只有列出的工具才会注册到当前 agent
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
  # （可委派名单 = 会话注册表全量，无主从划分——yaml 无 subagents 字段）
  - subagent

# Skill 裁剪名单（可选）：非空时仅允许列出的**包内 skill**（origin=package）
# 注入系统提示词附录——裁剪的是每轮自动注入的附录面（token 卫生 + 人格聚焦）。
# 注意语义边界：名单只约束包内 skill；用户级（~/.agents、~/.nova/agent）、
# 项目级（祖先 .agents、.nova）与 CLI 显式路径的 skill 始终放行——
# 用户与团队的技能库不需要 agent 作者授权（项目级安全边界归 project trust）。
skills:
  - python_best_practices
  - git_workflows

# Extension 白名单：允许挂载到当前会话的 extension 名称列表（空 = 全允许）
extensions:
  - session_commands

# 命令允许集：只放行列出的 slash 命令（空 = 全部允许）；
# 用户层另有 settings.disabled_commands 排除集——求交生效
commands:
  - tree
  - fork
```

字段说明：

- `name` / `version` / `description` / `author`：元数据，仅用于展示和包管理。
- `model`：该 agent 倾向使用的模型标识（可选）。当前会被记录到 `AgentConfig.model`，但**不会**自动作为会话默认模型；会话模型仍由调用方、设置或 SDK 默认策略决定。
- `persona`：人格条目列表（可选）。条目为**路径**（相对本文件解析，须收敛在包根内）或**注册名**（persona 注册表——包 `[tool.nova] personas` 类目 + `~/.nova/agent/backend/personas/` + `.nova/backend/personas/` 三源发现——前后端分治 §9 后端半区）；文本资源归 `backend/personas/`——与 `prompts/`（用户触发模板）是不同概念，不要混放。加载期只解析不装配，装配归会话期 `PersonaManager`。
- `tools` / `skills` / `extensions` / `user_tools` / `commands`：能力名单（可选）。名单字段统一三态：**键缺席 = 全放不设防、显式空列表 `[]` = 全禁、非空 = 名单（支持 `!` 排除）**；`skills` 的名单只约束包内 skill（用户级/项目级始终放行——随时可加性）。`tools` 另有 `role_boundary` 语义开关：open（默认）只做初始激活集、strict 才裁注册表。**`subagents` 字段已删除**——只有 agents 没有 subagents，可委派名单即会话注册表全量。
- **已退役**：`description.md` 及 frontmatter 合并、`sections/` 目录约定、`setup.md`。

解析逻辑位于 `core/resources/loaders/agent_config.py`（`load_agent_config_from_yaml` / `load_agents`——扫描目录顶层 `*.yaml`）；运行时过滤位于：

- skills：`core/harness/system_prompt/manager.py`（系统提示词注入）和 `core/agent_session/agent.py`（`/skill:name` 命令展开）——两处统一走 `core/harness/skills.py` 的 `filter_skills_by_whitelist`（来源分治 + 三态：None 全放、[] 包内全禁、名单仅约束 `origin="package"`，其余 origin 与无 `source_info` 放行）。加载层不做名单过滤。
- extensions：`core/agent_session/agent.py` 初始化 `ExtensionRunner` 时按白名单过滤（config 经 AgentManager 现取——首建时 SystemPromptManager 尚未创建）。
- commands：`core/agent_session/agent.py`（`get_commands` 过滤 + 快照透出 `allowedCommands`/`disabledCommands`）与 `core/agent_session/controllers/slash_input.py`（调用守卫）——用户层排除集走 settings `disabled_commands`。
- tools：仍由 `core/agent_session/controllers/tools.py` 按 `SystemPromptManager` 的激活工具集注册。

各过滤点同时产出 CapabilitySelection 报告（yaml 点名项的 ok/missing/disabled_by_settings/disabled_by_sdk 归因），共享判定函数为 `core/utils/name_sets.py::build_selection_report`；settings 层对 extensions/skills 是路径级 pattern（resolver 应用）——extensions 经 `DefaultResourceLoader.get_disabled_extension_names()` 推导注册名可精确归因，skills 因注册名来自 SKILL.md frontmatter 无法干净映射，被 settings 裁掉的包内 skill 只能呈现为 missing（局限，在案）。

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

- `name` / `version` / `description` / `authors` 复用 Poetry 标准段；`[tool.nova]` 段声明资源路径与包行为。可声明的资源类目为 `agents` / `tools` / `skills` / `extensions` / `prompts` / `user_tools` / `personas` 七类（themes 与 ui_blocks 已归 Node 层 UI 资产）；settings 的 package dict 支持 `editable`、七类资源过滤器以及 `autoload: false`（project scope 的 delta 语义：在 user 层自动加载基础上局部翻转，`+path` 启用、`-path`/`!pattern` 禁用）。
- 自安装边界只看**这个包是不是可安装的 Python 包**（声明了 `name` 且有 build-system）：满足则以 `--no-deps` 装进当前 Python 环境，与包含哪类资源无关——纯资源包无包结构时不自安装，executor 自包含的 tools 包同样不需要包结构。
- 该 Python package 供 executor/extension 通过标准 import 引用包内共享模块（例如 `nova_coding_agent.tools_common`）。
- agents、skills、extensions 必须是自包含的，不得依赖包的 Python package。
- `nova-pkg init` 会根据当前目录结构自动扫描并生成 `[tool.nova]` 段。

### 官方 Subagent 工具

`nova_coding_agent` 的 `tools/subagent.py` 是 Nova 官方 subagent tool，让任意 agent 可以调用 `subagent` 工具委托任务给其他 agent。核心实现位于 `nova_coding_agent` 包的 `nova_coding_agent/subagent/`，由 tool executor 调用。

**只有 agents，没有 subagents**（设计定案 §7）：工具名保留 subagent 仅指"委派"动作——agent 解析消费**会话注册表**（`ToolExecContext.agents` 快照按名查表，未知名报错并列出可用名与来源标签）；工具侧零发现管线（旧三源发现、`agent_scope` 参数、独立 trust 判定已全部删除——项目源安全归发现期 Project Trust 门控一条管道）。

安装方式：

`nova-coding-agent` 包已在 `pyproject.toml` 的 `[tool.nova]` 中声明 `tools` 包含 `./backend/tools/subagent.py`，随 bundle 安装后即可使用。在 agent 的 `agents/<name>.yaml` 中把 `subagent` 加入 `tools` 名单即启用：

```yaml
tools:
  - read
  - subagent
```

配套机制：

- **执行前确认**（自治权检查点）：bundle 扩展 `subagent_gate.py` 拦截 subagent 调用，per-agent 逐名裁决（允许一次 / 本会话始终允许 / 取消）；always 经 `subagent_allow` 会话条目持久化（分支安全，session_start/session_tree 恢复）；headless 直接放行。
- **动态菜单注入**：激活工具含 `subagent` 时系统提示词渲染 `# Available Agents`（name + source 标签 + description，AgentManager 供数），模型不再靠报错学名单。

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
1. **权威位置**：`core/types/` 下的对应分组。会话相关类型在 `core/types/session/`；资源类型（agents/skills/prompts/tools/context files）在 `core/types/resources/`；扩展协议（含 spawn hook 契约 `process.py`）在 `core/types/extensions/`；事件在 `core/types/events/`。
2. 内部模块和测试都应从 `core/types/` 下的对应分组导入（如 `core.types.session`、`core.types.resources`、`core.types.config`、`core.types.extensions`、`core.types.project_trust`、`core.types.model`），不要再走已删除的 `<module>/types.py`、`<module>/options.py` 或旧顶层文件。

### 添加新工具
1. 工具由包分发：创建 `tools/<name>.py`（单文件，推荐；需同目录资产时用 `tools/<name>/executor.py` 目录形态），实现 `Tool` 类——元数据为类属性（`name` / `description` / `parameters` 必需，可选 `label` / `execution_mode` / `prepare_arguments` / `prompt_snippet` / `prompt_guidelines`），`__init__(context)` 注入 `ToolContext`（cwd / settings 只读视图——构造期不变量），执行为 `execute(tool_call_id, params, signal, on_update, ctx)`（`ctx` 为 `ToolExecContext`，当前模型等执行期状态每次调用现取注入）。无独立元数据文件。
2. 工具加载统一由 `core/resources/loaders/tools.py` 的 `ToolLoader` 完成；`DefaultResourceLoader.get_tools()` 在 `reload()` 时自动加载。
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
