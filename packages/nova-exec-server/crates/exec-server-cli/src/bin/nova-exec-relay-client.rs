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

#[tokio::main]
async fn main() -> Result<()> {
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
    let local = JsonRpcConnection::client_from_stdio(
        tokio::io::stdin(),
        tokio::io::stdout(),
        "nova-exec-relay-stdio".to_string(),
    );
    bridge(local, remote).await?;
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
                let Some(event) = event else { break Ok(()); };
                match connection_message(event) {
                    Ok(Some(message)) => {
                        if local_outgoing.send(message).await.is_err() {
                            break Ok(());
                        }
                    }
                    Ok(None) => break Ok(()),
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
