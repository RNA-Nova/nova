//! SOCKS5 代理（移植自 codex network-proxy `socks5.rs`）。
//!
//! 裁点（第一期）：
//! - MITM 面：`SocksMitmMode` 与 `Socks5TcpConnection::Mitm/DetectTls` 变体未移植；
//!   SOCKS5 TCP 只做策略检查后的直接转发。`NetworkMode::None`（无网络访问）下
//!   TCP/UDP 一律拒绝（codex 的 Limited 语义在裁掉 MITM 后退化为全拒）。

use crate::attribution::BindConnectionAttribution;
use crate::connect_policy::TargetCheckedTcpConnector;
use crate::network_policy::BlockDecisionAuditEventArgs;
use crate::network_policy::emit_block_decision_audit_event;
use crate::network_policy::evaluate_host_policy;
use crate::policy::normalize_host;
use crate::reasons::REASON_METHOD_NOT_ALLOWED;
use crate::reasons::REASON_PROXY_DISABLED;
use crate::responses::PolicyDecisionDetails;
use crate::responses::blocked_message_with_policy;
use crate::state::BlockedRequestArgs;
use crate::state::NetworkProxyState;
use crate::BlockedRequest;
use crate::NetworkDecision;
use crate::NetworkDecisionSource;
use crate::NetworkMode;
use crate::NetworkPolicyDecider;
use crate::NetworkPolicyDecision;
use crate::NetworkPolicyRequest;
use crate::NetworkPolicyRequestArgs;
use crate::NetworkProtocol;
use anyhow::Context as _;
use anyhow::Result;
use rama_core::Service;
use rama_core::error::BoxError;
use rama_core::extensions::ExtensionsRef;
use rama_core::service::BoxService;
use rama_core::service::service_fn;
use rama_net::client::EstablishedClientConnection;
use rama_net::proxy::ProxyRequest;
use rama_net::proxy::StreamForwardService;
use rama_net::stream::SocketInfo;
use rama_socks5::Socks5Acceptor;
use rama_socks5::server::DefaultConnector;
use rama_socks5::server::DefaultUdpRelay;
use rama_socks5::server::udp::RelayRequest;
use rama_socks5::server::udp::RelayResponse;
use rama_tcp::TcpStream;
use rama_tcp::client::Request as TcpRequest;
use rama_tcp::server::TcpListener;
use std::io;
use std::net::SocketAddr;
use std::net::TcpListener as StdTcpListener;
use std::sync::Arc;
use std::time::Instant;
use tracing::error;
use tracing::info;
use tracing::warn;

pub async fn run_socks5(
    state: Arc<NetworkProxyState>,
    addr: SocketAddr,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
    enable_socks5_udp: bool,
) -> Result<()> {
    let listener = TcpListener::build()
        .bind(addr)
        .await
        // 与 `http_proxy.rs` 相同：先把 `BoxError` 包进 `OpaqueError` 再接 anyhow。
        .map_err(rama_core::error::OpaqueError::from)
        .map_err(anyhow::Error::from)
        .with_context(|| format!("bind SOCKS5 proxy: {addr}"))?;

    run_socks5_with_listener(
        state,
        listener,
        policy_decider,
        environment_id,
        enable_socks5_udp,
    )
    .await
}

pub async fn run_socks5_with_std_listener(
    state: Arc<NetworkProxyState>,
    listener: StdTcpListener,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
    enable_socks5_udp: bool,
) -> Result<()> {
    let listener =
        TcpListener::try_from(listener).context("convert std listener to SOCKS5 proxy listener")?;
    run_socks5_with_listener(
        state,
        listener,
        policy_decider,
        environment_id,
        enable_socks5_udp,
    )
    .await
}

async fn run_socks5_with_listener(
    state: Arc<NetworkProxyState>,
    listener: TcpListener,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
    enable_socks5_udp: bool,
) -> Result<()> {
    let addr = listener
        .local_addr()
        .context("read SOCKS5 listener local addr")?;

    info!("SOCKS5 proxy listening on {addr}");

    match state.network_mode().await {
        Ok(NetworkMode::None) => {
            info!("SOCKS5 TCP/UDP are blocked in none mode (network access disabled)");
        }
        Ok(NetworkMode::Proxy) => {}
        Err(err) => {
            warn!("failed to read network mode: {err}");
        }
    }

    listener
        .serve(socks5_proxy_service(
            state,
            policy_decider,
            environment_id,
            enable_socks5_udp,
        ))
        .await;
    Ok(())
}

