//! 具体执行/文件系统环境（对位 codex `environment.rs`）。
//!
//! Noise 供给/能力根两件随挂账剔除：供给状态机
//! （`report_environment_provisioning_status`/`materialize_pending_*`）与
//! capability roots（`EnvironmentReadyInfo`/`selected_capability_roots`/
//! `discover_capability_roots`）不进入本文件——远程能力发现归 nova 资源体系
//! 远程化时按 nova 语义重做。

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use std::sync::RwLock;

use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use tokio::sync::watch;
use tokio_util::sync::CancellationToken;
use tokio_util::task::AbortOnDropHandle;
use tracing::Instrument as _;
use tracing::instrument::WithSubscriber as _;

use crate::DefaultEnvironmentProvider;
use crate::EnvironmentProvider;
use crate::EnvironmentProviderFuture;
use crate::ExecBackend;
use crate::ExecServerError;
use crate::ExecServerRuntimePaths;
use crate::ExecutorFileSystem;
use crate::HttpClient;
use crate::LOCAL_FS;
use crate::LocalFileSystem;
use crate::LocalProcess;
use crate::PreparedEnvironmentManager;
use crate::PreparedEnvironmentSource;
use crate::client::LazyRemoteExecServerClient;
use crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT;
use crate::client_api::ExecServerTransportParams;
use crate::environment_config::read_environment_config;
use crate::environment_provider::EnvironmentDefault;
use crate::environment_provider::EnvironmentProviderSnapshot;
use crate::environment_provider::normalize_exec_server_url;
use crate::environment_toml::environment_provider_from_config_home;
use crate::remote_file_system::RemoteFileSystem;
use crate::remote_process::RemoteProcess;
use crate::RouteAwareHttpClient;
use crate::EnvironmentConfigReadParams;
use crate::EnvironmentConfigReadResponse;
use crate::EnvironmentInfo;

/// The current connection state for one concrete environment.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EnvironmentConnectionState {
    /// An initialized exec-server connection is available.
    Connected,
    /// No initialized exec-server connection is currently available.
    Disconnected,
}

/// Owns the execution/filesystem environments available to the Nova runtime.
///
/// `EnvironmentManager` is a shared registry for concrete environments. Its
/// default constructor preserves the legacy `NOVA_EXEC_SERVER_URL` behavior
/// while configured construction accepts a provider-supplied snapshot.
///
/// Setting `NOVA_EXEC_SERVER_URL=none` disables environment access by leaving
/// the default environment unset and omitting the local environment. Callers
/// use `default_environment().is_some()` as the signal for model-facing
/// shell/filesystem tool availability.
///
/// Remote environments begin connecting when added to the manager. Filesystem
/// and execution backends share the resulting startup and reconnect as needed.
#[derive(Debug)]
pub struct EnvironmentManager {
    default_environment: Option<String>,
    pub(super) environments: RwLock<HashMap<String, Arc<Environment>>>,
    local_environment: Option<Arc<Environment>>,
    local_runtime_paths: Option<ExecServerRuntimePaths>,
    http_client_factory: HttpClientFactory,
}

pub const LOCAL_ENVIRONMENT_ID: &str = "local";
pub const REMOTE_ENVIRONMENT_ID: &str = "remote";

/// `NOVA_EXEC_SERVER_URL` 环境变量：单远程环境的简写通道（对位 codex
/// `CODEX_EXEC_SERVER_URL`）——`none` 禁用环境访问。
pub const NOVA_EXEC_SERVER_URL_ENV_VAR: &str = "NOVA_EXEC_SERVER_URL";

/// Non-mutating connection status observed by an environment owner.
///
/// Computing this status never starts, waits for, or reconnects an exec-server
/// transport. Already-ready remote environments may receive a fail-fast probe
/// over their existing connection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EnvironmentObservedStatus {
    /// A local environment, or a remote environment whose existing connection answered a probe.
    Ready,
    /// The configured environment has no ready connection and no observed connection failure.
    ///
    /// This includes lazy transports that have never been started and initial startup that has
    /// not finished. Computing status does not start the environment or wait for startup.
    Pending,
    /// A connection attempt, prior connection, or fail-fast status probe observed a failure.
    ///
    /// This does not promise that the failure is terminal: later normal environment use may
    /// recover the connection. Computing status itself does not trigger recovery.
    Disconnected {
        /// Human-readable reason recorded by the failed connection attempt or probe.
        error: String,
    },
}

impl EnvironmentManager {
    /// Builds a test-only manager without configured sandbox helper paths.
    pub fn default_for_tests() -> Self {
        Self {
            default_environment: Some(LOCAL_ENVIRONMENT_ID.to_string()),
            environments: RwLock::new(HashMap::from([(
                LOCAL_ENVIRONMENT_ID.to_string(),
                Arc::new(Environment::default_for_tests()),
            )])),
            local_environment: Some(Arc::new(Environment::default_for_tests())),
            local_runtime_paths: None,
            // Test-only construction has no application config from which to resolve proxy policy.
            http_client_factory: HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        }
    }

    /// Builds a manager with no configured execution environments.
    pub fn without_environments(http_client_factory: HttpClientFactory) -> Self {
        Self {
            default_environment: None,
            environments: RwLock::new(HashMap::new()),
            local_environment: None,
            local_runtime_paths: None,
            http_client_factory,
        }
    }

