mod exec_permission_profile_serde;
mod find_up;

use bytes::Bytes;
pub use find_up::FindUpErrorPolicy;
pub use find_up::find_nearest_ancestor_with_markers;
pub use find_up::find_nearest_native_ancestor_with_markers;
use futures::Stream;
use nova_exec_server_protocol_core::config_types::WindowsSandboxLevel;
use nova_exec_server_protocol_core::config_types::WindowsSandboxProxySettingsMode;
use nova_exec_server_protocol_core::models::ManagedFileSystemPermissions;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_protocol_core::models::SandboxEnforcement;
use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntryMissingPathBehavior;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicyContext;
use nova_exec_server_protocol_core::permissions::FileSystemSpecialPath;
use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
use nova_exec_server_protocol_core::protocol::SandboxPolicy;
use nova_exec_server_utils_path_uri::PathUri;
use serde::Deserialize;
use serde::Serialize;
use std::collections::HashSet;
use std::collections::VecDeque;
use std::future::Future;
use std::io;
use std::num::NonZeroUsize;
use std::path::Path;
use std::pin::Pin;
use std::task::Context;
use std::task::Poll;

/// Maximum chunk size returned by [`ExecutorFileSystem::read_file_stream`].
pub const FILE_READ_CHUNK_SIZE: usize = 1024 * 1024;
/// Maximum decoded chunk size accepted by a streamed filesystem write.
/// （对位 codex `FILE_WRITE_CHUNK_SIZE`：fs/writeBlock 单块解码后上限）
pub const FILE_WRITE_CHUNK_SIZE: usize = 1024 * 1024;
/// fs/walk 的服务端上限（集成测试需要引用以构造超限用例）。
pub const MAX_WALK_DEPTH: usize = 64;
pub const MAX_WALK_DIRECTORIES: usize = 10_000;
pub const MAX_WALK_ENTRIES: usize = 50_000;
const MAX_WALK_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const WALK_RESPONSE_ITEM_OVERHEAD_BYTES: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReadFileOptions {
    pub follow_symlinks: bool,
}

