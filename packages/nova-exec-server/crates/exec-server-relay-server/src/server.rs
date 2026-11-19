use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Context as _;
use axum::Json;
use axum::Router as AxumRouter;
use axum::extract::Path;
use axum::extract::Query;
use axum::extract::State;
use axum::extract::ws::Message;
use axum::extract::ws::WebSocket;
use axum::extract::ws::WebSocketUpgrade;
use axum::http::HeaderMap;
use axum::http::StatusCode;
use axum::http::header::AUTHORIZATION;
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::get;
use axum::routing::post;
use futures::SinkExt as _;
use futures::StreamExt as _;
use nova_exec_server_relay_protocol::EnvironmentRegistryConnectRequest;
use nova_exec_server_relay_protocol::EnvironmentRegistryConnectResponse;
use nova_exec_server_relay_protocol::EnvironmentRegistryHarnessKeyValidationRequest;
use nova_exec_server_relay_protocol::EnvironmentRegistryHarnessKeyValidationResponse;
use nova_exec_server_relay_protocol::EnvironmentRegistryRegistrationRequest;
use nova_exec_server_relay_protocol::EnvironmentRegistryRegistrationResponse;
use nova_exec_server_relay_protocol::NOISE_RELAY_SECURITY_PROFILE;
use nova_exec_server_relay_protocol::RelayMessageFrame;
use prost::Message as _;
use serde::Deserialize;
use serde::Serialize;
use tokio::net::TcpListener;
use url::Url;

use crate::registry::Registry;
use crate::registry::RegistryError;
use crate::rendezvous::RendezvousError;
use crate::rendezvous::RendezvousRouter;
use crate::rendezvous::Role;

const MAX_RELAY_WEBSOCKET_MESSAGE_BYTES: usize = 256 * 1024;

#[derive(Clone, Debug)]
pub struct RelayServerConfig {
    pub listen: SocketAddr,
    pub database_path: PathBuf,
    /// 对外 HTTPS 源站；缺省仅适用于回环测试。
    pub public_url: Option<String>,
    pub authorization_check_interval: Duration,
}

impl RelayServerConfig {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.authorization_check_interval.is_zero()
            || self.authorization_check_interval > Duration::from_secs(60)
        {
            anyhow::bail!("authorization check interval must be in (0, 60s]");
        }
        if let Some(public_url) = &self.public_url {
            let parsed = Url::parse(public_url).context("invalid relay public URL")?;
            if !matches!(parsed.scheme(), "http" | "https")
                || parsed.host_str().is_none()
                || !parsed.username().is_empty()
                || parsed.password().is_some()
                || parsed.query().is_some()
                || parsed.fragment().is_some()
            {
                anyhow::bail!("relay public URL must be an absolute HTTP(S) origin");
            }
            if !self.listen.ip().is_loopback() && parsed.scheme() != "https" {
                anyhow::bail!("public relay deployment requires an HTTPS public URL");
            }
        } else if !self.listen.ip().is_loopback() {
            anyhow::bail!("public relay deployment requires --public-url");
        }
        Ok(())
    }

    fn rendezvous_url(
        &self,
        role: Role,
        environment_id: &str,
        registration_id: &str,
        secret_name: &str,
        secret: &str,
    ) -> Result<String, RegistryError> {
        let base = self.public_url.clone().unwrap_or_else(|| {
            let scheme = "http";
            format!("{scheme}://{}", self.listen)
        });
        let mut url = Url::parse(&base).map_err(RegistryError::internal)?;
        let websocket_scheme = match url.scheme() {
            "https" => "wss",
            "http" => "ws",
            _ => {
                return Err(RegistryError::internal("unsupported public URL scheme"));
            }
        };
        url.set_scheme(websocket_scheme)
            .map_err(|_| RegistryError::internal("invalid WebSocket public URL"))?;
        let root = url.path().trim_end_matches('/').to_string();
        url.set_path(&format!(
            "{root}/v1/rendezvous/{}",
            match role {
                Role::Environment => "environment",
                Role::Harness => "harness",
            }
        ));
        url.query_pairs_mut()
            .append_pair("environment_id", environment_id)
            .append_pair("registration_id", registration_id)
            .append_pair(secret_name, secret)
            .append_pair(
                "role",
                match role {
                    Role::Environment => "environment",
                    Role::Harness => "harness",
                },
            );
        Ok(url.into())
    }
}

#[derive(Clone)]
struct AppState {
    registry: Registry,
    rendezvous: RendezvousRouter,
    config: RelayServerConfig,
}

