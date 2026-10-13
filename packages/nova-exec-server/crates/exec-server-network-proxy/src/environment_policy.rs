//! 环境属主提供的流量限制（移植自 codex network-proxy `environment_policy.rs`）。
//!
//! 清单外说明：该文件不在首批移植清单，但被保留的 `proxy/execution_scope.rs`
//! （`for_execution`）与 `NetworkProxy::remote_launch_config` 引用，且自身无 MITM/凭证
//! 耦合，故随 proxy.rs 一并移植。
//!
//! 适配点：`unix_sockets` 线上形状为 `Vec<{path, permission}>`（codex 为
//! `#[serde(flatten)] BTreeMap`——serde 对重复键逐键 insert，后写覆盖即"后写赢"）。
//! nova 以 Vec 保序承载：写入面 `upsert_socket_entry` 按 path 去重（后写赢，与锚点
//! 一致）；读取面 `socket_permission` 对同 path 多条目按 deny 优先归并——仅线上数组
//! 能构造出这种畸形输入，deny 优先比锚点的后写赢更保守。合并语义其余各项与锚点
//! 一致：deny 永远优先；controller 省略 socket 面（三态 None 且无清单）时未设上限。

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
    /// Omission inherits local-binding policy; an explicit false remains a restriction.
    /// 对位 codex 39f4277087：bool → Option<bool>（省略 = 交给 executor 侧
    /// LocalBindingPolicy 归一；显式 false 仍是限制）。
    pub allow_local_binding: Option<bool>,
    pub managed_allowed_domains_only: bool,
}

