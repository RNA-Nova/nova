//! 调用方侧 RPC 尝试计数（按客户端协议方法名，独立于 tracing）。
//! （对位 codex 39f4277087 `client_telemetry.rs` 整文件移植）

use nova_exec_server_otel::EXEC_SERVER_CLIENT_REQUEST_COUNT_METRIC;
use nova_exec_server_otel::MetricsClient;

pub(crate) fn record_client_request(metrics: Option<&MetricsClient>, method: &str) {
    let Some(metrics) = metrics else {
        return;
    };
    // Record before local admission so failures and cancelled calls still count
    // as attempts. Notifications and responses never enter these call paths.
    if metrics
        .counter_with_description(
            EXEC_SERVER_CLIENT_REQUEST_COUNT_METRIC,
            "Total number of client-side exec-server RPC attempts, including local failures.",
            /*inc*/ 1,
            &[("method", method)],
        )
        .is_err()
    {
        tracing::warn!("failed to emit exec-server client request counter");
    }
}
