<!-- AGENTS.md - Nova Monorepo 项目指南 -->

# Nova —— LLM Agent 构建框架（Monorepo）

> 本文件面向 AI Coding Agent 编写。如果你不了解本项目，请从这里开始阅读。

## 项目概览

Nova 是一个用于构建大语言模型（LLM）智能体的 **Python 单体仓库（monorepo）**。项目采用分层架构，将 LLM 提供商抽象、Agent 核心框架、高阶 SDK、专用 Agent 定义与 TUI 前端拆分为独立的子包，便于按需组合与独立迭代。

- **目标语言**：Python 3.9 – 3.12
- **项目语言**：代码注释与文档主要使用**中文**
- **当前阶段**：Alpha（版本 `0.1.0`）
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
| 开发依赖 | `pre-commit`、`pytest`、`sniffio` |
| 其他关键依赖 | `openai`、`json-repair`、`jsonschema`、`pyyaml`、`filelock` |

**未使用** Mypy、Tox、Makefile、Docker 或 CI/CD（GitHub Actions / GitLab CI）。

---

## Monorepo 结构与包依赖关系

```
nova/
├── packages/
│   ├── nova_ai/        # 统一的 LLM 提供商抽象层
│   ├── nova_agent/     # 事件驱动的异步 Agent 框架（源码包名为 nova_agent）
│   ├── nova_harness/   # 高阶 Agent SDK（会话、压缩、工具链、RPC 服务器）
│   ├── nova_coding_agent/  # 编程 Agent 定义与本地文件系统工具（bash、edit、read、write）
│   ├── nova_team/      # 主从多智能体团队配置（早期 WIP，暂无 pyproject.toml）
│   └── nova-tui/       # TUI 前端（Node.js + TypeScript，JSON-RPC 后端在 nova_harness）
├── README.md
├── CHANGELOG.md
├── .gitignore
└── AGENTS.md           # 本文件
```

### 运行时依赖层次（自下而上）

1. **`nova_ai`** —— 最底层。提供多厂商（OpenAI、Anthropic、Google、Volcengine、GitHub Copilot、Bedrock 等）统一的流式调用、模型注册表、鉴权、消息类型与兼容性层。当前仅有 `api_impls/openai_completions.py` 一个完整实现。
2. **`nova_agent`（`nova_agent`）** —— 核心框架。基于 `nova_ai` 的模型能力，提供 `Agent` 类、事件订阅/发布、`agent_loop` 异步循环、生命周期管理、工具校验与执行。
3. **`nova_harness`** —— 高阶 SDK。基于 `nova_ai` + `nova_agent`，封装 `AgentSession`、会话树（分支/fork/导航）、上下文压缩（Compaction）、资源加载、设置持久化、模型注册表覆盖、内置工具链、JSON-RPC 服务器与包管理器 CLI。
4. **`nova_coding_agent`** —— 专用 Agent。依赖 `nova_harness`（已在 `pyproject.toml` 中声明为 Poetry path 依赖），提供 `coding_agent` 定义与 4 个本地工具（bash、edit、read、write）。
5. **`nova-tui`** —— 终端用户界面。Node.js + TypeScript 前端，基于 `@earendil-works/pi-tui` 渲染；通过 JSON-RPC over stdio 与 `nova_harness.rpc` 通信。`pyproject.toml` 中声明了 `nova-harness` 的 path 依赖，但本包不含 Python 源码。
6. **`nova_team`** —— 团队编排（WIP）。提供 `TeamDefinitor`，支持主从多智能体挂载配置与两级存储（项目级 / 全局）。**尚未配置 `pyproject.toml`**，不可独立安装。

> **依赖声明现状**：
> - `nova-tui` 与 `nova_coding_agent` 已在各自的 `pyproject.toml` 中通过 `path = "../nova_harness", develop = true` 声明了对 `nova_harness` 的依赖。
> - `nova_harness` 已在 `pyproject.toml` 中显式声明 `nova-ai` 与 `nova-agent` 的 Poetry path 依赖。
> - `nova_agent` 与 `nova_ai` 之间的依赖目前主要通过**运行时 import** 实现，未在 `pyproject.toml` 中声明为 Poetry path 依赖。开发时需确保相关包已在同一 Python 环境中安装（`poetry install` 或 `pip install -e`）。

---

## 各子包详细结构

### `nova_ai`（源码包 `nova_ai`）

位于 `packages/nova_ai/src/nova_ai/`：

