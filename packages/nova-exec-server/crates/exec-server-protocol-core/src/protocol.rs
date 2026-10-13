// 对位 codex 39f4277087 codex-rs/protocol/src/protocol.rs 的**子集镜像**：只含
// nova-exec-server 已镜像词汇（W3cTraceContext / NetworkAccess / SandboxPolicy /
// WritableRoot 及 permissions 再导出）。锚点文件的 agent 会话面词汇（items、
// events、SessionSource、TokenUsage、GranularApprovalConfig 等）按 nova 纯度
// 边界不镜像（agent 层词汇归 py 侧 nova_protocol）；文件内测试同理只恢复覆盖
// 已镜像词汇的用例。
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::fmt;
use std::ops::Mul;
use std::path::Path;
use std::path::PathBuf;
use std::str::FromStr;
use std::time::Duration;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use nova_exec_server_utils_path_uri::PathUri;
use schemars::JsonSchema;
use serde::Deserialize;
use serde::Deserializer;
use serde::Serialize;
use serde::de::Error as _;
use serde_json::Value;
use strum_macros::Display;
use tracing::error;
use ts_rs::TS;
/// W3C 追踪上下文（traceparent/tracestate），随 RPC 请求传播。
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq, JsonSchema, TS)]
pub struct W3cTraceContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub traceparent: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub tracestate: Option<String>,
}
pub use crate::permissions::FileSystemAccessMode;
pub use crate::permissions::FileSystemPath;
pub use crate::permissions::FileSystemSandboxEntry;
pub use crate::permissions::FileSystemSandboxKind;
pub use crate::permissions::FileSystemSandboxPolicy;
pub use crate::permissions::FileSystemSpecialPath;
pub use crate::permissions::NetworkSandboxPolicy;
pub use crate::permissions::RawFileSystemSandboxPolicy;
use crate::permissions::default_read_only_subpaths_for_writable_root;
/// Represents whether outbound network access is available to the agent.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Display, Default, JsonSchema, TS,
)]
#[serde(rename_all = "kebab-case")]
#[strum(serialize_all = "kebab-case")]
pub enum NetworkAccess {
    #[default]
    Restricted,
    Enabled,
}

impl NetworkAccess {
    pub fn is_enabled(self) -> bool {
        matches!(self, NetworkAccess::Enabled)
    }
}

/// Determines execution restrictions for model shell commands.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Display, JsonSchema, TS)]
#[strum(serialize_all = "kebab-case")]
#[serde(tag = "type", rename_all = "kebab-case")]
pub enum SandboxPolicy {
    /// No restrictions whatsoever. Use with caution.
    #[serde(rename = "danger-full-access")]
    DangerFullAccess,

    /// Read-only access configuration.
    #[serde(rename = "read-only")]
    ReadOnly {
        /// When set to `true`, outbound network access is allowed. `false` by
        /// default.
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        network_access: bool,
    },

    /// Indicates the process is already in an external sandbox. Allows full
    /// disk access while honoring the provided network setting.
    #[serde(rename = "external-sandbox")]
    ExternalSandbox {
        /// Whether the external sandbox permits outbound network traffic.
        #[serde(default)]
        network_access: NetworkAccess,
    },

    /// Same as `ReadOnly` but additionally grants write access to the current
    /// working directory ("workspace").
    #[serde(rename = "workspace-write")]
    WorkspaceWrite {
        /// Additional folders (beyond cwd and possibly TMPDIR) that should be
        /// writable from within the sandbox.
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        writable_roots: Vec<AbsolutePathBuf>,

        /// When set to `true`, outbound network access is allowed. `false` by
        /// default.
        #[serde(default)]
        network_access: bool,

        /// When set to `true`, will NOT include the per-user `TMPDIR`
        /// environment variable among the default writable roots. Defaults to
        /// `false`.
        #[serde(default)]
        exclude_tmpdir_env_var: bool,

        /// When set to `true`, will NOT include the `/tmp` among the default
        /// writable roots on UNIX. Defaults to `false`.
        #[serde(default)]
        exclude_slash_tmp: bool,
    },
}

