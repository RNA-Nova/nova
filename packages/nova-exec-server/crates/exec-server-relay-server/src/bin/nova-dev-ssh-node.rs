use std::net::SocketAddr;

use anyhow::Result;
use clap::Parser;
use nova_exec_server_relay_server::DevSshNodeConfig;
use nova_exec_server_relay_server::run_dev_ssh_node;

#[derive(Debug, Parser)]
#[command(name = "nova-dev-ssh-node")]
#[command(about = "Development-only OpenSSH reverse tunnel node")]
struct Cli {
    #[arg(long, value_name = "HTTP(S)_ORIGIN")]
    registry_url: String,
    #[arg(long, value_name = "ID")]
    environment_id: String,
    #[arg(long, env = "NOVA_DEV_SSH_NODE_TOKEN", hide_env_values = true)]
    node_token: String,
    #[arg(long, default_value = "127.0.0.1:22")]
    target: SocketAddr,
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
    run_dev_ssh_node(DevSshNodeConfig::new(
        cli.registry_url,
        cli.environment_id,
        cli.node_token,
        cli.target,
    )?)
    .await
}
