//! 把 RPC 读取完成时间带回调用方，使计时保持在等待任务内。
//! （对位 codex 39f4277087 `rpc_timing.rs` 整文件移植；事件名/target 按
//! nova 映射改名）

use std::time::Instant;

use serde_json::Value;

use crate::rpc::RpcCallError;

pub(crate) struct RpcCompletion {
    pub(crate) result: Result<Value, RpcCallError>,
    completed_at: Instant,
}

impl RpcCompletion {
    pub(crate) fn new(result: Result<Value, RpcCallError>) -> Self {
        Self {
            result,
            completed_at: Instant::now(),
        }
    }

    pub(crate) fn record_receipt(&self, method: &str) {
        let outcome = match &self.result {
            Ok(_) => "success",
            Err(RpcCallError::Server(_)) => "error",
            Err(
                RpcCallError::Closed
                | RpcCallError::Json(_)
                | RpcCallError::TimedOut { .. }
                | RpcCallError::PendingRequestLimitExceeded { .. },
            ) => return,
        };
        tracing::event!(
            name: "nova.exec_server.response_received",
            target: "nova_exec_server_otel.trace_safe",
            tracing::Level::INFO,
            event.name = "nova.exec_server.response_received",
            rpc.method = method,
            outcome,
            reader_to_caller_ns = i64::try_from(self.completed_at.elapsed().as_nanos())
                .unwrap_or(i64::MAX),
        );
    }
}
