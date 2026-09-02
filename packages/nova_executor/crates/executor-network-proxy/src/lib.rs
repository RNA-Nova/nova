//! nova-executor 网络代理类型定义
//!
//! 从 codex-network-proxy 提取核心类型，去除复杂网络代理实现。

use serde::Deserialize;
use serde::Serialize;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

/// 管理网络 sandbox 上下文
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagedNetworkSandboxContext {
    /// Loopback proxy ports that sandboxed commands may connect to.
    #[serde(default)]
    pub loopback_ports: Vec<u16>,
    /// Whether the command may bind local sockets and exchange loopback traffic.
    #[serde(default)]
    pub allow_local_binding: bool,
}

/// 网络模式
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum NetworkMode {
    #[default]
    None,
    Proxy,
}

/// 网络域名权限
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkDomainPermissions {
    #[serde(default)]
    pub entries: Vec<NetworkDomainPermissionEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkDomainPermissionEntry {
    pub domain: String,
    pub permission: NetworkDomainPermission,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum NetworkDomainPermission {
    Allow,
    Deny,
}

/// Unix socket 权限
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkUnixSocketPermissions {
    #[serde(default)]
    pub entries: Vec<NetworkUnixSocketPermissionEntry>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkUnixSocketPermissionEntry {
    pub path: String,
    pub permission: NetworkUnixSocketPermission,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum NetworkUnixSocketPermission {
    Allow,
    Deny,
}

/// 网络代理审计元数据
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkProxyAuditMetadata {
    #[serde(default)]
    pub conversation_id: Option<String>,
    #[serde(default)]
    pub turn_id: Option<String>,
}

/// 网络策略决策
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum NetworkPolicyDecision {
    Deny,
    Ask,
}

impl NetworkPolicyDecision {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Deny => "deny",
            Self::Ask => "ask",
        }
    }
}

/// 网络决策来源
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum NetworkDecisionSource {
    BaselinePolicy,
    ModeGuard,
    ProxyState,
    Decider,
}

impl NetworkDecisionSource {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::BaselinePolicy => "baseline_policy",
            Self::ModeGuard => "mode_guard",
            Self::ProxyState => "proxy_state",
            Self::Decider => "decider",
        }
    }
}

/// 网络代理配置
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkProxyConfig {
    pub enabled: bool,
    #[serde(default)]
    pub enable_socks5: bool,
    #[serde(default)]
    pub enable_socks5_udp: bool,
    #[serde(default)]
    pub allow_upstream_proxy: bool,
    #[serde(default)]
    pub dangerously_allow_all_unix_sockets: bool,
    #[serde(default)]
    pub mode: NetworkMode,
    #[serde(default)]
    pub domains: Option<NetworkDomainPermissions>,
    #[serde(default)]
    pub unix_sockets: Option<NetworkUnixSocketPermissions>,
    #[serde(default)]
    pub allow_local_binding: bool,
    #[serde(default)]
    pub mitm: bool,
    #[serde(default)]
    pub credential_broker: bool,
    #[serde(default)]
    pub dangerously_allow_plaintext_credential_injection: bool,
    #[serde(default)]
    pub mitm_hooks: Vec<String>,
    #[serde(default)]
    pub proxy_url: Option<String>,
    #[serde(default)]
    pub socks_url: Option<String>,
    #[serde(default)]
    pub dangerously_allow_non_loopback_proxy: bool,
}

impl NetworkProxyConfig {
    pub fn set_allowed_domains(&mut self, domains: Vec<String>) {
        self.domains = Some(NetworkDomainPermissions {
            entries: domains
                .into_iter()
                .map(|domain| NetworkDomainPermissionEntry {
                    domain,
                    permission: NetworkDomainPermission::Allow,
                })
                .collect(),
        });
    }
}

/// 远程网络代理启动配置
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
#[non_exhaustive]
pub struct RemoteNetworkProxyLaunchConfig {
    pub proxy: RemoteNetworkProxyConfig,
    #[serde(default)]
    pub audit_metadata: NetworkProxyAuditMetadata,
    #[serde(default)]
    pub environment_id: Option<String>,
    #[serde(default)]
    pub execution_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub policy_decision_timeout_ms: Option<u64>,
}

