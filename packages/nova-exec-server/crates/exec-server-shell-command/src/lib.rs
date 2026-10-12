//! Shell 检测与 shell 快照工具（executor 执行端面）。
//!
//! 命令解析与安全判定（审批概念，executor 不管）未纳入本 crate；
//! bash.rs 目前只保留快照管线依赖的 `try_parse_shell`。

pub mod shell_detect;
pub mod shell_snapshot;
mod startup;

pub use startup::shell_startup_script;

pub mod bash;
