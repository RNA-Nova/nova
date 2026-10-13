use std::path::PathBuf;

use nova_exec_server_sandboxing::LinuxSandboxPidNamespace;
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
    /// Trusted startup policy; requests and repository config cannot change PID isolation.
    /// （对位 codex 39f4277087 `ExecServerRuntimeOptions::linux_sandbox_pid_namespace`）
    pub linux_sandbox_pid_namespace: LinuxSandboxPidNamespace,
    /// Trusted startup routing; request policy still controls destination access.
    /// （对位 codex 39f4277087 `ExecServerRuntimeOptions::proxy_private_ips_via_upstream`）
    pub proxy_private_ips_via_upstream: bool,
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
            linux_sandbox_pid_namespace: LinuxSandboxPidNamespace::default(),
            proxy_private_ips_via_upstream: false,
            executor_self_exe: absolute_path(executor_self_exe)?,
            executor_linux_sandbox_exe: executor_linux_sandbox_exe
                .map(absolute_path)
                .transpose()?,
            #[cfg(target_os = "macos")]
            allowed_symlinked_nova_home: None,
        })
    }

    /// Applies the PID namespace policy chosen by trusted executor provisioning.
    /// （对位 codex 39f4277087）
    pub fn with_linux_sandbox_pid_namespace(mut self, mode: LinuxSandboxPidNamespace) -> Self {
        self.linux_sandbox_pid_namespace = mode;
        self
    }

    /// Routes permitted private IPs through the executor's configured upstream proxy.
    /// （对位 codex 39f4277087 `with_proxy_private_ips_via_upstream`）
    pub fn with_proxy_private_ips_via_upstream(mut self, enabled: bool) -> Self {
        self.proxy_private_ips_via_upstream = enabled;
        self
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