/// A writable root path accompanied by a list of subpaths that should remain
/// read‑only even when the root is writable. This is primarily used to ensure
/// that folders containing files that could be modified to escalate the
/// privileges of the agent (e.g. `.nova`, `.git`, notably `.git/hooks`) under
/// a writable root are not modified by the agent.
#[derive(Debug, Clone, PartialEq, Eq, JsonSchema)]
pub struct WritableRoot {
    pub root: AbsolutePathBuf,

    /// By construction, these subpaths are all under `root`.
    pub read_only_subpaths: Vec<AbsolutePathBuf>,

    /// Workspace metadata path names that must not be created or replaced under
    /// `root` unless the policy grants an explicit write rule for that metadata
    /// path.
    pub protected_metadata_names: Vec<String>,
}

impl WritableRoot {
    pub fn is_path_writable(&self, path: &Path) -> bool {
        // Check if the path is under the root.
        if !path.starts_with(&self.root) {
            return false;
        }

        // Check if the path is under any of the read-only subpaths.
        for subpath in &self.read_only_subpaths {
            if path.starts_with(subpath) {
                return false;
            }
        }

        if self.path_contains_protected_metadata_name(path) {
            return false;
        }

        true
    }

    fn path_contains_protected_metadata_name(&self, path: &Path) -> bool {
        let Ok(relative_path) = path.strip_prefix(&self.root) else {
            return false;
        };

        let Some(first_component) = relative_path.components().next() else {
            return false;
        };

        self.protected_metadata_names
            .iter()
            .any(|name| first_component.as_os_str() == std::ffi::OsStr::new(name))
    }
}

impl FromStr for SandboxPolicy {
    type Err = serde_json::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        serde_json::from_str(s)
    }
}

impl FromStr for FileSystemSandboxPolicy {
    type Err = serde_json::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        serde_json::from_str::<RawFileSystemSandboxPolicy>(s)?
            .try_into()
            .map_err(serde_json::Error::custom)
    }
}

impl FromStr for NetworkSandboxPolicy {
    type Err = serde_json::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        serde_json::from_str(s)
    }
}

impl SandboxPolicy {
    /// Returns a policy with read-only disk access and no network.
    pub fn new_read_only_policy() -> Self {
        SandboxPolicy::ReadOnly {
            network_access: false,
        }
    }

    /// Returns a policy that can read the entire disk, but can only write to
    /// the current working directory and the per-user tmp dir on macOS. It does
    /// not allow network access.
    pub fn new_workspace_write_policy() -> Self {
        SandboxPolicy::WorkspaceWrite {
            writable_roots: vec![],
            network_access: false,
            exclude_tmpdir_env_var: false,
            exclude_slash_tmp: false,
        }
    }

    pub fn has_full_disk_read_access(&self) -> bool {
        true
    }

    pub fn has_full_disk_write_access(&self) -> bool {
        match self {
            SandboxPolicy::DangerFullAccess => true,
            SandboxPolicy::ExternalSandbox { .. } => true,
            SandboxPolicy::ReadOnly { .. } => false,
            SandboxPolicy::WorkspaceWrite { .. } => false,
        }
    }

    pub fn has_full_network_access(&self) -> bool {
        match self {
            SandboxPolicy::DangerFullAccess => true,
            SandboxPolicy::ExternalSandbox { network_access } => network_access.is_enabled(),
            SandboxPolicy::ReadOnly { network_access, .. } => *network_access,
            SandboxPolicy::WorkspaceWrite { network_access, .. } => *network_access,
        }
    }

