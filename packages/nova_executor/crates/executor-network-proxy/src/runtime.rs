//! 代理运行时状态（移植自 codex network-proxy `runtime.rs`）。
//!
//! 裁点（第一期裁剪，均已在引用点缝合）：
//! - MITM 面：`ConfigState.mitm` / `mitm_hooks` 字段、`mitm_state()` /
//!   `evaluate_mitm_hook_request()` / `host_mitm_requirement()` / `HostMitmRequirement`
//!   未移植；`build_config_state` 遇 MITM/hook 配置直接报错（fail fast）。
//! - 凭证经纪：`CredentialBroker` 字段与 `virtualize_child_credentials` /
//!   `restore_child_credentials` / `virtualize_brokered_text` / `inject_request_credentials` /
//!   `plaintext_credential_injection_enabled` 未移植；`current_cfg_with_brokerage_provenance`
//!   随之裁掉（broker 创建的默认白名单概念不复存在）。
//! - `BlockedRequest` 以 crate 根线上类型为准：无 codex 端 `execution_id` 字段。

use crate::NetworkDomainPermission;
use crate::NetworkMode;
use crate::NetworkProxyAuditMetadata;
use crate::NetworkProxyConfig;
use crate::config::ValidatedUnixSocketPath;
use crate::network_policy::NetworkPolicyAuditObserver;
use crate::policy::Host;
use crate::policy::is_loopback_host;
use crate::policy::is_non_public_ip;
use crate::policy::normalize_host;
use crate::policy::unscoped_ip_literal;
use crate::reasons::REASON_DENIED;
use crate::reasons::REASON_NOT_ALLOWED;
use crate::reasons::REASON_NOT_ALLOWED_LOCAL;
use crate::state::NetworkProxyConstraintError;
use crate::state::NetworkProxyConstraints;
use crate::state::build_config_state;
use crate::state::validate_policy_against_constraints;
use anyhow::Context;
use anyhow::Result;
use globset::GlobSet;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use std::collections::HashMap;
use std::collections::HashSet;
use std::collections::VecDeque;
use std::future::Future;
use std::net::IpAddr;
use std::net::SocketAddr;
use std::path::Path;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Duration;
use time::OffsetDateTime;
use tokio::net::lookup_host;
use tokio::sync::RwLock;
use tokio::time::timeout;
use tracing::debug;
use tracing::info;
use tracing::warn;

const MAX_BLOCKED_EVENTS: usize = 200;
const DNS_LOOKUP_TIMEOUT: Duration = Duration::from_secs(2);
/// 违规日志行前缀（日志管线契约，沿用 codex 取值；nova 遥测命名先例见 executor-otel）。
const NETWORK_POLICY_VIOLATION_PREFIX: &str = "CODEX_NETWORK_POLICY_VIOLATION";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HostBlockReason {
    Denied,
    NotAllowed,
    NotAllowedLocal,
}

impl HostBlockReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Denied => REASON_DENIED,
            Self::NotAllowed => REASON_NOT_ALLOWED,
            Self::NotAllowedLocal => REASON_NOT_ALLOWED_LOCAL,
        }
    }
}

impl std::fmt::Display for HostBlockReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HostBlockDecision {
    Allowed,
    Blocked(HostBlockReason),
}

/// 被阻止请求的构造参数（`BlockedRequest` 线上类型本体在 crate 根）。
pub struct BlockedRequestArgs {
    pub host: String,
    pub reason: String,
    pub client: Option<String>,
    pub method: Option<String>,
    pub mode: Option<NetworkMode>,
    pub protocol: String,
    pub decision: Option<String>,
    pub source: Option<String>,
    pub port: Option<u16>,
}

impl crate::BlockedRequest {
    pub fn new(args: BlockedRequestArgs) -> Self {
        let BlockedRequestArgs {
            host,
            reason,
            client,
            method,
            mode,
            protocol,
            decision,
            source,
            port,
        } = args;
        Self {
            host,
            reason,
            client,
            method,
            mode,
            protocol,
            decision,
            source,
            port,
            timestamp: unix_timestamp(),
        }
    }
}

fn blocked_request_violation_log_line(entry: &crate::BlockedRequest) -> String {
    match serde_json::to_string(entry) {
        Ok(json) => format!("{NETWORK_POLICY_VIOLATION_PREFIX} {json}"),
        Err(err) => {
            debug!("failed to serialize blocked request for violation log: {err}");
            format!(
                "{NETWORK_POLICY_VIOLATION_PREFIX} host={} reason={}",
                entry.host, entry.reason
            )
        }
    }
}

#[derive(Clone)]
pub struct ConfigState {
    pub config: NetworkProxyConfig,
    pub allow_set: GlobSet,
    pub deny_set: GlobSet,
    pub constraints: NetworkProxyConstraints,
    pub blocked: VecDeque<crate::BlockedRequest>,
    pub blocked_total: u64,
}

pub trait ConfigReloader: Send + Sync {
    /// 配置来源的人类可读描述，用于日志。
    fn source_label(&self) -> String;

    /// 需要重载时返回新加载的状态；否则返回 `None`。
    fn maybe_reload(&self) -> ConfigReloaderFuture<'_, Option<ConfigState>>;

    /// 无论是否检测到变化都强制重载。
    fn reload_now(&self) -> ConfigReloaderFuture<'_, ConfigState>;
}

pub type ConfigReloaderFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T>> + Send + 'a>>;

struct StaticConfigReloader;

impl ConfigReloader for StaticConfigReloader {
    fn source_label(&self) -> String {
        "static config state".to_string()
    }

    fn maybe_reload(&self) -> ConfigReloaderFuture<'_, Option<ConfigState>> {
        Box::pin(async { Ok(None) })
    }

    fn reload_now(&self) -> ConfigReloaderFuture<'_, ConfigState> {
        Box::pin(async { anyhow::bail!("static config state cannot be reloaded") })
    }
}

pub trait BlockedRequestObserver: Send + Sync + 'static {
    fn on_blocked_request(&self, request: crate::BlockedRequest)
    -> BlockedRequestObserverFuture<'_>;
}

pub type BlockedRequestObserverFuture<'a> = Pin<Box<dyn Future<Output = ()> + Send + 'a>>;

impl<O: BlockedRequestObserver + ?Sized> BlockedRequestObserver for Arc<O> {
    fn on_blocked_request(
        &self,
        request: crate::BlockedRequest,
    ) -> BlockedRequestObserverFuture<'_> {
        Box::pin(async move { (**self).on_blocked_request(request).await })
    }
}

impl<F, Fut> BlockedRequestObserver for F
where
    F: Fn(crate::BlockedRequest) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = ()> + Send + 'static,
{
    fn on_blocked_request(
        &self,
        request: crate::BlockedRequest,
    ) -> BlockedRequestObserverFuture<'_> {
        Box::pin((self)(request))
    }
}

pub struct NetworkProxyState {
    state: Arc<RwLock<ConfigState>>,
    reloader: Arc<dyn ConfigReloader>,
    blocked_request_observer: Arc<RwLock<Option<Arc<dyn BlockedRequestObserver>>>>,
    pub(crate) policy_audit_observer: Option<NetworkPolicyAuditObserver>,
    audit_metadata: NetworkProxyAuditMetadata,
    execution_attributions: Arc<Mutex<HashMap<String, ExecutionAttribution>>>,
    environment_id: Option<Arc<str>>,
    execution_id: Option<Arc<str>>,
}

