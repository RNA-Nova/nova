//! Shared inventory entries and filesystem facts, independent of any checker.
//! （对位 codex 3342ee8c07 / c2eb1f42a0 `sandbox_integrity/dependencies.rs`）

use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use std::fs;
use std::io;
use std::path::PathBuf;

pub(super) type Dependency = (&'static str, io::Result<PathBuf>);

/// 依赖目标的文件系统事实（对位 codex c2eb1f42a0 `DependencyTarget`）。
pub(super) struct DependencyTarget {
    pub path: AbsolutePathBuf,
    pub metadata: fs::Metadata,
    pub resolved_path: Option<AbsolutePathBuf>,
}

impl DependencyTarget {
    pub(super) fn inspect(path: io::Result<PathBuf>) -> io::Result<Self> {
        let path = AbsolutePathBuf::from_absolute_path(path?)?;
        Ok(Self {
            metadata: fs::metadata(&path)?,
            resolved_path: path.canonicalize().ok(),
            path,
        })
    }
}

/// 当前可执行文件（nova 侧为 nova-exec-server 自身；对位 codex 的 `codex_executable`，
/// 依赖标签按 nova 改名纪律从 "codex" 改为 "nova"）。
pub(super) fn nova_executable() -> Dependency {
    ("nova", std::env::current_exe())
}

#[cfg(any(target_os = "linux", target_os = "windows"))]
pub(super) fn nova_and_launcher_dependencies(
    request: &nova_exec_server_sandboxing::SandboxExecRequest,
) -> Vec<Dependency> {
    let mut dependencies = vec![nova_executable()];
    if let Some(exe) = request.command.first() {
        dependencies.push(("sandbox_launcher", Ok(PathBuf::from(exe))));
    }
    dependencies
}