impl EnvironmentNetworkPolicy {
    /// 抓取可移植的流量限制，不暴露 controller 运行时设置。
    pub fn from_config(config: &NetworkProxyConfig, managed_allowed_domains_only: bool) -> Self {
        Self {
            domains: config.domains.clone(),
            unix_sockets: config.unix_sockets.clone(),
            allow_upstream_proxy: config.allow_upstream_proxy,
            dangerously_allow_all_unix_sockets: config
                .dangerously_allow_all_unix_sockets
                .unwrap_or(false),
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
        // 对位锚点 apply_to 的三态归一（124 复核 B-2 修复）：controller 省略
        // 该旋钮（None）且未配 socket 清单时视为未设上限；显式 false 仍是天花板。
        // 必须在 take() 之前读取 config.unix_sockets 的占位状态。
        let inherited_allow_all = config
            .dangerously_allow_all_unix_sockets
            .unwrap_or(config.unix_sockets.is_none());
        let inherited_sockets = config.unix_sockets.take().unwrap_or_default();
        let mut effective_sockets = self.unix_sockets.clone().unwrap_or_default();

        // "允许全部" 不能覆盖任一策略 deny 的 socket。
        let inherited_permits_all =
            inherited_allow_all && !has_socket_deny(&inherited_sockets.entries);
        let owner_permits_all =
            self.dangerously_allow_all_unix_sockets && !has_socket_deny(&effective_sockets.entries);

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
        config.dangerously_allow_all_unix_sockets =
            Some(inherited_permits_all && owner_permits_all);
        config.allow_upstream_proxy &= self.allow_upstream_proxy;
        // 对位 codex 39f4277087：任一显式否认胜出；双方都省略则保持省略，
        // 交给 executor 的 LocalBindingPolicy 归一。
        config.allow_local_binding = match (config.allow_local_binding, self.allow_local_binding) {
            (Some(controller), Some(owner)) => Some(controller && owner),
            (controller, owner) => controller.or(owner),
        };
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

#[cfg(test)]
mod tests {
    use super::*;

    fn socket_list(
        entries: &[(&str, NetworkUnixSocketPermission)],
    ) -> Option<NetworkUnixSocketPermissions> {
        Some(NetworkUnixSocketPermissions {
            entries: entries
                .iter()
                .map(|(path, permission)| NetworkUnixSocketPermissionEntry {
                    path: (*path).to_string(),
                    permission: *permission,
                })
                .collect(),
        })
    }

    // 锚点 EnvironmentNetworkPolicy 无 Default derive，测试构造全字段字面量。
    fn owner_policy(
        unix_sockets: Option<NetworkUnixSocketPermissions>,
        dangerously_allow_all_unix_sockets: bool,
    ) -> EnvironmentNetworkPolicy {
        EnvironmentNetworkPolicy {
            domains: None,
            unix_sockets,
            allow_upstream_proxy: false,
            dangerously_allow_all_unix_sockets,
            allow_local_binding: None,
            managed_allowed_domains_only: false,
        }
    }

    // 124 复核 B-2 的回归钉：锚点 `inherited_allow_all =
    // dangerously_allow_all_unix_sockets.unwrap_or(config.unix_sockets.is_none())`
    // ——controller 省略 socket 面时 attachment 授予的 Allow 条目必须存活。
    // （锚点镜像面无该语义的直接单测，本测试为 nova 侧补位。）
    #[test]
    fn apply_to_preserves_owner_socket_grants_when_controller_omits_socket_policy() {
        let mut config = NetworkProxyConfig::default();
        assert_eq!(config.dangerously_allow_all_unix_sockets, None);
        assert!(config.unix_sockets.is_none());
        let owner = owner_policy(
            socket_list(&[("/tmp/owner.sock", NetworkUnixSocketPermission::Allow)]),
            false,
        );

        owner.apply_to(&mut config);

        assert_eq!(
            config.allow_unix_sockets(),
            vec!["/tmp/owner.sock".to_string()]
        );
        assert_eq!(config.dangerously_allow_all_unix_sockets, Some(false));
    }

    // 对照组：controller 显式 false（天花板）时 owner 的 Allow 条目仍被删——
    // 三态里 None 与 Some(false) 必须可区分。
    #[test]
    fn apply_to_drops_owner_socket_grants_when_controller_explicitly_disallows() {
        let mut config = NetworkProxyConfig {
            dangerously_allow_all_unix_sockets: Some(false),
            ..NetworkProxyConfig::default()
        };
        let owner = owner_policy(
            socket_list(&[("/tmp/owner.sock", NetworkUnixSocketPermission::Allow)]),
            false,
        );

        owner.apply_to(&mut config);

        assert!(config.allow_unix_sockets().is_empty());
        assert_eq!(config.dangerously_allow_all_unix_sockets, Some(false));
    }

    // deny 永远优先：controller 的 deny 在 owner permit-all 下也存活。
    #[test]
    fn apply_to_preserves_controller_socket_denials_under_owner_permit_all() {
        let mut config = NetworkProxyConfig {
            unix_sockets: socket_list(&[("/tmp/denied.sock", NetworkUnixSocketPermission::Deny)]),
            ..NetworkProxyConfig::default()
        };
        let owner = owner_policy(None, true);

        owner.apply_to(&mut config);

        assert!(config.allow_unix_sockets().is_empty());
        assert_eq!(config.dangerously_allow_all_unix_sockets, Some(false));
        let entries = &config.unix_sockets.as_ref().expect("socket list").entries;
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].path, "/tmp/denied.sock");
        assert_eq!(entries[0].permission, NetworkUnixSocketPermission::Deny);
    }

    // Vec 线上形状的重复 path 冲突：读取面 deny 优先（锚点 BTreeMap 为后写覆盖，
    // 该输入只在 nova 的数组形状下可达）。
    #[test]
    fn allow_unix_sockets_dedupes_conflicting_duplicate_paths_deny_first() {
        let config = NetworkProxyConfig {
            unix_sockets: socket_list(&[
                ("/tmp/dup.sock", NetworkUnixSocketPermission::Allow),
                ("/tmp/dup.sock", NetworkUnixSocketPermission::Deny),
                ("/tmp/plain.sock", NetworkUnixSocketPermission::Allow),
            ]),
            ..NetworkProxyConfig::default()
        };

        assert_eq!(
            config.allow_unix_sockets(),
            vec!["/tmp/plain.sock".to_string()]
        );
    }
}