- `types/` —— 基础类型：消息（`messages.py`）、内容（`content.py`）、枚举（`enums.py`）、用量统计（`model.py`）、兼容性配置（`compat.py`）
- `models/` —— 厂商模型静态数据：`volcengine.py`
- `api_impls/` —— API 协议实现：`openai_completions.py`（当前唯一完整实现）
- `registry/` —— API adapter 注册表（`api_registry.py`）与模型注册表（`model_registry.py`）、内置注册（`builtins.py`）
- `streaming/` —— 流式事件定义（`event_stream.py`）、调用入口（`invoke.py`）
- `utils/` —— 环境变量、JSON 解析、消息转换、流选项、Copilot 辅助、Unicode 代理项清理、上下文溢出检测等
- `utils/` —— 环境变量、HTTP 代理、JSON 解析、消息转换、流选项、Copilot 辅助、Unicode 代理项清理、上下文溢出检测等

包内包含详细的 `docs/` 目录，记录架构设计、开发日志、架构决策记录（ADR）、使用与维护指南、代码约定和 API 参考。

### `nova_agent`（源码包 `nova_agent`）

位于 `packages/nova_agent/src/nova_agent/`：

- `agent.py` —— `Agent` 类，封装状态管理、事件订阅、消息队列与生命周期
- `agent_loop.py` —— 核心异步循环 `agent_loop()` / `agent_loop_continue()`
- `events.py` —— 完整事件类型体系（`AgentStartEvent`、`ToolExecutionStartEvent` 等）与核心数据类
- `proxy.py` —— 空文件（占位）
- `signal.py` —— `AbortSignal` 异步取消信号
- `utils.py` —— 工具调用校验与参数验证（基于 `jsonschema`）

### `nova_harness`（源码包 `nova_harness`）

位于 `packages/nova_harness/src/nova_harness/`：

- `sdk/` —— 入口工厂 `create_agent_session()`，负责初始化所有子系统；`high_level.py` 支持按名称启动预装 Agent
- `config.py` —— 全局常量与路径配置（`APP_NAME`、`CONFIG_DIR_NAME` 等）
- `messages.py` —— 自定义消息类型与 `convert_to_llm()` 转换器
- `subscribe.py` —— Agent 事件打印示例（调试用）
- `agent/` —— `AgentSession` 核心实现、自定义事件、配置 Pydantic 模型
- `session/` —— 会话持久化（JSONL）、会话树管理、分支与 fork
- `compaction/` —— 上下文压缩：token 估算、摘要生成、增量更新、分支摘要
- `system_prompt/` —— 系统提示词构建：`SystemPromptManager` + `SystemPromptBuilder` + `loader`（Agent 配置加载）
- `resource/` —— 公共资源诊断与工具函数；`resource/loaders/` 管理核心调度器，`resource/adapters/` 管理各模块 loader 的 resource 调用层
- `model_registry/` —— 自定义模型注册、鉴权存储（`auth.json`）、配置解析与环境变量引用
- `setting/` —— 双层设置管理：全局（`~/.nova/agent/settings.json`）+ 项目级（`<cwd>/.nova/settings.json`）
- `tools/` —— 内置工具：本地命令、读写文件、技能库访问、前端消息发送等
- `rpc/` —— JSON-RPC over stdio 服务器（`nova-harness-rpc` CLI 入口）
- `package_manager/` —— 包管理器 CLI（`nova-pkg` 入口），支持 install / uninstall / list / validate
- `utils/` —— API key 解析、异步 sleep（支持 `AbortSignal`）

### `nova_coding_agent`（源码包 `nova_coding_agent`）

位于 `packages/nova_coding_agent/`：

- `src/nova_coding_agent/sdk.py` —— `create_coding_agent_session()` 与 `install_coding_agent()`
- `definitions/coding_agent/` —— Agent 定义元数据（`description.md`、`setup.md`、`sections/role.md`、`tools.json`）
- `tools/bash/`、`tools/edit/`、`tools/read/`、`tools/write/` —— 各含 `executor.py`、`schema.json`、`package.json`

### `nova-tui`

位于 `packages/nova-tui/src/tui/`：

