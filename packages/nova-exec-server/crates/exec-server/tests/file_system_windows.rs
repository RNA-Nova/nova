#![cfg(windows)]
#![allow(clippy::expect_used)]

mod common;

#[path = "file_system/shared.rs"]
mod shared;
#[path = "file_system/support.rs"]
mod support;

use std::path::Path;
use std::process::Command;
use std::time::Duration;

use anyhow::Context as _;
use anyhow::Result;
use nova_exec_server_file_system::WindowsSandboxSelection;
use nova_exec_server_protocol_core::models::PermissionProfile;
use nova_exec_server_protocol_core::permissions::FileSystemAccessMode;
use nova_exec_server_protocol_core::permissions::FileSystemPath;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxEntry;
use nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy;
use nova_exec_server_protocol_core::permissions::FileSystemSpecialPath;
use nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy;
use nova_exec_server_protocol_core::protocol::SandboxPolicy;
use nova_exec_server::CreateDirectoryOptions;
use nova_exec_server::FileSystemSandboxContext;
use nova_exec_server::GetMetadataOptions;
use nova_exec_server::ReadFileOptions;
use nova_exec_server::RemoveOptions;
use nova_exec_server::WriteFileOptions;
use nova_exec_server_utils_path_uri::PathUri;
use test_case::test_case;
use tokio::net::windows::named_pipe::ServerOptions;
use tokio::time::timeout;
use uuid::Uuid;

use crate::support::FileSystemImplementation;
use crate::support::create_file_system_context;
use crate::support::is_unsupported_restricted_token_host;
use crate::support::workspace_write_sandbox;

