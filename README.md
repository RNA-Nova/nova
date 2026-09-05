# Nova

<!-- HERO-IMAGE-HERE: docs/assets/hero.gif（启动 + 一轮真实工具调用对话） -->

Nova 是一个用于构建大语言模型（LLM）智能体的分层框架，同时提供开箱即用的编程 Agent：底层是统一的多厂商 LLM 抽象与事件驱动的异步 Agent 框架，上层是带会话树、上下文压缩与包/扩展生态的高阶 SDK，配上终端界面与官方编程 Agent bundle，装完即可在终端里驱动一个能读写文件、执行命令、委派子代理的编程助手。

本仓库为 monorepo，框架各层拆分为独立子包，按需组合、独立迭代。

## 特性

- **多厂商 LLM 抽象**：内置 Volcengine（火山方舟）、Moonshot AI（国际/国内）、Kimi Coding 四家 provider，任意 OpenAI 兼容端点可接入；统一流式事件、思考级别、token 用量与成本统计、OAuth 登录与鉴权解析链。
- **事件驱动的 Agent 框架**：完整的 Agent 循环（消息 → 流式响应 → 工具调用 → 下一轮），agent / turn / message / tool 四级事件，工具校验与并行/串行执行，steering / follow-up 双队列，`AbortSignal` 取消传播。
- **高阶会话 SDK**：`AgentSession` 封装会话树（分支 / fork / 导航）、上下文压缩、会话持久化（JSONL）、设置与模型注册表、Project Trust 门控、JSON-RPC 服务器与扩展系统。
- **包与扩展生态**：`nova-pkg` 包管理器（path / git / npm 三种来源），Agent 组合声明（yaml 纯选配）、工具、用户工具、扩展、persona、prompt 模板七类资源按包分发，用户级 / 项目级双作用域。
- **终端界面**：`nova` 命令启动 TUI——流式渲染、工具卡片、对话框与选择器、主题、会话导出，前后端经 JSON-RPC over stdio 通信。
- **开箱即用的编程 Agent**：官方双 bundle 分层——`nova-base` 提供会话基础设施（/login /model /tree 等 21 个 slash 命令、question/todo 工具、/tools 面板），`nova-coding-agent` 提供 8 个本地工具（bash / read / write / edit / grep / find / ls / subagent）、子代理四件套（scout / planner / reviewer / worker）与执行扩展（权限门、计划模式等）。

## 包地图

| 包 | 说明 |
|----|------|
| [packages/nova_ai](packages/nova_ai/README.md) | 统一的多厂商 LLM 抽象层：Models 集合 + Provider 运行时 + 流式事件体系（PyPI：`nova-ai`） |
| [packages/nova_agent](packages/nova_agent/README.md) | 事件驱动的异步 Agent 框架：`Agent` 类、`agent_loop` 循环、工具执行与生命周期（PyPI：`nova-agent`） |
| [packages/nova_harness](packages/nova_harness/README.md) | 高阶 Agent SDK：`AgentSession`、会话树、上下文压缩、包管理器 CLI、JSON-RPC 服务器（PyPI：`nova-harness`） |
| [packages/nova-tui](packages/nova-tui/README.md) | 前端运行时与 TUI 宿主，`nova` 命令入口（npm：`nova-tui`） |
| [bundles/nova_base](bundles/nova_base/README.md) | 官方基础 bundle：会话产品基础设施（21 个 slash 命令、question/todo 工具、/tools 面板、UI 原语糖库） |
| [bundles/nova_coding_agent](bundles/nova_coding_agent/README.md) | 官方编程 Agent bundle：工具链、子代理、persona 与扩展的组合包（requires nova-base） |

运行时依赖自下而上：`nova_ai` → `nova_agent` → `nova_harness` →（`nova-tui` 经 JSON-RPC 驱动 `nova_harness`；`nova_coding_agent` 作为已安装包被会话加载）。

## 安装

> **完整文档见 [docs/](docs/README.md)**——安装矩阵、使用指南、bundle 开发、参考手册。

**渠道一：预编译二进制（推荐，零系统依赖——不需要 Node/Python）**

```bash
curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh
```

（已验证：下载 → sha256 校验 → 解压 → PATH 指引全链自动化；macOS arm64/x64、Linux x64/arm64 四平台，Windows 手动下载 zip。详见 [docs/installation.md](docs/installation.md)。）

**渠道二：源码安装**（不装预编译二进制，从源码构建；需要 Python `>=3.12,<3.14` + Node.js `>=22.19.0`）

```bash
git clone --depth 1 --branch v0.1.0 https://github.com/RNA-Nova/nova.git
cd nova
sh scripts/install-source.sh
```

（已验证：预检 → venv + pip 四包 → 注册官方双 bundle → npm 构建 → `~/.local/bin/nova` launcher → 双端版本自检。）

**渠道三：pip + npm**（PyPI `nova-harness` / npm `nova-tui`——**待发布**，发布段已在 release workflow 备好，首发演练后开启）

**仓库内开发**（统一 pixi 环境，editable 安装全部子包）：

```bash
pixi install --environment dev

# 终端界面
cd packages/nova-tui
npm install
npm run build
npm link        # 全局注册 nova 命令
```

运行 TUI 时，`nova` 会以 `python3 -m nova_harness.modes.rpc.cli` 启动后端——请确保 `nova_harness` 对同一 Python 环境可导入（可用 `NOVA_PYTHON` 指定后端解释器）。

## 快速上手

配置模型鉴权（以 Volcengine 为例，其余 provider 的环境变量见 [nova_ai 文档](packages/nova_ai/README.md#环境变量)）：

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

启动终端界面并直接发消息：

```bash
nova
```

也可以在会话内用 `/login` 交互式配置认证（Kimi Coding 支持 OAuth 设备码登录，其余 provider 可录入 API key），用 `/model` 切换模型，`/help` 查看全部命令。

## 文档

- [packages/nova_ai/README.md](packages/nova_ai/README.md) —— LLM 抽象层：provider、鉴权、流式事件参考
- [packages/nova_agent/README.md](packages/nova_agent/README.md) —— Agent 框架：事件流、工具、hooks、低层 API
- [packages/nova_harness/README.md](packages/nova_harness/README.md) —— 高阶 SDK 概览
- [packages/nova-tui/README.md](packages/nova-tui/README.md) —— 前端运行时与 TUI 架构
- [bundles/nova_coding_agent/README.md](bundles/nova_coding_agent/README.md) —— 官方编程 Agent bundle
- [CHANGELOG.md](CHANGELOG.md) —— 仓库级变更日志
- [AGENTS.md](AGENTS.md) —— 面向贡献者与 AI Coding Agent 的仓库指南

## 开发

```bash
# 测试（在各子包目录下独立执行，避免 tests 包名冲突）
pixi run -e dev test-ai        # nova_ai
pixi run -e dev test-agent     # nova_agent
pixi run -e dev test-harness   # nova_harness
pixi run -e dev test-coding    # nova_coding_agent
pixi run -e dev test-all

# 格式化全部 Python 源码（black + isort）
pixi run -e dev format
```

持续集成见 [.github/workflows/ci.yml](.github/workflows/ci.yml)：harness 与 bundle 的 Python 测试、TUI 的类型检查与测试在 ubuntu / macOS / windows 三平台矩阵运行，另有 bundle 前端测试、black/isort 风格门禁与仓库大二进制门禁。

## License

MIT