#[derive(Clone)]
struct ExecutionAttribution {
    environment_id: String,
    execution_id: String,
}

impl std::fmt::Debug for NetworkProxyState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // 避免记录内部状态（配置内容、派生 globset 等可能含敏感路径且噪音大）。
        f.debug_struct("NetworkProxyState").finish_non_exhaustive()
    }
}

impl Clone for NetworkProxyState {
    fn clone(&self) -> Self {
        Self {
            state: self.state.clone(),
            reloader: self.reloader.clone(),
            blocked_request_observer: self.blocked_request_observer.clone(),
            policy_audit_observer: self.policy_audit_observer.clone(),
            audit_metadata: self.audit_metadata.clone(),
            execution_attributions: self.execution_attributions.clone(),
            environment_id: self.environment_id.clone(),
            execution_id: self.execution_id.clone(),
        }
    }
}

impl NetworkProxyState {
    /// 为一次 executor 本地代理启动构建运行时状态。
    pub fn from_remote_launch_config(
        launch: crate::RemoteNetworkProxyLaunchConfig,
    ) -> Result<Self> {
        let crate::RemoteNetworkProxyLaunchConfig {
            proxy,
            audit_metadata,
            environment_id,
            execution_id,
            policy_decision_timeout_ms: _,
        } = launch;
        anyhow::ensure!(
            proxy.enabled,
            "executor-local network proxy launch requires an enabled proxy"
        );
        let config = proxy.into_network_proxy_config();
        let state = build_config_state(config, NetworkProxyConstraints::default())?;
        Ok(Self {
            environment_id: environment_id.map(Into::into),
            execution_id: execution_id.map(Into::into),
            ..Self::with_reloader_and_audit_metadata(
                state,
                Arc::new(StaticConfigReloader),
                audit_metadata,
            )
        })
    }

    pub fn with_reloader(state: ConfigState, reloader: Arc<dyn ConfigReloader>) -> Self {
        Self::with_reloader_and_audit_metadata(
            state,
            reloader,
            NetworkProxyAuditMetadata::default(),
        )
    }

    pub fn with_reloader_and_blocked_observer(
        state: ConfigState,
        reloader: Arc<dyn ConfigReloader>,
        blocked_request_observer: Option<Arc<dyn BlockedRequestObserver>>,
    ) -> Self {
        Self::with_reloader_and_audit_metadata_and_blocked_observer(
            state,
            reloader,
            NetworkProxyAuditMetadata::default(),
            blocked_request_observer,
        )
    }

    pub fn with_reloader_and_audit_metadata(
        state: ConfigState,
        reloader: Arc<dyn ConfigReloader>,
        audit_metadata: NetworkProxyAuditMetadata,
    ) -> Self {
        Self::with_reloader_and_audit_metadata_and_blocked_observer(
            state,
            reloader,
            audit_metadata,
            /*blocked_request_observer*/ None,
        )
    }

    pub fn with_reloader_and_audit_metadata_and_blocked_observer(
        state: ConfigState,
        reloader: Arc<dyn ConfigReloader>,
        audit_metadata: NetworkProxyAuditMetadata,
        blocked_request_observer: Option<Arc<dyn BlockedRequestObserver>>,
    ) -> Self {
        Self {
            state: Arc::new(RwLock::new(state)),
            reloader,
            blocked_request_observer: Arc::new(RwLock::new(blocked_request_observer)),
            policy_audit_observer: None,
            audit_metadata,
            execution_attributions: Arc::new(Mutex::new(HashMap::new())),
            environment_id: None,
            execution_id: None,
        }
    }

    pub(crate) fn register_execution(
        &self,
        attribution_token: &str,
        environment_id: &str,
        execution_id: &str,
    ) {
        self.execution_attributions
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(
                attribution_token.to_string(),
                ExecutionAttribution {
                    environment_id: environment_id.to_string(),
                    execution_id: execution_id.to_string(),
                },
            );
    }

    pub(crate) fn unregister_execution(&self, attribution_token: &str) {
        self.execution_attributions
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(attribution_token);
    }

    pub(crate) fn for_execution_token(&self, token: &str) -> Option<Self> {
        let attribution = self
            .execution_attributions
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(token)?
            .clone();
        Some(Self {
            environment_id: Some(attribution.environment_id.into()),
            execution_id: Some(attribution.execution_id.into()),
            ..self.clone()
        })
    }

    pub(crate) fn environment_id(&self) -> Option<&str> {
        self.environment_id.as_deref()
    }

    pub(crate) fn execution_id(&self) -> Option<String> {
        self.execution_id.as_deref().map(str::to_string)
    }

    pub async fn set_blocked_request_observer(
        &self,
        blocked_request_observer: Option<Arc<dyn BlockedRequestObserver>>,
    ) {
        let mut observer = self.blocked_request_observer.write().await;
        *observer = blocked_request_observer;
    }

    /// 安装一个尽力而为的观察器，接收每个最终的 domain / non-domain 策略决策。
    pub fn set_policy_audit_observer(&mut self, observer: NetworkPolicyAuditObserver) {
        self.policy_audit_observer = Some(observer);
    }

    pub fn audit_metadata(&self) -> &NetworkProxyAuditMetadata {
        &self.audit_metadata
    }