fn create_directory_junction(target: &Path, alias: &Path) -> Result<()> {
    let output = Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(alias)
        .arg(target)
        .output()?;
    if !output.status.success() {
        anyhow::bail!(
            "mklink /J failed: stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout).trim(),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}

#[test_case(FileSystemImplementation::Local ; "local")]
#[test_case(FileSystemImplementation::Remote ; "remote")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn file_system_canonicalize_resolves_directory_junction(
    implementation: FileSystemImplementation,
) -> Result<()> {
    shared::assert_canonicalize_resolves_directory_alias(implementation, create_directory_junction)
        .await
}

#[test_case(FileSystemImplementation::Local ; "local")]
#[test_case(FileSystemImplementation::Remote ; "remote")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn file_system_sandboxed_canonicalize_resolves_directory_junction(
    implementation: FileSystemImplementation,
) -> Result<()> {
    shared::assert_sandboxed_canonicalize_resolves_directory_alias(
        implementation,
        create_directory_junction,
    )
    .await
}

#[test_case(FileSystemImplementation::Local ; "local")]
#[test_case(FileSystemImplementation::Remote ; "remote")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn file_system_operations_can_reject_junctions_in_any_path_component(
    implementation: FileSystemImplementation,
) -> Result<()> {
    let context = create_file_system_context(implementation).await?;
    let tmp = tempfile::TempDir::new()?;
    let real = tmp.path().join("real");
    std::fs::create_dir(&real)?;
    let existing = real.join("existing.txt");
    std::fs::write(&existing, "unchanged")?;
    let removable = real.join("removable.txt");
    std::fs::write(&removable, "keep")?;
    let directory_junction = tmp.path().join("directory-junction");
    create_directory_junction(&real, &directory_junction)?;

    let no_follow_read = ReadFileOptions {
        follow_symlinks: false,
    };
    let no_follow_write = WriteFileOptions {
        follow_symlinks: false,
    };
    let no_follow_metadata = GetMetadataOptions {
        follow_symlinks: false,
    };
    let no_follow_create = CreateDirectoryOptions {
        recursive: true,
        follow_symlinks: false,
    };
    let no_follow_remove = RemoveOptions {
        recursive: false,
        force: false,
        follow_symlinks: false,
    };
    let uri = |path: &Path| PathUri::from_host_native_path(path);

    assert_eq!(
        context
            .file_system
            .read_file(&uri(&existing)?, no_follow_read, /*sandbox*/ None,)
            .await?,
        b"unchanged"
    );

    let file_link_target = real.join("file-link-target.txt");
    std::fs::write(&file_link_target, "target")?;
    let file_link = tmp.path().join("file-link.txt");
    if std::os::windows::fs::symlink_file(&file_link_target, &file_link).is_ok() {
        assert!(
            context
                .file_system
                .write_file(
                    &uri(&file_link)?,
                    b"changed".to_vec(),
                    no_follow_write,
                    /*sandbox*/ None,
                )
                .await
                .is_err()
        );
        assert_eq!(std::fs::read_to_string(&file_link_target)?, "target");
    }

    assert!(
        context
            .file_system
            .read_file(
                &uri(&directory_junction.join("existing.txt"))?,
                no_follow_read,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(
        context
            .file_system
            .write_file(
                &uri(&directory_junction.join("existing.txt"))?,
                b"changed".to_vec(),
                no_follow_write,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert_eq!(std::fs::read_to_string(&existing)?, "unchanged");
    assert!(
        context
            .file_system
            .get_metadata(
                &uri(&directory_junction)?,
                no_follow_metadata,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    let directory_metadata = context
        .file_system
        .get_metadata(&uri(&real)?, no_follow_metadata, /*sandbox*/ None)
        .await?;
    assert!(directory_metadata.is_directory);
    assert!(
        context
            .file_system
            .create_directory(
                &uri(&directory_junction.join("created"))?,
                no_follow_create,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(!real.join("created").exists());
    assert!(
        context
            .file_system
            .remove(
                &uri(&directory_junction.join("removable.txt"))?,
                no_follow_remove,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(removable.exists());
    assert!(
        context
            .file_system
            .remove(
                &uri(&directory_junction)?,
                no_follow_remove,
                /*sandbox*/ None,
            )
            .await
            .is_err()
    );
    assert!(
        directory_junction
            .symlink_metadata()?
            .file_type()
            .is_symlink()
    );

    let sandbox = workspace_write_sandbox(tmp.path().to_path_buf());
    // 对位 codex a4ee536f01：全盘整读不再进沙箱——读直通便过（不再能用来探测
    // restricted-token 不可用主机）；沙箱执法改由写路径验证。
    assert_eq!(
        context
            .file_system
            .read_file(&uri(&existing)?, no_follow_read, Some(&sandbox))
            .await?,
        b"unchanged"
    );
    let write_result = context
        .file_system
        .write_file(
            &uri(&existing)?,
            b"unchanged".to_vec(),
            no_follow_write,
            Some(&sandbox),
        )
        .await;
    if is_unsupported_restricted_token_host(&write_result) {
        return Ok(());
    }
    write_result?;
    assert!(
        context
            .file_system
            .read_file(
                &uri(&directory_junction.join("existing.txt"))?,
                no_follow_read,
                Some(&sandbox),
            )
            .await
            .is_err()
    );
    assert!(
        context
            .file_system
            .write_file(
                &uri(&directory_junction.join("existing.txt"))?,
                b"changed".to_vec(),
                no_follow_write,
                Some(&sandbox),
            )
            .await
            .is_err()
    );
    assert_eq!(std::fs::read_to_string(&existing)?, "unchanged");
    assert!(
        context
            .file_system
            .get_metadata(
                &uri(&directory_junction)?,
                no_follow_metadata,
                Some(&sandbox),
            )
            .await
            .is_err()
    );
    assert!(
        context
            .file_system
            .create_directory(
                &uri(&directory_junction.join("sandbox-created"))?,
                no_follow_create,
                Some(&sandbox),
            )
            .await
            .is_err()
    );
    assert!(!real.join("sandbox-created").exists());
    assert!(
        context
            .file_system
            .remove(
                &uri(&directory_junction.join("removable.txt"))?,
                no_follow_remove,
                Some(&sandbox),
            )
            .await
            .is_err()
    );
    assert!(removable.exists());

    Ok(())
}

#[test_case(FileSystemImplementation::Local ; "local")]
#[test_case(FileSystemImplementation::Remote ; "remote")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn file_system_no_follow_operations_reject_named_pipes(
    implementation: FileSystemImplementation,
) -> Result<()> {
    let context = create_file_system_context(implementation).await?;
    let pipe_name = format!("nova-fs-no-follow-{}", Uuid::new_v4());
    let server_path = format!(r"\\.\pipe\{pipe_name}");
    let client_path = format!(r"\\localhost\pipe\{pipe_name}");
    let _pipe = ServerOptions::new()
        .first_pipe_instance(true)
        .create(&server_path)?;

    let error = timeout(
        Duration::from_secs(1),
        context.file_system.read_file(
            &PathUri::from_host_native_path(Path::new(&client_path))?,
            ReadFileOptions {
                follow_symlinks: false,
            },
            /*sandbox*/ None,
        ),
    )
    .await
    .expect("strict named-pipe read must not hang")
    .expect_err("strict named-pipe read must be rejected");
    assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);

    let pipe_name = format!("nova-fs-no-follow-write-{}", Uuid::new_v4());
    let server_path = format!(r"\\.\pipe\{pipe_name}");
    let client_path = format!(r"\\localhost\pipe\{pipe_name}");
    let _pipe = ServerOptions::new()
        .first_pipe_instance(true)
        .create(&server_path)?;
    timeout(
        Duration::from_secs(1),
        context.file_system.write_file(
            &PathUri::from_host_native_path(Path::new(&client_path))?,
            b"must not be written".to_vec(),
            WriteFileOptions {
                follow_symlinks: false,
            },
            /*sandbox*/ None,
        ),
    )
    .await
    .expect("strict named-pipe write must not hang")
    .expect_err("strict named-pipe write must be rejected");
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn file_system_remote_fs_helper_respects_windows_sandbox_write_policy() -> Result<()> {
    let context = create_file_system_context(FileSystemImplementation::Remote).await?;
    let file_system = context.file_system;
    let tmp = tempfile::TempDir::new()?;
    let readonly_dir = tmp.path().join("readonly");
    std::fs::create_dir_all(&readonly_dir)?;

    let mut sandbox = read_only_sandbox_for_cwd(readonly_dir.clone())?;
    sandbox.windows_sandbox_selection = WindowsSandboxSelection::RestrictedToken;

    let readable_file = readonly_dir.join("readable.txt");
    std::fs::write(&readable_file, b"readable")?;
    // 对位 codex a4ee536f01：只读权限档的读不再进沙箱——读直通便过；沙箱执法
    // 改由写路径验证（restricted-token 探测也随之挪到写结果上）。
    let read_result = file_system
        .read_file(
            &PathUri::from_host_native_path(&readable_file)?,
            ReadFileOptions::default(),
            Some(&sandbox),
        )
        .await;
    assert_eq!(read_result?, b"readable");

    let blocked_file = readonly_dir.join("blocked.txt");
    let write_result = file_system
        .write_file(
            &PathUri::from_host_native_path(&blocked_file)?,
            b"blocked".to_vec(),
            WriteFileOptions::default(),
            Some(&sandbox),
        )
        .await;
    // Some local Windows hosts cannot create restricted tokens. Reaching that
    // error still proves the write went through the Windows sandbox launcher.
    if is_unsupported_restricted_token_host(&write_result) {
        assert!(!blocked_file.exists());
        return Ok(());
    }
    let error = write_result.expect_err("write outside the sandbox should fail");
    assert!(
        !blocked_file.exists(),
        "sandboxed fs helper must not create blocked file after error: {error}"
    );

    Ok(())
}

fn read_only_sandbox_for_cwd(cwd: std::path::PathBuf) -> Result<FileSystemSandboxContext> {
    Ok(FileSystemSandboxContext::from_legacy_sandbox_policy(
        SandboxPolicy::new_read_only_policy(),
        PathUri::from_host_native_path(cwd)?,
    )?)
}

/// An elevated filesystem helper must enforce relative deny globs from the policy cwd.
/// （对位 codex 841b5490b2 `file_system_elevated_relative_read_denial_uses_policy_cwd`；
/// helper 二进制/资源目录名换 nova 命名）
#[test_case(FileSystemImplementation::Local ; "local")]
#[test_case(FileSystemImplementation::Remote ; "remote")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(remote_exec_server)]
async fn file_system_elevated_relative_read_denial_uses_policy_cwd(
    implementation: FileSystemImplementation,
) -> Result<()> {
    // Both implementations re-enter this test binary; the elevated backend finds its helpers
    // next to that binary, while Cargo and Bazel provide them separately.
    let test_exe = std::env::current_exe()?;
    let resources = test_exe
        .parent()
        .context("Windows test executable should have a parent directory")?
        .join("nova-resources");
    if let Err(error) = std::fs::create_dir_all(&resources)
        && !(error.kind() == std::io::ErrorKind::PermissionDenied && resources.is_dir())
    {
        return Err(error).context("create Windows sandbox test resources");
    }
    for name in ["nova-windows-sandbox-setup", "nova-command-runner"] {
        let source = nova_exec_server_utils_cargo_bin::cargo_bin(name)?;
        let destination = resources.join(Path::new(name).with_extension("exe"));
        if let Err(error) = std::fs::copy(&source, &destination)
            && !(error.kind() == std::io::ErrorKind::PermissionDenied && destination.is_file())
        {
            return Err(error).with_context(|| format!("stage Windows sandbox helper {name}"));
        }
    }
    let context = create_file_system_context(implementation).await?;
    let tmp = tempfile::TempDir::new()?;
    let policy_cwd = tmp.path().join("checkout");
    let selected_files = policy_cwd.join("files");
    let other_files = tmp.path().join("files");
    std::fs::create_dir_all(&selected_files)?;
    std::fs::create_dir(&other_files)?;
    let allowed_neighbor = selected_files.join("allowed.txt");
    let denied = selected_files.join("blocked.env");
    let same_name_outside = other_files.join("blocked.env");
    std::fs::write(&allowed_neighbor, b"allowed neighbor")?;
    std::fs::write(&denied, b"denied")?;
    std::fs::write(&same_name_outside, b"allowed outside")?;

    let cwd = PathUri::from_host_native_path(&policy_cwd)?;
    let policy = FileSystemSandboxPolicy::restricted(vec![
        FileSystemSandboxEntry::new(
            FileSystemPath::Special {
                value: FileSystemSpecialPath::Root,
            },
            FileSystemAccessMode::Read,
        ),
        FileSystemSandboxEntry::new(
            FileSystemPath::Path {
                path: PathUri::from_host_native_path(tmp.path())?,
            },
            FileSystemAccessMode::Write,
        ),
        FileSystemSandboxEntry::new(
            FileSystemPath::GlobPattern {
                pattern: "files/*.env".to_string(),
            },
            FileSystemAccessMode::Deny,
        ),
    ]);
    let mut sandbox = FileSystemSandboxContext::from_permission_profile(
        PermissionProfile::from_runtime_permissions(&policy, NetworkSandboxPolicy::Restricted),
        cwd,
    );
    sandbox.windows_sandbox_selection = WindowsSandboxSelection::Elevated;

    let file_system = &context.file_system;
    let allowed_neighbor = file_system
        .read_file(
            &PathUri::from_host_native_path(&allowed_neighbor)?,
            ReadFileOptions::default(),
            Some(&sandbox),
        )
        .await?;
    let same_name_outside = file_system
        .read_file(
            &PathUri::from_host_native_path(&same_name_outside)?,
            ReadFileOptions::default(),
            Some(&sandbox),
        )
        .await?;
    let denied = file_system
        .read_file(
            &PathUri::from_host_native_path(&denied)?,
            ReadFileOptions::default(),
            Some(&sandbox),
        )
        .await
        .expect_err("read matching the policy-cwd denial must be rejected");
    assert_eq!(
        (
            allowed_neighbor.as_slice(),
            same_name_outside.as_slice(),
            denied.kind(),
        ),
        (
            b"allowed neighbor".as_slice(),
            b"allowed outside".as_slice(),
            std::io::ErrorKind::InvalidInput,
        )
    );
    assert!(
        denied.to_string().contains("Access is denied"),
        "expected Windows access denial, got: {denied}"
    );
    Ok(())
}
