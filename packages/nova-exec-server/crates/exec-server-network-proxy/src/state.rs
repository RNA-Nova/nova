//! 配置状态构建与托管约束校验（移植自 codex network-proxy `state.rs`）。
//!
//! 裁点：
//! - MITM 面：`MitmState` / `compile_mitm_hooks` / `validate_mitm_hook_config` 未移植；
//!   `build_config_state` 对 MITM / MITM hooks / 凭证经纪配置 fail fast。
//! - 凭证经纪：broker 创建代理 / 默认白名单的 `brokerage_*` 逻辑未移植。
//! - `PartialNetworkProxyConfig` 裁掉 `mitm` / `credential_broker` /
//!   `dangerously_allow_plaintext_credential_injection` / `mitm_hooks` 字段。

use crate::NetworkDomainPermissions;
use crate::NetworkMode;
use crate::NetworkProxyConfig;
use crate::NetworkUnixSocketPermissions;
use crate::policy::DomainPattern;
use crate::policy::compile_allowlist_globset;
use crate::policy::compile_denylist_globset;
use crate::policy::is_global_wildcard_domain_pattern;
use crate::runtime::ConfigState;
use serde::Deserialize;
use std::collections::HashSet;

pub use crate::runtime::BlockedRequestArgs;
pub use crate::runtime::NetworkProxyState;
#[cfg(test)]
pub(crate) use crate::runtime::network_proxy_state_for_policy;

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct NetworkProxyConstraints {
    pub enabled: Option<bool>,
    pub mode: Option<NetworkMode>,
    pub allow_upstream_proxy: Option<bool>,
    pub dangerously_allow_non_loopback_proxy: Option<bool>,
    pub dangerously_allow_all_unix_sockets: Option<bool>,
    pub allowed_domains: Option<Vec<String>>,
    pub allowlist_expansion_enabled: Option<bool>,
    pub denied_domains: Option<Vec<String>>,
    pub denylist_expansion_enabled: Option<bool>,
    pub allow_unix_sockets: Option<Vec<String>>,
    pub allow_local_binding: Option<bool>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PartialNetworkProxyConfig {
    pub enabled: Option<bool>,
    pub mode: Option<NetworkMode>,
    pub allow_upstream_proxy: Option<bool>,
    pub dangerously_allow_non_loopback_proxy: Option<bool>,
    pub dangerously_allow_all_unix_sockets: Option<bool>,
    #[serde(default)]
    pub domains: Option<NetworkDomainPermissions>,
    #[serde(default)]
    pub unix_sockets: Option<NetworkUnixSocketPermissions>,
    pub allow_local_binding: Option<bool>,
}

pub fn build_config_state(
    config: NetworkProxyConfig,
    constraints: NetworkProxyConstraints,
) -> anyhow::Result<ConfigState> {
    // 裁点 fail fast：MITM / 凭证经纪 / MITM hooks 第一期未移植（见模块文档）。
    // 远程边界由 `RemoteNetworkProxyConfig::from_effective_config` 先行拒绝；这里兜底，
    // 防止本地配置误入尚未实现的拦截面。
    anyhow::ensure!(
        !config.mitm
            && !config.credential_broker
            && !config.dangerously_allow_plaintext_credential_injection
            && config.mitm_hooks.is_empty(),
        "network proxy does not support MITM, credential injection, or MITM hooks yet"
    );
    crate::config::validate_unix_socket_allowlist_paths(&config)?;
    let allowed_domains = config.allowed_domains().unwrap_or_default();
    let denied_domains = config.denied_domains().unwrap_or_default();
    validate_non_global_wildcard_domain_patterns("network.denied_domains", &denied_domains)
        .map_err(NetworkProxyConstraintError::into_anyhow)?;
    let deny_set = compile_denylist_globset(&denied_domains)?;
    let allow_set = compile_allowlist_globset(&allowed_domains)?;
    Ok(ConfigState {
        config,
        allow_set,
        deny_set,
        constraints,
        blocked: std::collections::VecDeque::new(),
        blocked_total: 0,
    })
}

pub fn validate_policy_against_constraints(
    config: &NetworkProxyConfig,
    constraints: &NetworkProxyConstraints,
) -> Result<(), NetworkProxyConstraintError> {
    fn invalid_value(
        field_name: &'static str,
        candidate: impl Into<String>,
        allowed: impl Into<String>,
    ) -> NetworkProxyConstraintError {
        NetworkProxyConstraintError::InvalidValue {
            field_name,
            candidate: candidate.into(),
            allowed: allowed.into(),
        }
    }

    fn validate<T>(
        candidate: T,
        validator: impl FnOnce(&T) -> Result<(), NetworkProxyConstraintError>,
    ) -> Result<(), NetworkProxyConstraintError> {
        validator(&candidate)
    }

    let enabled = config.enabled;
    let config_allowed_domains = config.allowed_domains().unwrap_or_default();
    let config_denied_domains = config.denied_domains().unwrap_or_default();
    let denied_domain_overrides: HashSet<String> = config_denied_domains
        .iter()
        .map(|entry| entry.to_ascii_lowercase())
        .collect();
    let config_allow_unix_sockets = config.allow_unix_sockets();
    validate_non_global_wildcard_domain_patterns("network.denied_domains", &config_denied_domains)?;
    if let Some(max_enabled) = constraints.enabled {
        validate(enabled, move |candidate| {
            if *candidate && !max_enabled {
                Err(invalid_value(
                    "network.enabled",
                    "true",
                    "false (disabled by managed config)",
                ))
            } else {
                Ok(())
            }
        })?;
    }

    if let Some(max_mode) = constraints.mode {
        validate(config.mode, move |candidate| {
            if network_mode_rank(*candidate) > network_mode_rank(max_mode) {
                Err(invalid_value(
                    "network.mode",
                    format!("{candidate:?}"),
                    format!("{max_mode:?} or more restrictive"),
                ))
            } else {
                Ok(())
            }
        })?;
    }

    let allow_upstream_proxy = constraints.allow_upstream_proxy;
    validate(
        config.allow_upstream_proxy,
        move |candidate| match allow_upstream_proxy {
            Some(true) | None => Ok(()),
            Some(false) => {
                if *candidate {
                    Err(invalid_value(
                        "network.allow_upstream_proxy",
                        "true",
                        "false (disabled by managed config)",
                    ))
                } else {
                    Ok(())
                }
            }
        },
    )?;

    let allow_non_loopback_proxy = constraints.dangerously_allow_non_loopback_proxy;
    validate(
        config.dangerously_allow_non_loopback_proxy,
        move |candidate| match allow_non_loopback_proxy {
            Some(true) | None => Ok(()),
            Some(false) => {
                if *candidate {
                    Err(invalid_value(
                        "network.dangerously_allow_non_loopback_proxy",
                        "true",
                        "false (disabled by managed config)",
                    ))
                } else {
                    Ok(())
                }
            }
        },
    )?;

    let allow_all_unix_sockets = constraints
        .dangerously_allow_all_unix_sockets
        .unwrap_or(constraints.allow_unix_sockets.is_none());
    validate(
        config.dangerously_allow_all_unix_sockets,
        move |candidate| {
            if *candidate && !allow_all_unix_sockets {
                Err(invalid_value(
                    "network.dangerously_allow_all_unix_sockets",
                    "true",
                    "false (disabled by managed config)",
                ))
            } else {
                Ok(())
            }
        },
    )?;

    if let Some(allow_local_binding) = constraints.allow_local_binding {
        validate(config.allow_local_binding, move |candidate| {
            if *candidate && !allow_local_binding {
                Err(invalid_value(
                    "network.allow_local_binding",
                    "true",
                    "false (disabled by managed config)",
                ))
            } else {
                Ok(())
            }
        })?;
    }

    if let Some(allowed_domains) = &constraints.allowed_domains {
        validate_non_global_wildcard_domain_patterns("network.allowed_domains", allowed_domains)?;
        match constraints.allowlist_expansion_enabled {
            Some(true) => {
                let required_set: HashSet<String> = allowed_domains
                    .iter()
                    .map(|entry| entry.to_ascii_lowercase())
                    .collect();
                validate(config_allowed_domains, |candidate| {
                    let candidate_set: HashSet<String> = candidate
                        .iter()
                        .map(|entry| entry.to_ascii_lowercase())
                        .collect();
                    let missing: Vec<String> = required_set
                        .iter()
                        .filter(|entry| {
                            !candidate_set.contains(*entry)
                                && !denied_domain_overrides.contains(*entry)
                        })
                        .cloned()
                        .collect();
                    if missing.is_empty() {
                        Ok(())
                    } else {
                        Err(invalid_value(
                            "network.allowed_domains",
                            "missing managed allowed_domains entries",
                            format!("{missing:?}"),
                        ))
                    }
                })?;
            }
            Some(false) => {
                let required_set: HashSet<String> = allowed_domains
                    .iter()
                    .map(|entry| entry.to_ascii_lowercase())
                    .collect();
                validate(config_allowed_domains, |candidate| {
                    let candidate_set: HashSet<String> = candidate
                        .iter()
                        .map(|entry| entry.to_ascii_lowercase())
                        .collect();
                    let expected_set: HashSet<String> = required_set
                        .difference(&denied_domain_overrides)
                        .cloned()
                        .collect();
                    if candidate_set == expected_set {
                        Ok(())
                    } else {
                        Err(invalid_value(
                            "network.allowed_domains",
                            format!("{candidate:?}"),
                            "must match managed allowed_domains",
                        ))
                    }
                })?;
            }
            None => {
                let managed_patterns: Vec<DomainPattern> = allowed_domains
                    .iter()
                    .map(|entry| DomainPattern::parse_for_constraints(entry))
                    .collect();
                validate(config_allowed_domains, move |candidate| {
                    let mut invalid = Vec::new();
                    for entry in candidate {
                        let candidate_pattern = DomainPattern::parse_for_constraints(entry);
                        if !managed_patterns
                            .iter()
                            .any(|managed| managed.allows(&candidate_pattern))
                        {
                            invalid.push(entry.clone());
                        }
                    }
                    if invalid.is_empty() {
                        Ok(())
                    } else {
                        Err(invalid_value(
                            "network.allowed_domains",
                            format!("{invalid:?}"),
                            "subset of managed allowed_domains",
                        ))
                    }
                })?;
            }
        }
    }

    if let Some(denied_domains) = &constraints.denied_domains {
        validate_non_global_wildcard_domain_patterns("network.denied_domains", denied_domains)?;
        let required_set: HashSet<String> = denied_domains
            .iter()
            .map(|s| s.to_ascii_lowercase())
            .collect();
        match constraints.denylist_expansion_enabled {
            Some(false) => {
                validate(config_denied_domains, move |candidate| {
                    let candidate_set: HashSet<String> = candidate
                        .iter()
                        .map(|entry| entry.to_ascii_lowercase())
                        .collect();
                    if candidate_set == required_set {
                        Ok(())
                    } else {
                        Err(invalid_value(
                            "network.denied_domains",
                            format!("{candidate:?}"),
                            "must match managed denied_domains",
                        ))
                    }
                })?;
            }
            Some(true) | None => {
                validate(config_denied_domains, move |candidate| {
                    let candidate_set: HashSet<String> =
                        candidate.iter().map(|s| s.to_ascii_lowercase()).collect();
                    let missing: Vec<String> = required_set
                        .iter()
                        .filter(|entry| !candidate_set.contains(*entry))
                        .cloned()
                        .collect();
                    if missing.is_empty() {
                        Ok(())
                    } else {
                        Err(invalid_value(
                            "network.denied_domains",
                            "missing managed denied_domains entries",
                            format!("{missing:?}"),
                        ))
                    }
                })?;
            }
        }
    }

    if let Some(allow_unix_sockets) = &constraints.allow_unix_sockets {
        let allowed_set: HashSet<String> = allow_unix_sockets
            .iter()
            .map(|s| s.to_ascii_lowercase())
            .collect();
        validate(config_allow_unix_sockets, move |candidate| {
            let mut invalid = Vec::new();
            for entry in candidate {
                if !allowed_set.contains(&entry.to_ascii_lowercase()) {
                    invalid.push(entry.clone());
                }
            }
            if invalid.is_empty() {
                Ok(())
            } else {
                Err(invalid_value(
                    "network.allow_unix_sockets",
                    format!("{invalid:?}"),
                    "subset of managed allow_unix_sockets",
                ))
            }
        })?;
    }

    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum NetworkProxyConstraintError {
    #[error("invalid value for {field_name}: {candidate} (allowed {allowed})")]
    InvalidValue {
        field_name: &'static str,
        candidate: String,
        allowed: String,
    },
}

impl NetworkProxyConstraintError {
    pub fn into_anyhow(self) -> anyhow::Error {
        anyhow::anyhow!(self)
    }
}

fn validate_non_global_wildcard_domain_patterns(
    field_name: &'static str,
    patterns: &[String],
) -> Result<(), NetworkProxyConstraintError> {
    if let Some(pattern) = patterns
        .iter()
        .find(|pattern| is_global_wildcard_domain_pattern(pattern))
    {
        return Err(NetworkProxyConstraintError::InvalidValue {
            field_name,
            candidate: pattern.trim().to_string(),
            allowed: "exact hosts or scoped wildcards like *.example.com or **.example.com"
                .to_string(),
        });
    }
    Ok(())
}

/// 适配：nova 线上 `NetworkMode` 的受限等级排序（None=0 < Proxy=1，
/// 对位 codex Limited=0 < Full=1）。
fn network_mode_rank(mode: NetworkMode) -> u8 {
    match mode {
        NetworkMode::None => 0,
        NetworkMode::Proxy => 1,
    }
}

#[cfg(test)]
mod tests {}
