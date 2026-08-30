//! nova-executor 网络代理
//!
//! 从 codex-network-proxy（codex-rs/network-proxy）移植的网络沙箱核心：HTTP/SOCKS5 代理 +
//! 域名白名单策略。本文件是**线上类型的家**：`RemoteNetworkProxyConfig` 等类型的 serde
//! 形状是 executor 协议契约，不能随 codex 上游改动。
//!
//! 第一期裁剪（相对 codex）：
//! - MITM 面（`mitm.rs` / `mitm_hook.rs` 实现 / `certs.rs` / `native_certs.rs`）未移植；
//!   `MitmHookConfig` 等线上类型保留但暂无运行时使用者，`build_config_state` 对
//!   MITM/hooks 配置 fail fast，`managed_mitm_ca_trust_bundle_path` 恒为 None。
//! - 凭证经纪（`credential_broker/`）未移植。
//! - `request_disconnect`（HTTP 请求放弃跟踪）未移植：`NetworkPolicyRequest` 无
//!   `disconnect` 字段。
//! - Windows 专属面（`windows_proxy_ingress` / `windows_tcp_attribution`）未移植；
//!   `network_proxy_restricting_sid` 保留 cfg(windows) 占位（恒 None）。

mod attribution;
mod authorization_path;
mod config;
mod connect_policy;
mod environment_policy;
mod http_proxy;
mod network_policy;
mod policy;
mod proxy;
pub(crate) mod reasons;
mod remote_config;
mod responses;
mod runtime;
mod socks5;
mod state;
mod upstream;

pub use attribution::write_attribution_frame;
pub use config::host_and_port_from_network_addr;
pub use config::managed_proxy_ports;
pub use environment_policy::EnvironmentNetworkPolicy;
pub use network_policy::NetworkPolicyAuditEvent;
pub use network_policy::NetworkPolicyAuditObserver;
pub use policy::normalize_host;
pub use proxy::ALL_PROXY_ENV_KEYS;
pub use proxy::ALLOW_LOCAL_BINDING_ENV_KEY;
pub use proxy::Args;
#[cfg(target_os = "macos")]
pub use proxy::NOVA_PROXY_GIT_SSH_COMMAND_MARKER;
pub use proxy::DEFAULT_NO_PROXY_VALUE;
pub use proxy::NO_PROXY_ENV_KEYS;
pub use proxy::NetworkProxy;
pub use proxy::NetworkProxyBuilder;
pub use proxy::NetworkProxyHandle;
pub use proxy::PROXY_ACTIVE_ENV_KEY;
pub use proxy::PROXY_ENV_KEYS;
#[cfg(target_os = "macos")]
pub use proxy::PROXY_GIT_SSH_COMMAND_ENV_KEY;
pub use proxy::PROXY_URL_ENV_KEYS;
pub use proxy::has_proxy_url_env_vars;
pub use proxy::is_managed_proxy_env_var;
pub use proxy::proxy_url_env_value;
pub use proxy::strip_managed_proxy_env;
pub use runtime::BlockedRequestArgs;
pub use runtime::BlockedRequestObserver;
pub use runtime::BlockedRequestObserverFuture;
pub use runtime::ConfigReloader;
pub use runtime::ConfigReloaderFuture;
pub use runtime::ConfigState;
pub use runtime::NetworkProxyState;
pub use state::NetworkProxyConstraintError;
pub use state::NetworkProxyConstraints;
pub use state::PartialNetworkProxyConfig;
pub use state::build_config_state;
pub use state::validate_policy_against_constraints;

use reasons::REASON_POLICY_DENIED;
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
///
/// 线上契约：`None` = 无网络访问（全部流量拒绝）；`Proxy` = 全量代理放行
/// （对位 codex 的 `Full`）。codex 的 `Limited`（只读 HTTP 方法 + 强制 MITM）
/// 依赖第一期裁掉的 MITM 面，未引入线上枚举。
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
///
/// 行为方法（`set_allowed_domains` / `allowed_domains` / `upsert_domain_permission` 等）
/// 在 `config` 模块以扩展 impl 提供；运行时约束：
/// MITM / 凭证经纪 / MITM hooks 字段仅为线上兼容保留，配置非空时
/// `build_config_state` 会 fail fast。
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
///
/// 可以安全发往远程 executor 的生效网络代理设置。监听器地址刻意省略（executor
/// 自选 loopback 端口）；MITM、凭证注入与 hooks 不在其中，使其配置无法意外越过
/// exec-server 边界。
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

/// 可信网桥 attribution 帧的 env 键（跨进程契约，取值不变）。
pub const PROXY_ATTRIBUTION_TOKEN_ENV_KEY: &str = "NOVA_EXECUTOR_PROXY_ATTRIBUTION_TOKEN";

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
// 网络策略类型（运行时契约，经 decider 回调跨越 crate 边界）
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
        let reason = reason.into();
        let reason = if reason.is_empty() {
            REASON_POLICY_DENIED.to_string()
        } else {
            reason
        };
        Self::Deny {
            reason,
            source,
            decision: NetworkPolicyDecision::Deny,
        }
    }

    pub fn ask_with_source(reason: impl Into<String>, source: NetworkDecisionSource) -> Self {
        let reason = reason.into();
        let reason = if reason.is_empty() {
            REASON_POLICY_DENIED.to_string()
        } else {
            reason
        };
        Self::Deny {
            reason,
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

/// 自定义 CA 环境变量键（MITM 恢复后由代理写入；第一期仅供调用方识别这些键）。
pub const CUSTOM_CA_ENV_KEYS: &[&str] = &[
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
];

/// 路径是否为托管 MITM CA trust bundle。
///
/// 裁点：MITM 面第一期未移植，不存在任何托管 bundle，恒为 false。
pub fn is_managed_mitm_ca_trust_bundle_path(_path: &str) -> bool {
    false
}

/// 环境特定的管理网络设置
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreparedManagedNetwork {
    pub env: std::collections::HashMap<String, String>,
    pub sandbox_context: ManagedNetworkSandboxContext,
}

// ---------------------------------------------------------------------------
// MITM hook 线上类型（实现未随第一期移植；保留 serde 形状）
// ---------------------------------------------------------------------------

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