    pub async fn current_cfg(&self) -> Result<NetworkProxyConfig> {
        // 调用方把 `NetworkProxyState` 当作策略的实时视图。按需重载让配置编辑
        // 无需重启即可生效。
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.clone())
    }

    pub async fn current_patterns(&self) -> Result<(Vec<String>, Vec<String>)> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok((
            guard.config.allowed_domains().unwrap_or_default(),
            guard.config.denied_domains().unwrap_or_default(),
        ))
    }

    pub async fn enabled(&self) -> Result<bool> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.enabled)
    }

    pub async fn force_reload(&self) -> Result<()> {
        let previous_cfg = {
            let guard = self.state.read().await;
            guard.config.clone()
        };

        match self.reloader.reload_now().await {
            Ok(mut new_state) => {
                // 策略变更对运维敏感；记录 diff 让变更可追溯，而无需 dump 完整配置。
                log_policy_changes(&previous_cfg, &new_state.config);
                {
                    let mut guard = self.state.write().await;
                    new_state.blocked = guard.blocked.clone();
                    *guard = new_state;
                }
                let source = self.reloader.source_label();
                info!("reloaded config from {source}");
                Ok(())
            }
            Err(err) => {
                let source = self.reloader.source_label();
                warn!("failed to reload config from {source}: {err}; keeping previous config");
                Err(err)
            }
        }
    }

    pub async fn replace_config_state(&self, mut new_state: ConfigState) -> Result<()> {
        self.reload_if_needed().await?;
        let mut guard = self.state.write().await;
        log_policy_changes(&guard.config, &new_state.config);
        new_state.blocked = guard.blocked.clone();
        new_state.blocked_total = guard.blocked_total;
        *guard = new_state;
        info!("updated network proxy config state");
        Ok(())
    }

    pub async fn host_blocked(&self, host: &str, port: u16) -> Result<HostBlockDecision> {
        self.reload_if_needed().await?;
        let host = match Host::parse(host) {
            Ok(host) => host,
            Err(_) => return Ok(HostBlockDecision::Blocked(HostBlockReason::NotAllowed)),
        };
        let (deny_set, allow_set, allow_local_binding, allowed_domains) = {
            let guard = self.state.read().await;
            let allowed_domains = guard.config.allowed_domains();
            (
                guard.deny_set.clone(),
                guard.allow_set.clone(),
                guard.config.allow_local_binding,
                allowed_domains,
            )
        };
        let allowed_domains_empty = allowed_domains.is_none();
        let allowed_domains = allowed_domains.unwrap_or_default();

        let host_str = host.as_str();

        // 决策顺序敏感：
        //  1) 显式 deny 永远胜出
        //  2) 本地/私网访问默认关闭（纵深防御）
        //  3) 配置了 allowlist 时按 allowlist 执行
        if globset_matches_host_or_unscoped(&deny_set, host_str) {
            return Ok(HostBlockDecision::Blocked(HostBlockReason::Denied));
        }

        let is_allowlisted = globset_matches_host_or_unscoped(&allow_set, host_str);
        if !allow_local_binding {
            // 若意图是"禁止访问本地/内网"，就不能只依赖 `localhost` / `127.0.0.1`
            // 这类字符串检查。攻击者可用 DNS rebinding 或把主机名映射到私网 IP 的
            // 公共后缀服务绕过。
            //
            // 因此在放行前做尽力而为的 DNS + IP 分类检查：显式本地/loopback 字面量
            // 仅在被显式 allowlist 时放行；解析到本地/私网 IP 的主机名即使被
            // allowlist 也照样阻断。
            let local_literal = {
                let host_no_scope = unscoped_ip_literal(host_str).unwrap_or(host_str);
                if is_loopback_host(&host) {
                    true
                } else if let Ok(ip) = host_no_scope.parse::<IpAddr>() {
                    is_non_public_ip(ip)
                } else {
                    false
                }
            };

            if local_literal {
                if !is_explicit_local_allowlisted(&allowed_domains, &host) {
                    return Ok(HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal));
                }
            } else if host_resolves_to_non_public_ip(
                host_str,
                port,
                DNS_LOOKUP_TIMEOUT,
                |host, port| async move {
                    lookup_host((host.as_str(), port))
                        .await
                        .map(Iterator::collect)
                },
            )
            .await
            {
                return Ok(HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal));
            }
        }

        if allowed_domains_empty || !is_allowlisted {
            Ok(HostBlockDecision::Blocked(HostBlockReason::NotAllowed))
        } else {
            Ok(HostBlockDecision::Allowed)
        }
    }

    pub async fn record_blocked(&self, entry: crate::BlockedRequest) -> Result<()> {
        self.reload_if_needed().await?;
        let blocked_for_observer = entry.clone();
        let blocked_request_observer = self.blocked_request_observer.read().await.clone();
        let violation_line = blocked_request_violation_log_line(&entry);
        let host = entry.host.clone();
        let reason = entry.reason.clone();
        let decision = entry.decision.clone();
        let source = entry.source.clone();
        let protocol = entry.protocol.clone();
        let port = entry.port;
        let (total, buffered) = {
            let mut guard = self.state.write().await;
            guard.blocked.push_back(entry);
            guard.blocked_total = guard.blocked_total.saturating_add(1);
            let total = guard.blocked_total;
            while guard.blocked.len() > MAX_BLOCKED_EVENTS {
                guard.blocked.pop_front();
            }
            (total, guard.blocked.len())
        };
        debug!(
            "recorded blocked request telemetry (\
             total={total}, host={host}, reason={reason}, \
             decision={decision:?}, source={source:?}, \
             protocol={protocol}, port={port:?}, buffered={buffered})"
        );
        debug!("{violation_line}");

        if let Some(observer) = blocked_request_observer {
            observer.on_blocked_request(blocked_for_observer).await;
        }
        Ok(())
    }

    /// 返回已缓冲的 blocked 条目快照，不消费它们。
    pub async fn blocked_snapshot(&self) -> Result<Vec<crate::BlockedRequest>> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.blocked.iter().cloned().collect())
    }

    /// 以 FIFO 顺序取出并清空已缓冲的 blocked 条目。
    pub async fn drain_blocked(&self) -> Result<Vec<crate::BlockedRequest>> {
        self.reload_if_needed().await?;
        let blocked = {
            let mut guard = self.state.write().await;
            std::mem::take(&mut guard.blocked)
        };
        Ok(blocked.into_iter().collect())
    }

    pub async fn is_unix_socket_allowed(&self, path: &str) -> Result<bool> {
        self.reload_if_needed().await?;
        if !unix_socket_permissions_supported() {
            return Ok(false);
        }

        // 只支持绝对路径的 unix socket（相对路径相对代理进程的 CWD 有歧义，
        // 会导致 allowlist 行为混乱）。
        let requested_path = Path::new(path);
        if !requested_path.is_absolute() {
            return Ok(false);
        }

        let guard = self.state.read().await;
        if guard.config.dangerously_allow_all_unix_sockets {
            return Ok(true);
        }

        // 归一化路径，同时保持绝对路径要求显式可见。
        let requested_abs = match AbsolutePathBuf::from_absolute_path(requested_path) {
            Ok(path) => path,
            Err(_) => return Ok(false),
        };
        let requested_canonical = std::fs::canonicalize(requested_abs.as_path()).ok();
        for allowed in &guard.config.allow_unix_sockets() {
            let allowed_path = match ValidatedUnixSocketPath::parse(allowed) {
                Ok(ValidatedUnixSocketPath::Native(path)) => path,
                Ok(ValidatedUnixSocketPath::UnixStyleAbsolute(_)) => continue,
                Err(err) => {
                    warn!("ignoring invalid network.allow_unix_sockets entry at runtime: {err:#}");
                    continue;
                }
            };

            if allowed_path.as_path() == requested_abs.as_path() {
                return Ok(true);
            }

            // 尽力而为的 canonicalize，减少符号链接带来的意外。
            // canonicalize 失败（如 socket 尚未创建）时回退到原始比较。
            let Some(requested_canonical) = &requested_canonical else {
                continue;
            };
            if let Ok(allowed_canonical) = std::fs::canonicalize(allowed_path.as_path())
                && &allowed_canonical == requested_canonical
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub async fn method_allowed(&self, method: &str) -> Result<bool> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.mode.allows_method(method))
    }

    pub async fn allow_upstream_proxy(&self) -> Result<bool> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.allow_upstream_proxy)
    }

    pub async fn allow_local_binding(&self) -> Result<bool> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.allow_local_binding)
    }

    pub async fn network_mode(&self) -> Result<NetworkMode> {
        self.reload_if_needed().await?;
        let guard = self.state.read().await;
        Ok(guard.config.mode)
    }

    pub async fn set_network_mode(&self, mode: NetworkMode) -> Result<()> {
        loop {
            self.reload_if_needed().await?;
            let (candidate, constraints) = {
                let guard = self.state.read().await;
                let mut candidate = guard.config.clone();
                candidate.mode = mode;
                (candidate, guard.constraints.clone())
            };

            validate_policy_against_constraints(&candidate, &constraints)
                .map_err(NetworkProxyConstraintError::into_anyhow)
                .context("network.mode constrained by managed config")?;

            let mut guard = self.state.write().await;
            if guard.constraints != constraints {
                drop(guard);
                continue;
            }
            guard.config.mode = mode;
            info!("updated network mode to {mode:?}");
            return Ok(());
        }
    }

    pub async fn add_allowed_domain(&self, host: &str) -> Result<()> {
        self.update_domain_list(host, DomainListKind::Allow).await
    }

    pub async fn add_denied_domain(&self, host: &str) -> Result<()> {
        self.update_domain_list(host, DomainListKind::Deny).await
    }

    async fn update_domain_list(&self, host: &str, target: DomainListKind) -> Result<()> {
        let host = Host::parse(host).context("invalid network host")?;
        let normalized_host = host.as_str().to_string();
        let list_name = target.list_name();
        let constraint_field = target.constraint_field();

        loop {
            self.reload_if_needed().await?;
            let (previous_cfg, constraints, blocked, blocked_total) = {
                let guard = self.state.read().await;
                (
                    guard.config.clone(),
                    guard.constraints.clone(),
                    guard.blocked.clone(),
                    guard.blocked_total,
                )
            };

            let mut candidate = previous_cfg.clone();
            let target_entries = target.entries(&candidate);
            let opposite_entries = target.opposite_entries(&candidate);
            let target_contains = target_entries
                .iter()
                .any(|entry| normalize_host(entry) == normalized_host);
            let opposite_contains = opposite_entries
                .iter()
                .any(|entry| normalize_host(entry) == normalized_host);
            if target_contains && !opposite_contains {
                return Ok(());
            }

            candidate.upsert_domain_permission(
                normalized_host.clone(),
                target.permission(),
                normalize_host,
            );

            validate_policy_against_constraints(&candidate, &constraints)
                .map_err(NetworkProxyConstraintError::into_anyhow)
                .with_context(|| format!("{constraint_field} constrained by managed config"))?;

            let mut new_state = build_config_state(candidate.clone(), constraints.clone())
                .with_context(|| format!("failed to compile updated network {list_name}"))?;
            new_state.blocked = blocked;
            new_state.blocked_total = blocked_total;

            let mut guard = self.state.write().await;
            if guard.constraints != constraints || guard.config != previous_cfg {
                drop(guard);
                continue;
            }

            log_policy_changes(&guard.config, &candidate);
            *guard = new_state;
            info!("updated network {list_name} with {normalized_host}");
            return Ok(());
        }
    }

    async fn reload_if_needed(&self) -> Result<()> {
        match self.reloader.maybe_reload().await? {
            None => Ok(()),
            Some(mut new_state) => {
                let (previous_cfg, blocked, blocked_total) = {
                    let guard = self.state.read().await;
                    (
                        guard.config.clone(),
                        guard.blocked.clone(),
                        guard.blocked_total,
                    )
                };
                log_policy_changes(&previous_cfg, &new_state.config);
                new_state.blocked = blocked;
                new_state.blocked_total = blocked_total;
                {
                    let mut guard = self.state.write().await;
                    *guard = new_state;
                }
                let source = self.reloader.source_label();
                info!("reloaded config from {source}");
                Ok(())
            }
        }
    }
}

