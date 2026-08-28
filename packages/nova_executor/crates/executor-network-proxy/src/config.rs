//! 网络代理配置的行为层（移植自 codex network-proxy `config.rs`）。
//!
//! 线上类型本身（`NetworkProxyConfig` / `NetworkDomainPermissions` 等）定义在 crate 根
//! lib.rs，serde 形状以那里为准；本模块以同 crate 扩展 impl 的方式为这些类型提供
//! 运行时行为，并承载监听地址解析/校验逻辑。
//!
//! 适配点（codex → nova 线上形状）：
//! - `proxy_url` / `socks_url`：codex 为带默认值的 `String`，nova 为 `Option<String>`；
//!   解析时缺省回落到 `127.0.0.1:3128` / `127.0.0.1:8081`。
//! - 域名条目字段名 `domain`（codex 为 `pattern`）；权限枚举无 `None` 变体，
//!   冲突去重时 `Allow < Deny`（deny 胜出）。
//! - `unix_sockets` 为 `Vec<{path, permission}>`（codex 为扁平 map）。
//! - `mitm_hooks` 为 `Vec<String>`（codex 为结构化 hook 配置）；MITM 面第一期未移植。
//! - 裁点：`credential_broker_openai_host` / `trusted_credential_broker_host`（凭证经纪）未移植。

use crate::NetworkDomainPermission;
use crate::NetworkDomainPermissionEntry;
use crate::NetworkDomainPermissions;
use crate::NetworkMode;
use crate::NetworkProxyConfig;
use crate::NetworkUnixSocketPermission;
use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use nova_executor_utils_absolute_path::AbsolutePathBuf;
use std::net::IpAddr;
use std::net::SocketAddr;
use std::path::Path;
use tracing::warn;
use url::Url;

/// HTTP 代理默认监听地址（`proxy_url` 缺省时的回落值）。
pub(crate) const DEFAULT_PROXY_URL: &str = "http://127.0.0.1:3128";
/// SOCKS5 代理默认监听地址（`socks_url` 缺省时的回落值）。
pub(crate) const DEFAULT_SOCKS_URL: &str = "http://127.0.0.1:8081";

impl NetworkMode {
    /// 该模式下是否允许某个 HTTP 方法。
    ///
    /// 适配层：nova 线上 `NetworkMode` 只有 `None | Proxy`（codex 为 `Limited | Full`）。
    /// `Proxy` 对位 codex `Full`（全方法放行）；`None` 表示无网络访问，全部拒绝。
    pub fn allows_method(self, method: &str) -> bool {
        let _ = method;
        match self {
            Self::Proxy => true,
            Self::None => false,
        }
    }
}

impl NetworkDomainPermissions {
    /// 归并重复条目：保留首次出现顺序，同 `domain` 冲突时 `Deny` 覆盖 `Allow`。
    pub(crate) fn effective_entries(&self) -> Vec<NetworkDomainPermissionEntry> {
        let mut order = Vec::new();
        let mut effective_permissions: std::collections::BTreeMap<String, NetworkDomainPermission> =
            Default::default();

        for entry in &self.entries {
            if !effective_permissions.contains_key(&entry.domain) {
                order.push(entry.domain.clone());
            }

            let permission = effective_permissions
                .entry(entry.domain.clone())
                .or_insert(entry.permission);
            if domain_permission_rank(entry.permission) > domain_permission_rank(*permission) {
                *permission = entry.permission;
            }
        }

        order
            .into_iter()
            .filter_map(|domain| {
                effective_permissions
                    .remove(&domain)
                    .map(|permission| NetworkDomainPermissionEntry {
                        domain,
                        permission,
                    })
            })
            .collect()
    }
}

const fn domain_permission_rank(permission: NetworkDomainPermission) -> u8 {
    match permission {
        NetworkDomainPermission::Allow => 0,
        NetworkDomainPermission::Deny => 1,
    }
}

impl NetworkProxyConfig {
    pub fn allowed_domains(&self) -> Option<Vec<String>> {
        self.domain_entries(NetworkDomainPermission::Allow)
    }

    pub fn denied_domains(&self) -> Option<Vec<String>> {
        self.domain_entries(NetworkDomainPermission::Deny)
    }

