//! Startup-owned Linux PID isolation policy, independent of request permissions.
//!
//! 对位 codex 39f4277087 `codex-rs/sandboxing/src/linux_pid_namespace.rs`：
//! Linux bwrap 沙箱的 PID 命名空间策略（启动期持有的可信旋钮，请求与仓库
//! 配置均不可改写）。

/// Selects the PID namespace policy for Linux bubblewrap sandboxes.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum LinuxSandboxPidNamespace {
    /// Create a new PID namespace, retaining the legacy `/proc` mount fallback.
    #[default]
    Isolate,
    /// Reuse the caller's PID namespace and `/proc`, allowing signals to other same-UID processes.
    Inherit,
}

impl std::str::FromStr for LinuxSandboxPidNamespace {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "isolate" => Ok(Self::Isolate),
            "inherit" => Ok(Self::Inherit),
            _ => Err("expected 'isolate' or 'inherit'".to_string()),
        }
    }
}
