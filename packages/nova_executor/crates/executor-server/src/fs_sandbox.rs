use std::collections::HashMap;
use std::time::Duration;

use nova_executor_protocol::JSONRPCErrorError;
use nova_executor_protocol_core::models::PermissionProfile;
use nova_executor_protocol_core::permissions::FileSystemAccessMode;
use nova_executor_protocol_core::permissions::FileSystemPath;
use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_executor_protocol_core::permissions::FileSystemSpecialPath;
use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
use nova_executor_sandboxing::SandboxCommand;
use nova_executor_sandboxing::SandboxDirectSpawnTransformRequest;
use nova_executor_sandboxing::SandboxExecRequest;
use nova_executor_sandboxing::SandboxManager;
use nova_executor_sandboxing::SandboxTransformRequest;
use nova_executor_sandboxing::SandboxType;
use nova_executor_sandboxing::SandboxablePreference;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use nova_executor_utils_absolute_path::canonicalize_preserving_symlinks;
use nova_executor_utils_path_uri::PathUri;
use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio::io::BufReader;
use tokio::io::Lines;
use tokio::process::ChildStdin;
use tokio::process::ChildStdout;
use tokio::process::Command;

use crate::ExecServerRuntimePaths;
use crate::FileSystemSandboxContext;
use crate::fs_helper::NOVA_EXECUTOR_FS_HELPER_ARG1;
use crate::fs_helper::FsHelperPayload;
use crate::fs_helper::FsHelperRequest;
use crate::fs_helper::FsHelperResponse;
use crate::fs_helper::FsHelperWriteStreamEvent;
use crate::local_file_system::current_sandbox_cwd;
use crate::protocol::FsWriteStreamChunkNotification;
use crate::protocol::FsWriteStreamDoneParams;
use crate::protocol::FsWriteStreamDoneResponse;
use crate::protocol::FsWriteStreamResponse;
use crate::rpc::internal_error;
use crate::rpc::invalid_request;

const FS_HELPER_ENV_ALLOWLIST: &[&str] = &["PATH", "TMPDIR", "TMP", "TEMP"];
/// helper 不应答时的收尸等待上限：先在窗口内等其自行退出，超时再强杀
const FS_HELPER_EXIT_TIMEOUT: Duration = Duration::from_secs(/*secs*/ 2);
/// helper stderr 的限量排空上限（超出部分丢弃但继续排空，仅作诊断）
const MAX_FS_HELPER_STDERR_BYTES: u64 = 4096;
#[cfg(debug_assertions)]
const FS_HELPER_BAZEL_BWRAP_ENV_ALLOWLIST: &[&str] = &[
    "CARGO_BIN_EXE_bwrap",
    "RUNFILES_DIR",
    "RUNFILES_MANIFEST_FILE",
    "RUNFILES_MANIFEST_ONLY",
    "TEST_SRCDIR",
    "TEST_WORKSPACE",
];

#[derive(Debug, PartialEq, Eq)]
struct SandboxCwd {
    uri: PathUri,
    native: AbsolutePathBuf,
}

#[derive(Clone, Debug)]
pub(crate) struct FileSystemSandboxRunner {
    runtime_paths: ExecServerRuntimePaths,
    helper_env: HashMap<String, String>,
}

impl FileSystemSandboxRunner {
    pub(crate) fn new(runtime_paths: ExecServerRuntimePaths) -> Self {
        Self {
            runtime_paths,
            helper_env: helper_env(),
        }
    }

    pub(crate) async fn run(
        &self,
        sandbox: &FileSystemSandboxContext,
        request: FsHelperRequest,
    ) -> Result<FsHelperPayload, JSONRPCErrorError> {
        let command = self.prepare_command(sandbox)?;
        let request_json = serde_json::to_vec(&request).map_err(json_error)?;
        run_command(command, request_json).await
    }

    /// 启动长命沙箱 helper（fs/writeStream）：与一次性 `run` 相同的沙箱变换
    /// 与进程配置，但进程活到流结束——stdin 保持打开，executor 随后逐行转发
    /// chunk/finish 事件帧，helper 回传启动握手（首行）与最终确认（末行）。
    /// 返回的流对象经 `kill_on_drop` 兜底：任何提前丢弃都会击杀 helper。
    pub(crate) async fn spawn_streaming_write(
        &self,
        sandbox: &FileSystemSandboxContext,
        request: FsHelperRequest,
    ) -> Result<SandboxFsHelperWriteStream, JSONRPCErrorError> {
        let spawned = self.spawn_streaming_helper(sandbox, request).await?;
        Ok(SandboxFsHelperWriteStream {
            child: spawned.child,
            stdin: spawned.stdin,
            stdout: spawned.stdout,
            stderr_drain: spawned.stderr_drain,
        })
    }

