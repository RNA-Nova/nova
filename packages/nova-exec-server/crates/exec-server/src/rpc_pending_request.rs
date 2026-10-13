//! 调用完成或被取消时移除 client RPC 登记项；pending 表仅在同步簿记时加锁（含 Drop）。
//! 对位 codex ed9e5a26a8。

use std::collections::HashMap;
use std::sync::Mutex;

use nova_exec_server_protocol::RequestId;

use super::PendingRequest;

pub(super) struct PendingRequestGuard<'a> {
    pub(super) pending: &'a Mutex<HashMap<RequestId, PendingRequest>>,
    pub(super) request_id: RequestId,
}

impl Drop for PendingRequestGuard<'_> {
    fn drop(&mut self) {
        self.pending
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&self.request_id);
    }
}
