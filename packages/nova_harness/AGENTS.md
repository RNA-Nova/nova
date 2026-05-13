<!-- From: /root/nova/packages/nova_harness/AGENTS.md -->
# nova_harness — Agent SDK 项目指南

> 本文件面向 AI Coding Agent 编写。如果你不了解本项目，请从这里开始阅读。

## 项目概览

`nova_harness` 是 Nova monorepo 中的高阶 Agent SDK，建立在 `nova_ai` + `pi_agent` 之上。它为 LLM 驱动的智能体提供：

- **AgentSession**：封装底层 `Agent`，提供自动重试、模型切换、会话持久化。
- **会话树管理**：支持分支（branch）、fork、导航与会话统计。
- **上下文压缩（Compaction）**：通过 LLM 生成摘要，自动或手动缩减 token 占用。
- **资源加载**：提示词模板、诊断与资源冲突检测。
- **设置持久化**：本地 JSON 存储用户设置与模型配置（支持全局/项目级作用域）。
- **远程计算**：`ComputexManager` + `RemoteCommandTool` / `RemoteReadTool` / `RemoteWriteTool` / `RemoteSkillTool`，通过 XML-RPC 连接远程主机执行命令与文件操作。
- **工具链**：内置工具的注册与运行时白名单控制（`SendTool` 等）。

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
- 使用 `dataclass` + `mashumaro` 的 `DataClassJSONMixin` 做数据序列化（替代传统 Pydantic 或字典）。

---

## 项目结构

```
nova_harness/
├── pyproject.toml              # Poetry 配置、依赖、工具设置
├── README.md                   # 面向人类开发者的简介
├── CHANGELOG.md                # 变更日志（当前为空）
├── .gitignore                  # 忽略 pycache、venv、poetry.lock、本地会话等
└── src/nova_harness/
    ├── __init__.py             # 空包入口
    ├── sdk.py                  # 主入口：create_agent_session() 工厂函数
    ├── config.py               # 全局常量与路径配置（APP_NAME、CONFIG_DIR_NAME 等）
    ├── messages.py             # 自定义消息类型与 convert_to_llm() 转换器
    ├── subscribe.py            # Agent 事件打印示例（调试用）
    ├── agent/                  # AgentSession 核心实现
    │   ├── agent.py            # AgentSession 类（事件、工具、模型、压缩、重试）
    │   ├── events.py           # 自定义事件类型（AutoCompaction、AutoRetry）
    │   └── options.py          # AgentSessionConfig、PromptOptions 等配置 dataclass
    ├── session/                # 会话持久化与树管理
    │   ├── manager.py          # SessionManager（JSONL 读写、分支、fork）
    │   ├── types.py            # 会话相关 dataclass（SessionHeader、SessionEntry 等）
    │   ├── utils.py            # 会话工具函数（ID 生成、文件加载、上下文构建）
    │   ├── builders.py         # 会话树构建、分支会话创建
    │   ├── models.py           # 异步会话列表查询
    │   └── constants.py        # CURRENT_SESSION_VERSION 等常量
    ├── compaction/             # 上下文压缩
    │   ├── compaction.py       # 核心压缩逻辑（摘要生成、cut point、token 估算）
    │   ├── types.py            # CompactionSettings、CompactionResult 等
    │   ├── utils.py            # 文件操作提取、序列化、系统提示词
    │   └── branch_summarization.py  # 分支摘要生成
    ├── computex/               # 远程计算管理
    │   └── manager.py          # ComputexManager（XML-RPC 代理缓存）
    ├── definition/             # Agent 定义与系统提示词构建
    │   ├── definitor.py        # AgentDefinitor（加载 description/sections/tools/user）
    │   ├── loader.py           # 文件系统加载器（Markdown、JSON）
    │   ├── render.py           # 渲染系统提示词（支持工具白名单）
    │   └── types.py            # AgentConfig、Section、ToolInfo、DynamicContext
    ├── model_registry/         # 模型注册表
    │   ├── registry.py         # ModelRegistry（内置+自定义模型、provider 覆盖）
    │   ├── storage.py          # AuthStorage（auth.json 读写）
    │   ├── resolve.py          # 配置值解析（环境变量、文件引用）
    │   ├── helpers.py          # 模型覆盖辅助函数
    │   └── types.py            # ModelsConfig、ProviderOverride 等
    ├── resource/               # 资源加载
    │   ├── loader.py           # DefaultResourceLoader（提示词模板去重）
    │   ├── prompt_templates.py # 提示词模板加载逻辑
    │   ├── diagnostics.py      # 资源诊断类型
    │   ├── types.py            # DefaultResourceLoaderOptions
    │   └── utils.py            # 提示词模板展开工具
    ├── setting/                # 设置管理
    │   ├── manager.py          # SettingsManager（全局/项目级设置合并）
    │   ├── storage.py          # FileSettingsStorage / InMemorySettingsStorage
    │   ├── types.py            # Settings、CompactionSettings、RetrySettings 等
    │   └── utils.py            # deep_merge_settings
    ├── tools/                  # 内置工具
    │   ├── command.py          # RemoteCommandTool（远程 bash/python 执行）
    │   ├── read.py             # RemoteReadTool（远程文件读取）
    │   ├── write.py            # RemoteWriteTool（远程文件写入）
    │   ├── skill_tool.py       # RemoteSkillTool（技能库访问）
    │   ├── send.py             # SendTool（向前端发送消息）
    │   ├── remote_tool.py      # RemoteTool 抽象基类
    │   └── __init__.py         # create_all_tools() 工厂
    └── utils/                  # 通用工具
        ├── resolve.py          # API key 解析
        └── sleep.py            # 异步 sleep（支持 AbortSignal）
```

