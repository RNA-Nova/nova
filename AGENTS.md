<!-- AGENTS.md - Nova Monorepo 项目指南 -->

# Nova —— LLM Agent 构建框架（Monorepo）

> 本文件面向 AI Coding Agent 编写。如果你不了解本项目，请从这里开始阅读。

## 项目概览

Nova 是一个用于构建大语言模型（LLM）智能体的 **Python 单体仓库（monorepo）**。项目采用分层架构，将 LLM 提供商抽象、Agent 核心框架、高阶 SDK、专用 Agent 定义与 TUI 前端拆分为独立的子包，便于按需组合与独立迭代。

- **目标语言**：Python `>=3.9,<3.13`
- **项目语言**：代码注释与文档主要使用**中文**
- **当前阶段**：Alpha（版本 `0.1.0`，其中 `nova-coding-agent` bundle 为 `1.0.0`）
- **License**：MIT
- **作者**：Liujinming

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python `>=3.9,<3.13`；`nova-tui` 前端额外需要 Node.js `>=20.0.0` |
| 包管理器 | **Poetry**（各子包独立管理）；`nova-tui` 同时使用 **npm** |
| 格式化 | `black`（目标语法版本 `py311`） |
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
│   ├── nova_harness/       # 高阶 Agent SDK（会话、压缩、工具链、RPC 服务器、Project Trust、UI 桥接）
│   ├── nova_coding_agent/  # 官方编程 Agent bundle 与本地文件系统工具
│   ├── nova_team/          # 主从多智能体团队配置（早期 WIP，暂无 pyproject.toml）
│   ├── nova-tui/           # TUI 前端（Node.js + TypeScript，JSON-RPC 后端在 nova_harness）
│   └── nova_web_ui/        # Web UI 占位目录（当前为空）
├── README.md
├── CHANGELOG.md
├── .gitignore
└── AGENTS.md               # 本文件
```

### 运行时依赖层次（自下而上）

1. **`nova_ai`** —— 最底层。提供多厂商（OpenAI、Anthropic、Google、Volcengine、GitHub Copilot 等）统一的流式调用、模型注册表、鉴权、消息类型与兼容性层。当前仅有 `api_impls/openai_completions.py` 一个完整实现。
2. **`nova_agent`（源码包 `nova_agent`）** —— 核心框架。基于 `nova_ai` 的模型能力，提供 `Agent` 类、事件订阅/发布、`agent_loop` 异步循环、生命周期管理、工具校验与执行。
3. **`nova_harness`** —— 高阶 SDK。基于 `nova_ai` + `nova_agent`，封装 `AgentSession`、会话树（分支/fork/导航）、上下文压缩（Compaction）、资源加载、设置持久化、模型注册表覆盖、内置工具链、JSON-RPC 服务器、包管理器 CLI、Project Trust 门控与 `ExtensionUIContext` / RPC UI 桥接。
4. **`nova_coding_agent`** —— 官方 bundle。同时是一个可 import 的 Python 包，依赖 `nova-ai`、`nova-agent`、`nova-harness`（均声明为 Poetry path 依赖），提供 `coding_agent` Agent 定义、`session_commands` 扩展以及 7 个本地工具（bash、edit、find、grep、ls、read、write）。
5. **`nova-tui`** —— 终端用户界面。Node.js + TypeScript 前端，基于 `@earendil-works/pi-tui` 渲染；通过 JSON-RPC over stdio 与 `nova_harness.modes.rpc` 通信。`pyproject.toml` 中声明了 `nova-harness` 的 path 依赖，但本包不含 Python 源码。
6. **`nova_team`** —— 团队编排（WIP）。提供 `TeamDefinitor`，支持主从多智能体挂载配置与两级存储（项目级 / 全局）。**尚未配置 `pyproject.toml`**，不可独立安装。
7. **`nova_web_ui`** —— 当前为空目录，仅为未来 Web UI 占位。

> **依赖声明现状**：
> - `nova_harness` 在 `pyproject.toml` 中显式声明 `nova-ai` 与 `nova-agent` 的 Poetry path 依赖。
> - `nova_coding_agent` 在 `pyproject.toml` 中声明 `nova-ai`、`nova-agent`、`nova-harness` 三个 path 依赖。
> - `nova-tui` 在 `pyproject.toml` 中仅声明 `nova-harness` path 依赖；运行时仍需确保 `nova_ai`、`nova_agent` 已在同一 Python 环境中可导入。

---

## 各子包详细结构

### `nova_ai`（源码包 `nova_ai`）

位于 `packages/nova_ai/src/nova_ai/`：

- `types/` —— 基础类型：枚举（`enums.py`）、内容（`content.py`）、消息（`messages.py`）、用量统计（`model.py`）、兼容性配置（`compat.py`）、流选项（`stream_options.py`）、API adapter 契约（`api_adapter.py`）、`NovaBaseModel` 基类（`base_model.py`）
- `models/` —— 厂商模型静态数据，当前仅有 `volcengine.py`
- `api_impls/` —— API 协议实现：`openai_completions.py`（当前唯一完整实现）
- `registry/` —— API adapter 注册表（`api_registry.py`）、模型注册表（`model_registry.py`）、内置注册（`builtins.py`）
- `streaming/` —— 流式事件定义（`event_stream.py`）、调用入口（`invoke.py`）
- `utils/` —— 环境变量、JSON 解析、消息转换、流选项、Copilot 辅助、Unicode 代理项清理、上下文溢出检测、模型工具函数等

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

位于 `packages/nova_harness/src/nova_harness/`：

- `cli/` —— 所有 CLI 入口：`nova-harness`（`main.py`）、`nova-pkg`（`package.py`）
- `modes/` —— 运行模式
  - `print/` —— 非交互式命令行运行模式（`nova-harness run`）
  - `rpc/` —— JSON-RPC over stdio 服务器，含 `OutputGuard`、`StdioTransport`、`NovaRpcServer`、`RpcMethods`、`RpcUIContext`
  - `websocket/` —— WebSocket 模式占位（当前仅 `__init__.py`）
- `core/agent_session/` —— `AgentSession` 运行时核心、`AgentSessionRuntime`、`AgentSessionServices`、领域控制器（bash、compaction、events、model、queue、retry、stats、tools、tree）
- `core/harness/` —— 高阶能力：会话持久化与树管理、上下文压缩、系统提示词构建、skills、Project Trust
- `core/resources/` —— 资源发现与加载（`loader.py` 与 `loaders/` 下的 agent_config、extensions、prompt_templates、skills、tools）
- `core/package/` —— Agent / tool / bundle / skill / extension 包管理器核心，含 manifest、resolver、scaffold、deps 等
- `core/config/` —— settings、model registry、auth storage、路径默认值、配置解析
- `core/types/` —— 统一 Pydantic / dataclass 类型层
- `core/utils/` —— 通用工具
- `core/extensions/` —— 扩展系统：API、loader、runner、wrapper、types
- `extensions/subagent/` —— 官方 subagent 扩展

### `nova_coding_agent`（bundle + Python 包）

位于 `packages/nova_coding_agent/`：

- `agents/coding_agent/` —— Agent 定义：
  - `agent.yaml` —— 元数据、工具白名单、扩展白名单
  - `description.md` —— 描述与可选 YAML frontmatter
  - `sections/role.md`、`setup.md`
- `tools/` —— 7 个本地工具，每个子目录含 `executor.py` 与 `schema.json`：
  - `bash` / `edit` / `find` / `grep` / `ls` / `read` / `write`
- `extensions/session_commands/` —— `session_commands` 扩展实现
- `nova_coding_agent/` —— bundle 自身的 Python 包，供 tools 共享辅助模块（`tools_common/`）
- `tests/` —— 工具与扩展的单元测试

该 bundle 的 `pyproject.toml` 中 `[tool.nova]` 段声明：
- `agents = ["./agents/coding_agent"]`
- `tools = ["./tools/bash", "./tools/edit", "./tools/find", "./tools/grep", "./tools/ls", "./tools/read", "./tools/write"]`
- `extensions = ["./extensions/session_commands"]`
- `auto_install_dependencies = true`
- `binary_dependencies = { rg = "ripgrep", fd = "fd-find" }`

### `nova-tui`

位于 `packages/nova-tui/src/tui/`：

- `main.ts` —— CLI 入口（commander），处理 `nova` 与 `nova pkg` 子命令
- `pkg-cli.ts` —— `nova pkg` 前端子命令实现
- `app.ts` —— `NovaTUI` 类，生命周期、布局、会话管理
- `state.ts` —— TUI 状态定义
- `rpc-client.ts` —— `NovaRpcClient`，通过 stdio 启动 Python 子进程进行 JSON-RPC 通信
- `components/` —— 消息渲染、页脚、工具调用卡片、活动指示器等 UI 组件
- `controllers/` —— 键盘、事件处理、流式 UI、历史记录控制器
- `theme/` —— 配色与主题配置

构建命令（npm）：
```bash
cd packages/nova-tui
npm install
npm run build   # tsc -> dist/
npm run dev     # tsx src/tui/main.ts
npm start       # node dist/tui/main.js
npm link        # 全局注册 `nova` 命令
```

### `nova_team`（源码包 `nova_team`）

位于 `packages/nova_team/src/nova_team/team/`：

- `definitor.py` —— `TeamDefinitor`，动态合并配置、状态修改与保存
- `types.py` —— `SubagentMountEntry`、`MasterMountEntry` 等 dataclass
- `storage/` —— 两级存储后端抽象：`base.py`、`file.py`（基于 `filelock`）、`memory.py`、`types.py`

该包**没有 `pyproject.toml`**，也未声明 Poetry 依赖，属于早期开发状态。

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

对于 `nova_coding_agent`，工具代码位于 `tools/`，建议同时格式化：
```bash
pixi run -e dev black packages/nova_coding_agent/src packages/nova_coding_agent/tools
pixi run -e dev isort packages/nova_coding_agent/src packages/nova_coding_agent/tools
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
nova-pkg install <path>
nova-pkg uninstall <name>
nova-pkg update <name>
nova-pkg info <name>
nova-pkg validate <path>
nova-pkg init             # 根据当前目录结构生成 [tool.nova] 段
```

### `nova-tui` 专属命令

```bash
cd packages/nova-tui
npm install
npm run build      # TypeScript 编译到 dist/
npm run dev        # tsx 直接运行
npm start          # node 运行编译产物
npm link           # 全局注册 `nova` 命令
```

---

## 代码风格指南

- **类名**：`PascalCase`
- **函数 / 变量**：`snake_case`
- **常量**：`UPPER_CASE`（如 `APP_NAME = "nova"`）
- **导入排序**：使用 `isort`，配置为 `profile = "black"`、`multi_line_output = 3`、`include_trailing_comma = true`
- **格式化**：`black`，目标版本 `py311`
- **注释与文档字符串**：以**中文**为主，保持与现有代码一致
- **数据建模**：按是否跨越 JSON/文件/RPC 边界选择技术栈。
  - **Pydantic v2 (`NovaBaseModel`)**：用于配置持久化、会话 JSONL、包 manifest、资源诊断报告、前后端 UI 契约、RPC payload 等需要 schema 校验与序列化的 JSON 边界类型。使用原生 `model_dump()` / `model_validate()` / `model_dump_json()` / `model_validate_json()`。
  - **`dataclass`**：用于运行时内部对象、事件 payload、含 `Callable`/服务实例/异常的依赖容器，避免对不可序列化对象触发 Pydantic 校验。
  - 不教条地“优先 dataclass”或“优先 Pydantic”，决策唯一依据是类型是否跨越 JSON 边界。
- **类型注解**：代码中已大量使用类型注解，但未配置 `mypy` 静态检查
- **枚举字段**：在内存中以 `Enum` 对象保存（便于代码中使用 `.value` 和枚举比较），不要依赖 `use_enum_values=True`。
- **不要**对运行时容器（如 `AgentSessionConfig`、`AgentSessionServices`、`ToolDefinition`）使用 Pydantic 序列化，它们可能持有服务实例、`Callable` 等不可 JSON 化的对象。

---

## 测试说明

- 所有包含 `pyproject.toml` 的子包均已将 `pytest` 声明为开发依赖。
- 测试目录结构：
  - `packages/nova_ai/tests/`
  - `packages/nova_agent/tests/`
  - `packages/nova_harness/tests/`
  - `packages/nova_coding_agent/tests/`
- 真实 API 集成测试已用 `pytest.mark.integration` 标记；`nova_ai` 与 `nova_harness` 的集成测试需要 `VOLCENGINE_API_KEY` 等环境变量。
- 已通过 pixi 安装 dev 环境并验证：`nova_ai` 158 个非集成测试通过，`nova_agent` 66 个非集成测试通过。
- `nova_harness` 当前存在若干与 asyncio 事件循环及 package manager 内部实现相关的既有失败用例，与 pixi 环境配置无关；修改关键逻辑后应在对应子包内运行测试并确认结果。

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
   - `models.json` 支持通过 `"${ENV_VAR}"` 语法引用环境变量，解析逻辑在 `core/config/resolve.py`。
   - `nova_ai` 层不持久化密钥，全部通过环境变量按 `provider` 名称映射读取。

2. **会话数据**
   - 会话历史以 **JSONL 明文**存储在 `~/.nova/agent/sessions/--<cwd>--/` 下，可能包含敏感代码片段或输出。
   - 根目录 `.gitignore` 已忽略 `sessions/` 与 `*.session`。

3. **文件操作安全**
   - Agent 配置加载器（`core/resources/loaders/agent_config.py`）在加载 `user/` 等用户自定义章节时**目前未做路径校验**；生产环境应补充对 `..` 与绝对路径的校验。

4. **Project Trust**
   - `~/.nova/agent/trust.json` 保存用户对项目文件夹的信任决策；扩展可通过 `project_trust` 事件参与裁决。
   - 无 UI 的 headless/RPC 模式默认信任存在 `.nova` 资源的项目，以保持向后兼容；有 UI 的前端（如 `nova-tui`）会弹出确认对话框。

5. **敏感信息**
   - 历史 notebook 文件 `packages/nova_agent/src/test.ipynb` 已删除。新增示例 `packages/nova_harness/examples/` 中不应包含真实 API Key。

---

## 开发惯例与给 AI Agent 的提示

- **修改前请先确认所属子包**：不同子包有独立的 `pyproject.toml` 与依赖，不要混用。
- **环境管理**：仓库使用根目录 `pyproject.toml` 中的 `[tool.pixi.*]` 作为统一 workspace。新增或调整依赖时，优先在根 `pyproject.toml` 中声明，以便所有子包共享同一环境。
- **不要假设测试一定通过**：`nova_ai` 与 `nova_agent` 非集成测试当前通过；`nova_harness` 存在若干既有失败用例。修改关键逻辑后建议手动验证或补充测试。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文，与现有代码一致。
- **序列化层**：若需新增 JSON 边界数据类，请继承 `NovaBaseModel`（基于 `pydantic.BaseModel`）；运行时内部对象使用 `dataclass`。
- **依赖新增**：
  - 若新增**第三方库**，优先在根 `pyproject.toml` 的 `[tool.pixi.pypi-dependencies]`（运行时）或 `[tool.pixi.feature.dev.pypi-dependencies]`（开发时）中声明，然后执行 `pixi install -e dev`。
  - 各子包仍保留 Poetry 配置作为兼容；如使用 Poetry，需在对应子包 `pyproject.toml` 的 `[tool.poetry.dependencies]` 中声明并执行 `poetry lock`（如有 lock 文件）。
- **路径约定**：
  - 全局配置根目录默认：`~/.nova/agent`
  - 项目级配置目录：`<cwd>/.nova`
  - 会话目录：`~/.nova/agent/sessions/--<cwd>--/`
  - Project Trust 记录：`~/.nova/agent/trust.json`
- **`nova-tui` 与 `nova_team`** 为早期实现，修改时请保持最小侵入，避免破坏上层 `nova_harness` 的既有接口。
- **子包级 AGENTS.md**：`nova_ai` 与 `nova_harness` 各自包含更详细的包级指南，深入修改这两个包时建议优先阅读对应文件。
- **新增 Agent / 工具 / 扩展**：参考 `nova_coding_agent` 的 `[tool.nova]` 段与目录结构；使用 `nova-pkg init` 可自动生成该段。

---

## 版本与变更

- 当前版本：`0.1.0`（Alpha）；`nova-coding-agent` bundle 版本为 `1.0.0`
- 变更日志：根目录 `CHANGELOG.md` 记录了仓库级变更；各子包的 `CHANGELOG.md` 目前为空。