    /// Returns the list of writable roots (tailored to the current working
    /// directory) together with subpaths that should remain read‑only under
    /// each writable root.
    pub fn get_writable_roots_with_cwd(&self, cwd: &Path) -> Vec<WritableRoot> {
        match self {
            SandboxPolicy::DangerFullAccess => Vec::new(),
            SandboxPolicy::ExternalSandbox { .. } => Vec::new(),
            SandboxPolicy::ReadOnly { .. } => Vec::new(),
            SandboxPolicy::WorkspaceWrite {
                writable_roots,
                exclude_tmpdir_env_var,
                exclude_slash_tmp,
                network_access: _,
            } => {
                // Start from explicitly configured writable roots.
                let mut roots: Vec<AbsolutePathBuf> = writable_roots.clone();

                // Always include defaults: cwd, /tmp (if present on Unix), and
                // on macOS, the per-user TMPDIR unless explicitly excluded.
                // TODO(nova): cwd param should be AbsolutePathBuf.
                let cwd_absolute = AbsolutePathBuf::from_absolute_path(cwd);
                match cwd_absolute {
                    Ok(cwd) => {
                        roots.push(cwd);
                    }
                    Err(e) => {
                        error!(
                            "Ignoring invalid cwd {:?} for sandbox writable root: {}",
                            cwd, e
                        );
                    }
                }

                // Include /tmp on Unix unless explicitly excluded.
                if cfg!(unix) && !exclude_slash_tmp {
                    match AbsolutePathBuf::from_absolute_path("/tmp") {
                        Ok(slash_tmp) => {
                            if slash_tmp.as_path().is_dir() {
                                roots.push(slash_tmp);
                            }
                        }
                        Err(e) => {
                            error!("Ignoring invalid /tmp for sandbox writable root: {e}");
                        }
                    }
                }

                // Include $TMPDIR unless explicitly excluded. On macOS, TMPDIR
                // is per-user, so writes to TMPDIR should not be readable by
                // other users on the system.
                //
                // By comparison, TMPDIR is not guaranteed to be defined on
                // Linux or Windows, but supporting it here gives users a way to
                // provide the model with their own temporary directory without
                // having to hardcode it in the config.
                if !exclude_tmpdir_env_var
                    && let Some(tmpdir) = std::env::var_os("TMPDIR")
                    && !tmpdir.is_empty()
                {
                    match AbsolutePathBuf::from_absolute_path(PathBuf::from(&tmpdir)) {
                        Ok(tmpdir_path) => {
                            roots.push(tmpdir_path);
                        }
                        Err(e) => {
                            error!(
                                "Ignoring invalid TMPDIR value {tmpdir:?} for sandbox writable root: {e}",
                            );
                        }
                    }
                }

                // For each root, compute subpaths that should remain read-only.
                let cwd_root = AbsolutePathBuf::from_absolute_path(cwd).ok();
                roots
                    .into_iter()
                    .map(|writable_root| {
                        let protect_missing_dot_nova = cwd_root
                            .as_ref()
                            .is_some_and(|cwd_root| cwd_root == &writable_root);
                        WritableRoot {
                            read_only_subpaths: default_read_only_subpaths_for_writable_root(
                                &writable_root,
                                protect_missing_dot_nova,
                            ),
                            protected_metadata_names: Vec::new(),
                            root: writable_root,
                        }
                    })
                    .collect()
            }
        }
    }
}

// 对位 codex 39f4277087 codex-rs/protocol/src/protocol.rs 内联测试：只恢复覆盖
// 已镜像词汇的 8 件（SandboxPolicy 访问标志族 / FileSystemSandboxPolicy
// restricted 语义族 / legacy 桥接族）；辅助函数 sorted_writable_roots /
// sandbox_policy_allows_read / sandbox_policy_allows_write /
// sandbox_policy_probe_paths / assert_same_sandbox_policy_semantics 随同移植。
#[cfg(test)]
mod tests {
    use super::*;
    use nova_exec_server_utils_absolute_path::canonicalize_preserving_symlinks;
    use pretty_assertions::assert_eq;
    use tempfile::TempDir;

    fn sorted_writable_roots(roots: Vec<WritableRoot>) -> Vec<(PathBuf, Vec<PathBuf>)> {
        let mut sorted_roots: Vec<(PathBuf, Vec<PathBuf>)> = roots
            .into_iter()
            .map(|root| {
                let mut read_only_subpaths: Vec<PathBuf> = root
                    .read_only_subpaths
                    .into_iter()
                    .map(|path| path.to_path_buf())
                    .collect();
                read_only_subpaths.sort();
                (root.root.to_path_buf(), read_only_subpaths)
            })
            .collect();
        sorted_roots.sort_by(|left, right| left.0.cmp(&right.0));
        sorted_roots
    }