---

## 核心模块职责

### 1. `sdk.py` — 入口工厂
提供 `create_agent_session(options)` 异步函数，负责：
- 解析/创建 `agent_dir`（默认 `~/.nova/agent`）与 `session_dir`。
- 初始化 `SessionManager`、`SettingsManager`、`ComputexManager`、`ModelRegistry`、`AuthStorage`。
- 构建 `Agent` 实例（来自 `pi_agent`），配置默认模型（当前硬编码为 `volcengine/deepseek-r1-250528`）。
- 组装 `AgentSessionConfig` 并返回 `AgentSession`。

### 2. `agent/agent.py` — AgentSession
- 事件订阅/发布（`subscribe` / `_emit`）。
- 工具注册与白名单（`set_active_tools_by_name`）。
- 消息处理：`prompt`、`steer`、`follow_up`、`send_custom_message`、`send_frontend_message`、`send_inter_agent_message`。
- 会话管理：`new_session`、`switch_session`、`fork`、`navigate_tree`。
- 模型管理：`set_model`、`cycle_model`。
- 思考级别：`set_thinking_level`、`cycle_thinking_level`。
- 自动压缩：`_check_compaction`、`_run_auto_compaction`。
- 自动重试：`_handle_retryable_error`。

### 3. `session/manager.py` — SessionManager
- 会话持久化为 **JSONL** 文件，存储在 `~/.nova/agent/sessions/--<cwd>--/` 下。
- 支持分支（`branch`、`branch_with_summary`）、fork（`create_branched_session`）。
- 条目类型：`message`、`thinking_level_change`、`model_change`、`compaction`、`label`、`custom_message`、`inter_agent_message`、`frontend_message`、`session_info` 等。

### 4. `compaction/compaction.py` — 上下文压缩
- 基于 token 估算（字符数 / 4 的启发式算法）与模型 `context_window` 判断是否触发压缩。
- 使用 LLM 生成结构化摘要（`_SUMMARIZATION_PROMPT`），支持增量更新（`_UPDATE_SUMMARIZATION_PROMPT`）。
- 提取文件操作（read/modified）并附在摘要中。

