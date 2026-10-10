#[cfg(target_os = "linux")]
mod bwrap;
mod denial;
pub mod landlock;
mod manager;
pub mod policy_transforms;
// 对位 codex 11da6b9edc：沙箱完整性检查件（FileContentsChecker 等）
mod sandbox_integrity;
#[cfg(target_os = "macos")]
pub mod seatbelt;
mod spawn;
// 对位 codex origin/main（spawn 管线批）：终端查询应答
mod terminal_queries;
mod violation;
mod windows;
#[cfg(windows)]
mod windows_mxc;

#[cfg(target_os = "linux")]
pub use bwrap::find_executable_in_search_paths;
#[cfg(target_os = "linux")]
pub use bwrap::find_pre_sandbox_executable_in_path;
#[cfg(target_os = "linux")]
pub use bwrap::find_system_bwrap_in_path;
#[cfg(target_os = "linux")]
pub use bwrap::system_bwrap_warning;
pub use denial::is_likely_executor_managed_sandbox_denied;
pub use denial::is_likely_sandbox_denied;
pub use manager::SandboxCommand;
pub use nova_exec_server_mxc_sandbox::NOVA_EXEC_SERVER_WINDOWS_MXC_ARG1;
pub use nova_exec_server_mxc_sandbox::is_available as windows_mxc_available;
pub use nova_exec_server_mxc_sandbox::run_main as run_windows_mxc_main;
pub use manager::SandboxDirectSpawnTransformRequest;
pub use manager::SandboxExecRequest;
pub use manager::SandboxManager;
pub use manager::SandboxTransformError;
pub use manager::SandboxTransformRequest;
pub use manager::SandboxType;
pub use manager::SandboxablePreference;
pub use manager::compatibility_sandbox_policy_for_permission_profile;
pub use manager::get_platform_sandbox;
pub use manager::with_managed_mitm_ca_readable_root;
// 对位 codex 11da6b9edc
pub use sandbox_integrity::FileContentsChecker;
pub use sandbox_integrity::IntegrityFinding;
pub use sandbox_integrity::IntegrityFindingDetails;
pub use nova_exec_server_protocol_core::config_types::WindowsSandboxProxySettingsMode;
pub use spawn::SpawnRequest;
pub use spawn::WindowsSandboxSpawnRequest;
pub use spawn::spawn_process;
pub use violation::FileSystemSandboxViolation;
pub use violation::FileSystemSandboxViolationReason;
pub use violation::NetworkSandboxViolation;
pub use violation::SandboxViolationBackend;
pub use violation::SandboxViolationEvent;
pub use violation::record_filesystem_sandbox_violation;
pub use violation::record_network_sandbox_violation;
pub use violation::record_sandbox_violation;
pub use windows::WindowsSandboxFilesystemOverrides;
pub use windows::permission_profile_supports_windows_restricted_token_sandbox;
pub use windows::resolve_windows_elevated_filesystem_overrides;
pub use windows::resolve_windows_restricted_token_filesystem_overrides;
pub use windows::unsupported_windows_restricted_token_sandbox_reason;
pub use windows::windows_sandbox_uses_elevated_backend;

use nova_exec_server_protocol_core::error::ExecErr;

#[cfg(not(target_os = "linux"))]
pub fn system_bwrap_warning(
    _permission_profile: &nova_exec_server_protocol_core::models::PermissionProfile,
    // 对位 codex 7aa8f51049：桩签名与 linux 版同步（新增 cwd 参）。
    _sandbox_policy_cwd: &std::path::Path,
) -> Option<String> {
    None
}

impl From<SandboxTransformError> for ExecErr {
    fn from(err: SandboxTransformError) -> Self {
        match err {
            error @ SandboxTransformError::InvalidCommandCwd { .. }
            | error @ SandboxTransformError::InvalidSandboxPolicyCwd { .. } => {
                ExecErr::InvalidRequest(error.to_string())
            }
            SandboxTransformError::MissingLinuxSandboxExecutable => {
                ExecErr::LandlockSandboxExecutableNotProvided
            }
            SandboxTransformError::WindowsMxcPreparation(message) => {
                ExecErr::UnsupportedOperation(message)
            }
            SandboxTransformError::EnvironmentNetworkProxy(message) => {
                ExecErr::UnsupportedOperation(message)
            }
            #[cfg(target_os = "macos")]
            SandboxTransformError::SeatbeltPreparation(message) => {
                ExecErr::UnsupportedOperation(message)
            }
            #[cfg(target_os = "linux")]
            SandboxTransformError::Wsl1UnsupportedForBubblewrap => {
                ExecErr::UnsupportedOperation(crate::bwrap::WSL1_BWRAP_WARNING.to_string())
            }
            #[cfg(not(target_os = "macos"))]
            SandboxTransformError::SeatbeltUnavailable => ExecErr::UnsupportedOperation(
                "seatbelt sandbox is only available on macOS".to_string(),
            ),
            #[cfg(target_os = "windows")]
            SandboxTransformError::WindowsSandboxPreparation(message) => {
                ExecErr::UnsupportedOperation(message)
            }
        }
    }
}
