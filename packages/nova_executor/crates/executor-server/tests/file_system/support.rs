use std::fmt;
use std::sync::Arc;

use anyhow::Result;
use nova_executor_protocol_core::config_types::WindowsSandboxLevel;
use nova_executor_protocol_core::models::PermissionProfile;
use nova_executor_protocol_core::permissions::FileSystemAccessMode;
use nova_executor_protocol_core::permissions::FileSystemPath;
use nova_executor_protocol_core::permissions::FileSystemSandboxEntry;
use nova_executor_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_executor_protocol_core::permissions::FileSystemSpecialPath;
use nova_executor_protocol_core::permissions::NetworkSandboxPolicy;
use nova_executor_server::Environment;
use nova_executor_server::ExecServerRuntimePaths;
use nova_executor_server::ExecutorFileSystem;
use nova_executor_server::FileSystemSandboxContext;
use nova_executor_server::LocalFileSystem;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
#[cfg(windows)]
use nova_executor_utils_path_uri::PathUri;

use crate::common::exec_server::ExecServerHarness;
use crate::common::exec_server::TestExecutorHelperPaths;
use crate::common::exec_server::exec_server;
use crate::common::exec_server::test_executor_helper_paths;

pub(crate) struct FileSystemContext {
    pub(crate) file_system: Arc<dyn ExecutorFileSystem>,
    _helper_paths: Option<TestExecutorHelperPaths>,
    _server: Option<ExecServerHarness>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum FileSystemImplementation {
    Local,
    Remote,
}

impl fmt::Display for FileSystemImplementation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Local => formatter.write_str("local"),
            Self::Remote => formatter.write_str("remote"),
        }
    }
}

pub(crate) async fn create_file_system_context(
    implementation: FileSystemImplementation,
) -> Result<FileSystemContext> {
    match implementation {
        FileSystemImplementation::Local => {
            let helper_paths = test_executor_helper_paths()?;
            let runtime_paths = ExecServerRuntimePaths::new(
                helper_paths.executor_exe.clone(),
                helper_paths.executor_linux_sandbox_exe.clone(),
            )?;
            Ok(FileSystemContext {
                file_system: Arc::new(LocalFileSystem::with_runtime_paths(runtime_paths)),
                _helper_paths: Some(helper_paths),
                _server: None,
            })
        }
        FileSystemImplementation::Remote => {
            let server = exec_server().await?;
            let environment =
                Environment::create_for_tests(Some(server.websocket_url().to_string()))?;
            Ok(FileSystemContext {
                file_system: environment.get_filesystem(),
                _helper_paths: None,
                _server: Some(server),
            })
        }
    }
}

#[cfg(windows)]
pub(crate) fn is_unsupported_restricted_token_host<T>(result: &std::io::Result<T>) -> bool {
    result.as_ref().err().is_some_and(|err| {
        err.to_string()
            .contains("windows sandbox failed: CreateRestrictedToken failed: 87")
    })
}

pub(crate) fn absolute_path(path: std::path::PathBuf) -> AbsolutePathBuf {
    assert!(
        path.is_absolute(),
        "path must be absolute: {}",
        path.display()
    );
    AbsolutePathBuf::try_from(path).expect("path should be absolute")
}

pub(crate) fn read_only_sandbox(readable_root: std::path::PathBuf) -> FileSystemSandboxContext {
    let readable_root = absolute_path(readable_root);
    sandbox_context(vec![FileSystemSandboxEntry {
        path: FileSystemPath::Path {
            path: readable_root.into(),
        },
        access: FileSystemAccessMode::Read,
        missing_path_behavior: None,
    }])
}

#[cfg(not(windows))]
pub(crate) fn workspace_write_sandbox(
    writable_root: std::path::PathBuf,
) -> FileSystemSandboxContext {
    let writable_root = absolute_path(writable_root);
    sandbox_context(vec![FileSystemSandboxEntry {
        path: FileSystemPath::Path {
            path: writable_root.into(),
        },
        access: FileSystemAccessMode::Write,
        missing_path_behavior: None,
    }])
}

#[cfg(windows)]
pub(crate) fn workspace_write_sandbox(
    writable_root: std::path::PathBuf,
) -> FileSystemSandboxContext {
    let writable_root = absolute_path(writable_root);
    // Keep the runtime policy aligned with the legacy workspace-write projection used by the
    // unelevated restricted-token preflight.
    let policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            FileSystemAccessMode::Read,
        ),
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::project_roots(/*subpath*/ None),
            },
            FileSystemAccessMode::Write,
        ),
    ]);
    let mut sandbox = FileSystemSandboxContext::from_permission_profile_with_cwd(
        PermissionProfile::from_runtime_permissions(&policy, NetworkSandboxPolicy::Restricted),
        PathUri::from_abs_path(&writable_root),
    );
    sandbox.windows_sandbox_level = WindowsSandboxLevel::RestrictedToken;
    sandbox
}

fn sandbox_context(mut entries: Vec<FileSystemSandboxEntry>) -> FileSystemSandboxContext {
    if cfg!(windows) {
        // Restricted-token sandboxing cannot enforce read restrictions, so leave the root
        // readable while exercising the requested write restrictions.
        entries.push(FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            FileSystemAccessMode::Read,
        ));
    }
    let mut sandbox = FileSystemSandboxContext::from_permission_profile(
        PermissionProfile::from_runtime_permissions(
            &FileSystemSandboxPolicy::restricted(entries),
            NetworkSandboxPolicy::Restricted,
        ),
    );
    if cfg!(windows) {
        sandbox.windows_sandbox_level = WindowsSandboxLevel::RestrictedToken;
    }
    sandbox
}