    fn domain_entries(&self, permission: NetworkDomainPermission) -> Option<Vec<String>> {
        self.domains
            .as_ref()
            .map(|domains| {
                domains
                    .effective_entries()
                    .iter()
                    .filter(|entry| entry.permission == permission)
                    .map(|entry| entry.domain.clone())
                    .collect()
            })
            .filter(|entries: &Vec<String>| !entries.is_empty())
    }

    pub fn allow_unix_sockets(&self) -> Vec<String> {
        self.unix_sockets
            .as_ref()
            .map(|unix_sockets| {
                unix_sockets
                    .entries
                    .iter()
                    .filter(|entry| {
                        matches!(entry.permission, NetworkUnixSocketPermission::Allow)
                    })
                    .map(|entry| entry.path.clone())
                    .collect()
            })
            .unwrap_or_default()
    }

    pub fn set_allowed_domains(&mut self, allowed_domains: Vec<String>) {
        self.set_domain_entries(allowed_domains, NetworkDomainPermission::Allow);
    }

    pub fn set_denied_domains(&mut self, denied_domains: Vec<String>) {
        self.set_domain_entries(denied_domains, NetworkDomainPermission::Deny);
    }

    pub fn upsert_domain_permission(
        &mut self,
        host: String,
        permission: NetworkDomainPermission,
        normalize: impl Fn(&str) -> String,
    ) {
        let mut domains = self.domains.take().unwrap_or_default();
        let normalized_host = normalize(&host);
        domains
            .entries
            .retain(|entry| normalize(&entry.domain) != normalized_host);
        domains.entries.push(NetworkDomainPermissionEntry {
            domain: host,
            permission,
        });
        self.domains = (!domains.entries.is_empty()).then_some(domains);
    }

    pub fn set_allow_unix_sockets(&mut self, allow_unix_sockets: Vec<String>) {
        self.set_unix_socket_entries(allow_unix_sockets, NetworkUnixSocketPermission::Allow);
    }

    fn set_domain_entries(&mut self, entries: Vec<String>, permission: NetworkDomainPermission) {
        let mut domains = self.domains.take().unwrap_or_default();
        domains
            .entries
            .retain(|entry| entry.permission != permission);
        for entry in entries {
            if !domains
                .entries
                .iter()
                .any(|existing| existing.domain == entry && existing.permission == permission)
            {
                domains.entries.push(NetworkDomainPermissionEntry {
                    domain: entry,
                    permission,
                });
            }
        }
        self.domains = (!domains.entries.is_empty()).then_some(domains);
    }

    fn set_unix_socket_entries(
        &mut self,
        entries: Vec<String>,
        permission: NetworkUnixSocketPermission,
    ) {
        let mut unix_sockets = self.unix_sockets.take().unwrap_or_default();
        unix_sockets
            .entries
            .retain(|entry| entry.permission != permission);
        for entry in entries {
            // codex 端该集合是 BTreeMap（path 唯一）；Vec 形状下同 path 先去重再追加。
            unix_sockets.entries.retain(|existing| {
                existing.permission == permission || existing.path != entry
            });
            unix_sockets
                .entries
                .push(crate::NetworkUnixSocketPermissionEntry {
                    path: entry,
                    permission,
                });
        }
        self.unix_sockets = (!unix_sockets.entries.is_empty()).then_some(unix_sockets);
    }
}

/// 钳制非 loopback 绑定地址到 loopback，除非显式允许。
fn clamp_non_loopback(
    addr: SocketAddr,
    allow_non_loopback: bool,
    name: &str,
    override_setting_name: &str,
) -> SocketAddr {
    if addr.ip().is_loopback() {
        return addr;
    }

    if allow_non_loopback {
        warn!("DANGEROUS: {name} listening on non-loopback address {addr}");
        return addr;
    }

    warn!(
        "{name} requested non-loopback bind ({addr}); clamping to 127.0.0.1:{port} (set {override_setting_name} to override)",
        port = addr.port()
    );
    SocketAddr::from(([127, 0, 0, 1], addr.port()))
}