- `main.ts` —— CLI 入口（commander），处理 `nova` 与 `nova pkg` 子命令
- `app.ts` —— `NovaTUI` 类，生命周期、布局、会话管理
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
```

### `nova_team`（源码包 `nova_team`）

位于 `packages/nova_team/src/nova_team/team/`：

- `definitor.py` —— `TeamDefinitor`，动态合并配置、状态修改与保存
- `types.py` —— `SubagentMountEntry`、`MasterMountEntry` 等 dataclass
- `storage/` —— 两级存储后端抽象：`base.py`、`file.py`（基于 `filelock`）、`memory.py`

该包**没有 `pyproject.toml`**，也未声明 Poetry 依赖，属于早期开发状态。

---

## 构建与开发命令

### Python 子包通用流程

每个含 `pyproject.toml` 的子包都是独立的 Poetry 项目：

```bash
cd packages/<子包名>
poetry install
```

### 格式化

```bash
cd packages/<子包名>
poetry run black src/
poetry run isort src/
```

### 构建与发布

```bash
cd packages/<子包名>
poetry build      # 生成 wheel / sdist
poetry publish    # 如需发布到 PyPI
```

### 可执行脚本（由 `nova_harness` 注册）

安装 `nova_harness` 后，环境中会新增以下命令：

```bash
nova-harness-rpc          # 启动 JSON-RPC over stdio 服务器
nova-pkg list             # 列出已安装的包/定义/工具
nova-pkg install <path> --kind agent|definition|tool
nova-pkg uninstall <name> --kind agent|definition|tool
nova-pkg validate <path>
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
- **数据建模**：使用 `pydantic.BaseModel`（`NovaBaseModel` 基类），配置 `validate_assignment=True`；`model_dump()` 默认 `mode="json"`，Enum 字段会序列化为字符串
- **类型注解**：代码中已大量使用类型注解，但未配置 `mypy` 静态检查

---

## 测试说明

- 所有包含 `pyproject.toml` 的子包均已将 `pytest` 声明为开发依赖。
- `nova_ai` 包含 `tests/` 目录，当前 198 个测试全部通过。
- `nova_agent` 包含 `tests/` 目录，当前 112 个测试全部通过。
- `nova_harness` 已新增 `tests/` 目录，当前 22 个测试通过，其中包含需 `VOLCENGINE_API_KEY` 的真实 LLM 集成测试。
- 建议继续按模块结构补充测试：

```
packages/nova_ai/tests/
packages/nova_agent/tests/
packages/nova_harness/tests/
```

运行方式：

```bash
cd packages/<子包名>
poetry run pytest
poetry run pytest --cov=<包名> --cov-report=html
```

---

## 安全注意事项

1. **API Key 存储**
   - `nova_harness` 的鉴权信息保存在 `~/.nova/agent/auth.json`，由 `AuthStorage` 管理。
   - `models.json` 支持通过 `"${ENV_VAR}"` 语法引用环境变量，解析逻辑在 `model_registry/resolve.py`。
   - `nova_ai` 层不持久化密钥，全部通过环境变量按 `provider` 名称映射读取。

2. **会话数据**
   - 会话历史以 **JSONL 明文**存储在 `~/.nova/agent/sessions/--<cwd>--/` 下，可能包含敏感代码片段或输出。
   - 根目录 `.gitignore` 已忽略 `sessions/` 与 `*.session`。

3. **文件操作安全**
   - Agent 配置加载器（`resource/adapters/agent_config.py`）在加载 `user/` 等用户自定义章节时已做基础路径校验（禁止 `..` 与绝对路径），但生产环境仍需额外加固。

5. **Notebook 中的敏感信息**
   - 历史 notebook 文件 `packages/nova_agent/src/test.ipynb` 已删除。新增示例 `packages/nova_harness/examples/01-quickstart.ipynb` 中不应包含真实 API Key。

---

## 开发惯例与给 AI Agent 的提示

- **修改前请先确认所属子包**：不同子包有独立的 `pyproject.toml` 与依赖，不要混用。
- **不要假设有测试**：当前没有测试覆盖，修改关键逻辑后建议手动验证或补充测试。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文，与现有代码一致。
- **序列化层**：若需新增数据类，请继承 `NovaBaseModel`（基于 `pydantic.BaseModel`）。
- **依赖新增**：若引入新的第三方库，需在对应子包的 `pyproject.toml` 的 `[tool.poetry.dependencies]` 中声明，并执行 `poetry lock`（如有 lock 文件）。
- **路径约定**：
  - 全局配置根目录默认：`~/.nova/agent`
  - 项目级配置目录：`<cwd>/.nova`
  - 会话目录：`~/.nova/agent/sessions/--<cwd>--/`
- **`nova-tui` 与 `nova_team`** 为早期实现，修改时请保持最小侵入，避免破坏上层 `nova_harness` 的既有接口。
- **子包级 AGENTS.md**：`nova_ai` 与 `nova_harness` 各自包含更详细的包级指南，深入修改这两个包时建议优先阅读对应文件。

---

## 版本与变更

- 当前版本：`0.1.0`（Alpha）
- 变更日志：根目录 `CHANGELOG.md` 记录了仓库级变更；各子包的 `CHANGELOG.md` 目前为空。
