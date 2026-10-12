// 对位 codex 39f4277087 codex-rs/shell-command/src/shell_snapshot.rs
//! 捕获 shell 状态、准备凭据安全的状态，并渲染可恢复的 shell 快照。

#[path = "shell_snapshot_capture.rs"]
mod capture;
#[path = "shell_snapshot_credentials.rs"]
mod credentials;
#[path = "shell_snapshot_exports.rs"]
mod exports;
#[path = "shell_snapshot_literals.rs"]
mod literals;
#[path = "shell_snapshot_render.rs"]
mod render;

pub use capture::CapturedSnapshot;
pub use capture::CapturedStartupEnvironment;
pub use capture::SnapshotCaptureOptions;
pub use capture::snapshot_capture_script;
pub use capture::snapshot_source_capture_script;
pub use credentials::PreparedSnapshot;
pub use credentials::SnapshotCredentialEnvironment;
pub use credentials::prepare_snapshot_credentials;

use capture::BASH_SH_SNAPSHOT_HEADER;

#[cfg(all(test, unix))]
#[path = "shell_snapshot_tests.rs"]
mod tests;

/// 返回用于解析受支持的 `ENV` 启动路径的 POSIX shell 辅助函数。
///
/// 该辅助函数刻意只支持不求值的路径形式。不支持的 shell 表达式
/// 原样返回，绝不执行。
pub fn posix_env_path_expansion_function() -> &'static str {
    r#"__nova_exec_server_snapshot_expand_env() (
  set +u
  __nova_exec_server_snapshot_getenv() {
    # 把取值与查找状态及其格式化换行分开保存。
    __nova_exec_server_env_expanded=$(
      if command -v printenv >/dev/null 2>&1; then
        printenv "$1"
      elif [ "$1" = PATH ]; then
        [ "${PATH+x}" = x ] && printf '%s\n' "$PATH"
      else
        command -p printenv "$1"
      fi && printf '.'
    ) || return 1
    __nova_exec_server_env_expanded=${__nova_exec_server_env_expanded%?}
    __nova_exec_server_env_expanded=${__nova_exec_server_env_expanded%?}
  }
  __nova_exec_server_env_file=$1
  case "$__nova_exec_server_env_file" in
    '~/'*) __nova_exec_server_env_file="${HOME-}/${__nova_exec_server_env_file#*/}" ;;
    '${PATH%%:*}') __nova_exec_server_env_file="${PATH%%:*}" ;;
    '${PATH%%:*}/'*) __nova_exec_server_env_file="${PATH%%:*}/${__nova_exec_server_env_file#*/}" ;;
    '${'*)
      __nova_exec_server_env_body=${__nova_exec_server_env_file#\$\{}
      case "$__nova_exec_server_env_body" in
        *\}*)
          __nova_exec_server_env_name=${__nova_exec_server_env_body%%\}*}
          __nova_exec_server_env_suffix=${__nova_exec_server_env_body#*\}}
          case "$__nova_exec_server_env_name" in
            *:-*)
              __nova_exec_server_env_default=${__nova_exec_server_env_name#*:-}
              __nova_exec_server_env_name=${__nova_exec_server_env_name%%:-*}
              __nova_exec_server_env_has_default=1
              ;;
            *) __nova_exec_server_env_has_default= ;;
          esac
          case "$__nova_exec_server_env_name" in
            ''|[0-9]*|*[!A-Za-z0-9_]*) ;;
            *)
              case "$__nova_exec_server_env_suffix" in
                ''|/*)
                  if __nova_exec_server_snapshot_getenv "$__nova_exec_server_env_name" 2>/dev/null &&
                    { [ -n "$__nova_exec_server_env_expanded" ] || [ -z "$__nova_exec_server_env_has_default" ]; }; then
                    __nova_exec_server_env_file="$__nova_exec_server_env_expanded$__nova_exec_server_env_suffix"
                  elif [ -n "$__nova_exec_server_env_has_default" ]; then
                    __nova_exec_server_env_default=$(__nova_exec_server_snapshot_expand_env "$__nova_exec_server_env_default")
                    __nova_exec_server_env_file="$__nova_exec_server_env_default$__nova_exec_server_env_suffix"
                  fi
                  ;;
              esac
              ;;
          esac
          ;;
      esac
      ;;
    '$'*)
      __nova_exec_server_env_name=${__nova_exec_server_env_file%%/*}
      __nova_exec_server_env_name=${__nova_exec_server_env_name#\$}
      case "$__nova_exec_server_env_name" in
        ''|[0-9]*|*[!A-Za-z0-9_]*) ;;
        *)
          if __nova_exec_server_snapshot_getenv "$__nova_exec_server_env_name" 2>/dev/null; then
            if [ "$__nova_exec_server_env_file" = "\$$__nova_exec_server_env_name" ]; then
              __nova_exec_server_env_file=$__nova_exec_server_env_expanded
            else
              __nova_exec_server_env_file="$__nova_exec_server_env_expanded/${__nova_exec_server_env_file#*/}"
            fi
          fi
          ;;
      esac
      ;;
  esac
  printf '%s' "$__nova_exec_server_env_file"
)"#
}

#[derive(Clone, Copy)]
pub enum SnapshotStartup {
    Interactive,
    NonInteractive,
}
