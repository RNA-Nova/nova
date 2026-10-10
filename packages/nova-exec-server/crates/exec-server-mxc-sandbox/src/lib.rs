//! Native Windows MXC helper. Policy conversion is portable; execution requires
//! a usable process security environment and never enters MXC's ACL fallbacks.

#[cfg(windows)]
mod native;
#[cfg(any(windows, test))]
mod policy;
mod transport;
#[cfg(windows)]
mod windows;

use std::collections::HashMap;
use std::path::Path;
use std::path::PathBuf;

use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use nova_exec_server_network_proxy::ManagedNetworkSandboxContext;
use nova_exec_server_protocol_core::models::PermissionProfile;
use serde::Deserialize;
use serde::Serialize;

pub const NOVA_EXEC_SERVER_WINDOWS_MXC_ARG1: &str = "--__nova-windows-mxc";
const CLIENT_ONLY_LOOPBACK_UNSUPPORTED: &str = "MXC cannot enforce managed networking with allow_local_binding=false: native host-loopback access is bidirectional";

fn validate_managed_network(network: &ManagedNetworkSandboxContext) -> Result<()> {
    ensure!(
        !network.loopback_ports.is_empty(),
        "MXC managed networking requires dedicated proxy ports"
    );
    ensure!(
        !network.loopback_ports.contains(&0),
        "MXC proxy ports must be nonzero"
    );
    ensure!(
        network.allow_local_binding,
        CLIENT_ONLY_LOOPBACK_UNSUPPORTED
    );
    Ok(())
}

#[derive(Serialize, Deserialize)]
struct MxcCommand {
    permissions: PermissionProfile,
    sandbox_policy_cwd: PathBuf,
    managed_network: Option<ManagedNetworkSandboxContext>,
    command: Vec<String>,
}

/// Inputs used to build an MXC helper invocation.
#[derive(Debug)]
pub struct CreateMxcCommandArgsParams<'a> {
    pub command: Vec<String>,
    pub permission_profile: &'a PermissionProfile,
    pub sandbox_policy_cwd: &'a Path,
    pub managed_network: Option<&'a ManagedNetworkSandboxContext>,
    pub env: &'a mut HashMap<String, String>,
}

/// Wrap exact argv and add bounded launcher-only environment variables. The
/// helper removes these variables before starting the sandboxed command.
pub fn create_command_args(args: CreateMxcCommandArgsParams<'_>) -> Result<Vec<String>> {
    let CreateMxcCommandArgsParams {
        command,
        permission_profile,
        sandbox_policy_cwd,
        managed_network,
        env,
    } = args;
    sandbox_policy_cwd
        .to_str()
        .context("MXC requires a Unicode policy working directory")?;
    if let Some(network) = managed_network {
        validate_managed_network(network)?;
    }
    transport::encode(
        &MxcCommand {
            permissions: permission_profile.clone(),
            sandbox_policy_cwd: sandbox_policy_cwd.to_owned(),
            managed_network: managed_network.cloned(),
            command,
        },
        env,
    )?;
    Ok(vec![NOVA_EXEC_SERVER_WINDOWS_MXC_ARG1.to_owned()])
}

/// Whether the executor exposes MXC's process security-environment API set.
/// This deliberately excludes MXC's older AppContainer fallback backends.
pub fn is_available() -> bool {
    #[cfg(windows)]
    {
        // 对位 codex 1c7c43cd85：改用 SDK 缓存的 PSEC API-set 可用性检查。
        mxc_sdk::process_container_common::base_container_runner::BaseContainerRunner::is_base_container_api_present()
    }
    #[cfg(not(windows))]
    {
        false
    }
}

// 对位 codex 3342ee8c07：deny glob 扫描器常量转出口（供完整性清点引用）
#[cfg(windows)]
pub use nova_exec_server_windows_sandbox::GLOB_SCAN_PROGRAM;

/// Resolve configured symbols and snapshot deny globs for policy inspection.
/// Reuses MXC's resolution without adding native grants or protections.
/// （对位 codex 3342ee8c07 `prepare_file_system_policy`）
#[cfg(windows)]
pub fn prepare_file_system_policy(
    policy: nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy,
    env: &HashMap<String, String>,
    policy_cwd: &nova_exec_server_utils_absolute_path::AbsolutePathBuf,
    command_cwd: &Path,
) -> Result<nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy> {
    use nova_exec_server_utils_path_uri::PathUri;

    let env_entries = env
        .iter()
        .map(|(key, value)| format!("{key}={value}"))
        .collect::<Vec<_>>();
    let mut policy = policy::materialize_temporary_paths(policy, &env_entries)?;
    let denied = nova_exec_server_windows_sandbox::resolve_windows_deny_read_paths_in_environment(
        &policy,
        policy_cwd,
        env,
        command_cwd,
    )
    .map_err(anyhow::Error::msg)?;
    policy = policy.with_expanded_deny_globs(denied);
    if policy.has_full_disk_write_access() {
        return Ok(policy);
    }
    let volumes = windows::volume_roots(policy_cwd.as_path(), command_cwd)?
        .into_iter()
        .map(PathUri::from_host_native_path)
        .collect::<std::io::Result<Vec<_>>>()?;
    Ok(policy::materialize_volume_roots(policy, &volumes)?)
}

/// Entry point dispatched before ordinary nova CLI parsing.
pub fn run_main() -> ! {
    #[cfg(windows)]
    {
        match windows::run() {
            Ok(exit_code) => std::process::exit(exit_code),
            Err(error) => {
                eprintln!("MXC launcher: {error:#}");
                std::process::exit(1);
            }
        }
    }
    #[cfg(not(windows))]
    {
        eprintln!("MXC sandbox execution requires Windows");
        std::process::exit(1);
    }
}

#[cfg(test)]
#[path = "policy_tests.rs"]
mod tests;