    /// 拉起长命 helper 并写入请求帧（NDJSON 首行，以换行收尾）；写方向流式
    /// helper 专用（stdin 保持打开，随后逐行转发 chunk/finish 事件帧）。
    async fn spawn_streaming_helper(
        &self,
        sandbox: &FileSystemSandboxContext,
        request: FsHelperRequest,
    ) -> Result<SpawnedFsHelper, JSONRPCErrorError> {
        let command = self.prepare_command(sandbox)?;
        let request_json = serde_json::to_vec(&request).map_err(json_error)?;
        let mut child = spawn_command(command, std::process::Stdio::piped())?;
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| internal_error("failed to open fs sandbox helper stdin".to_string()))?;
        stdin.write_all(&request_json).await.map_err(io_error)?;
        stdin.write_all(b"\n").await.map_err(io_error)?;
        stdin.flush().await.map_err(io_error)?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| internal_error("failed to open fs sandbox helper stdout".to_string()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| internal_error("failed to open fs sandbox helper stderr".to_string()))?;
        // stderr 后台限量排空：超过上限后丢弃余量但继续排到 EOF，
        // 避免管道写满阻塞 helper，退出时附作诊断
        let stderr_drain = tokio::spawn(async move {
            let mut buffer = Vec::new();
            let mut stderr = stderr;
            let _ = (&mut stderr)
                .take(MAX_FS_HELPER_STDERR_BYTES)
                .read_to_end(&mut buffer)
                .await;
            let _ = tokio::io::copy(&mut stderr, &mut tokio::io::sink()).await;
            buffer
        });
        Ok(SpawnedFsHelper {
            child,
            stdin,
            stdout: BufReader::new(stdout).lines(),
            stderr_drain,
        })
    }

    pub(crate) fn prepare_command(
        &self,
        sandbox: &FileSystemSandboxContext,
    ) -> Result<SandboxExecRequest, JSONRPCErrorError> {
        let cwd = sandbox_cwd(sandbox)?;
        let native_workspace_roots = sandbox
            .workspace_roots
            .iter()
            .map(native_workspace_root)
            .collect::<Result<Vec<_>, _>>()?;
        let workspace_roots = native_workspace_roots.as_slice();
        let native_permissions: PermissionProfile =
            sandbox.permissions.clone().try_into().map_err(|err| {
                invalid_request(format!("invalid sandbox permission path URI: {err}"))
            })?;
        let native_permissions =
            native_permissions.materialize_project_roots_with_workspace_roots(workspace_roots);
        let mut file_system_policy = native_permissions.file_system_sandbox_policy();
        let helper_read_roots = if sandbox.use_legacy_landlock {
            Vec::new()
        } else {
            helper_read_roots(&self.runtime_paths)
        };
        add_helper_runtime_permissions(
            &mut file_system_policy,
            &helper_read_roots,
            cwd.native.as_path(),
        );
        normalize_file_system_policy_root_aliases(&mut file_system_policy);
        let network_policy = NetworkSandboxPolicy::Restricted;
        let permission_profile = PermissionProfile::from_runtime_permissions_with_enforcement(
            native_permissions.enforcement(),
            &file_system_policy,
            network_policy,
        );
        self.sandbox_exec_request(&permission_profile, &cwd, workspace_roots, sandbox)
    }

    fn sandbox_exec_request(
        &self,
        permission_profile: &PermissionProfile,
        cwd: &SandboxCwd,
        workspace_roots: &[AbsolutePathBuf],
        sandbox_context: &FileSystemSandboxContext,
    ) -> Result<SandboxExecRequest, JSONRPCErrorError> {
        let helper = &self.runtime_paths.executor_self_exe;
        // fail-closed：fs helper 必须使用更窄的 FileSystemHelper profile，
        // 平台沙箱不可用时显式报错而不是静默裸跑
        let sandbox_manager = SandboxManager::for_file_system_helpers();
        let sandbox = sandbox_manager.select_initial(
            permission_profile,
            SandboxablePreference::Require,
            sandbox_context.windows_sandbox_level,
            /*has_managed_network_requirements*/ false,
        );
        if sandbox == SandboxType::None {
            return Err(invalid_request(
                "filesystem sandbox cannot be enforced on this executor".to_string(),
            ));
        }
        let command = SandboxCommand {
            program: helper.as_path().as_os_str().to_owned(),
            args: vec![NOVA_EXECUTOR_FS_HELPER_ARG1.to_string()],
            cwd: cwd.uri.clone(),
            env: self.helper_env.clone(),
            managed_network: None,
            additional_permissions: None,
        };
        sandbox_manager
            .transform_for_direct_spawn(SandboxDirectSpawnTransformRequest {
                workspace_roots,
                windows_sandbox_proxy_settings_mode:
                    nova_executor_sandboxing::WindowsSandboxProxySettingsMode::Preserve,
                transform: SandboxTransformRequest {
                    command,
                    permissions: permission_profile,
                    sandbox,
                    enforce_managed_network: false,
                    environment_id: None,
                    network: None,
                    sandbox_policy_cwd: &cwd.uri,
                    codex_linux_sandbox_exe: self
                        .runtime_paths
                        .executor_linux_sandbox_exe
                        .as_deref(),
                    use_legacy_landlock: sandbox_context.use_legacy_landlock,
                    windows_sandbox_level: sandbox_context.windows_sandbox_level,
                    windows_sandbox_private_desktop: sandbox_context
                        .windows_sandbox_private_desktop,
                },
            })
            .map_err(|err| invalid_request(format!("failed to prepare fs sandbox: {err}")))
    }
}

/// 长命 helper 的 spawn 产物（fs/writeStream 流式写方向）。
struct SpawnedFsHelper {
    child: tokio::process::Child,
    stdin: ChildStdin,
    stdout: Lines<BufReader<ChildStdout>>,
    stderr_drain: tokio::task::JoinHandle<Vec<u8>>,
}

