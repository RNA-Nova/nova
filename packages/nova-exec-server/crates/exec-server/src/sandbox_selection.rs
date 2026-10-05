//! Resolves an executor sandbox context to a concrete local sandbox implementation.
//!
//! 对位 codex `exec-server/src/sandbox_selection.rs`。

use nova_exec_server_file_system::FileSystemSandboxContext;
use nova_exec_server_file_system::WindowsSandboxSelection;
use nova_exec_server_protocol_core::config_types::WindowsSandboxLevel;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_sandboxing::SandboxManager;
use nova_exec_server_sandboxing::SandboxType;
use nova_exec_server_sandboxing::SandboxablePreference;

pub(crate) fn select_sandbox(
    manager: &SandboxManager,
    permission_profile: &PermissionProfile,
    sandbox_context: &FileSystemSandboxContext,
    has_managed_network_requirements: bool,
) -> (SandboxType, Option<WindowsSandboxLevel>) {
    let windows_sandbox_level = match sandbox_context.windows_sandbox_selection {
        WindowsSandboxSelection::Disabled => WindowsSandboxLevel::Disabled,
        WindowsSandboxSelection::RestrictedToken => WindowsSandboxLevel::RestrictedToken,
        WindowsSandboxSelection::Elevated => WindowsSandboxLevel::Elevated,
        WindowsSandboxSelection::Mxc => return (SandboxType::WindowsMxc, None),
    };
    let windows_sandbox_type = match windows_sandbox_level {
        WindowsSandboxLevel::Disabled => SandboxType::None,
        WindowsSandboxLevel::RestrictedToken | WindowsSandboxLevel::Elevated => {
            SandboxType::WindowsRestrictedToken
        }
    };
    let sandbox_type = manager.select_initial(
        permission_profile,
        SandboxablePreference::Require,
        windows_sandbox_type,
        has_managed_network_requirements,
    );
    (sandbox_type, Some(windows_sandbox_level))
}