pub(crate) fn socks5_proxy_service(
    state: Arc<NetworkProxyState>,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
    enable_socks5_udp: bool,
) -> BoxService<TcpStream, (), BoxError> {
    let tcp_connector = TargetCheckedTcpConnector::new(state.clone());
    let policy_tcp_connector = service_fn({
        let policy_decider = policy_decider.clone();
        let environment_id = environment_id.clone();
        move |req: TcpRequest| {
            let tcp_connector = tcp_connector.clone();
            let policy_decider = policy_decider.clone();
            let environment_id = environment_id.clone();
            async move { handle_socks5_tcp(req, tcp_connector, policy_decider, environment_id).await }
        }
    });

    // 裁点（MITM）：codex 的 `proxy_socks5_tcp` 还要分发 MITM 连接形态；裁掉后
    // 只剩直接转发一种。
    let socks_proxy = service_fn(|request: ProxyRequest<TcpStream, TcpStream>| async move {
        StreamForwardService::default()
            .serve(request)
            .await
            .map_err(|err| -> BoxError { err.into() })
    });
    let socks_connector = DefaultConnector::default()
        .with_connector(policy_tcp_connector)
        .with_service(socks_proxy);
    let base = Socks5Acceptor::new().with_connector(socks_connector);

    if enable_socks5_udp {
        let udp_state = state.clone();
        let udp_decider = policy_decider.clone();
        let udp_relay =
            DefaultUdpRelay::default().with_async_inspector(service_fn({
                let environment_id = environment_id.clone();
                move |request: RelayRequest| {
                    let udp_state = udp_state.clone();
                    let udp_decider = udp_decider.clone();
                    let environment_id = environment_id.clone();
                    async move {
                        inspect_socks5_udp(request, udp_state, udp_decider, environment_id).await
                    }
                }
            }));
        let socks_acceptor = base.with_udp_associator(udp_relay);
        BindConnectionAttribution::new(socks_acceptor, state, environment_id).boxed()
    } else {
        BindConnectionAttribution::new(base, state, environment_id).boxed()
    }
}

