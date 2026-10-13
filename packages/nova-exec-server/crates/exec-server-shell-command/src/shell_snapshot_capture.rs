// 对位 codex 39f4277087 codex-rs/shell-command/src/shell_snapshot_capture.rs
//! 捕获原生 shell 状态并解码完整的 export 记录，不套用凭据策略。
//! 保留原生声明标志与 tied 数组边界；不完整的记录直接拒绝。

use super::SnapshotStartup;
use super::exports;
use super::literals;
use super::posix_env_path_expansion_function;
use crate::shell_detect::ShellType;
use crate::shell_startup_script;
use nova_exec_server_protocol_core::shell_environment::NOVA_EXEC_SERVER_THREAD_ID_ENV_VAR;
use nova_exec_server_protocol_core::shell_environment::NOVA_EXEC_SERVER_TOOL_CALL_ID_ENV_VAR;
use std::borrow::Cow;

const SNAPSHOT_COMMAND_HELPER: &str = r#"__nova_exec_server_snapshot_command() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@"
  else
    command -p "$@"
  fi
}"#;

const SNAPSHOT_ENVIRONMENT: &str = r#"if command -v env >/dev/null 2>&1; then
  "env" -0
else
  # 回退路径单独解析：Bash 的 command -p 会改变该工具的 PATH。
  "$(PATH="$(command -p getconf PATH)" command -v env)" -0
fi
"#;

/// 选择启动行为以及快照消费方所需的记录。
/// shell 状态与别名总是捕获；凭据准备需要声明与环境两者，
/// executor 回放只需要环境。
#[derive(Clone, Copy)]
pub struct SnapshotCaptureOptions {
    pub startup: SnapshotStartup,
    pub declarations: bool,
    pub environment: bool,
}

/// 返回捕获原生状态、别名与所请求 export 记录的脚本。
///
/// 启动行为与捕获记录后续的渲染方式无关。
/// 快照调用方不支持 PowerShell 与 Command Prompt 快照。
pub fn snapshot_capture_script(
    shell_type: ShellType,
    options: SnapshotCaptureOptions,
) -> Option<String> {
    capture_script(shell_type, options, Replay::Eval)
}

/// 为增量 source 捕获状态。选项与别名一起解析，
/// 使恢复后的别名与引号选项无法重新解释后面的声明。
pub fn snapshot_source_capture_script(
    shell_type: ShellType,
    options: SnapshotCaptureOptions,
) -> Option<String> {
    capture_script(
        shell_type,
        options,
        if shell_type == ShellType::Sh {
            Replay::Eval
        } else {
            Replay::Source
        },
    )
}

enum Replay {
    Eval,
    Source,
}

fn capture_script(
    shell_type: ShellType,
    options: SnapshotCaptureOptions,
    replay: Replay,
) -> Option<String> {
    let script = match shell_type {
        ShellType::Zsh => zsh_snapshot_script(options.startup),
        ShellType::Bash => bash_snapshot_script(options.startup),
        ShellType::Sh => sh_snapshot_script(options.startup),
        ShellType::PowerShell | ShellType::Cmd => return None,
    };
    let declarations = if options.declarations {
        exports::script(shell_type)
    } else {
        String::new()
    };
    Some(
        script
            .replace("SNAPSHOT_OPTIONS_BEGIN", match replay { Replay::Eval => "", Replay::Source => "printf '{\\n'" })
            .replace("SNAPSHOT_ALIASES_END", match replay { Replay::Eval => "", Replay::Source => "printf \"case '' in '') ;; esac\\n}\\n\"" })
            // source 组在其选项生效前就被解析。序列化别名时必须用回放 shell 的
            // 初始引号规则，而非 RC_QUOTES。setopt 不依赖可选的 zsh/parameter 模块。
            .replace("SNAPSHOT_ZSH_ALIASES", match replay {
                Replay::Eval => "\\alias -L",
                Replay::Source => "(\\setopt NO_RC_QUOTES; \\alias -L)",
            })
            .replace("SNAPSHOT_EXPORTS", &declarations)
            .replace(
                "SNAPSHOT_DECLARATION_ENVIRONMENT",
                if options.declarations { SNAPSHOT_ENVIRONMENT } else { "" },
            )
            .replace("SNAPSHOT_COMMAND_HELPER", SNAPSHOT_COMMAND_HELPER)
            .replace(
                "SNAPSHOT_STARTUP_ENVIRONMENT",
                if options.declarations && options.environment {
                    "printf '\\0NOVA_EXEC_SERVER_SNAPSHOT_POSIX_STARTUP\\0%s\\0' \"$__nova_exec_server_env_file\"\n    SNAPSHOT_ENVIRONMENT\n    printf '\\0'"
                } else {
                    ""
                },
            )
            .replace(
                "SNAPSHOT_ENVIRONMENT",
                if options.environment {
                    SNAPSHOT_ENVIRONMENT
                } else {
                    ""
                },
            ),
    )
}

