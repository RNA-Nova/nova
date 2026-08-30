//! Linux 沙箱 helper 入口（移植自 codex-rs/linux-sandbox）。
//!
//! 在 Linux 上，`codex-linux-sandbox` helper 施加两层限制：
//! - 进程内限制（`no_new_privs` + seccomp），以及
//! - 基于 bubblewrap 的文件系统隔离。
//!
//! 二进制名保持 `codex-linux-sandbox`（argv0 互操作契约，见
//! `nova_executor_sandboxing::landlock::NOVA_EXECUTOR_LINUX_SANDBOX_ARG0`），与
//! executor-windows-sandbox 保留 helper 原名的先例一致。全部实现模块只面向
//! Linux，经 `#[cfg(target_os = "linux")]` 门控；其他平台编译为 stub，
//! `run_main()` 直接 panic。
#[cfg(target_os = "linux")]
mod bazel_bwrap;
#[cfg(target_os = "linux")]
mod bundled_bwrap;
#[cfg(target_os = "linux")]
mod bwrap;
#[cfg(target_os = "linux")]
mod exec_util;
#[cfg(target_os = "linux")]
mod fd_mount;
#[cfg(target_os = "linux")]
mod landlock;
#[cfg(target_os = "linux")]
mod launcher;
#[cfg(target_os = "linux")]
mod linux_run_main;
#[cfg(target_os = "linux")]
mod proxy_lifecycle;
#[cfg(target_os = "linux")]
mod proxy_routing;

/// Exit status returned when bundled bubblewrap fails digest verification.
#[cfg(target_os = "linux")]
pub const BUNDLED_BWRAP_DIGEST_VERIFICATION_FAILURE_EXIT_CODE: i32 = 8;

#[cfg(target_os = "linux")]
pub fn run_main() -> ! {
    linux_run_main::run_main();
}

#[cfg(not(target_os = "linux"))]
pub fn run_main() -> ! {
    panic!("codex-linux-sandbox is only supported on Linux");
}
