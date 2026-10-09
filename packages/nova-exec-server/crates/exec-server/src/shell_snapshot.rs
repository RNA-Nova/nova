use std::collections::HashMap;
use std::collections::VecDeque;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use nova_exec_server_network_proxy::PROXY_ACTIVE_ENV_KEY;
use nova_exec_server_network_proxy::strip_managed_proxy_env;
use nova_exec_server_protocol::JSONRPCErrorError;
use nova_exec_server_protocol_core::config_types::ShellEnvironmentPolicyInherit;
use nova_exec_server_protocol_core::shell_environment;
// 对位 codex 9b738582b1：快照捕获/回放要剔除的两个关联标签变量
use nova_exec_server_protocol_core::shell_environment::CODEX_THREAD_ID_ENV_VAR;
use nova_exec_server_protocol_core::shell_environment::CODEX_TOOL_CALL_ID_ENV_VAR;
use nova_exec_server_shell_command::shell_detect::ShellType;
use nova_exec_server_shell_command::shell_snapshot::snapshot_state_and_environment_script;
use nova_exec_server_utils_path_uri::PathUri;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::Mutex;
use tokio::sync::OnceCell;
use tokio::time::Instant;

use crate::FileSystemSandboxContext;
use crate::local_process::apply_exec_metadata;
use crate::local_process::shell_environment_policy;
use crate::process_sandbox::PreparedExecRequest;
use crate::protocol::ExecEnvPolicy;
use crate::protocol::ExecParams;
use crate::protocol::ShellSnapshotRequest;
use crate::rpc::internal_error;
use crate::rpc::invalid_params;
use crate::telemetry::ExecServerTelemetry;

const MAX_CACHED_SNAPSHOTS: usize = 16;
const MAX_SNAPSHOT_BYTES: usize = 512 * 1024;
// 对位 codex e32365a2c6：文件回放允许更大的 shell 状态
const MAX_FILE_SNAPSHOT_BYTES: usize = 4 * 1024 * 1024;
// 捕获输出还包含带引号的 export 记录与可选的启动前环境（对位 codex 常量注释）
const MAX_SNAPSHOT_CAPTURE_BYTES: usize = 8 * MAX_SNAPSHOT_BYTES;
// 对位 codex e32365a2c6：文件回放的捕获输出预算
const MAX_FILE_SNAPSHOT_CAPTURE_BYTES: usize = 8 * 1024 * 1024;
const MAX_SNAPSHOT_ENV_VALUE_BYTES: usize = 60 * 1024;
const MAX_SNAPSHOT_SCOPE_BYTES: usize = 256;
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(10);
const SNAPSHOT_RETRY_BACKOFF: Duration = Duration::from_secs(1);
const MAX_SNAPSHOT_ATTEMPTS: usize = 3;

// 对位 codex e32365a2c6：回放方式（文件/环境）决定快照尺寸预算。
// nova 尚未镜像文件回放（unnamed reader 探针），File 当前仅测试构造。
#[derive(Clone, Copy)]
enum SnapshotReplay {
    #[allow(dead_code)]
    File,
    Environment,
}

#[derive(Default)]
pub(crate) struct ShellSnapshotCache {
    entries: Mutex<VecDeque<CachedShellSnapshot>>,
}

struct CachedShellSnapshot {
    request: ShellSnapshotRequest,
    cwd: PathUri,
    env_policy: Option<ExecEnvPolicy>,
    sandbox: Option<FileSystemSandboxContext>,
    attempts: usize,
    // Failed captures store the earliest time another attempt may start.
    snapshot: Arc<OnceCell<Result<ShellSnapshot, Instant>>>,
}

struct ShellSnapshot {
    // 对位 codex 588f616e8b：标记快照是否由 prewarm 捕获（nova 暂无 prewarm 路径，恒为 false）
    prewarmed: bool,
    state: String,
    environment: HashMap<String, String>,
}

