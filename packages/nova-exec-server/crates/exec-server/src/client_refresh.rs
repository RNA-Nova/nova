//! 计划内更换后的显式连接刷新（对位 codex `client_refresh.rs`）。
//!
//! 普通恢复（recovery）在瞬时断线后尝试恢复**同一个** executor 会话；更换
//! 场景需要全新会话，且不等旧恢复放弃。调用方负责排序：先登记新执行体，
//! 再刷新。执行体身份不外泄到本层之上。
//!
//! 流程（codex 骨架：新鲜查找 → 复用或退役会话 → 需要时重连 → 探活）中，
//! "查找"一步对 codex 是 Noise 注册表 bundle 获取；nova 的端点是静态传输
//! 参数（WS URL / stdio 命令），无注册表可查，该步省略——刷新语义不变：
//! 作废旧启动/在途重连 → 退役旧会话 → 全新连接（不 resume）→ 探活。
//!
//! 两个竞态决定这里的同步设计（codex 原注）：查找期间安装的新 client 会让
//! 查找过期，故刷新后复检；被刷新取消的连接尝试绝不允许迟到的握手再安装——
//! 取消与安装在同一 `current_client` 锁下同步。`refresh_lock` 只串行化显式
//! 刷新，普通连接与恢复工作可并发进行。

use std::sync::Arc;
use std::sync::Mutex as StdMutex;

use futures::future::BoxFuture;
use tokio::sync::Mutex;
use tokio::sync::OnceCell;
use tokio::sync::watch;
use tokio_util::sync::CancellationToken;

use super::ConnectionResult;
use super::ConnectionStatus;
use super::ExecServerClient;
use super::ExecServerError;
use super::Inner;
use super::LazyRemoteExecServerClient;
use crate::environment::EnvironmentConnectionState;
use super::fail_all_in_flight_work;

/// 共享的启动/重连结果 + 刷新作废旧工作的取消令牌；可选传输参数供刷新
/// 携带新端点（静态端点场景为 None，回退 `self.transport_params`）。
#[derive(Default)]
pub(super) struct ConnectionAttempt {
    pub(super) result: OnceCell<ConnectionResult>,
    pub(super) cancelled: CancellationToken,
    pub(super) transport: Option<crate::client_api::ExecServerTransportParams>,
}

impl LazyRemoteExecServerClient {
    #[expect(
        clippy::await_holding_invalid_type,
        reason = "serialize explicit refreshes, not ordinary connection or recovery attempts"
    )]
    pub async fn refresh_connection(&self) -> Result<(), ExecServerError> {
        let _refresh = self.refresh_lock.lock().await;
        let (previous, attempt) = loop {
            let observed = self.cached_client();
            let mut reconnect = self
                .reconnect
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let current = self
                .current_client
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            // An ordinary connection may have finished during the wait. Re-read rather
            // than retire a newer client using a superseded observation.
            if !match (&observed, &*current) {
                (Some(observed), Some(current)) => Arc::ptr_eq(&observed.inner, &current.inner),
                (None, None) => true,
                _ => false,
            } {
                continue;
            }
            // Cancellation and connection installation use the same lock. A late
            // handshake cannot install a client after its attempt has been superseded.
            self.startup.cancelled.cancel();
            if let Some(attempt) = reconnect.as_ref() {
                attempt.cancelled.cancel();
            }
            self.environment_connection_state_tx
                .send_replace(EnvironmentConnectionState::Disconnected);
            let attempt = Arc::new(ConnectionAttempt::default());
            *reconnect = Some(Arc::clone(&attempt));
            break (current.clone(), attempt);
        };
        if let Some(previous) = previous {
            previous.inner.retire().await;
        }
        let result = attempt
            .result
            .get_or_init(|| self.connect_once(&attempt))
            .await
            .clone();
        let mut reconnect = self
            .reconnect
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if reconnect
            .as_ref()
            .is_some_and(|current| Arc::ptr_eq(current, &attempt))
        {
            *reconnect = None;
        }
        let client = result.map_err(ExecServerError::ConnectionAttempt)?;
        // Metadata may be cached; readiness requires a live, non-recovering probe.
        client.environment_status().await.map(drop)
    }

    #[tracing::instrument(name = "nova.exec_server.remote.connect", skip_all)]
    pub(super) fn connect_once<'a>(
        &'a self,
        attempt: &'a ConnectionAttempt,
    ) -> BoxFuture<'a, ConnectionResult> {
        // Keep the transport future out of every caller's async layout, including
        // the CLI entry point, which otherwise exceeds rustc's query-depth limit.
        Box::pin(async move {
            let transport = attempt
                .transport
                .as_ref()
                .or(Some(&self.transport_params))
                .ok_or_else(|| {
                    Arc::new(ExecServerError::Protocol(
                        "missing transport params for lazy exec-server connection".to_string(),
                    ))
                })?;
            let client = tokio::select! {
                biased;
                _ = attempt.cancelled.cancelled() => return Err(Arc::new(ExecServerError::Disconnected("connection attempt was superseded".to_string()))),
                result = ExecServerClient::connect_for_transport(transport.clone(), self.http_client_factory.clone()) => result.map_err(Arc::new)?,
            };
            // Cancellation can race with a completed handshake. Recheck before attaching
            // state or installing the client, under the same lock used by refresh.
            {
                let mut current = self
                    .current_client
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                if !attempt.cancelled.is_cancelled() {
                    client.attach_environment_connection_state(
                        self.environment_connection_state_tx.clone(),
                    );
                    *current = Some(client.clone());
                    return Ok(client);
                }
            }
            client.inner.retire().await;
            Err(Arc::new(ExecServerError::Disconnected(
                "connection attempt was superseded".to_string(),
            )))
        })
    }
}

impl Inner {
    pub(super) async fn retire(self: &Arc<Self>) {
        let message = "exec-server executor was replaced".to_string();
        let rpc_client = {
            let mut connection = self
                .connection
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            // Detach before a later transport completion can publish stale state.
            connection.environment_connection_state_tx =
                watch::channel(EnvironmentConnectionState::Disconnected).0;
            let rpc_client = match &connection.status {
                ConnectionStatus::Connected(client) => Some(Arc::clone(client)),
                ConnectionStatus::Recovering | ConnectionStatus::Failed(_) => None,
            };
            self.retired.cancel();
            connection.set_status(ConnectionStatus::Failed(message.clone()));
            rpc_client
        };
        self.connection_changed.send_replace(());
        // Drain pending RPCs before stream cleanup, which may wait for other work.
        if let Some(rpc_client) = rpc_client {
            rpc_client.close_transport().await;
        }
        fail_all_in_flight_work(self, message).await;
    }
}