async fn handle_socks5_tcp(
    req: TcpRequest,
    tcp_connector: TargetCheckedTcpConnector,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
) -> Result<EstablishedClientConnection<TcpStream, TcpRequest>, BoxError> {
    let app_state = req
        .extensions()
        .get::<Arc<NetworkProxyState>>()
        .cloned()
        .ok_or_else(|| io::Error::other("missing state"))?;

    let host = normalize_host(&req.authority.host.to_string());
    let port = req.authority.port;
    if host.is_empty() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid host").into());
    }

    let client = req
        .extensions()
        .get::<SocketInfo>()
        .map(|info| info.peer_addr().to_string());

    match app_state.enabled().await {
        Ok(true) => {}
        Ok(false) => {
            emit_socks_block_decision_audit_event(
                &app_state,
                NetworkDecisionSource::ProxyState,
                REASON_PROXY_DISABLED,
                NetworkProtocol::Socks5Tcp,
                host.as_str(),
                port,
                client.as_deref(),
            );
            let details = PolicyDecisionDetails {
                decision: NetworkPolicyDecision::Deny,
                reason: REASON_PROXY_DISABLED,
                source: NetworkDecisionSource::ProxyState,
                protocol: NetworkProtocol::Socks5Tcp,
                host: &host,
                port,
            };
            let _ = app_state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: host.clone(),
                    reason: REASON_PROXY_DISABLED.to_string(),
                    client: client.clone(),
                    method: None,
                    mode: None,
                    protocol: "socks5".to_string(),
                    decision: Some(details.decision.as_str().to_string()),
                    source: Some(details.source.as_str().to_string()),
                    port: Some(port),
                }))
                .await;
            let client = client.as_deref().unwrap_or_default();
            warn!("SOCKS blocked; proxy disabled (client={client}, host={host})");
            return Err(policy_denied_error(REASON_PROXY_DISABLED, &details).into());
        }
        Err(err) => {
            error!("failed to read enabled state: {err}");
            return Err(io::Error::other("proxy error").into());
        }
    }

    let mode = match app_state.network_mode().await {
        Ok(mode) => mode,
        Err(err) => {
            error!("failed to evaluate method policy: {err}");
            return Err(io::Error::other("proxy error").into());
        }
    };
    // 裁点（MITM）：codex 在 Limited 模式下仅放行 :443 的 HTTPS 目标（交给 MITM 检查
    // 内层请求）；nova 的 None 模式下 SOCKS5 TCP 一律拒绝。
    if mode == NetworkMode::None {
        emit_socks_block_decision_audit_event(
            &app_state,
            NetworkDecisionSource::ModeGuard,
            REASON_METHOD_NOT_ALLOWED,
            NetworkProtocol::Socks5Tcp,
            host.as_str(),
            port,
            client.as_deref(),
        );
        let details = PolicyDecisionDetails {
            decision: NetworkPolicyDecision::Deny,
            reason: REASON_METHOD_NOT_ALLOWED,
            source: NetworkDecisionSource::ModeGuard,
            protocol: NetworkProtocol::Socks5Tcp,
            host: &host,
            port,
        };
        let _ = app_state
            .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                host: host.clone(),
                reason: REASON_METHOD_NOT_ALLOWED.to_string(),
                client: client.clone(),
                method: None,
                mode: Some(NetworkMode::None),
                protocol: "socks5".to_string(),
                decision: Some(details.decision.as_str().to_string()),
                source: Some(details.source.as_str().to_string()),
                port: Some(port),
            }))
            .await;
        let client = client.as_deref().unwrap_or_default();
        warn!(
            "SOCKS blocked; network mode denies tunneling (client={client}, host={host}, port={port})"
        );
        return Err(policy_denied_error(REASON_METHOD_NOT_ALLOWED, &details).into());
    }

    let request = NetworkPolicyRequest::new(NetworkPolicyRequestArgs {
        protocol: NetworkProtocol::Socks5Tcp,
        host: host.clone(),
        port,
        environment_id,
        client_addr: client.clone(),
        method: None,
        command: None,
        exec_policy_hint: None,
    });

    match evaluate_host_policy(&app_state, policy_decider.as_ref(), &request).await {
        Ok(NetworkDecision::Deny {
            reason,
            source,
            decision,
        }) => {
            let details = PolicyDecisionDetails {
                decision,
                reason: &reason,
                source,
                protocol: NetworkProtocol::Socks5Tcp,
                host: &host,
                port,
            };
            let _ = app_state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: host.clone(),
                    reason: reason.clone(),
                    client: client.clone(),
                    method: None,
                    mode: None,
                    protocol: "socks5".to_string(),
                    decision: Some(details.decision.as_str().to_string()),
                    source: Some(details.source.as_str().to_string()),
                    port: Some(port),
                }))
                .await;
            let client = client.as_deref().unwrap_or_default();
            warn!("SOCKS blocked (client={client}, host={host}, reason={reason})");
            return Err(policy_denied_error(&reason, &details).into());
        }
        Ok(NetworkDecision::Allow) => {
            let client = client.as_deref().unwrap_or_default();
            info!("SOCKS allowed (client={client}, host={host}, port={port})");
        }
        Err(err) => {
            error!("failed to evaluate host: {err}");
            return Err(io::Error::other("proxy error").into());
        }
    }

    // 裁点（MITM）：codex 在此处按 host hook / 凭证经纪选择 MITM 连接形态；裁掉后
    // 一律直接拨号。
    info!("SOCKS upstream dial started (host={host}, port={port})");
    let connect_started_at = Instant::now();
    let result = tcp_connector.serve(req).await;
    match &result {
        Ok(_) => info!(
            "SOCKS upstream dial established (host={host}, port={port}, elapsed_ms={})",
            connect_started_at.elapsed().as_millis()
        ),
        Err(_) => warn!(
            "SOCKS upstream dial failed (host={host}, port={port}, elapsed_ms={})",
            connect_started_at.elapsed().as_millis()
        ),
    }
    result
}