pub(crate) fn clamp_bind_addrs(
    http_addr: SocketAddr,
    socks_addr: SocketAddr,
    cfg: &NetworkProxyConfig,
) -> (SocketAddr, SocketAddr) {
    let http_addr = clamp_non_loopback(
        http_addr,
        cfg.dangerously_allow_non_loopback_proxy,
        "HTTP proxy",
        "dangerously_allow_non_loopback_proxy",
    );
    let socks_addr = clamp_non_loopback(
        socks_addr,
        cfg.dangerously_allow_non_loopback_proxy,
        "SOCKS5 proxy",
        "dangerously_allow_non_loopback_proxy",
    );
    if cfg.allow_unix_sockets().is_empty() && !cfg.dangerously_allow_all_unix_sockets {
        return (http_addr, socks_addr);
    }

    // `x-unix-socket` 是有意的本地逃生门。如果代理可从机器外可达，它就可能变成
    // 通往本地守护进程（如 docker.sock）的远程桥梁。为避免误伤，启用 unix socket
    // 代理时一律强制 loopback 绑定。
    if cfg.dangerously_allow_non_loopback_proxy && !http_addr.ip().is_loopback() {
        warn!(
            "unix socket proxying is enabled; ignoring dangerously_allow_non_loopback_proxy and clamping HTTP proxy to loopback"
        );
    }
    if cfg.dangerously_allow_non_loopback_proxy && !socks_addr.ip().is_loopback() {
        warn!(
            "unix socket proxying is enabled; ignoring dangerously_allow_non_loopback_proxy and clamping SOCKS5 proxy to loopback"
        );
    }
    (
        SocketAddr::from(([127, 0, 0, 1], http_addr.port())),
        SocketAddr::from(([127, 0, 0, 1], socks_addr.port())),
    )
}

