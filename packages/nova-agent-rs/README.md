# nova-agent-rs —— agent 运行时的 Rust 备件存档

**这里不是 executor 的一部分。** 这里存放的是 fork 自 codex 时连锅端进 executor 仓库、但
**生产消费方在 agent 层（core）而非执行后端**的 crate/模块。executor（`packages/nova_executor`）
只保留执行端面（进程/fs/PTY/沙箱/传输）。

## 收录内容

- `execpolicy/`——命令审批规则引擎（prefix_rule 语言 + 求值）。executor 不管审批；
  它在 codex 里的消费方是 core。将来 nova 审批体系（`permission_gate` 扩展）若升级为
  可配置规则（用户/项目级规则文件），从这里启用。
- `shell_command_tools/`——命令解析与安全判定备件：bash/PowerShell 命令解析器
  （含 tree-sitter 进程内解析芯）、危险命令判定（fail-closed）、安全命令白名单、
  git 全局选项防绕过。executor 不审批命令；这些为将来客户端层 Rust 化备用。

## 纪律

- 本目录**不进任何 workspace**、不参与构建；代码按存档保管（不保证编译随主仓库绿）。
- 启用时再接入对应 workspace 并修复漂移。
- 上游参照：`/Users/liujinming/agent/codex/codex-rs` 的 `execpolicy/` 与
  `shell-command/`（审批面已被 codex execpolicy 接管，is_safe_command 等白名单 codex 已删）。
