use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;

use nova_executor_protocol::JSONRPCErrorError;
use nova_executor_protocol::RequestId;
use nova_executor_http_client::HttpClientFactory;
use serde_json::to_value;
use std::collections::HashSet;
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
use tokio_util::task::TaskTracker;

use crate::ExecServerRuntimePaths;
use crate::client::http_client::PendingRouteAwareHttpBodyStream;
use crate::client::http_client::RouteAwareHttpClient;
use crate::client::http_client::RouteAwareHttpRequestRunner;
use crate::protocol::EnvironmentInfo;
use crate::protocol::EnvironmentStatus;
use crate::protocol::EnvironmentStatusKind;
use crate::protocol::ExecParams;
use crate::protocol::ExecResponse;
use crate::protocol::FsCanonicalizeParams;
use crate::protocol::FsCanonicalizeResponse;
use crate::protocol::FsCloseParams;
use crate::protocol::FsCloseResponse;
use crate::protocol::FsCopyParams;
use crate::protocol::FsCopyResponse;
use crate::protocol::FsCreateDirectoryParams;
use crate::protocol::FsCreateDirectoryResponse;
use crate::protocol::FsGetMetadataParams;
use crate::protocol::FsGetMetadataResponse;
use crate::protocol::FsOpenParams;
use crate::protocol::FsOpenResponse;
use crate::protocol::FsReadBlockParams;
use crate::protocol::FsReadBlockResponse;
use crate::protocol::FsReadDirectoryParams;
use crate::protocol::FsReadDirectoryResponse;
use crate::protocol::FsReadFileParams;
use crate::protocol::FsReadFileResponse;
use crate::protocol::FsRemoveParams;
use crate::protocol::FsRemoveResponse;
use crate::protocol::FsWalkParams;
use crate::protocol::FsWalkResponse;
use crate::protocol::FsWriteFileParams;
use crate::protocol::FsWriteFileResponse;
use crate::protocol::HttpRequestParams;
use crate::protocol::InitializeParams;
use crate::protocol::InitializeResponse;
use crate::protocol::ReadParams;
use crate::protocol::ReadResponse;
use crate::protocol::SignalParams;
use crate::protocol::SignalResponse;
use crate::protocol::TerminateParams;
use crate::protocol::TerminateResponse;
use crate::protocol::WriteParams;
use crate::protocol::WriteResponse;
use crate::rpc::RpcNotificationSender;
use crate::rpc::internal_error;
use crate::rpc::invalid_params;
use crate::rpc::invalid_request;
use crate::server::file_system_handler::FileSystemHandler;
use crate::server::session_registry::SessionHandle;
use crate::server::session_registry::SessionRegistry;

/// `http/request` 出网白名单环境变量（逗号分隔域名后缀；
/// 未设置或为空 = 全放行；设置后仅白名单域名及其子域可访问）。
pub const NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS_ENV_VAR: &str = "NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS";

/// `http/request` 出网门控：executor 代发 HTTP 的域名白名单。
///
/// 门控在 RPC 边界裁决（每次 `http/request` 调用前），与沙箱内进程的网络
/// 管控（managed network sandbox）是两个独立层。
#[derive(Debug, Clone, Default)]
pub(crate) struct HttpEgressPolicy {
    allowed_domains: Vec<String>,
}

impl HttpEgressPolicy {
    fn from_env() -> Self {
        let allowed_domains = std::env::var(NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS_ENV_VAR)
            .unwrap_or_default()
            .split(',')
            .map(|domain| domain.trim().to_ascii_lowercase())
            .filter(|domain| !domain.is_empty())
            .collect();
        Self { allowed_domains }
    }

    /// 空白名单 = 全放行（向后兼容）；非空 = 域名精确或后缀匹配。
    fn allows(&self, url: &str) -> bool {
        if self.allowed_domains.is_empty() {
            return true;
        }
        let host = url
            .parse::<http::Uri>()
            .ok()
            .and_then(|uri| uri.host().map(|host| host.to_ascii_lowercase()));
        let Some(host) = host else {
            return false;
        };
        self.allowed_domains
            .iter()
            .any(|domain| host == *domain || host.ends_with(&format!(".{domain}")))
    }
}

