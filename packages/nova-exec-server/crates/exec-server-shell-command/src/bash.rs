// 对位 codex 39f4277087 codex-rs/shell-command/src/bash.rs
// 仅 `try_parse_shell` 对位剥取（bash.rs:13-21）；其余 4 个 pub fn 属命令安全面，挂账不移植。

use tree_sitter::Parser;
use tree_sitter::Tree;
use tree_sitter_bash::LANGUAGE as BASH;

/// 用 tree-sitter-bash 解析给定的 bash 源码，成功返回 Tree，失败返回 None。
pub fn try_parse_shell(shell_lc_arg: &str) -> Option<Tree> {
    let lang = BASH.into();
    let mut parser = Parser::new();
    #[expect(clippy::expect_used)]
    parser.set_language(&lang).expect("load bash grammar");
    let old_tree: Option<&Tree> = None;
    parser.parse(shell_lc_arg, old_tree)
}
