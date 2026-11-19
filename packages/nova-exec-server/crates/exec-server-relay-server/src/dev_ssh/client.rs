use std::time::Duration;

use futures::SinkExt as _;
use futures::StreamExt as _;
use http::HeaderMap;
use http::HeaderValue;
use http::header::AUTHORIZATION;
use nova_exec_server_http_client::ClientRouteClass;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use nova_exec_server_http_client::RouteAwareClientPool;
use nova_exec_server_websocket_client::WebSocketConnection;
use nova_exec_server_websocket_client::WebSocketConnector;
use nova_exec_server_websocket_client::WebSocketTlsMode;
use tokio::io::AsyncRead;
use tokio::io::AsyncReadExt as _;
use tokio::io::AsyncWrite;
use tokio::io::AsyncWriteExt as _;
use tokio::net::TcpListener;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest as _;

use super::DevSshConnectResponse;
use super::DevSshFrame;
use super::MAX_DEV_SSH_DATA_BYTES;
use super::dev_ssh_frame;
use super::web_socket_config;

#[derive(Clone)]
pub struct DevSshClientConfig {
    pub registry_url: String,
    pub environment_id: String,
    pub client_token: String,
    pub http_client_factory: HttpClientFactory,
}

impl DevSshClientConfig {
    pub fn new(registry_url: String, environment_id: String, client_token: String) -> Self {
        Self {
            registry_url,
            environment_id,
            client_token,
            http_client_factory: HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
        }
    }
}

pub async fn serve_dev_ssh_listener(
    config: DevSshClientConfig,
    listener: TcpListener,
) -> anyhow::Result<()> {
    let capacity = std::sync::Arc::new(tokio::sync::Semaphore::new(64));
    loop {
        let (socket, _) = listener.accept().await?;
        let permit = capacity.clone().acquire_owned().await?;
        let config = config.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(error) = run_dev_ssh_tcp(config, socket).await {
                tracing::warn!(error = %error, "dev-ssh client session failed");
            }
        });
    }
}

pub async fn run_dev_ssh_stdio(config: DevSshClientConfig) -> anyhow::Result<()> {
    let mut stdin = tokio::io::stdin();
    let mut stdout = tokio::io::stdout();
    run_dev_ssh_io(config, &mut stdin, &mut stdout).await
}

pub async fn run_dev_ssh_tcp(config: DevSshClientConfig, socket: TcpStream) -> anyhow::Result<()> {
    let (mut reader, mut writer) = socket.into_split();
    run_dev_ssh_io(config, &mut reader, &mut writer).await
}

async fn run_dev_ssh_io<R, W>(
    config: DevSshClientConfig,
    reader: &mut R,
    writer: &mut W,
) -> anyhow::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let socket = connect_client(&config).await?;
    let (mut socket_outgoing, mut socket_incoming) = socket.split();
    let first = tokio::time::timeout(Duration::from_secs(10), socket_incoming.next())
        .await
        .map_err(|_| anyhow::anyhow!("dev-ssh target open timed out"))?
        .ok_or_else(|| anyhow::anyhow!("dev-ssh target closed before opening"))??;
    let stream_id = match first {
        Message::Binary(bytes) => {
            let frame = DevSshFrame::decode(bytes.as_ref())?;
            match frame.body {
                Some(dev_ssh_frame::Body::OpenResult(result)) if result.ok => frame.stream_id,
                Some(dev_ssh_frame::Body::OpenResult(result)) => {
                    anyhow::bail!("dev-ssh target open failed: {}", result.error)
                }
                _ => anyhow::bail!("dev-ssh target did not return an open result"),
            }
        }
        _ => anyhow::bail!("dev-ssh target did not return a binary open result"),
    };

    let mut buffer = vec![0_u8; MAX_DEV_SSH_DATA_BYTES];
    loop {
        tokio::select! {
            read = reader.read(&mut buffer) => {
                let size = read?;
                if size == 0 { break; }
                let frame = DevSshFrame::data(stream_id.clone(), buffer[..size].to_vec())?;
                socket_outgoing.send(Message::Binary(frame.encode()?.into())).await?;
            }
            message = socket_incoming.next() => {
                let Some(message) = message else { break; };
                match message? {
                    Message::Binary(bytes) => {
                        let frame = DevSshFrame::decode(bytes.as_ref())?;
                        match frame.body {
                            Some(dev_ssh_frame::Body::Data(data)) => writer.write_all(&data.payload).await?,
                            Some(dev_ssh_frame::Body::Close(_)) => break,
                            _ => anyhow::bail!("invalid dev-ssh client frame"),
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
    let close = DevSshFrame::close(stream_id, "client_eof".to_string());
    let _ = socket_outgoing
        .send(Message::Binary(close.encode()?.into()))
        .await;
    writer.flush().await?;
    Ok(())
}

async fn connect_client(config: &DevSshClientConfig) -> anyhow::Result<WebSocketConnection> {
    let mut url = url::Url::parse(&config.registry_url)?;
    if !matches!(url.scheme(), "http" | "https") {
        anyhow::bail!("dev-ssh Registry URL must use HTTP(S)");
    }
    let root = url.path().trim_matches('/').to_string();
    url.set_query(None);
    url.set_fragment(None);
    {
        let mut segments = url
            .path_segments_mut()
            .map_err(|_| anyhow::anyhow!("dev-ssh Registry URL cannot be a base"))?;
        segments.clear();
        for segment in root.split('/').filter(|segment| !segment.is_empty()) {
            segments.push(segment);
        }
        segments
            .push("cloud")
            .push("environment")
            .push(&config.environment_id)
            .push("dev-ssh")
            .push("connect");
    }
    let pool = RouteAwareClientPool::new_without_redirects_or_request_logging(
        config.http_client_factory.clone(),
        ClientRouteClass::Other,
    );
    let mut authorization = HeaderValue::from_str(&format!("Bearer {}", config.client_token))?;
    authorization.set_sensitive(true);
    let mut headers = HeaderMap::new();
    headers.insert(AUTHORIZATION, authorization);
    let response = pool.post(url).headers(headers).send().await?;
    let status = response.status();
    if !status.is_success() {
        anyhow::bail!("dev-ssh client authorization failed ({status})");
    }
    let grant: DevSshConnectResponse = response.json().await?;
    if grant.environment_id != config.environment_id {
        anyhow::bail!("dev-ssh Registry returned a different environment id");
    }
    let request = grant.url.into_client_request()?;
    let connector = WebSocketConnector::new_with_tls_mode(
        &config.http_client_factory,
        WebSocketTlsMode::TungsteniteDefault,
    )?
    .with_tcp_nodelay();
    let (socket, _) = connector.connect(request, web_socket_config()).await?;
    Ok(socket)
}
