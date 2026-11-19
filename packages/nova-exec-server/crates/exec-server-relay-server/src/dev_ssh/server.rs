use std::sync::Arc;

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
use axum::response::IntoResponse;
use axum::response::Response;
use axum::routing::get;
use axum::routing::post;
use futures::SinkExt as _;
use futures::StreamExt as _;
use serde::Deserialize;
use url::Url;

use super::DevSshConnectResponse;
use super::DevSshFrame;
use super::DevSshRouter;
use super::dev_ssh_frame;
use crate::registry::Registry;
use crate::registry::RegistryError;
use crate::server::RelayServerConfig;
use crate::server::bearer_token;
use crate::server::error_response;

#[derive(Clone)]
pub(crate) struct DevSshServerState {
    registry: Registry,
    config: RelayServerConfig,
    router: DevSshRouter,
}

impl DevSshServerState {
    pub(crate) fn new(registry: Registry, config: RelayServerConfig, router: DevSshRouter) -> Self {
        Self {
            registry,
            config,
            router,
        }
    }
}

pub(crate) fn router(state: DevSshServerState) -> AxumRouter {
    AxumRouter::new()
        .route(
            "/cloud/environment/{environment_id}/dev-ssh/connect",
            post(connect),
        )
        .route("/v1/dev-ssh/node", get(node_socket))
        .route("/v1/dev-ssh/client", get(client_socket))
        .with_state(Arc::new(state))
}

#[derive(Deserialize)]
struct NodeParams {
    environment_id: String,
}

#[derive(Deserialize)]
struct ClientParams {
    environment_id: String,
    authorization: String,
}

