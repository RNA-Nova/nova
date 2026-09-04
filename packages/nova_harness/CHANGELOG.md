# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [0.1.0] - 2026-09-04

首个封版。

### Added

- **AgentSession 运行时核心**：封装 `Agent`，提供会话持久化、自动重试（指数退避 + 上下文溢出恢复）、模型与思考级别切换、工具激活集控制、steering / follow-up 双消息队列、事件订阅（`subscribe` 返回退订函数）。
- **工厂层**：`create_agent_session()` / `create_agent_session_runtime()` / `create_agent_session_by_name()` / `list_installed_agents()`；`AgentSessionServices` 服务容器装配 settings、模型运行时、资源加载器与扩展系统。
- **会话树**：JSONL 落盘（`~/.nova/agent/sessions/--<cwd>--/`，文件版本 3），条目类型 `message` / `thinking_level_change` / `model_change` / `compaction` / `branch_summary` / `label` / `session_info` / `custom` / `custom_message`；支持分支（可附 LLM 摘要）、fork、树内导航、会话切换/克隆/导出/导入与统计（`get_session_stats` / `get_context_usage`）。
- **上下文压缩（Compaction）**：`reserve_tokens` 阈值触发判定（`enabled` / `reserve_tokens: 16384` / `keep_recent_tokens: 20000` 可配），LLM 生成结构化摘要并支持增量更新，自动提取压缩区间文件操作清单；手动 `compact()` 与自动压缩双路径；分支摘要走同一引擎。
- **模型运行时（ModelRuntime）**：内置 provider → `models.json` → 扩展注册三层合成；credential-blind（密钥不进 `Model`，请求时经鉴权链解析）；动态模型目录刷新（启动 15 秒预算）与 `NOVA_OFFLINE` 离线缓存（`models-store.json`）；运行时凭据管理与 OAuth 登录直通 `nova-ai`。
- **设置持久化**：全局 `~/.nova/agent/settings.json` 与项目级 `<cwd>/.nova/settings.json` 双层，`SettingsManager` 唯一写门（字段级 dirty 追踪 + 后台写队列）；配置值支持 `$VAR` / `!cmd` 引用解析。
- **七类资源加载**：agents / tools / skills / extensions / prompts / user_tools / personas，包分发 + user/project 两级散养目录（`<base>/backend/*` 与共享平级 `agents/`）+ `.agents/skills` 通道，优先级裁决、同名遮蔽诊断与来源跟踪；`role_boundary`（open/strict）角色边界与 CapabilitySelection 归因报告。
- **包管理器 `nova-pkg`**：path / git / npm 三种来源的 list / install / uninstall / update / info / validate / init；npm semver range 全形态；安装事实以 `*.dist-info/`（PEP 610 风格）快照记录；`uv pip` 优先、`python -m pip` 兜底的依赖安装；`--editable` 原地引用；`--local` 项目级存储；`--json` 机器可读输出。
- **包清单契约**：`[tool.nova]` 七类资源类目 + `auto_install_dependencies` + 三档二进制依赖（`binary_dependencies` / `binary_managed_dependencies` / `binary_system_dependencies`）+ `requires` 包间依赖校验；B 型纯 TS 包以 `package.json` 顶层 `"nova"` 段声明。
- **JSON-RPC 服务器（`nova-harness-rpc`）**：stdio / WebSocket 双传输，76 个方法分 8 个域（session / model / auth / resources / settings / user_tools / system / package）；连接一等公民（多客户端、事件广播、背压分流、`cancelRequest` 按连接隔离）；WS 鉴权三守则（bearer token、非 loopback 必须显式 token、Origin 白名单）；`rpc-server.json` 监听信息落盘（0600）。
- **Project Trust**：项目级资源加载前的信任决议链（显式覆盖 → 无待门控资源 → 扩展裁决 → `trust.json` 持久化 → `default_project_trust` 设置 → 无 UI 默认不信任 → UI 选择框），`session.trust_project()` 运行中翻转；包管理不做 trust 检查。
- **扩展系统**：单文件 / 目录形态，工厂函数（`extension` / `load`）装载期收到 `NovaExtensionAPI` 声明式注册（事件、命令、快捷键、flag、provider、spawn hook）；运行期动作经 `ExtensionContext` 注入（消息、exec、工具集、模型、压缩、trust 判定、`ui` / `has_ui` 等）；会话生命周期、工具调用、provider 请求、turn 边界等全量事件钩子。
- **UI 桥接**：`UIContext` 泛型反向原语通道（`capabilities` / `has_capability` / `request` / `notify`，零交互词汇——词汇定义权归包）；`NoOpUIContext` headless 降级；RPC 反向通道 `ui/request` / `ui/response` + `system/capabilities` 按连接寻址；工具执行期 UI 句柄经 `ToolExecContext.ui` 注入（弹窗串行锁与 abort 竞速内建）。
- **CLI（`nova-harness run`）**：print 模式非交互执行 agent 任务，text / JSONL 双输出形态，`--trust` / `--no-session` / `--skill` / `--prompt-template` / `--tools` / `--exclude-tools` 选项。

### Fixed

- **auto-compaction 重试路径崩溃**：压缩重建状态时，已落盘的截断回复（`stop_reason="length"`）可能随保留窗口还原为 assistant 尾，`continue_()` 拒从 assistant 尾续跑抛 `RuntimeError`；收尾守卫由只剥 `error` 尾扩为 `error` / `length` 同剥（此前潜伏——大窗口模型下阈值压缩实际不触发），新增回归单测与 PTY 端到端加固。
- **无摘要导航的 SessionTreeEvent 校验崩溃**：`navigate` 未生成摘要时给 `from_extension`（`bool`）传 `None`，校验爆炸导致导航半完成（leaf 已迁移但事件发射崩溃、RPC -32603、前端转录不同步）；改传 `False`。新增 `TreeNavigator` 编排层单测套件 12 例与 PTY 端到端导航/分叉加固（`pty-tree-navigate.py`）。
- **executor 时代残留 API 清除**：`ExecutorSettings` / `ExecutorEndpoint` 模型与 settings `executor` 键、`SettingsManager` 端点注册三方法、扩展动作三字段（`get_executor_settings` / `register_executor_endpoint` / `unregister_executor_endpoint`）、`ToolSettingsView.get_executor_settings`、会话环境段的 `executor_backend` 条目恢复链与 `DynamicContext.environment_id`——本发布线纯本地执行，以上全部为死表面。