pub(crate) struct ExecServerHandler {
    session_registry: Arc<SessionRegistry>,
    notifications: RpcNotificationSender,
    session: StdMutex<Option<SessionHandle>>,
    active_body_stream_ids: Mutex<HashSet<String>>,
    background_task_shutdown: CancellationToken,
    background_tasks: TaskTracker,
    file_system: FileSystemHandler,
    runtime_paths: ExecServerRuntimePaths,
    http_client: RouteAwareHttpClient,
    http_egress_policy: HttpEgressPolicy,
    initialize_requested: AtomicBool,
    initialized: AtomicBool,
}

impl ExecServerHandler {
    pub(crate) fn new(
        session_registry: Arc<SessionRegistry>,
        notifications: RpcNotificationSender,
        runtime_paths: ExecServerRuntimePaths,
        http_client_factory: HttpClientFactory,
    ) -> Self {
        Self {
            session_registry,
            notifications: notifications.clone(),
            session: StdMutex::new(None),
            active_body_stream_ids: Mutex::new(HashSet::new()),
            background_task_shutdown: CancellationToken::new(),
            background_tasks: TaskTracker::new(),
            file_system: FileSystemHandler::new(runtime_paths.clone())
                .with_notifications(notifications),
            runtime_paths,
            http_client: RouteAwareHttpClient::new(http_client_factory),
            http_egress_policy: HttpEgressPolicy::from_env(),
            initialize_requested: AtomicBool::new(false),
            initialized: AtomicBool::new(false),
        }
    }

    pub(crate) async fn shutdown(&self) {
        self.background_task_shutdown.cancel();
        self.background_tasks.close();
        self.background_tasks.wait().await;
        self.file_system.shutdown().await;
        if let Some(session) = self.session() {
            session.detach().await;
        }
    }

    pub(crate) fn is_session_attached(&self) -> bool {
        self.session()
            .is_none_or(|session| session.is_session_attached())
    }

    pub(crate) async fn initialize(
        &self,
        params: InitializeParams,
    ) -> Result<InitializeResponse, JSONRPCErrorError> {
        if self.initialize_requested.swap(true, Ordering::SeqCst) {
            return Err(invalid_request(
                "initialize may only be sent once per connection".to_string(),
            ));
        }

        let session = match self
            .session_registry
            .attach(
                params.resume_session_id.clone(),
                self.notifications.clone(),
                self.runtime_paths.clone(),
            )
            .await
        {
            Ok(session) => session,
            Err(error) => {
                self.initialize_requested.store(false, Ordering::SeqCst);
                return Err(error);
            }
        };
        let session_id = session.session_id().to_string();
        tracing::debug!(
            session_id,
            connection_id = %session.connection_id(),
            "exec-server session attached"
        );
        *self
            .session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(session);
        Ok(InitializeResponse {
            session_id,
            protocol_version: crate::protocol::PROTOCOL_VERSION.to_string(),
        })
    }

    pub(crate) fn initialized(&self) -> Result<(), String> {
        if !self.initialize_requested.load(Ordering::SeqCst) {
            return Err("received `initialized` notification before `initialize`".into());
        }
        self.require_session_attached()
            .map_err(|error| error.message)?;
        self.initialized.store(true, Ordering::SeqCst);
        Ok(())
    }

    pub(crate) async fn exec(&self, params: ExecParams) -> Result<ExecResponse, JSONRPCErrorError> {
        let session = self.require_initialized_for("exec")?;
        session.process().exec(params).await
    }

    pub(crate) fn environment_info(&self) -> Result<EnvironmentInfo, JSONRPCErrorError> {
        self.require_initialized_for("environment info")?;
        Ok(EnvironmentInfo::local())
    }

