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
- 使用 **Pydantic v2** 做数据建模与 JSON 序列化；所有 harness 数据类型直接继承自 `nova_ai.NovaBaseModel`，使用原生 `model_dump()` / `model_validate()` / `model_dump_json()` / `model_validate_json()`。

### 类型系统说明

- `NovaBaseModel.model_dump()` 默认 `mode="json"`，Enum 字段会序列化为字符串。
- 枚举字段在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。

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
    ├── cli/                    # 所有 CLI 入口
    │   ├── __init__.py         # 公开 main（转发自 cli/main.py）
    │   ├── main.py             # nova-harness 主入口
    │   ├── run.py              # run 子命令
    │   └── package.py          # nova-pkg 包管理器入口
    ├── modes/                  # 运行模式
    │   ├── rpc/                # JSON-RPC 服务（供 TUI 等外部前端调用）
    │   └── interactive/        # 交互模式（占位）
    └── core/                   # 所有运行时实现与内部基础设施
        ├── __init__.py         # 公开运行时核心符号
        ├── sdk.py              # 对外 SDK 工厂函数
        ├── agent_session/      # AgentSession 运行时核心
        │   ├── agent.py        # AgentSession 类
        │   ├── runtime.py      # AgentSessionRuntime
        │   ├── services.py     # AgentSessionServices
        │   ├── options.py      # AgentSessionConfig
        │   ├── controllers/    # 领域控制器（bash、compaction、events、model 等）
        │   └── extensions/     # 扩展系统（api、runner、context）
        ├── harness/            # 高阶 SDK 能力
        │   ├── session/        # 会话持久化与树管理
        │   ├── compaction/     # 上下文压缩与分支总结
        │   ├── system_prompt/  # 系统提示词构建
        │   └── skills.py       # Skill 管理与命令展开
        ├── resources/          # 资源发现与加载
        │   ├── loader.py       # ResourceLoader 抽象基类与 DefaultResourceLoader
        │   └── loaders/        # 资源加载器（agent_config、extensions、prompt_templates、skills、tools）
        ├── package/            # Agent / tool / bundle / skill 包管理核心
        │   ├── core.py
        │   ├── manifest.py
        │   ├── sources.py
        │   ├── deps.py
        │   └── utils.py
        ├── core/config/             # 配置层：settings、model registry、auth storage、路径默认值
        │   ├── defaults.py
        │   ├── resolve.py
        │   ├── storage/        # 通用存储后端抽象
        │   ├── settings/       # 设置管理
        │   ├── auth/           # 鉴权 / API key 存储
        │   └── model_registry/ # 模型注册表
        ├── core/types/              # 统一类型层：所有跨模块/模块内数据类型与事件 payload
        │   ├── messages.py
        │   ├── session.py
        │   ├── compaction.py
        │   ├── events/
        │   ├── extensions.py
        │   ├── setting.py
        │   ├── tools.py
        │   ├── agent_config.py
        │   ├── model_registry.py
        │   ├── resource.py
        │   ├── package_manager.py
        │   ├── skills.py
        │   ├── diagnostics.py
        │   └── agent.py
        └── core/utils/              # 通用工具
└── extensions/                 # 官方扩展（可选安装）
    └── subagent/               # 子智能体扩展
        ├── extension.py        # 扩展入口
        ├── runner.py           # single/parallel/chain 执行引擎
        ├── types.py            # SubagentCall / SubagentResult
        └── README.md
