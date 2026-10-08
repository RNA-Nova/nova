//! Removes one authenticated owner's sandbox resources using prepared native cleanup.
//! Owner impersonation, directory pins, and registration-aware cleanup order are preserved.

use std::io;
use std::path::Path;
use std::sync::atomic::AtomicBool;

use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use nova_exec_server_windows_sandbox::PreparedWindowsSandboxCleanup;
use nova_exec_server_windows_sandbox::resolve_sid;
use nova_exec_server_windows_sandbox::revoke_ace;

use super::UserInstallation;
use super::with_owner_impersonation;
use crate::installation_record::InstallationRecord;
use crate::service::EVENT_CLEANUP_DETAIL;
use crate::service::log_error;
use crate::service::log_information;

pub(super) fn clean_up(
    installation: &mut UserInstallation,
    prepared: &PreparedWindowsSandboxCleanup,
    runtime: Option<&InstallationRecord>,
    uninstalling: &AtomicBool,
) -> Result<()> {
    crate::service::log_information(
        crate::service::EVENT_CLEANUP_STARTED,
        "sandbox uninstall cleanup started",
    );
    let sandbox_home = installation.sandbox_home.clone();
    // Remove exact grants from the locked cleanup record before native account deletion.
    if let Some(record) = runtime {
        log_cleanup("removing registered runtime metadata");
        crate::registered_runtime::remove_metadata(installation.user_token.0, record)?;
    }
    log_cleanup("removing native sandbox resources");
    let mut prune_sandbox_home = false;
    // Once owner-scoped deletion releases the home, retries must not traverse it as SYSTEM.
    let sandbox_home = sandbox_home
        .as_deref()
        .filter(|_| installation.directory_guard.is_some());
    let result = prepared.finish(sandbox_home, log_cleanup, || {
        if let Some(record) = runtime
            && !super::registered::owner_allows_cleanup(uninstalling, record)?
        {
            // Only the old sandbox resources are repaired on reinstall. Never remove
            // the reinstalled app's desktop-created home or runtime cache.
            log_cleanup("skipping desktop directories: owner reinstalled the app");
            return Ok(());
        }
        let Some(desktop) = &installation.record.desktop_installation else {
            log_cleanup("skipping desktop directories: no desktop installation record");
            return Ok(());
        };
        // The marker is user-writable. It must never authorize deletion as LocalSystem.
        with_owner_impersonation(installation.user_token.0, || {
            let mut errors = Vec::new();
            let mut record_result = |operation: &str, result: io::Result<()>| match result {
                Ok(()) => log_cleanup(&format!("{operation}: completed")),
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    log_cleanup(&format!("{operation}: skipped, not found"));
                }
                Err(error) => {
                    let message = format!("{operation}: failed, {error}");
                    log_error(EVENT_CLEANUP_DETAIL, &message);
                    errors.push(message);
                }
            };
            if let Some(home) = &sandbox_home {
                if desktop.created_sandbox_home {
                    // Release the home itself so it can be deleted; keep its ancestors pinned.
                    if installation.directory_guard.take().is_some() {
                        installation.directory_handles.pop();
                    }
                    record_result(
                        "remove desktop-created nova home",
                        std::fs::remove_dir_all(home),
                    );
                } else {
                    prune_sandbox_home = true;
                    log_cleanup("skipping recursive nova home removal: existing CLI home");
                    // Preserve CLI data without leaving inherited permissions for the deleted group.
                    record_result(
                        "remove nova home sandbox permissions",
                        resolve_sid("NovaSandboxUsers")
                            .and_then(|mut sid| unsafe {
                                revoke_ace(home, sid.as_mut_ptr().cast())
                            })
                            .map_err(io::Error::other),
                    );
                }
            } else {
                log_cleanup("skipping nova home: no pinned home");
            }
            // 运行时缓存有两个候选根（对位 setup_runtime_bin.rs 的 runtime_paths）：
            // 托管主运行时在 profile\.cache\nova-exec-server-runtimes，其余运行时根在
            // LocalAppData\Nova\ExecServer\runtimes（LocalAppData 由 profile 推导，
            // 与安装侧的 USERPROFILE 回退一致）。缓存可能在 provisioning 之后创建，
            // 仅在清理时按需 pin。
            let local_runtime_root = desktop.cache_home.parent().map(|profile| {
                profile
                    .join("AppData")
                    .join("Local")
                    .join("Nova")
                    .join("ExecServer")
            });
            if local_runtime_root.is_none() {
                log_cleanup("skipping local runtime cache: cache home has no profile parent");
            }
            let mut runtime_roots =
                vec![(desktop.cache_home.as_path(), "nova-exec-server-runtimes", "cache home")];
            if let Some(root) = local_runtime_root.as_deref() {
                runtime_roots.push((root, "runtimes", "runtime root"));
            }
            // pin 失败留到循环结束后合并：循环内直写 errors 会与 record_result
            // 的 &mut 借用冲突（E0499）。
            let mut pin_errors = Vec::new();
            for (root, leaf, target) in runtime_roots {
                if !root.is_dir() {
                    log_cleanup(&format!(
                        "skipping runtime cache {}: no accessible directory",
                        root.display()
                    ));
                    continue;
                }
                let mut cache_directory_handles = Vec::new();
                match crate::ipc::pin_existing_ancestors(root, &mut cache_directory_handles) {
                    Ok(()) => {
                        record_result(
                            "remove nova runtime cache",
                            std::fs::remove_dir_all(root.join(leaf)),
                        );
                        // Release only the cache root; its ancestors must remain pinned.
                        cache_directory_handles.pop();
                        remove_empty_directory(root, target);
                    }
                    Err(error) => {
                        log_error(
                            EVENT_CLEANUP_DETAIL,
                            &format!(
                                "skipping runtime cache {}: could not pin cache root, {error:#}",
                                root.display()
                            ),
                        );
                        pin_errors.push(error.to_string());
                    }
                }
            }
            errors.extend(pin_errors);
            ensure!(
                errors.is_empty(),
                "remove desktop directories: {}",
                errors.join("; ")
            );
            Ok(())
        })
    });
    result.context("remove packaged Windows sandbox resources")?;
    if prune_sandbox_home && let Some(home) = &sandbox_home {
        // Keep the home pinned through native retries. Empty-root pruning is best effort
        // so a failure cannot restart native cleanup through an unpinned home.
        if let Err(error) = with_owner_impersonation(installation.user_token.0, || {
            if installation.directory_guard.take().is_some() {
                installation.directory_handles.pop();
            }
            remove_empty_directory(home, "nova home");
            Ok(())
        }) {
            log_error(
                EVENT_CLEANUP_DETAIL,
                &format!("remove empty nova home: failed, {error:#}"),
            );
        }
    }
    crate::service::log_information(
        if runtime.is_some() {
            EVENT_CLEANUP_DETAIL
        } else {
            crate::service::EVENT_CLEANUP_FINISHED
        },
        if runtime.is_some() {
            "native sandbox cleanup finished; registered runtime cleanup pending"
        } else {
            "sandbox uninstall cleanup finished"
        },
    );
    Ok(())
}

fn remove_empty_directory(path: &Path, target: &str) {
    match std::fs::remove_dir(path) {
        Ok(()) => log_cleanup(&format!("removed empty {target}")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            log_cleanup(&format!("skipping empty {target} removal: not found"));
        }
        Err(error) if error.kind() == io::ErrorKind::DirectoryNotEmpty => {
            log_cleanup(&format!("preserving {target}: directory is not empty"));
        }
        Err(error) => {
            log_error(
                EVENT_CLEANUP_DETAIL,
                &format!("remove empty {target}: failed, {error}"),
            );
        }
    }
}

fn log_cleanup(message: &str) {
    log_information(EVENT_CLEANUP_DETAIL, message);
}