impl Default for ReadFileOptions {
    fn default() -> Self {
        Self {
            follow_symlinks: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WriteFileOptions {
    pub follow_symlinks: bool,
}

impl Default for WriteFileOptions {
    fn default() -> Self {
        Self {
            follow_symlinks: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GetMetadataOptions {
    pub follow_symlinks: bool,
}

impl Default for GetMetadataOptions {
    fn default() -> Self {
        Self {
            follow_symlinks: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CreateDirectoryOptions {
    pub recursive: bool,
    pub follow_symlinks: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RemoveOptions {
    pub recursive: bool,
    pub force: bool,
    pub follow_symlinks: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CopyOptions {
    pub recursive: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FileMetadata {
    pub is_directory: bool,
    pub is_file: bool,
    pub is_symlink: bool,
    /// Size in bytes.
    pub size: u64,
    pub created_at_ms: i64,
    pub modified_at_ms: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReadDirectoryEntry {
    pub file_name: String,
    pub is_directory: bool,
    pub is_file: bool,
}

/// Bounds for a recursive filesystem walk.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WalkOptions {
    /// Maximum directory depth below the root that may be traversed.
    pub max_depth: usize,
    /// Maximum number of directories that may be traversed, including the root.
    pub max_directories: usize,
    /// Maximum number of directory entries that may be examined.
    pub max_entries: usize,
    /// Whether directory symlinks should be followed.
    pub follow_directory_symlinks: bool,
    /// Whether directories whose names start with `.` should be returned but not traversed.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub prune_hidden_directories: bool,
}

/// Type of a filesystem entry returned by a walk.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub enum WalkEntryKind {
    Directory,
    File,
}

/// One entry returned by a walk.
#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WalkEntry {
    pub path: PathUri,
    pub kind: WalkEntryKind,
}

/// A descendant that could not be inspected during a walk.
#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WalkError {
    pub path: PathUri,
    pub message: String,
}

/// Entries and recoverable errors collected by a bounded walk.
#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WalkOutcome {
    pub entries: Vec<WalkEntry>,
    pub errors: Vec<WalkError>,
    pub truncated: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ExecFileSystemPath {
    Path { path: PathUri },
    GlobPattern { pattern: String },
    Special { value: FileSystemSpecialPath },
}

impl From<FileSystemPath> for ExecFileSystemPath {
    fn from(value: FileSystemPath) -> Self {
        match value {
            FileSystemPath::Path { path } => Self::Path { path },
            FileSystemPath::GlobPattern { pattern } => Self::GlobPattern { pattern },
            FileSystemPath::Special { value } => Self::Special { value },
        }
    }
}

// 对位 codex 841b5490b2：转换不再做本机校验（校验移到
// `FileSystemSandboxContext::validate_file_system_paths_for_current_host`，在执法侧进行）。
impl From<ExecFileSystemPath> for FileSystemPath {
    fn from(value: ExecFileSystemPath) -> Self {
        match value {
            ExecFileSystemPath::Path { path } => Self::Path { path },
            ExecFileSystemPath::GlobPattern { pattern } => Self::GlobPattern { pattern },
            ExecFileSystemPath::Special { value } => Self::Special { value },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecFileSystemSandboxEntry {
    pub path: ExecFileSystemPath,
    pub access: FileSystemAccessMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub missing_path_behavior: Option<FileSystemSandboxEntryMissingPathBehavior>,
}

impl From<FileSystemSandboxEntry> for ExecFileSystemSandboxEntry {
    fn from(value: FileSystemSandboxEntry) -> Self {
        Self {
            path: value.path.into(),
            access: value.access,
            missing_path_behavior: value.missing_path_behavior,
        }
    }
}

impl From<ExecFileSystemSandboxEntry> for FileSystemSandboxEntry {
    fn from(value: ExecFileSystemSandboxEntry) -> Self {
        Self {
            path: value.path.into(),
            access: value.access,
            missing_path_behavior: value.missing_path_behavior,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ExecManagedFileSystemPermissions {
    Restricted {
        entries: Vec<ExecFileSystemSandboxEntry>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        glob_scan_max_depth: Option<NonZeroUsize>,
    },
    Unrestricted,
}

impl From<ManagedFileSystemPermissions> for ExecManagedFileSystemPermissions {
    fn from(value: ManagedFileSystemPermissions) -> Self {
        match value {
            ManagedFileSystemPermissions::Restricted {
                entries,
                glob_scan_max_depth,
            } => Self::Restricted {
                entries: entries.into_iter().map(Into::into).collect(),
                glob_scan_max_depth,
            },
            ManagedFileSystemPermissions::Unrestricted => Self::Unrestricted,
        }
    }
}

impl From<ExecManagedFileSystemPermissions> for ManagedFileSystemPermissions {
    fn from(value: ExecManagedFileSystemPermissions) -> Self {
        match value {
            ExecManagedFileSystemPermissions::Restricted {
                entries,
                glob_scan_max_depth,
            } => Self::Restricted {
                entries: entries.into_iter().map(Into::into).collect(),
                glob_scan_max_depth,
            },
            ExecManagedFileSystemPermissions::Unrestricted => Self::Unrestricted,
        }
    }
}

/// Executor permission profile whose explicit filesystem paths serialize as file URIs.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ExecPermissionProfile {
    Managed {
        file_system: ExecManagedFileSystemPermissions,
        network: NetworkSandboxPolicy,
    },
    Disabled,
    External {
        network: NetworkSandboxPolicy,
    },
}

impl From<PermissionProfile> for ExecPermissionProfile {
    fn from(value: PermissionProfile) -> Self {
        match value {
            PermissionProfile::Managed {
                file_system,
                network,
            } => Self::Managed {
                file_system: file_system.into(),
                network,
            },
            PermissionProfile::Disabled => Self::Disabled,
            PermissionProfile::External { network } => Self::External { network },
        }
    }
}

impl From<ExecPermissionProfile> for PermissionProfile {
    fn from(value: ExecPermissionProfile) -> Self {
        match value {
            ExecPermissionProfile::Managed {
                file_system,
                network,
            } => Self::Managed {
                file_system: file_system.into(),
                network,
            },
            ExecPermissionProfile::Disabled => Self::Disabled,
            ExecPermissionProfile::External { network } => Self::External { network },
        }
    }
}

/// Windows sandbox choice encoded in executor RPCs.
///
/// The serialized field retains its legacy `windowsSandboxLevel` name for compatibility, but MXC
/// is a sandbox implementation rather than a RestrictedToken level（对位 codex
/// `WindowsSandboxSelection`，d4e11a9b97）。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum WindowsSandboxSelection {
    #[default]
    Disabled,
    RestrictedToken,
    Elevated,
    Mxc,
}

impl From<WindowsSandboxLevel> for WindowsSandboxSelection {
    fn from(level: WindowsSandboxLevel) -> Self {
        match level {
            WindowsSandboxLevel::Disabled => Self::Disabled,
            WindowsSandboxLevel::RestrictedToken => Self::RestrictedToken,
            WindowsSandboxLevel::Elevated => Self::Elevated,
        }
    }
}

impl WindowsSandboxSelection {
    /// 受限令牌实现层级；Mxc 不是受限令牌层级，返回 None（由调用方按 executor
    /// 能力拒绝，对位 codex sandbox_selection 的拆分语义）。
    pub fn restricted_token_level(self) -> Option<WindowsSandboxLevel> {
        match self {
            Self::Disabled => Some(WindowsSandboxLevel::Disabled),
            Self::RestrictedToken => Some(WindowsSandboxLevel::RestrictedToken),
            Self::Elevated => Some(WindowsSandboxLevel::Elevated),
            Self::Mxc => None,
        }
    }

    /// Whether this context selects either supported Windows sandbox implementation.
    pub fn windows_sandbox_is_requested(self) -> bool {
        self != Self::Disabled
    }
}

/// Filesystem sandbox policy and the selected executor paths needed to interpret it.
///
/// 对位 codex 841b5490b2：`permissions` 改持 `PermissionProfile`（线上经
/// `exec_permission_profile_serde` 以 executor file URI 序列化显式路径），
/// `cwd` 改为必填（策略锚定目录；旧客户端省略时由 executor 入口解析补齐——
/// 见 [`WireFileSystemSandboxContext`]）。
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileSystemSandboxContext {
    /// Serializes paths as executor file URIs instead of the profile's default native paths.
    #[serde(with = "exec_permission_profile_serde")]
    pub permissions: PermissionProfile,
    /// Working directory on the selected executor used to interpret sandbox permissions.
    /// Required even for absolute permissions; a process may use a different working directory.
    pub cwd: PathUri,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub workspace_roots: Vec<PathUri>,
    /// Executor-local user home used to resolve home-relative policy paths.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_home_dir: Option<PathUri>,
    /// Executor-local default directories used to resolve `:tmpdir` policy entries.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temporary_directories: Option<Vec<PathUri>>,
    /// 线上字段保留旧名 `windowsSandboxLevel`；取值扩展为实现选择（含 "mxc"）。
    #[serde(rename = "windowsSandboxLevel")]
    pub windows_sandbox_selection: WindowsSandboxSelection,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub windows_sandbox_proxy_settings_mode: Option<WindowsSandboxProxySettingsMode>,
    #[serde(default)]
    pub use_legacy_landlock: bool,
}

impl FileSystemSandboxContext {
    pub fn from_legacy_sandbox_policy(
        sandbox_policy: SandboxPolicy,
        cwd: PathUri,
    ) -> io::Result<Self> {
        // Legacy policy projection materializes native roots, so convert at the receiving-host
        // boundary while retaining the URI in the resulting sandbox context.
        let native_cwd = cwd.to_abs_path()?;
        let file_system_sandbox_policy =
            FileSystemSandboxPolicy::from_legacy_sandbox_policy_for_cwd(
                &sandbox_policy,
                &native_cwd,
            );
        let permissions = PermissionProfile::from_runtime_permissions_with_enforcement(
            SandboxEnforcement::from_legacy_sandbox_policy(&sandbox_policy),
            &file_system_sandbox_policy,
            NetworkSandboxPolicy::from(&sandbox_policy),
        );
        Ok(Self::from_permission_profile(permissions, cwd))
    }

    /// 对位 codex 841b5490b2：`from_permission_profile` 合并原
    /// `from_permission_profile`/`from_permission_profile_with_cwd` 两个构造器，cwd 必填。
    pub fn from_permission_profile(permissions: PermissionProfile, cwd: PathUri) -> Self {
        Self {
            workspace_roots: vec![cwd.clone()],
            permissions,
            cwd,
            user_home_dir: None,
            temporary_directories: None,
            windows_sandbox_selection: WindowsSandboxSelection::Disabled,
            windows_sandbox_proxy_settings_mode: None,
            use_legacy_landlock: false,
        }
    }

    /// Whether filesystem reads need a platform sandbox on the selected executor.
    ///
    /// 对位 codex a4ee536f01 `should_read_from_sandbox`（841b5490b2 起 cwd 必填，
    /// convention 直接取自策略 cwd；上游同提交删除 `should_run_in_sandbox`，
    /// nova 侧消费方已全部改按读/写各自分流，同步删除）。
    pub fn should_read_from_sandbox(&self) -> bool {
        !self
            .permissions
            .file_system_sandbox_policy()
            .has_full_disk_read_access_for_convention(self.cwd.infer_path_convention())
    }

    /// Whether filesystem writes need a platform sandbox on the selected executor.
    ///
    /// 对位 codex a4ee536f01 `should_write_into_sandbox`。
    pub fn should_write_into_sandbox(&self) -> bool {
        !self
            .permissions
            .file_system_sandbox_policy()
            .has_full_disk_write_access_for_convention(self.cwd.infer_path_convention())
    }

    /// Checks that explicit permission paths can be enforced by the current host. An
    /// orchestrator can still construct this context with paths belonging to another executor.
    ///
    /// 对位 codex 841b5490b2 `validate_file_system_paths_for_current_host`。
    pub fn validate_file_system_paths_for_current_host(&self) -> io::Result<()> {
        if let PermissionProfile::Managed {
            file_system: ManagedFileSystemPermissions::Restricted { entries, .. },
            ..
        } = &self.permissions
        {
            for entry in entries {
                if let FileSystemPath::Path { path } = &entry.path {
                    path.to_abs_path().map_err(|error| {
                        io::Error::new(
                            error.kind(),
                            format!("invalid sandbox permission path URI: {error}"),
                        )
                    })?;
                }
            }
        }
        Ok(())
    }

    /// Borrows the executor-owned paths needed to interpret filesystem policy entries.
    ///
    /// 对位 codex 841b5490b2：cwd 必填后返回不再是 Option。
    pub fn policy_context(&self) -> FileSystemSandboxPolicyContext<'_> {
        FileSystemSandboxPolicyContext {
            cwd: &self.cwd,
            workspace_roots: &self.workspace_roots,
            user_home_dir: self.user_home_dir.as_ref(),
            temporary_directories: self.temporary_directories.as_deref(),
        }
    }
}

/// Filesystem RPC wire context; clients can omit the policy cwd before executor resolution.
///
/// 对位 codex 841b5490b2 `WireFileSystemSandboxContext` 的终态化（v1.11）：
/// 策略目录只经 `policyContext` 承载——codex 为老客户端保留的平铺
/// `cwd`/`workspaceRoots` 双形状字段在 nova 侧整个删除（从未对外发布，没有
/// 老客户端存在）；保留的是"客户端整个省略 policyContext 时 executor 入口
/// 回退为自身 cwd"的人机工学（见 `cwd()` 与 server/registry.rs 的
/// `resolve_filesystem_sandbox`）。
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WireFileSystemSandboxContext {
    permissions: ExecPermissionProfile,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    policy_context: Option<WireFileSystemPolicyContext>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    user_home_dir: Option<PathUri>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    temporary_directories: Option<Vec<PathUri>>,
    #[serde(rename = "windowsSandboxLevel")]
    windows_sandbox_selection: WindowsSandboxSelection,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    windows_sandbox_proxy_settings_mode: Option<WindowsSandboxProxySettingsMode>,
    #[serde(default)]
    use_legacy_landlock: bool,
}

/// Filesystem clients provide these paths independently of the helper launch cwd.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WireFileSystemPolicyContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    cwd: Option<PathUri>,
    #[serde(default)]
    workspace_roots: Vec<PathUri>,
}

impl From<FileSystemSandboxContext> for WireFileSystemSandboxContext {
    fn from(sandbox: FileSystemSandboxContext) -> Self {
        let FileSystemSandboxContext {
            permissions,
            cwd,
            workspace_roots,
            user_home_dir,
            temporary_directories,
            windows_sandbox_selection,
            windows_sandbox_proxy_settings_mode,
            use_legacy_landlock,
        } = sandbox;
        let permissions = ExecPermissionProfile::from(permissions);
        Self {
            permissions,
            policy_context: Some(WireFileSystemPolicyContext {
                cwd: Some(cwd),
                workspace_roots,
            }),
            user_home_dir,
            temporary_directories,
            windows_sandbox_selection,
            windows_sandbox_proxy_settings_mode,
            use_legacy_landlock,
        }
    }
}

impl WireFileSystemSandboxContext {
    /// Returns the policy cwd supplied by the client via `policyContext`.
    pub fn cwd(&self) -> Option<&PathUri> {
        self.policy_context
            .as_ref()
            .and_then(|policy_context| policy_context.cwd.as_ref())
    }

    /// Returns whether a filesystem policy needs the client's cwd to be interpreted.
    pub fn requires_cwd(&self) -> bool {
        let ExecPermissionProfile::Managed {
            file_system: ExecManagedFileSystemPermissions::Restricted { entries, .. },
            ..
        } = &self.permissions
        else {
            return false;
        };

        entries.iter().any(|entry| match &entry.path {
            ExecFileSystemPath::GlobPattern { pattern } => !Path::new(pattern).is_absolute(),
            ExecFileSystemPath::Special {
                value: FileSystemSpecialPath::ProjectRoots { .. },
            } => true,
            ExecFileSystemPath::Path { .. } | ExecFileSystemPath::Special { .. } => false,
        })
    }

    /// Constructs the strict context after executor ingress has resolved the policy cwd.
    pub fn into_context(self, cwd: PathUri) -> FileSystemSandboxContext {
        FileSystemSandboxContext {
            permissions: self.permissions.into(),
            cwd,
            workspace_roots: self
                .policy_context
                .map(|policy_context| policy_context.workspace_roots)
                .unwrap_or_default(),
            user_home_dir: self.user_home_dir,
            temporary_directories: self.temporary_directories,
            windows_sandbox_selection: self.windows_sandbox_selection,
            windows_sandbox_proxy_settings_mode: self.windows_sandbox_proxy_settings_mode,
            use_legacy_landlock: self.use_legacy_landlock,
        }
    }
}

pub type FileSystemResult<T> = io::Result<T>;

/// Future returned by [`ExecutorFileSystem`] operations.
pub type ExecutorFileSystemFuture<'a, T> =
    Pin<Box<dyn Future<Output = FileSystemResult<T>> + Send + 'a>>;

/// Stream of immutable chunks read from an [`ExecutorFileSystem`].
pub struct FileSystemReadStream {
    inner: Pin<Box<dyn Stream<Item = FileSystemResult<Bytes>> + Send + 'static>>,
}

impl FileSystemReadStream {
    /// Wraps a filesystem byte stream.
    pub fn new(stream: impl Stream<Item = FileSystemResult<Bytes>> + Send + 'static) -> Self {
        Self {
            inner: Box::pin(stream),
        }
    }
}

impl Stream for FileSystemReadStream {
    type Item = FileSystemResult<Bytes>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.inner.as_mut().poll_next(cx)
    }
}

/// Abstract filesystem access used by components that may operate locally or via
/// a remote environment.
pub trait ExecutorFileSystem: Send + Sync {
    /// Resolves a path within this filesystem.
    fn canonicalize<'a>(
        &'a self,
        path: &'a PathUri,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, PathUri>;

    fn read_file<'a>(
        &'a self,
        path: &'a PathUri,
        options: ReadFileOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, Vec<u8>>;

    /// Reads a file as a stream of chunks no larger than [`FILE_READ_CHUNK_SIZE`].
    fn read_file_stream<'a>(
        &'a self,
        path: &'a PathUri,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, FileSystemReadStream>;

    /// Reads a file and decodes it as UTF-8 text.
    fn read_file_text<'a>(
        &'a self,
        path: &'a PathUri,
        options: ReadFileOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, String> {
        Box::pin(async move {
            let bytes = self.read_file(path, options, sandbox).await?;
            String::from_utf8(bytes).map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))
        })
    }

    fn write_file<'a>(
        &'a self,
        path: &'a PathUri,
        contents: Vec<u8>,
        options: WriteFileOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()>;

    fn create_directory<'a>(
        &'a self,
        path: &'a PathUri,
        create_directory_options: CreateDirectoryOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()>;

    fn get_metadata<'a>(
        &'a self,
        path: &'a PathUri,
        options: GetMetadataOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, FileMetadata>;

    fn read_directory<'a>(
        &'a self,
        path: &'a PathUri,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, Vec<ReadDirectoryEntry>>;

    /// Recursively lists descendants, optionally following directory symlinks.
    fn walk<'a>(
        &'a self,
        path: &'a PathUri,
        options: WalkOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, WalkOutcome> {
        self.walk_via_directory_reads(path, options, sandbox)
    }

    /// Performs a bounded walk using the primitive filesystem operations.
    ///
    /// Implementations with an optimized walk transport can use this as a compatibility fallback.
    fn walk_via_directory_reads<'a>(
        &'a self,
        path: &'a PathUri,
        options: WalkOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, WalkOutcome> {
        Box::pin(walk_via_directory_reads(self, path, options, sandbox))
    }

    fn remove<'a>(
        &'a self,
        path: &'a PathUri,
        remove_options: RemoveOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()>;

    fn copy<'a>(
        &'a self,
        source_path: &'a PathUri,
        destination_path: &'a PathUri,
        copy_options: CopyOptions,
        sandbox: Option<&'a FileSystemSandboxContext>,
    ) -> ExecutorFileSystemFuture<'a, ()>;
}

async fn walk_via_directory_reads<F: ExecutorFileSystem + ?Sized>(
    file_system: &F,
    root: &PathUri,
    options: WalkOptions,
    sandbox: Option<&FileSystemSandboxContext>,
) -> FileSystemResult<WalkOutcome> {
    if options.max_directories == 0 || options.max_entries == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "filesystem walk limits must be greater than zero",
        ));
    }
    if options.max_depth > MAX_WALK_DEPTH
        || options.max_directories > MAX_WALK_DIRECTORIES
        || options.max_entries > MAX_WALK_ENTRIES
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!(
                "filesystem walk limits exceed maximums: depth={MAX_WALK_DEPTH}, directories={MAX_WALK_DIRECTORIES}, entries={MAX_WALK_ENTRIES}"
            ),
        ));
    }

    let root_metadata = file_system
        .get_metadata(root, GetMetadataOptions::default(), sandbox)
        .await?;
    if !root_metadata.is_directory
        || (root_metadata.is_symlink && !options.follow_directory_symlinks)
    {
        return Ok(WalkOutcome::default());
    }

    let root_identity = if options.follow_directory_symlinks {
        file_system.canonicalize(root, sandbox).await?
    } else {
        root.clone()
    };
    let mut outcome = WalkOutcome::default();
    let mut queue = VecDeque::from([(root.clone(), 0usize)]);
    let mut visited_directories = HashSet::from([root_identity]);
    let mut directory_count = 1usize;
    let mut entry_count = 0usize;
    let mut response_bytes = 0usize;

    while let Some((directory, depth)) = queue.pop_front() {
        let mut entries = match file_system.read_directory(&directory, sandbox).await {
            Ok(entries) => entries,
            Err(error) => {
                if !push_walk_error(
                    &mut outcome,
                    &mut response_bytes,
                    directory,
                    error.to_string(),
                ) {
                    return Ok(outcome);
                }
                continue;
            }
        };
        entries.sort_by(|left, right| left.file_name.cmp(&right.file_name));

        for entry in entries {
            if entry_count == options.max_entries {
                outcome.truncated = true;
                return Ok(outcome);
            }
            entry_count += 1;

            let path = match directory.join(&entry.file_name) {
                Ok(path) => path,
                Err(error) => {
                    if !push_walk_error(
                        &mut outcome,
                        &mut response_bytes,
                        directory.clone(),
                        error.to_string(),
                    ) {
                        return Ok(outcome);
                    }
                    continue;
                }
            };
            let metadata = match file_system
                .get_metadata(&path, GetMetadataOptions::default(), sandbox)
                .await
            {
                Ok(metadata) => metadata,
                Err(error) => {
                    if !push_walk_error(&mut outcome, &mut response_bytes, path, error.to_string())
                    {
                        return Ok(outcome);
                    }
                    continue;
                }
            };
            if metadata.is_symlink && (!options.follow_directory_symlinks || !metadata.is_directory)
            {
                continue;
            }

            let kind = if metadata.is_directory {
                WalkEntryKind::Directory
            } else if metadata.is_file {
                WalkEntryKind::File
            } else {
                continue;
            };
            if !reserve_walk_response_bytes(
                &mut outcome,
                &mut response_bytes,
                path.to_string().len(),
            ) {
                return Ok(outcome);
            }
            outcome.entries.push(WalkEntry {
                path: path.clone(),
                kind,
            });

            if kind == WalkEntryKind::Directory && depth < options.max_depth {
                if options.prune_hidden_directories && entry.file_name.starts_with('.') {
                    continue;
                }
                let directory_identity = if options.follow_directory_symlinks {
                    match file_system.canonicalize(&path, sandbox).await {
                        Ok(path) => path,
                        Err(error) => {
                            if !push_walk_error(
                                &mut outcome,
                                &mut response_bytes,
                                path,
                                error.to_string(),
                            ) {
                                return Ok(outcome);
                            }
                            continue;
                        }
                    }
                } else {
                    path.clone()
                };
                if !visited_directories.insert(directory_identity) {
                    continue;
                }
                if directory_count == options.max_directories {
                    outcome.truncated = true;
                } else {
                    directory_count += 1;
                    queue.push_back((path, depth + 1));
                }
            }
        }
    }

    Ok(outcome)
}

fn push_walk_error(
    outcome: &mut WalkOutcome,
    response_bytes: &mut usize,
    path: PathUri,
    message: String,
) -> bool {
    let item_bytes = path.to_string().len().saturating_add(message.len());
    if !reserve_walk_response_bytes(outcome, response_bytes, item_bytes) {
        return false;
    }
    outcome.errors.push(WalkError { path, message });
    true
}

fn reserve_walk_response_bytes(
    outcome: &mut WalkOutcome,
    response_bytes: &mut usize,
    content_bytes: usize,
) -> bool {
    let item_bytes = content_bytes.saturating_add(WALK_RESPONSE_ITEM_OVERHEAD_BYTES);
    let Some(total_bytes) = response_bytes.checked_add(item_bytes) else {
        outcome.truncated = true;
        return false;
    };
    if total_bytes > MAX_WALK_RESPONSE_BYTES {
        outcome.truncated = true;
        return false;
    }
    *response_bytes = total_bytes;
    true
}
