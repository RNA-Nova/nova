//! 域名/主机策略原语（移植自 codex network-proxy `policy.rs`）。
//!
//! 对位 codex 529cd6b860：域名通配匹配（`DomainPatternSet`）与主机归一化
//! （`normalize_host` / `unscoped_ip_literal` / `is_global_wildcard_domain_pattern`）
//! 的本体已迁至 `domain_matcher.rs`，此处仅作 re-export 保持既有 import 路径。

use crate::domain_matcher::DomainPatternSet;
use crate::domain_matcher::GlobalWildcard;
pub(crate) use crate::domain_matcher::is_global_wildcard_domain_pattern;
pub use crate::domain_matcher::normalize_host;
pub(crate) use crate::domain_matcher::unscoped_ip_literal;
use anyhow::Result;
use anyhow::ensure;
use std::net::IpAddr;
use std::net::Ipv4Addr;
use std::net::Ipv6Addr;
use url::Host as UrlHost;

/// 策略评估用的归一化主机字符串。
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Host(String);

impl Host {
    pub fn parse(input: &str) -> Result<Self> {
        let normalized = normalize_host(input);
        ensure!(!normalized.is_empty(), "host is empty");
        Ok(Self(normalized))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// 主机是 loopback 主机名或 IP 字面量时返回 true。
pub fn is_loopback_host(host: &Host) -> bool {
    let host = host.as_str();
    let host = unscoped_ip_literal(host).unwrap_or(host);
    if host == "localhost" {
        return true;
    }
    if let Ok(ip) = host.parse::<IpAddr>() {
        return ip.is_loopback();
    }
    false
}

pub fn is_non_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => is_non_public_ipv4(ip),
        IpAddr::V6(ip) => is_non_public_ipv6(ip),
    }
}

fn is_non_public_ipv4(ip: Ipv4Addr) -> bool {
    // 尽量使用标准库分类助手（语义更清晰）；CGNAT、TEST-NET 等尚未被稳定
    // stdlib 助手覆盖的网段回退到 CIDR 检查。
    ip.is_loopback()
        || ip.is_private()
        || ip.is_link_local()
        || ip.is_unspecified()
        || ip.is_multicast()
        || ip.is_broadcast()
        || ipv4_in_cidr(ip, [0, 0, 0, 0], /*prefix*/ 8) // "this network" (RFC 1122)
        || ipv4_in_cidr(ip, [100, 64, 0, 0], /*prefix*/ 10) // CGNAT (RFC 6598)
        || ipv4_in_cidr(ip, [192, 0, 0, 0], /*prefix*/ 24) // IETF Protocol Assignments (RFC 6890)
        || ipv4_in_cidr(ip, [192, 0, 2, 0], /*prefix*/ 24) // TEST-NET-1 (RFC 5737)
        || ipv4_in_cidr(ip, [198, 18, 0, 0], /*prefix*/ 15) // Benchmarking (RFC 2544)
        || ipv4_in_cidr(ip, [198, 51, 100, 0], /*prefix*/ 24) // TEST-NET-2 (RFC 5737)
        || ipv4_in_cidr(ip, [203, 0, 113, 0], /*prefix*/ 24) // TEST-NET-3 (RFC 5737)
        || ipv4_in_cidr(ip, [240, 0, 0, 0], /*prefix*/ 4) // Reserved (RFC 6890)
}

fn ipv4_in_cidr(ip: Ipv4Addr, base: [u8; 4], prefix: u8) -> bool {
    let ip = u32::from(ip);
    let base = u32::from(Ipv4Addr::from(base));
    let mask = if prefix == 0 {
        0
    } else {
        u32::MAX << (32 - prefix)
    };
    (ip & mask) == (base & mask)
}

fn is_non_public_ipv6(ip: Ipv6Addr) -> bool {
    if let Some(v4) = ip.to_ipv4() {
        return is_non_public_ipv4(v4) || ip.is_loopback();
    }
    // 从 SSRF 防护出发，凡非全局可路由地址一律按"本地"处理：
    //  - `::1` loopback
    //  - `fc00::/7` unique-local (RFC 4193)
    //  - `fe80::/10` link-local
    //  - `::` unspecified
    //  - multicast 段
    ip.is_loopback()
        || ip.is_unspecified()
        || ip.is_multicast()
        || ip.is_unique_local()
        || ip.is_unicast_link_local()
}

