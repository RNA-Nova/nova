// 对位 codex 39f4277087 codex-rs/shell-command/src/startup.rs
//! 快照捕获与其他初始化 shell 共用的 profile 载入前导段。
//! 保留既有脚本原样；shell 的启动参数与生命周期由调用方负责。

use crate::shell_detect::ShellType;

/// 在 login shell 中载入快照捕获所用的交互式配置。
/// 调用方必须已按惯例带上 login 启动参数启动 shell。
/// 此处仅支持 Bash 与 Zsh；POSIX sh 的 ENV 处理仍留在 capture 中。
pub fn shell_startup_script(shell_type: ShellType) -> &'static str {
    match shell_type {
        ShellType::Zsh => {
            r#"if [[ -n "${ZDOTDIR-}" ]]; then
  rc="$ZDOTDIR/.zshrc"
elif [[ -n "${HOME-}" ]]; then
  rc="$HOME/.zshrc"
else
  rc=
fi
[[ -r "$rc" ]] && . "$rc"
"#
        }
        ShellType::Bash => {
            r#"if [ -z "${BASH_ENV-}" ] && [ -n "${HOME-}" ] && [ -r "$HOME/.bashrc" ]; then
  . "$HOME/.bashrc"
fi
"#
        }
        ShellType::Sh | ShellType::PowerShell | ShellType::Cmd => "",
    }
}
