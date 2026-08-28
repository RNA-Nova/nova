//! 网络代理编排层（移植自 codex network-proxy `proxy.rs`）。
//!
//! 裁点（第一期）：
//! - Windows 面：`WindowsProxyIngress` / 共享路由 / 托管端口段未移植；
//!   `network_proxy_restricting_sid` 保留 cfg(windows) 占位（恒 None，待 ingress 恢复）。
//! - 凭证经纪：`virtualize_child_credentials` 调用、`remote_launch_config` 里的
//!   broker-only 检测与凭证字段剥离逻辑未移植（远程边界由
//!   `RemoteNetworkProxyConfig::from_effective_config` 拒绝 broker 配置兜底）。
//! - MITM 面：`NetworkProxyRuntimeSettings.mitm_ca_trust_bundle` 与
//!   `apply_proxy_env_overrides` 的 CA 注入段未移植；
//!   `managed_mitm_ca_trust_bundle_path` 保留恒 None（MITM 恢复前无托管 bundle）。

mod execution_scope;

use crate::attribution::PROXY_ATTRIBUTION_TOKEN_ENV_KEY;
use crate::config;
use crate::http_proxy;
use crate::runtime::BlockedRequestObserver;
use crate::runtime::ConfigState;
use crate::runtime::HostBlockDecision;
use crate::runtime::HostBlockReason;
use crate::runtime::unix_socket_permissions_supported;
use crate::socks5;
use crate::state::NetworkProxyState;
use crate::NetworkDecision;
use crate::NetworkDecisionSource;
use crate::NetworkPolicyDecider;
use anyhow::Context;
use anyhow::Result;
use clap::Parser;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::net::TcpListener as StdTcpListener;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::RwLock;
use tokio::task::JoinHandle;
use tracing::warn;

use self::execution_scope::ExecutionScope;

#[derive(Debug, Clone, Parser)]
#[command(name = "nova-executor-network-proxy", about = "Nova network sandbox proxy")]
pub struct Args {}

#[derive(Debug)]
struct ReservedListeners {
    http: Mutex<Option<StdTcpListener>>,
    socks: Mutex<Option<StdTcpListener>>,
}

impl ReservedListeners {
    fn new(http: StdTcpListener, socks: Option<StdTcpListener>) -> Self {
        Self {
            http: Mutex::new(Some(http)),
            socks: Mutex::new(socks),
        }
    }

    fn take_http(&self) -> Option<StdTcpListener> {
        let mut guard = self
            .http
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.take()
    }

    fn take_socks(&self) -> Option<StdTcpListener> {
        let mut guard = self
            .socks
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.take()
    }
}

pub(super) struct ReservedListenerSet {
    http_listener: StdTcpListener,
    socks_listener: Option<StdTcpListener>,
}

impl ReservedListenerSet {
    fn new(http_listener: StdTcpListener, socks_listener: Option<StdTcpListener>) -> Self {
        Self {
            http_listener,
            socks_listener,
        }
    }

    pub(super) fn http_addr(&self) -> Result<SocketAddr> {
        self.http_listener
            .local_addr()
            .context("failed to read reserved HTTP proxy address")
    }

    pub(super) fn socks_addr(&self, default_addr: SocketAddr) -> Result<SocketAddr> {
        self.socks_listener
            .as_ref()
            .map_or(Ok(default_addr), |listener| {
                listener
                    .local_addr()
                    .context("failed to read reserved SOCKS5 proxy address")
            })
    }

    fn into_reserved_listeners(self) -> Arc<ReservedListeners> {
        Arc::new(ReservedListeners::new(
            self.http_listener,
            self.socks_listener,
        ))
    }
}

#[derive(Clone)]
pub struct NetworkProxyBuilder {
    state: Option<Arc<NetworkProxyState>>,
    http_addr: Option<SocketAddr>,
    socks_addr: Option<SocketAddr>,
    managed_by_nova: bool,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    blocked_request_observer: Option<Arc<dyn BlockedRequestObserver>>,
}

impl NetworkProxyBuilder {
    /// 与 `Default` 相同的空构造器（stub 时代的既有入口，保留）。
    pub fn new() -> Self {
        Self::default()
    }

    pub fn state(mut self, state: Arc<NetworkProxyState>) -> Self {
        self.state = Some(state);
        self
    }

    pub fn http_addr(mut self, addr: SocketAddr) -> Self {
        self.http_addr = Some(addr);
        self
    }

    pub fn socks_addr(mut self, addr: SocketAddr) -> Self {
        self.socks_addr = Some(addr);
        self
    }

    /// 对位 codex 的 `managed_by_codex`：托管模式下构建期预留 loopback 临时端口监听器，
    /// 避免 run() 前端口竞争。
    pub fn managed_by_nova(mut self, managed_by_nova: bool) -> Self {
        self.managed_by_nova = managed_by_nova;
        self
    }

    pub fn policy_decider<D>(mut self, decider: D) -> Self
    where
        D: NetworkPolicyDecider,
    {
        self.policy_decider = Some(Arc::new(decider));
        self
    }

    pub fn policy_decider_arc(mut self, decider: Arc<dyn NetworkPolicyDecider>) -> Self {
        self.policy_decider = Some(decider);
        self
    }

    pub fn blocked_request_observer<O>(mut self, observer: O) -> Self
    where
        O: BlockedRequestObserver,
    {
        self.blocked_request_observer = Some(Arc::new(observer));
        self
    }

    pub fn blocked_request_observer_arc(
        mut self,
        observer: Arc<dyn BlockedRequestObserver>,
    ) -> Self {
        self.blocked_request_observer = Some(observer);
        self
    }

    pub async fn build(self) -> Result<NetworkProxy> {
        let state = self.state.ok_or_else(|| {
            anyhow::anyhow!(
                "NetworkProxyBuilder requires a state; supply one via builder.state(...)"
            )
        })?;
        state
            .set_blocked_request_observer(self.blocked_request_observer.clone())
            .await;
        let current_cfg = state.current_cfg().await?;
        let (requested_http_addr, requested_socks_addr, reserved_listeners) =
            if self.managed_by_nova {
                let runtime = config::resolve_runtime(&current_cfg)?;
                let reserved = reserve_loopback_ephemeral_listeners(current_cfg.enable_socks5)
                    .context("reserve managed loopback proxy listeners")?;
                let http_addr = reserved.http_addr()?;
                let socks_addr = reserved.socks_addr(runtime.socks_addr)?;
                (
                    http_addr,
                    socks_addr,
                    Some(reserved.into_reserved_listeners()),
                )
            } else {
                let runtime = config::resolve_runtime(&current_cfg)?;
                (
                    self.http_addr.unwrap_or(runtime.http_addr),
                    self.socks_addr.unwrap_or(runtime.socks_addr),
                    None,
                )
            };

        // 对调用方覆盖的地址重新做绑定钳制，保证 unix-socket 代理只监听 loopback。
        let (http_addr, socks_addr) =
            config::clamp_bind_addrs(requested_http_addr, requested_socks_addr, &current_cfg);

        let runtime_settings = NetworkProxyRuntimeSettings::from_config(&current_cfg);

        Ok(NetworkProxy {
            state,
            http_addr,
            socks_addr,
            socks_enabled: current_cfg.enable_socks5,
            socks5_udp_enabled: current_cfg.enable_socks5_udp,
            runtime_settings: Arc::new(RwLock::new(runtime_settings)),
            reserved_listeners,
            policy_decider: self.policy_decider,
            environment_proxies: Arc::new(Mutex::new(HashMap::new())),
            execution_scope: None,
        })
    }
}

impl Default for NetworkProxyBuilder {
    fn default() -> Self {
        Self {
            state: None,
            http_addr: None,
            socks_addr: None,
            managed_by_nova: true,
            policy_decider: None,
            blocked_request_observer: None,
        }
    }
}

