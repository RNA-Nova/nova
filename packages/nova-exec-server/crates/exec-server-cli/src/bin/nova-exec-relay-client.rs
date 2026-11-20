use std::time::Duration;

use anyhow::Context as _;
use anyhow::Result;
use clap::Parser;
use nova_exec_server::ExecServerClient;
use nova_exec_server::JsonRpcConnection;
use nova_exec_server::JsonRpcConnectionEvent;
use nova_exec_server::NoiseChannelIdentity;
use nova_exec_server::NoiseRendezvousConnectArgs;
use nova_exec_server::NoiseRendezvousEnvironmentConfig;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use nova_exec_server_protocol::JSONRPCMessage;

#[derive(Debug, Parser)]
#[command(name = "nova-exec-relay-client")]
#[command(about = "Bridge Nova Noise Relay to exec-server stdio JSON-RPC")]
struct Cli {
    #[arg(long, value_name = "URL")]
    registry_url: String,
    #[arg(long, value_name = "ID", env = "NOVA_EXEC_SERVER_ENVIRONMENT_ID")]
    environment_id: String,
    #[arg(long, env = "NOVA_EXEC_SERVER_HARNESS_TOKEN", hide_env_values = true)]
    harness_token: String,
    #[arg(long, default_value = "nova-exec-relay-client")]
    client_name: String,
    #[arg(long, default_value_t = 10)]
    connect_timeout_sec: u64,
    #[arg(long, default_value_t = 10)]
    initialize_timeout_sec: u64,
}

fn main() -> Result<()> {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .init();
    let runtime = tokio::runtime::Runtime::new()?;
    let result = runtime.block_on(run());
    // tokio::io::stdin 的底层阻塞读不能随 future abort 取消。父进程仍握着
    // stdin 写端时，默认 Runtime drop 会等这条阻塞读，导致桥主逻辑已结束、
    // 进程却不退出，Python 监督器也就收不到 EOF。显式后台关闭运行时，让
    // 已完成的传输收尾立即体现为子进程退出。
    runtime.shutdown_background();
    result
}

async fn run() -> Result<()> {
    let cli = Cli::parse();
    let factory = HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault);
    let provider = NoiseRendezvousEnvironmentConfig::new(
        cli.registry_url,
        cli.environment_id,
        cli.harness_token,
        None,
    )?
    .into_connect_provider(factory.clone())?;
    let identity = NoiseChannelIdentity::generate()?;
    let bundle = provider.connect_bundle(identity.public_key()).await?;
    let remote = ExecServerClient::connect_noise_rendezvous_transport(NoiseRendezvousConnectArgs {
        bundle,
        harness_identity: identity,
        client_name: cli.client_name,
        connect_timeout: Duration::from_secs(cli.connect_timeout_sec),
        initialize_timeout: Duration::from_secs(cli.initialize_timeout_sec),
        resume_session_id: None,
        http_client_factory: factory,
    })
    .await
    .context("failed to establish Nova Noise Relay transport")?;
    let local = JsonRpcConnection::relay_bridge_from_stdio(
        tokio::io::stdin(),
        tokio::io::stdout(),
        "nova-exec-relay-stdio".to_string(),
    );
    bridge(local, remote).await?;
    tracing::debug!("Nova Relay bridge main finished");
    Ok(())
}

async fn bridge(local: JsonRpcConnection, remote: JsonRpcConnection) -> Result<()> {
    let JsonRpcConnection {
        outgoing_tx: local_outgoing,
        incoming_rx: mut local_incoming,
        task_handles: local_tasks,
        ..
    } = local;
    let JsonRpcConnection {
        outgoing_tx: remote_outgoing,
        incoming_rx: mut remote_incoming,
        task_handles: remote_tasks,
        ..
    } = remote;
    let result = loop {
        tokio::select! {
            event = local_incoming.recv() => {
                let Some(event) = event else { break Ok(()); };
                match connection_message(event) {
                    Ok(Some(message)) => {
                        if remote_outgoing.send(message).await.is_err() {
                            break Ok(());
                        }
                    }
                    Ok(None) => break Ok(()),
                    Err(error) => break Err(error),
                }
            }
            event = remote_incoming.recv() => {
                let Some(event) = event else {
                    tracing::debug!("Nova Relay bridge remote incoming channel closed");
                    break Ok(());
                };
                match connection_message(event) {
                    Ok(Some(message)) => {
                        if local_outgoing.send(message).await.is_err() {
                            break Ok(());
                        }
                    }
                    Ok(None) => {
                        tracing::debug!("Nova Relay bridge remote side disconnected");
                        break Ok(());
                    }
                    Err(error) => break Err(error),
                }
            }
        }
    };
    drop(local_outgoing);
    drop(remote_outgoing);
    for task in local_tasks.into_iter().chain(remote_tasks) {
        task.abort();
        let _ = task.await;
    }
    result
}

fn connection_message(event: JsonRpcConnectionEvent) -> Result<Option<JSONRPCMessage>> {
    match event {
        JsonRpcConnectionEvent::Message(message) => Ok(Some(message)),
        JsonRpcConnectionEvent::QueuedRequest { request, .. } => {
            Ok(Some(JSONRPCMessage::Request(request)))
        }
        JsonRpcConnectionEvent::Disconnected { .. } => Ok(None),
        JsonRpcConnectionEvent::MalformedMessage { reason, .. } => {
            anyhow::bail!("Relay peer sent malformed JSON-RPC: {reason}")
        }
    }
}