```
---

## 核心模块职责

### 1. `core/sdk.py` — 入口工厂
提供 `create_agent_session(options)` 异步函数，负责：
- 解析/创建 `agent_dir`（默认 `~/.nova/agent`）与 `session_dir`。
- 初始化 `SessionManager`、`SettingsManager`、`ModelRegistry`、`AuthStorage`，并封装为 `AgentSessionServices`。
- 解析初始模型：优先恢复现有会话上下文中的模型，其次 settings 默认模型，最后 fallback 到 `volcengine/deepseek-v3-2-251201`。
- 构建 `Agent` 实例（来自 `nova_agent`）；Agent 层的扩展 hook 由 `AgentSession` 在初始化时直接绑定到它自己创建的 `ExtensionRunner`。
- 将 `AgentSessionServices` 与 `session_start_event` 注入 `AgentSessionConfig`，创建 `AgentSession`。
- 调用 `session.bind_extensions()` 触发扩展 `session_start` 生命周期。
- 包装为 `AgentSessionRuntime` 返回。

### 2. `core/agent_session/services.py` — AgentSessionServices
**cwd 绑定的服务容器**，只有一个职责：把创建 session 所需的服务集中到一起，供 `AgentSessionRuntime` 持有和复用。

包含：
- `session_manager`、`settings_manager`
- `model_registry`、`resource_loader`、`system_prompt_manager`
- `cwd`、`agent_dir`、`auth_storage`、`diagnostics`

`AgentSession` 本身不持有 `AgentSessionServices`，而是持有从 services 解包出来的扁平依赖；
但为了在初始化时自行构建 `ExtensionRunner`，`AgentSessionConfig` 会额外携带 `services` 字段。

### 3. `core/agent_session/options.py` — AgentSessionConfig
与 TypeScript 参考实现对齐的**扁平配置**：
- `agent`、`session_manager`、`settings_manager`
- `cwd`、`system_prompt_manager`、`resource_loader`、`model_registry`
- `scoped_models`、`initial_active_tool_names`、`base_tools_override`
- `services`：用于在 `AgentSession` 内部创建 `ExtensionRunner`
- `extension_runner_ref`：可选的可变引用，AgentSession 创建 runner 后写回，供外部获取当前 runner
- `session_start_event`：会话启动事件，由 `AgentSession.bind_extensions()` 发出

`AgentSessionRuntime` 在创建新 session 时，把 `AgentSessionServices` 解包成此 config。

### 4. `core/agent_session/agent.py` — AgentSession
专注于**单一会话**内的运行时逻辑：
- 直接持有 `_session_manager`、`_settings_manager`、`_model_registry`、`_resource_loader`、`_system_prompt_manager`、`_cwd`。
- 在初始化时从 `ResourceLoader` 创建 `ExtensionRunner`，并把 Agent 层的扩展 hook（`before_tool_call` / `after_tool_call` / `transform_context` / `on_payload` / `on_response` / `prepare_next_turn` / `should_stop_after_turn`）直接绑定到 runner。
- `bind_extensions()`：与 TS `AgentSession.bindExtensions()` 对齐，绑定扩展上下文、发出 `session_start`，并调用 `resources_discover` 把扩展贡献的 skill/prompt/theme 路径合并到 `ResourceLoader`。
- `execute_command()` / `prompt()` 中的 slash command 解析：支持扩展通过 `/command args` 执行自定义命令；`get_slash_commands()` 同时暴露扩展命令与 `skill:name` 命令。
- `/skill:name` 命令展开：`prompt()`、`steer()`、`follow_up()` 在启用模板展开时，通过 `core.harness.skills.SkillManager.expand_command()` 把 `/skill:name args` 展开为 XML skill block。
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
- `core/resources/loaders/agent_config.py` 负责 Agent 配置文件读取（`description.md`、`sections/`、`tools.json`、`setup.md`、`user/`），并按 Nova 资源优先级（全局 -> 项目级）加载 Agent 配置。
- `core/resources/loader.py` 只负责调度：在 `reload()` 中依次调用 `core/resources/loaders` 下的 `agent_config`、`prompt_templates`、`extensions`、`skills`，自身不再包含具体加载逻辑。
- `SystemPromptManager` 运行时维护当前选中的 agent、默认激活工具、扩展工具注入与激活工具白名单。
- `SystemPromptBuilder`（`core/harness/system_prompt/builder.py`）把 Agent 配置与工具白名单渲染成最终系统提示词字符串；支持通过 `append_system_prompt` 追加额外内容。
- 当当前激活工具包含 `read` 时，`SystemPromptManager` 会把可用 skill 列表（`disable_model_invocation=False`）以 XML 格式追加到系统提示词末尾。
- 系统提示词在以下场景重建：会话初始化、切换 agent、改变激活工具集、`AgentSession.reload()`。

> **`core/resources/loaders/` 的定位**：它是各业务模块 loader 的 resource 级调用层。业务模块（如 `core/harness/system_prompt`、`core/agent_session/extensions`）自己实现文件格式解析，`core/resources/loaders/` 负责按 Nova 的全局/项目优先级、去重、扩展上下文组装等规则调用它们，并把结果交给 `DefaultResourceLoader`。

### 10. `core/agent_session/extensions/` / `core/resources/loaders/extensions.py` — 扩展系统
- `ResourceLoader`（具体为 `DefaultResourceLoader`）持有扩展间事件总线 `event_bus`，并在 `reload()` 中负责发现并加载扩展、skills、agents、prompt templates。
- `AgentSession` 在初始化时从 `ResourceLoader.get_extensions()` 读取扩展，自行创建 `ExtensionRunner`，负责扩展事件分发和 action 委托。
- `ExtensionRunner` 提供 `emit_error`、`has_handlers`、`get_command`、`get_registered_commands`、`get_flags`、`get_flag_values`、`set_flag_value`、`get_shortcuts`、`get_message_renderer`、`get_tool_definition`、`emit_resources_discover` 等运行时 API。
- `session_start` 由 `AgentSession.bind_extensions()` 发出（与 TS 参考实现一致），随后触发 `resources_discover`。
- 支持事件：`session_start` / `session_shutdown` / `session_before_compact` / `session_compact` / `session_before_tree` / `session_tree`，以及 `prepare_next_turn` / `should_stop_after_turn`，`AgentEvent` 桥接、`tool_call` / `tool_result` / `context` / `input` / `before_provider_request` / `after_provider_response` 等 hook。
- 扩展可通过 `NovaExtensionAPI`（`nova`）注册工具、provider、命令、快捷键、flag、消息渲染器。
- 扩展目录：项目级 `<cwd>/.nova/extensions/`、全局 `~/.nova/agent/extensions/`，以及 `Settings.extensions` 显式配置的路径。

> **与 TS 的差异**：Python 版尚无 TUI，因此 `ui`/`mode`/`hasUI` 为占位实现，`message_renderers` / `shortcuts` / `user_bash` 尚未被 UI 层消费；`themes` 加载为占位；工具注册未实现 `allowedToolNames` / `excludedToolNames`、prompt snippets/guidelines 等细粒度控制。

### 11. `core/config/model_registry/registry.py` — ModelRegistry
- 加载内置模型（来自 `nova_ai`）与自定义模型（`models.json`）。
- 支持 provider 级别覆盖（`base_url`、`headers`、`api_key`）与 per-model 覆盖。
- 动态 provider 注册（`register_provider` / `unregister_provider`）。

### 12. `core/config/settings/manager.py` — SettingsManager
- 双层设置：**全局**（`~/.nova/agent/settings.json`）与 **项目级**（`<cwd>/.nova/settings.json`）。
- 字段级 dirty tracking，延迟写入（`flush()`）。
- 涵盖 retry、compaction、terminal、image 等设置。

---

## 测试

当前 `tests/` 目录已包含以下测试：

- `test_harness_smoke.py`
  - `test_harness_base_model_serialization`：验证 `NovaBaseModel` 序列化。
  - `test_create_agent_session_and_prompt`：真实 LLM 集成测试，需环境变量 `VOLCENGINE_API_KEY`。
- `test_harness_types.py`：覆盖迁移后的 Pydantic 类型往返序列化。

运行方式：

```bash
cd packages/nova_harness
poetry run pytest