### 5. `computex/manager.py` — ComputexManager
- 维护 XML-RPC `ServerProxy` 缓存。
- 通过 `set_proxy(host, port)` 切换当前远程主机。
- 默认主机 `127.0.0.1:50001`。

### 6. `definition/` — Agent 定义
- 从 `<cwd>/.nova/definition/` 目录加载 `description.md`、`sections/`、`tools.json`、`setup.md`、`user/`。
- `AgentDefinitor.build_system_prompt()` 组装系统提示词，支持 `selected_tools` 白名单过滤。

### 7. `model_registry/registry.py` — ModelRegistry
- 加载内置模型（来自 `nova_ai`）与自定义模型（`models.json`）。
- 支持 provider 级别覆盖（`base_url`、`headers`、`api_key`）与 per-model 覆盖。
- 动态 provider 注册（`register_provider` / `unregister_provider`）。

### 8. `setting/manager.py` — SettingsManager
- 双层设置：**全局**（`~/.nova/agent/settings.json`）与 **项目级**（`<cwd>/.nova/settings.json`）。
- 字段级 dirty tracking，延迟写入（`flush()`）。
- 涵盖 retry、compaction、terminal、image、computex 等设置。

---

## 测试

当前仓库**没有测试目录或测试文件**，但 `pyproject.toml` 已声明 `pytest` 作为开发依赖。添加测试的建议：

```bash
# 运行测试
poetry run pytest

# 生成覆盖率报告
poetry run pytest --cov=nova_harness --cov-report=html
```

建议在 `tests/` 目录下按模块结构组织测试：

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
- 解析逻辑在 `model_registry/resolve.py` 中实现。

### 远程连接
- `ComputexManager` 使用 HTTP XML-RPC（非加密），默认 `127.0.0.1:50001`。
- 生产环境若跨网络使用，建议增加 TLS 或 VPN 保护。

### 会话文件
- 会话历史以 **JSONL** 明文存储，可能包含敏感代码片段或输出。
- 存储路径：`~/.nova/agent/sessions/--<cwd>--/`。
- `.gitignore` 已忽略 `sessions/` 与 `*.session`。

### 文件操作安全
- `RemoteReadTool` / `RemoteWriteTool` 通过 XML-RPC 在远程主机执行，无本地沙箱限制。
- `AgentDefinitor.add_user_section()` 会校验路径（禁止 `..` 与绝对路径）。

---

## 常见开发任务

### 添加新工具
1. 在 `tools/` 下创建新类，继承 `RemoteTool`（远程）或 `AgentTool`（本地）。
2. 实现 `execute(self, tool_call_id, params, signal, on_update)` 方法。
3. 在 `tools/__init__.py` 的 `create_all_tools()` 中注册。

### 修改会话条目类型
1. 更新 `session/types.py` 中的 dataclass。
2. 在 `session/manager.py` 中添加 `append_xxx()` 方法。
3. 在 `session/utils.py` 的解析逻辑中处理新类型。
4. 在 `messages.py` 的 `convert_to_llm()` 中映射到 LLM 消息。

### 调整压缩策略
- 修改 `compaction/compaction.py` 中的 `estimate_tokens()` 启发式算法。
- 或调整 `setting/manager.py` 中的默认 `DEFAULT_COMPACTION_RESERVE_TOKENS` / `DEFAULT_COMPACTION_KEEP_RECENT_TOKENS`。

### 修改系统提示词结构
- 编辑 `definition/render.py` 中的 `compose_system_prompt()` 与 `render_xxx()` 函数。
- 或修改 `definition/loader.py` 加载的文件命名规则。

---

## 依赖注意事项

- **`nova-ai`** 与 **`nova-agent`** 为本地路径依赖（monorepo 内其他包），不在 PyPI 上发布。
- `mashumaro` 用于 dataclass 的 JSON 序列化/反序列化，是类型系统的核心。
- `json-repair` 用于修复 LLM 返回的破损 JSON（主要在 `SendTool` 中使用）。

---

## 版本与作者

- 当前版本：`0.1.0`（Alpha）
- License：MIT
- 作者：Liujinming