impl RemoteNetworkProxyLaunchConfig {
    pub fn new(proxy: RemoteNetworkProxyConfig) -> Self {
        Self {
            proxy,
            audit_metadata: NetworkProxyAuditMetadata::default(),
            environment_id: None,
            execution_id: None,
            policy_decision_timeout_ms: None,
        }
    }

    pub fn with_audit_metadata(mut self, audit_metadata: NetworkProxyAuditMetadata) -> Self {
        self.audit_metadata = audit_metadata;
        self
    }

    pub fn for_execution(mut self, environment_id: String, execution_id: String) -> Self {
        self.environment_id = Some(environment_id);
        self.execution_id = Some(execution_id);
        self
    }
}

/// 远程网络代理配置
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
#[non_exhaustive]
pub struct RemoteNetworkProxyConfig {
    pub enabled: bool,
    pub enable_socks5: bool,
    pub enable_socks5_udp: bool,
    pub allow_upstream_proxy: bool,
    pub dangerously_allow_all_unix_sockets: bool,
    pub mode: NetworkMode,
    pub domains: Option<NetworkDomainPermissions>,
    pub unix_sockets: Option<NetworkUnixSocketPermissions>,
    pub allow_local_binding: bool,
}

impl RemoteNetworkProxyConfig {
    pub fn from_effective_config(config: &NetworkProxyConfig) -> Result<Self, anyhow::Error> {
        anyhow::ensure!(
            !config.enabled
                || (!config.mitm
                    && !config.credential_broker
                    && !config.dangerously_allow_plaintext_credential_injection
                    && config.mitm_hooks.is_empty()),
            "remote exec-server network proxy does not support MITM, credential injection, or MITM hooks"
        );
        Ok(Self {
            enabled: config.enabled,
            enable_socks5: config.enable_socks5,
            enable_socks5_udp: config.enable_socks5_udp,
            allow_upstream_proxy: config.allow_upstream_proxy,
            dangerously_allow_all_unix_sockets: config.dangerously_allow_all_unix_sockets,
            mode: config.mode,
            domains: config.domains.clone(),
            unix_sockets: config.unix_sockets.clone(),
            allow_local_binding: config.allow_local_binding,
        })
    }
}

// ---------------------------------------------------------------------------
// 从 codex-network-proxy 提取的常量和辅助函数
// ---------------------------------------------------------------------------

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
pub const PROXY_ATTRIBUTION_TOKEN_ENV_KEY: &str = "CODEX_PROXY_ATTRIBUTION_TOKEN";
pub const PROXY_ENV_KEYS: &[&str] = &[
    PROXY_ACTIVE_ENV_KEY,
    ALLOW_LOCAL_BINDING_ENV_KEY,
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

pub fn proxy_url_env_value<'a>(
    env: &'a std::collections::HashMap<String, String>,
    canonical_key: &str,
) -> Option<&'a str> {
    if let Some(value) = env.get(canonical_key) {
        return Some(value.as_str());
    }
    let lower_key = canonical_key.to_ascii_lowercase();
    env.get(lower_key.as_str()).map(String::as_str)
}

pub fn has_proxy_url_env_vars(env: &std::collections::HashMap<String, String>) -> bool {
    PROXY_URL_ENV_KEYS
        .iter()
        .any(|key| proxy_url_env_value(env, key).is_some_and(|value| !value.trim().is_empty()))
}

pub fn is_managed_proxy_env_var(key: &str, _value: &str) -> bool {
    PROXY_ENV_KEYS.contains(&key)
}

pub fn strip_managed_proxy_env(env: &mut std::collections::HashMap<String, String>) {
    env.retain(|key, value| !is_managed_proxy_env_var(key, value));
}

/// 被阻止的请求
#[derive(Clone, Debug, Serialize)]
pub struct BlockedRequest {
    pub host: String,
    pub reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub client: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<NetworkMode>,
    pub protocol: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decision: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub port: Option<u16>,
    pub timestamp: i64,
}