fn reserve_loopback_ephemeral_listeners(
    reserve_socks_listener: bool,
) -> Result<ReservedListenerSet> {
    let http_listener =
        reserve_loopback_ephemeral_listener().context("reserve HTTP proxy listener")?;
    let socks_listener = if reserve_socks_listener {
        Some(reserve_loopback_ephemeral_listener().context("reserve SOCKS5 proxy listener")?)
    } else {
        None
    };
    Ok(ReservedListenerSet::new(http_listener, socks_listener))
}

fn reserve_loopback_ephemeral_listener() -> Result<StdTcpListener> {
    StdTcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0)))
        .context("bind loopback ephemeral port")
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NetworkProxyRuntimeSettings {
    allow_local_binding: bool,
    allow_unix_sockets: Arc<[String]>,
    dangerously_allow_all_unix_sockets: bool,
}

impl NetworkProxyRuntimeSettings {
    fn from_config(config: &crate::NetworkProxyConfig) -> Self {
        Self {
            allow_local_binding: config.allow_local_binding,
            allow_unix_sockets: if cfg!(target_os = "windows") {
                Arc::default()
            } else {
                config.allow_unix_sockets().into()
            },
            dangerously_allow_all_unix_sockets: !cfg!(target_os = "windows")
                && config.dangerously_allow_all_unix_sockets,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct EnvironmentProxyAddrs {
    http_addr: SocketAddr,
    socks_addr: SocketAddr,
}

/// 操作系统沙箱所需的可移植托管网络事实。
///
/// 注：线上类型 `ManagedNetworkSandboxContext` 与 `PreparedManagedNetwork` 定义在
/// crate 根（见 lib.rs）；这里只是使用点。
struct EnvironmentProxy {
    addrs: EnvironmentProxyAddrs,
    runtime: EnvironmentProxyRuntime,
}

enum EnvironmentProxyRuntime {
    ListenerTasks {
        http_task: JoinHandle<Result<()>>,
        socks_task: Option<JoinHandle<Result<()>>>,
    },
}

#[derive(Clone)]
pub struct NetworkProxy {
    state: Arc<NetworkProxyState>,
    http_addr: SocketAddr,
    socks_addr: SocketAddr,
    socks_enabled: bool,
    socks5_udp_enabled: bool,
    runtime_settings: Arc<RwLock<NetworkProxyRuntimeSettings>>,
    reserved_listeners: Option<Arc<ReservedListeners>>,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
    environment_proxies: Arc<Mutex<HashMap<String, EnvironmentProxy>>>,
    execution_scope: Option<Arc<ExecutionScope>>,
}

impl std::fmt::Debug for NetworkProxy {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // 避免记录内部状态（配置内容、派生 globset 等可能含敏感路径且噪音大）。
        f.debug_struct("NetworkProxy")
            .field("http_addr", &self.http_addr)
            .field("socks_addr", &self.socks_addr())
            .finish_non_exhaustive()
    }
}

impl PartialEq for NetworkProxy {
    fn eq(&self, other: &Self) -> bool {
        self.http_addr == other.http_addr
            && self.socks_addr() == other.socks_addr()
            && self.runtime_settings() == other.runtime_settings()
    }
}

impl Eq for NetworkProxy {}

pub const PROXY_URL_ENV_KEYS: &[&str] = &[
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "WS_PROXY",
    "WSS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "YARN_HTTP_PROXY",
    "YARN_HTTPS_PROXY",
    "NPM_CONFIG_HTTP_PROXY",
    "NPM_CONFIG_HTTPS_PROXY",
    "NPM_CONFIG_PROXY",
    "BUNDLE_HTTP_PROXY",
    "BUNDLE_HTTPS_PROXY",
    "PIP_PROXY",
    "DOCKER_HTTP_PROXY",
    "DOCKER_HTTPS_PROXY",
];

pub const ALL_PROXY_ENV_KEYS: &[&str] = &["ALL_PROXY", "all_proxy"];
pub const PROXY_ACTIVE_ENV_KEY: &str = "CODEX_NETWORK_PROXY_ACTIVE";
pub const ALLOW_LOCAL_BINDING_ENV_KEY: &str = "CODEX_NETWORK_ALLOW_LOCAL_BINDING";
const ELECTRON_GET_USE_PROXY_ENV_KEY: &str = "ELECTRON_GET_USE_PROXY";
const NODE_USE_ENV_PROXY_ENV_KEY: &str = "NODE_USE_ENV_PROXY";
#[cfg(any(target_os = "macos", test))]
const GIT_SSH_COMMAND_ENV_KEY: &str = "GIT_SSH_COMMAND";

/// 由托管代理接管的环境变量全集（strip/注入对称性的单一事实源）。
///
/// 相对 stub 快照补齐了 `CODEX_PROXY_ATTRIBUTION_TOKEN`、`ELECTRON_GET_USE_PROXY`、
/// `NODE_USE_ENV_PROXY`——`apply_proxy_env_overrides` / `prepare_for_addrs` 会写入它们，
/// `strip_managed_proxy_env` 必须能对称剥除。键值沿用 codex 线上取值（子进程 env 契约，
/// 与 executor-otel 保留 `codex.*` 遥测名的先例一致）。
pub const PROXY_ENV_KEYS: &[&str] = &[
    PROXY_ACTIVE_ENV_KEY,
    ALLOW_LOCAL_BINDING_ENV_KEY,
    PROXY_ATTRIBUTION_TOKEN_ENV_KEY,
    ELECTRON_GET_USE_PROXY_ENV_KEY,
    NODE_USE_ENV_PROXY_ENV_KEY,
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "YARN_HTTP_PROXY",
    "YARN_HTTPS_PROXY",
    "npm_config_http_proxy",
    "npm_config_https_proxy",
    "npm_config_proxy",
    "NPM_CONFIG_HTTP_PROXY",
    "NPM_CONFIG_HTTPS_PROXY",
    "NPM_CONFIG_PROXY",
    "BUNDLE_HTTP_PROXY",
    "BUNDLE_HTTPS_PROXY",
    "PIP_PROXY",
    "DOCKER_HTTP_PROXY",
    "DOCKER_HTTPS_PROXY",
    "WS_PROXY",
    "WSS_PROXY",
    "ws_proxy",
    "wss_proxy",
    "NO_PROXY",
    "no_proxy",
    "npm_config_noproxy",
    "NPM_CONFIG_NOPROXY",
    "YARN_NO_PROXY",
    "BUNDLE_NO_PROXY",
    "ALL_PROXY",
    "all_proxy",
    "FTP_PROXY",
    "ftp_proxy",
];

pub fn is_managed_proxy_env_var(key: &str, value: &str) -> bool {
    if PROXY_ENV_KEYS.contains(&key) {
        return true;
    }
    // 裁点（MITM）：codex 还会按值识别托管 MITM CA bundle 路径（CUSTOM_CA_ENV_KEYS）；
    // 第一期恒不匹配（见 lib.rs `is_managed_mitm_ca_trust_bundle_path`）。
    #[cfg(target_os = "macos")]
    {
        key == PROXY_GIT_SSH_COMMAND_ENV_KEY
            && value.starts_with(NOVA_PROXY_GIT_SSH_COMMAND_MARKER)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = value;
        false
    }
}

pub fn strip_managed_proxy_env(env: &mut HashMap<String, String>) {
    env.retain(|key, value| !is_managed_proxy_env_var(key, value));
}

#[cfg(target_os = "macos")]
pub const PROXY_GIT_SSH_COMMAND_ENV_KEY: &str = GIT_SSH_COMMAND_ENV_KEY;

const FTP_PROXY_ENV_KEYS: &[&str] = &["FTP_PROXY", "ftp_proxy"];
const WEBSOCKET_PROXY_ENV_KEYS: &[&str] = &["WS_PROXY", "WSS_PROXY", "ws_proxy", "wss_proxy"];

pub const NO_PROXY_ENV_KEYS: &[&str] = &[
    "NO_PROXY",
    "no_proxy",
    "npm_config_noproxy",
    "NPM_CONFIG_NOPROXY",
    "YARN_NO_PROXY",
    "BUNDLE_NO_PROXY",
];

pub const DEFAULT_NO_PROXY_VALUE: &str = concat!(
    "localhost,127.0.0.1,::1,",
    "10.0.0.0/8,",
    "172.16.0.0/12,",
    "192.168.0.0/16"
);

#[cfg(target_os = "macos")]
pub const NOVA_PROXY_GIT_SSH_COMMAND_MARKER: &str = "CODEX_PROXY_GIT_SSH_COMMAND=1 ";
#[cfg(target_os = "macos")]
const NOVA_PROXY_GIT_SSH_COMMAND_PREFIX: &str =
    "CODEX_PROXY_GIT_SSH_COMMAND=1 ssh -o ProxyCommand='nc -X 5 -x ";
#[cfg(target_os = "macos")]
const NOVA_PROXY_GIT_SSH_COMMAND_SUFFIX: &str = " %h %p'";

pub fn proxy_url_env_value<'a>(
    env: &'a HashMap<String, String>,
    canonical_key: &str,
) -> Option<&'a str> {
    if let Some(value) = env.get(canonical_key) {
        return Some(value.as_str());
    }
    let lower_key = canonical_key.to_ascii_lowercase();
    env.get(lower_key.as_str()).map(String::as_str)
}