    fn sandbox_policy_allows_read(policy: &SandboxPolicy, _path: &Path, _cwd: &Path) -> bool {
        policy.has_full_disk_read_access()
    }

    fn sandbox_policy_allows_write(policy: &SandboxPolicy, path: &Path, cwd: &Path) -> bool {
        if policy.has_full_disk_write_access() {
            return true;
        }

        policy
            .get_writable_roots_with_cwd(cwd)
            .iter()
            .any(|root| root.is_path_writable(path))
    }

    fn sandbox_policy_probe_paths(policy: &SandboxPolicy, cwd: &Path) -> Vec<PathBuf> {
        let mut paths = vec![cwd.to_path_buf()];
        for root in policy.get_writable_roots_with_cwd(cwd) {
            paths.push(root.root.to_path_buf());
            paths.extend(
                root.read_only_subpaths
                    .into_iter()
                    .map(|path| path.to_path_buf()),
            );
        }
        paths.sort();
        paths.dedup();
        paths
    }

    fn assert_same_sandbox_policy_semantics(
        expected: &SandboxPolicy,
        actual: &SandboxPolicy,
        cwd: &Path,
    ) {
        assert_eq!(
            actual.has_full_disk_read_access(),
            expected.has_full_disk_read_access()
        );
        assert_eq!(
            actual.has_full_disk_write_access(),
            expected.has_full_disk_write_access()
        );
        assert_eq!(
            actual.has_full_network_access(),
            expected.has_full_network_access()
        );
        let mut probe_paths = sandbox_policy_probe_paths(expected, cwd);
        probe_paths.extend(sandbox_policy_probe_paths(actual, cwd));
        probe_paths.sort();
        probe_paths.dedup();

        for path in probe_paths {
            assert_eq!(
                sandbox_policy_allows_read(actual, &path, cwd),
                sandbox_policy_allows_read(expected, &path, cwd),
                "read access mismatch for {}",
                path.display()
            );
            assert_eq!(
                sandbox_policy_allows_write(actual, &path, cwd),
                sandbox_policy_allows_write(expected, &path, cwd),
                "write access mismatch for {}",
                path.display()
            );
        }
    }

    #[test]
    fn external_sandbox_reports_full_access_flags() {
        let restricted = SandboxPolicy::ExternalSandbox {
            network_access: NetworkAccess::Restricted,
        };
        assert!(restricted.has_full_disk_write_access());
        assert!(!restricted.has_full_network_access());

        let enabled = SandboxPolicy::ExternalSandbox {
            network_access: NetworkAccess::Enabled,
        };
        assert!(enabled.has_full_disk_write_access());
        assert!(enabled.has_full_network_access());
    }

    #[test]
    fn read_only_reports_network_access_flags() {
        let restricted = SandboxPolicy::new_read_only_policy();
        assert!(!restricted.has_full_network_access());

        let enabled = SandboxPolicy::ReadOnly {
            network_access: true,
        };
        assert!(enabled.has_full_network_access());
    }

