//! Native Windows MXC capability discovery and availability metrics.
//! Probing is cached upstream; reporting happens at most once per process.

use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;

static AVAILABILITY_RECORDED: AtomicBool = AtomicBool::new(false);

pub(super) fn record_availability_once() {
    // Record the SDK's cached PSEC API-set check. Creation and request-specific
    // capability checks happen when launching the MXC command.
    // （对位 codex 1c7c43cd85：由 create/close 探针改为 SDK 缓存的 API-set 检查）
    let available = nova_exec_server_mxc_sandbox::is_available();
    if let Some(metrics) = nova_exec_server_otel::global()
        && !AVAILABILITY_RECORDED.swap(true, Ordering::Relaxed)
    {
        let _ = metrics.counter(
            "nova.windows_mxc.available",
            /*inc*/ 1,
            &[("available", if available { "true" } else { "false" })],
        );
    }
}