pub fn has_proxy_url_env_vars(env: &HashMap<String, String>) -> bool {
    PROXY_URL_ENV_KEYS
        .iter()
        .any(|key| proxy_url_env_value(env, key).is_some_and(|value| !value.trim().is_empty()))
}

fn set_env_keys(env: &mut HashMap<String, String>, keys: &[&str], value: &str) {
    for key in keys {
        env.insert((*key).to_string(), value.to_string());
    }
}

#[cfg(target_os = "macos")]
fn nova_proxy_git_ssh_command(socks_addr: SocketAddr) -> String {
    format!("{NOVA_PROXY_GIT_SSH_COMMAND_PREFIX}{socks_addr}{NOVA_PROXY_GIT_SSH_COMMAND_SUFFIX}")
}

#[cfg(target_os = "macos")]
fn is_nova_proxy_git_ssh_command(command: &str) -> bool {
    command.starts_with(NOVA_PROXY_GIT_SSH_COMMAND_PREFIX)
        && command.ends_with(NOVA_PROXY_GIT_SSH_COMMAND_SUFFIX)
}

fn apply_proxy_env_overrides(
    env: &mut HashMap<String, String>,
    http_addr: SocketAddr,
    socks_addr: SocketAddr,
    socks_enabled: bool,
    allow_local_binding: bool,
) {
    let http_proxy_url = format!("http://{http_addr}");
    let socks_proxy_url = format!("socks5h://{socks_addr}");
    env.insert(PROXY_ACTIVE_ENV_KEY.to_string(), "1".to_string());
    env.insert(
        ALLOW_LOCAL_BINDING_ENV_KEY.to_string(),
        if allow_local_binding {
            "1".to_string()
        } else {
            "0".to_string()
        },
    );

    // 基于 HTTP 的客户端最适合用显式的 HTTP 代理 URL。
    set_env_keys(
        env,
        &[
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "YARN_HTTP_PROXY",
            "YARN_HTTPS_PROXY",
            "npm_config_http_proxy",
            "npm_config_https_proxy",
            "npm_config_proxy",
            "NPM_CONFIG_HTTP_PROXY",
            "NPM_CONFIG_HTTPS_PROXY",
            "NPM_CONFIG_PROXY",
            "BUNDLE_HTTP_PROXY",
            "BUNDLE_HTTPS_PROXY",
            "PIP_PROXY",
            "DOCKER_HTTP_PROXY",
            "DOCKER_HTTPS_PROXY",
        ],
        &http_proxy_url,
    );
    // 一些 websocket 客户端找专用的 WS/WSS 代理环境变量，而不是 HTTP(S)_PROXY。
    // 让它们与托管 HTTP 代理端点对齐。
    set_env_keys(env, WEBSOCKET_PROXY_ENV_KEYS, &http_proxy_url);

    // 仅在允许本地绑定时让本地目标直连；否则也走代理，以便执行显式字面量
    // allowlist 与本地网络限制。
    let no_proxy = if allow_local_binding {
        DEFAULT_NO_PROXY_VALUE
    } else {
        ""
    };
    set_env_keys(env, NO_PROXY_ENV_KEYS, no_proxy);

    env.insert(
        ELECTRON_GET_USE_PROXY_ENV_KEY.to_string(),
        "true".to_string(),
    );
    // Node.js 内建 HTTP 客户端只在开启该开关后才认代理环境变量。
    env.insert(NODE_USE_ENV_PROXY_ENV_KEY.to_string(), "1".to_string());

    // HTTP_PROXY/HTTPS_PROXY 保持 HTTP 端点——很多客户端遇到 SOCKS URL 会坏。
    // 这里只切换 ALL_PROXY。
    //
    if socks_enabled {
        set_env_keys(env, ALL_PROXY_ENV_KEYS, &socks_proxy_url);
        set_env_keys(env, FTP_PROXY_ENV_KEYS, &socks_proxy_url);
    } else {
        set_env_keys(env, ALL_PROXY_ENV_KEYS, &http_proxy_url);
        set_env_keys(env, FTP_PROXY_ENV_KEYS, &http_proxy_url);
    }

    #[cfg(target_os = "macos")]
    if socks_enabled {
        // 保留已有的 SSH 包装（例如 Secretive/Teleport 配置），但刷新此前注入的
        // 代理回落，避免代理重启后指向过期端口。
        match env.get(GIT_SSH_COMMAND_ENV_KEY) {
            Some(command) if !is_nova_proxy_git_ssh_command(command) => {}
            _ => {
                env.insert(
                    GIT_SSH_COMMAND_ENV_KEY.to_string(),
                    nova_proxy_git_ssh_command(socks_addr),
                );
            }
        }
    }

    // 裁点（MITM）：codex 在此把托管 MITM CA bundle 路径写入 CUSTOM_CA_ENV_KEYS
    // 系列环境变量；第一期未移植 MITM，无 bundle 可写。
}

impl NetworkProxy {
    pub fn builder() -> NetworkProxyBuilder {
        NetworkProxyBuilder::default()
    }

    pub fn http_addr(&self) -> SocketAddr {
        self.http_addr
    }

    pub fn socks_addr(&self) -> SocketAddr {
        self.socks_addr
    }

    /// 返回标识该逻辑代理路由的 restricting SID（供 Windows 沙箱关联路由）。
    ///
    /// 裁点：Windows 共享 ingress 未移植，当前恒为 None。
    #[cfg(target_os = "windows")]
    pub fn network_proxy_restricting_sid(&self, environment_id: Option<&str>) -> Option<String> {
        let _ = environment_id;
        None
    }

    pub async fn current_cfg(&self) -> Result<crate::NetworkProxyConfig> {
        self.state.current_cfg().await
    }