#[derive(Clone, Copy)]
enum DomainListKind {
    Allow,
    Deny,
}

impl DomainListKind {
    fn list_name(self) -> &'static str {
        match self {
            Self::Allow => "allowlist",
            Self::Deny => "denylist",
        }
    }

    fn constraint_field(self) -> &'static str {
        match self {
            Self::Allow => "network.allowed_domains",
            Self::Deny => "network.denied_domains",
        }
    }

    fn permission(self) -> NetworkDomainPermission {
        match self {
            Self::Allow => NetworkDomainPermission::Allow,
            Self::Deny => NetworkDomainPermission::Deny,
        }
    }

    fn entries(self, network: &NetworkProxyConfig) -> Vec<String> {
        match self {
            Self::Allow => network.allowed_domains().unwrap_or_default(),
            Self::Deny => network.denied_domains().unwrap_or_default(),
        }
    }

    fn opposite_entries(self, network: &NetworkProxyConfig) -> Vec<String> {
        match self {
            Self::Allow => network.denied_domains().unwrap_or_default(),
            Self::Deny => network.allowed_domains().unwrap_or_default(),
        }
    }
}

pub(crate) fn unix_socket_permissions_supported() -> bool {
    cfg!(target_os = "macos")
}

async fn host_resolves_to_non_public_ip<F, Fut>(
    host: &str,
    port: u16,
    lookup_timeout: Duration,
    lookup: F,
) -> bool
where
    F: FnOnce(String, u16) -> Fut,
    Fut: Future<Output = std::io::Result<Vec<SocketAddr>>>,
{
    if let Ok(ip) = host.parse::<IpAddr>() {
        return is_non_public_ip(ip);
    }

    // DNS 查询失败即阻断：连接时还会再次解析，这里的失败并不能证明目标是公网地址。
    let addrs = match timeout(lookup_timeout, lookup(host.to_string(), port)).await {
        Ok(Ok(addrs)) => addrs,
        Ok(Err(err)) => {
            debug!(
                "blocking host because DNS lookup failed during local/private IP check (host={host}, port={port}): {err}"
            );
            return true;
        }
        Err(_) => {
            debug!(
                "blocking host because DNS lookup timed out during local/private IP check (host={host}, port={port})"
            );
            return true;
        }
    };

    for addr in addrs {
        if is_non_public_ip(addr.ip()) {
            return true;
        }
    }

    false
}

fn log_policy_changes(previous: &NetworkProxyConfig, next: &NetworkProxyConfig) {
    let previous_allowed_domains = previous.allowed_domains().unwrap_or_default();
    let next_allowed_domains = next.allowed_domains().unwrap_or_default();
    log_domain_list_changes(
        "allowlist",
        &previous_allowed_domains,
        &next_allowed_domains,
    );
    let previous_denied_domains = previous.denied_domains().unwrap_or_default();
    let next_denied_domains = next.denied_domains().unwrap_or_default();
    log_domain_list_changes("denylist", &previous_denied_domains, &next_denied_domains);
}

fn log_domain_list_changes(list_name: &str, previous: &[String], next: &[String]) {
    let previous_set: HashSet<String> = previous
        .iter()
        .map(|entry| entry.to_ascii_lowercase())
        .collect();
    let next_set: HashSet<String> = next
        .iter()
        .map(|entry| entry.to_ascii_lowercase())
        .collect();

    let added = next_set
        .difference(&previous_set)
        .cloned()
        .collect::<HashSet<_>>();
    let removed = previous_set
        .difference(&next_set)
        .cloned()
        .collect::<HashSet<_>>();

    let mut seen_next = HashSet::new();
    for entry in next {
        let key = entry.to_ascii_lowercase();
        if seen_next.insert(key.clone()) && added.contains(&key) {
            info!("config entry added to {list_name}: {entry}");
        }
    }

    let mut seen_previous = HashSet::new();
    for entry in previous {
        let key = entry.to_ascii_lowercase();
        if seen_previous.insert(key.clone()) && removed.contains(&key) {
            info!("config entry removed from {list_name}: {entry}");
        }
    }
}