    pub(crate) async fn environment_status(&self) -> Result<EnvironmentStatus, JSONRPCErrorError> {
        self.require_initialized_for("environment status")?;
        Ok(EnvironmentStatus {
            status: EnvironmentStatusKind::Ready,
        })
    }

    pub(crate) async fn exec_read(
        &self,
        params: ReadParams,
    ) -> Result<ReadResponse, JSONRPCErrorError> {
        let session = self.require_initialized_for("exec")?;
        let response = session.process().exec_read(params).await?;
        self.require_session_attached()?;
        Ok(response)
    }

    pub(crate) async fn exec_write(
        &self,
        params: WriteParams,
    ) -> Result<WriteResponse, JSONRPCErrorError> {
        let session = self.require_initialized_for("exec")?;
        session.process().exec_write(params).await
    }

    pub(crate) async fn signal(
        &self,
        params: SignalParams,
    ) -> Result<SignalResponse, JSONRPCErrorError> {
        let session = self.require_initialized_for("exec")?;
        session.process().signal(params).await
    }

    pub(crate) async fn terminate(
        &self,
        params: TerminateParams,
    ) -> Result<TerminateResponse, JSONRPCErrorError> {
        let session = self.require_initialized_for("exec")?;
        session.process().terminate(params).await
    }

    pub(crate) async fn http_request(
        self: &Arc<Self>,
        request_id: RequestId,
        params: HttpRequestParams,
    ) -> Result<(), JSONRPCErrorError> {
        self.require_initialized_for("http")?;
        // 审计：executor 代发 HTTP 全部留痕（出网行为的单一日志点）
        tracing::info!(
            http.request_id = params.request_id,
            http.method = %params.method,
            http.url = %params.url,
            "executor http/request"
        );
        if !self.http_egress_policy.allows(&params.url) {
            return Err(invalid_request(format!(
                "http/request url is not allowed by {NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS_ENV_VAR}: {}",
                params.url
            )));
        }
        let stream_response = params.stream_response;
        let http_request_id = params.request_id.clone();
        if stream_response {
            self.reserve_http_body_stream(&http_request_id).await?;
        }
        let response = self
            .http_client
            .runner(params.redirect_policy)
            .run(params)
            .await;
        if response.is_err() && stream_response {
            self.release_http_body_stream(&http_request_id).await;
        }
        let (response, mut pending_stream) = response?;
        let result = match to_value(response) {
            Ok(result) => result,
            Err(err) => {
                if let Some(pending_stream) = pending_stream.take() {
                    self.release_http_body_stream(&pending_stream.request_id)
                        .await;
                }
                return Err(internal_error(err.to_string()));
            }
        };
        if let Err(error) = self.notifications.response(request_id, result).await {
            if let Some(pending_stream) = pending_stream.take() {
                self.release_http_body_stream(&pending_stream.request_id)
                    .await;
            }
            return Err(error);
        }
        if let Some(pending_stream) = pending_stream {
            self.start_http_body_stream(pending_stream).await;
        }
        Ok(())
    }

