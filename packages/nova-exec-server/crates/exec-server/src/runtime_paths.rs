use std::path::PathBuf;

use nova_exec_server_utils_absolute_path::AbsolutePathBuf;

/// Runtime paths needed by exec-server child processes.
/// （对位 codex d13aeb77ea：同时承载 executor 初始化期解析的沙箱设置）
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecServerRuntimePaths {
    /// Stable path to the nova-exec-server executable used to launch hidden helper modes.
    pub executor_self_exe: AbsolutePathBuf,
    /// Path to the Linux sandbox helper alias used when the platform sandbox
    /// needs to re-enter nova-exec-server by argv0.
    pub executor_linux_sandbox_exe: Option<AbsolutePathBuf>,
    /// User-config opt-out of writable-root symlink checks beneath this host's home.
    /// （配置源未接入 nova，当前恒为 None，见移植报告挂账项）
    #[cfg(target_os = "macos")]
    pub allowed_symlinked_nova_home: Option<AbsolutePathBuf>,
}

impl ExecServerRuntimePaths {
    pub fn from_optional_paths(
        executor_self_exe: Option<PathBuf>,
        executor_linux_sandbox_exe: Option<PathBuf>,
    ) -> std::io::Result<Self> {
        let executor_self_exe = executor_self_exe.ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "nova-exec-server executable path is not configured",
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
            executor_linux_sandbox_exe: executor_linux_sandbox_exe
                .map(absolute_path)
                .transpose()?,
            #[cfg(target_os = "macos")]
            allowed_symlinked_nova_home: None,
        })
    }

    /// Applies the symlink opt-in resolved by the execution host's config loader.
    /// （对位 codex d13aeb77ea）
    #[cfg(target_os = "macos")]
    pub fn with_allowed_symlinked_nova_home(
        mut self,
        allowed_symlinked_nova_home: Option<AbsolutePathBuf>,
    ) -> Self {
        self.allowed_symlinked_nova_home = allowed_symlinked_nova_home;
        self
    }
}

fn absolute_path(path: PathBuf) -> std::io::Result<AbsolutePathBuf> {
    AbsolutePathBuf::from_absolute_path(path.as_path())
        .map_err(|err| std::io::Error::new(std::io::ErrorKind::InvalidInput, err))
}