fn globset_matches_host_or_unscoped(set: &GlobSet, host: &str) -> bool {
    set.is_match(host) || unscoped_ip_literal(host).is_some_and(|ip| set.is_match(ip))
}

fn is_explicit_local_allowlisted(allowed_domains: &[String], host: &Host) -> bool {
    let normalized_host = host.as_str();
    let unscoped_host = unscoped_ip_literal(normalized_host);
    allowed_domains.iter().any(|pattern| {
        let pattern = pattern.trim();
        if pattern == "*" || pattern.starts_with("*.") || pattern.starts_with("**.") {
            return false;
        }
        if pattern.contains('*') || pattern.contains('?') {
            return false;
        }
        let normalized_pattern = normalize_host(pattern);
        normalized_pattern == normalized_host
            || unscoped_host.is_some_and(|ip| normalized_pattern == ip)
    })
}

fn unix_timestamp() -> i64 {
    OffsetDateTime::now_utc().unix_timestamp()
}

#[cfg(test)]
pub(crate) fn network_proxy_state_for_policy(
    mut config: NetworkProxyConfig,
) -> NetworkProxyState {
    // 测试 helper 语义与 codex 一致：强制 enabled，让策略路径可被直接驱动。
    config.enabled = true;
    let state = ConfigState {
        allow_set: crate::policy::compile_allowlist_globset(
            &config.allowed_domains().unwrap_or_default(),
        )
        .unwrap(),
        blocked: VecDeque::new(),
        blocked_total: 0,
        constraints: NetworkProxyConstraints::default(),
        deny_set: crate::policy::compile_denylist_globset(
            &config.denied_domains().unwrap_or_default(),
        )
        .unwrap(),
        config,
    };

    NetworkProxyState::with_reloader(state, Arc::new(NoopReloader))
}

#[cfg(test)]
struct NoopReloader;

#[cfg(test)]
impl ConfigReloader for NoopReloader {
    fn source_label(&self) -> String {
        "test config state".to_string()
    }

