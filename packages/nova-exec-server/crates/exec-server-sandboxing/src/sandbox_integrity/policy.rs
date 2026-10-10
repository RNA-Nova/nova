//! Prepare policy grants and glob matching shared by containment integrity checks.
//! Each checker owns further filesystem resolution of these roots and carveouts.
//! （对位 codex 11da6b9edc `sandbox_integrity/policy.rs`）

use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_protocol_core::permissions::ReadDenyMatcher;
use nova_exec_server_protocol_core::protocol::WritableRoot;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use std::io;

pub(super) struct PreparedPolicy {
    pub roots: Vec<WritableRoot>,
    pub glob_denials: Option<ReadDenyMatcher>,
}

pub(super) fn prepare_policy(
    policy: &FileSystemSandboxPolicy,
    cwd: &AbsolutePathBuf,
) -> io::Result<PreparedPolicy> {
    // Writable roots already resolve precedence for exact-path rules.
    let mut glob_policy = policy.clone();
    glob_policy
        .entries
        .retain(|entry| matches!(entry.path, FileSystemPath::GlobPattern { .. }));
    // 对位 codex `ReadDenyMatcher::try_new_for_local_paths`（permissions.rs:359）：
    // nova 同实现的既有名为 `try_new`（本地路径语义构造、畸形 glob 报错）。
    let glob_denials = ReadDenyMatcher::try_new(&glob_policy, cwd.as_path())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
    Ok(PreparedPolicy {
        roots: policy.get_writable_roots_with_cwd(cwd.as_path()),
        glob_denials,
    })
}
