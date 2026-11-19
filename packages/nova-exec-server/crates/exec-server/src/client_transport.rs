use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::AsyncBufReadExt;
use tokio::io::AsyncReadExt;
use tokio::io::BufReader;
use tokio::process::Command;
use tokio::sync::OwnedSemaphorePermit;
use tokio::time::timeout;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tracing::debug;
use tracing::warn;

use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_protocol_core::shell_environment::scrub_non_inheritable_env_vars;
use nova_exec_server_utils_rustls_provider::ensure_rustls_crypto_provider;
use nova_exec_server_websocket_client::WebSocketConnector;
use nova_exec_server_websocket_client::WebSocketTlsMode;

use crate::ExecServerClient;
use crate::ExecServerError;
use crate::client_api::ExecServerClientConnectOptions;
use crate::client_api::ExecServerTransportParams;
use crate::client_api::NoiseRendezvousConnectArgs;
use crate::client_api::NoiseRendezvousConnectBundle;
use crate::client_api::NoiseRendezvousConnectProvider;
use crate::client_api::RemoteExecServerConnectArgs;
use crate::client_api::StdioExecServerCommand;
use crate::client_api::StdioExecServerConnectArgs;
use crate::connection::JsonRpcConnection;
use crate::noise_channel::NoiseChannelIdentity;
use crate::noise_relay::NoiseHarnessConnectionArgs;
use crate::noise_relay::noise_harness_connection_from_websocket_with_readiness;
use crate::noise_relay::noise_relay_websocket_config;
use crate::registry_auth::AuthError;
use crate::registry_auth::AuthProvider;
use crate::trace_context::current_rendezvous_headers;
use nova_exec_server_http_client::Request;
use nova_exec_server_http_client::RequestCompression;
use nova_exec_server_websocket_client::WebSocketConnection;

const MAX_STDIO_STDERR_LOG_LINE_LEN: u64 = 8 * 1024;
const ENVIRONMENT_CLIENT_NAME: &str = "nova-exec-server";

pub(crate) async fn connect_websocket_request(
    request: http::Request<()>,
    diagnostic_url: String,
    connector: WebSocketConnector,
    connect_timeout: Duration,
    use_loopback_direct: bool,
) -> Result<WebSocketConnection, ExecServerError> {
    let websocket_config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default();
    timeout(connect_timeout, async {
        if use_loopback_direct {
            connector
                .connect_loopback_direct(request, websocket_config)
                .await
        } else {
            connector.connect(request, websocket_config).await
        }
    })
    .await
    .map_err(|_| ExecServerError::WebSocketConnectTimeout {
        url: diagnostic_url.clone(),
        timeout: connect_timeout,
    })?
    .map(|(websocket, _)| websocket)
    .map_err(|source| ExecServerError::WebSocketConnect {
        url: diagnostic_url,
        source,
    })
}

pub(crate) async fn authenticate_websocket_request(
    request: &mut http::Request<()>,
    auth_provider: &dyn AuthProvider,
) -> Result<(), AuthError> {
    let url = request.uri().to_string();
    let signing_url = if let Some(rest) = url.strip_prefix("wss://") {
        format!("https://{rest}")
    } else if let Some(rest) = url.strip_prefix("ws://") {
        format!("http://{rest}")
    } else {
        url
    };
    let mut auth_request = Request::new(request.method().clone(), signing_url);
    // Intermediaries may rewrite WebSocket and hop-by-hop headers after signing.
    if let Some(host) = request.headers().get(http::header::HOST) {
        auth_request
            .headers
            .insert(http::header::HOST, host.clone());
    }
    let authenticated = auth_provider.apply_auth(auth_request).await?;
    if authenticated.method != *request.method() {
        return Err(AuthError::Build(
            "authentication changed the WebSocket request method".to_string(),
        ));
    }
    if authenticated.body.is_some() || authenticated.compression != RequestCompression::None {
        return Err(AuthError::Build(
            "authentication added a body or compression to the WebSocket request".to_string(),
        ));
    }

    let authenticated_websocket_url = websocket_url_from_authenticated_url(&authenticated.url)?;
    let authenticated_uri = authenticated_websocket_url.parse().map_err(|error| {
        AuthError::Build(format!("invalid authenticated WebSocket URL: {error}"))
    })?;
    let original_host = request.headers().get(http::header::HOST).cloned();
    for (name, value) in &authenticated.headers {
        if is_websocket_handshake_header(name) {
            if name == http::header::HOST && original_host.as_ref() == Some(value) {
                continue;
            }
            return Err(AuthError::Build(format!(
                "authentication changed WebSocket handshake header {name}"
            )));
        }
        request.headers_mut().insert(name, value.clone());
    }
    *request.uri_mut() = authenticated_uri;
    Ok(())
}