async fn connect(
    State(state): State<Arc<DevSshServerState>>,
    Path(environment_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    let token = match bearer_token(&headers) {
        Ok(token) => token,
        Err(error) => return error_response(error),
    };
    match state
        .registry
        .connect_dev_ssh(environment_id.clone(), token)
        .await
    {
        Ok(authorization) => match client_url(&state.config, &environment_id, &authorization) {
            Ok(url) => Json(DevSshConnectResponse {
                environment_id,
                url,
            })
            .into_response(),
            Err(error) => error_response(error),
        },
        Err(error) => error_response(error),
    }
}

fn client_url(
    config: &RelayServerConfig,
    environment_id: &str,
    authorization: &str,
) -> Result<String, RegistryError> {
    let base = config
        .public_url
        .clone()
        .unwrap_or_else(|| format!("http://{}", config.listen));
    let mut url = Url::parse(&base).map_err(RegistryError::internal)?;
    url.set_scheme(if url.scheme() == "https" { "wss" } else { "ws" })
        .map_err(|_| RegistryError::internal("invalid dev-ssh WebSocket URL"))?;
    let root = url.path().trim_end_matches('/').to_string();
    url.set_path(&format!("{root}/v1/dev-ssh/client"));
    url.query_pairs_mut()
        .append_pair("environment_id", environment_id)
        .append_pair("authorization", authorization);
    Ok(url.into())
}

async fn node_socket(
    State(state): State<Arc<DevSshServerState>>,
    Query(params): Query<NodeParams>,
    headers: HeaderMap,
    upgrade: WebSocketUpgrade,
) -> Response {
    let token = match bearer_token(&headers) {
        Ok(token) => token,
        Err(error) => return error_response(error),
    };
    match state
        .registry
        .dev_ssh_node_authorized(params.environment_id.clone(), token.clone())
        .await
    {
        Ok(true) => {}
        Ok(false) => return StatusCode::FORBIDDEN.into_response(),
        Err(error) => return error_response(error),
    }
    if state.router.node_online(&params.environment_id) {
        return error_response(RegistryError::new(
            StatusCode::CONFLICT,
            "dev_ssh_node_conflict",
            "A dev-ssh node is already connected",
        ));
    }
    let environment_id = params.environment_id;
    upgrade
        .on_upgrade(move |socket| async move {
            serve_node(state, socket, environment_id, token).await;
        })
        .into_response()
}

async fn client_socket(
    State(state): State<Arc<DevSshServerState>>,
    Query(params): Query<ClientParams>,
    upgrade: WebSocketUpgrade,
) -> Response {
    match state
        .registry
        .dev_ssh_client_authorized(params.environment_id.clone(), params.authorization.clone())
        .await
    {
        Ok(true) => {}
        Ok(false) => return StatusCode::FORBIDDEN.into_response(),
        Err(error) => return error_response(error),
    }
    upgrade
        .on_upgrade(move |socket| async move {
            serve_client(state, socket, params.environment_id).await;
        })
        .into_response()
}

async fn serve_node(
    state: Arc<DevSshServerState>,
    socket: WebSocket,
    environment_id: String,
    token: String,
) {
    let (node, mut outgoing) = match state.router.connect_node(environment_id.clone()) {
        Ok(connected) => connected,
        Err(_) => return,
    };
    let (mut socket_outgoing, mut socket_incoming) = socket.split();
    let mut authorization_check = tokio::time::interval(state.config.authorization_check_interval);
    authorization_check.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    authorization_check.tick().await;
    loop {
        tokio::select! {
            _ = authorization_check.tick() => {
                match state.registry.dev_ssh_node_authorized(environment_id.clone(), token.clone()).await {
                    Ok(true) => {}
                    Ok(false) | Err(_) => break,
                }
            }
            frame = outgoing.recv() => {
                let Some(frame) = frame else { break; };
                let Ok(bytes) = frame.encode() else { break; };
                if socket_outgoing.send(Message::Binary(bytes.into())).await.is_err() {
                    break;
                }
            }
            message = socket_incoming.next() => {
                let Some(message) = message else { break; };
                match message {
                    Ok(Message::Binary(bytes)) => {
                        let Ok(frame) = DevSshFrame::decode(bytes.as_ref()) else { break; };
                        if matches!(&frame.body, Some(dev_ssh_frame::Body::Open(_))) {
                            break;
                        }
                        let stream_id = frame.stream_id.clone();
                        match state.router.forward_from_node(&node.id, frame) {
                            Ok(()) | Err(super::router::DevSshRouteError::UnknownStream) => {}
                            Err(super::router::DevSshRouteError::Overloaded) => {
                                state.router.drop_stream(&stream_id);
                                let _ = node.outgoing.try_send(DevSshFrame::close(
                                    stream_id,
                                    "client_overloaded".to_string(),
                                ));
                            }
                            Err(_) => break,
                        }
                    }
                    Ok(Message::Ping(payload)) => {
                        if socket_outgoing.send(Message::Pong(payload)).await.is_err() { break; }
                    }
                    Ok(Message::Pong(_)) => {}
                    Ok(Message::Close(_)) | Err(_) => break,
                    Ok(Message::Text(_)) => break,
                }
            }
        }
    }
    state.router.close_node(&node);
}

async fn serve_client(state: Arc<DevSshServerState>, socket: WebSocket, environment_id: String) {
    let (client, mut outgoing) = match state.router.connect_client(environment_id) {
        Ok(connected) => connected,
        Err(_) => return,
    };
    if state
        .router
        .forward_from_client(&client, DevSshFrame::open(client.stream_id.clone()))
        .is_err()
    {
        state.router.close_client(&client);
        return;
    }
    let (mut socket_outgoing, mut socket_incoming) = socket.split();
    loop {
        tokio::select! {
            frame = outgoing.recv() => {
                let Some(frame) = frame else { break; };
                let terminal = match &frame.body {
                    Some(dev_ssh_frame::Body::Close(_)) => true,
                    Some(dev_ssh_frame::Body::OpenResult(result)) => !result.ok,
                    _ => false,
                };
                let Ok(bytes) = frame.encode() else { break; };
                if socket_outgoing.send(Message::Binary(bytes.into())).await.is_err() || terminal {
                    break;
                }
            }
            message = socket_incoming.next() => {
                let Some(message) = message else { break; };
                match message {
                    Ok(Message::Binary(bytes)) => {
                        let Ok(frame) = DevSshFrame::decode(bytes.as_ref()) else { break; };
                        match &frame.body {
                            Some(dev_ssh_frame::Body::Data(_)) => {
                                if state.router.forward_from_client(&client, frame).is_err() { break; }
                            }
                            Some(dev_ssh_frame::Body::Close(_)) => break,
                            _ => break,
                        }
                    }
                    Ok(Message::Ping(payload)) => {
                        if socket_outgoing.send(Message::Pong(payload)).await.is_err() { break; }
                    }
                    Ok(Message::Pong(_)) => {}
                    Ok(Message::Close(_)) | Err(_) => break,
                    Ok(Message::Text(_)) => break,
                }
            }
        }
    }
    state.router.close_client(&client);
}