impl ShellSnapshotCache {
    pub(crate) async fn prepare(
        &self,
        params: &ExecParams,
        prepared: &mut PreparedExecRequest,
        telemetry: &ExecServerTelemetry,
    ) -> Result<(), JSONRPCErrorError> {
        let Some(request) = params.shell_snapshot.as_ref() else {
            return Ok(());
        };
        if request.scope_id.is_empty() || request.scope_id.len() > MAX_SNAPSHOT_SCOPE_BYTES {
            return Err(invalid_params(format!(
                "shell snapshot scope must be non-empty and at most {MAX_SNAPSHOT_SCOPE_BYTES} bytes"
            )));
        }

        if params.argv.len() < 3
            || params.argv[0] != request.shell.path
            || params.argv[1] != "-lc"
            || !prepared.command.ends_with(&params.argv)
        {
            return Ok(());
        }

        let shell_type = match request.shell.name.as_str() {
            "bash" => ShellType::Bash,
            "zsh" => ShellType::Zsh,
            "sh" => ShellType::Sh,
            name => {
                return Err(invalid_params(format!(
                    "shell snapshots are unsupported for shell `{name}`"
                )));
            }
        };

        // 对位 codex 588f616e8b：从缓存查找前开始计快照等待时间
        let wait_started_at = std::time::Instant::now();
        let snapshot = {
            let mut entries = self.entries.lock().await;
            let position = entries.iter().position(|entry| {
                &entry.request == request
                    && entry.cwd == params.cwd
                    && entry.env_policy == params.env_policy
                    && entry.sandbox == params.sandbox
            });
            let cached = position.and_then(|position| {
                let mut entry = entries.remove(position)?;
                // Share each failed attempt during backoff. After the retry
                // budget is exhausted, keep falling back until eviction.
                if entry.attempts < MAX_SNAPSHOT_ATTEMPTS
                    && let Some(Err(retry_at)) = entry.snapshot.get()
                    && Instant::now() >= *retry_at
                {
                    entry.attempts += 1;
                    entry.snapshot = Arc::new(OnceCell::new());
                }
                let snapshot = Arc::clone(&entry.snapshot);
                entries.push_back(entry);
                Some(snapshot)
            });
            if let Some(snapshot) = cached {
                snapshot
            } else {
                let snapshot = Arc::new(OnceCell::new());
                let entry = CachedShellSnapshot {
                    request: request.clone(),
                    cwd: params.cwd.clone(),
                    env_policy: params.env_policy.clone(),
                    sandbox: params.sandbox.clone(),
                    attempts: 1,
                    snapshot: Arc::clone(&snapshot),
                };
                entries.push_back(entry);
                if entries.len() > MAX_CACHED_SNAPSHOTS {
                    entries.pop_front();
                }

                snapshot
            }
        };
        // 对位 codex 588f616e8b：缓存可用性标签；prewarm_ready 在 nova 暂无触发路径
        let mut availability = match snapshot.get() {
            Some(Ok(snapshot)) if snapshot.prewarmed => "prewarm_ready",
            Some(Ok(_)) => "cache_hit",
            Some(Err(_)) => "unavailable",
            None => "capture_pending",
        };
        let snapshot = snapshot
            .get_or_init(|| async {
                // 对位 codex 588f616e8b：execution 按需捕获覆盖可用性标签
                availability = "on_demand";
                let started_at = std::time::Instant::now();
                let result = capture_snapshot(params, prepared, shell_type).await;
                telemetry.shell_snapshot_captured(
                    started_at.elapsed(),
                    result.as_ref().map(|_| ()).map_err(|_| "capture_failed"),
                );
                result.map_err(|err| {
                    tracing::warn!("failed to capture shell snapshot: {err:?}");
                    Instant::now() + SNAPSHOT_RETRY_BACKOFF
                })
            })
            .await;
        let wait = wait_started_at.elapsed();
        let Ok(snapshot) = snapshot else {
            // 对位 codex 588f616e8b：回退普通 shell 启动同样计一次观测
            telemetry.shell_snapshot_command(wait, availability, "fallback");
            return Ok(());
        };

        let request_overrides = params
            .env
            .iter()
            .map(|(name, value)| {
                (
                    name.clone(),
                    prepared.env.get(name).unwrap_or(value).clone(),
                )
            })
            .collect::<HashMap<_, _>>();
        prepared.env.extend(
            snapshot
                .environment
                .iter()
                .map(|(name, value)| (name.clone(), value.clone())),
        );
        prepared.env.extend(request_overrides);
        // 对位 codex 9b738582b1：快照回放后再应用执行元数据，覆盖快照中的陈旧 ID
        apply_exec_metadata(&mut prepared.env, params.metadata.as_ref());
        prepared
            .env
            .retain(|name, _| !shell_environment::is_non_inheritable_env_var(name));

        let mut state = snapshot.state.as_str();
        let mut state_variables = Vec::new();
        while !state.is_empty() {
            let mut end = state.len().min(MAX_SNAPSHOT_ENV_VALUE_BYTES);
            while !state.is_char_boundary(end) {
                end -= 1;
            }
            let (chunk, remaining) = state.split_at(end);
            let name = format!(
                "__NOVA_EXEC_SERVER_SHELL_SNAPSHOT_STATE_{}",
                state_variables.len()
            );
            prepared.env.insert(name.clone(), chunk.to_string());
            state_variables.push(name);
            state = remaining;
        }
        let state_expansion = state_variables
            .iter()
            .map(|name| format!("${{{name}}}"))
            .collect::<String>();
        let state_variables = state_variables.join(" ");
        let shell_start = prepared.command.len() - params.argv.len();
        // Automatic startup files run before the restoration script and could
        // reintroduce environment variables that the snapshot already filtered.
        // bash 需要 --norc：沙箱包装链的 stdin 是 socket，bash 的 rshd 检测会把
        // 非交互非登录执行误判为 rshd 启动而自动 source ~/.bashrc，让用户 rc 中
        // 的别名/函数重定义（如遮蔽 unset）污染快照恢复——恢复必须从纯净状态开始。
        let (norc, shell_flag, startup) = match shell_type {
            ShellType::Bash => (true, "-pc", "set +o privileged\n"),
            ShellType::Zsh => (false, "-fc", "setopt RCS\n"),
            ShellType::Sh => (false, "-c", ""),
            ShellType::PowerShell | ShellType::Cmd => unreachable!(),
        };
        let restore_script = format!(
            "{startup}if ! eval \"unset {state_variables}\n{state_expansion}\" >/dev/null; then printf 'failed to restore shell snapshot\\n' >&2; fi\n{}",
            params.argv[2]
        );
        if norc {
            prepared
                .command
                .insert(shell_start + 1, "--norc".to_string());
            prepared.command[shell_start + 2] = shell_flag.to_string();
            prepared.command[shell_start + 3] = restore_script;
        } else {
            prepared.command[shell_start + 1] = shell_flag.to_string();
            prepared.command[shell_start + 2] = restore_script;
        }

        // 对位 codex 588f616e8b：选中快照回放计一次 used 观测
        telemetry.shell_snapshot_command(wait, availability, "used");
        Ok(())
    }
}

