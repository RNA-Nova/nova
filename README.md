<p align="center">
  <img alt="nova 吉祥物" src="docs/assets/mascot.png" width="160">
</p>
<h1 align="center">Nova</h1>
<p align="center">
  <a href="https://github.com/RNA-Nova/nova/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/RNA-Nova/nova/actions/workflows/ci.yml/badge.svg?branch=legacy/0.1.x" /></a>
  <a href="https://github.com/RNA-Nova/nova/releases"><img alt="Release" src="https://img.shields.io/github/v/release/RNA-Nova/nova?style=flat-square" /></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" /></a>
  <img alt="Python >=3.12" src="https://img.shields.io/badge/python-%3E%3D3.12-blue?style=flat-square" />
</p>

Nova 是构建与运行 LLM 智能体的分层框架 + 开箱即用的终端编程助手：底层是统一的多厂商 LLM 抽象与事件驱动的异步 Agent 框架，上层是带会话树、上下文压缩与包/扩展生态的高阶 SDK，配上终端界面（TUI）与官方 bundle——装完即可在终端里驱动一个能读写文件、执行命令、委派子代理的编程助手。

<p align="center">
  <img alt="nova 演示：启动 + 一轮真实工具调用对话" src="docs/assets/hero.gif" width="960">
</p>

- **多厂商 LLM 抽象**：内置 Volcengine / Moonshot AI（国际·国内）/ Kimi Coding，任意 OpenAI 兼容端点可接入；统一流式事件、OAuth 登录与鉴权解析链。
- **事件驱动 Agent 框架**：完整 Agent 循环、四级事件、工具并行/串行执行、`AbortSignal` 取消传播。
- **高阶会话 SDK**：会话树（fork/导航）、上下文压缩、JSONL 持久化、Project Trust、JSON-RPC 服务器与扩展系统。
- **包生态**：工具/扩展/agent 组合/persona/模板等七类资源按包分发，path/git/npm 三源安装，用户级/项目级双作用域。

## 安装

**预编译二进制（推荐，零系统依赖——不需要 Node/Python）：**

```bash
curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh
```

**源码安装**（从源码构建，需 Python `>=3.12,<3.14` + Node `>=22.19`）：

```bash
git clone --depth 1 --branch v0.1.0 https://github.com/RNA-Nova/nova.git
cd nova && sh scripts/install-source.sh
```

pip + npm 渠道（PyPI `nova-harness` / npm `nova-tui`）待发布。全渠道细节、卸载与排错见 [docs/installation.md](docs/installation.md)。

## 文档

**[docs/](docs/README.md) 是完整文档库**——安装矩阵、使用指南（TUI/命令/模型/会话/包管理/配置/自动化）、**bundle 开发详述**（工具/扩展/渲染器契约 + 端到端教程）、参考手册（CLI/环境变量/RPC 契约）。

各子包 README：[nova_ai](packages/nova_ai/README.md)（LLM 抽象层）· [nova_agent](packages/nova_agent/README.md)（Agent 框架）· [nova_harness](packages/nova_harness/README.md)（高阶 SDK）· [nova-tui](packages/nova-tui/README.md)（前端运行时与 TUI）· [nova-coding-agent](bundles/nova_coding_agent/README.md)（官方编程 bundle）

## 包地图

| 包 | 说明 |
|----|------|
| [packages/nova_ai](packages/nova_ai/README.md) | 统一的多厂商 LLM 抽象层：Models 集合 + Provider 运行时 + 流式事件体系 |
| [packages/nova_agent](packages/nova_agent/README.md) | 事件驱动的异步 Agent 框架：`Agent` 类、`agent_loop` 循环、工具执行与生命周期 |
| [packages/nova_harness](packages/nova_harness/README.md) | 高阶 Agent SDK：`AgentSession`、会话树、上下文压缩、包管理器、JSON-RPC 服务器 |
| [packages/nova-tui](packages/nova-tui/README.md) | 前端运行时与 TUI 宿主，`nova` 命令入口 |
| [bundles/nova_base](bundles/nova_base/README.md) | 官方基础 bundle：会话基础设施（21 个 slash 命令、question/todo 工具、UI 原语糖库） |
| [bundles/nova_coding_agent](bundles/nova_coding_agent/README.md) | 官方编程 bundle：8 工具、子代理四件套、执行扩展（requires nova-base） |

运行时依赖自下而上：`nova_ai` → `nova_agent` → `nova_harness` →（`nova-tui` 经 JSON-RPC 驱动 `nova_harness`；bundle 作为已安装包被会话加载）。

## 安全边界

使用前请了解 Nova **不做**什么：

- **权限模型是"询问门"，不是沙箱**：工具以启动用户的权限运行（读写文件、执行命令即你的权限）。`permission_gate` / `plan_mode` 是产品层护栏（危险命令询问、只读模式），不构成安全边界。需要硬隔离请自行容器化运行。
- **会话数据明文落盘**：`~/.nova/agent/sessions/` 下的 JSONL 可能含敏感代码片段与命令输出；分享、备份、同步目录前请自查。
- **鉴权本地存储**：`auth.json` 保存 OAuth token / API key；`models.json` 支持 `$VAR` / `!cmd` 引用避免明文落 key。
- **Project Trust**：项目目录（`.nova/`）的资源加载前有信任门控；无界面（headless/print）模式默认不信任。
- **网络面**：模型调用出网到配置的 provider；包管理按来源出网（git/npm registry）；无遥测外发。

## 供应链

- 发布归档附 `SHA256SUMS`，官方安装器**强制校验**（sha256 不符即拒装）；
- CI / 发布流水线的第三方 action 全部 pin 到 commit SHA；
- 源码可复现构建：`install-source.sh`（或 tag 源码 + `scripts/build-*.sh` 出同款产物）。

## 开发

```bash
pixi install --environment dev   # 统一环境（editable 安装全部子包）

pixi run -e dev test-all         # 全部 Python 测试
cd packages/nova-tui && npm test # 前端测试
```

贡献者指南见 [AGENTS.md](AGENTS.md)（架构、代码约定、测试纪律）；仓库级变更见 [CHANGELOG.md](CHANGELOG.md)；持续集成与发布见 [.github/workflows/](.github/workflows)（三平台 CI 矩阵 + 六平台发布管线）。

## License

MIT
