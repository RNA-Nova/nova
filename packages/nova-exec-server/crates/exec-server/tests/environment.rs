//! 环境注册表集成测试（对位 codex `exec-server/tests/environment.rs`——
//! capabilityRoots 相关系列随协议清洗剔除，仅保留系统代理用例）。

mod common;

use std::time::Duration;

use anyhow::Context;
use common::exec_server::exec_server;
use nova_exec_server::EnvironmentManager;
use nova_exec_server::REMOTE_ENVIRONMENT_ID;
use nova_exec_server_http_client::HttpClientFactory;
use nova_exec_server_http_client::OutboundProxyPolicy;
use nova_exec_server_http_client::cache_system_proxy_route_for_test;
use pretty_assertions::assert_eq;
use tokio::io::AsyncReadExt;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio::time::timeout;
use tokio_util::task::AbortOnDropHandle;

/// 对位 codex 39f4277087 tests/environment.rs 同名用例：经注册表准备的远程环境
/// 走配置的系统代理（nova 的注册表条目住 `<config home>/config.toml` 顶层三键，
/// 而非独立 environments.toml——词表差异见 environment_toml.rs 文件头）。
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[serial_test::serial(remote_exec_server)]
async fn prepared_remote_environment_uses_configured_system_proxy() -> anyhow::Result<()> {
    let server = exec_server().await?;
    let upstream = server
        .websocket_url()
        .strip_prefix("ws://")
        .context("exec-server websocket should use ws://")?
        .to_string();
    let proxy_listener = TcpListener::bind("127.0.0.1:0").await?;
    let proxy_url = format!("http://{}", proxy_listener.local_addr()?);
    let websocket_url = "ws://exec-server-system-proxy.invalid:8765/";
    let proxy_resolution_url = "http://exec-server-system-proxy.invalid:8765/";
    cache_system_proxy_route_for_test(proxy_resolution_url, proxy_url);

    let (request_tx, request_rx) = oneshot::channel();
    let _proxy_task = AbortOnDropHandle::new(tokio::spawn(async move {
        let (mut client, _) = proxy_listener.accept().await?;
        let mut request = Vec::new();
        let mut byte = [0_u8; 1];
        while !request.ends_with(b"\r\n\r\n") {
            client.read_exact(&mut byte).await?;
            request.push(byte[0]);
        }
        let request_line = String::from_utf8(request)?
            .lines()
            .next()
            .context("system proxy should receive a CONNECT request")?
            .to_string();
        request_tx
            .send(request_line)
            .map_err(|_| anyhow::anyhow!("system proxy request receiver was dropped"))?;

        let mut target = TcpStream::connect(upstream).await?;
        client
            .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            .await?;
        tokio::io::copy_bidirectional(&mut client, &mut target).await?;
        Ok::<(), anyhow::Error>(())
    }));

    let config_home = tempfile::tempdir()?;
    std::fs::write(
        config_home.path().join("config.toml"),
        format!(
            "default_environment = \"{REMOTE_ENVIRONMENT_ID}\"\ninclude_local = false\n\n[[environments]]\nid = \"{REMOTE_ENVIRONMENT_ID}\"\nurl = \"{websocket_url}\"\n"
        ),
    )?;

    let prepared = EnvironmentManager::prepare_from_config_home(config_home.path()).await?;
    assert!(prepared.default_environment_is_remote());
    let manager = prepared.build(
        /*local_runtime_paths*/ None,
        HttpClientFactory::new(OutboundProxyPolicy::RespectSystemProxy),
    )?;

    let request_line = timeout(Duration::from_secs(5), request_rx)
        .await
        .context("prepared environment did not connect through the system proxy")??;
    assert_eq!(
        request_line,
        "CONNECT exec-server-system-proxy.invalid:8765 HTTP/1.1"
    );
    let environment = manager
        .default_environment()
        .context("prepared remote environment")?;
    timeout(Duration::from_secs(5), environment.info())
        .await
        .context("prepared remote environment did not initialize through the system proxy")??;

    Ok(())
}