async fn capture_snapshot(
    params: &ExecParams,
    prepared: &PreparedExecRequest,
    shell_type: ShellType,
) -> Result<ShellSnapshot, JSONRPCErrorError> {
    let script = snapshot_state_and_environment_script(shell_type)
        .ok_or_else(|| invalid_params("unsupported shell snapshot script".to_string()))?;
    let shell_start = prepared.command.len() - params.argv.len();
    let mut argv = prepared.command.clone();
    argv[shell_start + 2] = script;
    let (program, args) = argv
        .split_first()
        .ok_or_else(|| internal_error("missing shell snapshot command".to_string()))?;

    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(prepared.cwd.as_path())
        .env_clear()
        .envs(&prepared.env)
        // 对位 codex 9b738582b1：调用 ID 不进捕获进程环境
        .env_remove(CODEX_TOOL_CALL_ID_ENV_VAR)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    if let Some(arg0) = &prepared.arg0 {
        command.arg0(arg0);
    }
    let mut child = command
        .spawn()
        .map_err(|err| internal_error(format!("cannot capture shell snapshot: {err}")))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| internal_error("missing shell snapshot output".to_string()))?;
    // 对位 codex e32365a2c6：回放方式决定捕获输出预算；nova 尚未镜像文件回放
    // （unnamed reader 探针），当前恒为环境回放
    let replay = SnapshotReplay::Environment;
    let capture_limit = match replay {
        SnapshotReplay::File => MAX_FILE_SNAPSHOT_CAPTURE_BYTES,
        SnapshotReplay::Environment => MAX_SNAPSHOT_CAPTURE_BYTES,
    };
    let capture = async {
        let mut output = Vec::new();
        stdout
            .take((capture_limit + 1) as u64)
            .read_to_end(&mut output)
            .await
            .map_err(|err| internal_error(format!("cannot read shell snapshot: {err}")))?;
        if output.len() > capture_limit {
            return Err(internal_error(format!(
                "shell snapshot capture exceeds {capture_limit} bytes"
            )));
        }
        let status = child
            .wait()
            .await
            .map_err(|err| internal_error(format!("cannot finish shell snapshot: {err}")))?;
        if !status.success() {
            return Err(internal_error(format!(
                "shell snapshot capture exited with {status}"
            )));
        }
        Ok(output)
    };
    let output = tokio::time::timeout(SNAPSHOT_TIMEOUT, capture)
        .await
        .map_err(|_| internal_error("shell snapshot capture timed out".to_string()))??;

    parse_snapshot(&output, params.env_policy.as_ref(), replay)
}

