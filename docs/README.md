# Nova 文档

Nova 是构建与运行 LLM 智能体的框架 + 产品：Python 分层框架（`nova_ai` / `nova_agent` / `nova_harness`）+ 终端前端（`nova` TUI）+ 官方 bundle（`nova-base` 会话基础设施 / `nova-coding-agent` 编程能力）。

## 文档地图

### 上手

- [安装与升级](installation.md)——全部安装渠道（curl 管道 / 手动归档 / pip+npm / 源码）、卸载、版本自检
- [快速上手](quickstart.md)——从安装到第一轮对话、配模型、跑第一个任务

### 使用指南（guide/）

- [TUI 界面](guide/tui.md)——界面构成、键位、面板、主题、状态区
- [Slash 命令](guide/commands.md)——全部命令逐项参考（按域分组）
- [模型与鉴权](guide/models.md)——provider、OAuth、API key、scoped 模型池、thinking 级别
- [会话与分支](guide/sessions.md)——会话树、fork/tree/navigate/resume、导入导出
- [包管理](guide/packages.md)——安装/卸载/更新、三种来源、包间依赖、信任门控
- [配置参考](guide/configuration.md)——settings 键表、目录布局、环境变量
- [自动化与集成](guide/automation.md)——print 一次性执行、nova-server、CI 用法

### Bundle 开发（bundles/）——详细

- [概览与生命周期](bundles/README.md)——什么是 bundle、A/B 型包、安装/发现/加载全链路
- [包清单 `[tool.nova]`](bundles/manifest.md)——七类资源声明、三态名单、二进制依赖、requires
- [工具开发](bundles/tools.md)——`Tool` 类契约、上下文注入、UI 原语、执行模式
- [扩展开发](bundles/extensions.md)——事件面、ExtensionContext 动作面、命令/flag/快捷键注册
- [前端渲染器](bundles/frontend.md)——工具卡片/对话框/条目渲染器、宿主共享件、主题
- [Agent 组合声明与人格](bundles/agents.md)——agents/*.yaml、persona、prompts、skills
- [分发与发布](bundles/distribution.md)——path/git/npm 三源、版本语义、官方包要求
- [完整教程](bundles/tutorial.md)——从零写一个可安装的 bundle（端到端）

### 参考（reference/）

- [CLI 参考](reference/cli.md)——`nova` / `nova-server` / `install.sh` 全旗标
- [环境变量](reference/env-vars.md)——全部 `NOVA_*` 旋钮
- [RPC 契约](reference/rpc.md)——前后端线上协议、版本语义、schema 导出

### 更多

- 架构与代码约定：仓库根 [AGENTS.md](../AGENTS.md)（面向框架开发者）
- 变更历史：[CHANGELOG.md](../CHANGELOG.md)