    fn maybe_reload(&self) -> ConfigReloaderFuture<'_, Option<ConfigState>> {
        Box::pin(async { Ok(None) })
    }

    fn reload_now(&self) -> ConfigReloaderFuture<'_, ConfigState> {
        Box::pin(async { Err(anyhow::anyhow!("force reload is not supported in tests")) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::BlockedRequest;
    use crate::BlockedRequestArgs;
    use crate::policy::compile_allowlist_globset;
    use crate::policy::compile_denylist_globset;
    use crate::state::NetworkProxyConstraints;
    use crate::state::build_config_state;
    use crate::state::validate_policy_against_constraints;
    use pretty_assertions::assert_eq;

    // 裁点：codex 端 credential broker 的 reload/virtualize 测试未移植（凭证经纪已裁）；
    // 其专用的 `StaticReloader` 夹具随之移除。

    fn strings(entries: &[&str]) -> Vec<String> {
        entries.iter().map(|entry| (*entry).to_string()).collect()
    }

    fn network_settings(allowed_domains: &[&str], denied_domains: &[&str]) -> NetworkProxyConfig {
        let mut network = NetworkProxyConfig::default();
        if !allowed_domains.is_empty() {
            network.set_allowed_domains(strings(allowed_domains));
        }
        if !denied_domains.is_empty() {
            network.set_denied_domains(strings(denied_domains));
        }
        network
    }

    fn network_settings_with_unix_sockets(
        allowed_domains: &[&str],
        denied_domains: &[&str],
        unix_sockets: &[String],
    ) -> NetworkProxyConfig {
        let mut network = network_settings(allowed_domains, denied_domains);
        if !unix_sockets.is_empty() {
            network.set_allow_unix_sockets(unix_sockets.to_vec());
        }
        network
    }

    #[tokio::test]
    async fn host_blocked_denied_wins_over_allowed() {
        let state =
            network_proxy_state_for_policy(network_settings(&["example.com"], &["example.com"]));

        assert_eq!(
            state
                .host_blocked("example.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::Denied)
        );
    }

    #[tokio::test]
    async fn host_blocked_requires_allowlist_match() {
        let state = network_proxy_state_for_policy(network_settings(&["example.com"], &[]));

        assert_eq!(
            state
                .host_blocked("example.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
        assert_eq!(
            // 用公网 IP 字面量，避免依赖环境 DNS 行为（有些网络会把未知主机名
            // 解析到私网 IP，触发 `not_allowed_local`）。
            state.host_blocked("8.8.8.8", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowed)
        );
    }

    #[tokio::test]
    async fn add_allowed_domain_removes_matching_deny_entry() {
        let state = network_proxy_state_for_policy(network_settings(&[], &["example.com"]));

        state.add_allowed_domain("ExAmPlE.CoM").await.unwrap();

        let (allowed, denied) = state.current_patterns().await.unwrap();
        assert_eq!(allowed, vec!["example.com".to_string()]);
        assert!(denied.is_empty());
        assert_eq!(
            state
                .host_blocked("example.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
    }

    #[tokio::test]
    async fn add_denied_domain_removes_matching_allow_entry() {
        let state = network_proxy_state_for_policy(network_settings(&["example.com"], &[]));

        state.add_denied_domain("EXAMPLE.COM").await.unwrap();

        let (allowed, denied) = state.current_patterns().await.unwrap();
        assert!(allowed.is_empty());
        assert_eq!(denied, vec!["example.com".to_string()]);
        assert_eq!(
            state
                .host_blocked("example.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::Denied)
        );
    }

    #[tokio::test]
    async fn add_denied_domain_forces_block_with_global_wildcard_allowlist() {
        let state = network_proxy_state_for_policy(network_settings(&["*"], &[]));

        assert_eq!(
            // 用公网 IP 字面量，避免依赖环境 DNS 行为。
            state.host_blocked("8.8.8.8", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Allowed
        );

        state.add_denied_domain("8.8.8.8").await.unwrap();

        let (allowed, denied) = state.current_patterns().await.unwrap();
        assert_eq!(allowed, vec!["*".to_string()]);
        assert_eq!(denied, vec!["8.8.8.8".to_string()]);
        assert_eq!(
            state.host_blocked("8.8.8.8", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::Denied)
        );
    }

    #[tokio::test]
    async fn add_allowed_domain_succeeds_when_managed_baseline_allows_expansion() {
        let mut config = network_settings(&["managed.example.com"], &[]);
        config.enabled = true;
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["managed.example.com".to_string()]),
            allowlist_expansion_enabled: Some(true),
            ..NetworkProxyConstraints::default()
        };
        let state = NetworkProxyState::with_reloader(
            build_config_state(config, constraints).unwrap(),
            Arc::new(NoopReloader),
        );

        state.add_allowed_domain("user.example.com").await.unwrap();

        let (allowed, denied) = state.current_patterns().await.unwrap();
        assert_eq!(
            allowed,
            vec![
                "managed.example.com".to_string(),
                "user.example.com".to_string()
            ]
        );
        assert!(denied.is_empty());
    }

    #[tokio::test]
    async fn add_allowed_domain_rejects_expansion_when_managed_baseline_is_fixed() {
        let mut config = network_settings(&["managed.example.com"], &[]);
        config.enabled = true;
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["managed.example.com".to_string()]),
            allowlist_expansion_enabled: Some(false),
            ..NetworkProxyConstraints::default()
        };
        let state = NetworkProxyState::with_reloader(
            build_config_state(config, constraints).unwrap(),
            Arc::new(NoopReloader),
        );

        let err = state
            .add_allowed_domain("user.example.com")
            .await
            .expect_err("managed baseline should reject allowlist expansion");

        assert!(
            format!("{err:#}").contains("network.allowed_domains constrained by managed config"),
            "unexpected error: {err:#}"
        );
    }

    #[tokio::test]
    async fn add_denied_domain_rejects_expansion_when_managed_baseline_is_fixed() {
        let mut config = network_settings(&[], &["managed.example.com"]);
        config.enabled = true;
        let constraints = NetworkProxyConstraints {
            denied_domains: Some(vec!["managed.example.com".to_string()]),
            denylist_expansion_enabled: Some(false),
            ..NetworkProxyConstraints::default()
        };
        let state = NetworkProxyState::with_reloader(
            build_config_state(config, constraints).unwrap(),
            Arc::new(NoopReloader),
        );

        let err = state
            .add_denied_domain("user.example.com")
            .await
            .expect_err("managed baseline should reject denylist expansion");

        assert!(
            format!("{err:#}").contains("network.denied_domains constrained by managed config"),
            "unexpected error: {err:#}"
        );
    }

    #[tokio::test]
    async fn blocked_snapshot_does_not_consume_entries() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig::default());

        state
            .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                host: "google.com".to_string(),
                reason: "not_allowed".to_string(),
                client: None,
                method: Some("GET".to_string()),
                mode: None,
                protocol: "http".to_string(),
                decision: Some("ask".to_string()),
                source: Some("decider".to_string()),
                port: Some(80),
            }))
            .await
            .expect("entry should be recorded");

        let snapshot = state
            .blocked_snapshot()
            .await
            .expect("snapshot should succeed");
        assert_eq!(snapshot.len(), 1);
        assert_eq!(snapshot[0].host, "google.com");
        assert_eq!(snapshot[0].decision.as_deref(), Some("ask"));

        let drained = state
            .drain_blocked()
            .await
            .expect("drain should include snapshot entry");
        assert_eq!(drained.len(), 1);
        assert_eq!(drained[0].host, snapshot[0].host);
        assert_eq!(drained[0].reason, snapshot[0].reason);
        assert_eq!(drained[0].decision, snapshot[0].decision);
        assert_eq!(drained[0].source, snapshot[0].source);
        assert_eq!(drained[0].port, snapshot[0].port);
    }

    #[tokio::test]
    async fn drain_blocked_returns_buffered_window() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig::default());

        for idx in 0..(MAX_BLOCKED_EVENTS + 5) {
            state
                .record_blocked(BlockedRequest::new(BlockedRequestArgs {
                    host: format!("example{idx}.com"),
                    reason: "not_allowed".to_string(),
                    client: None,
                    method: Some("GET".to_string()),
                    mode: None,
                    protocol: "http".to_string(),
                    decision: Some("ask".to_string()),
                    source: Some("decider".to_string()),
                    port: Some(80),
                }))
                .await
                .expect("entry should be recorded");
        }

        let blocked = state.drain_blocked().await.expect("drain should succeed");
        assert_eq!(blocked.len(), MAX_BLOCKED_EVENTS);
        assert_eq!(blocked[0].host, "example5.com");
    }

    #[test]
    fn blocked_request_violation_log_line_serializes_payload() {
        let entry = BlockedRequest {
            host: "google.com".to_string(),
            reason: "not_allowed".to_string(),
            client: Some("127.0.0.1".to_string()),
            method: Some("GET".to_string()),
            mode: Some(NetworkMode::Proxy),
            protocol: "http".to_string(),
            decision: Some("ask".to_string()),
            source: Some("decider".to_string()),
            port: Some(80),
            timestamp: 1_735_689_600,
        };

        assert_eq!(
            blocked_request_violation_log_line(&entry),
            r#"CODEX_NETWORK_POLICY_VIOLATION {"host":"google.com","reason":"not_allowed","client":"127.0.0.1","method":"GET","mode":"proxy","protocol":"http","decision":"ask","source":"decider","port":80,"timestamp":1735689600}"#
        );
    }

    #[tokio::test]
    async fn host_blocked_subdomain_wildcards_exclude_apex() {
        let state = network_proxy_state_for_policy(network_settings(&["*.openai.com"], &[]));

        assert_eq!(
            state
                .host_blocked("api.openai.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
        assert_eq!(
            state.host_blocked("openai.com", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowed)
        );
    }

    #[tokio::test]
    async fn host_blocked_global_wildcard_allowlist_allows_public_hosts_except_denylist() {
        let state = network_proxy_state_for_policy(network_settings(&["*"], &["evil.example"]));

        assert_eq!(
            state
                .host_blocked("example.com", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
        assert_eq!(
            state
                .host_blocked("api.openai.com", /*port*/ 443)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
        assert_eq!(
            state
                .host_blocked("evil.example", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::Denied)
        );
    }

    #[tokio::test]
    async fn host_blocked_rejects_loopback_when_local_binding_disabled() {
        let state = network_proxy_state_for_policy(network_settings(&["example.com"], &[]));

        assert_eq!(
            state.host_blocked("127.0.0.1", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
        assert_eq!(
            state.host_blocked("localhost", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
    }

    #[tokio::test]
    async fn host_blocked_allows_loopback_when_explicitly_allowlisted_and_local_binding_disabled() {
        let state = network_proxy_state_for_policy(network_settings(&["localhost"], &[]));

        assert_eq!(
            state.host_blocked("localhost", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Allowed
        );
    }

    #[tokio::test]
    async fn host_blocked_allows_private_ip_literal_when_explicitly_allowlisted() {
        let state = network_proxy_state_for_policy(network_settings(&["10.0.0.1"], &[]));

        assert_eq!(
            state.host_blocked("10.0.0.1", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Allowed
        );
    }

    #[tokio::test]
    async fn host_blocked_rejects_scoped_ipv6_literal_when_not_allowlisted() {
        let state = network_proxy_state_for_policy(network_settings(&["example.com"], &[]));

        assert_eq!(
            state
                .host_blocked("fe80::1%lo0", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
    }

    #[tokio::test]
    async fn host_blocked_allows_scoped_ipv6_literal_when_explicitly_allowlisted() {
        let state = network_proxy_state_for_policy(network_settings(&["fe80::1"], &[]));

        assert_eq!(
            state
                .host_blocked("fe80::1%lo0", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
    }

    #[tokio::test]
    async fn host_blocked_requires_exact_scoped_ipv6_allowlist_match() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig {
            allow_local_binding: true,
            ..network_settings(&["fe80::1%eth0"], &[])
        });

        assert_eq!(
            state
                .host_blocked("fe80::1%eth0", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
        assert_eq!(
            state
                .host_blocked("fe80::1%eth1", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowed)
        );
    }

    #[tokio::test]
    async fn host_blocked_denies_scoped_ipv6_literal_before_local_binding() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig {
            allow_local_binding: true,
            ..network_settings(&["*"], &["fd00::1"])
        });

        for host in ["fd00::1%eth0", "[fd00::1%eth0]", "[fd00::1%25eth0]"] {
            assert_eq!(
                state.host_blocked(host, /*port*/ 80).await.unwrap(),
                HostBlockDecision::Blocked(HostBlockReason::Denied),
                "host should be denied after normalization: {host}"
            );
        }
    }

    #[tokio::test]
    async fn host_blocked_requires_exact_scoped_ipv6_denylist_match() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig {
            allow_local_binding: true,
            ..network_settings(&["*"], &["fd00::1%eth0"])
        });

        assert_eq!(
            state
                .host_blocked("fd00::1%eth0", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::Denied)
        );
        assert_eq!(
            state
                .host_blocked("fd00::1%eth1", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Allowed
        );
    }

    #[tokio::test]
    async fn host_blocked_rejects_private_ip_literals_when_local_binding_disabled() {
        let state = network_proxy_state_for_policy(network_settings(&["example.com"], &[]));

        assert_eq!(
            state.host_blocked("10.0.0.1", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
    }

    #[tokio::test]
    async fn host_blocked_rejects_loopback_when_allowlist_empty() {
        let state = network_proxy_state_for_policy(NetworkProxyConfig::default());

        assert_eq!(
            state.host_blocked("127.0.0.1", /*port*/ 80).await.unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
    }

    #[tokio::test]
    async fn host_blocked_rejects_allowlisted_hostname_when_dns_lookup_fails() {
        let mut network = NetworkProxyConfig::default();
        network.set_allowed_domains(vec!["does-not-resolve.invalid".to_string()]);
        let state = network_proxy_state_for_policy(network);

        assert_eq!(
            state
                .host_blocked("does-not-resolve.invalid", /*port*/ 80)
                .await
                .unwrap(),
            HostBlockDecision::Blocked(HostBlockReason::NotAllowedLocal)
        );
    }

    #[tokio::test]
    async fn host_resolves_to_non_public_ip_blocks_on_dns_lookup_timeout() {
        let blocked = host_resolves_to_non_public_ip(
            "slow.example",
            /*port*/ 80,
            Duration::from_millis(1),
            |_host, _port| async {
                std::future::pending::<std::io::Result<Vec<SocketAddr>>>().await
            },
        )
        .await;

        assert!(blocked);
    }

    #[tokio::test]
    async fn host_resolves_to_non_public_ip_blocks_on_dns_lookup_error() {
        let blocked = host_resolves_to_non_public_ip(
            "error.example",
            /*port*/ 80,
            Duration::from_millis(10),
            |_host, _port| async {
                Err::<Vec<SocketAddr>, std::io::Error>(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "forced failure",
                ))
            },
        )
        .await;

        assert!(blocked);
    }

    #[tokio::test]
    async fn host_resolves_to_non_public_ip_blocks_private_resolution() {
        let blocked = host_resolves_to_non_public_ip(
            "local.example",
            /*port*/ 80,
            Duration::from_millis(10),
            |_host, _port| async { Ok(vec!["127.0.0.1:80".parse().unwrap()]) },
        )
        .await;

        assert!(blocked);
    }

    #[tokio::test]
    async fn host_resolves_to_non_public_ip_allows_public_resolution() {
        let blocked = host_resolves_to_non_public_ip(
            "public.example",
            /*port*/ 80,
            Duration::from_millis(10),
            |_host, _port| async { Ok(vec!["8.8.8.8:80".parse().unwrap()]) },
        )
        .await;

        assert!(!blocked);
    }

    #[test]
    fn validate_policy_against_constraints_disallows_widening_allowed_domains() {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["example.com".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["example.com", "evil.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_allows_expanding_allowed_domains_when_enabled() {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["example.com".to_string()]),
            allowlist_expansion_enabled: Some(true),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["example.com", "api.openai.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_ok());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_widening_mode() {
        // 适配：nova None < Proxy（对位 codex Limited < Full）。
        let constraints = NetworkProxyConstraints {
            mode: Some(NetworkMode::None),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            mode: NetworkMode::Proxy,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_allows_narrowing_wildcard_allowlist() {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["*.example.com".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["api.example.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_ok());
    }

    #[test]
    fn validate_policy_against_constraints_rejects_widening_wildcard_allowlist() {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["*.example.com".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["**.example.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_rejects_global_wildcard_in_managed_allowlist() {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["*".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["api.example.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_rejects_bracketed_global_wildcard_in_managed_allowlist()
    {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["[*]".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["api.example.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_rejects_double_wildcard_bracketed_global_wildcard_in_managed_allowlist()
     {
        let constraints = NetworkProxyConstraints {
            allowed_domains: Some(vec!["**.[*]".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&["api.example.com"], &[]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_requires_managed_denied_domains_entries() {
        let constraints = NetworkProxyConstraints {
            denied_domains: Some(vec!["evil.com".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_expanding_denied_domains_when_fixed() {
        let constraints = NetworkProxyConstraints {
            denied_domains: Some(vec!["evil.com".to_string()]),
            denylist_expansion_enabled: Some(false),
            ..NetworkProxyConstraints::default()
        };

        let mut config = network_settings(&[], &["evil.com", "more-evil.com"]);
        config.enabled = true;

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_enabling_when_managed_disabled() {
        let constraints = NetworkProxyConstraints {
            enabled: Some(false),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_allow_local_binding_when_managed_disabled() {
        let constraints = NetworkProxyConstraints {
            allow_local_binding: Some(false),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            allow_local_binding: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_allow_all_unix_sockets_without_managed_opt_in()
    {
        let constraints = NetworkProxyConstraints {
            dangerously_allow_all_unix_sockets: Some(false),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            dangerously_allow_all_unix_sockets: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_disallows_allow_all_unix_sockets_when_allowlist_is_managed()
     {
        let constraints = NetworkProxyConstraints {
            allow_unix_sockets: Some(vec!["/tmp/allowed.sock".to_string()]),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            dangerously_allow_all_unix_sockets: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_err());
    }

    #[test]
    fn validate_policy_against_constraints_allows_allow_all_unix_sockets_with_managed_opt_in() {
        let constraints = NetworkProxyConstraints {
            dangerously_allow_all_unix_sockets: Some(true),
            ..NetworkProxyConstraints::default()
        };

        let config = NetworkProxyConfig {
            enabled: true,
            dangerously_allow_all_unix_sockets: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_ok());
    }

    #[test]
    fn validate_policy_against_constraints_allows_allow_all_unix_sockets_when_unmanaged() {
        let constraints = NetworkProxyConstraints::default();

        let config = NetworkProxyConfig {
            enabled: true,
            dangerously_allow_all_unix_sockets: true,
            ..NetworkProxyConfig::default()
        };

        assert!(validate_policy_against_constraints(&config, &constraints).is_ok());
    }

    #[test]
    fn compile_globset_is_case_insensitive() {
        let patterns = vec!["ExAmPle.CoM".to_string()];
        let set = compile_denylist_globset(&patterns).unwrap();
        assert!(set.is_match("example.com"));
        assert!(set.is_match("EXAMPLE.COM"));
    }

    #[test]
    fn compile_globset_excludes_apex_for_subdomain_patterns() {
        let patterns = vec!["*.openai.com".to_string()];
        let set = compile_denylist_globset(&patterns).unwrap();
        assert!(set.is_match("api.openai.com"));
        assert!(!set.is_match("openai.com"));
        assert!(!set.is_match("evilopenai.com"));
    }

    #[test]
    fn compile_globset_includes_apex_for_double_wildcard_patterns() {
        let patterns = vec!["**.openai.com".to_string()];
        let set = compile_denylist_globset(&patterns).unwrap();
        assert!(set.is_match("openai.com"));
        assert!(set.is_match("api.openai.com"));
        assert!(!set.is_match("evilopenai.com"));
    }

    #[test]
    fn compile_globset_rejects_global_wildcard() {
        let patterns = vec!["*".to_string()];
        assert!(compile_denylist_globset(&patterns).is_err());
    }

    #[test]
    fn compile_globset_allows_global_wildcard_when_enabled() {
        let patterns = vec!["*".to_string()];
        let set = compile_allowlist_globset(&patterns).unwrap();
        assert!(set.is_match("example.com"));
        assert!(set.is_match("api.openai.com"));
        assert!(set.is_match("localhost"));
    }

    #[test]
    fn compile_globset_rejects_bracketed_global_wildcard() {
        let patterns = vec!["[*]".to_string()];
        assert!(compile_denylist_globset(&patterns).is_err());
    }

    #[test]
    fn compile_globset_rejects_double_wildcard_bracketed_global_wildcard() {
        let patterns = vec!["**.[*]".to_string()];
        assert!(compile_denylist_globset(&patterns).is_err());
    }

    #[test]
    fn compile_globset_dedupes_patterns_without_changing_behavior() {
        let patterns = vec!["example.com".to_string(), "example.com".to_string()];
        let set = compile_denylist_globset(&patterns).unwrap();
        assert!(set.is_match("example.com"));
        assert!(set.is_match("EXAMPLE.COM"));
        assert!(!set.is_match("not-example.com"));
    }

    #[test]
    fn compile_globset_rejects_invalid_patterns() {
        let patterns = vec!["[".to_string()];
        assert!(compile_denylist_globset(&patterns).is_err());
    }

    #[test]
    fn build_config_state_allows_global_wildcard_allowed_domains() {
        let mut config = network_settings(&["*"], &[]);
        config.enabled = true;

        assert!(build_config_state(config, NetworkProxyConstraints::default()).is_ok());
    }

    #[test]
    fn build_config_state_allows_bracketed_global_wildcard_allowed_domains() {
        let mut config = network_settings(&["[*]"], &[]);
        config.enabled = true;

        assert!(build_config_state(config, NetworkProxyConstraints::default()).is_ok());
    }

    #[test]
    fn build_config_state_rejects_global_wildcard_denied_domains() {
        let mut config = network_settings(&["example.com"], &["*"]);
        config.enabled = true;

        assert!(build_config_state(config, NetworkProxyConstraints::default()).is_err());
    }

    #[test]
    fn build_config_state_rejects_bracketed_global_wildcard_denied_domains() {
        let mut config = network_settings(&["example.com"], &["[*]"]);
        config.enabled = true;

        assert!(build_config_state(config, NetworkProxyConstraints::default()).is_err());
    }

    #[test]
    fn build_config_state_rejects_unsupported_mitm_and_broker_features() {
        // 裁点行为锚定：MITM / 凭证经纪 / MITM hooks 第一期未移植，配置非空即报错。
        for config in [
            NetworkProxyConfig {
                mitm: true,
                ..NetworkProxyConfig::default()
            },
            NetworkProxyConfig {
                credential_broker: true,
                ..NetworkProxyConfig::default()
            },
            NetworkProxyConfig {
                dangerously_allow_plaintext_credential_injection: true,
                ..NetworkProxyConfig::default()
            },
            NetworkProxyConfig {
                mitm_hooks: vec!["hook".to_string()],
                ..NetworkProxyConfig::default()
            },
        ] {
            assert!(
                build_config_state(config, NetworkProxyConstraints::default()).is_err(),
                "unsupported feature config should fail fast"
            );
        }
    }

    #[cfg(target_os = "macos")]
    #[tokio::test]
    async fn unix_socket_allowlist_is_respected_on_macos() {
        let socket_path = "/tmp/example.sock".to_string();
        let state = network_proxy_state_for_policy(network_settings_with_unix_sockets(
            &["example.com"],
            &[],
            std::slice::from_ref(&socket_path),
        ));

        assert!(state.is_unix_socket_allowed(&socket_path).await.unwrap());
        assert!(
            !state
                .is_unix_socket_allowed("/tmp/not-allowed.sock")
                .await
                .unwrap()
        );
    }

    #[cfg(target_os = "macos")]
    #[tokio::test]
    async fn unix_socket_allowlist_resolves_symlinks() {
        use std::os::unix::fs::symlink;
        use tempfile::tempdir;

        let temp_dir = tempdir().unwrap();
        let dir = temp_dir.path();

        let real = dir.join("real.sock");
        let link = dir.join("link.sock");

        // allowlist 机制基于路径；测试不需要真实的 unix domain socket，
        // 任何文件系统条目都能用于 canonicalize。
        std::fs::write(&real, b"not a socket").unwrap();
        symlink(&real, &link).unwrap();

        let real_s = real.to_str().unwrap().to_string();
        let link_s = link.to_str().unwrap().to_string();

        let state = network_proxy_state_for_policy(network_settings_with_unix_sockets(
            &["example.com"],
            &[],
            std::slice::from_ref(&real_s),
        ));

        assert!(state.is_unix_socket_allowed(&link_s).await.unwrap());
    }

    #[cfg(target_os = "macos")]
    #[tokio::test]
    async fn unix_socket_allow_all_flag_bypasses_allowlist() {
        let state = network_proxy_state_for_policy({
            let mut network = network_settings(&["example.com"], &[]);
            network.dangerously_allow_all_unix_sockets = true;
            network
        });

        assert!(state.is_unix_socket_allowed("/tmp/any.sock").await.unwrap());
        assert!(!state.is_unix_socket_allowed("relative.sock").await.unwrap());
    }

    #[cfg(not(target_os = "macos"))]
    #[tokio::test]
    async fn unix_socket_allowlist_is_rejected_on_non_macos() {
        let socket_path = "/tmp/example.sock".to_string();
        let state = network_proxy_state_for_policy({
            let mut network = network_settings_with_unix_sockets(
                &["example.com"],
                &[],
                std::slice::from_ref(&socket_path),
            );
            network.dangerously_allow_all_unix_sockets = true;
            network
        });

        assert!(!state.is_unix_socket_allowed(&socket_path).await.unwrap());
    }
}
