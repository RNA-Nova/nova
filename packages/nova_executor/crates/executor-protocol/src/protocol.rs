use std::collections::HashMap;

use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use nova_executor_file_system::FileSystemSandboxContext;
pub use nova_executor_file_system::WalkOptions;
pub use nova_executor_file_system::WalkOutcome;
use nova_executor_network_proxy::ManagedNetworkSandboxContext;
use nova_executor_network_proxy::RemoteNetworkProxyLaunchConfig;
use nova_executor_protocol_core::config_types::ShellEnvironmentPolicyInherit;
use nova_executor_shell_command::shell_detect::DetectedShell;
use nova_executor_utils_path_uri::PathUri;
use serde::Deserialize;
use serde::Serialize;

use crate::ProcessId;

pub const INITIALIZE_METHOD: &str = "initialize";
pub const INITIALIZED_METHOD: &str = "initialized";
pub const EXEC_METHOD: &str = "process/start";
pub const EXEC_READ_METHOD: &str = "process/read";
pub const EXEC_WRITE_METHOD: &str = "process/write";
pub const EXEC_SIGNAL_METHOD: &str = "process/signal";
pub const EXEC_TERMINATE_METHOD: &str = "process/terminate";
pub const EXEC_OUTPUT_DELTA_METHOD: &str = "process/output";
pub const EXEC_EXITED_METHOD: &str = "process/exited";
pub const EXEC_CLOSED_METHOD: &str = "process/closed";
pub const ENVIRONMENT_INFO_METHOD: &str = "environment/info";
pub const ENVIRONMENT_STATUS_METHOD: &str = "environment/status";
pub const FS_READ_FILE_METHOD: &str = "fs/readFile";
pub const FS_OPEN_METHOD: &str = "fs/open";
pub const FS_READ_BLOCK_METHOD: &str = "fs/readBlock";
pub const FS_CLOSE_METHOD: &str = "fs/close";
pub const FS_READ_STREAM_METHOD: &str = "fs/readStream";
pub const FS_READ_STREAM_CHUNK_METHOD: &str = "fs/readStream/chunk";
pub const FS_READ_STREAM_DONE_METHOD: &str = "fs/readStream/done";
pub const FS_WRITE_FILE_METHOD: &str = "fs/writeFile";
pub const FS_WRITE_STREAM_METHOD: &str = "fs/writeStream";
pub const FS_WRITE_STREAM_CHUNK_METHOD: &str = "fs/writeStream/chunk";
pub const FS_WRITE_STREAM_DONE_METHOD: &str = "fs/writeStream/done";
pub const FS_CREATE_DIRECTORY_METHOD: &str = "fs/createDirectory";
pub const FS_GET_METADATA_METHOD: &str = "fs/getMetadata";
pub const FS_CANONICALIZE_METHOD: &str = "fs/canonicalize";
pub const FS_READ_DIRECTORY_METHOD: &str = "fs/readDirectory";
pub const FS_WALK_METHOD: &str = "fs/walk";
pub const FS_REMOVE_METHOD: &str = "fs/remove";
pub const FS_COPY_METHOD: &str = "fs/copy";
/// JSON-RPC request method for executor-side HTTP requests.
pub const HTTP_REQUEST_METHOD: &str = "http/request";
/// JSON-RPC notification method for streamed executor HTTP response bodies.
pub const HTTP_REQUEST_BODY_DELTA_METHOD: &str = "http/request/bodyDelta";
/// Maximum decoded response-body bytes carried by one streamed HTTP notification.
pub const MAX_HTTP_BODY_DELTA_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ByteChunk(#[serde(with = "base64_bytes")] pub Vec<u8>);

impl ByteChunk {
    pub fn into_inner(self) -> Vec<u8> {
        self.0
    }
}

impl From<Vec<u8>> for ByteChunk {
    fn from(value: Vec<u8>) -> Self {
        Self(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeParams {
    pub client_name: String,
    #[serde(default)]
    pub resume_session_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeResponse {
    pub session_id: String,
    /// 服务端协议版本（客户端据此做 major 匹配——旧服务端缺省视为 0.x）。
    #[serde(default)]
    pub protocol_version: String,
    /// 初始化捎带的执行端元数据，形状与 `environment/info` 一致（省客户端一次往返）。
    // TODO: 待所有支持的 exec-server 版本都返回 environmentInfo 后改为必填。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub environment_info: Option<EnvironmentInfo>,
}

/// Information about an execution/filesystem environment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentInfo {
    pub shell: ShellInfo,
    /// Working directory inherited by the exec-server process.
    #[serde(default)]
    pub cwd: Option<PathUri>,
    /// Executor user home used to expand `~` in path-bearing values.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_home_dir: Option<PathUri>,
    /// Operating system reported by the executor; absent for legacy exec-servers.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub platform_os: Option<String>,
    /// Executor-local default directories for resolving `:tmpdir`, when reported.
    /// On Windows, a command's `TEMP` or `TMP` overrides take precedence.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temporary_directories: Option<Vec<PathUri>>,
    /// Executor-native temporary directory for child-visible sidecars.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temp_dir: Option<PathUri>,
    /// Optional executor features that clients must gate before sending newer request fields.
    #[serde(default)]
    pub capabilities: EnvironmentCapabilities,
}

/// Features supported by the selected exec-server environment.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentCapabilities {
    /// Whether `exec` accepts instructions for launching an executor-local network proxy.
    #[serde(default)]
    pub network_proxy_launch: bool,
    /// Whether this executor supports the `environmentConfig/read` request.
    #[serde(default)]
    pub environment_config_read: bool,
    /// Whether this executor supports `fs/readStream`（大文件流式读，服务端推送）。
    #[serde(default)]
    pub read_stream: bool,
    /// Whether this executor supports `fs/writeStream`（大文件流式写，客户端分片推）。
    #[serde(default)]
    pub write_stream: bool,
    /// Whether shell state can be cached and restored entirely inside the executor.
    #[serde(default)]
    pub shell_snapshot_v2: bool,
}

/// Status returned by an initialized exec-server connection.
///
/// The response is intentionally small today. New status details can be added
/// without changing the method used by clients to verify that an initialized
/// exec-server connection is still responsive.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentStatus {
    pub status: EnvironmentStatusKind,
}

/// High-level status reported by exec-server itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum EnvironmentStatusKind {
    /// The connection is initialized and exec-server can handle requests.
    Ready,
}

impl EnvironmentInfo {
    /// Returns information about the current local exec-server process.
    pub fn local() -> Self {
        let cwd = std::env::current_dir().ok();
        let temporary_directory_env_vars: &[&str] = if cfg!(windows) {
            &["TEMP", "TMP"]
        } else {
            &["TMPDIR"]
        };
        // 临时目录路径规整：优先按本机路径直接转 URI；unix 下相对路径（如
        // 相对 TMPDIR）回退按 cwd 拼接后再转。temporary_directories 与
        // temp_dir 共用同一规整，保证两者语义一致。
        let normalize_temp_path = |path: std::ffi::OsString| {
            PathUri::from_host_native_path(&path).ok().or_else(|| {
                if cfg!(unix) {
                    PathUri::from_host_native_path(cwd.as_ref()?.join(path)).ok()
                } else {
                    None
                }
            })
        };
        let mut temporary_directories = Vec::new();
        for name in temporary_directory_env_vars {
            if let Some(path) = std::env::var_os(name)
                .filter(|path| !path.is_empty())
                .filter(|path| cfg!(unix) || std::path::Path::new(path).is_absolute())
                .and_then(&normalize_temp_path)
                && !temporary_directories.contains(&path)
            {
                temporary_directories.push(path);
            }
        }
        let temp_dir = normalize_temp_path(std::env::temp_dir().into_os_string());

        Self {
            shell: nova_executor_shell_command::shell_detect::default_user_shell().into(),
            cwd: cwd.and_then(|cwd| PathUri::from_host_native_path(cwd).ok()),
            // 家目录经 `~` 展开取绝对路径（底层即 absolute-path crate 的 home 解析，
            // 再往下是 dirs::home_dir）——与客户端展开的 `~` 同一语义
            user_home_dir: PathUri::from_host_native_path("~").ok(),
            platform_os: Some(std::env::consts::OS.to_string()),
            temporary_directories: Some(temporary_directories),
            temp_dir,
            capabilities: EnvironmentCapabilities {
                // 托管网络代理由 executor-network-proxy 真实承载（process/start 的
                // networkProxy 启动指令 + network/policyRequest 回调 +
                // network/policyDecision 审计通知）——如实宣告 true
                network_proxy_launch: true,
                // environmentConfig/read 已恢复（v1.4 nova 语义：executor 代读
                // 本机 user/project 配置层回传，不合并不裁决）——能力位如实宣告 true
                environment_config_read: true,
                // fs/readStream 与 fs/writeStream 为本 executor 自有端点（已注册
                // 并含沙箱化实现）——如实宣告 true，客户端可据此选用流式通道
                read_stream: true,
                write_stream: true,
                // shell snapshot（登录 shell 状态缓存/恢复）仅在 unix 落地——
                // 非 unix 端如实宣告 false，客户端按位门控后再下发 shellSnapshot
                shell_snapshot_v2: cfg!(unix),
            },
        }
    }
}

/// Shell detected for an execution/filesystem environment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ShellInfo {
    /// Stable shell name, for example `zsh`, `bash`, `powershell`, `sh`, or `cmd`.
    pub name: String,
    /// Target-native shell executable path or command name. Fallbacks such as `cmd.exe` need not
    /// be absolute, so this is not a [`PathUri`].
    pub path: String,
}

