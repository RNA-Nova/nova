use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::Duration;

use anyhow::Context as _;
use futures::SinkExt as _;
use futures::StreamExt as _;
use http::HeaderValue;
use http::header::AUTHORIZATION;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use nova_exec_server_websocket_client::WebSocketConnector;
use nova_exec_server_websocket_client::WebSocketTlsMode;
use tokio::io::AsyncReadExt as _;
use tokio::io::AsyncWriteExt as _;
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest as _;

use super::DevSshFrame;
use super::MAX_DEV_SSH_DATA_BYTES;
use super::dev_ssh_frame;
use super::web_socket_config;

const MAX_NODE_STREAMS: usize = 64;
const STREAM_INPUT_CAPACITY: usize = 32;
const NODE_OUTPUT_CAPACITY: usize = 256;

#[derive(Clone)]
pub struct DevSshNodeConfig {
    pub registry_url: String,
    pub environment_id: String,
    pub node_token: String,
    pub target: SocketAddr,
    pub http_client_factory: HttpClientFactory,
}

impl DevSshNodeConfig {
    pub fn new(
        registry_url: String,
        environment_id: String,
        node_token: String,
        target: SocketAddr,
    ) -> anyhow::Result<Self> {
        if !target.ip().is_loopback() {
            anyhow::bail!("dev-ssh node target must be a loopback address");
        }
        Ok(Self {
            registry_url,
            environment_id,
            node_token,
            target,
            http_client_factory: HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        })
    }
}

pub async fn run_dev_ssh_node(config: DevSshNodeConfig) -> anyhow::Result<()> {
    let mut url = url::Url::parse(&config.registry_url).context("invalid Registry URL")?;
    let scheme = match url.scheme() {
        "http" => "ws",
        "https" => "wss",
        scheme => anyhow::bail!("unsupported Registry URL scheme: {scheme}"),
    };
    url.set_scheme(scheme)
        .map_err(|_| anyhow::anyhow!("invalid dev-ssh node WebSocket URL"))?;
    let root = url.path().trim_end_matches('/').to_string();
    url.set_path(&format!("{root}/v1/dev-ssh/node"));
    url.query_pairs_mut()
        .append_pair("environment_id", &config.environment_id);
    let mut request = url.as_str().into_client_request()?;
    let mut authorization = HeaderValue::from_str(&format!("Bearer {}", config.node_token))?;
    authorization.set_sensitive(true);
    request.headers_mut().insert(AUTHORIZATION, authorization);
    let connector = WebSocketConnector::new_with_tls_mode(
        &config.http_client_factory,
        WebSocketTlsMode::TungsteniteDefault,
    )?
    .with_tcp_nodelay();
    let (socket, _) = connector.connect(request, web_socket_config()).await?;
    let (mut socket_outgoing, mut socket_incoming) = socket.split();
    let (outgoing, mut outgoing_rx) = mpsc::channel::<DevSshFrame>(NODE_OUTPUT_CAPACITY);
    let mut streams: HashMap<String, mpsc::Sender<Vec<u8>>> = HashMap::new();
    let mut tasks: Vec<tokio::task::JoinHandle<()>> = Vec::new();
    loop {
        tokio::select! {
            frame = outgoing_rx.recv() => {
                let Some(frame) = frame else { break; };
                socket_outgoing.send(Message::Binary(frame.encode()?.into())).await?;
            }
            message = socket_incoming.next() => {
                let Some(message) = message else { break; };
                match message? {
                    Message::Binary(bytes) => {
                        let frame = DevSshFrame::decode(bytes.as_ref())?;
                        match frame.body {
                            Some(dev_ssh_frame::Body::Open(_)) => {
                                tasks.retain(|task| !task.is_finished());
                                if streams.len() >= MAX_NODE_STREAMS || streams.contains_key(&frame.stream_id) {
                                    outgoing.send(DevSshFrame::open_result(
                                        frame.stream_id,
                                        Some("stream unavailable".to_string()),
                                    )).await?;
                                    continue;
                                }
                                let (input, input_rx) = mpsc::channel(STREAM_INPUT_CAPACITY);
                                streams.insert(frame.stream_id.clone(), input);
                                tasks.push(tokio::spawn(run_node_stream(
                                    frame.stream_id,
                                    config.target,
                                    outgoing.clone(),
                                    input_rx,
                                )));
                            }
                            Some(dev_ssh_frame::Body::Data(data)) => {
                                if let Some(input) = streams.get(&frame.stream_id)
                                    && input.try_send(data.payload).is_err()
                                {
                                    streams.remove(&frame.stream_id);
                                    outgoing.send(DevSshFrame::close(
                                        frame.stream_id,
                                        "stream_overloaded".to_string(),
                                    )).await?;
                                }
                            }
                            Some(dev_ssh_frame::Body::Close(_)) => {
                                streams.remove(&frame.stream_id);
                            }
                            Some(dev_ssh_frame::Body::OpenResult(_)) | None => break,
                        }
                    }
                    Message::Ping(payload) => socket_outgoing.send(Message::Pong(payload)).await?,
                    Message::Pong(_) => {}
                    Message::Close(_) => break,
                    Message::Text(_) | Message::Frame(_) => break,
                }
            }
        }
    }
    drop(streams);
    drop(outgoing);
    for task in tasks {
        task.abort();
    }
    Ok(())
}

async fn run_node_stream(
    stream_id: String,
    target: SocketAddr,
    outgoing: mpsc::Sender<DevSshFrame>,
    mut input: mpsc::Receiver<Vec<u8>>,
) {
    let socket =
        match tokio::time::timeout(Duration::from_secs(10), TcpStream::connect(target)).await {
            Ok(Ok(socket)) => socket,
            Ok(Err(error)) => {
                let _ = outgoing
                    .send(DevSshFrame::open_result(
                        stream_id,
                        Some(safe_diagnostic(error)),
                    ))
                    .await;
                return;
            }
            Err(_) => {
                let _ = outgoing
                    .send(DevSshFrame::open_result(
                        stream_id,
                        Some("target connection timed out".to_string()),
                    ))
                    .await;
                return;
            }
        };
    if outgoing
        .send(DevSshFrame::open_result(stream_id.clone(), None))
        .await
        .is_err()
    {
        return;
    }
    let (mut reader, mut writer) = socket.into_split();
    let mut buffer = vec![0_u8; MAX_DEV_SSH_DATA_BYTES];
    loop {
        tokio::select! {
            read = reader.read(&mut buffer) => {
                match read {
                    Ok(0) | Err(_) => break,
                    Ok(size) => {
                        let Ok(frame) = DevSshFrame::data(stream_id.clone(), buffer[..size].to_vec()) else { break; };
                        if outgoing.send(frame).await.is_err() { return; }
                    }
                }
            }
            payload = input.recv() => {
                let Some(payload) = payload else { break; };
                if writer.write_all(&payload).await.is_err() { break; }
            }
        }
    }
    let _ = outgoing
        .send(DevSshFrame::close(stream_id, "target_closed".to_string()))
        .await;
}

fn safe_diagnostic(error: impl std::fmt::Display) -> String {
    error
        .to_string()
        .chars()
        .filter(|character| !character.is_control())
        .take(256)
        .collect()
}