/// Windows sandbox 代理设置模式（跨平台占位类型）
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum WindowsSandboxProxySettingsMode {
    #[default]
    Reconcile,
    Preserve,
}

// ---------------------------------------------------------------------------
// 从 codex-network-proxy 提取的网络策略类型
// ---------------------------------------------------------------------------

/// 网络协议
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NetworkProtocol {
    Http,
    HttpsConnect,
    Socks5Tcp,
    Socks5Udp,
}

impl NetworkProtocol {
    pub const fn as_policy_protocol(self) -> &'static str {
        match self {
            Self::Http => "http",
            Self::HttpsConnect => "https_connect",
            Self::Socks5Tcp => "socks5_tcp",
            Self::Socks5Udp => "socks5_udp",
        }
    }
}

/// 网络策略请求
#[derive(Clone, Debug)]
pub struct NetworkPolicyRequest {
    pub protocol: NetworkProtocol,
    pub host: String,
    pub port: u16,
    pub environment_id: Option<String>,
    pub client_addr: Option<String>,
    pub method: Option<String>,
    pub command: Option<String>,
    pub exec_policy_hint: Option<String>,
    pub execution_id: Option<String>,
}

/// 网络策略请求参数
#[derive(Clone, Debug)]
pub struct NetworkPolicyRequestArgs {
    pub protocol: NetworkProtocol,
    pub host: String,
    pub port: u16,
    pub environment_id: Option<String>,
    pub client_addr: Option<String>,
    pub method: Option<String>,
    pub command: Option<String>,
    pub exec_policy_hint: Option<String>,
}

impl NetworkPolicyRequest {
    pub fn new(args: NetworkPolicyRequestArgs) -> Self {
        let NetworkPolicyRequestArgs {
            protocol,
            host,
            port,
            environment_id,
            client_addr,
            method,
            command,
            exec_policy_hint,
        } = args;
        Self {
            protocol,
            host,
            port,
            environment_id,
            client_addr,
            method,
            command,
            exec_policy_hint,
            execution_id: None,
        }
    }
}

/// 网络决策
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NetworkDecision {
    Allow,
    Deny {
        reason: String,
        source: NetworkDecisionSource,
        decision: NetworkPolicyDecision,
    },
}

impl NetworkDecision {
    pub fn deny(reason: impl Into<String>) -> Self {
        Self::deny_with_source(reason, NetworkDecisionSource::Decider)
    }

    pub fn ask(reason: impl Into<String>) -> Self {
        Self::ask_with_source(reason, NetworkDecisionSource::Decider)
    }

    pub fn deny_with_source(reason: impl Into<String>, source: NetworkDecisionSource) -> Self {
        Self::Deny {
            reason: reason.into(),
            source,
            decision: NetworkPolicyDecision::Deny,
        }
    }

    pub fn ask_with_source(reason: impl Into<String>, source: NetworkDecisionSource) -> Self {
        Self::Deny {
            reason: reason.into(),
            source,
            decision: NetworkPolicyDecision::Ask,
        }
    }
}

/// 网络策略决策 trait
pub trait NetworkPolicyDecider: Send + Sync + 'static {
    fn decide(&self, req: NetworkPolicyRequest) -> NetworkPolicyDeciderFuture<'_>;
}

pub type NetworkPolicyDeciderFuture<'a> =
    Pin<Box<dyn Future<Output = NetworkDecision> + Send + 'a>>;

impl<D: NetworkPolicyDecider + ?Sized> NetworkPolicyDecider for Arc<D> {
    fn decide(&self, req: NetworkPolicyRequest) -> NetworkPolicyDeciderFuture<'_> {
        Box::pin(async move { (**self).decide(req).await })
    }
}

impl<F, Fut> NetworkPolicyDecider for F
where
    F: Fn(NetworkPolicyRequest) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = NetworkDecision> + Send + 'static,
{
    fn decide(&self, req: NetworkPolicyRequest) -> NetworkPolicyDeciderFuture<'_> {
        Box::pin((self)(req))
    }
}

/// 网络代理状态（占位）
#[derive(Clone, Debug, Default)]
pub struct NetworkProxyState;

