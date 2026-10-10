//! Discover MXC dependencies separately from resolving configured policy rules.
//! （对位 codex 3342ee8c07 `sandbox_integrity/mxc.rs`）

use super::dependencies::Dependency;
use super::dependencies::nova_and_launcher_dependencies;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_sandboxing::SandboxExecRequest;
use nova_exec_server_sandboxing::SandboxType;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use std::io;

pub(super) const SANDBOX_TYPE: SandboxType = SandboxType::WindowsMxc;
pub(super) const NAME: &str = "windows_mxc";

pub(super) fn critical_dependencies(
    request: &SandboxExecRequest,
    policy: &FileSystemSandboxPolicy,
    cwd: &AbsolutePathBuf,
) -> io::Result<Vec<Dependency>> {
    let command_cwd = request.cwd.to_abs_path()?;
    let mut dependencies = nova_and_launcher_dependencies(request);
    if !policy
        .get_unreadable_globs_with_cwd(cwd.as_path())
        .is_empty()
    {
        let path = request
            .env
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case("PATH"))
            .map(|(_, value)| value);
        if let Ok(scanner) = which::which_in(
            nova_exec_server_mxc_sandbox::GLOB_SCAN_PROGRAM,
            path,
            command_cwd.as_path(),
        ) {
            dependencies.push(("glob_scanner", Ok(scanner)));
        }
    }
    Ok(dependencies)
}

pub(super) fn prepare_policy(
    request: &SandboxExecRequest,
    policy: FileSystemSandboxPolicy,
    cwd: &AbsolutePathBuf,
) -> io::Result<FileSystemSandboxPolicy> {
    let command_cwd = request.cwd.to_abs_path()?;
    nova_exec_server_mxc_sandbox::prepare_file_system_policy(
        policy,
        &request.env,
        cwd,
        command_cwd.as_path(),
    )
    .map_err(io::Error::other)
}