    /// Builds a test-only manager from a raw exec-server URL value.
    pub async fn create_for_tests(
        exec_server_url: Option<String>,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
    ) -> Self {
        let provider = DefaultEnvironmentProvider::new(exec_server_url);
        match Self::from_snapshot(
            provider.snapshot_inner(),
            local_runtime_paths,
            // Test-only construction has no application config from which to resolve proxy policy.
            HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        ) {
            Ok(manager) => manager,
            Err(err) => panic!("default provider should create valid environments: {err}"),
        }
    }

    /// Discovers configured environments without starting remote connections.
    ///
    /// If `<config home>/config.toml` carries environment keys, they define the
    /// configured environments. Otherwise this preserves the
    /// `NOVA_EXEC_SERVER_URL` behavior.
    pub async fn prepare_from_config_home(
        config_home: impl AsRef<std::path::Path>,
    ) -> Result<PreparedEnvironmentManager, ExecServerError> {
        let provider = environment_provider_from_config_home(config_home.as_ref())?;
        let source = PreparedEnvironmentSource::Snapshot(provider.snapshot().await?);
        Ok(PreparedEnvironmentManager { source })
    }

    /// Builds a manager from the exec-server config home with an explicit outbound HTTP policy.
    pub async fn from_config_home(
        config_home: impl AsRef<std::path::Path>,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        Self::prepare_from_config_home(config_home)
            .await?
            .build(local_runtime_paths, http_client_factory)
    }

    /// Discovers environment-variable environments without starting connections.
    pub async fn prepare_from_env() -> Result<PreparedEnvironmentManager, ExecServerError> {
        let provider = DefaultEnvironmentProvider::from_env();
        let source = PreparedEnvironmentSource::Snapshot(provider.snapshot().await?);
        Ok(PreparedEnvironmentManager { source })
    }

    /// Builds a manager from environment variables with an explicit outbound HTTP policy.
    pub async fn from_env(
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        Self::prepare_from_env()
            .await?
            .build(local_runtime_paths, http_client_factory)
    }

    /// Builds a test-only manager that keeps the provider default while also
    /// allowing tests to select the local environment explicitly.
    pub async fn create_for_tests_with_local(
        exec_server_url: Option<String>,
        local_runtime_paths: ExecServerRuntimePaths,
    ) -> Self {
        let mut snapshot = DefaultEnvironmentProvider::new(exec_server_url).snapshot_inner();
        snapshot.include_local = true;
        match Self::from_snapshot(
            snapshot,
            Some(local_runtime_paths),
            // Test-only construction has no application config from which to resolve proxy policy.
            HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        ) {
            Ok(manager) => manager,
            Err(err) => panic!("test provider with local should create valid environments: {err}"),
        }
    }

    pub(crate) fn from_snapshot(
        snapshot: EnvironmentProviderSnapshot,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        let EnvironmentProviderSnapshot {
            environments,
            default,
            include_local,
        } = snapshot;
        let mut environment_map =
            HashMap::with_capacity(environments.len() + usize::from(include_local));
        let local_environment = if include_local {
            let local_runtime_paths = local_runtime_paths.clone().ok_or_else(|| {
                ExecServerError::Protocol(
                    "local environment requires configured runtime paths".to_string(),
                )
            })?;
            let local_environment = Arc::new(Environment::local(
                local_runtime_paths,
                http_client_factory.clone(),
            ));
            environment_map.insert(
                LOCAL_ENVIRONMENT_ID.to_string(),
                Arc::clone(&local_environment),
            );
            Some(local_environment)
        } else {
            None
        };
        for (id, transport) in environments {
            if id.is_empty() {
                return Err(ExecServerError::Protocol(
                    "environment id cannot be empty".to_string(),
                ));
            }
            if id == LOCAL_ENVIRONMENT_ID {
                return Err(ExecServerError::Protocol(format!(
                    "environment id `{LOCAL_ENVIRONMENT_ID}` is reserved for EnvironmentManager"
                )));
            }
            let environment = Environment::remote_with_transport(
                transport,
                /*local_runtime_paths*/ None,
                http_client_factory.clone(),
            );
            if environment_map
                .insert(id.clone(), Arc::new(environment))
                .is_some()
            {
                return Err(ExecServerError::Protocol(format!(
                    "environment id `{id}` is duplicated"
                )));
            }
        }
        let default_environment = match default {
            EnvironmentDefault::Disabled => None,
            EnvironmentDefault::EnvironmentId(environment_id) => {
                if !environment_map.contains_key(&environment_id) {
                    return Err(ExecServerError::Protocol(format!(
                        "default environment `{environment_id}` is not configured"
                    )));
                }
                Some(environment_id)
            }
        };
        // The snapshot is valid; start connecting its remote environments in the background.
        for environment in environment_map.values() {
            environment.start_connecting();
        }
        Ok(Self {
            default_environment,
            environments: RwLock::new(environment_map),
            local_environment,
            local_runtime_paths,
            http_client_factory,
        })
    }

    /// Returns the default environment instance.
    pub fn default_environment(&self) -> Option<Arc<Environment>> {
        self.default_environment
            .as_deref()
            .and_then(|environment_id| self.get_environment(environment_id))
    }

    /// Returns the id of the default environment.
    pub fn default_environment_id(&self) -> Option<&str> {
        self.default_environment.as_deref()
    }

    /// Returns the ordered environment ids used for new thread startup.
    pub fn default_environment_ids(&self) -> Vec<String> {
        let Some(default_environment_id) = self.default_environment.as_ref() else {
            return Vec::new();
        };
        let environments = self
            .environments
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut environment_ids = Vec::with_capacity(environments.len());
        environment_ids.push(default_environment_id.clone());
        environment_ids.extend(
            environments
                .keys()
                .filter(|environment_id| *environment_id != default_environment_id)
                .cloned(),
        );
        environment_ids
    }