impl NetworkProxyState {
    pub fn from_remote_launch_config(_config: RemoteNetworkProxyLaunchConfig) -> Result<Self, anyhow::Error> {
        Ok(Self)
    }
}

/// 自定义 CA 环境变量键
pub const CUSTOM_CA_ENV_KEYS: &[&str] = &[
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
];

pub fn is_managed_mitm_ca_trust_bundle_path(_path: &str) -> bool {
    false
}

/// 网络代理（占位实现，真实网络代理功能后续补充）
#[derive(Clone, Debug)]
pub struct NetworkProxy;

impl NetworkProxy {
    pub fn builder() -> NetworkProxyBuilder {
        NetworkProxyBuilder::new()
    }

    pub async fn run(&self) -> Result<NetworkProxyHandle, anyhow::Error> {
        Ok(NetworkProxyHandle)
    }

    pub fn prepare_for_optional_environment(
        &self,
        env: std::collections::HashMap<String, String>,
        _environment_id: Option<&str>,
    ) -> Result<PreparedManagedNetwork, anyhow::Error> {
        Ok(PreparedManagedNetwork {
            env,
            sandbox_context: ManagedNetworkSandboxContext::default(),
        })
    }

    pub fn managed_mitm_ca_trust_bundle_path(&self) -> Option<nova_executor_utils_absolute_path::AbsolutePathBuf> {
        None
    }

    pub fn dangerously_allow_all_unix_sockets(&self) -> bool {
        false
    }

    pub fn allow_unix_sockets(&self) -> Vec<String> {
        Vec::new()
    }

    pub fn apply_to_env_for_optional_environment(
        &self,
        _env: &mut std::collections::HashMap<String, String>,
        _environment_id: Option<&str>,
    ) -> Result<(), anyhow::Error> {
        Ok(())
    }

    pub fn allow_local_binding(&self) -> bool {
        false
    }
}

#[derive(Clone)]
pub struct NetworkProxyBuilder {
    state: Option<Arc<NetworkProxyState>>,
    policy_decider: Option<Arc<dyn NetworkPolicyDecider>>,
}

impl std::fmt::Debug for NetworkProxyBuilder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NetworkProxyBuilder")
            .field("state", &self.state)
            .field("policy_decider", &self.policy_decider.as_ref().map(|_| "<dyn NetworkPolicyDecider>"))
            .finish()
    }
}

impl NetworkProxyBuilder {
    pub fn new() -> Self {
        Self {
            state: None,
            policy_decider: None,
        }
    }

    pub fn state(mut self, state: Arc<NetworkProxyState>) -> Self {
        self.state = Some(state);
        self
    }

    pub fn policy_decider_arc(mut self, decider: Arc<dyn NetworkPolicyDecider>) -> Self {
        self.policy_decider = Some(decider);
        self
    }

    pub async fn build(self) -> Result<NetworkProxy, anyhow::Error> {
        Ok(NetworkProxy)
    }
}

/// 环境特定的管理网络设置
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreparedManagedNetwork {
    pub env: std::collections::HashMap<String, String>,
    pub sandbox_context: ManagedNetworkSandboxContext,
}

/// 网络代理句柄
#[derive(Clone, Debug)]
pub struct NetworkProxyHandle;

impl NetworkProxyHandle {
    pub async fn shutdown(self) -> Result<(), anyhow::Error> {
        Ok(())
    }
}

/// 规范化主机名
pub fn normalize_host(host: &str) -> String {
    host.trim().to_ascii_lowercase()
}

/// MITM hook 配置
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MitmHookConfig {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub match_config: Option<MitmHookMatchConfig>,
    #[serde(default)]
    pub actions: Option<MitmHookActionsConfig>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MitmHookMatchConfig {
    #[serde(default)]
    pub host: Option<String>,
    #[serde(default)]
    pub path: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MitmHookActionsConfig {
    #[serde(default)]
    pub request: Option<MitmHookBodyConfig>,
    #[serde(default)]
    pub response: Option<MitmHookBodyConfig>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MitmHookBodyConfig {
    #[serde(default)]
    pub inject_headers: Vec<InjectedHeaderConfig>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InjectedHeaderConfig {
    pub name: String,
    pub value: String,
}