/// 回收长命 helper：先在 FS_HELPER_EXIT_TIMEOUT 窗口内等其自行退出（正常
/// 路径下 helper 在确认帧后已自行退出，wait 立即返回）；超时不应答则
/// start_kill + wait 强杀收尸（不留僵尸/孤儿），最后收编 stderr 排空任务
/// （同样限时，兜底 kill 失败等极端情况）。
async fn reap_fs_helper(
    child: &mut tokio::process::Child,
    stderr_drain: &mut tokio::task::JoinHandle<Vec<u8>>,
) -> (Result<std::process::ExitStatus, std::io::Error>, Vec<u8>) {
    let status = match tokio::time::timeout(FS_HELPER_EXIT_TIMEOUT, child.wait()).await {
        Ok(status) => status,
        Err(_) => {
            // helper 不应答：强杀并再给一个退出窗口
            let _ = child.start_kill();
            match tokio::time::timeout(FS_HELPER_EXIT_TIMEOUT, child.wait()).await {
                Ok(status) => status,
                Err(_) => Err(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "fs sandbox helper did not stop after kill",
                )),
            }
        }
    };
    let stderr = match tokio::time::timeout(FS_HELPER_EXIT_TIMEOUT, &mut *stderr_drain).await {
        Ok(result) => result.unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    (status, stderr)
}

/// 回收 helper 并按退出状态告警（正常退出静默）。
async fn finish_fs_helper(
    mut child: tokio::process::Child,
    mut stderr_drain: tokio::task::JoinHandle<Vec<u8>>,
    stream_kind: &str,
) {
    let (status, stderr) = reap_fs_helper(&mut child, &mut stderr_drain).await;
    match status {
        Ok(status) if status.success() => {}
        Ok(status) => tracing::warn!(
            %status,
            stderr = %String::from_utf8_lossy(&stderr).trim(),
            "fs sandbox {stream_kind} stream helper exited with failure"
        ),
        Err(err) => {
            tracing::warn!(
                ?err,
                "failed to reap fs sandbox {stream_kind} stream helper"
            )
        }
    }
}

/// 构造 helper 早退的内部错误：击杀并回收子进程，错误信息附带退出状态
/// 与 stderr 摘要（helper 失败时会把原因写到 stderr）。
async fn fs_helper_exit_error(
    child: &mut tokio::process::Child,
    stderr_drain: &mut tokio::task::JoinHandle<Vec<u8>>,
    context: &str,
) -> JSONRPCErrorError {
    let (status, stderr) = reap_fs_helper(child, stderr_drain).await;
    let status = status
        .map(|status| status.to_string())
        .unwrap_or_else(|err| format!("unknown (reap failed: {err})"));
    internal_error(format!(
        "fs sandbox helper exited {context} (status: {status}, stderr: {})",
        String::from_utf8_lossy(&stderr).trim()
    ))
}

/// 长命沙箱 fs/writeStream helper 句柄：包装子进程与其 stdin 事件帧通道 /
/// stdout 响应帧流。stdin 保持打开：executor 逐行转发 chunk/finish 事件帧。
///
/// 回收语义：正常结束（最终确认读回后）或中断/异常都经
/// `finish`/`kill_on_drop` 完成收尸，不留孤儿进程。
pub(crate) struct SandboxFsHelperWriteStream {
    child: tokio::process::Child,
    stdin: ChildStdin,
    stdout: Lines<BufReader<ChildStdout>>,
    stderr_drain: tokio::task::JoinHandle<Vec<u8>>,
}

impl SandboxFsHelperWriteStream {
    /// 读取启动握手（首行）：Ok 为 writeStream 响应；helper 报告的请求级错误
    /// （如打开被沙箱拒绝）原样透传为 RPC 错误，与非沙箱路径语义一致；
    /// EOF/帧损坏归为内部错误并附带 helper 退出状态与 stderr 摘要。
    pub(crate) async fn write_start(&mut self) -> Result<FsWriteStreamResponse, JSONRPCErrorError> {
        match self.stdout.next_line().await.map_err(io_error)? {
            Some(line) => {
                let response: FsHelperResponse = serde_json::from_str(&line).map_err(json_error)?;
                match response {
                    FsHelperResponse::Ok(payload) => payload.expect_write_stream(),
                    FsHelperResponse::Error(error) => Err(error),
                }
            }
            None => Err(self
                .helper_exit_error("before the write stream started")
                .await),
        }
    }

    /// 转发一个数据块（与线上 fs/writeStream/chunk 通知同构的 NDJSON 帧）。
    pub(crate) async fn send_chunk(
        &mut self,
        notification: &FsWriteStreamChunkNotification,
    ) -> Result<(), JSONRPCErrorError> {
        self.write_event(&FsHelperWriteStreamEvent::Chunk(notification.clone()))
            .await
    }

    /// 转发收尾请求（对应 fs/writeStream/done）：helper 校验 eof 并回传最终确认。
    pub(crate) async fn send_finish(
        &mut self,
        done: &FsWriteStreamDoneParams,
    ) -> Result<(), JSONRPCErrorError> {
        self.write_event(&FsHelperWriteStreamEvent::Finish(done.clone()))
            .await
    }