    /// Returns the local environment instance when one is configured.
    pub fn try_local_environment(&self) -> Option<Arc<Environment>> {
        self.local_environment.as_ref().map(Arc::clone)
    }

    /// Returns a named environment instance.
    pub fn get_environment(&self, environment_id: &str) -> Option<Arc<Environment>> {
        self.environments
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(environment_id)
            .cloned()
    }

    /// Returns the outbound HTTP policy carried by this manager.
    pub fn http_client_factory(&self) -> &HttpClientFactory {
        &self.http_client_factory
    }

    /// Returns the current status of one named environment when it is configured.
    pub async fn get_environment_status(
        &self,
        environment_id: &str,
    ) -> Option<EnvironmentObservedStatus> {
        let environment = self.get_environment(environment_id)?;
        Some(environment.status().await)
    }

    /// Adds or replaces a named remote environment without changing the
    /// manager's default environment selection. Uses the default WebSocket
    /// connection timeout when none is provided.
    pub fn upsert_environment(
        &self,
        environment_id: String,
        exec_server_url: String,
        connect_timeout: Option<std::time::Duration>,
    ) -> Result<(), ExecServerError> {
        validate_environment_id(&environment_id)?;
        let exec_server_url = validate_remote_exec_server_url(exec_server_url)?;
        let environment = Arc::new(Environment::remote_with_transport(
            ExecServerTransportParams::websocket_url(
                exec_server_url,
                connect_timeout.unwrap_or(DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT),
            ),
            self.local_runtime_paths.clone(),
            self.http_client_factory.clone(),
        ));
        self.insert_environment(environment_id, environment)
    }

    fn insert_environment(
        &self,
        environment_id: String,
        environment: Arc<Environment>,
    ) -> Result<(), ExecServerError> {
        let replaced = {
            let mut environments = self
                .environments
                .write()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            environments.insert(environment_id, Arc::clone(&environment))
        };
        drop(replaced);
        environment.start_connecting();
        Ok(())
    }
}

fn validate_environment_id(environment_id: &str) -> Result<(), ExecServerError> {
    if environment_id.is_empty() {
        return Err(ExecServerError::Protocol(
            "environment id cannot be empty".to_string(),
        ));
    }
    if environment_id == LOCAL_ENVIRONMENT_ID {
        return Err(ExecServerError::Protocol(format!(
            "environment id `{LOCAL_ENVIRONMENT_ID}` is reserved for EnvironmentManager"
        )));
    }
    Ok(())
}

fn validate_remote_exec_server_url(exec_server_url: String) -> Result<String, ExecServerError> {
    let (exec_server_url, disabled) = normalize_exec_server_url(Some(exec_server_url));
    if disabled {
        return Err(ExecServerError::Protocol(
            "remote environment cannot use disabled exec-server url".to_string(),
        ));
    }
    exec_server_url.ok_or_else(|| {
        ExecServerError::Protocol("remote environment requires an exec-server url".to_string())
    })
}

/// Concrete execution/filesystem environment selected for a session.
///
/// This bundles the selected backend metadata together with the local runtime
/// paths used by filesystem helpers.
#[derive(Clone)]
pub struct Environment {
    remote_client: Option<LazyRemoteExecServerClient>,
    // Dropping the environment stops unfinished background startup work.
    startup_task: Arc<StdMutex<Option<AbortOnDropHandle<()>>>>,
    exec_backend: Arc<dyn ExecBackend>,
    filesystem: Arc<dyn ExecutorFileSystem>,
    http_client: Arc<dyn HttpClient>,
    local_runtime_paths: Option<ExecServerRuntimePaths>,
}

impl Environment {
    /// Builds a test-only local environment without configured sandbox helper paths.
    pub fn default_for_tests() -> Self {
        Self {
            remote_client: None,
            startup_task: Arc::new(StdMutex::new(None)),
            exec_backend: Arc::new(LocalProcess::default()),
            filesystem: Arc::new(LocalFileSystem::unsandboxed()),
            // Test-only construction has no application config from which to resolve proxy policy.
            http_client: Arc::new(RouteAwareHttpClient::new(HttpClientFactory::new(
                OutboundProxyPolicy::ReqwestDefault,
            ))),
            local_runtime_paths: None,
        }
    }
}

impl std::fmt::Debug for Environment {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Environment").finish_non_exhaustive()
    }
}

impl Environment {
    /// Builds an environment using the caller's effective outbound HTTP policy.
    pub fn create(
        exec_server_url: Option<String>,
        local_runtime_paths: ExecServerRuntimePaths,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        Self::create_inner(
            exec_server_url,
            Some(local_runtime_paths),
            http_client_factory,
        )
    }

    /// Builds a test-only environment without configured sandbox helper paths.
    pub fn create_for_tests(exec_server_url: Option<String>) -> Result<Self, ExecServerError> {
        Self::create_inner(
            exec_server_url,
            /*local_runtime_paths*/ None,
            HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        )
    }

    /// Builds an environment from the raw `NOVA_EXEC_SERVER_URL` value and
    /// local runtime paths used when creating local filesystem helpers.
    fn create_inner(
        exec_server_url: Option<String>,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        let (exec_server_url, disabled) = normalize_exec_server_url(exec_server_url);
        if disabled {
            return Err(ExecServerError::Protocol(
                "disabled mode does not create an Environment".to_string(),
            ));
        }

        Ok(match exec_server_url {
            Some(exec_server_url) => Self::remote_with_transport(
                ExecServerTransportParams::websocket_url(
                    exec_server_url,
                    DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT,
                ),
                local_runtime_paths,
                http_client_factory,
            ),
            None => match local_runtime_paths {
                Some(local_runtime_paths) => Self::local(local_runtime_paths, http_client_factory),
                None => Self::default_for_tests(),
            },
        })
    }