fn parse_snapshot(
    output: &[u8],
    env_policy: Option<&ExecEnvPolicy>,
    replay: SnapshotReplay,
) -> Result<ShellSnapshot, JSONRPCErrorError> {
    let separator = output
        .iter()
        .position(|byte| *byte == 0)
        .ok_or_else(|| internal_error("shell snapshot is missing its environment".to_string()))?;
    let state = &output[..separator];
    let marker = b"# Snapshot file";
    let start = state
        .windows(marker.len())
        .position(|window| window == marker)
        .ok_or_else(|| internal_error("shell snapshot is missing its state marker".to_string()))?;
    let state = std::str::from_utf8(&state[start..])
        .map_err(|err| internal_error(format!("shell snapshot state is not UTF-8: {err}")))?;

    // 对位 codex e32365a2c6：捕获环境独立保持 512KiB 上限（过滤前计量原始记录）
    let environment_bytes = &output[separator + 1..];
    if environment_bytes.len() > MAX_SNAPSHOT_BYTES {
        return Err(internal_error(format!(
            "shell snapshot environment exceeds {MAX_SNAPSHOT_BYTES} bytes"
        )));
    }
    // 对位 codex e32365a2c6：文件回放状态上限 4MiB；环境回放保持 512KiB 合并预算
    let state_limit = match replay {
        SnapshotReplay::File => MAX_FILE_SNAPSHOT_BYTES,
        SnapshotReplay::Environment => MAX_SNAPSHOT_BYTES - environment_bytes.len(),
    };
    if state.len() > state_limit {
        return Err(internal_error(format!(
            "shell snapshot state exceeds {state_limit} bytes"
        )));
    }

    let mut environment = environment_bytes
        .split(|byte| *byte == 0)
        .filter(|entry| !entry.is_empty())
        .filter_map(|entry| {
            let (name, value) = std::str::from_utf8(entry).ok()?.split_once('=')?;
            Some((name.to_string(), value.to_string()))
        })
        .collect::<HashMap<_, _>>();
    if environment.contains_key(PROXY_ACTIVE_ENV_KEY) {
        strip_managed_proxy_env(&mut environment);
    }
    let mut environment = match env_policy {
        Some(policy) => {
            let mut policy = shell_environment_policy(policy);
            policy.inherit = ShellEnvironmentPolicyInherit::All;
            shell_environment::create_env_from_vars(environment, &policy)
        }
        None => environment,
    };
    // 对位 codex 9b738582b1：两个关联标签 ID 不进新捕获的快照
    environment.remove(CODEX_THREAD_ID_ENV_VAR);
    environment.remove(CODEX_TOOL_CALL_ID_ENV_VAR);
    environment.remove("PWD");
    environment.remove("OLDPWD");
    environment.retain(|name, _| !shell_environment::is_non_inheritable_env_var(name));

    Ok(ShellSnapshot {
        // 对位 codex 588f616e8b：prewarmed 由捕获方按 purpose 标记，解析处恒为 false
        prewarmed: false,
        state: state.to_string(),
        environment,
    })
}

#[cfg(test)]
#[path = "shell_snapshot_tests.rs"]
mod tests;