    /// 抓取启动一个匹配的 executor 本地代理所需的静态输入。
    pub async fn remote_launch_config(&self) -> Result<crate::RemoteNetworkProxyLaunchConfig> {
        let mut config = self.state.current_cfg().await?;
        // 代理启用与否与 MITM/凭证配置归 controller 所有；远程边界由
        // `RemoteNetworkProxyConfig::from_effective_config` 拒绝不支持的配置。
        let environment_policy = self
            .execution_scope
            .as_ref()
            .and_then(|scope| scope.environment_policy.as_ref());
        if let Some(policy) = environment_policy {
            policy.apply_to(&mut config);
        }
        anyhow::ensure!(
            environment_policy.is_none() || config.enabled,
            "environment network policy requires an enabled executor proxy"
        );
        let proxy = crate::RemoteNetworkProxyConfig::from_effective_config(&config)?;
        let (environment_id, execution_id) = self
            .execution_scope
            .as_ref()
            .map(|scope| {
                (
                    Some(scope.environment_id.clone()),
                    Some(scope.execution_id.clone()),
                )
            })
            .unwrap_or_default();
        Ok(crate::RemoteNetworkProxyLaunchConfig {
            proxy,
            audit_metadata: self.state.audit_metadata().clone(),
            environment_id,
            execution_id,
            policy_decision_timeout_ms: None,
        })
    }

    /// 返回恢复了可信执行归因的策略 decider。
    pub fn remote_policy_decider(&self) -> Option<Arc<dyn NetworkPolicyDecider>> {
        let scope = self.execution_scope.as_ref()?;
        self.state.for_execution_token(&scope.attribution_token)?;
        let decider = Arc::clone(self.policy_decider.as_ref()?);
        let state = Arc::clone(&self.state);
        let environment_id = scope.environment_id.clone();
        let execution_id = scope.execution_id.clone();
        let environment_policy_applies = scope.environment_policy.is_some();
        let execution_lifetime = scope.lifetime_tx.subscribe();
        Some(Arc::new(move |mut request: crate::NetworkPolicyRequest| {
            let decider = Arc::clone(&decider);
            let state = Arc::clone(&state);
            let mut execution_lifetime = execution_lifetime.clone();
            request.environment_id = Some(environment_id.clone());
            request.execution_id = Some(execution_id.clone());
            async move {
                tokio::select! {
                    biased;
                    _ = execution_lifetime.changed() => {
                        crate::NetworkDecision::deny(crate::reasons::REASON_NOT_ALLOWED)
                    }
                    decision = async {
                        match state.host_blocked(&request.host, request.port).await {
                            // 仅有 controller 批准不能绕过 attachment 策略。
                            Ok(HostBlockDecision::Allowed) if !environment_policy_applies => {
                                NetworkDecision::Allow
                            }
                            Ok(HostBlockDecision::Allowed)
                            | Ok(HostBlockDecision::Blocked(HostBlockReason::NotAllowed)) => {
                                decider.decide(request).await
                            }
                            Ok(HostBlockDecision::Blocked(reason)) => {
                                NetworkDecision::deny_with_source(
                                    reason.as_str(),
                                    NetworkDecisionSource::BaselinePolicy,
                                )
                            }
                            Err(err) => {
                                warn!("failed to evaluate controller network policy: {err}");
                                NetworkDecision::deny_with_source(
                                    crate::reasons::REASON_NOT_ALLOWED,
                                    NetworkDecisionSource::BaselinePolicy,
                                )
                            }
                        }
                    } => decision,
                }
            }
        }))
    }

    pub async fn add_allowed_domain(&self, host: &str) -> Result<()> {
        self.state.add_allowed_domain(host).await
    }

    pub async fn add_denied_domain(&self, host: &str) -> Result<()> {
        self.state.add_denied_domain(host).await
    }

    pub fn allow_local_binding(&self) -> bool {
        self.runtime_settings().allow_local_binding
    }

    pub fn allow_unix_sockets(&self) -> Vec<String> {
        self.runtime_settings().allow_unix_sockets.to_vec()
    }

    pub fn dangerously_allow_all_unix_sockets(&self) -> bool {
        self.runtime_settings().dangerously_allow_all_unix_sockets
    }

    /// 返回子沙箱应向 TLS 客户端暴露的托管 MITM CA bundle 路径。
    ///
    /// 裁点：MITM 面未移植，恒为 None。
    pub fn managed_mitm_ca_trust_bundle_path(&self) -> Option<AbsolutePathBuf> {
        None
    }

    fn prepare_for_addrs(
        &self,
        mut env: HashMap<String, String>,
        addrs: EnvironmentProxyAddrs,
    ) -> crate::PreparedManagedNetwork {
        let runtime_settings = self.runtime_settings();
        // 对子进程强制走代理：代理端点值总是重写。
        apply_proxy_env_overrides(
            &mut env,
            addrs.http_addr,
            addrs.socks_addr,
            self.socks_enabled,
            runtime_settings.allow_local_binding,
        );
        // 裁点（凭证经纪）：codex 在此把子进程凭证替换为 dummy 值；未移植。
        if let Some(execution_scope) = self.execution_scope.as_ref() {
            env.insert(
                PROXY_ATTRIBUTION_TOKEN_ENV_KEY.to_string(),
                execution_scope.attribution_token.clone(),
            );
        } else {
            env.remove(PROXY_ATTRIBUTION_TOKEN_ENV_KEY);
        }
        let expose_socks_port = self.socks_enabled;
        let mut loopback_ports = [
            Some(addrs.http_addr),
            expose_socks_port.then_some(addrs.socks_addr),
        ]
        .into_iter()
        .flatten()
        .filter(|addr| addr.ip().is_loopback())
        .map(|addr| addr.port())
        .collect::<Vec<_>>();
        loopback_ports.sort_unstable();
        loopback_ports.dedup();
        crate::PreparedManagedNetwork {
            env,
            sandbox_context: crate::ManagedNetworkSandboxContext {
                loopback_ports,
                allow_local_binding: runtime_settings.allow_local_binding,
            },
        }
    }

    fn apply_to_env_for_addrs(
        &self,
        env: &mut HashMap<String, String>,
        addrs: EnvironmentProxyAddrs,
    ) {
        let prepared = self.prepare_for_addrs(std::mem::take(env), addrs);
        *env = prepared.env;
    }

    pub fn apply_to_env(&self, env: &mut HashMap<String, String>) {
        self.apply_to_env_for_addrs(
            env,
            EnvironmentProxyAddrs {
                http_addr: self.http_addr,
                socks_addr: self.socks_addr,
            },
        );
    }

    pub fn apply_to_env_for_environment(
        &self,
        env: &mut HashMap<String, String>,
        environment_id: &str,
    ) -> Result<()> {
        let addrs = self.environment_proxy_addrs(environment_id)?;
        self.apply_to_env_for_addrs(env, addrs);
        Ok(())
    }

    pub fn apply_to_env_for_optional_environment(
        &self,
        env: &mut HashMap<String, String>,
        environment_id: Option<&str>,
    ) -> Result<()> {
        match environment_id {
            Some(environment_id) => self.apply_to_env_for_environment(env, environment_id),
            None => {
                self.apply_to_env(env);
                Ok(())
            }
        }
    }

    /// 应用环境特定的代理设置，并从同一运行时配置快照返回匹配的可移植沙箱投影。
    pub fn prepare_for_optional_environment(
        &self,
        env: HashMap<String, String>,
        environment_id: Option<&str>,
    ) -> Result<crate::PreparedManagedNetwork> {
        let addrs = match environment_id {
            Some(environment_id) => self.environment_proxy_addrs(environment_id)?,
            None => EnvironmentProxyAddrs {
                http_addr: self.http_addr,
                socks_addr: self.socks_addr,
            },
        };
        Ok(self.prepare_for_addrs(env, addrs))
    }

    /// 为远程 executor 准备代理设置：它的连接经可信代理网桥到达本进程，
    /// 而不是本地直接拉起的沙箱进程。
    pub fn prepare_for_remote_environment(
        &self,
        env: HashMap<String, String>,
        environment_id: &str,
    ) -> Result<crate::PreparedManagedNetwork> {
        let addrs = self.environment_proxy_addrs(environment_id)?;
        Ok(self.prepare_for_addrs(env, addrs))
    }

