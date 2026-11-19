use std::collections::HashMap;
use std::time::Duration;

use nova_exec_server::ExecParams;
use nova_exec_server::ExecServerClient;
use nova_exec_server::ExecServerRuntimePaths;
use nova_exec_server::NoiseChannelIdentity;
use nova_exec_server::NoiseRendezvousConnectArgs;
use nova_exec_server::NoiseRendezvousEnvironmentConfig;
use nova_exec_server::ProcessId;
use nova_exec_server::ReadParams;
use nova_exec_server::RegistryBearerAuth;
use nova_exec_server::RemoteEnvironmentConfig;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use nova_exec_server_relay_server::Registry;
use nova_exec_server_relay_server::RelayServerConfig;
use nova_exec_server_relay_server::server::build_router;
use nova_exec_server_utils_path_uri::PathUri;

#[cfg(windows)]
fn relay_echo_argv() -> Vec<String> {
    vec![
        "cmd.exe".to_string(),
        "/C".to_string(),
        "echo relay-e2e".to_string(),
    ]
}

#[cfg(not(windows))]
fn relay_echo_argv() -> Vec<String> {
    vec!["/bin/echo".to_string(), "relay-e2e".to_string()]
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn nova_registry_rendezvous_and_noise_endpoints_execute_a_real_process() {
    let directory = tempfile::tempdir().expect("tempdir");
    let registry = Registry::open(directory.path().join("registry.sqlite3")).expect("registry");
    let tokens = registry
        .enroll("machine-a".to_string(), "tenant-a".to_string())
        .await
        .expect("enroll");
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("bind relay");
    let address = listener.local_addr().expect("relay address");
    let config = RelayServerConfig {
        listen: address,
        database_path: directory.path().join("unused.sqlite3"),
        public_url: None,
        authorization_check_interval: Duration::from_millis(50),
    };
    let app = build_router(
        config,
        registry,
        nova_exec_server_relay_server::RendezvousRouter::default(),
    );
    let server_task = tokio::spawn(async move {
        axum::serve(listener, app).await.expect("serve relay");
    });
    let registry_url = format!("http://{address}");
    let factory = HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault);
    let remote_config = RemoteEnvironmentConfig::new(
        registry_url.clone(),
        "machine-a".to_string(),
        RegistryBearerAuth::shared(&tokens.node_token).expect("node auth"),
        factory.clone(),
    )
    .expect("remote config");
    let runtime_paths =
        ExecServerRuntimePaths::new(std::env::current_exe().expect("current exe"), None)
            .expect("runtime paths");
    let (shutdown_tx, mut shutdown_rx) = tokio::sync::watch::channel(false);
    let remote_task = tokio::spawn(async move {
        nova_exec_server::run_remote_environment_until_shutdown(
            remote_config,
            runtime_paths,
            async move {
                let _ = shutdown_rx.changed().await;
            },
        )
        .await
    });

    let provider = NoiseRendezvousEnvironmentConfig::new(
        registry_url,
        "machine-a".to_string(),
        tokens.harness_token,
        None,
    )
    .expect("harness config")
    .into_connect_provider(factory.clone())
    .expect("connect provider");
    let identity = NoiseChannelIdentity::generate().expect("harness identity");
    let mut client = None;
    for _ in 0..100 {
        let Ok(bundle) = provider.connect_bundle(identity.public_key()).await else {
            tokio::time::sleep(Duration::from_millis(20)).await;
            continue;
        };
        match ExecServerClient::connect_noise_rendezvous(NoiseRendezvousConnectArgs {
            bundle,
            harness_identity: identity.clone(),
            client_name: "nova-relay-e2e".to_string(),
            connect_timeout: Duration::from_secs(2),
            initialize_timeout: Duration::from_secs(2),
            resume_session_id: None,
            http_client_factory: factory.clone(),
        })
        .await
        {
            Ok(connected) => {
                client = Some(connected);
                break;
            }
            Err(_) => tokio::time::sleep(Duration::from_millis(20)).await,
        }
    }
    let client = client.expect("connect harness through real Relay");
    client.environment_info().await.expect("environment info");
    let process_id = ProcessId::new("relay-process");
    client
        .exec(ExecParams {
            process_id: process_id.clone(),
            metadata: None,
            argv: relay_echo_argv(),
            cwd: PathUri::from_host_native_path(directory.path()).expect("cwd URI"),
            env_policy: None,
            shell_snapshot: None,
            env: HashMap::new(),
            tty: false,
            pipe_stdin: false,
            arg0: None,
            sandbox: None,
            enforce_managed_network: false,
            managed_network: None,
            network_proxy: None,
        })
        .await
        .expect("start process");
    let mut output = Vec::new();
    let mut exited = false;
    for _ in 0..50 {
        let response = client
            .read(ReadParams {
                process_id: process_id.clone(),
                after_seq: None,
                max_bytes: Some(64 * 1024),
                wait_ms: Some(100),
            })
            .await
            .expect("read process");
        output.extend(
            response
                .chunks
                .iter()
                .flat_map(|chunk| chunk.chunk.as_ref().to_vec()),
        );
        exited = response.exited;
        if exited {
            break;
        }
    }
    assert!(exited, "process did not exit");
    assert!(
        String::from_utf8_lossy(&output).contains("relay-e2e"),
        "unexpected output: {}",
        String::from_utf8_lossy(&output)
    );

    drop(client);
    let _ = shutdown_tx.send(true);
    remote_task.abort();
    server_task.abort();
}