fn websocket_url_from_authenticated_url(url: &str) -> Result<String, AuthError> {
    let mut url = url::Url::parse(url)
        .map_err(|error| AuthError::Build(format!("invalid authenticated request URL: {error}")))?;
    let websocket_scheme = match url.scheme() {
        "https" => "wss",
        "http" => "ws",
        scheme => {
            return Err(AuthError::Build(format!(
                "authentication returned unsupported WebSocket URL scheme: {scheme}"
            )));
        }
    };
    url.set_scheme(websocket_scheme).map_err(|_| {
        AuthError::Build("failed to convert authenticated URL to WebSocket scheme".to_string())
    })?;
    Ok(url.into())
}

fn is_websocket_handshake_header(name: &http::header::HeaderName) -> bool {
    name == http::header::HOST
        || name == http::header::CONNECTION
        || name == http::header::UPGRADE
        || name == http::header::CONTENT_LENGTH
        || name == http::header::TRANSFER_ENCODING
        || name.as_str().starts_with("sec-websocket-")
}

/// 一次物理重连及其初始化参数。
///
/// `attempt_permit` 必须持有到 initialize 完成，避免宿主在替换连接尚未
/// 接管会话时并发提交下一次替换。
pub(crate) struct ReconnectAttempt {
    pub(crate) connection: JsonRpcConnection,
    pub(crate) options: ExecServerClientConnectOptions,
    attempt_permit: Option<OwnedSemaphorePermit>,
}

impl ReconnectAttempt {
    pub(crate) fn new(
        connection: JsonRpcConnection,
        options: ExecServerClientConnectOptions,
    ) -> Self {
        Self {
            connection,
            options,
            attempt_permit: None,
        }
    }

    pub(crate) fn with_attempt_permit(
        connection: JsonRpcConnection,
        options: ExecServerClientConnectOptions,
        permit: OwnedSemaphorePermit,
    ) -> Self {
        Self {
            connection,
            options,
            attempt_permit: Some(permit),
        }
    }

    pub(crate) fn into_parts(
        self,
    ) -> (
        JsonRpcConnection,
        ExecServerClientConnectOptions,
        Option<OwnedSemaphorePermit>,
    ) {
        (self.connection, self.options, self.attempt_permit)
    }
}

/// Reopens the transport for one logical exec-server client session.
///
/// URL connections reuse their configured endpoint.
#[derive(Clone)]
pub(crate) struct NoiseReconnectStrategy {
    provider: Arc<dyn NoiseRendezvousConnectProvider>,
    identity: NoiseChannelIdentity,
    http_client_factory: HttpClientFactory,
}

impl NoiseReconnectStrategy {
    async fn connect_args(
        &self,
        resume_session_id: Option<String>,
    ) -> Result<NoiseRendezvousConnectArgs, ExecServerError> {
        let bundle = self
            .provider
            .connect_bundle(self.identity.public_key())
            .await?;
        Ok(NoiseRendezvousConnectArgs {
            bundle,
            harness_identity: self.identity.clone(),
            client_name: ENVIRONMENT_CLIENT_NAME.to_string(),
            connect_timeout: crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_CONNECT_TIMEOUT,
            initialize_timeout: crate::client_api::DEFAULT_REMOTE_EXEC_SERVER_INITIALIZE_TIMEOUT,
            resume_session_id,
            http_client_factory: self.http_client_factory.clone(),
        })
    }
}

#[derive(Clone)]
pub(crate) enum ExecServerReconnectStrategy {
    WebSocket(RemoteExecServerConnectArgs),
    Noise(NoiseReconnectStrategy),
    Accepted(crate::client::accepted::AcceptedConnectionSource),
}

impl ExecServerReconnectStrategy {
    pub(crate) async fn resume(
        &self,
        session_id: &str,
    ) -> Result<ReconnectAttempt, ExecServerError> {
        match self {
            Self::WebSocket(args) => {
                let mut args = args.clone();
                args.resume_session_id = Some(session_id.to_string());
                let connection = ExecServerClient::open_websocket_connection(&args).await?;
                Ok(ReconnectAttempt::new(connection, args.into()))
            }
            Self::Noise(strategy) => {
                let args = strategy.connect_args(Some(session_id.to_string())).await?;
                let (connection, options) = ExecServerClient::open_noise_connection(args).await?;
                Ok(ReconnectAttempt::new(connection, options))
            }
            Self::Accepted(source) => source.next_connection(session_id).await,
        }
    }
}

