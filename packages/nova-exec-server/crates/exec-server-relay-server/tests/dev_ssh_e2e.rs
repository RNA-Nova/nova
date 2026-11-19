use std::time::Duration;

use anyhow::Context as _;
use nova_exec_server_relay_server::DevSshClientConfig;
use nova_exec_server_relay_server::DevSshNodeConfig;
use nova_exec_server_relay_server::DevSshRouter;
use nova_exec_server_relay_server::Registry;
use nova_exec_server_relay_server::RelayServerConfig;
use nova_exec_server_relay_server::RendezvousRouter;
use nova_exec_server_relay_server::run_dev_ssh_node;
use nova_exec_server_relay_server::run_dev_ssh_tcp;
use nova_exec_server_relay_server::server::build_router_with_dev_ssh;
use tokio::io::AsyncReadExt as _;
use tokio::io::AsyncWriteExt as _;
use tokio::net::TcpListener;
use tokio::net::TcpStream;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dev_ssh_routes_real_bytes_between_multiple_clients_and_revokes_cleanly()
-> anyhow::Result<()> {
    tokio::time::timeout(Duration::from_secs(30), dev_ssh_scenario())
        .await
        .context("dev-ssh E2E exceeded 30s")?
}

async fn dev_ssh_scenario() -> anyhow::Result<()> {
    let target = TcpListener::bind("127.0.0.1:0").await?;
    let target_address = target.local_addr()?;
    let target_task = tokio::spawn(async move {
        let mut connections = Vec::new();
        for _ in 0..2 {
            let (mut socket, _) = target.accept().await?;
            connections.push(tokio::spawn(async move {
                let mut buffer = [0_u8; 16 * 1024];
                loop {
                    let size = socket.read(&mut buffer).await?;
                    if size == 0 {
                        return anyhow::Ok(());
                    }
                    socket.write_all(&buffer[..size]).await?;
                }
            }));
        }
        for connection in connections {
            connection.await??;
        }
        anyhow::Ok(())
    });

    let directory = tempfile::tempdir()?;
    let registry = Registry::open(directory.path().join("registry.sqlite3"))?;
    let tokens = registry
        .enroll_dev_ssh("windows-dev".to_string(), "tenant-a".to_string())
        .await?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let dev_router = DevSshRouter::default();
    let app = build_router_with_dev_ssh(
        RelayServerConfig {
            listen: address,
            database_path: directory.path().join("unused.sqlite3"),
            public_url: None,
            authorization_check_interval: Duration::from_millis(50),
        },
        registry.clone(),
        RendezvousRouter::default(),
        dev_router.clone(),
    );
    let server_task = tokio::spawn(async move {
        axum::serve(listener, app).await?;
        anyhow::Ok(())
    });
    let registry_url = format!("http://{address}");
    let node_task = tokio::spawn(run_dev_ssh_node(DevSshNodeConfig::new(
        registry_url.clone(),
        "windows-dev".to_string(),
        tokens.node_token.clone(),
        target_address,
    )?));
    tokio::time::timeout(Duration::from_secs(5), async {
        while !dev_router.node_online("windows-dev") {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .context("dev-ssh node did not become online")?;

    eprintln!("dev-ssh E2E: invalid client");
    let bad_client = DevSshClientConfig::new(
        registry_url.clone(),
        "windows-dev".to_string(),
        "wrong-client-token".to_string(),
    );
    assert!(
        exercise_client(bad_client, b"must-not-connect")
            .await
            .is_err(),
        "an invalid client token must fail before opening the target"
    );

    eprintln!("dev-ssh E2E: valid clients");
    let client = || {
        DevSshClientConfig::new(
            registry_url.clone(),
            "windows-dev".to_string(),
            tokens.client_token.clone(),
        )
    };
    exercise_client(client(), b"first-ssh-ciphertext").await?;
    exercise_client(client(), b"second-ssh-ciphertext").await?;

    eprintln!("dev-ssh E2E: duplicate node");
    let second_node = tokio::time::timeout(
        Duration::from_secs(5),
        run_dev_ssh_node(DevSshNodeConfig::new(
            registry_url.clone(),
            "windows-dev".to_string(),
            tokens.node_token,
            target_address,
        )?),
    )
    .await
    .context("duplicate node did not settle")?;
    assert!(
        second_node.is_err(),
        "only one dev-ssh node may own an environment"
    );

    eprintln!("dev-ssh E2E: revocation");
    assert!(registry.revoke_dev_ssh("windows-dev".to_string()).await?);
    let _ = tokio::time::timeout(Duration::from_secs(2), node_task)
        .await
        .context("revocation did not disconnect the active node")??;
    assert!(
        exercise_client(client(), b"revoked").await.is_err(),
        "revoked client tokens must not create new streams"
    );

    eprintln!("dev-ssh E2E: target drain");
    target_task.await??;
    server_task.abort();
    Ok(())
}

async fn exercise_client(config: DevSshClientConfig, payload: &[u8]) -> anyhow::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (connecting, accepted) = tokio::join!(TcpStream::connect(address), listener.accept());
    let local = connecting?;
    let (socket, _) = accepted?;
    let client_task = tokio::spawn(run_dev_ssh_tcp(config, socket));
    let (mut reader, mut writer) = local.into_split();
    writer.write_all(payload).await?;
    let mut response = vec![0_u8; payload.len()];
    tokio::time::timeout(Duration::from_secs(5), reader.read_exact(&mut response))
        .await
        .context("dev-ssh response timed out")??;
    assert_eq!(response, payload);
    writer.shutdown().await?;
    tokio::time::timeout(Duration::from_secs(5), client_task)
        .await
        .context("dev-ssh client did not close")???;
    Ok(())
}
