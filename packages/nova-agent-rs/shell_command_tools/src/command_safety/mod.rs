// PowerShell subprocess 解析器仅保留为测试 oracle，不参与生产命令分类（对齐 codex 上游：
// 生产路径一律走 tree-sitter 进程内解析，见 powershell_tree_sitter.rs）。
#[cfg(test)]
#[allow(dead_code)]
mod powershell_parser;
mod powershell_tree_sitter;

pub mod is_dangerous_command;
pub mod is_safe_command;
#[cfg(windows)]
pub(crate) mod windows_safe_commands;
pub(crate) use powershell_tree_sitter::try_parse_powershell_commands;