pub async fn run_server(config: RelayServerConfig) -> anyhow::Result<()> {
    config.validate()?;
    let registry = Registry::open(&config.database_path)?;
    let app = build_router(config.clone(), registry, RendezvousRouter::default());
    let listener = TcpListener::bind(config.listen)
        .await
        .with_context(|| format!("failed to bind relay listener {}", config.listen))?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

pub fn build_router(
    config: RelayServerConfig,
    registry: Registry,
    rendezvous: RendezvousRouter,
) -> AxumRouter {
    build_router_with_dev_ssh(
        config,
        registry,
        rendezvous,
        crate::dev_ssh::DevSshRouter::default(),
    )
}

pub fn build_router_with_dev_ssh(
    config: RelayServerConfig,
    registry: Registry,
    rendezvous: RendezvousRouter,
    dev_ssh: crate::dev_ssh::DevSshRouter,
) -> AxumRouter {
    let dev_ssh_router = crate::dev_ssh::server::router(
        crate::dev_ssh::server::DevSshServerState::new(registry.clone(), config.clone(), dev_ssh),
    );
    let state = AppState {
        registry,
        rendezvous,
        config,
    };
    AxumRouter::new()
        .route("/healthz", get(health))
        .route(
            "/cloud/environment/{environment_id}/register",
            post(register),
        )
        .route("/cloud/environment/{environment_id}/connect", post(connect))
        .route(
            "/cloud/environment/{environment_id}/validate",
            post(validate),
        )
        .route("/v1/rendezvous/environment", get(environment_socket))
        .route("/v1/rendezvous/harness", get(harness_socket))
        .with_state(Arc::new(state))
        .merge(dev_ssh_router)
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": true}))
}

async fn register(
    State(state): State<Arc<AppState>>,
    Path(environment_id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<EnvironmentRegistryRegistrationRequest>,
) -> Result<Json<EnvironmentRegistryRegistrationResponse>, Response> {
    let token = bearer_token(&headers).map_err(error_response)?;
    let grant = state
        .registry
        .register(environment_id, token, request)
        .await
        .map_err(error_response)?;
    let url = state
        .config
        .rendezvous_url(
            Role::Environment,
            &grant.environment_id,
            &grant.registration_id,
            "executor_token",
            &grant.executor_token,
        )
        .map_err(error_response)?;
    Ok(Json(EnvironmentRegistryRegistrationResponse {
        environment_id: grant.environment_id,
        url,
        security_profile: NOISE_RELAY_SECURITY_PROFILE.to_string(),
        executor_registration_id: grant.registration_id,
    }))
}

async fn connect(
    State(state): State<Arc<AppState>>,
    Path(environment_id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<EnvironmentRegistryConnectRequest>,
) -> Result<Json<EnvironmentRegistryConnectResponse>, Response> {
    let token = bearer_token(&headers).map_err(error_response)?;
    let grant = state
        .registry
        .connect(environment_id, token, request)
        .await
        .map_err(error_response)?;
    let url = state
        .config
        .rendezvous_url(
            Role::Harness,
            &grant.environment_id,
            &grant.registration_id,
            "harness_key_authorization",
            &grant.harness_key_authorization,
        )
        .map_err(error_response)?;
    Ok(Json(EnvironmentRegistryConnectResponse {
        environment_id: grant.environment_id,
        url,
        security_profile: NOISE_RELAY_SECURITY_PROFILE.to_string(),
        executor_registration_id: grant.registration_id,
        executor_public_key: grant.executor_public_key,
        harness_key_authorization: grant.harness_key_authorization,
    }))
}

async fn validate(
    State(state): State<Arc<AppState>>,
    Path(environment_id): Path<String>,
    headers: HeaderMap,
    Json(request): Json<EnvironmentRegistryHarnessKeyValidationRequest>,
) -> Result<Json<EnvironmentRegistryHarnessKeyValidationResponse>, Response> {
    let token = bearer_token(&headers).map_err(error_response)?;
    let valid = state
        .registry
        .validate_harness(environment_id, token, request)
        .await
        .map_err(error_response)?;
    Ok(Json(EnvironmentRegistryHarnessKeyValidationResponse {
        valid,
    }))
}

#[derive(Debug, Deserialize)]
struct SocketParams {
    environment_id: String,
    registration_id: String,
    executor_token: Option<String>,
    harness_key_authorization: Option<String>,
    role: Option<String>,
}

async fn environment_socket(
    State(state): State<Arc<AppState>>,
    Query(params): Query<SocketParams>,
    upgrade: WebSocketUpgrade,
) -> Result<Response, Response> {
    require_role(params.role.as_deref(), "environment").map_err(status_response)?;
    let token = params
        .executor_token
        .clone()
        .ok_or_else(|| status_response(StatusCode::UNAUTHORIZED))?;
    if !state
        .registry
        .executor_authorized(
            params.environment_id.clone(),
            params.registration_id.clone(),
            token,
        )
        .await
        .map_err(error_response)?
    {
        return Err(status_response(StatusCode::FORBIDDEN));
    }
    let registry_physical_id = state
        .registry
        .physical_connected(
            params.environment_id.clone(),
            params.registration_id.clone(),
        )
        .await
        .map_err(error_response)?;
    let failed_state = Arc::clone(&state);
    let failed_environment_id = params.environment_id.clone();
    let failed_registration_id = params.registration_id.clone();
    let failed_physical_id = registry_physical_id.clone();
    let upgrade = upgrade.on_failed_upgrade(move |_error| {
        tokio::spawn(async move {
            let _ = failed_state
                .registry
                .physical_disconnected(
                    failed_environment_id,
                    failed_registration_id,
                    failed_physical_id,
                )
                .await;
        });
    });
    Ok(upgrade.on_upgrade(move |socket| async move {
        serve_socket(
            state,
            socket,
            params.environment_id,
            params.registration_id,
            Role::Environment,
            Some(registry_physical_id),
        )
        .await;
    }))
}

async fn harness_socket(
    State(state): State<Arc<AppState>>,
    Query(params): Query<SocketParams>,
    upgrade: WebSocketUpgrade,
) -> Result<Response, Response> {
    require_role(params.role.as_deref(), "harness").map_err(status_response)?;
    let authorization = params
        .harness_key_authorization
        .clone()
        .ok_or_else(|| status_response(StatusCode::UNAUTHORIZED))?;
    if !state
        .registry
        .harness_authorized(
            params.environment_id.clone(),
            params.registration_id.clone(),
            authorization,
        )
        .await
        .map_err(error_response)?
    {
        return Err(status_response(StatusCode::FORBIDDEN));
    }
    Ok(upgrade.on_upgrade(move |socket| async move {
        serve_socket(
            state,
            socket,
            params.environment_id,
            params.registration_id,
            Role::Harness,
            None,
        )
        .await;
    }))
}

fn require_role(actual: Option<&str>, expected: &'static str) -> Result<(), StatusCode> {
    if actual.is_some_and(|actual| actual != expected) {
        return Err(StatusCode::FORBIDDEN);
    }
    Ok(())
}

async fn serve_socket(
    state: Arc<AppState>,
    socket: WebSocket,
    environment_id: String,
    registration_id: String,
    role: Role,
    registry_physical_id: Option<String>,
) {
    let (physical, mut outgoing) =
        match state
            .rendezvous
            .connect(environment_id.clone(), registration_id.clone(), role)
        {
            Ok(connected) => connected,
            Err(_) => {
                if let Some(physical_id) = registry_physical_id {
                    let _ = state
                        .registry
                        .physical_disconnected(environment_id, registration_id, physical_id)
                        .await;
                }
                return;
            }
        };
    let (mut socket_outgoing, mut socket_incoming) = socket.split();
    let mut authorization_check = tokio::time::interval(state.config.authorization_check_interval);
    authorization_check.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    authorization_check.tick().await;
    loop {
        tokio::select! {
            _ = physical.closed() => break,
            _ = authorization_check.tick() => {
                match state.registry.registration_active(environment_id.clone(), registration_id.clone()).await {
                    Ok(true) => {}
                    Ok(false) | Err(_) => break,
                }
            }
            frame = outgoing.recv() => {
                let Some(frame) = frame else { break; };
                physical.complete_send(&frame.stream_id);
                let bytes = frame.encode_to_vec();
                if socket_outgoing.send(Message::Binary(bytes.into())).await.is_err() {
                    break;
                }
            }
            message = socket_incoming.next() => {
                let Some(message) = message else { break; };
                match message {
                    Ok(Message::Binary(bytes)) => {
                        if bytes.len() > MAX_RELAY_WEBSOCKET_MESSAGE_BYTES {
                            break;
                        }
                        let Ok(frame) = RelayMessageFrame::decode(bytes.as_ref()) else {
                            break;
                        };
                        if let Err(error) = state.rendezvous.forward(&physical, frame.clone()) {
                            match error {
                                RendezvousError::StreamOverloaded => {
                                    state.rendezvous.abort_stream(&frame.stream_id);
                                }
                                _ => break,
                            }
                        }
                    }
                    Ok(Message::Ping(payload)) => {
                        if socket_outgoing.send(Message::Pong(payload)).await.is_err() {
                            break;
                        }
                    }
                    Ok(Message::Pong(_)) => {}
                    Ok(Message::Close(_)) | Err(_) => break,
                    Ok(Message::Text(_)) => break,
                }
            }
        }
    }
    state.rendezvous.close_physical(&physical);
    if let Some(physical_id) = registry_physical_id {
        let _ = state
            .registry
            .physical_disconnected(environment_id, registration_id, physical_id)
            .await;
    }
}

pub(crate) fn bearer_token(headers: &HeaderMap) -> Result<String, RegistryError> {
    let value = headers
        .get(AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            RegistryError::new(
                StatusCode::UNAUTHORIZED,
                "unauthorized",
                "Bearer token is required",
            )
        })?;
    Ok(value.to_string())
}

pub(crate) fn error_response(error: RegistryError) -> Response {
    let message = if error.internal {
        tracing::error!(
            kind = error.kind,
            message = error.message,
            "Relay internal error"
        );
        "Internal relay error".to_string()
    } else {
        error.message
    };
    (
        error.status,
        Json(ErrorBody {
            error: ErrorDetail {
                code: error.kind,
                message,
            },
        }),
    )
        .into_response()
}

fn status_response(status: StatusCode) -> Response {
    status.into_response()
}

#[derive(Serialize)]
struct ErrorBody {
    error: ErrorDetail,
}

#[derive(Serialize)]
struct ErrorDetail {
    code: &'static str,
    message: String,
}