# 仅运行真实 API 集成测试
poetry run pytest -m integration

# 生成覆盖率报告
poetry run pytest --cov=nova_harness --cov-report=html
```

建议继续按模块结构补充测试：

```
tests/
├── test_session.py
├── test_compaction.py
├── test_model_registry.py
└── ...
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
- Agent 配置加载器在加载 `user/` 等用户自定义章节时会校验路径（禁止 `..` 与绝对路径）。

---

## Agent 配置与 Subagent

### Agent 配置 frontmatter

`description.md` 现在支持可选 YAML frontmatter，用于声明 agent 元数据：

```markdown
---
name: scout
description: Fast codebase recon
model: claude-haiku-4-5
subagents: []
tools: [read, grep, find, ls, bash]
---

You are a scout...
```

- `model`：该 agent 的默认模型（可选）。
- `subagents`：允许该 agent 调用的子 agent 白名单（可选）。
- `tools`：工具白名单，与 `tools.json` 合并，按 name 去重。
- 无 frontmatter 的 `description.md` 行为与之前完全一致。

解析逻辑位于 `core/resources/loaders/agent_config.py`。

### 官方 Subagent 扩展

`extensions/subagent/` 是第一个官方扩展，让任意 agent 可以调用 `subagent` 工具委托任务给其他已安装 agent。

安装方式：

```bash
mkdir -p ~/.nova/agent/extensions
ln -sf /path/to/nova_harness/extensions/subagent ~/.nova/agent/extensions/subagent
```

在 agent 的 `tools.json` 中加入 `"subagent"` 即可启用：

```json
[
  {"name": "read"},
  {"name": "subagent"}
]
```

支持三种模式：

- **single**：`{ agent, task, cwd? }`
- **parallel**：`{ tasks: [...] }`，最多 8 个任务，并发 4 个
- **chain**：`{ chain: [...] }`，支持 `{previous}` 占位符传递上一步输出

核心 API `NovaExtensionAPI.create_subagent_session(name, options)` 在 `core/agent_session/extensions/runner.py` 中实现，复用父 session 的 `model_registry`、`auth_storage`、`settings_manager` 等服务。

---

## 常见开发任务

### 新增或修改类型
1. **权威位置**：`core/types/` 下的对应子模块（`core/types/messages.py`、`core/types/setting.py` 等）。
2. 内部模块和测试都应从 `core/types/` 下的对应子模块导入（如 `core.types.agent_config`、`core.types.messages`），不要再走已删除的 `<module>/types.py`，也不依赖 `core/types/__init__.py` 的顶层重导出。

### 添加新工具
1. 在 `~/.nova/agent/tools/`（全局）或 `<cwd>/.nova/tools/`（项目级）下创建新的 tool 目录，包含 `schema.json`（schema）和 `executor.py`（实现 `ToolExecutor` 类）。
2. 工具加载统一由 `core/resources/loaders/tools.py` 的 `ToolLoader` 完成；`DefaultResourceLoader.get_tools()` 在 `reload()` 时自动加载全局与项目级工具。
3. `core/agent_session/controllers/tools.py` 只保留运行时执行包装 `DynamicTool`，不再负责扫描加载。
4. 如需在 Agent 中默认激活，在对应 Agent 配置的 `tools.json` 中加入 tool name。

### 修改会话条目类型
1. 更新 `core/types/session.py` 中的 Pydantic 模型。
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