    /// 读取最终确认（helper stdout 末行）：Ok 为 writeStream/done 响应；
    /// helper 报告的业务错误（乱序/缺 eof/落盘失败等）原样透传；
    /// EOF/帧损坏归为内部错误并附带 helper 退出状态与 stderr 摘要。
    pub(crate) async fn read_done(
        &mut self,
    ) -> Result<FsWriteStreamDoneResponse, JSONRPCErrorError> {
        match self.stdout.next_line().await.map_err(io_error)? {
            Some(line) => {
                let response: FsHelperResponse = serde_json::from_str(&line).map_err(json_error)?;
                match response {
                    FsHelperResponse::Ok(payload) => payload.expect_write_stream_done(),
                    FsHelperResponse::Error(error) => Err(error),
                }
            }
            None => Err(self
                .helper_exit_error("before confirming the write stream")
                .await),
        }
    }

    /// 回收 helper（同读流：限时等待自行退出，超时强杀收尸 + stderr 收编）。
    pub(crate) async fn finish(self) {
        finish_fs_helper(self.child, self.stderr_drain, "write").await;
    }

    /// 写一行 NDJSON 事件帧并 flush（每帧即时送达，不攒在缓冲里拖住写流）。
    async fn write_event(
        &mut self,
        event: &FsHelperWriteStreamEvent,
    ) -> Result<(), JSONRPCErrorError> {
        let encoded = serde_json::to_vec(event).map_err(json_error)?;
        self.stdin.write_all(&encoded).await.map_err(io_error)?;
        self.stdin.write_all(b"\n").await.map_err(io_error)?;
        self.stdin.flush().await.map_err(io_error)
    }

    async fn helper_exit_error(&mut self, context: &str) -> JSONRPCErrorError {
        fs_helper_exit_error(&mut self.child, &mut self.stderr_drain, context).await
    }
}

fn sandbox_cwd(sandbox: &FileSystemSandboxContext) -> Result<SandboxCwd, JSONRPCErrorError> {
    if let Some(uri) = &sandbox.cwd {
        return Ok(SandboxCwd {
            native: native_sandbox_cwd(uri)?,
            uri: uri.clone(),
        });
    }

    if sandbox.has_cwd_dependent_permissions() {
        return Err(invalid_request(
            "file system sandbox context with dynamic permissions requires cwd".to_string(),
        ));
    }

    let native = AbsolutePathBuf::from_absolute_path(current_sandbox_cwd().map_err(io_error)?)
        .map_err(|err| invalid_request(format!("current directory is not absolute: {err}")))?;
    let uri = PathUri::from_abs_path(&native);
    Ok(SandboxCwd { uri, native })
}

fn native_sandbox_cwd(cwd: &PathUri) -> Result<AbsolutePathBuf, JSONRPCErrorError> {
    cwd.to_abs_path()
        .map_err(|err| invalid_request(err.to_string()))
}

fn native_workspace_root(root: &PathUri) -> Result<AbsolutePathBuf, JSONRPCErrorError> {
    root.to_abs_path().map_err(|err| {
        invalid_request(format!(
            "file system sandbox workspace root is not native to this exec-server host: {err}"
        ))
    })
}

fn helper_read_roots(runtime_paths: &ExecServerRuntimePaths) -> Vec<AbsolutePathBuf> {
    // 只读挂根收紧为可执行文件本身：helper 只需要加载这两个 exe，
    // 不给它们的父目录开口（父目录可能还放着其他敏感文件）
    let mut roots = vec![runtime_paths.executor_self_exe.clone()];
    if let Some(path) = &runtime_paths.executor_linux_sandbox_exe
        && !roots.contains(path)
    {
        roots.push(path.clone());
    }
    roots
}

fn add_helper_runtime_permissions(
    file_system_policy: &mut FileSystemSandboxPolicy,
    helper_read_roots: &[AbsolutePathBuf],
    cwd: &std::path::Path,
) {
    if !file_system_policy.has_full_disk_read_access() {
        let minimal_read_entry = FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Minimal,
            },
            FileSystemAccessMode::Read,
        );
        if !file_system_policy.entries.contains(&minimal_read_entry) {
            file_system_policy.entries.push(minimal_read_entry);
        }
    }

    for helper_read_root in helper_read_roots {
        if file_system_policy.can_read_path_with_cwd(helper_read_root.as_path(), cwd) {
            continue;
        }

        file_system_policy.entries.push(FileSystemSandboxEntry::new(
            helper_read_root.clone().into(),
            FileSystemAccessMode::Read,
        ));
    }
}

fn normalize_file_system_policy_root_aliases(file_system_policy: &mut FileSystemSandboxPolicy) {
    for entry in &mut file_system_policy.entries {
        // 别名规整用的是本执行端的文件系统；外来平台或 opaque 的 PathUri 原样保留。
        if let FileSystemPath::Path { path } = &mut entry.path
            && let Ok(native_path) = path.to_abs_path()
        {
            *path = normalize_top_level_alias(native_path).into();
        }
    }
}