    pub(crate) fn local(
        local_runtime_paths: ExecServerRuntimePaths,
        http_client_factory: HttpClientFactory,
    ) -> Self {
        Self {
            remote_client: None,
            startup_task: Arc::new(StdMutex::new(None)),
            exec_backend: Arc::new(LocalProcess::with_local_runtime_paths(
                local_runtime_paths.clone(),
            )),
            filesystem: Arc::new(LocalFileSystem::with_runtime_paths(
                local_runtime_paths.clone(),
            )),
            http_client: Arc::new(RouteAwareHttpClient::new(http_client_factory)),
            local_runtime_paths: Some(local_runtime_paths),
        }
    }

    pub(crate) fn remote_with_transport(
        remote_transport: ExecServerTransportParams,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
        http_client_factory: HttpClientFactory,
    ) -> Self {
        let client = LazyRemoteExecServerClient::new(remote_transport, http_client_factory);
        Self::remote_with_client(client, local_runtime_paths)
    }

    pub(crate) fn remote_with_client(
        client: LazyRemoteExecServerClient,
        local_runtime_paths: Option<ExecServerRuntimePaths>,
    ) -> Self {
        let exec_backend: Arc<dyn ExecBackend> = Arc::new(RemoteProcess::new(client.clone()));
        let filesystem: Arc<dyn ExecutorFileSystem> =
            Arc::new(RemoteFileSystem::new(client.clone()));

        Self {
            remote_client: Some(client.clone()),
            startup_task: Arc::new(StdMutex::new(None)),
            exec_backend,
            filesystem,
            http_client: Arc::new(client),
            local_runtime_paths,
        }
    }

    pub fn is_remote(&self) -> bool {
        self.remote_client.is_some()
    }

    /// Subscribes to the current connection state for this remote environment.
    pub fn subscribe_connection_state(
        &self,
    ) -> Option<watch::Receiver<EnvironmentConnectionState>> {
        self.remote_client
            .as_ref()
            .map(LazyRemoteExecServerClient::subscribe_connection_state)
    }

    pub fn local_runtime_paths(&self) -> Option<&ExecServerRuntimePaths> {
        self.local_runtime_paths.as_ref()
    }

