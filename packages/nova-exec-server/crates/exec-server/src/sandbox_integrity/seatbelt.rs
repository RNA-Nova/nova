//! Seatbelt keeps deny globs as native patterns; no snapshot scan is needed.
//! （对位 codex b5c37c35fd `sandbox_integrity/seatbelt.rs`）

use super::dependencies::Dependency;
use super::dependencies::nova_executable;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_sandboxing::SandboxExecRequest;
use nova_exec_server_sandboxing::SandboxType;
use nova_exec_server_utils_absolute_path::AbsolutePathBuf;
use std::io;
use std::path::PathBuf;

pub(super) const SANDBOX_TYPE: SandboxType = SandboxType::MacosSeatbelt;
pub(super) const NAME: &str = "seatbelt";

pub(super) fn critical_dependencies(
    _request: &SandboxExecRequest,
    _policy: &FileSystemSandboxPolicy,
    _cwd: &AbsolutePathBuf,
) -> io::Result<Vec<Dependency>> {
    Ok(vec![
        nova_executable(),
        (
            "seatbelt",
            Ok(PathBuf::from(
                nova_exec_server_sandboxing::seatbelt::MACOS_PATH_TO_SEATBELT_EXECUTABLE,
            )),
        ),
    ])
}

pub(super) fn prepare_policy(
    _request: &SandboxExecRequest,
    policy: FileSystemSandboxPolicy,
    _cwd: &AbsolutePathBuf,
) -> io::Result<FileSystemSandboxPolicy> {
    Ok(policy)
}