fn normalize_top_level_alias(path: AbsolutePathBuf) -> AbsolutePathBuf {
    let raw_path = path.to_path_buf();
    for ancestor in raw_path.ancestors() {
        if std::fs::symlink_metadata(ancestor).is_err() {
            continue;
        }
        let Ok(normalized_ancestor) = canonicalize_preserving_symlinks(ancestor) else {
            continue;
        };
        if normalized_ancestor == ancestor {
            continue;
        }
        let Ok(suffix) = raw_path.strip_prefix(ancestor) else {
            continue;
        };
        if let Ok(normalized_path) =
            AbsolutePathBuf::from_absolute_path(normalized_ancestor.join(suffix))
        {
            return normalized_path;
        }
    }
    path
}

fn helper_env() -> HashMap<String, String> {
    helper_env_from_vars(std::env::vars_os())
}

fn helper_env_from_vars(
    vars: impl IntoIterator<Item = (std::ffi::OsString, std::ffi::OsString)>,
) -> HashMap<String, String> {
    vars.into_iter()
        .filter_map(|(key, value)| {
            let key = key.to_string_lossy();
            helper_env_key_is_allowed(&key)
                .then(|| (key.into_owned(), value.to_string_lossy().into_owned()))
        })
        .collect()
}

fn helper_env_key_is_allowed(key: &str) -> bool {
    FS_HELPER_ENV_ALLOWLIST.contains(&key)
        // CoreFoundation consults this before falling back to user lookup during helper startup.
        || (cfg!(target_os = "macos") && key == "__CF_USER_TEXT_ENCODING")
        || bazel_bwrap_env_key_is_allowed(key)
        || (cfg!(windows) && key.eq_ignore_ascii_case("PATH"))
}

#[cfg(debug_assertions)]
fn bazel_bwrap_env_key_is_allowed(key: &str) -> bool {
    option_env!("BAZEL_PACKAGE").is_some() && FS_HELPER_BAZEL_BWRAP_ENV_ALLOWLIST.contains(&key)
}

#[cfg(not(debug_assertions))]
fn bazel_bwrap_env_key_is_allowed(_key: &str) -> bool {
    false
}

async fn run_command(
    command: SandboxExecRequest,
    request_json: Vec<u8>,
) -> Result<FsHelperPayload, JSONRPCErrorError> {
    let mut child = spawn_command(command, std::process::Stdio::piped())?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| internal_error("failed to open fs sandbox helper stdin".to_string()))?;
    stdin.write_all(&request_json).await.map_err(io_error)?;
    stdin.shutdown().await.map_err(io_error)?;
    drop(stdin);

    let output = wait_for_helper_output(child).await?;
    let response: FsHelperResponse = serde_json::from_slice(&output.stdout).map_err(json_error)?;
    match response {
        FsHelperResponse::Ok(payload) => Ok(payload),
        FsHelperResponse::Error(error) => Err(error),
    }
}