    /// Returns environment information from the selected execution/filesystem environment.
    /// Remote metadata is cached for the current client's lifetime.
    #[tracing::instrument(
        name = "exec_server.environment.info",
        skip_all,
        fields(remote = self.is_remote())
    )]
    pub async fn info(&self) -> Result<EnvironmentInfo, ExecServerError> {
        match &self.remote_client {
            Some(client) => client.environment_info().await,
            None => Ok(EnvironmentInfo::local()),
        }
    }

    /// Refresh the connection to the executor currently registered for this environment.
    ///
    /// # Caller contract
    ///
    /// Call after a planned replacement has registered and become available under the
    /// same environment ID. This method does not provision or destroy executors. It
    /// requires a remote environment; the local environment returns an error.
    ///
    /// # Session behavior
    ///
    /// Retirement cancels old recovery, fails its outstanding work and process handles,
    /// and never replays commands. The environment object and filesystem handle remain
    /// usable through the new connection. The live readiness check rejects a recovering
    /// connection.
    ///
    /// # Completion and errors
    ///
    /// Success means the selected connection answered a live status RPC, not merely that
    /// metadata was cached. Refresh bypasses the old session's recovery deadline, but
    /// connection and status RPC timeouts still apply. Errors after retirement do not
    /// restore the old session. Ordinary disconnect recovery is unchanged unless refresh
    /// retires the session.
    #[tracing::instrument(
        name = "exec_server.environment.refresh_connection",
        skip_all,
        fields(remote = self.is_remote())
    )]
    pub async fn refresh_connection(&self) -> Result<(), ExecServerError> {
        let client = self.remote_client.as_ref().ok_or_else(|| {
            ExecServerError::Protocol(
                "connection refresh requires a remote environment".to_string(),
            )
        })?;
        client.refresh_connection().await
    }

    /// Fetches uncached metadata, connecting or waiting for recovery as needed.
    #[tracing::instrument(
        name = "exec_server.environment.force_info",
        skip_all,
        fields(remote = self.is_remote())
    )]
    pub async fn force_info(&self) -> Result<EnvironmentInfo, ExecServerError> {
        match &self.remote_client {
            Some(client) => client.get().await?.force_environment_info().await,
            None => Ok(EnvironmentInfo::local()),
        }
    }

    /// Reads selected executor-local configuration fields for this environment.
    pub async fn read_environment_config(
        &self,
        params: EnvironmentConfigReadParams,
    ) -> Result<EnvironmentConfigReadResponse, ExecServerError> {
        match &self.remote_client {
            Some(client) => client.get().await?.read_environment_config(params).await,
            None => read_environment_config(params)
                .await
                .map_err(|error| ExecServerError::Protocol(error.to_string())),
        }
    }

    /// Starts connecting a remote environment without waiting for it.
    /// Requires an active Tokio runtime when background startup is supported.
    pub fn start_connecting(&self) {
        let Some(client) = &self.remote_client else {
            return;
        };
        let mut startup_task = self
            .startup_task
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if startup_task.is_none() {
            *startup_task = client.start_connecting();
        }
    }

    /// Starts the initial connection after an environment is actually selected for use.
    pub(crate) fn start_connecting_for_use(environment: &Arc<Self>) {
        let Some(client) = &environment.remote_client else {
            return;
        };
        let mut startup_task = environment
            .startup_task
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if startup_task.is_none() {
            let client = client.clone();
            *startup_task = Some(AbortOnDropHandle::new(tokio::spawn(
                async move {
                    if let Err(error) = client.wait_until_ready().await {
                        tracing::debug!(%error, "exec-server environment startup failed");
                    }
                }
                .in_current_span()
                .with_current_subscriber(),
            )));
        }
    }

    /// Returns whether startup has completed, including a first connection made by refresh.
    pub fn startup_finished(&self) -> bool {
        self.remote_client
            .as_ref()
            .is_none_or(LazyRemoteExecServerClient::startup_finished)
    }

    /// Waits for initial startup, retrying a previous transient failure when possible.
    #[tracing::instrument(
        name = "exec_server.environment.wait_until_ready",
        skip_all,
        fields(remote = self.is_remote())
    )]
    pub async fn wait_until_ready(&self) -> Result<(), ExecServerError> {
        match &self.remote_client {
            Some(client) => client.wait_until_ready().await,
            None => Ok(()),
        }
    }

    /// Returns whether the environment can serve a request without waiting or reconnecting.
    pub(crate) fn readiness_result(&self) -> Option<Result<(), ExecServerError>> {
        match &self.remote_client {
            Some(client) => client.readiness_result(),
            None => Some(Ok(())),
        }
    }

    /// Returns the environment's status without starting or recovering it.
    ///
    /// Local environments are always ready. Remote environments with an
    /// already-ready cached connection receive a fail-fast `environment/status`
    /// probe; other remote states are returned from cached connection state
    /// without waiting for startup or recovery.
    pub async fn status(&self) -> EnvironmentObservedStatus {
        match &self.remote_client {
            Some(client) => client.status().await,
            None => EnvironmentObservedStatus::Ready,
        }
    }

    pub fn get_exec_backend(&self) -> Arc<dyn ExecBackend> {
        Arc::clone(&self.exec_backend)
    }

    pub fn get_http_client(&self) -> Arc<dyn HttpClient> {
        Arc::clone(&self.http_client)
    }

    pub fn get_filesystem(&self) -> Arc<dyn ExecutorFileSystem> {
        Arc::clone(&self.filesystem)
    }

    /// Returns a filesystem view that fails instead of starting or waiting for a connection.
    pub fn get_filesystem_without_reconnect(&self) -> Arc<dyn ExecutorFileSystem> {
        match &self.remote_client {
            Some(client) => Arc::new(RemoteFileSystem::new(client.fail_fast())),
            None => Arc::clone(&self.filesystem),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::time::Duration;

    use nova_exec_server_utils_path_uri::PathUri;
    use pretty_assertions::assert_eq;
    use tokio::net::TcpListener;
    use tokio::time::timeout;

    use super::*;
    use crate::ExecParams;
    use crate::ProcessId;
    use crate::client_api::StdioExecServerCommand;

    fn legacy_http_client_factory() -> HttpClientFactory {
        HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault)
    }

    fn prepared_websocket_environment() -> ExecServerTransportParams {
        ExecServerTransportParams::websocket_url(
            "ws://127.0.0.1:8765".to_string(),
            DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT,
        )
    }

    fn test_runtime_paths() -> ExecServerRuntimePaths {
        ExecServerRuntimePaths::new(
            std::env::current_exe().expect("current exe"),
            /*nova_linux_sandbox_exe*/ None,
        )
        .expect("runtime paths")
    }

    fn assert_local_environment_unavailable(manager: &EnvironmentManager) {
        assert!(manager.try_local_environment().is_none());
    }

    #[test]
    fn local_environment_info_includes_current_directory() {
        let info = super::EnvironmentInfo::local();

        assert_eq!(
            info.cwd,
            Some(
                PathUri::from_host_native_path(std::env::current_dir().expect("current directory"))
                    .expect("cwd URI")
            )
        );
        assert_eq!(
            info.temp_dir,
            PathUri::from_host_native_path(std::env::temp_dir()).ok()
        );
    }

    #[tokio::test]
    async fn create_local_environment_does_not_connect() {
        let environment = Environment::create(
            /*exec_server_url*/ None,
            test_runtime_paths(),
            legacy_http_client_factory(),
        )
        .expect("create environment");

        assert!(!environment.is_remote());
        assert!(environment.info().await.is_ok());
    }

    #[tokio::test]
    async fn environment_manager_normalizes_empty_url() {
        let manager =
            EnvironmentManager::create_for_tests(Some(String::new()), Some(test_runtime_paths()))
                .await;

        let environment = manager.default_environment().expect("default environment");
        assert_eq!(manager.default_environment_id(), Some(LOCAL_ENVIRONMENT_ID));
        assert!(Arc::ptr_eq(
            &environment,
            &manager
                .get_environment(LOCAL_ENVIRONMENT_ID)
                .expect("local environment")
        ));
        assert!(Arc::ptr_eq(
            &environment,
            &manager.try_local_environment().expect("local environment")
        ));
        assert!(manager.try_local_environment().is_some());
        assert!(manager.get_environment(REMOTE_ENVIRONMENT_ID).is_none());
        assert!(!environment.is_remote());
    }

    #[tokio::test]
    async fn disabled_environment_manager_has_no_default_or_local_environment() {
        let manager = EnvironmentManager::without_environments(HttpClientFactory::new(
            OutboundProxyPolicy::RespectSystemProxy,
        ));

        assert!(manager.default_environment().is_none());
        assert_eq!(manager.default_environment_id(), None);
        assert_local_environment_unavailable(&manager);
        assert!(manager.get_environment(LOCAL_ENVIRONMENT_ID).is_none());
        assert!(manager.get_environment(REMOTE_ENVIRONMENT_ID).is_none());
        assert_eq!(
            manager.http_client_factory().outbound_proxy_policy(),
            OutboundProxyPolicy::RespectSystemProxy
        );
    }

    #[tokio::test]
    async fn environment_manager_creates_remote_environment_for_url() {
        let manager = EnvironmentManager::create_for_tests(
            Some("ws://127.0.0.1:8765".to_string()),
            Some(test_runtime_paths()),
        )
        .await;

        let environment = manager.default_environment().expect("default environment");
        assert_eq!(
            manager.default_environment_id(),
            Some(REMOTE_ENVIRONMENT_ID)
        );
        assert!(environment.is_remote());
        assert!(Arc::ptr_eq(
            &environment,
            &manager
                .get_environment(REMOTE_ENVIRONMENT_ID)
                .expect("remote environment")
        ));
        assert!(manager.get_environment(LOCAL_ENVIRONMENT_ID).is_none());
        assert_local_environment_unavailable(&manager);
    }

    #[tokio::test]
    async fn environment_manager_default_environment_caches_environment() {
        let manager = EnvironmentManager::default_for_tests();

        let first = manager.default_environment().expect("default environment");
        let second = manager.default_environment().expect("default environment");

        assert!(Arc::ptr_eq(&first, &second));
        assert!(Arc::ptr_eq(
            &first.get_filesystem(),
            &second.get_filesystem()
        ));
    }

    #[tokio::test]
    async fn environment_manager_builds_from_snapshot() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![(
                REMOTE_ENVIRONMENT_ID.to_string(),
                prepared_websocket_environment(),
            )],
            default: EnvironmentDefault::EnvironmentId(REMOTE_ENVIRONMENT_ID.to_string()),
            include_local: false,
        };
        let manager = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect("environment manager");

        assert_eq!(
            manager.default_environment_id(),
            Some(REMOTE_ENVIRONMENT_ID)
        );
        assert!(
            manager
                .get_environment(REMOTE_ENVIRONMENT_ID)
                .expect("remote environment")
                .is_remote()
        );
        assert!(manager.get_environment(LOCAL_ENVIRONMENT_ID).is_none());
        assert_local_environment_unavailable(&manager);
    }

    #[tokio::test]
    async fn environment_manager_rejects_empty_environment_id() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![("".to_string(), prepared_websocket_environment())],
            default: EnvironmentDefault::Disabled,
            include_local: false,
        };
        let err = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect_err("empty id should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: environment id cannot be empty"
        );
    }

    #[tokio::test]
    async fn environment_manager_rejects_provider_supplied_local_environment() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![(
                LOCAL_ENVIRONMENT_ID.to_string(),
                prepared_websocket_environment(),
            )],
            default: EnvironmentDefault::Disabled,
            include_local: false,
        };
        let err = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect_err("local id should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: environment id `local` is reserved for EnvironmentManager"
        );
    }

    #[tokio::test]
    async fn environment_manager_uses_explicit_provider_default() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![("devbox".to_string(), prepared_websocket_environment())],
            default: EnvironmentDefault::EnvironmentId("devbox".to_string()),
            include_local: true,
        };
        let manager = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect("manager");

        assert_eq!(manager.default_environment_id(), Some("devbox"));
        assert_eq!(
            manager.default_environment_ids(),
            vec!["devbox".to_string(), LOCAL_ENVIRONMENT_ID.to_string()]
        );
        assert!(manager.default_environment().expect("default").is_remote());
    }

    #[tokio::test]
    async fn environment_manager_disables_provider_default() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![("devbox".to_string(), prepared_websocket_environment())],
            default: EnvironmentDefault::Disabled,
            include_local: true,
        };
        let manager = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect("manager");

        assert_eq!(manager.default_environment_id(), None);
        assert!(manager.default_environment().is_none());
        assert!(Arc::ptr_eq(
            &manager
                .get_environment(LOCAL_ENVIRONMENT_ID)
                .expect("local environment"),
            &manager.try_local_environment().expect("local environment")
        ));
    }

    #[tokio::test]
    async fn environment_manager_rejects_unknown_provider_default() {
        let snapshot = EnvironmentProviderSnapshot {
            environments: vec![("devbox".to_string(), prepared_websocket_environment())],
            default: EnvironmentDefault::EnvironmentId("missing".to_string()),
            include_local: true,
        };
        let err = EnvironmentManager::from_snapshot(
            snapshot,
            Some(test_runtime_paths()),
            legacy_http_client_factory(),
        )
        .expect_err("unknown default should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: default environment `missing` is not configured"
        );
    }

    #[tokio::test]
    async fn environment_manager_includes_local_for_default_provider_without_url() {
        let manager = EnvironmentManager::create_for_tests(
            /*exec_server_url*/ None,
            Some(test_runtime_paths()),
        )
        .await;

        let environment = manager.default_environment().expect("default environment");
        assert_eq!(manager.default_environment_id(), Some(LOCAL_ENVIRONMENT_ID));
        assert!(Arc::ptr_eq(
            &environment,
            &manager
                .get_environment(LOCAL_ENVIRONMENT_ID)
                .expect("local environment")
        ));
        assert!(Arc::ptr_eq(
            &environment,
            &manager.try_local_environment().expect("local environment")
        ));
        assert!(!environment.is_remote());
    }

    #[tokio::test]
    async fn environment_manager_carries_local_runtime_paths() {
        let runtime_paths = test_runtime_paths();
        let manager = EnvironmentManager::create_for_tests(
            /*exec_server_url*/ None,
            Some(runtime_paths.clone()),
        )
        .await;

        let environment = manager.try_local_environment().expect("local environment");

        assert_eq!(environment.local_runtime_paths(), Some(&runtime_paths));
        let manager = EnvironmentManager::create_for_tests(
            /*exec_server_url*/ None,
            Some(
                environment
                    .local_runtime_paths()
                    .expect("local runtime paths")
                    .clone(),
            ),
        )
        .await;
        let environment = manager.try_local_environment().expect("local environment");
        assert_eq!(environment.local_runtime_paths(), Some(&runtime_paths));
    }

    #[tokio::test]
    async fn environment_manager_omits_default_provider_local_lookup_when_default_disabled() {
        let manager = EnvironmentManager::create_for_tests(
            Some("none".to_string()),
            Some(test_runtime_paths()),
        )
        .await;

        assert!(manager.default_environment().is_none());
        assert_eq!(manager.default_environment_id(), None);
        assert!(manager.get_environment(LOCAL_ENVIRONMENT_ID).is_none());
        assert!(manager.get_environment(REMOTE_ENVIRONMENT_ID).is_none());
        assert_local_environment_unavailable(&manager);
    }

    #[tokio::test]
    async fn environment_manager_snapshot_without_local_environment_disables_local_default() {
        let mut snapshot = EnvironmentProviderSnapshot {
            environments: Vec::new(),
            default: EnvironmentDefault::EnvironmentId(LOCAL_ENVIRONMENT_ID.to_string()),
            include_local: true,
        };
        snapshot.include_local = false;
        snapshot.default = EnvironmentDefault::Disabled;
        let manager = EnvironmentManager::from_snapshot(
            snapshot,
            /*local_runtime_paths*/ None,
            legacy_http_client_factory(),
        )
        .expect("environment manager");

        assert!(manager.default_environment().is_none());
        assert_eq!(manager.default_environment_id(), None);
        assert!(manager.get_environment(LOCAL_ENVIRONMENT_ID).is_none());
        assert_local_environment_unavailable(&manager);
    }

    #[tokio::test]
    async fn get_environment_returns_none_for_unknown_id() {
        let manager = EnvironmentManager::default_for_tests();

        assert!(manager.get_environment("does-not-exist").is_none());
    }

    #[tokio::test]
    async fn environment_manager_upserts_named_remote_environment() {
        let manager = EnvironmentManager::without_environments(legacy_http_client_factory());

        manager
            .upsert_environment(
                "executor-a".to_string(),
                "ws://127.0.0.1:8765".to_string(),
                /*connect_timeout*/ None,
            )
            .expect("remote environment");
        let first = manager
            .get_environment("executor-a")
            .expect("first remote environment");
        assert!(first.is_remote());
        assert_eq!(manager.default_environment_id(), None);

        manager
            .upsert_environment(
                "executor-a".to_string(),
                "ws://127.0.0.1:9876".to_string(),
                /*connect_timeout*/ None,
            )
            .expect("updated remote environment");
        let second = manager
            .get_environment("executor-a")
            .expect("second remote environment");
        assert!(second.is_remote());
        assert!(!Arc::ptr_eq(&first, &second));
    }

    #[tokio::test]
    async fn environment_manager_starts_remote_environment_when_upserted() {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind websocket listener");
        let manager = EnvironmentManager::without_environments(legacy_http_client_factory());

        manager
            .upsert_environment(
                "executor-a".to_string(),
                format!("ws://{}", listener.local_addr().expect("listener address")),
                /*connect_timeout*/ None,
            )
            .expect("remote environment");

        timeout(Duration::from_secs(5), listener.accept())
            .await
            .expect("environment should start connecting when registered")
            .expect("accept connection");
    }

    #[tokio::test]
    async fn environment_status_keeps_stdio_environment_pending() {
        let environment = Environment::remote_with_transport(
            ExecServerTransportParams::StdioCommand {
                command: StdioExecServerCommand {
                    program: "nova-missing-exec-server-for-test".to_string(),
                    args: Vec::new(),
                    env: HashMap::new(),
                    cwd: None,
                },
                initialize_timeout: Duration::from_secs(1),
            },
            /*local_runtime_paths*/ None,
            legacy_http_client_factory(),
        );

        assert_eq!(
            environment.status().await,
            EnvironmentObservedStatus::Pending
        );
        assert!(!environment.startup_finished());
    }

    #[tokio::test]
    async fn environment_manager_leaves_stdio_environment_lazy() {
        let transport = ExecServerTransportParams::StdioCommand {
            command: StdioExecServerCommand {
                program: "nova-missing-exec-server-for-test".to_string(),
                args: Vec::new(),
                env: HashMap::new(),
                cwd: None,
            },
            initialize_timeout: Duration::from_secs(1),
        };
        let manager = EnvironmentManager::from_snapshot(
            EnvironmentProviderSnapshot {
                environments: vec![("stdio".to_string(), transport)],
                default: EnvironmentDefault::Disabled,
                include_local: false,
            },
            /*local_runtime_paths*/ None,
            legacy_http_client_factory(),
        )
        .expect("environment manager");
        let environment = manager.get_environment("stdio").expect("stdio environment");

        assert!(!environment.startup_finished());
        assert!(environment.wait_until_ready().await.is_err());
        assert!(environment.startup_finished());
    }

    #[tokio::test]
    async fn replacing_environment_stops_its_startup_task() {
        let first_listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind first websocket listener");
        let second_listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind second websocket listener");
        let manager = EnvironmentManager::without_environments(legacy_http_client_factory());
        manager
            .upsert_environment(
                "executor-a".to_string(),
                format!(
                    "ws://{}",
                    first_listener.local_addr().expect("first listener address")
                ),
                /*connect_timeout*/ None,
            )
            .expect("first remote environment");
        let environment = manager
            .get_environment("executor-a")
            .expect("first remote environment");
        let startup_abort = environment
            .startup_task
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
            .expect("startup task")
            .abort_handle();
        assert!(!startup_abort.is_finished());
        drop(environment);

        manager
            .upsert_environment(
                "executor-a".to_string(),
                format!(
                    "ws://{}",
                    second_listener
                        .local_addr()
                        .expect("second listener address")
                ),
                /*connect_timeout*/ None,
            )
            .expect("replacement remote environment");

        timeout(Duration::from_secs(1), async {
            while !startup_abort.is_finished() {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("replacing the environment should cancel its startup task");
    }

    #[tokio::test]
    async fn environment_manager_rejects_empty_remote_environment_url() {
        let manager = EnvironmentManager::without_environments(legacy_http_client_factory());

        let err = manager
            .upsert_environment(
                "executor-a".to_string(),
                String::new(),
                /*connect_timeout*/ None,
            )
            .expect_err("empty URL should fail");

        assert_eq!(
            err.to_string(),
            "exec-server protocol error: remote environment requires an exec-server url"
        );
    }

    #[tokio::test]
    async fn default_environment_has_ready_local_executor() {
        let environment = Environment::default_for_tests();

        let response = environment
            .get_exec_backend()
            .start(crate::ExecParams {
                process_id: ProcessId::from("default-env-proc"),
                argv: vec!["true".to_string()],
                cwd: PathUri::from_host_native_path(
                    std::env::current_dir().expect("read current dir"),
                )
                .expect("cwd URI"),
                shell_snapshot: None,
                env_policy: None,
                env: Default::default(),
                tty: false,
                pipe_stdin: false,
                arg0: None,
                sandbox: None,
                enforce_managed_network: false,
                managed_network: None,
                network_proxy: None,
            })
            .await
            .expect("start process");

        assert_eq!(response.process.process_id().as_str(), "default-env-proc");
    }

    #[tokio::test]
    async fn local_environment_passes_runtime_paths_to_exec_backend() {
        let environment = Environment::local(test_runtime_paths(), legacy_http_client_factory());
        #[cfg(unix)]
        let uri = "file://server/share/checkout";
        #[cfg(windows)]
        let uri = "file:///usr/local/checkout";
        let sandbox_cwd = PathUri::parse(uri).expect("non-native sandbox cwd URI");
        let source = sandbox_cwd
            .to_abs_path()
            .expect_err("sandbox cwd should not be native to this host");
        let sandbox = crate::FileSystemSandboxContext::from_permission_profile_with_cwd(
            nova_exec_server_protocol_core::models::PermissionProfile::workspace_write(),
            sandbox_cwd.clone(),
        );

        let result = environment
            .get_exec_backend()
            .start(crate::ExecParams {
                process_id: ProcessId::from("local-sandbox-proc"),
                argv: vec!["true".to_string()],
                cwd: PathUri::from_host_native_path(
                    std::env::current_dir().expect("read current dir"),
                )
                .expect("cwd URI"),
                shell_snapshot: None,
                env_policy: None,
                env: Default::default(),
                tty: false,
                pipe_stdin: false,
                arg0: None,
                sandbox: Some(sandbox),
                enforce_managed_network: false,
                managed_network: None,
                network_proxy: None,
            })
            .await;
        let Err(err) = result else {
            panic!("sandbox cwd should be rejected after resolving runtime paths");
        };

        assert_eq!(
            err.to_string(),
            format!(
                "exec-server rejected request (-32602): sandbox cwd URI `{sandbox_cwd}` is not valid on this exec-server host: {source}"
            )
        );
    }

    #[tokio::test]
    async fn test_environment_rejects_sandboxed_filesystem_without_runtime_paths() {
        let environment = Environment::default_for_tests();
        let path = nova_exec_server_utils_absolute_path::AbsolutePathBuf::from_absolute_path(
            std::env::current_exe().expect("current exe").as_path(),
        )
        .expect("absolute current exe");
        let path = nova_exec_server_utils_path_uri::PathUri::from_abs_path(&path);
        let sandbox = crate::FileSystemSandboxContext::from_permission_profile(
            nova_exec_server_protocol_core::models::PermissionProfile::from_runtime_permissions(
                &nova_exec_server_protocol_core::permissions::FileSystemSandboxPolicy::restricted(
                    Vec::new(),
                ),
                nova_exec_server_protocol_core::permissions::NetworkSandboxPolicy::Restricted,
            ),
        );

        let err = environment
            .get_filesystem()
            .read_file(&path, Default::default(), Some(&sandbox))
            .await
            .expect_err("sandboxed read should require runtime paths");

        assert_eq!(
            err.to_string(),
            "sandboxed filesystem operations require configured runtime paths"
        );
    }
}