// 对位 codex 529cd6b860：globset 编译入口替换为 DomainPatternSet 编译入口。
pub(crate) fn compile_allowlist(patterns: &[String]) -> Result<DomainPatternSet> {
    DomainPatternSet::new(patterns, GlobalWildcard::Allow)
}

pub(crate) fn compile_denylist(patterns: &[String]) -> Result<DomainPatternSet> {
    DomainPatternSet::new(patterns, GlobalWildcard::Reject)
}

#[derive(Debug, Clone)]
pub(crate) enum DomainPattern {
    ApexAndSubdomains(String),
    SubdomainsOnly(String),
    Exact(String),
}

impl DomainPattern {
    /// 为约束比较解析策略模式，域名部分用 `url` 校验。
    pub(crate) fn parse_for_constraints(input: &str) -> Self {
        let input = input.trim();
        if input.is_empty() {
            return Self::Exact(String::new());
        }
        if let Some(domain) = input.strip_prefix("**.") {
            return Self::ApexAndSubdomains(parse_domain_for_constraints(domain));
        }
        if let Some(domain) = input.strip_prefix("*.") {
            return Self::SubdomainsOnly(parse_domain_for_constraints(domain));
        }
        Self::Exact(parse_domain_for_constraints(input))
    }

    pub(crate) fn allows(&self, candidate: &DomainPattern) -> bool {
        match self {
            DomainPattern::Exact(domain) => match candidate {
                DomainPattern::Exact(candidate) => domain_eq(candidate, domain),
                _ => false,
            },
            DomainPattern::SubdomainsOnly(domain) => match candidate {
                DomainPattern::Exact(candidate) => is_strict_subdomain(candidate, domain),
                DomainPattern::SubdomainsOnly(candidate) => {
                    is_subdomain_or_equal(candidate, domain)
                }
                DomainPattern::ApexAndSubdomains(candidate) => {
                    is_strict_subdomain(candidate, domain)
                }
            },
            DomainPattern::ApexAndSubdomains(domain) => match candidate {
                DomainPattern::Exact(candidate) => is_subdomain_or_equal(candidate, domain),
                DomainPattern::SubdomainsOnly(candidate) => {
                    is_subdomain_or_equal(candidate, domain)
                }
                DomainPattern::ApexAndSubdomains(candidate) => {
                    is_subdomain_or_equal(candidate, domain)
                }
            },
        }
    }
}

fn parse_domain_for_constraints(domain: &str) -> String {
    let domain = domain.trim().trim_end_matches('.');
    if domain.is_empty() {
        return String::new();
    }
    let host = if domain.starts_with('[') && domain.ends_with(']') {
        &domain[1..domain.len().saturating_sub(1)]
    } else {
        domain
    };
    if host.contains('*') || host.contains('?') || host.contains('%') {
        return domain.to_string();
    }
    match UrlHost::parse(host) {
        Ok(host) => host.to_string(),
        Err(_) => String::new(),
    }
}

fn normalize_domain(domain: &str) -> String {
    domain.trim_end_matches('.').to_ascii_lowercase()
}

fn domain_eq(left: &str, right: &str) -> bool {
    normalize_domain(left) == normalize_domain(right)
}

fn is_subdomain_or_equal(child: &str, parent: &str) -> bool {
    let child = normalize_domain(child);
    let parent = normalize_domain(parent);
    if child == parent {
        return true;
    }
    child.ends_with(&format!(".{parent}"))
}

