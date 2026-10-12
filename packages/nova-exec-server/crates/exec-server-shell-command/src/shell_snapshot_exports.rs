// 对位 codex 39f4277087 codex-rs/shell-command/src/shell_snapshot_exports.rs
//! 捕获原生 export 声明与未赋值的 POSIX export。
//! 已赋值的 POSIX 值批量捕获，由 Rust 侧负责引用转义。

use crate::shell_detect::ShellType;

pub(super) fn script(shell_type: ShellType) -> String {
    let script = match shell_type {
        ShellType::Bash => {
            r#"(
  while IFS= read -r __nova_exec_server_snapshot_export_name; do
    case "$__nova_exec_server_snapshot_export_name" in
      ""|[0-9]*|*[!A-Za-z0-9_]*|PWD|OLDPWD) continue ;;
    esac
    RECORD_START
    declare -xp "$__nova_exec_server_snapshot_export_name" 2>/dev/null || true
    RECORD_END
  done < <(compgen -e)
)
"#
        }
        ShellType::Zsh => {
            r#"(
  unsetopt rcquotes
  # 部分 Zsh 构建不含 zsh/parameter 模块。
  for __nova_exec_server_snapshot_export_name in ${(f)"$(typeset +x)"}; do
    case "$__nova_exec_server_snapshot_export_name" in
      ""|[0-9]*|*[!A-Za-z0-9_]*|PWD|OLDPWD) continue ;;
    esac
    case "${(tP)__nova_exec_server_snapshot_export_name}" in
      *readonly*) continue ;;
      *export*) ;;
      *) continue ;;
    esac
    RECORD_START
    typeset -xp "$__nova_exec_server_snapshot_export_name"
    RECORD_END
  done
)
"#
        }
        ShellType::Sh => {
            r#"if export -p >/dev/null 2>&1; then
export -p | __nova_exec_server_snapshot_command awk '
/^(export|declare -x|typeset -x) [A-Za-z_][A-Za-z0-9_]*$/ {
  name=$0
  sub(/^(export|declare -x|typeset -x) /, "", name)
  if (name ~ /^(PWD|OLDPWD)$/) {
    next
  }
  if (!seen[name]++) {
    print name
  }
}' | while IFS= read -r __nova_exec_server_snapshot_export_name; do
  # 只有校验过的标识符进入 eval；已赋值变量来自批量环境捕获。
  if eval '[ "${'"$__nova_exec_server_snapshot_export_name"'+x}" = x ]'; then
    continue
  fi
  # 仅在原生 shell 确认该未赋值 export 已存在时才保留。
  if (
    set +a
    set -- "$__nova_exec_server_snapshot_export_name" "$(export -p)"
    export "$1"
    [ "$2" = "$(export -p)" ]
  ); then
    RECORD_START
    printf 'export %s\n' "$__nova_exec_server_snapshot_export_name"
    RECORD_END
  fi
done
fi
"#
        }
        ShellType::PowerShell | ShellType::Cmd => return String::new(),
    };
    script
        .replace(
            "RECORD_START",
            "printf '%s\\0' \"$__nova_exec_server_snapshot_export_name\"",
        )
        .replace("RECORD_END", "printf '\\0'")
}
