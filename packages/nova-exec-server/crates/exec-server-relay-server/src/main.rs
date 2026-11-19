use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use anyhow::Result;
use clap::Parser;
use clap::Subcommand;
use nova_exec_server_relay_server::Registry;
use nova_exec_server_relay_server::RelayServerConfig;
use nova_exec_server_relay_server::run_server;

#[derive(Debug, Parser)]
#[command(name = "nova-exec-relay-server")]
#[command(about = "Nova Registry and encrypted Rendezvous service")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Serve {
        #[arg(long, default_value = "127.0.0.1:8787")]
        listen: SocketAddr,
        #[arg(long, default_value_os_t = default_database_path())]
        database_path: PathBuf,
        #[arg(long, value_name = "HTTP(S)_ORIGIN")]
        public_url: Option<String>,
        #[arg(long, default_value_t = 1.0)]
        authorization_check_interval_sec: f64,
    },
    Enroll {
        #[arg(long, default_value_os_t = default_database_path())]
        database_path: PathBuf,
        #[arg(long, value_name = "ID")]
        environment_id: String,
        #[arg(long, default_value = "default")]
        tenant_id: String,
    },
    EnrollDevSsh {
        #[arg(long, default_value_os_t = default_database_path())]
        database_path: PathBuf,
        #[arg(long, value_name = "ID")]
        environment_id: String,
        #[arg(long, default_value = "default")]
        tenant_id: String,
    },
    RevokeDevSsh {
        #[arg(long, default_value_os_t = default_database_path())]
        database_path: PathBuf,
        #[arg(long, value_name = "ID")]
        environment_id: String,
    },
    Revoke {
        #[arg(long, default_value_os_t = default_database_path())]
        database_path: PathBuf,
        #[arg(long, value_name = "ID")]
        environment_id: String,
    },
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
    match Cli::parse().command {
        Command::Serve {
            listen,
            database_path,
            public_url,
            authorization_check_interval_sec,
        } => {
            if !authorization_check_interval_sec.is_finite()
                || authorization_check_interval_sec <= 0.0
            {
                anyhow::bail!("authorization check interval must be positive");
            }
            run_server(RelayServerConfig {
                listen,
                database_path,
                public_url,
                authorization_check_interval: Duration::from_secs_f64(
                    authorization_check_interval_sec,
                ),
            })
            .await?;
        }
        Command::Enroll {
            database_path,
            environment_id,
            tenant_id,
        } => {
            let tokens = Registry::open(database_path)?
                .enroll(environment_id.clone(), tenant_id)
                .await?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "environment_id": environment_id,
                    "node_token": tokens.node_token,
                    "harness_token": tokens.harness_token,
                }))?
            );
        }
        Command::EnrollDevSsh {
            database_path,
            environment_id,
            tenant_id,
        } => {
            let tokens = Registry::open(database_path)?
                .enroll_dev_ssh(environment_id.clone(), tenant_id)
                .await?;
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "environment_id": environment_id,
                    "node_token": tokens.node_token,
                    "client_token": tokens.client_token,
                }))?
            );
        }
        Command::RevokeDevSsh {
            database_path,
            environment_id,
        } => {
            let revoked = Registry::open(database_path)?
                .revoke_dev_ssh(environment_id)
                .await?;
            println!("{revoked}");
        }
        Command::Revoke {
            database_path,
            environment_id,
        } => {
            let revoked = Registry::open(database_path)?
                .revoke(environment_id)
                .await?;
            println!("{revoked}");
        }
    }
    Ok(())
}

fn default_database_path() -> PathBuf {
    nova_exec_server_utils_home_dir::find_nova_exec_server_home()
        .map(|home| home.join("relay").join("registry.sqlite3").to_path_buf())
        .unwrap_or_else(|_| PathBuf::from("nova-exec-relay-registry.sqlite3"))
}