    fn environment_proxy_addrs(&self, environment_id: &str) -> Result<EnvironmentProxyAddrs> {
        if let Some(execution_scope) = self.execution_scope.as_ref() {
            anyhow::ensure!(
                execution_scope.environment_id == environment_id,
                "execution-scoped network proxy belongs to environment `{}`, not `{environment_id}`",
                execution_scope.environment_id
            );
        }

        let mut proxies = self
            .environment_proxies
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(proxy) = proxies.get(environment_id) {
            return Ok(proxy.addrs);
        }

        let runtime = tokio::runtime::Handle::try_current().with_context(|| {
            format!("failed to create network proxy for environment `{environment_id}`")
        })?;
        let listeners =
            reserve_loopback_ephemeral_listeners(self.socks_enabled).with_context(|| {
                format!("failed to reserve network proxy for environment `{environment_id}`")
            })?;
        let http_addr = listeners.http_addr().with_context(|| {
            format!("failed to read HTTP proxy address for environment `{environment_id}`")
        })?;
        let socks_addr = listeners.socks_addr(self.socks_addr).with_context(|| {
            format!("failed to read SOCKS proxy address for environment `{environment_id}`")
        })?;
        let addrs = EnvironmentProxyAddrs {
            http_addr,
            socks_addr,
        };
        let ReservedListenerSet {
            http_listener,
            socks_listener,
        } = listeners;

        let environment_id = environment_id.to_string();
        let http_state = self.state.clone();
        let http_decider = self.policy_decider.clone();
        let http_environment_id = Some(environment_id.clone());
        let http_task = runtime.spawn(async move {
            http_proxy::run_http_proxy_with_std_listener(
                http_state,
                http_listener,
                http_decider,
                http_environment_id,
            )
            .await
        });

        let socks_task = if self.socks_enabled {
            let socks_state = self.state.clone();
            let socks_decider = self.policy_decider.clone();
            let socks_environment_id = Some(environment_id.clone());
            let socks5_udp_enabled = self.socks5_udp_enabled;
            socks_listener.map(|listener| {
                runtime.spawn(async move {
                    socks5::run_socks5_with_std_listener(
                        socks_state,
                        listener,
                        socks_decider,
                        socks_environment_id,
                        socks5_udp_enabled,
                    )
                    .await
                })
            })
        } else {
            None
        };

        proxies.insert(
            environment_id,
            EnvironmentProxy {
                addrs,
                runtime: EnvironmentProxyRuntime::ListenerTasks {
                    http_task,
                    socks_task,
                },
            },
        );
        Ok(addrs)
    }

    pub async fn replace_config_state(&self, new_state: ConfigState) -> Result<()> {
        let current_cfg = self.state.current_cfg().await?;
        anyhow::ensure!(
            new_state.config.enabled == current_cfg.enabled,
            "cannot update network.enabled on a running proxy"
        );
        anyhow::ensure!(
            new_state.config.proxy_url == current_cfg.proxy_url,
            "cannot update network.proxy_url on a running proxy"
        );
        anyhow::ensure!(
            new_state.config.socks_url == current_cfg.socks_url,
            "cannot update network.socks_url on a running proxy"
        );
        anyhow::ensure!(
            new_state.config.enable_socks5 == current_cfg.enable_socks5,
            "cannot update network.enable_socks5 on a running proxy"
        );
        anyhow::ensure!(
            new_state.config.enable_socks5_udp == current_cfg.enable_socks5_udp,
            "cannot update network.enable_socks5_udp on a running proxy"
        );
        let settings = NetworkProxyRuntimeSettings::from_config(&new_state.config);
        self.state.replace_config_state(new_state).await?;
        let mut guard = self
            .runtime_settings
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        *guard = settings;
        Ok(())
    }

    fn runtime_settings(&self) -> NetworkProxyRuntimeSettings {
        self.runtime_settings
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }

    pub async fn run(&self) -> Result<NetworkProxyHandle> {
        anyhow::ensure!(
            self.execution_scope.is_none(),
            "execution-scoped network proxy is already running"
        );
        let current_cfg = self.state.current_cfg().await?;
        if !current_cfg.enabled {
            warn!("network.enabled is false; skipping proxy listeners");
            return Ok(NetworkProxyHandle::noop());
        }

        if !cfg!(target_os = "windows") && !unix_socket_permissions_supported() {
            warn!(
                "allowUnixSockets and dangerouslyAllowAllUnixSockets are macOS-only; requests will be rejected on this platform"
            );
        }

        let reserved_listeners = self.reserved_listeners.as_ref();
        let http_listener = reserved_listeners.and_then(|listeners| listeners.take_http());
        let socks_listener = reserved_listeners.and_then(|listeners| listeners.take_socks());

        let http_state = self.state.clone();
        let http_decider = self.policy_decider.clone();
        let http_addr = self.http_addr;
        let http_task = tokio::spawn(async move {
            match http_listener {
                Some(listener) => {
                    http_proxy::run_http_proxy_with_std_listener(
                        http_state,
                        listener,
                        http_decider,
                        /*environment_id*/ None,
                    )
                    .await
                }
                None => {
                    http_proxy::run_http_proxy(
                        http_state,
                        http_addr,
                        http_decider,
                        /*environment_id*/ None,
                    )
                    .await
                }
            }
        });

        let socks_task = if current_cfg.enable_socks5 {
            let socks_state = self.state.clone();
            let socks_decider = self.policy_decider.clone();
            let socks_addr = self.socks_addr;
            let enable_socks5_udp = current_cfg.enable_socks5_udp;
            Some(tokio::spawn(async move {
                match socks_listener {
                    Some(listener) => {
                        socks5::run_socks5_with_std_listener(
                            socks_state,
                            listener,
                            socks_decider,
                            /*environment_id*/ None,
                            enable_socks5_udp,
                        )
                        .await
                    }
                    None => {
                        socks5::run_socks5(
                            socks_state,
                            socks_addr,
                            socks_decider,
                            /*environment_id*/ None,
                            enable_socks5_udp,
                        )
                        .await
                    }
                }
            }))
        } else {
            None
        };

        Ok(NetworkProxyHandle {
            http_task: Some(http_task),
            socks_task,
            environment_proxies: self.environment_proxies.clone(),
            completed: false,
        })
    }
}

pub struct NetworkProxyHandle {
    http_task: Option<JoinHandle<Result<()>>>,
    socks_task: Option<JoinHandle<Result<()>>>,
    environment_proxies: Arc<Mutex<HashMap<String, EnvironmentProxy>>>,
    completed: bool,
}

impl std::fmt::Debug for NetworkProxyHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NetworkProxyHandle")
            .field("completed", &self.completed)
            .finish_non_exhaustive()
    }
}

impl NetworkProxyHandle {
    fn noop() -> Self {
        Self {
            http_task: Some(tokio::spawn(async { Ok(()) })),
            socks_task: None,
            environment_proxies: Arc::new(Mutex::new(HashMap::new())),
            completed: true,
        }
    }

    pub async fn wait(mut self) -> Result<()> {
        let http_task = self.http_task.take().context("missing http proxy task")?;
        let socks_task = self.socks_task.take();
        let http_result = http_task.await;
        let socks_result = match socks_task {
            Some(task) => Some(task.await),
            None => None,
        };
        self.completed = true;
        abort_environment_proxies(self.environment_proxies.clone()).await;
        http_result??;
        if let Some(socks_result) = socks_result {
            socks_result??;
        }
        Ok(())
    }

    pub async fn shutdown(mut self) -> Result<()> {
        abort_tasks(self.http_task.take(), self.socks_task.take()).await;
        abort_environment_proxies(self.environment_proxies.clone()).await;
        self.completed = true;
        Ok(())
    }
}

