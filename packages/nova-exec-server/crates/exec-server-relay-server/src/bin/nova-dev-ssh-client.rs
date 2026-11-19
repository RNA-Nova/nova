use std::net::SocketAddr;

use anyhow::Result;
use clap::Parser;
use nova_exec_server_relay_server::DevSshClientConfig;
use nova_exec_server_relay_server::run_dev_ssh_stdio;
use nova_exec_server_relay_server::serve_dev_ssh_listener;
use tokio::net::TcpListener;

#[derive(Debug, Parser)]
#[command(name = "nova-dev-ssh-client")]
#[command(about = "Development-only OpenSSH reverse tunnel client")]
struct Cli {
    #[arg(long, value_name = "HTTP(S)_ORIGIN")]
    registry_url: String,
    #[arg(long, value_name = "ID")]
    environment_id: String,
    #[arg(long, env = "NOVA_DEV_SSH_CLIENT_TOKEN", hide_env_values = true)]
    client_token: String,
    #[arg(long, conflicts_with = "stdio")]
    listen: Option<SocketAddr>,
    #[arg(long)]
    stdio: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();
    let cli = Cli::parse();
    let config = DevSshClientConfig::new(cli.registry_url, cli.environment_id, cli.client_token);
    if cli.stdio {
        return run_dev_ssh_stdio(config).await;
    }
    let listen = cli
        .listen
        .unwrap_or_else(|| "127.0.0.1:2222".parse().expect("default listen"));
    if !listen.ip().is_loopback() {
        anyhow::bail!("dev-ssh client must listen on a loopback address");
    }
    let listener = TcpListener::bind(listen).await?;
    eprintln!("dev-ssh listening on {}", listener.local_addr()?);
    serve_dev_ssh_listener(config, listener).await
}
