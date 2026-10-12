//! 特权 daemon socket 目录的强制路径与网络保护。
//!
//! 对位 codex 39f4277087：`sandboxing/src/seatbelt_daemon.rs`（protection_policy）
//! + `uds/src/daemon_directory.rs`（目录函数）。nova 无 uds 独立 crate（那是
//! app-server 异步传输层，不在镜像面），目录函数并入本模块。
//!
//! 本文件挂载两处（内容相同）：`seatbelt::daemon`（macOS 沙箱保护策略用）与
//! crate 根 `seatbelt_daemon`（unix 通用，经 lib.rs `pub use` 导出目录函数给
//! exec-server 的 shell snapshot 传输物化用）。根实例下 macOS 专属的
//! `protection_policy` 无人消费，故带 `#[allow(dead_code)]`。
//!
//! 预封语义：daemon 目录路径固定且不依赖 HOME/TMPDIR/配置——沙箱档案在
//! daemon 存在之前就拒绝一切读写/连接/祖先改名，防止沙箱内进程在公认路径
//! 抢占埋点（squatting），未来 daemon 形态落地当天保护即自动生效。

#[cfg(target_os = "macos")]
use crate::seatbelt::SeatbeltPreparationError;
use std::fs;
use std::io;
use std::os::unix::fs::DirBuilderExt;
use std::os::unix::fs::MetadataExt;
#[cfg(target_os = "macos")]
use std::path::Path;
use std::path::PathBuf;

/// Returns the fixed executor-local directory that every sandbox must hide.
///
/// （对位 codex：路径为 `/tmp/nova-daemon-<uid>`——命名适配，语义相同；
/// 解析 /tmp → /private/tmp 的 macOS 系统别名）
pub fn shared_daemon_socket_directory() -> io::Result<PathBuf> {
    let temporary_root = fs::canonicalize("/tmp")?;
    let uid = unsafe { libc::geteuid() };
    Ok(temporary_root.join(format!("nova-daemon-{uid}")))
}

/// Creates the reserved directory, rejecting symlinks and unsafe existing owners or modes.
/// （对位 codex `prepare_shared_daemon_socket_directory`）
#[allow(dead_code)] // seatbelt::daemon 实例的副本仅测试在用；crate 根实例经 pub use 导出
pub fn prepare_shared_daemon_socket_directory() -> io::Result<PathBuf> {
    let directory = shared_daemon_socket_directory()?;
    match fs::DirBuilder::new().mode(0o700).create(&directory) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error),
    }
    let metadata = fs::symlink_metadata(&directory)?;
    if !metadata.is_dir()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.mode() & 0o777 != 0o700
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "app-server socket directory must be a user-owned directory with mode 0700",
        ));
    }
    Ok(directory)
}

/// Mandatory pathname and network protections for privileged daemon sockets.
/// Emit these after all grants, including implicit platform access.
/// （对位 codex `seatbelt_daemon.rs::protection_policy` 逐行）
#[cfg(target_os = "macos")]
#[allow(dead_code)] // crate 根实例的副本无人消费（消费方走 seatbelt::daemon）
pub(super) fn protection_policy(directory: &Path) -> Result<String, SeatbeltPreparationError> {
    let quoted = serde_json::to_string(&directory.to_string_lossy())
        .map_err(|error| SeatbeltPreparationError::FileSystem(error.to_string()))?;
    let mut rules = vec![format!(
        "(deny file-read* file-write* (literal {quoted}) (subpath {quoted}))\n\
         (deny network-outbound (remote unix-socket (subpath {quoted})))"
    )];
    // Moving an ancestor would relocate the entire protected subtree beyond
    // both pathname rules. Only unlink is denied; sibling writes still work.
    for ancestor in directory.ancestors().skip(/*n*/ 1) {
        let quoted = serde_json::to_string(&ancestor.to_string_lossy())
            .map_err(|error| SeatbeltPreparationError::FileSystem(error.to_string()))?;
        rules.push(format!(
            "(deny file-write-unlink (require-all (vnode-type DIRECTORY) (literal {quoted})))"
        ));
    }
    Ok(rules.join("\n"))
}