fn zsh_snapshot_script(shell_startup: SnapshotStartup) -> String {
    let startup = match shell_startup {
        SnapshotStartup::Interactive => shell_startup_script(ShellType::Zsh),
        SnapshotStartup::NonInteractive => "",
    };
    let script = r##"print '# Snapshot file'
print '# Unset all aliases to avoid conflicts with functions'
print 'unalias -a 2>/dev/null || true'
print '# Functions'
functions
print ''
SNAPSHOT_OPTIONS_BEGIN
SNAPSHOT_COMMAND_HELPER
setopt_count=$(setopt | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
print "# setopts $setopt_count"
setopt | __nova_exec_server_snapshot_command sed 's/^/setopt /'
print ''
printf '\0'
alias_count=$(\alias -L | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
print "# aliases $alias_count"
SNAPSHOT_ZSH_ALIASES
print ''
SNAPSHOT_ALIASES_END
printf '\0'
SNAPSHOT_EXPORTS
printf '\0'
SNAPSHOT_ENVIRONMENT
"##;
    format!("{startup}{script}")
}

fn bash_snapshot_script(shell_startup: SnapshotStartup) -> String {
    let startup = match shell_startup {
        SnapshotStartup::Interactive => shell_startup_script(ShellType::Bash),
        SnapshotStartup::NonInteractive => "",
    };
    let script = r##"echo '# Snapshot file'
echo '# Unset all aliases to avoid conflicts with functions'
echo 'unalias -a 2>/dev/null || true'
shopt -p || true
echo '# Functions'
declare -f
echo ''
SNAPSHOT_OPTIONS_BEGIN
SNAPSHOT_COMMAND_HELPER
bash_opts=$(set -o | __nova_exec_server_snapshot_command awk '$2=="on"{print $1}')
bash_opt_count=$(printf '%s\n' "$bash_opts" | __nova_exec_server_snapshot_command sed '/^$/d' | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
echo "# setopts $bash_opt_count"
if [ -n "$bash_opts" ]; then
  printf 'set -o %s\n' $bash_opts
fi
echo ''
printf '\0'
alias_count=$(\alias -p | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
echo "# aliases $alias_count"
\alias -p
echo ''
SNAPSHOT_ALIASES_END
printf '\0'
SNAPSHOT_EXPORTS
printf '\0'
SNAPSHOT_ENVIRONMENT
"##;
    format!("{startup}{script}")
}

pub(super) const BASH_SH_SNAPSHOT_HEADER: &str = "# Snapshot file\n# Bash-backed sh\n";

fn sh_snapshot_script(shell_startup: SnapshotStartup) -> String {
    let startup = match shell_startup {
        SnapshotStartup::Interactive => [
            posix_env_path_expansion_function(),
            r##"if [ -n "${ENV-}" ]; then
  __nova_exec_server_env_file=$(__nova_exec_server_snapshot_expand_env "$ENV")
  if [ -r "$__nova_exec_server_env_file" ] && [ ! -d "$__nova_exec_server_env_file" ]; then
    SNAPSHOT_STARTUP_ENVIRONMENT
    case "$__nova_exec_server_env_file" in
      /*) . "$__nova_exec_server_env_file" ;;
      *) . "./$__nova_exec_server_env_file" ;;
    esac
  fi
  command unset __nova_exec_server_env_file
fi
command unset -f __nova_exec_server_snapshot_expand_env
"##,
        ]
        .join("\n"),
        SnapshotStartup::NonInteractive => String::new(),
    };
    let script = r##"echo '# Snapshot file'
if [ -n "${BASH_VERSINFO-}" ]; then
  echo '# Bash-backed sh'
  shopt -p || true
fi
echo '# Unset all aliases to avoid conflicts with functions'
unalias -a 2>/dev/null || true
echo '# Functions'
if command -v typeset >/dev/null 2>&1; then
  typeset -f
elif command -v declare >/dev/null 2>&1; then
  declare -f
fi
echo ''
SNAPSHOT_OPTIONS_BEGIN
SNAPSHOT_COMMAND_HELPER
if set -o >/dev/null 2>&1; then
  sh_opts=$(set -o | __nova_exec_server_snapshot_command awk '$2=="on"{print $1}')
  sh_opt_count=$(printf '%s\n' "$sh_opts" | __nova_exec_server_snapshot_command sed '/^$/d' | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
  echo "# setopts $sh_opt_count"
  if [ -n "$sh_opts" ]; then
    printf 'set -o %s\n' $sh_opts
  fi
else
  echo '# setopts 0'
fi
echo ''
printf '\0'
if alias >/dev/null 2>&1; then
  alias_count=$(alias | __nova_exec_server_snapshot_command wc -l | __nova_exec_server_snapshot_command tr -d ' ')
  echo "# aliases $alias_count"
  alias
  echo ''
else
  echo '# aliases 0'
fi
SNAPSHOT_ALIASES_END
printf '\0'
SNAPSHOT_EXPORTS
printf '\0'
SNAPSHOT_DECLARATION_ENVIRONMENT
printf '\0'
SNAPSHOT_ENVIRONMENT
"##;
    format!("{startup}{script}")
}

/// 套用凭据或环境策略之前捕获的原生 shell 分区。
///
/// 函数与别名声明保持 shell 源码形态。每个 export 的名称与边界由 shell 提供；
/// 只有 tied 数组需要语法解码。捕获到的代码一律不求值。
pub struct CapturedSnapshot<'a> {
    /// 已解析的 POSIX 启动文件，以及 source 它之前那一刻的环境。
    pub startup_environment: Option<CapturedStartupEnvironment<'a>>,
    pub(super) shell_type: ShellType,
    pub(super) state: &'a str,
    pub(super) aliases: &'a str,
    pub(super) exports: Vec<CapturedExport<'a>>,
    /// 同一次 shell 调用产出的 NUL 分隔环境条目。
    pub environment: &'a [u8],
}

/// 可选的启动前状态，与最终的快照记录一起捕获。
pub struct CapturedStartupEnvironment<'a> {
    pub path: Cow<'a, str>,
    pub environment: &'a [u8],
}

pub(super) struct CapturedExport<'a> {
    pub source: Cow<'a, str>,
    pub key: &'a str,
    pub value: ExportValue,
}

pub(super) enum ExportValue {
    Plain,
    TiedArray(CapturedArray),
    UnparsedArray,
}

pub(super) struct CapturedArray {
    pub span: std::ops::Range<usize>,
    pub values: Vec<Vec<u8>>,
    pub separator: Vec<u8>,
}

impl<'a> CapturedSnapshot<'a> {
    /// 解码 `snapshot_capture_script` 产出的本地捕获格式。
    /// 环境保留原始字节，调用方可独立丢弃非 UTF-8 条目。
    pub fn parse(shell_type: ShellType, captured: &'a [u8]) -> Option<Self> {
        if !matches!(shell_type, ShellType::Bash | ShellType::Zsh | ShellType::Sh) {
            return None;
        }
        let mut remaining = captured;
        let startup_marker = b"\0NOVA_EXEC_SERVER_SNAPSHOT_POSIX_STARTUP\0";
        let startup_environment = if shell_type == ShellType::Sh
            && let Some(start) = remaining
                .windows(startup_marker.len())
                .position(|part| part == startup_marker)
        {
            remaining = &remaining[start + startup_marker.len()..];
            let path = String::from_utf8_lossy(take_record(&mut remaining)?);
            let environment = remaining;
            while !take_record(&mut remaining)?.is_empty() {}
            let environment = &environment[..environment.len() - remaining.len() - 1];
            Some(CapturedStartupEnvironment { path, environment })
        } else {
            None
        };
        // 布局：state NUL aliases NUL (name NUL declaration NUL)* NUL environment。
        // POSIX sh 额外把声明值捕获为一份 NUL 分隔的环境。
        let state = take_record(&mut remaining)?;
        let marker = b"# Snapshot file";
        let start = state
            .windows(marker.len())
            .position(|part| part == marker)?;
        let state = std::str::from_utf8(&state[start..]).ok()?;
        let aliases = std::str::from_utf8(take_record(&mut remaining)?).ok()?;
        let mut records = Vec::new();
        loop {
            let key = std::str::from_utf8(take_record(&mut remaining)?).ok()?;
            if key.is_empty() {
                if shell_type == ShellType::Sh {
                    loop {
                        let entry = take_record(&mut remaining)?;
                        if entry.is_empty() {
                            break;
                        }
                        let equals = entry.iter().position(|byte| *byte == b'=')?;
                        let Ok(key) = std::str::from_utf8(&entry[..equals]) else {
                            continue;
                        };
                        if key.is_empty()
                            || matches!(key, "PWD" | "OLDPWD")
                            || !key.bytes().enumerate().all(|(index, byte)| {
                                byte == b'_'
                                    || byte.is_ascii_alphabetic()
                                    || index > 0 && byte.is_ascii_digit()
                            })
                        {
                            continue;
                        }
                        let value = String::from_utf8_lossy(&entry[equals + 1..]);
                        records.push(CapturedExport {
                            source: Cow::Owned(format!(
                                "export {key}={}\n",
                                shlex::try_quote(&value).ok()?
                            )),
                            key,
                            value: ExportValue::Plain,
                        });
                    }
                }
                // 每次启动都会提供执行 ID；其捕获 export 一律省略。
                records.retain(|export| {
                    !matches!(
                        export.key,
                        NOVA_EXEC_SERVER_THREAD_ID_ENV_VAR | NOVA_EXEC_SERVER_TOOL_CALL_ID_ENV_VAR
                    )
                });
                return Some(Self {
                    startup_environment,
                    shell_type,
                    state,
                    aliases,
                    exports: records,
                    environment: remaining,
                });
            }
            let source = String::from_utf8_lossy(take_record(&mut remaining)?);
            // 只有 tied 数组需要语法解码；标量值保持原生声明形态。
            let tied = shell_type == ShellType::Zsh
                && source
                    .split_whitespace()
                    .skip(/*n*/ 1)
                    .take_while(|word| word.starts_with('-'))
                    .any(|flags| flags.contains('T'));
            let tree = tied
                .then(|| crate::bash::try_parse_shell(&source))
                .flatten();
            let array = tree.as_ref().and_then(|tree| {
                if tree.root_node().has_error() {
                    return None;
                }
                let mut cursor = tree.root_node().walk();
                tree.root_node()
                    .named_child(/*i*/ 0)?
                    .named_children(&mut cursor)
                    .find_map(|child| {
                        child
                            .child_by_field_name("value")
                            .filter(|value| value.kind() == "array")
                    })
            });
            let value = if let Some(array) = array {
                let mut cursor = array.walk();
                let values = array
                    .named_children(&mut cursor)
                    .map(|node| {
                        literals::literal_word_bytes(
                            node,
                            &source,
                            shell_type,
                            literals::Quoting::Posix,
                        )
                    })
                    .collect::<Option<Vec<_>>>();
                let separator = array.parent().and_then(|node| node.next_named_sibling());
                let separator = if let Some(separator) = separator
                    && separator.kind() == "variable_name"
                {
                    separator
                        .utf8_text(source.as_bytes())
                        .ok()
                        .map(|value| value.as_bytes().to_vec())
                } else if let Some(separator) = separator {
                    literals::literal_word_bytes(
                        separator,
                        &source,
                        shell_type,
                        literals::Quoting::Posix,
                    )
                } else {
                    Some(vec![b':'])
                };
                match (values, separator) {
                    (Some(values), Some(separator)) => ExportValue::TiedArray(CapturedArray {
                        span: array.byte_range(),
                        values,
                        separator,
                    }),
                    _ => ExportValue::UnparsedArray,
                }
            } else if tied {
                ExportValue::UnparsedArray
            } else {
                ExportValue::Plain
            };
            records.push(CapturedExport { source, key, value });
        }
    }
}

fn take_record<'a>(remaining: &mut &'a [u8]) -> Option<&'a [u8]> {
    let boundary = remaining.iter().position(|byte| *byte == 0)?;
    let record = &remaining[..boundary];
    *remaining = &remaining[boundary + 1..];
    Some(record)
}