    #[test]
    fn restricted_file_system_policy_reports_full_access_from_root_entries() {
        let read_only = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            access: FileSystemAccessMode::Read,
            missing_path_behavior: None,
        }]);
        assert!(read_only.has_full_disk_read_access());
        assert!(!read_only.has_full_disk_write_access());
        assert!(!read_only.include_platform_defaults());

        let writable = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        }]);
        assert!(writable.has_full_disk_read_access());
        assert!(writable.has_full_disk_write_access());
    }

    #[test]
    fn restricted_file_system_policy_treats_root_with_carveouts_as_scoped_access() {
        let cwd = TempDir::new().expect("tempdir");
        let canonical_cwd = canonicalize_preserving_symlinks(cwd.path()).expect("canonicalize cwd");
        let root = AbsolutePathBuf::from_absolute_path(&canonical_cwd)
            .expect("absolute canonical tempdir")
            .as_path()
            .ancestors()
            .last()
            .and_then(|path| AbsolutePathBuf::from_absolute_path(path).ok())
            .expect("filesystem root");
        let blocked = AbsolutePathBuf::resolve_path_against_base("blocked", cwd.path());
        let expected_blocked = AbsolutePathBuf::from_absolute_path(
            canonicalize_preserving_symlinks(cwd.path())
                .expect("canonicalize cwd")
                .join("blocked"),
        )
        .expect("canonical blocked");
        let policy = FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry {
                path: FileSystemPath::Special {
                    value: FileSystemSpecialPath::Root,
                },
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: blocked.into(),
                access: FileSystemAccessMode::Deny,
                missing_path_behavior: None,
            },
        ]);

        assert!(!policy.has_full_disk_read_access());
        assert!(!policy.has_full_disk_write_access());
        assert_eq!(
            policy.get_readable_roots_with_cwd(cwd.path()),
            vec![root.clone()]
        );
        assert_eq!(
            policy.get_unreadable_roots_with_cwd(cwd.path()),
            vec![expected_blocked.clone()]
        );

        let writable_roots = policy.get_writable_roots_with_cwd(cwd.path());
        assert_eq!(writable_roots.len(), 1);
        assert_eq!(writable_roots[0].root, root);
        assert!(
            writable_roots[0]
                .read_only_subpaths
                .iter()
                .any(|path| path.as_path() == expected_blocked.as_path())
        );
    }

    #[test]
    fn restricted_file_system_policy_derives_effective_paths() {
        let cwd = TempDir::new().expect("tempdir");
        std::fs::create_dir_all(cwd.path().join(".agents")).expect("create .agents");
        // 命名铁律：锚点建 .codex，nova 对位 .nova
        std::fs::create_dir_all(cwd.path().join(".nova")).expect("create .nova");
        let canonical_cwd = canonicalize_preserving_symlinks(cwd.path()).expect("canonicalize cwd");
        let cwd_absolute =
            AbsolutePathBuf::from_absolute_path(&canonical_cwd).expect("absolute tempdir");
        let secret = AbsolutePathBuf::resolve_path_against_base("secret", cwd.path());
        let expected_secret = AbsolutePathBuf::from_absolute_path(canonical_cwd.join("secret"))
            .expect("canonical secret");
        let expected_agents = AbsolutePathBuf::from_absolute_path(canonical_cwd.join(".agents"))
            .expect("canonical .agents");
        let expected_dot_nova = AbsolutePathBuf::from_absolute_path(canonical_cwd.join(".nova"))
            .expect("canonical .nova");
        let policy = FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry {
                path: FileSystemPath::Special {
                    value: FileSystemSpecialPath::Minimal,
                },
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: FileSystemPath::Special {
                    value: FileSystemSpecialPath::project_roots(/*subpath*/ None),
                },
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: secret.into(),
                access: FileSystemAccessMode::Deny,
                missing_path_behavior: None,
            },
        ]);

        assert!(!policy.has_full_disk_read_access());
        assert!(!policy.has_full_disk_write_access());
        assert!(policy.include_platform_defaults());
        assert_eq!(
            policy.get_readable_roots_with_cwd(cwd.path()),
            vec![cwd_absolute.clone()]
        );
        assert_eq!(
            policy.get_unreadable_roots_with_cwd(cwd.path()),
            vec![expected_secret.clone()]
        );

        let writable_roots = policy.get_writable_roots_with_cwd(cwd.path());
        assert_eq!(writable_roots.len(), 1);
        assert_eq!(writable_roots[0].root, cwd_absolute);
        assert!(
            writable_roots[0]
                .read_only_subpaths
                .iter()
                .any(|path| path.as_path() == expected_secret.as_path())
        );
        assert!(
            writable_roots[0]
                .read_only_subpaths
                .iter()
                .any(|path| path.as_path() == expected_agents.as_path())
        );
        assert!(
            writable_roots[0]
                .read_only_subpaths
                .iter()
                .any(|path| path.as_path() == expected_dot_nova.as_path())
        );
    }

    #[test]
    fn restricted_file_system_policy_treats_read_entries_as_read_only_subpaths() {
        let cwd = TempDir::new().expect("tempdir");
        let canonical_cwd = canonicalize_preserving_symlinks(cwd.path()).expect("canonicalize cwd");
        let docs = AbsolutePathBuf::resolve_path_against_base("docs", cwd.path());
        let docs_public = AbsolutePathBuf::resolve_path_against_base("docs/public", cwd.path());
        let expected_docs = AbsolutePathBuf::from_absolute_path(canonical_cwd.join("docs"))
            .expect("canonical docs");
        let expected_docs_public =
            AbsolutePathBuf::from_absolute_path(canonical_cwd.join("docs/public"))
                .expect("canonical docs/public");
        // 命名铁律：锚点 .codex，nova 对位 .nova
        let expected_dot_nova = AbsolutePathBuf::from_absolute_path(canonical_cwd.join(".nova"))
            .expect("canonical .nova");
        let policy = FileSystemSandboxPolicy::restricted(vec![
            FileSystemSandboxEntry {
                path: FileSystemPath::Special {
                    value: FileSystemSpecialPath::project_roots(/*subpath*/ None),
                },
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: docs.into(),
                access: FileSystemAccessMode::Read,
                missing_path_behavior: None,
            },
            FileSystemSandboxEntry {
                path: docs_public.into(),
                access: FileSystemAccessMode::Write,
                missing_path_behavior: None,
            },
        ]);

        assert!(!policy.has_full_disk_write_access());
        assert_eq!(
            sorted_writable_roots(policy.get_writable_roots_with_cwd(cwd.path())),
            vec![
                (
                    canonical_cwd,
                    vec![
                        expected_dot_nova.to_path_buf(),
                        expected_docs.to_path_buf()
                    ],
                ),
                (expected_docs_public.to_path_buf(), Vec::new()),
            ]
        );
    }

    #[test]
    fn file_system_policy_rejects_legacy_bridge_for_non_workspace_writes() {
        let cwd = if cfg!(windows) {
            Path::new(r"C:\workspace")
        } else {
            Path::new("/tmp/workspace")
        };
        let external_write_path = if cfg!(windows) {
            AbsolutePathBuf::from_absolute_path(r"C:\temp").expect("absolute windows temp path")
        } else {
            AbsolutePathBuf::from_absolute_path("/tmp").expect("absolute tmp path")
        };
        let policy = FileSystemSandboxPolicy::restricted(vec![FileSystemSandboxEntry {
            path: FileSystemPath::Path {
                path: external_write_path.into(),
            },
            access: FileSystemAccessMode::Write,
            missing_path_behavior: None,
        }]);

        let err = policy
            .to_legacy_sandbox_policy(NetworkSandboxPolicy::Restricted, cwd)
            .expect_err("non-workspace writes should be rejected");

        assert!(
            err.to_string()
                .contains("filesystem writes outside the workspace root"),
            "{err}"
        );
    }

    #[test]
    fn legacy_sandbox_policy_semantics_survive_split_bridge() {
        let cwd = TempDir::new().expect("tempdir");
        let writable_root = AbsolutePathBuf::resolve_path_against_base("writable", cwd.path());
        let policies = [
            SandboxPolicy::DangerFullAccess,
            SandboxPolicy::ExternalSandbox {
                network_access: NetworkAccess::Restricted,
            },
            SandboxPolicy::ExternalSandbox {
                network_access: NetworkAccess::Enabled,
            },
            SandboxPolicy::ReadOnly {
                network_access: false,
            },
            SandboxPolicy::WorkspaceWrite {
                writable_roots: vec![],
                network_access: false,
                exclude_tmpdir_env_var: true,
                exclude_slash_tmp: true,
            },
            SandboxPolicy::WorkspaceWrite {
                writable_roots: vec![writable_root],
                network_access: true,
                exclude_tmpdir_env_var: false,
                exclude_slash_tmp: true,
            },
        ];

        for expected in policies {
            let actual =
                FileSystemSandboxPolicy::from_legacy_sandbox_policy_for_cwd(&expected, cwd.path())
                    .to_legacy_sandbox_policy(NetworkSandboxPolicy::from(&expected), cwd.path())
                    .expect("legacy bridge should preserve legacy policy semantics");

            assert_same_sandbox_policy_semantics(&expected, &actual, cwd.path());
        }
    }
}