fn is_strict_subdomain(child: &str, parent: &str) -> bool {
    let child = normalize_domain(child);
    let parent = normalize_domain(parent);
    child != parent && child.ends_with(&format!(".{parent}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::NetworkMode;
    use pretty_assertions::assert_eq;

    #[test]
    fn method_allowed_proxy_mode_allows_everything() {
        assert!(NetworkMode::Proxy.allows_method("GET"));
        assert!(NetworkMode::Proxy.allows_method("POST"));
        assert!(NetworkMode::Proxy.allows_method("CONNECT"));
    }

    #[test]
    fn method_allowed_none_mode_allows_nothing() {
        // 适配层：nova 线上 NetworkMode 只有 None|Proxy（对位 codex Limited|Full 的
        // 裁剪映射）；None = 无网络访问，所有方法一律拒绝。
        assert!(!NetworkMode::None.allows_method("GET"));
        assert!(!NetworkMode::None.allows_method("HEAD"));
        assert!(!NetworkMode::None.allows_method("OPTIONS"));
        assert!(!NetworkMode::None.allows_method("POST"));
        assert!(!NetworkMode::None.allows_method("CONNECT"));
    }

    #[test]
    fn compile_globset_normalizes_trailing_dots() {
        // 对位 codex 529cd6b860：globset 编译入口替换为 DomainPatternSet 编译入口。
        let set = compile_denylist(&["Example.COM.".to_string()]).unwrap();

        assert_eq!(true, set.is_match("example.com"));
        assert_eq!(false, set.is_match("api.example.com"));
    }

    #[test]
    fn compile_globset_normalizes_wildcards() {
        let set = compile_denylist(&["*.Example.COM.".to_string()]).unwrap();

        assert_eq!(true, set.is_match("api.example.com"));
        assert_eq!(false, set.is_match("example.com"));
    }

    #[test]
    fn compile_globset_supports_mid_label_wildcards() {
        let set = compile_denylist(&["region*.v2.argotunnel.com".to_string()]).unwrap();

        assert_eq!(true, set.is_match("region1.v2.argotunnel.com"));
        assert_eq!(true, set.is_match("region.v2.argotunnel.com"));
        assert_eq!(false, set.is_match("xregion1.v2.argotunnel.com"));
        assert_eq!(false, set.is_match("foo.region1.v2.argotunnel.com"));
    }

    #[test]
    fn compile_globset_normalizes_apex_and_subdomains() {
        let set = compile_denylist(&["**.Example.COM.".to_string()]).unwrap();

        assert_eq!(true, set.is_match("example.com"));
        assert_eq!(true, set.is_match("api.example.com"));
    }

    #[test]
    fn compile_globset_normalizes_bracketed_ipv6_literals() {
        let set = compile_denylist(&["[::1]".to_string()]).unwrap();

        assert_eq!(true, set.is_match("::1"));
    }

    #[test]
    fn compile_globset_preserves_scoped_ipv6_literals() {
        let set = compile_denylist(&["[fe80::1%25lo0]".to_string()]).unwrap();

        assert_eq!(true, set.is_match("fe80::1%lo0"));
        assert_eq!(false, set.is_match("fe80::1%lo1"));
        assert_eq!(false, set.is_match("fe80::1"));
    }

    #[test]
    fn is_loopback_host_handles_localhost_variants() {
        assert!(is_loopback_host(&Host::parse("localhost").unwrap()));
        assert!(is_loopback_host(&Host::parse("localhost.").unwrap()));
        assert!(is_loopback_host(&Host::parse("LOCALHOST").unwrap()));
        assert!(!is_loopback_host(&Host::parse("notlocalhost").unwrap()));
    }

    #[test]
    fn is_loopback_host_handles_ip_literals() {
        assert!(is_loopback_host(&Host::parse("127.0.0.1").unwrap()));
        assert!(is_loopback_host(&Host::parse("::1").unwrap()));
        assert!(!is_loopback_host(&Host::parse("1.2.3.4").unwrap()));
    }

    #[test]
    fn is_non_public_ip_rejects_private_and_loopback_ranges() {
        assert!(is_non_public_ip("127.0.0.1".parse().unwrap()));
        assert!(is_non_public_ip("10.0.0.1".parse().unwrap()));
        assert!(is_non_public_ip("192.168.0.1".parse().unwrap()));
        assert!(is_non_public_ip("100.64.0.1".parse().unwrap()));
        assert!(is_non_public_ip("192.0.0.1".parse().unwrap()));
        assert!(is_non_public_ip("192.0.2.1".parse().unwrap()));
        assert!(is_non_public_ip("198.18.0.1".parse().unwrap()));
        assert!(is_non_public_ip("198.51.100.1".parse().unwrap()));
        assert!(is_non_public_ip("203.0.113.1".parse().unwrap()));
        assert!(is_non_public_ip("240.0.0.1".parse().unwrap()));
        assert!(is_non_public_ip("0.1.2.3".parse().unwrap()));
        assert!(!is_non_public_ip("8.8.8.8".parse().unwrap()));

        assert!(is_non_public_ip("::ffff:127.0.0.1".parse().unwrap()));
        assert!(is_non_public_ip("::ffff:10.0.0.1".parse().unwrap()));
        assert!(!is_non_public_ip("::ffff:8.8.8.8".parse().unwrap()));

        assert!(is_non_public_ip("::1".parse().unwrap()));
        assert!(is_non_public_ip("fe80::1".parse().unwrap()));
        assert!(is_non_public_ip("fc00::1".parse().unwrap()));
    }
    // 对位 codex 529cd6b860：`normalize_host_*` 测试随 normalize_host 本体迁至
    // `domain_matcher_tests.rs`。
}