async fn abort_task(task: Option<JoinHandle<Result<()>>>) {
    if let Some(task) = task {
        task.abort();
        let _ = task.await;
    }
}

async fn abort_tasks(
    http_task: Option<JoinHandle<Result<()>>>,
    socks_task: Option<JoinHandle<Result<()>>>,
) {
    abort_task(http_task).await;
    abort_task(socks_task).await;
}

async fn abort_environment_proxies(
    environment_proxies: Arc<Mutex<HashMap<String, EnvironmentProxy>>>,
) {
    let proxies = {
        let mut guard = environment_proxies
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.drain().map(|(_, proxy)| proxy).collect::<Vec<_>>()
    };
    for proxy in proxies {
        let EnvironmentProxyRuntime::ListenerTasks {
            http_task,
            socks_task,
        } = proxy.runtime;
        abort_task(Some(http_task)).await;
        abort_task(socks_task).await;
    }
}

impl Drop for NetworkProxyHandle {
    fn drop(&mut self) {
        if self.completed {
            return;
        }
        let http_task = self.http_task.take();
        let socks_task = self.socks_task.take();
        let environment_proxies = self.environment_proxies.clone();
        tokio::spawn(async move {
            abort_tasks(http_task, socks_task).await;
            abort_environment_proxies(environment_proxies).await;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::NetworkMode;
    use crate::NetworkProxyConfig;
    use crate::state::network_proxy_state_for_policy;
    use pretty_assertions::assert_eq;
    use std::net::IpAddr;
    use std::net::Ipv4Addr;

    fn https_request(host: &str) -> crate::NetworkPolicyRequest {
        crate::NetworkPolicyRequest::new(crate::NetworkPolicyRequestArgs {
            protocol: crate::NetworkProtocol::HttpsConnect,
            host: host.to_string(),
            port: 443,
            environment_id: None,
            client_addr: None,
            method: None,
            command: None,
            exec_policy_hint: None,
        })
    }

    #[tokio::test]
    async fn proxy_startup_applies_unix_socket_permissions_off_windows() -> Result<()> {
        // 改写自 codex `proxy_startup_ignores_macos_unix_socket_permissions_on_windows`
        // 的非 Windows 半边（Windows 共享 ingress 未移植）。
        let unix_sockets = crate::NetworkUnixSocketPermissions {
            entries: vec![
                crate::NetworkUnixSocketPermissionEntry {
                    path: "/tmp/allowed.sock".to_string(),
                    permission: crate::NetworkUnixSocketPermission::Allow,
                },
                crate::NetworkUnixSocketPermissionEntry {
                    path: "/tmp/denied.sock".to_string(),
                    permission: crate::NetworkUnixSocketPermission::Deny,
                },
            ],
        };
        let config = NetworkProxyConfig {
            enabled: true,
            proxy_url: Some("http://0.0.0.0:3128".to_string()),
            socks_url: Some("http://0.0.0.0:8081".to_string()),
            dangerously_allow_non_loopback_proxy: true,
            dangerously_allow_all_unix_sockets: true,
            unix_sockets: Some(unix_sockets.clone()),
            ..NetworkProxyConfig::default()
        };
        let state = Arc::new(network_proxy_state_for_policy(config.clone()));

        let (result, events) = crate::network_policy::test_support::capture_events(|| async {
            NetworkProxy::builder()
                .state(state)
                .managed_by_nova(/*managed_by_nova*/ false)
                .build()
                .await
        })
        .await;
        let proxy = result?;

        assert_eq!(
            (proxy.http_addr, proxy.socks_addr),
            (
                "127.0.0.1:3128".parse::<SocketAddr>()?,
                "127.0.0.1:8081".parse::<SocketAddr>()?,
            )
        );
        let replacement = crate::state::build_config_state(config.clone(), Default::default())?;
        proxy.replace_config_state(replacement).await?;
        assert_eq!(
            proxy.allow_unix_sockets().as_slice(),
            vec!["/tmp/allowed.sock".to_string()].as_slice()
        );
        assert!(proxy.dangerously_allow_all_unix_sockets());
        assert_eq!(proxy.current_cfg().await?, config);

        let remote = proxy.remote_launch_config().await?;
        assert_eq!(
            (
                remote.proxy.unix_sockets,
                remote.proxy.dangerously_allow_all_unix_sockets,
            ),
            (Some(unix_sockets), true)
        );
        let emitted_unix_socket_warning = events.iter().any(|event| {
            event.field("message").is_some_and(|message| {
                message.contains("unix socket proxying is enabled")
                    || message.contains("allowUnixSockets")
            })
        });
        assert!(emitted_unix_socket_warning);

        Ok(())
    }

    #[tokio::test]
    async fn remote_policy_decider_rechecks_live_policy_and_restores_attribution() -> Result<()> {
        let captured = Arc::new(Mutex::new(None));
        let captured_request = Arc::clone(&captured);
        let mut config = NetworkProxyConfig {
            allow_local_binding: true,
            ..NetworkProxyConfig::default()
        };
        config.set_allowed_domains(vec!["controller.example".to_string()]);
        let mut owner_config = NetworkProxyConfig::default();
        owner_config.set_allowed_domains(vec!["owner.example".to_string()]);
        let fallback_policy_decider: Arc<dyn NetworkPolicyDecider> =
            Arc::new(move |request: crate::NetworkPolicyRequest| {
                let captured = Arc::clone(&captured_request);
                async move {
                    *captured
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner) =
                        Some((request.host, request.environment_id, request.execution_id));
                    crate::NetworkDecision::Allow
                }
            });
        let proxy = NetworkProxy::builder()
            .state(Arc::new(network_proxy_state_for_policy(config)))
            .managed_by_nova(/*managed_by_nova*/ false)
            .build()
            .await?;
        let scoped = proxy.for_execution(
            "remote",
            "execution-1",
            "token-1".to_string(),
            Some(crate::EnvironmentNetworkPolicy::from_config(
                &owner_config,
                /*managed_allowed_domains_only*/ false,
            )),
            Some(Arc::clone(&fallback_policy_decider)),
        )?;
        let decider = scoped
            .remote_policy_decider()
            .expect("execution-scoped proxy should expose its policy decider");
        let mut request = https_request("controller.example");
        request.environment_id = Some("forged-environment".to_string());
        request.execution_id = Some("forged-execution".to_string());

        assert_eq!(decider.decide(request).await, crate::NetworkDecision::Allow);
        assert_eq!(
            *captured
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
            Some((
                "controller.example".to_string(),
                Some("remote".to_string()),
                Some("execution-1".to_string())
            ))
        );

        scoped.add_denied_domain("denied.example").await?;
        assert_eq!(
            decider.decide(https_request("denied.example")).await,
            crate::NetworkDecision::deny_with_source(
                crate::reasons::REASON_DENIED,
                crate::NetworkDecisionSource::BaselinePolicy,
            )
        );
        assert_eq!(
            *captured
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
            Some((
                "controller.example".to_string(),
                Some("remote".to_string()),
                Some("execution-1".to_string())
            ))
        );

        owner_config.set_denied_domains(vec!["denied.example".to_string()]);
        assert_eq!(
            scoped.remote_launch_config().await?.proxy.domains,
            owner_config.domains
        );
        let strict_scoped = proxy.for_execution(
            "owner-environment",
            "execution-3",
            "token-3".to_string(),
            Some(crate::EnvironmentNetworkPolicy::from_config(
                &owner_config,
                /*managed_allowed_domains_only*/ true,
            )),
            Some(fallback_policy_decider),
        )?;
        assert!(strict_scoped.remote_policy_decider().is_none());
        Ok(())
    }

    #[tokio::test]
    async fn remote_policy_decider_stops_with_execution_scope() -> Result<()> {
        let decision_started = Arc::new(tokio::sync::Notify::new());
        let decision_started_for_decider = Arc::clone(&decision_started);
        let config = NetworkProxyConfig {
            allow_local_binding: true,
            ..NetworkProxyConfig::default()
        };
        let proxy = NetworkProxy::builder()
            .state(Arc::new(network_proxy_state_for_policy(config)))
            .policy_decider(move |_request: crate::NetworkPolicyRequest| {
                let decision_started = Arc::clone(&decision_started_for_decider);
                async move {
                    decision_started.notify_one();
                    std::future::pending::<crate::NetworkDecision>().await
                }
            })
            .managed_by_nova(/*managed_by_nova*/ false)
            .build()
            .await?;
        let scoped = proxy.for_execution(
            "remote",
            "execution-1",
            "token-1".to_string(),
            /*environment_policy*/ None,
            /*fallback_policy_decider*/ None,
        )?;
        let decider = scoped
            .remote_policy_decider()
            .expect("execution-scoped proxy should expose its policy decider");
        let request = https_request("pending.example");
        let decision = tokio::spawn(async move { decider.decide(request).await });

        decision_started.notified().await;
        drop(scoped);

        let decision = tokio::time::timeout(std::time::Duration::from_secs(1), decision).await??;
        assert_eq!(
            decision,
            crate::NetworkDecision::deny(crate::reasons::REASON_NOT_ALLOWED)
        );
        Ok(())
    }

    #[tokio::test]
    async fn managed_proxy_builder_uses_loopback_ports() {
        let http_listener = StdTcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let http_addr = http_listener.local_addr().unwrap();
        let socks_listener = StdTcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let socks_addr = socks_listener.local_addr().unwrap();
        drop(http_listener);
        drop(socks_listener);

        let state = Arc::new(network_proxy_state_for_policy(NetworkProxyConfig {
            enabled: true,
            proxy_url: Some(format!("http://{http_addr}")),
            socks_url: Some(format!("http://{socks_addr}")),
            ..NetworkProxyConfig::default()
        }));
        let proxy = match NetworkProxy::builder().state(state).build().await {
            Ok(proxy) => proxy,
            Err(err) => {
                if err
                    .chain()
                    .any(|cause| cause.to_string().contains("Operation not permitted"))
                {
                    return;
                }
                panic!("failed to build managed proxy: {err:#}");
            }
        };

        assert!(proxy.http_addr.ip().is_loopback());
        assert!(proxy.socks_addr.ip().is_loopback());
        assert_ne!(proxy.http_addr.port(), 0);
        assert_ne!(proxy.socks_addr.port(), 0);
    }

    #[tokio::test]
    async fn non_managed_proxy_builder_uses_configured_ports() {
        let settings = NetworkProxyConfig {
            proxy_url: Some("http://127.0.0.1:43128".to_string()),
            socks_url: Some("http://127.0.0.1:48081".to_string()),
            ..NetworkProxyConfig::default()
        };
        let state = Arc::new(network_proxy_state_for_policy(settings));
        let proxy = NetworkProxy::builder()
            .state(state)
            .managed_by_nova(/*managed_by_nova*/ false)
            .build()
            .await
            .unwrap();

        assert_eq!(
            proxy.http_addr,
            "127.0.0.1:43128".parse::<SocketAddr>().unwrap()
        );
        assert_eq!(
            proxy.socks_addr,
            "127.0.0.1:48081".parse::<SocketAddr>().unwrap()
        );
    }

    #[tokio::test]
    async fn prepare_for_environment_keeps_env_and_sandbox_ports_in_sync() -> Result<()> {
        // 适配：nova 线上默认 `enable_socks5 = false`（codex 默认为 true），显式打开。
        let state = Arc::new(network_proxy_state_for_policy(NetworkProxyConfig {
            enabled: true,
            enable_socks5: true,
            mode: NetworkMode::Proxy,
            ..NetworkProxyConfig::default()
        }));
        let proxy = NetworkProxy::builder().state(state).build().await?;
        let handle = proxy.run().await?;

        let base_env = HashMap::from([("PRESERVED".to_string(), "value".to_string())]);
        let local = proxy.prepare_for_optional_environment(base_env.clone(), Some("local"))?;
        let remote = proxy.prepare_for_remote_environment(HashMap::new(), "remote")?;

        assert_eq!(
            local.env.get("PRESERVED").map(String::as_str),
            Some("value")
        );
        assert_ne!(local.env.get("HTTP_PROXY"), remote.env.get("HTTP_PROXY"));
        assert_ne!(
            local.env.get("HTTP_PROXY"),
            Some(&format!("http://{}", proxy.http_addr()))
        );
        assert_ne!(
            remote.env.get("HTTP_PROXY"),
            Some(&format!("http://{}", proxy.http_addr()))
        );
        for prepared in [&local, &remote] {
            let http_port = prepared
                .env
                .get("HTTP_PROXY")
                .and_then(|value| value.strip_prefix("http://"))
                .and_then(|value| value.parse::<SocketAddr>().ok())
                .map(|addr| addr.port())
                .expect("managed HTTP proxy address");
            let socks_port = prepared
                .env
                .get("ALL_PROXY")
                .and_then(|value| value.strip_prefix("socks5h://"))
                .and_then(|value| value.parse::<SocketAddr>().ok())
                .map(|addr| addr.port())
                .expect("managed SOCKS proxy address");
            let mut expected_ports = vec![http_port, socks_port];
            expected_ports.sort_unstable();
            expected_ports.dedup();
            assert_eq!(
                prepared.sandbox_context,
                crate::ManagedNetworkSandboxContext {
                    loopback_ports: expected_ports,
                    allow_local_binding: false,
                }
            );
        }
        let mut legacy_env = base_env;
        proxy.apply_to_env_for_environment(&mut legacy_env, "local")?;
        assert_eq!(legacy_env, local.env);

        handle.shutdown().await?;
        Ok(())
    }

    #[tokio::test]
    async fn remote_launch_config_carries_execution_scope() -> Result<()> {
        // 改写自 codex 同名测试：裁掉 credential broker 的 broker-only 判定，
        // 保留执行归因（environment/execution id 与 attribution token 注入）。
        let config = NetworkProxyConfig {
            enabled: true,
            mode: NetworkMode::Proxy,
            ..NetworkProxyConfig::default()
        };
        let state = Arc::new(network_proxy_state_for_policy(config));
        let proxy = match NetworkProxy::builder().state(state).build().await {
            Ok(proxy) => proxy,
            Err(err) => {
                if err
                    .chain()
                    .any(|cause| cause.to_string().contains("Operation not permitted"))
                {
                    return Ok(());
                }
                return Err(err);
            }
        };

        let scoped = proxy.for_execution(
            "remote-env",
            "execution-1",
            "token-1".to_string(),
            /*environment_policy*/ None,
            /*fallback_policy_decider*/ None,
        )?;
        let launch = scoped.remote_launch_config().await?;
        let prepared = scoped.prepare_for_optional_environment(
            HashMap::from([(
                PROXY_ATTRIBUTION_TOKEN_ENV_KEY.to_string(),
                "foreign-token".to_string(),
            )]),
            /*environment_id*/ None,
        )?;

        assert_eq!(launch.environment_id.as_deref(), Some("remote-env"));
        assert_eq!(launch.execution_id.as_deref(), Some("execution-1"));
        assert!(launch.proxy.enabled);
        assert_eq!(
            prepared
                .env
                .get(PROXY_ATTRIBUTION_TOKEN_ENV_KEY)
                .map(String::as_str),
            Some("token-1")
        );
        Ok(())
    }

    #[tokio::test]
    async fn managed_proxy_builder_lazily_upgrades_disabled_socks() {
        let http_listener = StdTcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let http_addr = http_listener.local_addr().unwrap();
        drop(http_listener);
        let occupied_socks = StdTcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).unwrap();
        let socks_addr = occupied_socks.local_addr().unwrap();
        let settings = NetworkProxyConfig {
            enabled: true,
            enable_socks5: false,
            proxy_url: Some(format!("http://{http_addr}")),
            socks_url: Some(format!("http://{socks_addr}")),
            ..NetworkProxyConfig::default()
        };
        let state = Arc::new(network_proxy_state_for_policy(settings));
        let proxy = match NetworkProxy::builder().state(state).build().await {
            Ok(proxy) => proxy,
            Err(err) => {
                if err
                    .chain()
                    .any(|cause| cause.to_string().contains("Operation not permitted"))
                {
                    return;
                }
                panic!("failed to build managed proxy: {err:#}");
            }
        };

        assert!(proxy.http_addr.ip().is_loopback());
        assert_ne!(proxy.http_addr.port(), 0);
        assert_eq!(proxy.socks_addr, socks_addr);
        assert!(
            proxy
                .reserved_listeners
                .as_ref()
                .expect("managed builder should reserve listeners")
                .take_socks()
                .is_none()
        );
        drop(proxy);
        drop(occupied_socks);
    }

    #[test]
    fn proxy_url_env_value_resolves_lowercase_aliases() {
        let mut env = HashMap::new();
        env.insert(
            "http_proxy".to_string(),
            "http://127.0.0.1:3128".to_string(),
        );

        assert_eq!(
            proxy_url_env_value(&env, "HTTP_PROXY"),
            Some("http://127.0.0.1:3128")
        );
    }

    #[test]
    fn has_proxy_url_env_vars_detects_lowercase_aliases() {
        let mut env = HashMap::new();
        env.insert(
            "all_proxy".to_string(),
            "socks5h://127.0.0.1:8081".to_string(),
        );

        assert_eq!(has_proxy_url_env_vars(&env), true);
    }

    #[test]
    fn has_proxy_url_env_vars_detects_websocket_proxy_keys() {
        let mut env = HashMap::new();
        env.insert("wss_proxy".to_string(), "http://127.0.0.1:3128".to_string());

        assert_eq!(has_proxy_url_env_vars(&env), true);
    }

    #[test]
    fn apply_proxy_env_overrides_sets_common_tool_vars() {
        let mut env = HashMap::new();
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        assert_eq!(
            env.get("HTTP_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("WS_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("WSS_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("npm_config_proxy"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("ALL_PROXY"),
            Some(&"socks5h://127.0.0.1:8081".to_string())
        );
        assert_eq!(
            env.get("FTP_PROXY"),
            Some(&"socks5h://127.0.0.1:8081".to_string())
        );
        assert_eq!(env.get("NO_PROXY"), Some(&String::new()));
        assert_eq!(env.get(PROXY_ACTIVE_ENV_KEY), Some(&"1".to_string()));
        assert_eq!(env.get(ALLOW_LOCAL_BINDING_ENV_KEY), Some(&"0".to_string()));
        assert_eq!(
            env.get(ELECTRON_GET_USE_PROXY_ENV_KEY),
            Some(&"true".to_string())
        );
        assert_eq!(env.get(NODE_USE_ENV_PROXY_ENV_KEY), Some(&"1".to_string()));
        #[cfg(target_os = "macos")]
        assert_eq!(
            env.get(GIT_SSH_COMMAND_ENV_KEY),
            Some(
                &"CODEX_PROXY_GIT_SSH_COMMAND=1 ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:8081 %h %p'"
                    .to_string()
            )
        );
        #[cfg(not(target_os = "macos"))]
        assert_eq!(env.get(GIT_SSH_COMMAND_ENV_KEY), None);
    }

    #[test]
    fn apply_proxy_env_overrides_keeps_local_targets_direct_when_local_binding_enabled() {
        let mut env = HashMap::new();
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ true,
        );

        assert_eq!(
            env.get("NO_PROXY"),
            Some(&DEFAULT_NO_PROXY_VALUE.to_string())
        );
    }

    #[test]
    fn apply_proxy_env_overrides_sets_only_expected_env_keys() {
        let mut env = HashMap::new();
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        for key in env.keys() {
            let is_managed_git_ssh_key =
                cfg!(target_os = "macos") && key == GIT_SSH_COMMAND_ENV_KEY;
            assert!(
                crate::PROXY_ENV_KEYS.contains(&key.as_str()) || is_managed_git_ssh_key,
                "proxy env writer set unexpected key: {key}"
            );
        }
    }

    // 裁点：codex 的 MITM CA bundle 环境变量注入测试随 MITM 面裁掉。

    #[test]
    fn apply_proxy_env_overrides_uses_http_for_all_proxy_without_socks() {
        let mut env = HashMap::new();
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ false,
            /*allow_local_binding*/ true,
        );

        assert_eq!(
            env.get("ALL_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(env.get(ALLOW_LOCAL_BINDING_ENV_KEY), Some(&"1".to_string()));
    }

    #[test]
    fn apply_proxy_env_overrides_uses_plain_http_proxy_url() {
        let mut env = HashMap::new();
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        assert_eq!(
            env.get("HTTP_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("HTTPS_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("WS_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("WSS_PROXY"),
            Some(&"http://127.0.0.1:3128".to_string())
        );
        assert_eq!(
            env.get("ALL_PROXY"),
            Some(&"socks5h://127.0.0.1:8081".to_string())
        );
        #[cfg(target_os = "macos")]
        assert_eq!(
            env.get(GIT_SSH_COMMAND_ENV_KEY),
            Some(
                &"CODEX_PROXY_GIT_SSH_COMMAND=1 ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:8081 %h %p'"
                    .to_string()
            )
        );
        #[cfg(not(target_os = "macos"))]
        assert_eq!(env.get(GIT_SSH_COMMAND_ENV_KEY), None);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn apply_proxy_env_overrides_preserves_existing_git_ssh_command() {
        let mut env = HashMap::new();
        env.insert(
            GIT_SSH_COMMAND_ENV_KEY.to_string(),
            "ssh -o ProxyCommand='tsh proxy ssh --cluster=dev %r@%h:%p'".to_string(),
        );
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        assert_eq!(
            env.get(GIT_SSH_COMMAND_ENV_KEY),
            Some(&"ssh -o ProxyCommand='tsh proxy ssh --cluster=dev %r@%h:%p'".to_string())
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn apply_proxy_env_overrides_preserves_unmarked_git_ssh_command_with_proxy_shape() {
        let mut env = HashMap::new();
        env.insert(
            GIT_SSH_COMMAND_ENV_KEY.to_string(),
            "ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:8081 %h %p'".to_string(),
        );
        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 3128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 48081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        assert_eq!(
            env.get(GIT_SSH_COMMAND_ENV_KEY),
            Some(&"ssh -o ProxyCommand='nc -X 5 -x 127.0.0.1:8081 %h %p'".to_string())
        );
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn apply_proxy_env_overrides_refreshes_previous_nova_proxy_git_ssh_command() {
        let mut env = HashMap::new();
        env.insert(
            GIT_SSH_COMMAND_ENV_KEY.to_string(),
            nova_proxy_git_ssh_command(SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8081)),
        );

        apply_proxy_env_overrides(
            &mut env,
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 43128),
            SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 48081),
            /*socks_enabled*/ true,
            /*allow_local_binding*/ false,
        );

        assert_eq!(
            env.get(GIT_SSH_COMMAND_ENV_KEY),
            Some(&nova_proxy_git_ssh_command(SocketAddr::new(
                IpAddr::V4(Ipv4Addr::LOCALHOST),
                48081
            )))
        );
    }
}
