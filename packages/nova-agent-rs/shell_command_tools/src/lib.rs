//! 存档 crate：命令解析与安全判定备件（自 nova-executor-shell-command 挪出）。
//!
//! executor 不管审批/命令判定——这些模块的生产消费方在 agent 层（将来客户端层
//! Rust 化或审批升级时启用）。不进 workspace、不参与构建。

pub mod bash;
pub mod command_safety;
pub mod parse_command;
pub mod powershell;
