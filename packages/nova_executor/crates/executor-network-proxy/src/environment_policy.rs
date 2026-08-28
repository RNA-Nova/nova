//! 环境属主提供的流量限制（移植自 codex network-proxy `environment_policy.rs`）。
//!
//! 清单外说明：该文件不在首批移植清单，但被保留的 `proxy/execution_scope.rs`
//! （`for_execution`）与 `NetworkProxy::remote_launch_config` 引用，且自身无 MITM/凭证
//! 耦合，故随 proxy.rs 一并移植。
//!
//! 适配点：`unix_sockets` 线上形状为 `Vec<{path, permission}>`（codex 为扁平 map），
//! 合并语义保持一致：deny 永远优先；同 path 冲突按 deny 优先归并。

use crate::NetworkDomainPermission;
use crate::NetworkDomainPermissions;
use crate::NetworkProxyConfig;
use crate::NetworkUnixSocketPermission;
use crate::NetworkUnixSocketPermissionEntry;
use crate::NetworkUnixSocketPermissions;
use serde::Deserialize;
use serde::Serialize;

/// 单个执行环境属主提供的流量限制。
///
/// 代理启用与否、监听器、网络模式、MITM、凭证都不属于 attachment 拥有的流量策略。
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentNetworkPolicy {
    pub domains: Option<NetworkDomainPermissions>,
    pub unix_sockets: Option<NetworkUnixSocketPermissions>,
    pub allow_upstream_proxy: bool,
    pub dangerously_allow_all_unix_sockets: bool,
    pub allow_local_binding: bool,
    pub managed_allowed_domains_only: bool,
}

impl EnvironmentNetworkPolicy {
    /// 抓取可移植的流量限制，不暴露 controller 运行时设置。
    pub fn from_config(config: &NetworkProxyConfig, managed_allowed_domains_only: bool) -> Self {
        Self {
            domains: config.domains.clone(),
            unix_sockets: config.unix_sockets.clone(),
            allow_upstream_proxy: config.allow_upstream_proxy,
            dangerously_allow_all_unix_sockets: config.dangerously_allow_all_unix_sockets,
            allow_local_binding: config.allow_local_binding,
            managed_allowed_domains_only,
        }
    }

    /// 应用 attachment 拥有的流量设置，保留继承的 deny 与代理设置。
    pub fn apply_to(&self, config: &mut NetworkProxyConfig) {
        // 用 owner 的域名规则，但不丢 controller 的 deny。
        let inherited_denials = config.denied_domains().unwrap_or_default();
        config.domains.clone_from(&self.domains);
        for domain in inherited_denials {
            config.upsert_domain_permission(
                domain,
                NetworkDomainPermission::Deny,
                crate::normalize_host,
            );
        }
        let inherited_sockets = config.unix_sockets.take().unwrap_or_default();
        let mut effective_sockets = self.unix_sockets.clone().unwrap_or_default();

        // "允许全部" 不能覆盖任一策略 deny 的 socket。
        let inherited_permits_all = config.dangerously_allow_all_unix_sockets
            && !has_socket_deny(&inherited_sockets.entries);
        let owner_permits_all = self.dangerously_allow_all_unix_sockets
            && !has_socket_deny(&effective_sockets.entries);

        // 保留共享的 socket 授权；controller 的 deny 永远优先。
        effective_sockets.entries.retain(|entry| {
            matches!(entry.permission, NetworkUnixSocketPermission::Deny)
                || inherited_permits_all
                || socket_permission(&inherited_sockets.entries, &entry.path)
                    == Some(NetworkUnixSocketPermission::Allow)
        });
        for entry in inherited_sockets.entries {
            if owner_permits_all || matches!(entry.permission, NetworkUnixSocketPermission::Deny) {
                upsert_socket_entry(&mut effective_sockets.entries, entry);
            }
        }

        // 仅当 controller 与 owner 都允许时才开启对应权限。
        config.unix_sockets = (!effective_sockets.entries.is_empty()).then_some(effective_sockets);
        config.dangerously_allow_all_unix_sockets = inherited_permits_all && owner_permits_all;
        config.allow_upstream_proxy &= self.allow_upstream_proxy;
        config.allow_local_binding &= self.allow_local_binding;
    }
}

fn has_socket_deny(entries: &[NetworkUnixSocketPermissionEntry]) -> bool {
    entries
        .iter()
        .any(|entry| matches!(entry.permission, NetworkUnixSocketPermission::Deny))
}

/// 查询 path 的生效权限；同 path 多条目时 Deny 优先（与域名条目的冲突归并一致）。
fn socket_permission(
    entries: &[NetworkUnixSocketPermissionEntry],
    path: &str,
) -> Option<NetworkUnixSocketPermission> {
    let mut effective = None;
    for entry in entries.iter().filter(|entry| entry.path == path) {
        effective = Some(match (effective, entry.permission) {
            (Some(NetworkUnixSocketPermission::Deny), _) => NetworkUnixSocketPermission::Deny,
            (_, permission) => permission,
        });
    }
    effective
}

fn upsert_socket_entry(
    entries: &mut Vec<NetworkUnixSocketPermissionEntry>,
    entry: NetworkUnixSocketPermissionEntry,
) {
    entries.retain(|existing| existing.path != entry.path);
    entries.push(entry);
}