impl ExecServerClient {
    /// Open the selected transport and run the common JSON-RPC initialization.
    pub(crate) async fn connect_for_transport(
        transport_params: ExecServerTransportParams,
        http_client_factory: HttpClientFactory,
    ) -> Result<Self, ExecServerError> {
        let (websocket_url, connect_timeout, initialize_timeout) = match transport_params {
            ExecServerTransportParams::WebSocketUrl {
                websocket_url,
                connect_timeout,
                initialize_timeout,
            } => (websocket_url, connect_timeout, initialize_timeout),
            ExecServerTransportParams::NoiseRendezvous { provider, identity } => {
                let strategy = NoiseReconnectStrategy {
                    provider,
                    identity,
                    http_client_factory,
                };
                let args = strategy.connect_args(None).await?;
                let (connection, options) = Self::open_noise_connection(args).await?;
                return Self::connect_with_recovery(
                    connection,
                    options,
                    Some(ExecServerReconnectStrategy::Noise(strategy)),
                )
                .await;
            }
            ExecServerTransportParams::StdioCommand {
                command,
                initialize_timeout,
            } => {
                return Self::connect_stdio_command(StdioExecServerConnectArgs {
                    command,
                    client_name: ENVIRONMENT_CLIENT_NAME.to_string(),
                    initialize_timeout,
                    resume_session_id: None,
                })
                .await;
            }
        };
        Self::connect_websocket(RemoteExecServerConnectArgs {
            websocket_url,
            client_name: ENVIRONMENT_CLIENT_NAME.to_string(),
            connect_timeout,
            initialize_timeout,
            resume_session_id: None,
            http_client_factory,
        })
        .await
    }

    pub async fn connect_noise_rendezvous_transport(
        args: NoiseRendezvousConnectArgs,
    ) -> Result<JsonRpcConnection, ExecServerError> {
        Self::open_noise_connection(args)
            .await
            .map(|(connection, _)| connection)
    }

    /// 使用 Registry 签发的一次性材料建立 Noise Relay；公开单次连接不隐式重放授权。
    pub async fn connect_noise_rendezvous(
        args: NoiseRendezvousConnectArgs,
    ) -> Result<Self, ExecServerError> {
        let (connection, options) = Self::open_noise_connection(args).await?;
        Self::connect_with_recovery(connection, options, None).await
    }

    async fn open_noise_connection(
        args: NoiseRendezvousConnectArgs,
    ) -> Result<(JsonRpcConnection, ExecServerClientConnectOptions), ExecServerError> {
        ensure_rustls_crypto_provider();
        let options = ExecServerClientConnectOptions {
            client_name: args.client_name.clone(),
            initialize_timeout: args.initialize_timeout,
            resume_session_id: args.resume_session_id.clone(),
        };
        let NoiseRendezvousConnectBundle {
            websocket_url,
            environment_id,
            executor_registration_id,
            executor_public_key,
            harness_key_authorization,
        } = args.bundle;
        let diagnostic_url = websocket_url
            .split(['?', '#'])
            .next()
            .unwrap_or(websocket_url.as_str())
            .to_string();
        let mut request = websocket_url
            .as_str()
            .into_client_request()
            .map_err(|source| ExecServerError::WebSocketConnect {
                url: diagnostic_url.clone(),
                source,
            })?;
        request.headers_mut().extend(current_rendezvous_headers());
        let connector = WebSocketConnector::new_with_tls_mode(
            &args.http_client_factory,
            WebSocketTlsMode::TungsteniteDefault,
        )
        .map_err(|error| ExecServerError::WebSocketConfiguration(error.to_string()))?
        .with_tcp_nodelay();
        let stream = timeout(
            args.connect_timeout,
            connector.connect(request, noise_relay_websocket_config()),
        )
        .await
        .map_err(|_| ExecServerError::WebSocketConnectTimeout {
            url: diagnostic_url.clone(),
            timeout: args.connect_timeout,
        })?
        .map_err(|source| ExecServerError::WebSocketConnect {
            url: diagnostic_url.clone(),
            source,
        })?
        .0;
        let connection = noise_harness_connection_from_websocket_with_readiness(
            stream,
            NoiseHarnessConnectionArgs {
                connection_label: format!("Nova exec-server Noise rendezvous {diagnostic_url}"),
                environment_id,
                executor_registration_id,
                identity: args.harness_identity,
                responder_public_key: executor_public_key,
                harness_key_authorization,
            },
        );
        timeout(args.initialize_timeout, connection.handshake_ready)
            .await
            .map_err(|_| ExecServerError::InitializeTimedOut {
                timeout: args.initialize_timeout,
            })?
            .map_err(|_| ExecServerError::Closed)?;
        Ok((connection.connection, options))
    }

