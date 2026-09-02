use std::path::PathBuf;

use nova_executor_utils_absolute_path::AbsolutePathBuf;

/// Runtime paths needed by exec-server child processes.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecServerRuntimePaths {
    /// Stable path to the nova-executor executable used to launch hidden helper modes.
    pub executor_self_exe: AbsolutePathBuf,
    /// Path to the Linux sandbox helper alias used when the platform sandbox
    /// needs to re-enter nova-executor by argv0.
    pub executor_linux_sandbox_exe: Option<AbsolutePathBuf>,
}

impl ExecServerRuntimePaths {
    pub fn from_optional_paths(
        executor_self_exe: Option<PathBuf>,
        executor_linux_sandbox_exe: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        let executor_self_exe = executor_self_exe.ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "nova-executor executable path is not configured",
            )
        })?;
        Self::new(executor_self_exe, executor_linux_sandbox_exe)
    }

    pub fn new(
        executor_self_exe: PathBuf,
        executor_linux_sandbox_exe: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        Ok(Self {
            executor_self_exe: absolute_path(executor_self_exe)?,
            executor_linux_sandbox_exe: executor_linux_sandbox_exe.map(absolute_path).transpose()?,
        })
    }
}

fn absolute_path(path: PathBuf) -> std::io::Result<AbsolutePathBuf> {
    AbsolutePathBuf::from_absolute_path(path.as_path())
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidInput, err))
}