/// 等待一次性 helper 退出并收集其输出：非零退出即内部错误（附 stderr 摘要）。
/// 一次性请求与沙箱开门（sandboxed_file_open）共用。
pub(crate) async fn wait_for_helper_output(
    child: tokio::process::Child,
) -> Result<std::process::Output, JSONRPCErrorError> {
    let output = child.wait_with_output().await.map_err(io_error)?;
    if !output.status.success() {
        return Err(internal_error(format!(
            "fs sandbox helper failed with status {status}: {stderr}",
            status = output.status,
            stderr = String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(output)
}

/// 读取 helper stdout 的一行响应帧（Windows 开门路径：helper 回完响应行后
/// 仍保活句柄等待 ack，不能 wait 退出再读）。
#[cfg(windows)]
pub(crate) async fn read_helper_response(
    stdout: impl tokio::io::AsyncRead + Unpin,
) -> Result<Vec<u8>, JSONRPCErrorError> {
    let mut response = Vec::new();
    let bytes_read = tokio::io::BufReader::new(stdout)
        .read_until(b'\n', &mut response)
        .await
        .map_err(io_error)?;
    if bytes_read == 0 {
        return Err(internal_error(
            "fs sandbox helper closed stdout without responding".to_string(),
        ));
    }
    Ok(response)
}

/// helper stderr 的后台限量排空（超出上限丢弃余量但继续排到 EOF，仅作诊断）。
#[cfg(windows)]
pub(crate) fn drain_helper_stderr(
    child: &mut tokio::process::Child,
) -> tokio::task::JoinHandle<Result<Vec<u8>, std::io::Error>> {
    let stderr_pipe = child.stderr.take();
    tokio::spawn(async move {
        let mut stderr = Vec::new();
        if let Some(mut stderr_pipe) = stderr_pipe {
            (&mut stderr_pipe)
                .take(MAX_FS_HELPER_STDERR_BYTES)
                .read_to_end(&mut stderr)
                .await?;
            tokio::io::copy(&mut stderr_pipe, &mut tokio::io::sink()).await?;
        }
        Ok::<_, std::io::Error>(stderr)
    })
}

/// 响应读取完毕后回收 helper（Windows 开门路径：ack 已发，helper 应立即退出；
/// 超时不退则强杀收尸），并按退出状态把 helper 失败归为内部错误。
#[cfg(windows)]
pub(crate) async fn reap_helper_after_response(
    mut child: tokio::process::Child,
    stderr: tokio::task::JoinHandle<Result<Vec<u8>, std::io::Error>>,
) -> Result<(), JSONRPCErrorError> {
    let (status, stderr) = match tokio::time::timeout(FS_HELPER_EXIT_TIMEOUT, async {
        tokio::try_join!(child.wait(), async {
            stderr.await.map_err(std::io::Error::other)?
        })
    })
    .await
    {
        Ok(result) => result.map_err(io_error)?,
        Err(_) => {
            tokio::time::timeout(FS_HELPER_EXIT_TIMEOUT, child.kill())
                .await
                .map_err(|_| {
                    internal_error("fs sandbox helper did not stop after its response".to_string())
                })?
                .map_err(io_error)?;
            return Ok(());
        }
    };
    if status.success() {
        return Ok(());
    }

    Err(internal_error(format!(
        "fs sandbox helper failed with status {status}: {stderr}",
        stderr = String::from_utf8_lossy(&stderr).trim()
    )))
}

pub(crate) fn spawn_command(
    SandboxExecRequest {
        command: argv,
        cwd,
        mut env,
        arg0,
        ..
    }: SandboxExecRequest,
    stdin: std::process::Stdio,
) -> Result<tokio::process::Child, JSONRPCErrorError> {
    let Some((program, args)) = argv.split_first() else {
        return Err(invalid_request("fs sandbox command was empty".to_string()));
    };
    let mut command = Command::new(program);
    #[cfg(unix)]
    if let Some(arg0) = arg0 {
        command.arg0(arg0);
    }
    #[cfg(not(unix))]
    let _ = arg0;
    command.args(args);
    // TODO(anp): Keep PathUri through the filesystem helper launch boundary.
    let cwd = cwd.to_abs_path().map_err(io_error)?;
    command.current_dir(cwd.as_path());
    env.retain(|name, _| {
        !nova_executor_protocol_core::shell_environment::is_non_inheritable_env_var(name)
    });
    command.env_clear();
    command.envs(env);
    command.stdin(stdin);
    command.stdout(std::process::Stdio::piped());
    command.stderr(std::process::Stdio::piped());
    command.kill_on_drop(true);
    // macOS cannot receive passed fds with close-on-exec set atomically.
    #[cfg(target_os = "macos")]
    // SAFETY: Descriptor cleanup only uses fork-safe system calls.
    unsafe {
        command.pre_exec(|| {
            nova_executor_utils_pty::pty::close_inherited_fds_except(&[]);
            Ok(())
        });
    }
    command.spawn().map_err(io_error)
}

pub(crate) fn io_error(err: std::io::Error) -> JSONRPCErrorError {
    internal_error(err.to_string())
}

fn json_error(err: serde_json::Error) -> JSONRPCErrorError {
    internal_error(format!(
        "failed to encode or decode fs sandbox helper message: {err}"
    ))
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::ffi::OsString;

    use nova_executor_protocol_core::models::PermissionProfile;
    use nova_executor_protocol_core::permissions::FileSystemAccessMode;
    use nova_executor_protocol_core::permissions::FileSystemPath;
    use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
    use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
    use nova_executor_protocol_core::permissions::FileSystemSpecialPath;
    use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
    use nova_executor_utils_absolute_path::AbsolutePathBuf;
    use nova_executor_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;

    use crate::ExecServerRuntimePaths;

    use super::FileSystemSandboxRunner;
    use super::SandboxCwd;
    use super::add_helper_runtime_permissions;
    use super::helper_env;
    use super::helper_env_from_vars;
    use super::helper_env_key_is_allowed;
    use super::helper_read_roots;
    use super::sandbox_cwd;

    #[test]
    fn helper_permissions_enable_minimal_reads_for_restricted_profile() {
        let cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let mut policy = restricted_policy(Vec::new());

        add_helper_runtime_permissions(&mut policy, /*helper_read_roots*/ &[], cwd.as_path());

        assert!(policy.include_platform_defaults());
    }

    #[test]
    fn helper_permissions_enable_minimal_reads_for_restricted_profile_with_writes() {
        let cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let mut policy = restricted_policy(vec![path_entry(
            cwd.join("writable"),
            FileSystemAccessMode::Write,
        )]);

        add_helper_runtime_permissions(&mut policy, /*helper_read_roots*/ &[], cwd.as_path());

        assert!(policy.include_platform_defaults());
    }

    #[test]
    fn helper_permissions_preserve_existing_writes() {
        let executor_self_exe = std::env::current_exe().expect("current exe");
        let runtime_paths = ExecServerRuntimePaths::new(
            executor_self_exe,
            /*executor_linux_sandbox_exe*/ None,
        )
        .expect("runtime paths");
        let cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let writable = cwd.join("writable");
        let mut policy = restricted_policy(vec![path_entry(
            writable.clone(),
            FileSystemAccessMode::Write,
        )]);
        let readable = runtime_paths.executor_self_exe.clone();

        add_helper_runtime_permissions(
            &mut policy,
            &helper_read_roots(&runtime_paths),
            cwd.as_path(),
        );

        assert!(policy.can_read_path_with_cwd(readable.as_path(), cwd.as_path()));
        assert!(policy.can_write_path_with_cwd(writable.as_path(), cwd.as_path()));
    }

    #[test]
    fn helper_env_carries_only_allowlisted_runtime_vars() {
        let env = helper_env();

        let expected = std::env::vars_os()
            .filter_map(|(key, value)| {
                let key = key.to_string_lossy();
                helper_env_key_is_allowed(&key)
                    .then(|| (key.into_owned(), value.to_string_lossy().into_owned()))
            })
            .collect::<HashMap<_, _>>();

        assert_eq!(env, expected);
    }

    #[test]
    fn helper_env_preserves_path_for_system_bwrap_discovery_without_leaking_secrets() {
        let env = helper_env_from_vars(
            [
                ("PATH", "/usr/bin:/bin"),
                ("TMPDIR", "/tmp/codex"),
                ("TMP", "/tmp"),
                ("TEMP", "/tmp"),
                ("HOME", "/home/user"),
                ("OPENAI_API_KEY", "secret"),
                ("HTTPS_PROXY", "http://proxy.example"),
            ]
            .map(|(key, value)| (OsString::from(key), OsString::from(value))),
        );

        assert_eq!(
            env,
            HashMap::from([
                ("PATH".to_string(), "/usr/bin:/bin".to_string()),
                ("TMPDIR".to_string(), "/tmp/codex".to_string()),
                ("TMP".to_string(), "/tmp".to_string()),
                ("TEMP".to_string(), "/tmp".to_string()),
            ])
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn helper_env_preserves_corefoundation_text_encoding() {
        let env = helper_env_from_vars(
            [
                ("__CF_USER_TEXT_ENCODING", "0x1F6:0x0:0x0"),
                ("HOME", "/Users/test"),
            ]
            .map(|(key, value)| (OsString::from(key), OsString::from(value))),
        );

        assert_eq!(
            env,
            HashMap::from([(
                "__CF_USER_TEXT_ENCODING".to_string(),
                "0x1F6:0x0:0x0".to_string(),
            )])
        );
    }

    #[cfg(windows)]
    #[test]
    fn helper_env_preserves_windows_path_key_for_system_bwrap_discovery() {
        let env = helper_env_from_vars(
            [
                ("Path", r"C:\Windows\System32"),
                ("PATH_INJECTION", "bad"),
                ("OPENAI_API_KEY", "secret"),
            ]
            .map(|(key, value)| (OsString::from(key), OsString::from(value))),
        );

        assert_eq!(
            env,
            HashMap::from([("Path".to_string(), r"C:\Windows\System32".to_string())])
        );
    }

    #[test]
    fn sandbox_exec_request_carries_helper_env() {
        let Some((path_key, path)) = std::env::vars_os().find(|(key, _)| {
            let key = key.to_string_lossy();
            key == "PATH" || (cfg!(windows) && key.eq_ignore_ascii_case("PATH"))
        }) else {
            return;
        };
        let path_key = path_key.to_string_lossy().into_owned();
        let path = path.to_string_lossy().into_owned();
        let executor_self_exe = std::env::current_exe().expect("current exe");
        let runtime_paths =
            ExecServerRuntimePaths::new(executor_self_exe.clone(), Some(executor_self_exe))
                .expect("runtime paths");
        let runner = FileSystemSandboxRunner::new(runtime_paths);
        let native_cwd = AbsolutePathBuf::current_dir().expect("cwd");
        let cwd = PathUri::from_abs_path(&native_cwd);
        let file_system_policy = restricted_policy(vec![
            #[cfg(windows)]
            special_entry(FileSystemSpecialPath::Root, FileSystemAccessMode::Read),
            path_entry(native_cwd.clone(), FileSystemAccessMode::Write),
        ]);
        let network_policy = NetworkSandboxPolicy::Restricted;
        let permission_profile =
            PermissionProfile::from_runtime_permissions(&file_system_policy, network_policy);
        let sandbox_context = sandbox_context_with_cwd(&file_system_policy, cwd.clone());
        let sandbox_cwd = SandboxCwd {
            uri: cwd,
            native: native_cwd,
        };
        #[cfg(windows)]
        let sandbox_context = {
            let error = runner
                .sandbox_exec_request(
                    &permission_profile,
                    &sandbox_cwd,
                    std::slice::from_ref(&sandbox_cwd.native),
                    &sandbox_context,
                )
                .expect_err("disabled Windows sandbox must not run the helper unsandboxed");
            assert_eq!(
                error.message,
                "filesystem sandbox cannot be enforced on this executor"
            );
            crate::FileSystemSandboxContext {
                windows_sandbox_level:
                    nova_executor_protocol_core::config_types::WindowsSandboxLevel::RestrictedToken,
                ..sandbox_context
            }
        };

        let request = runner
            .sandbox_exec_request(
                &permission_profile,
                &sandbox_cwd,
                std::slice::from_ref(&sandbox_cwd.native),
                &sandbox_context,
            )
            .expect("sandbox exec request");

        assert_eq!(request.env.get(&path_key), Some(&path));
    }

    #[test]
    fn sandbox_cwd_uses_context_cwd() {
        let native_cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let cwd = PathUri::from_abs_path(&native_cwd);
        let policy = restricted_policy(vec![special_entry(
            FileSystemSpecialPath::project_roots(/*subpath*/ None),
            FileSystemAccessMode::Write,
        )]);
        let sandbox_context = sandbox_context_with_cwd(&policy, cwd.clone());

        assert_eq!(
            sandbox_cwd(&sandbox_context).expect("sandbox cwd"),
            SandboxCwd {
                uri: cwd,
                native: native_cwd
            }
        );
    }

    #[test]
    fn sandbox_cwd_rejects_non_native_context_cwd_without_fallback() {
        let cwd = non_native_cwd();
        let policy = restricted_policy(vec![special_entry(
            FileSystemSpecialPath::project_roots(/*subpath*/ None),
            FileSystemAccessMode::Write,
        )]);
        let sandbox_context = sandbox_context_with_cwd(&policy, cwd.clone());

        let err = sandbox_cwd(&sandbox_context).expect_err("non-native cwd should be rejected");

        assert_eq!(
            err,
            crate::rpc::invalid_request(format!(
                "'{cwd}' is invalid on '{}'",
                std::env::consts::OS
            ))
        );
    }

    #[test]
    fn sandbox_cwd_rejects_cwd_dependent_profile_without_context_cwd() {
        let policy = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::project_roots(/*subpath*/ None),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        }]);
        let sandbox_context =
            nova_executor_file_system::FileSystemSandboxContext::from_permission_profile(
                PermissionProfile::from_runtime_permissions(
                    &policy,
                    NetworkSandboxPolicy::Restricted,
                ),
            );

        let err = sandbox_cwd(&sandbox_context).expect_err("missing cwd should be rejected");

        assert_eq!(
            err.message,
            "file system sandbox context with dynamic permissions requires cwd"
        );
    }

    #[test]
    fn helper_permissions_include_only_the_helper_executable() {
        let executor_self_exe = std::env::current_exe().expect("current exe");
        let runtime_paths = ExecServerRuntimePaths::new(
            executor_self_exe,
            /*executor_linux_sandbox_exe*/ None,
        )
        .expect("runtime paths");
        let cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let mut policy = restricted_policy(Vec::new());
        let parent = runtime_paths
            .executor_self_exe
            .parent()
            .expect("current exe parent");
        let sibling = parent.join("credentials.json");

        add_helper_runtime_permissions(
            &mut policy,
            &helper_read_roots(&runtime_paths),
            cwd.as_path(),
        );

        assert!(
            policy.can_read_path_with_cwd(runtime_paths.executor_self_exe.as_path(), cwd.as_path())
        );
        assert!(!policy.can_read_path_with_cwd(parent.as_path(), cwd.as_path()));
        assert!(!policy.can_read_path_with_cwd(sibling.as_path(), cwd.as_path()));
    }

    #[test]
    fn helper_permissions_include_only_linux_sandbox_alias_executable() {
        let root = tempfile::tempdir().expect("temp dir");
        let executor_self_exe = root.path().join("bin").join("codex");
        let executor_linux_sandbox_exe = root.path().join("aliases").join("codex-linux-sandbox");
        let runtime_paths =
            ExecServerRuntimePaths::new(executor_self_exe, Some(executor_linux_sandbox_exe))
                .expect("runtime paths");
        let cwd = AbsolutePathBuf::from_absolute_path(std::env::temp_dir().as_path())
            .expect("absolute cwd");
        let mut policy = restricted_policy(Vec::new());
        let codex_parent = runtime_paths
            .executor_self_exe
            .parent()
            .expect("codex parent");
        let alias = runtime_paths
            .executor_linux_sandbox_exe
            .as_ref()
            .expect("linux sandbox alias");
        let alias_parent = alias.parent().expect("alias parent");

        add_helper_runtime_permissions(
            &mut policy,
            &helper_read_roots(&runtime_paths),
            cwd.as_path(),
        );

        assert!(
            policy.can_read_path_with_cwd(runtime_paths.executor_self_exe.as_path(), cwd.as_path())
        );
        assert!(policy.can_read_path_with_cwd(alias.as_path(), cwd.as_path()));
        assert!(!policy.can_read_path_with_cwd(codex_parent.as_path(), cwd.as_path()));
        assert!(!policy.can_read_path_with_cwd(alias_parent.as_path(), cwd.as_path()));
    }

    fn restricted_policy(entries: Vec<FileSystemSandboxEntry>) -> FileSystemSandboxPolicy {
        FileSystemSandboxPolicy::restricted(entries)
    }

    fn sandbox_context_with_cwd(
        policy: &FileSystemSandboxPolicy,
        cwd: PathUri,
    ) -> crate::FileSystemSandboxContext {
        nova_executor_file_system::FileSystemSandboxContext::from_permission_profile_with_cwd(
            PermissionProfile::from_runtime_permissions(policy, NetworkSandboxPolicy::Restricted),
            cwd,
        )
    }

    fn non_native_cwd() -> PathUri {
        #[cfg(unix)]
        let uri = "file://server/share/checkout";
        #[cfg(windows)]
        let uri = "file:///usr/local/checkout";

        PathUri::parse(uri).expect("non-native cwd URI")
    }

    fn path_entry(path: AbsolutePathBuf, access: FileSystemAccessMode) -> FileSystemSandboxEntry {
        FileSystemSandboxEntry {
            path: FileSystemPath::Path { path: path.into() },
            access,
            missing_path_behavior: None,
        }
    }

    fn special_entry(
        value: FileSystemSpecialPath,
        access: FileSystemAccessMode,
    ) -> FileSystemSandboxEntry {
        FileSystemSandboxEntry {
            path: FileSystemPath::Special { value },
            access,
            missing_path_behavior: None,
        }
    }
}