    pub(crate) async fn fs_read_file(
        &self,
        params: FsReadFileParams,
    ) -> Result<FsReadFileResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.read_file(params).await
    }

    pub(crate) async fn fs_open(
        &self,
        params: FsOpenParams,
    ) -> Result<FsOpenResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.open(params).await
    }

    pub(crate) async fn fs_read_block(
        &self,
        params: FsReadBlockParams,
    ) -> Result<FsReadBlockResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.read_block(params).await
    }

    pub(crate) async fn fs_read_stream(
        &self,
        params: crate::protocol::FsReadStreamParams,
    ) -> Result<crate::protocol::FsReadStreamResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.read_stream(params).await
    }

    pub(crate) async fn fs_close(
        &self,
        params: FsCloseParams,
    ) -> Result<FsCloseResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.close(params).await
    }

    pub(crate) async fn fs_write_file(
        &self,
        params: FsWriteFileParams,
    ) -> Result<FsWriteFileResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.write_file(params).await
    }

    pub(crate) async fn fs_create_directory(
        &self,
        params: FsCreateDirectoryParams,
    ) -> Result<FsCreateDirectoryResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.create_directory(params).await
    }

    pub(crate) async fn fs_get_metadata(
        &self,
        params: FsGetMetadataParams,
    ) -> Result<FsGetMetadataResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.get_metadata(params).await
    }

    pub(crate) async fn fs_canonicalize(
        &self,
        params: FsCanonicalizeParams,
    ) -> Result<FsCanonicalizeResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.canonicalize(params).await
    }

    pub(crate) async fn fs_read_directory(
        &self,
        params: FsReadDirectoryParams,
    ) -> Result<FsReadDirectoryResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.read_directory(params).await
    }

    pub(crate) async fn fs_walk(
        &self,
        params: FsWalkParams,
    ) -> Result<FsWalkResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.walk(params).await
    }

    pub(crate) async fn fs_remove(
        &self,
        params: FsRemoveParams,
    ) -> Result<FsRemoveResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.remove(params).await
    }

    pub(crate) async fn fs_copy(
        &self,
        params: FsCopyParams,
    ) -> Result<FsCopyResponse, JSONRPCErrorError> {
        self.require_initialized_for("filesystem")?;
        self.file_system.copy(params).await
    }

    fn require_initialized_for(
        &self,
        method_family: &str,
    ) -> Result<SessionHandle, JSONRPCErrorError> {
        if !self.initialize_requested.load(Ordering::SeqCst) {
            return Err(invalid_request(format!(
                "client must call initialize before using {method_family} methods"
            )));
        }
        let session = self.require_session_attached()?;
        if !self.initialized.load(Ordering::SeqCst) {
            return Err(invalid_request(format!(
                "client must send initialized before using {method_family} methods"
            )));
        }
        Ok(session)
    }

    fn require_session_attached(&self) -> Result<SessionHandle, JSONRPCErrorError> {
        let Some(session) = self.session() else {
            return Err(invalid_request(
                "client must call initialize before using methods".to_string(),
            ));
        };
        if session.is_session_attached() {
            return Ok(session);
        }

        Err(invalid_request(
            "session has been resumed by another connection".to_string(),
        ))
    }

    fn session(&self) -> Option<SessionHandle> {
        self.session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    async fn start_http_body_stream(
        self: &Arc<Self>,
        pending_stream: PendingRouteAwareHttpBodyStream,
    ) {
        let request_id = pending_stream.request_id.clone();
        if self.background_task_shutdown.is_cancelled() {
            self.release_http_body_stream(&request_id).await;
            return;
        }
        let finished_request_id = request_id.clone();
        let handler = Arc::clone(self);
        let notifications = self.notifications.clone();
        let shutdown = self.background_task_shutdown.clone();
        self.background_tasks.spawn(async move {
            tokio::select! {
                _ = shutdown.cancelled() => {}
                _ = RouteAwareHttpRequestRunner::stream_body(pending_stream, notifications) => {}
            }
            handler.release_http_body_stream(&finished_request_id).await;
        });
    }

    async fn release_http_body_stream(&self, request_id: &str) {
        let mut active_body_stream_ids = self.active_body_stream_ids.lock().await;
        active_body_stream_ids.remove(request_id);
    }

    async fn reserve_http_body_stream(&self, request_id: &str) -> Result<(), JSONRPCErrorError> {
        let mut active_body_stream_ids = self.active_body_stream_ids.lock().await;
        if active_body_stream_ids.contains(request_id) {
            return Err(invalid_params(format!(
                "http/request streamResponse requestId `{request_id}` is already active"
            )));
        }
        active_body_stream_ids.insert(request_id.to_string());
        Ok(())
    }
}

#[cfg(test)]
mod tests;