    pub async fn connect_websocket(
        args: RemoteExecServerConnectArgs,
    ) -> Result<Self, ExecServerError> {
        let connection = Self::open_websocket_connection(&args).await?;
        let options = args.clone().into();
        Self::connect_with_recovery(
            connection,
            options,
            Some(ExecServerReconnectStrategy::WebSocket(args)),
        )
        .await
    }

    pub(crate) async fn open_websocket_connection(
        args: &RemoteExecServerConnectArgs,
    ) -> Result<JsonRpcConnection, ExecServerError> {
        ensure_rustls_crypto_provider();
        let websocket_url = args.websocket_url.clone();
        let connect_timeout = args.connect_timeout;
        let request = websocket_url
            .as_str()
            .into_client_request()
            .map_err(|source| ExecServerError::WebSocketConnect {
                url: websocket_url.clone(),
                source,
            })?;
        let connector = WebSocketConnector::new_with_tls_mode(
            &args.http_client_factory,
            WebSocketTlsMode::TungsteniteDefault,
        )
        .map_err(|error| ExecServerError::WebSocketConfiguration(error.to_string()))?;
        let (stream, _) = timeout(
            connect_timeout,
            connector.connect(
                request,
                tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default(),
            ),
        )
        .await
        .map_err(|_| ExecServerError::WebSocketConnectTimeout {
            url: websocket_url.clone(),
            timeout: connect_timeout,
        })?
        .map_err(|source| ExecServerError::WebSocketConnect {
            url: websocket_url.clone(),
            source,
        })?;

        let connection_label = format!("exec-server websocket {websocket_url}");
        Ok(JsonRpcConnection::from_websocket(stream, connection_label))
    }

    pub(crate) async fn connect_stdio_command(
        args: StdioExecServerConnectArgs,
    ) -> Result<Self, ExecServerError> {
        let mut child = stdio_command_process(&args.command)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(ExecServerError::Spawn)?;

        let stdin = child.stdin.take().ok_or_else(|| {
            ExecServerError::Protocol("spawned exec-server command has no stdin".to_string())
        })?;
        let stdout = child.stdout.take().ok_or_else(|| {
            ExecServerError::Protocol("spawned exec-server command has no stdout".to_string())
        })?;
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(async move {
                let mut reader = BufReader::new(stderr);
                let mut line = Vec::new();
                loop {
                    line.clear();
                    match (&mut reader)
                        .take(MAX_STDIO_STDERR_LOG_LINE_LEN)
                        .read_until(b'\n', &mut line)
                        .await
                    {
                        Ok(0) => break,
                        Ok(_) => {
                            let line = line.strip_suffix(b"\n").unwrap_or(&line);
                            let line = line.strip_suffix(b"\r").unwrap_or(line);
                            let line = String::from_utf8_lossy(line);
                            debug!("exec-server stdio stderr: {line}");
                        }
                        Err(err) => {
                            warn!("failed to read exec-server stdio stderr: {err}");
                            break;
                        }
                    }
                }
            });
        }

        Self::connect(
            JsonRpcConnection::client_from_stdio(
                stdout,
                stdin,
                "exec-server stdio command".to_string(),
            )
            .with_child_process(child),
            args.into(),
        )
        .await
    }
}

fn stdio_command_process(stdio_command: &StdioExecServerCommand) -> Command {
    // 对位 codex 39f4277087 client_transport.rs：经 background_command 构造（Windows 下
    // CREATE_NO_WINDOW 抑制控制台分配）。偏差说明：nova 的对位物内联在
    // exec-server-windows-sandbox crate，exec-server 仅在 Windows 目标依赖该 crate；
    // 非 Windows 平台 background_command 语义本就等同 Command::new，故直接构造。
    #[cfg(windows)]
    let mut command = Command::from(
        nova_exec_server_windows_sandbox::background_command::background_command(
            &stdio_command.program,
        ),
    );
    #[cfg(not(windows))]
    let mut command = Command::new(&stdio_command.program);
    command.args(&stdio_command.args);
    command.envs(&stdio_command.env);
    scrub_non_inheritable_env_vars(command.as_std_mut());
    if let Some(cwd) = &stdio_command.cwd {
        command.current_dir(cwd);
    }
    #[cfg(unix)]
    command.process_group(0);
    command
}
