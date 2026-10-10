//! 对位 codex 622e9e3696 `protocol/src/sandbox.rs`：承载命令的沙箱实现与覆盖选择。
//!
//! 与上游的差异：codex 同文件里的 `SandboxType` 在 nova 已居
//! `nova_exec_server_sandboxing::manager`（既有住所，不随迁）；`effective_windows_sandbox_type`
//! 未镜像（nova 以 file-system 的 `WindowsSandboxSelection` 承载实现选择）。本文件只补
//! 本次新增的 `SandboxOverride`。

use serde::Deserialize;
use serde::Serialize;

/// Controller decision that overrides normal sandbox selection for a command.
/// （对位 codex 622e9e3696：控制面选定的沙箱覆盖决策，仅作观测随行，
/// 从不作为放宽权限的授权。）
#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum SandboxOverride {
    /// Use normal sandbox selection.
    #[default]
    NoOverride,
    /// Broaden approved filesystem access while retaining denied-read enforcement.
    EscalatedSandboxWithRestrictions,
    /// Bypass the sandbox on the first attempt.
    BypassSandboxFirstAttempt,
}

impl SandboxOverride {
    pub fn is_no_override(&self) -> bool {
        *self == Self::NoOverride
    }
}