async fn inspect_socks5_udp(
    request: RelayRequest,
    state: Arc<NetworkProxyState>,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_id: Option<String>,
) -> io::Result<RelayResponse> {
    let RelayRequest {
        server_address,
        payload,
        extensions,
        ..
    } = request;

    let host = normalize_host(&server_address.ip_addr.to_string());
    let port = server_address.port;
    if host.is_empty() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid host"));
    }

    let client = extensions
        .get::<SocketInfo>()
        .map(|info| info.peer_addr().to_string());

    match state.enabled().await {
        Ok(true) => {}
        Ok(false) => {
            emit_socks_block_decision_audit_event(
                &state,
                NetworkDecisionSource::ProxyState,
                REASON_PROXY_DISABLED,
                NetworkProtocol::Socks5Udp,
                host.as_str(),
                port,
                client.as_deref(),
            );
            let details = PolicyDecisionDetails {
                decision: NetworkPolicyDecision::Deny,
                reason: REASON_PROXY_DISABLED,
                source: NetworkDecisionSource::ProxyState,
                protocol: NetworkProtocol::Socks5Udp,
                host: &host,
                port,
            };
            let _ = state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: host.clone(),
                    reason: REASON_PROXY_DISABLED.to_string(),
                    client: client.clone(),
                    method: None,
                    mode: None,
                    protocol: "socks5-udp".to_string(),
                    decision: Some(details.decision.as_str().to_string()),
                    source: Some(details.source.as_str().to_string()),
                    port: Some(port),
                }))
                .await;
            let client = client.as_deref().unwrap_or_default();
            warn!("SOCKS UDP blocked; proxy disabled (client={client}, host={host})");
            return Err(policy_denied_error(REASON_PROXY_DISABLED, &details));
        }
        Err(err) => {
            error!("failed to read enabled state: {err}");
            return Err(io::Error::other("proxy error"));
        }
    }

    match state.network_mode().await {
        Ok(NetworkMode::None) => {
            emit_socks_block_decision_audit_event(
                &state,
                NetworkDecisionSource::ModeGuard,
                REASON_METHOD_NOT_ALLOWED,
                NetworkProtocol::Socks5Udp,
                host.as_str(),
                port,
                client.as_deref(),
            );
            let details = PolicyDecisionDetails {
                decision: NetworkPolicyDecision::Deny,
                reason: REASON_METHOD_NOT_ALLOWED,
                source: NetworkDecisionSource::ModeGuard,
                protocol: NetworkProtocol::Socks5Udp,
                host: &host,
                port,
            };
            let _ = state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: host.clone(),
                    reason: REASON_METHOD_NOT_ALLOWED.to_string(),
                    client: client.clone(),
                    method: None,
                    mode: Some(NetworkMode::None),
                    protocol: "socks5-udp".to_string(),
                    decision: Some(details.decision.as_str().to_string()),
                    source: Some(details.source.as_str().to_string()),
                    port: Some(port),
                }))
                .await;
            return Err(policy_denied_error(REASON_METHOD_NOT_ALLOWED, &details));
        }
        Ok(NetworkMode::Proxy) => {}
        Err(err) => {
            error!("failed to evaluate method policy: {err}");
            return Err(io::Error::other("proxy error"));
        }
    }

    let request = NetworkPolicyRequest::new(NetworkPolicyRequestArgs {
        protocol: NetworkProtocol::Socks5Udp,
        host: host.clone(),
        port,
        environment_id,
        client_addr: client.clone(),
        method: None,
        command: None,
        exec_policy_hint: None,
    });

    match evaluate_host_policy(&state, policy_decider.as_ref(), &request).await {
        Ok(NetworkDecision::Deny {
            reason,
            source,
            decision,
        }) => {
            let details = PolicyDecisionDetails {
                decision,
                reason: &reason,
                source,
                protocol: NetworkProtocol::Socks5Udp,
                host: &host,
                port,
            };
            let _ = state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: host.clone(),
                    reason: reason.clone(),
                    client: client.clone(),
                    method: None,
                    mode: None,
                    protocol: "socks5-udp".to_string(),
                    decision: Some(details.decision.as_str().to_string()),
                    source: Some(details.source.as_str().to_string()),
                    port: Some(port),
                }))
                .await;
            let client = client.as_deref().unwrap_or_default();
            warn!("SOCKS UDP blocked (client={client}, host={host}, reason={reason})");
            Err(policy_denied_error(&reason, &details))
        }
        Ok(NetworkDecision::Allow) => Ok(RelayResponse {
            maybe_payload: Some(payload),
            extensions,
        }),
        Err(err) => {
            error!("failed to evaluate UDP host: {err}");
            Err(io::Error::other("proxy error"))
        }
    }
}

