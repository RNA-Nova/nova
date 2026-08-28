//! Shell 检测与 shell 快照工具（executor 执行端面）。
//!
//! 命令解析与安全判定（审批概念，executor 不管）已存档至
//! `packages/nova-agent-rs/shell_command_tools/`——将来客户端层 Rust 化时启用。

pub mod shell_detect;
pub mod shell_snapshot;