impl From<DetectedShell> for ShellInfo {
    fn from(shell: DetectedShell) -> Self {
        Self {
            name: shell.name().to_string(),
            path: shell.shell_path.to_string_lossy().into_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecParams {
    /// Client-chosen logical process handle scoped to this connection/session.
    /// This is a protocol key, not an OS pid.
    pub process_id: ProcessId,
    pub argv: Vec<String>,
    /// Working directory URI, interpreted using the exec-server host's path rules at launch time.
    pub cwd: PathUri,
    #[serde(default)]
    pub env_policy: Option<ExecEnvPolicy>,
    /// Optional request to restore executor-owned, attachment-scoped shell state.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shell_snapshot: Option<ShellSnapshotRequest>,
    pub env: HashMap<String, String>,
    pub tty: bool,
    /// Keep non-tty stdin writable through `process/write`.
    #[serde(default)]
    pub pipe_stdin: bool,
    /// Optional process-visible argv0 override. Values such as `nova-linux-sandbox` are command
    /// names rather than paths, so this is not a [`PathUri`].
    pub arg0: Option<String>,
    /// Portable sandbox intent. Concrete wrapper argv is resolved by the exec-server.
    #[serde(default)]
    pub sandbox: Option<FileSystemSandboxContext>,
    /// Whether the eventual executor-side sandbox must enforce managed networking.
    #[serde(default)]
    pub enforce_managed_network: bool,
    /// Optional details for enforcing managed networking without a live proxy object.
    ///
    /// When `enforce_managed_network` is true and these details are absent, the executor must
    /// continue to fail closed. This preserves compatibility with older clients.
    #[serde(default)]
    pub managed_network: Option<ManagedNetworkSandboxContext>,
    /// Optional instructions for starting an executor-local managed-network proxy.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub network_proxy: Option<RemoteNetworkProxyLaunchConfig>,
}

/// Identifies shell state owned by one attachment within an executor session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ShellSnapshotRequest {
    /// Attachment identity; executor sessions independently scope every cache.
    pub scope_id: String,
    /// Executor-native shell used to capture and restore the snapshot.
    pub shell: ShellInfo,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecEnvPolicy {
    pub inherit: ShellEnvironmentPolicyInherit,
    pub ignore_default_excludes: bool,
    pub exclude: Vec<String>,
    pub r#set: HashMap<String, String>,
    pub include_only: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecResponse {
    pub process_id: ProcessId,
    /// `None` means the peer did not report its sandbox type. Current peers
    /// report [`ProcessSandboxType::None`] when the process was not sandboxed.
    #[serde(default)]
    pub sandbox_type: Option<ProcessSandboxType>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ProcessSandboxType {
    /// The process was explicitly started without a platform sandbox.
    None,
    MacosSeatbelt,
    LinuxSeccomp,
    WindowsRestrictedToken,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReadParams {
    pub process_id: ProcessId,
    pub after_seq: Option<u64>,
    pub max_bytes: Option<usize>,
    pub wait_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProcessOutputChunk {
    pub seq: u64,
    pub stream: ExecOutputStream,
    pub chunk: ByteChunk,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReadResponse {
    pub chunks: Vec<ProcessOutputChunk>,
    pub next_seq: u64,
    pub exited: bool,
    pub exit_code: Option<i32>,
    pub closed: bool,
    pub failure: Option<String>,
    /// Whether the executor classified the process failure as a sandbox denial.
    #[serde(default)]
    pub sandbox_denied: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WriteParams {
    pub process_id: ProcessId,
    pub chunk: ByteChunk,
    pub write_id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum WriteStatus {
    Accepted,
    UnknownProcess,
    StdinClosed,
    Starting,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WriteResponse {
    pub status: WriteStatus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ProcessSignal {
    Interrupt,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SignalParams {
    pub process_id: ProcessId,
    pub signal: ProcessSignal,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SignalResponse {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TerminateParams {
    pub process_id: ProcessId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TerminateResponse {
    pub running: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadFileParams {
    pub path: PathUri,
    /// 是否跟随符号链接（缺省 = true，保持旧行为）；false 时逐组件拒绝符号链接
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow_symlinks: Option<bool>,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadFileResponse {
    pub data_base64: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsOpenParams {
    pub handle_id: String,
    pub path: PathUri,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsOpenResponse {
    pub handle_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadBlockParams {
    pub handle_id: String,
    pub offset: u64,
    pub len: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadBlockResponse {
    pub chunk: ByteChunk,
    pub eof: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCloseParams {
    pub handle_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCloseResponse {}

/// 流式读取文件请求
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadStreamParams {
    /// 流式读取句柄 ID，由客户端分配
    pub handle_id: String,
    /// 文件路径
    pub path: PathUri,
    /// 起始偏移量，默认 0
    #[serde(default)]
    pub offset: u64,
    /// 总读取长度，None 表示读到文件末尾
    #[serde(default)]
    pub len: Option<u64>,
    /// 每块大小，默认 256KB
    #[serde(default)]
    pub block_size: Option<usize>,
    /// Sandbox 策略
    #[serde(default)]
    pub sandbox: Option<FileSystemSandboxContext>,
}

/// 流式读取文件响应
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadStreamResponse {
    pub handle_id: String,
    /// 文件总大小（如果已知）
    #[serde(default)]
    pub total_size: Option<u64>,
}

/// 流式读取数据块通知
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadStreamChunkNotification {
    pub handle_id: String,
    pub seq: u64,
    pub chunk: ByteChunk,
    pub eof: bool,
}

/// 流式读取完成通知
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadStreamDoneNotification {
    pub handle_id: String,
    pub total_bytes: u64,
    #[serde(default)]
    pub error: Option<String>,
}

/// 流式写入文件请求（readStream 的方向反转：客户端分片推，服务端顺序落盘）
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteStreamParams {
    /// 流式写入句柄 ID，由客户端分配
    pub handle_id: String,
    /// 文件路径（打开即创建/截断，与 fs/writeFile 语义一致）
    pub path: PathUri,
    /// Sandbox 策略
    #[serde(default)]
    pub sandbox: Option<FileSystemSandboxContext>,
}

/// 流式写入文件响应
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteStreamResponse {
    pub handle_id: String,
}

/// 流式写入数据块通知（客户端 → 服务端）
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteStreamChunkNotification {
    pub handle_id: String,
    /// 从 0 开始的连续序号，服务端严格按序落盘
    pub seq: u64,
    pub chunk: ByteChunk,
    /// 标记最后一个数据块；流仍须以 `fs/writeStream/done` 请求收尾确认
    #[serde(default)]
    pub eof: bool,
}

/// 流式写入完成请求（客户端发 eof 收尾，服务端确认成功/失败）
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteStreamDoneParams {
    pub handle_id: String,
}

/// 流式写入完成响应
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteStreamDoneResponse {
    pub handle_id: String,
    /// 实际落盘的总字节数
    pub total_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteFileParams {
    pub path: PathUri,
    pub data_base64: String,
    /// 是否跟随符号链接（缺省 = true，保持旧行为）；false 时逐组件拒绝符号链接
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow_symlinks: Option<bool>,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWriteFileResponse {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCreateDirectoryParams {
    pub path: PathUri,
    pub recursive: Option<bool>,
    /// 是否跟随符号链接（缺省 = true，保持旧行为）；false 时逐组件拒绝符号链接
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow_symlinks: Option<bool>,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCreateDirectoryResponse {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsGetMetadataParams {
    pub path: PathUri,
    /// 是否跟随符号链接（缺省 = true，保持旧行为）；false 时逐组件拒绝符号链接
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow_symlinks: Option<bool>,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsGetMetadataResponse {
    pub is_directory: bool,
    pub is_file: bool,
    pub is_symlink: bool,
    pub size: u64,
    pub created_at_ms: i64,
    pub modified_at_ms: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCanonicalizeParams {
    pub path: PathUri,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCanonicalizeResponse {
    pub path: PathUri,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadDirectoryParams {
    pub path: PathUri,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadDirectoryEntry {
    pub file_name: String,
    pub is_directory: bool,
    pub is_file: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsReadDirectoryResponse {
    pub entries: Vec<FsReadDirectoryEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsWalkParams {
    pub path: PathUri,
    pub options: WalkOptions,
    pub sandbox: Option<FileSystemSandboxContext>,
}

pub type FsWalkResponse = WalkOutcome;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsRemoveParams {
    pub path: PathUri,
    pub recursive: Option<bool>,
    pub force: Option<bool>,
    /// 是否跟随符号链接（缺省 = true，保持旧行为）；false 时逐组件拒绝符号链接
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub follow_symlinks: Option<bool>,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsRemoveResponse {}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCopyParams {
    pub source_path: PathUri,
    pub destination_path: PathUri,
    pub recursive: bool,
    pub sandbox: Option<FileSystemSandboxContext>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FsCopyResponse {}

/// HTTP header represented in the executor protocol.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpHeader {
    /// Header name as it appears on the HTTP wire.
    pub name: String,
    /// Header value after UTF-8 conversion.
    pub value: String,
}

/// Redirect behavior for an executor-side HTTP request.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum HttpRedirectPolicy {
    /// Follow redirects using the HTTP client's normal limits.
    #[default]
    Follow,
    /// Return the redirect response without following its location.
    Stop,
}

/// Executor-side HTTP request envelope.
///
/// This intentionally stays transport-shaped rather than MCP-shaped so callers
/// can use it for Streamable HTTP, OAuth discovery, and future executor-owned
/// HTTP probes without introducing one protocol method per higher-level use.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpRequestParams {
    /// HTTP method, for example `GET`, `POST`, or `DELETE`.
    pub method: String,
    /// Absolute `http://` or `https://` URL.
    pub url: String,
    /// Ordered request headers. Repeated header names are preserved.
    #[serde(default)]
    pub headers: Vec<HttpHeader>,
    /// Optional request body bytes.
    #[serde(default, rename = "bodyBase64")]
    pub body: Option<ByteChunk>,
    /// Request timeout in milliseconds.
    ///
    /// Omitted or `null` disables the timeout. A number applies that exact
    /// millisecond deadline.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
    /// Whether the executor should follow HTTP redirects.
    #[serde(default)]
    pub redirect_policy: HttpRedirectPolicy,
    /// Caller-chosen stream id for `http/request/bodyDelta` notifications.
    ///
    /// The id must remain unique on a connection until the terminal body delta
    /// arrives, even if the caller stops reading the stream earlier. Buffered
    /// requests still send an id so callers can keep one consistent request
    /// envelope shape.
    pub request_id: String,
    /// Return after response headers and stream the response body as deltas.
    #[serde(default)]
    pub stream_response: bool,
}

/// HTTP response envelope returned from an executor `http/request` call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpRequestResponse {
    /// Numeric HTTP response status code.
    pub status: u16,
    /// Ordered response headers. Repeated header names are preserved.
    pub headers: Vec<HttpHeader>,
    /// Buffered response body bytes. Empty when `streamResponse` is true.
    #[serde(rename = "bodyBase64")]
    pub body: ByteChunk,
}

/// Ordered response-body frame for `streamResponse` HTTP requests.
///
/// Headers are returned in the `http/request` response so the caller can choose
/// a parser immediately; body bytes then arrive on this notification stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HttpRequestBodyDeltaNotification {
    /// Request id from the streamed `http/request` call.
    pub request_id: String,
    /// Monotonic one-based body frame sequence number.
    pub seq: u64,
    /// Response-body bytes carried by this frame.
    #[serde(rename = "deltaBase64")]
    pub delta: ByteChunk,
    /// Marks response-body EOF. No later deltas are expected for this request.
    #[serde(default)]
    pub done: bool,
    /// Terminal stream error. Set only on the final notification.
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ExecOutputStream {
    Stdout,
    Stderr,
    Pty,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecOutputDeltaNotification {
    pub process_id: ProcessId,
    pub seq: u64,
    pub stream: ExecOutputStream,
    pub chunk: ByteChunk,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecExitedNotification {
    pub process_id: ProcessId,
    pub seq: u64,
    pub exit_code: i32,
    #[serde(default)]
    pub sandbox_denied: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecClosedNotification {
    pub process_id: ProcessId,
    pub seq: u64,
}

mod base64_bytes {
    use super::BASE64_STANDARD;
    use base64::Engine as _;
    use serde::Deserialize;
    use serde::Deserializer;
    use serde::Serializer;

    pub fn serialize<S>(bytes: &[u8], serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&BASE64_STANDARD.encode(bytes))
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Vec<u8>, D::Error>
    where
        D: Deserializer<'de>,
    {
        let encoded = String::deserialize(deserializer)?;
        BASE64_STANDARD
            .decode(encoded)
            .map_err(serde::de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::EnvironmentCapabilities;
    use super::EnvironmentInfo;
    use super::ExecExitedNotification;
    use super::ExecParams;
    use super::ExecResponse;
    use super::FsReadFileParams;
    use super::HttpRequestParams;
    use super::InitializeResponse;
    use super::ProcessId;
    use super::ProcessSandboxType;
    use super::ShellInfo;
    use nova_executor_file_system::FileSystemSandboxContext;
    use nova_executor_network_proxy::ManagedNetworkSandboxContext;
    use nova_executor_network_proxy::NetworkProxyAuditMetadata;
    use nova_executor_network_proxy::NetworkProxyConfig;
    use nova_executor_network_proxy::RemoteNetworkProxyConfig;
    use nova_executor_network_proxy::RemoteNetworkProxyLaunchConfig;
    use nova_executor_protocol_core::config_types::WindowsSandboxProxySettingsMode;
    use nova_executor_protocol_core::models::ManagedFileSystemPermissions;
    use nova_executor_protocol_core::models::PermissionProfile;
    use nova_executor_protocol_core::permissions::FileSystemAccessMode;
    use nova_executor_protocol_core::permissions::FileSystemPath;
    use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
    use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
    use nova_executor_protocol_core::permissions::FileSystemSpecialPath;
    use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
    use nova_executor_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;
    use std::collections::HashMap;

    #[test]
    fn exec_params_keeps_proxy_launch_separate_from_sandbox_facts() {
        let cwd =
            PathUri::from_host_native_path(std::env::current_dir().expect("current directory"))
                .expect("cwd URI");
        let params = ExecParams {
            process_id: ProcessId::from("managed-network"),
            argv: vec!["true".to_string()],
            cwd,
            env_policy: None,
            shell_snapshot: None,
            env: HashMap::new(),
            tty: false,
            pipe_stdin: false,
            arg0: None,
            sandbox: None,
            enforce_managed_network: true,
            managed_network: Some(ManagedNetworkSandboxContext {
                loopback_ports: vec![43123, 48081],
                allow_local_binding: false,
            }),
            network_proxy: Some(
                RemoteNetworkProxyLaunchConfig::new(
                    RemoteNetworkProxyConfig::from_effective_config(&NetworkProxyConfig::default())
                        .expect("supported remote config"),
                )
                .with_audit_metadata(NetworkProxyAuditMetadata {
                    conversation_id: Some("conversation-1".to_string()),
                    ..NetworkProxyAuditMetadata::default()
                })
                .for_execution("remote".to_string(), "execution-1".to_string()),
            ),
        };

        let mut serialized = serde_json::to_value(&params).expect("serialize exec params");
        assert_eq!(
            serialized["managedNetwork"],
            serde_json::json!({
                "loopbackPorts": [43123, 48081],
                "allowLocalBinding": false,
            })
        );
        assert_eq!(
            serialized["networkProxy"]["auditMetadata"]["conversationId"],
            "conversation-1"
        );
        let round_trip: ExecParams =
            serde_json::from_value(serialized.clone()).expect("deserialize exec params");
        assert_eq!(round_trip, params);

        serialized
            .as_object_mut()
            .expect("exec params object")
            .remove("managedNetwork");
        serialized
            .as_object_mut()
            .expect("exec params object")
            .remove("networkProxy");
        let legacy: ExecParams =
            serde_json::from_value(serialized).expect("deserialize legacy exec params");
        assert!(legacy.enforce_managed_network);
        assert_eq!(legacy.managed_network, None);
        assert_eq!(legacy.network_proxy, None);
        let legacy_serialized =
            serde_json::to_value(&legacy).expect("serialize exec params without proxy launch");
        assert!(legacy_serialized.get("networkProxy").is_none());
    }

    #[test]
    fn environment_info_accepts_legacy_response_without_cwd() {
        let info: EnvironmentInfo = serde_json::from_value(serde_json::json!({
            "shell": { "name": "zsh", "path": "/bin/zsh" }
        }))
        .expect("legacy environment info should deserialize");

        assert_eq!(
            info,
            EnvironmentInfo {
                shell: ShellInfo {
                    name: "zsh".to_string(),
                    path: "/bin/zsh".to_string(),
                },
                cwd: None,
                user_home_dir: None,
                platform_os: None,
                temporary_directories: None,
                temp_dir: None,
                capabilities: EnvironmentCapabilities::default(),
            }
        );
    }

    #[test]
    fn environment_capabilities_accept_legacy_response_without_environment_config_read() {
        let capabilities: EnvironmentCapabilities = serde_json::from_value(serde_json::json!({
            "networkProxyLaunch": true,
            "capabilityDiscoverySandbox": true,
        }))
        .expect("legacy environment capabilities should deserialize");

        assert_eq!(
            capabilities,
            EnvironmentCapabilities {
                network_proxy_launch: true,
                environment_config_read: false,
                read_stream: false,
                write_stream: false,
                shell_snapshot_v2: false,
            }
        );
    }

    #[test]
    fn environment_info_preserves_executor_temporary_directories() {
        let expected = serde_json::json!({
            "shell": { "name": "powershell", "path": "powershell.exe" },
            "cwd": null,
            "userHomeDir": "file:///C:/Users/remote",
            "platformOs": "windows",
            "temporaryDirectories": ["file:///C:/Temp", "file:///D:/Temp"],
            "tempDir": "file:///C:/Temp",
            "capabilities": {
                "networkProxyLaunch": false,
                "environmentConfigRead": false,
                "readStream": false,
                "writeStream": false,
                "shellSnapshotV2": false,
            },
        });
        let info: EnvironmentInfo = serde_json::from_value(expected.clone())
            .expect("environment info with executor temporary directories should deserialize");

        assert_eq!(
            serde_json::to_value(info).expect("environment info should serialize"),
            expected,
        );
    }

    #[test]
    fn initialize_response_piggybacks_environment_info() {
        // 捎带形态：environmentInfo 随 initialize 响应上线，字段逐一保留。
        let expected = serde_json::json!({
            "sessionId": "session-1",
            "protocolVersion": "1.0",
            "environmentInfo": {
                "shell": { "name": "zsh", "path": "/bin/zsh" },
                "cwd": "file:///Users/test",
                "userHomeDir": "file:///Users/test",
                "platformOs": "macos",
                "tempDir": "file:///tmp",
                "capabilities": {
                    "networkProxyLaunch": false,
                    "environmentConfigRead": false,
                    "readStream": true,
                    "writeStream": true,
                    "shellSnapshotV2": true,
                },
            },
        });
        let response: InitializeResponse = serde_json::from_value(expected.clone())
            .expect("initialize response with environment info should deserialize");
        assert!(response.environment_info.is_some());
        assert_eq!(
            serde_json::to_value(response).expect("initialize response should serialize"),
            expected,
        );
    }

    #[test]
    fn initialize_response_omits_environment_info_for_legacy_server() {
        // 缺省形态：旧服务端无 environmentInfo 字段 → 反序列化为 None，
        // 回序列化也不产出该键（skip_serializing_if 保持线上形状干净）。
        let legacy = serde_json::json!({
            "sessionId": "session-1",
            "protocolVersion": "1.0",
        });
        let response: InitializeResponse = serde_json::from_value(legacy.clone())
            .expect("legacy initialize response should deserialize");
        assert_eq!(response.environment_info, None);
        assert_eq!(
            serde_json::to_value(response).expect("initialize response should serialize"),
            legacy,
        );
    }

    #[test]
    fn local_environment_info_reads_platform_temporary_directories() {
        let cwd = std::env::current_dir().expect("current directory");
        let names: &[&str] = if cfg!(windows) {
            &["TEMP", "TMP"]
        } else {
            &["TMPDIR"]
        };
        let mut expected = names
            .iter()
            .filter_map(std::env::var_os)
            .filter(|path| !path.is_empty())
            .filter(|path| cfg!(unix) || std::path::Path::new(path).is_absolute())
            .filter_map(|path| {
                PathUri::from_host_native_path(&path).ok().or_else(|| {
                    if cfg!(unix) {
                        PathUri::from_host_native_path(cwd.join(path)).ok()
                    } else {
                        None
                    }
                })
            })
            .collect::<Vec<_>>();
        expected.dedup();

        let info = EnvironmentInfo::local();
        assert_eq!(info.temporary_directories, Some(expected));
        // 捎带元数据的其余字段：家目录经 `~` 展开，平台即编译目标 OS
        assert_eq!(info.user_home_dir, PathUri::from_host_native_path("~").ok());
        assert_eq!(info.platform_os.as_deref(), Some(std::env::consts::OS));
    }

    #[cfg(unix)]
    #[test]
    fn local_environment_info_resolves_relative_temporary_directory() {
        if std::env::var_os("NOVA_EXECUTOR_TEST_RELATIVE_TMPDIR").is_none() {
            let status = std::process::Command::new(std::env::current_exe().expect("test binary"))
                .arg("--exact")
                .arg(
                    "protocol::tests::local_environment_info_resolves_relative_temporary_directory",
                )
                .env("NOVA_EXECUTOR_TEST_RELATIVE_TMPDIR", "1")
                .env("TMPDIR", "relative-temp")
                .status()
                .expect("run relative TMPDIR subprocess");
            assert!(status.success(), "relative TMPDIR subprocess failed");
            return;
        }

        let expected = PathUri::from_host_native_path(
            std::env::current_dir()
                .expect("current directory")
                .join("relative-temp"),
        )
        .expect("absolute temporary directory URI");
        let info = EnvironmentInfo::local();
        assert_eq!(info.temporary_directories, Some(vec![expected.clone()]));
        // temp_dir 与 temporary_directories 走同一路径规整，相对 TMPDIR 同样按 cwd 解析
        assert_eq!(info.temp_dir, Some(expected));
    }

    #[test]
    fn filesystem_protocol_rejects_native_absolute_paths() {
        let native_path = std::env::current_dir()
            .expect("current directory")
            .join("native-file.txt");
        let native_cwd = std::env::current_dir().expect("current directory");

        serde_json::from_value::<FsReadFileParams>(serde_json::json!({
            "path": native_path.to_string_lossy(),
            "sandbox": null,
        }))
        .expect_err("native absolute path should not deserialize as a URI");

        let sandbox = FileSystemSandboxContext::from_permission_profile_with_cwd(
            PermissionProfile::default(),
            PathUri::from_host_native_path(&native_cwd).expect("cwd URI"),
        );
        let mut native_path_sandbox =
            serde_json::to_value(sandbox).expect("sandbox should serialize");
        native_path_sandbox["cwd"] = serde_json::json!(native_cwd.to_string_lossy());

        serde_json::from_value::<FsReadFileParams>(serde_json::json!({
            "path": PathUri::from_host_native_path(native_path)
                .expect("path URI")
                .to_string(),
            "sandbox": native_path_sandbox,
        }))
        .expect_err("native absolute sandbox cwd should not deserialize as a URI");
    }

    #[test]
    fn filesystem_protocol_round_trips_permission_entries() {
        let native_cwd = std::env::current_dir().expect("current directory");
        let cwd = PathUri::from_host_native_path(&native_cwd).expect("cwd URI");
        let file_system = ManagedFileSystemPermissions::Restricted {
            entries: vec![
                FileSystemSandboxEntry {
                    path: FileSystemPath::Path { path: cwd.clone() },
                    access: FileSystemAccessMode::Read,
                    missing_path_behavior: None,
                },
                FileSystemSandboxEntry::skip_missing_path(
                    FileSystemPath::Path {
                        path: PathUri::from_host_native_path(native_cwd.join(".git"))
                            .expect("path URI"),
                    },
                    FileSystemAccessMode::Read,
                ),
                FileSystemSandboxEntry::skip_missing_path(
                    FileSystemPath::Special {
                        value: FileSystemSpecialPath::ProjectRoots {
                            subpath: Some(".nova".into()),
                        },
                    },
                    FileSystemAccessMode::Read,
                ),
            ],
            glob_scan_max_depth: Some(2.try_into().expect("non-zero depth")),
        };
        let permissions = PermissionProfile::Managed {
            file_system,
            network: NetworkSandboxPolicy::Restricted,
        };
        let sandbox =
            FileSystemSandboxContext::from_permission_profile_with_cwd(permissions, cwd.clone());

        let serialized = serde_json::to_value(&sandbox).expect("serialize sandbox");

        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][0]["path"]["path"],
            serde_json::json!(cwd.to_string())
        );
        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][1]["path"]["type"],
            serde_json::json!("path")
        );
        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][1]["missing_path_behavior"],
            serde_json::json!("skip")
        );
        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][2]["path"]["type"],
            serde_json::json!("special")
        );
        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][2]["missing_path_behavior"],
            serde_json::json!("skip")
        );
        assert!(!serialized.to_string().contains("generated_default_path"));
        assert!(!serialized.to_string().contains("generated_default_special"));
        assert_eq!(
            serde_json::from_value::<FileSystemSandboxContext>(serialized)
                .expect("deserialize sandbox"),
            sandbox
        );
        let preserve = FileSystemSandboxContext {
            windows_sandbox_proxy_settings_mode: Some(WindowsSandboxProxySettingsMode::Preserve),
            ..sandbox
        };
        let serialized = serde_json::to_value(&preserve).expect("serialize preserve mode");
        assert_eq!(serialized["windowsSandboxProxySettingsMode"], "preserve");
        assert_eq!(
            serde_json::from_value::<FileSystemSandboxContext>(serialized)
                .expect("deserialize preserve mode"),
            preserve
        );
    }

    #[test]
    fn filesystem_protocol_round_trips_legacy_policy_paths_as_uris() {
        let native_cwd = std::env::current_dir().expect("current directory");
        let cwd = PathUri::from_host_native_path(&native_cwd).expect("cwd URI");
        let mut file_system_policy =
            FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
                path: FileSystemPath::Path { path: cwd.clone() },
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            }]);
        file_system_policy.glob_scan_max_depth = Some(2);
        let permissions = PermissionProfile::from_runtime_permissions(
            &file_system_policy,
            NetworkSandboxPolicy::Restricted,
        );
        let sandbox =
            FileSystemSandboxContext::from_permission_profile_with_cwd(permissions, cwd.clone());

        let serialized = serde_json::to_value(&sandbox).expect("serialize sandbox");

        assert_eq!(
            serialized["permissions"]["file_system"]["entries"][0]["path"]["path"],
            serde_json::json!(cwd.to_string())
        );
        assert_eq!(
            serde_json::from_value::<FileSystemSandboxContext>(serialized)
                .expect("deserialize sandbox"),
            sandbox
        );
    }

    #[test]
    fn http_request_timeout_treats_omitted_and_null_as_no_timeout() {
        let omitted: HttpRequestParams = serde_json::from_value(serde_json::json!({
            "method": "GET",
            "url": "https://example.test",
            "requestId": "req-omitted-timeout",
        }))
        .expect("omitted timeout should deserialize");
        let null_timeout: HttpRequestParams = serde_json::from_value(serde_json::json!({
            "method": "GET",
            "url": "https://example.test",
            "requestId": "req-null-timeout",
            "timeoutMs": null,
        }))
        .expect("null timeout should deserialize");
        let explicit_timeout: HttpRequestParams = serde_json::from_value(serde_json::json!({
            "method": "GET",
            "url": "https://example.test",
            "requestId": "req-explicit-timeout",
            "timeoutMs": 1234,
        }))
        .expect("numeric timeout should deserialize");

        assert_eq!(
            (omitted.request_id.as_str(), omitted.timeout_ms),
            ("req-omitted-timeout", None)
        );
        assert_eq!(
            (null_timeout.request_id.as_str(), null_timeout.timeout_ms),
            ("req-null-timeout", None)
        );
        assert_eq!(
            (
                explicit_timeout.request_id.as_str(),
                explicit_timeout.timeout_ms
            ),
            ("req-explicit-timeout", Some(1234))
        );
    }

    #[test]
    fn exited_notification_accepts_legacy_payload_without_sandbox_denied() {
        let notification: ExecExitedNotification = serde_json::from_value(serde_json::json!({
            "processId": "proc-1",
            "seq": 3,
            "exitCode": 1,
        }))
        .expect("legacy exited notification should deserialize");

        assert_eq!(notification.sandbox_denied, None);
    }

    #[test]
    fn exec_response_distinguishes_unknown_from_explicitly_unsandboxed() {
        let unknown: ExecResponse = serde_json::from_value(serde_json::json!({
            "processId": "legacy",
        }))
        .expect("legacy response should deserialize");
        let unsandboxed: ExecResponse = serde_json::from_value(serde_json::json!({
            "processId": "current",
            "sandboxType": "none",
        }))
        .expect("explicitly unsandboxed response should deserialize");

        assert_eq!(
            (unknown.sandbox_type, unsandboxed.sandbox_type),
            (None, Some(ProcessSandboxType::None))
        );
    }
}