fn emit_socks_block_decision_audit_event(
    state: &NetworkProxyState,
    source: NetworkDecisionSource,
    reason: &str,
    protocol: NetworkProtocol,
    host: &str,
    port: u16,
    client_addr: Option<&str>,
) {
    emit_block_decision_audit_event(
        state,
        BlockDecisionAuditEventArgs {
            source,
            reason,
            protocol,
            server_address: host,
            server_port: port,
            method: None,
            client_addr,
        },
    );
}

fn policy_denied_error(reason: &str, details: &PolicyDecisionDetails<'_>) -> io::Error {
    io::Error::new(
        io::ErrorKind::PermissionDenied,
        blocked_message_with_policy(reason, details),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::NetworkProxyConfig;
    use crate::network_policy::test_support::POLICY_DECISION_EVENT_NAME;
    use crate::network_policy::test_support::capture_events;
    use crate::network_policy::test_support::find_event_by_name;
    use crate::runtime::ConfigReloader;
    use crate::runtime::ConfigReloaderFuture;
    use crate::runtime::ConfigState;
    use crate::state::NetworkProxyConstraints;
    use crate::state::build_config_state;
    use pretty_assertions::assert_eq;
    use rama_core::extensions::Extensions;
    use rama_core::extensions::ExtensionsMut;
    use rama_net::address::HostWithPort;
    use rama_net::address::SocketAddress;
    use rama_net::stream::Socket as _;
    use rama_socks5::server::udp::RelayDirection;
    use std::net::IpAddr;
    use std::net::Ipv4Addr;
    use std::sync::Arc;

    #[derive(Clone)]
    struct StaticReloader {
        state: ConfigState,
    }

    impl ConfigReloader for StaticReloader {
        fn maybe_reload(&self) -> ConfigReloaderFuture<'_, Option<ConfigState>> {
            Box::pin(async { Ok(None) })
        }

        fn reload_now(&self) -> ConfigReloaderFuture<'_, ConfigState> {
            Box::pin(async { Ok(self.state.clone()) })
        }

        fn source_label(&self) -> String {
            "static test reloader".to_string()
        }
    }

    fn state_for_settings(network: NetworkProxyConfig) -> Arc<NetworkProxyState> {
        let config = network;
        let state = build_config_state(config, NetworkProxyConstraints::default()).unwrap();
        let reloader = Arc::new(StaticReloader {
            state: state.clone(),
        });
        Arc::new(NetworkProxyState::with_reloader(state, reloader))
    }

    #[tokio::test(flavor = "current_thread")]
    async fn handle_socks5_tcp_emits_block_decision_for_proxy_disabled() {
        let state = state_for_settings(NetworkProxyConfig {
            enabled: false,
            mode: NetworkMode::Proxy,
            ..NetworkProxyConfig::default()
        });
        let mut request =
            TcpRequest::new(HostWithPort::try_from("example.com:443").expect("valid authority"));
        request.extensions_mut().insert(state.clone());

        let (result, events) = capture_events(|| async {
            handle_socks5_tcp(
                request,
                TargetCheckedTcpConnector::new(state.clone()),
                /*policy_decider*/ None,
                /*environment_id*/ None,
            )
            .await
        })
        .await;
        assert!(result.is_err(), "proxy-disabled request should be denied");

        let event = find_event_by_name(&events, POLICY_DECISION_EVENT_NAME)
            .expect("expected policy decision event");
        assert_eq!(event.field("network.policy.scope"), Some("non_domain"));
        assert_eq!(event.field("network.policy.decision"), Some("deny"));
        assert_eq!(event.field("network.policy.source"), Some("proxy_state"));
        assert_eq!(
            event.field("network.policy.reason"),
            Some(REASON_PROXY_DISABLED)
        );
        assert_eq!(
            event.field("network.transport.protocol"),
            Some("socks5_tcp")
        );
        assert_eq!(event.field("server.address"), Some("example.com"));
        assert_eq!(event.field("server.port"), Some("443"));
        assert_eq!(event.field("http.request.method"), Some("none"));
        assert_eq!(event.field("client.address"), Some("unknown"));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn handle_socks5_tcp_blocks_in_none_mode() {
        // 适配：codex 在 Limited 模式只阻断非 HTTPS 目标；nova None 模式全部拒绝。
        let mut settings = NetworkProxyConfig {
            enabled: true,
            mode: NetworkMode::None,
            ..NetworkProxyConfig::default()
        };
        settings.set_allowed_domains(vec!["example.com".to_string()]);
        let state = state_for_settings(settings);
        let mut request =
            TcpRequest::new(HostWithPort::try_from("example.com:443").expect("valid authority"));
        request.extensions_mut().insert(state.clone());

        let (result, events) = capture_events(|| async {
            handle_socks5_tcp(
                request,
                TargetCheckedTcpConnector::new(state),
                /*policy_decider*/ None,
                /*environment_id*/ None,
            )
            .await
        })
        .await;
        assert!(result.is_err(), "none-mode SOCKS should be denied");

        let event = find_event_by_name(&events, POLICY_DECISION_EVENT_NAME)
            .expect("expected policy decision event");
        assert_eq!(event.field("network.policy.scope"), Some("non_domain"));
        assert_eq!(event.field("network.policy.decision"), Some("deny"));
        assert_eq!(event.field("network.policy.source"), Some("mode_guard"));
        assert_eq!(
            event.field("network.policy.reason"),
            Some(REASON_METHOD_NOT_ALLOWED)
        );
        assert_eq!(
            event.field("network.transport.protocol"),
            Some("socks5_tcp")
        );
        assert_eq!(event.field("server.address"), Some("example.com"));
        assert_eq!(event.field("server.port"), Some("443"));
        assert_eq!(event.field("http.request.method"), Some("none"));
        assert_eq!(event.field("client.address"), Some("unknown"));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn handle_socks5_tcp_dials_directly_in_proxy_mode() {
        // 覆盖裁掉 MITM 后的直接转发路径：本地 listener 充当上行目标。
        let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .await
            .expect("bind local listener");
        let target = listener.local_addr().expect("local addr");
        let mut settings = NetworkProxyConfig {
            enabled: true,
            mode: NetworkMode::Proxy,
            allow_local_binding: true,
            ..NetworkProxyConfig::default()
        };
        settings.set_allowed_domains(vec!["127.0.0.1".to_string()]);
        let state = state_for_settings(settings);
        let mut request = TcpRequest::new(HostWithPort::from(target));
        request.extensions_mut().insert(state.clone());

        let connection = handle_socks5_tcp(
            request,
            TargetCheckedTcpConnector::new(state),
            /*policy_decider*/ None,
            /*environment_id*/ None,
        )
        .await
        .expect("proxy-mode SOCKS should dial directly");

        assert_eq!(connection.conn.peer_addr().expect("peer addr"), target);
    }

    // 裁点：codex 的 MITM 连接形态（Limited 模式 :443、hook 命中、凭证经纪 TLS 探测）
    // 相关测试第一期未移植。

    #[tokio::test(flavor = "current_thread")]
    async fn inspect_socks5_udp_emits_block_decision_for_mode_guard_deny() {
        let state = state_for_settings(NetworkProxyConfig {
            enabled: true,
            mode: NetworkMode::None,
            ..NetworkProxyConfig::default()
        });
        let request = RelayRequest {
            direction: RelayDirection::South,
            server_address: SocketAddress::new(IpAddr::V4(Ipv4Addr::new(93, 184, 216, 34)), 53),
            payload: Default::default(),
            extensions: Extensions::new(),
        };

        let (result, events) = capture_events(|| async {
            inspect_socks5_udp(
                request, state, /*policy_decider*/ None, /*environment_id*/ None,
            )
            .await
        })
        .await;
        assert!(result.is_err(), "none-mode UDP request should be denied");

        let event = find_event_by_name(&events, POLICY_DECISION_EVENT_NAME)
            .expect("expected policy decision event");
        assert_eq!(event.field("network.policy.scope"), Some("non_domain"));
        assert_eq!(event.field("network.policy.decision"), Some("deny"));
        assert_eq!(event.field("network.policy.source"), Some("mode_guard"));
        assert_eq!(
            event.field("network.policy.reason"),
            Some(REASON_METHOD_NOT_ALLOWED)
        );
        assert_eq!(
            event.field("network.transport.protocol"),
            Some("socks5_udp")
        );
        assert_eq!(event.field("server.address"), Some("93.184.216.34"));
        assert_eq!(event.field("server.port"), Some("53"));
        assert_eq!(event.field("http.request.method"), Some("none"));
        assert_eq!(event.field("client.address"), Some("unknown"));
    }
}