pub struct RuntimeConfig {
    pub http_addr: SocketAddr,
    pub socks_addr: SocketAddr,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct UnixStyleAbsolutePath(String);

impl UnixStyleAbsolutePath {
    fn parse(value: &str) -> Option<Self> {
        value.starts_with('/').then(|| Self(value.to_string()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ValidatedUnixSocketPath {
    Native(AbsolutePathBuf),
    UnixStyleAbsolute(UnixStyleAbsolutePath),
}

impl ValidatedUnixSocketPath {
    pub(crate) fn parse(socket_path: &str) -> Result<Self> {
        let path = Path::new(socket_path);
        if path.is_absolute() {
            let path = AbsolutePathBuf::from_absolute_path(path)
                .with_context(|| format!("failed to normalize unix socket path {socket_path:?}"))?;
            return Ok(Self::Native(path));
        }

        if let Some(path) = UnixStyleAbsolutePath::parse(socket_path) {
            return Ok(Self::UnixStyleAbsolute(path));
        }

        bail!("expected an absolute path, got {socket_path:?}");
    }
}

pub(crate) fn validate_unix_socket_allowlist_paths(cfg: &NetworkProxyConfig) -> Result<()> {
    for (index, socket_path) in cfg.allow_unix_sockets().iter().enumerate() {
        ValidatedUnixSocketPath::parse(socket_path)
            .with_context(|| format!("invalid network.allow_unix_sockets[{index}]"))?;
    }
    Ok(())
}

pub fn resolve_runtime(cfg: &NetworkProxyConfig) -> Result<RuntimeConfig> {
    validate_unix_socket_allowlist_paths(cfg)?;

    let proxy_url = cfg.proxy_url.as_deref().unwrap_or(DEFAULT_PROXY_URL);
    let socks_url = cfg.socks_url.as_deref().unwrap_or(DEFAULT_SOCKS_URL);
    let http_addr = resolve_addr(proxy_url, /*default_port*/ 3128)
        .with_context(|| format!("invalid network.proxy_url: {proxy_url}"))?;
    let socks_addr = resolve_addr(socks_url, /*default_port*/ 8081)
        .with_context(|| format!("invalid network.socks_url: {socks_url}"))?;
    let (http_addr, socks_addr) = clamp_bind_addrs(http_addr, socks_addr, cfg);

    Ok(RuntimeConfig {
        http_addr,
        socks_addr,
    })
}

/// 返回已配置托管代理监听器使用的 loopback 端口（排序后）。
pub fn managed_proxy_ports(cfg: &NetworkProxyConfig) -> Result<Vec<u16>> {
    let runtime = resolve_runtime(cfg)?;
    if runtime.http_addr.port() == 0 {
        bail!("network.proxy_url must use a fixed non-zero port for managed proxy provisioning");
    }
    let mut ports = vec![runtime.http_addr.port()];
    if cfg.enable_socks5 {
        if runtime.socks_addr.port() == 0 {
            bail!(
                "network.socks_url must use a fixed non-zero port for managed proxy provisioning"
            );
        }
        ports.push(runtime.socks_addr.port());
    }
    ports.sort_unstable();
    ports.dedup();
    Ok(ports)
}

fn resolve_addr(url: &str, default_port: u16) -> Result<SocketAddr> {
    let addr_parts = parse_host_port(url, default_port)?;
    let host = if addr_parts.host.eq_ignore_ascii_case("localhost") {
        "127.0.0.1".to_string()
    } else {
        addr_parts.host
    };
    match host.parse::<IpAddr>() {
        Ok(ip) => Ok(SocketAddr::new(ip, addr_parts.port)),
        Err(_) => Ok(SocketAddr::from(([127, 0, 0, 1], addr_parts.port))),
    }
}

pub fn host_and_port_from_network_addr(value: &str, default_port: u16) -> String {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return "<missing>".to_string();
    }

    let parts = match parse_host_port(trimmed, default_port) {
        Ok(parts) => parts,
        Err(_) => {
            return format_host_and_port(trimmed, default_port);
        }
    };

    format_host_and_port(&parts.host, parts.port)
}

fn format_host_and_port(host: &str, port: u16) -> String {
    if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct SocketAddressParts {
    host: String,
    port: u16,
}

fn parse_host_port(url: &str, default_port: u16) -> Result<SocketAddressParts> {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        bail!("missing host in network proxy address: {url}");
    }

    // 避免把不带方括号的 IPv6 字面量（如 "2001:db8::1"）当作带 scheme 的 URL。
    if matches!(trimmed.parse::<IpAddr>(), Ok(IpAddr::V6(_))) && !trimmed.starts_with('[') {
        return Ok(SocketAddressParts {
            host: trimmed.to_string(),
            port: default_port,
        });
    }

    // 输入像 URL 时优先用标准 URL 解析器；缺 scheme 时补一个，兼容松散的 host:port 输入。
    let candidate = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("http://{trimmed}")
    };
    if let Ok(parsed) = Url::parse(&candidate)
        && let Some(host) = parsed.host_str()
    {
        let host = host.trim_matches(|c| c == '[' || c == ']');
        if host.is_empty() {
            bail!("missing host in network proxy address: {url}");
        }
        return Ok(SocketAddressParts {
            host: host.to_string(),
            port: parsed.port().unwrap_or(default_port),
        });
    }

    parse_host_port_fallback(trimmed, default_port)
}

fn parse_host_port_fallback(input: &str, default_port: u16) -> Result<SocketAddressParts> {
    let without_scheme = input
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(input);
    let host_port = without_scheme.split('/').next().unwrap_or(without_scheme);
    let host_port = host_port
        .rsplit_once('@')
        .map(|(_, rest)| rest)
        .unwrap_or(host_port);

    if host_port.starts_with('[')
        && let Some(end) = host_port.find(']')
    {
        let host = &host_port[1..end];
        let port = host_port[end + 1..]
            .strip_prefix(':')
            .and_then(|port| port.parse::<u16>().ok())
            .unwrap_or(default_port);
        if host.is_empty() {
            bail!("missing host in network proxy address: {input}");
        }
        return Ok(SocketAddressParts {
            host: host.to_string(),
            port,
        });
    }

    // 只在恰好有一个 `:` 时按 `host:port` 处理，避免把不带方括号的 IPv6 地址误判。
    if host_port.bytes().filter(|b| *b == b':').count() == 1
        && let Some((host, port)) = host_port.rsplit_once(':')
    {
        if host.is_empty() {
            bail!("missing host in network proxy address: {input}");
        }
        return Ok(SocketAddressParts {
            host: host.to_string(),
            port: port.parse::<u16>().ok().unwrap_or(default_port),
        });
    }

    if host_port.is_empty() {
        bail!("missing host in network proxy address: {input}");
    }
    Ok(SocketAddressParts {
        host: host_port.to_string(),
        port: default_port,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::NetworkUnixSocketPermissionEntry;
    use pretty_assertions::assert_eq;

    fn settings_with_unix_sockets(unix_sockets: &[&str]) -> NetworkProxyConfig {
        let mut settings = NetworkProxyConfig::default();
        if !unix_sockets.is_empty() {
            settings.set_allow_unix_sockets(
                unix_sockets
                    .iter()
                    .map(|path| (*path).to_string())
                    .collect(),
            );
        }
        settings
    }

    #[test]
    fn network_proxy_settings_default_matches_local_use_baseline() {
        // nova 线上形状的默认值基线（与 codex 的差异：proxy_url/socks_url 为 Option、
        // 无 credential_broker_openai_host、mitm_hooks 为 Vec<String>）。
        assert_eq!(
            NetworkProxyConfig::default(),
            NetworkProxyConfig {
                enabled: false,
                enable_socks5: false,
                enable_socks5_udp: false,
                allow_upstream_proxy: false,
                dangerously_allow_all_unix_sockets: false,
                mode: NetworkMode::None,
                domains: None,
                unix_sockets: None,
                allow_local_binding: false,
                mitm: false,
                credential_broker: false,
                dangerously_allow_plaintext_credential_injection: false,
                mitm_hooks: Vec::new(),
                proxy_url: None,
                socks_url: None,
                dangerously_allow_non_loopback_proxy: false,
            }
        );
    }

    #[test]
    fn managed_proxy_ports_reject_ephemeral_ports() {
        let mut config = NetworkProxyConfig {
            proxy_url: Some("http://127.0.0.1:0".to_string()),
            ..Default::default()
        };

        assert_eq!(
            managed_proxy_ports(&config).unwrap_err().to_string(),
            "network.proxy_url must use a fixed non-zero port for managed proxy provisioning"
        );

        config.proxy_url = Some("http://127.0.0.1:3128".to_string());
        config.socks_url = Some("socks5h://127.0.0.1:48081".to_string());
        config.enable_socks5 = true;
        assert_eq!(managed_proxy_ports(&config).unwrap(), vec![3128, 48081]);

        config.socks_url = Some("socks5h://127.0.0.1:0".to_string());
        assert_eq!(
            managed_proxy_ports(&config).unwrap_err().to_string(),
            "network.socks_url must use a fixed non-zero port for managed proxy provisioning"
        );

        config.enable_socks5 = false;
        assert_eq!(managed_proxy_ports(&config).unwrap(), vec![3128]);
    }

    #[test]
    fn network_proxy_config_uses_struct_defaults_for_missing_fields() {
        let config: NetworkProxyConfig = serde_json::from_str(r#"{ "enabled": true }"#).unwrap();
        let expected = NetworkProxyConfig {
            enabled: true,
            ..NetworkProxyConfig::default()
        };

        assert_eq!(config, expected);
    }

    #[test]
    fn set_allowed_domains_preserves_existing_deny_for_same_pattern() {
        let mut settings = NetworkProxyConfig::default();
        settings.set_denied_domains(vec!["example.com".to_string()]);

        settings.set_allowed_domains(vec!["example.com".to_string()]);

        assert_eq!(settings.allowed_domains(), None);
        assert_eq!(
            settings.denied_domains(),
            Some(vec!["example.com".to_string()])
        );
    }

    #[test]
    fn network_domain_permissions_serialize_to_nova_wire_shape() {
        // 锁定 nova 线上 serde 形状：`{entries: [{domain, permission}]}`（camelCase），
        // 与 codex 的扁平 map 形状刻意不同——线上契约以 stub 为准。
        let mut settings = NetworkProxyConfig::default();
        settings.set_denied_domains(vec!["example.com".to_string()]);
        settings.set_allowed_domains(vec!["api.example.com".to_string()]);

        let value = serde_json::to_value(&settings).unwrap();

        assert_eq!(
            value.get("domains").unwrap(),
            &serde_json::json!({
                "entries": [
                    { "domain": "example.com", "permission": "deny" },
                    { "domain": "api.example.com", "permission": "allow" },
                ],
            })
        );
        // 往返无损。
        let round_trip: NetworkProxyConfig =
            serde_json::from_value(serde_json::to_value(&settings).unwrap()).unwrap();
        assert_eq!(round_trip, settings);
    }

    #[test]
    fn unix_socket_permissions_round_trip_in_nova_wire_shape() {
        let mut settings = NetworkProxyConfig::default();
        settings.unix_sockets = Some(crate::NetworkUnixSocketPermissions {
            entries: vec![
                NetworkUnixSocketPermissionEntry {
                    path: "/tmp/allowed.sock".to_string(),
                    permission: NetworkUnixSocketPermission::Allow,
                },
                NetworkUnixSocketPermissionEntry {
                    path: "/tmp/denied.sock".to_string(),
                    permission: NetworkUnixSocketPermission::Deny,
                },
            ],
        });

        let value = serde_json::to_value(&settings).unwrap();
        assert_eq!(
            value.get("unixSockets").unwrap(),
            &serde_json::json!({
                "entries": [
                    { "path": "/tmp/allowed.sock", "permission": "allow" },
                    { "path": "/tmp/denied.sock", "permission": "deny" },
                ],
            })
        );
        assert_eq!(settings.allow_unix_sockets(), vec!["/tmp/allowed.sock"]);
    }

    #[test]
    fn parse_host_port_defaults_for_empty_string() {
        assert!(parse_host_port("", /*default_port*/ 1234).is_err());
    }

    #[test]
    fn parse_host_port_defaults_for_whitespace() {
        assert!(parse_host_port("   ", /*default_port*/ 5555).is_err());
    }

    #[test]
    fn parse_host_port_parses_host_port_without_scheme() {
        assert_eq!(
            parse_host_port("127.0.0.1:8080", /*default_port*/ 3128).unwrap(),
            SocketAddressParts {
                host: "127.0.0.1".to_string(),
                port: 8080,
            }
        );
    }

    #[test]
    fn parse_host_port_parses_host_port_with_scheme_and_path() {
        assert_eq!(
            parse_host_port(
                "http://example.com:8080/some/path",
                /*default_port*/ 3128
            )
            .unwrap(),
            SocketAddressParts {
                host: "example.com".to_string(),
                port: 8080,
            }
        );
    }

    #[test]
    fn parse_host_port_strips_userinfo() {
        assert_eq!(
            parse_host_port(
                "http://user:pass@host.example:5555",
                /*default_port*/ 3128
            )
            .unwrap(),
            SocketAddressParts {
                host: "host.example".to_string(),
                port: 5555,
            }
        );
    }

    #[test]
    fn parse_host_port_parses_ipv6_with_brackets() {
        assert_eq!(
            parse_host_port("http://[::1]:9999", /*default_port*/ 3128).unwrap(),
            SocketAddressParts {
                host: "::1".to_string(),
                port: 9999,
            }
        );
    }

    #[test]
    fn parse_host_port_does_not_treat_unbracketed_ipv6_as_host_port() {
        assert_eq!(
            parse_host_port("2001:db8::1", /*default_port*/ 3128).unwrap(),
            SocketAddressParts {
                host: "2001:db8::1".to_string(),
                port: 3128,
            }
        );
    }

    #[test]
    fn parse_host_port_falls_back_to_default_port_when_port_is_invalid() {
        assert_eq!(
            parse_host_port("example.com:notaport", /*default_port*/ 3128).unwrap(),
            SocketAddressParts {
                host: "example.com".to_string(),
                port: 3128,
            }
        );
    }

    #[test]
    fn host_and_port_from_network_addr_defaults_for_empty_string() {
        assert_eq!(
            host_and_port_from_network_addr("", /*default_port*/ 1234),
            "<missing>"
        );
    }

    #[test]
    fn host_and_port_from_network_addr_formats_ipv6() {
        assert_eq!(
            host_and_port_from_network_addr("http://[::1]:8080", /*default_port*/ 3128),
            "[::1]:8080"
        );
    }

    #[test]
    fn resolve_addr_maps_localhost_to_loopback() {
        assert_eq!(
            resolve_addr("localhost", /*default_port*/ 3128).unwrap(),
            "127.0.0.1:3128".parse::<SocketAddr>().unwrap()
        );
    }

    #[test]
    fn resolve_addr_parses_ip_literals() {
        assert_eq!(
            resolve_addr("1.2.3.4", /*default_port*/ 80).unwrap(),
            "1.2.3.4:80".parse::<SocketAddr>().unwrap()
        );
    }

    #[test]
    fn resolve_addr_parses_ipv6_literals() {
        assert_eq!(
            resolve_addr("http://[::1]:8080", /*default_port*/ 3128).unwrap(),
            "[::1]:8080".parse::<SocketAddr>().unwrap()
        );
    }

    #[test]
    fn resolve_addr_falls_back_to_loopback_for_hostnames() {
        assert_eq!(
            resolve_addr("http://example.com:5555", /*default_port*/ 3128).unwrap(),
            "127.0.0.1:5555".parse::<SocketAddr>().unwrap()
        );
    }

    #[test]
    fn clamp_bind_addrs_allows_non_loopback_when_enabled() {
        let cfg = NetworkProxyConfig {
            dangerously_allow_non_loopback_proxy: true,
            ..Default::default()
        };
        let http_addr = "0.0.0.0:3128".parse::<SocketAddr>().unwrap();
        let socks_addr = "0.0.0.0:8081".parse::<SocketAddr>().unwrap();

        let (http_addr, socks_addr) = clamp_bind_addrs(http_addr, socks_addr, &cfg);

        assert_eq!(http_addr, "0.0.0.0:3128".parse::<SocketAddr>().unwrap());
        assert_eq!(socks_addr, "0.0.0.0:8081".parse::<SocketAddr>().unwrap());
    }

    #[test]
    fn clamp_bind_addrs_forces_loopback_when_unix_sockets_enabled() {
        let cfg = {
            let mut settings = settings_with_unix_sockets(&["/tmp/docker.sock"]);
            settings.dangerously_allow_non_loopback_proxy = true;
            settings
        };
        let http_addr = "0.0.0.0:3128".parse::<SocketAddr>().unwrap();
        let socks_addr = "0.0.0.0:8081".parse::<SocketAddr>().unwrap();

        let (http_addr, socks_addr) = clamp_bind_addrs(http_addr, socks_addr, &cfg);

        assert_eq!(http_addr, "127.0.0.1:3128".parse::<SocketAddr>().unwrap());
        assert_eq!(socks_addr, "127.0.0.1:8081".parse::<SocketAddr>().unwrap());
    }

    #[test]
    fn clamp_bind_addrs_forces_loopback_when_all_unix_sockets_enabled() {
        let cfg = NetworkProxyConfig {
            dangerously_allow_non_loopback_proxy: true,
            dangerously_allow_all_unix_sockets: true,
            ..Default::default()
        };
        let http_addr = "0.0.0.0:3128".parse::<SocketAddr>().unwrap();
        let socks_addr = "0.0.0.0:8081".parse::<SocketAddr>().unwrap();

        let (http_addr, socks_addr) = clamp_bind_addrs(http_addr, socks_addr, &cfg);

        assert_eq!(http_addr, "127.0.0.1:3128".parse::<SocketAddr>().unwrap());
        assert_eq!(socks_addr, "127.0.0.1:8081".parse::<SocketAddr>().unwrap());
    }

    #[test]
    fn resolve_runtime_rejects_relative_allow_unix_sockets_entries() {
        let cfg = settings_with_unix_sockets(&["relative.sock"]);

        let err = match resolve_runtime(&cfg) {
            Ok(runtime) => panic!(
                "relative allow_unix_sockets should fail, but resolve_runtime succeeded: {:?}",
                runtime.http_addr
            ),
            Err(err) => err,
        };
        assert!(
            err.to_string().contains("network.allow_unix_sockets[0]"),
            "error should point at the invalid allow_unix_sockets entry: {err:#}"
        );
    }

    #[test]
    fn resolve_runtime_accepts_unix_style_absolute_allow_unix_sockets_entries() {
        let cfg = settings_with_unix_sockets(&["/private/tmp/example.sock"]);

        assert!(
            resolve_runtime(&cfg).is_ok(),
            "unix-style absolute allow_unix_sockets entry should be accepted"
        );
    }

    #[test]
    fn resolve_runtime_uses_default_loopback_addrs_when_urls_unset() {
        // nova 适配：`proxy_url`/`socks_url` 为 None 时回落默认 loopback 端口。
        let runtime = resolve_runtime(&NetworkProxyConfig::default()).unwrap();
        assert_eq!(
            runtime.http_addr,
            "127.0.0.1:3128".parse::<SocketAddr>().unwrap()
        );
        assert_eq!(
            runtime.socks_addr,
            "127.0.0.1:8081".parse::<SocketAddr>().unwrap()
        );
    }
}
