//! 远程代理配置的行为适配（codex network-proxy `remote_config.rs` 的裁剪移植）。
//!
//! 线上类型 `RemoteNetworkProxyConfig` / `RemoteNetworkProxyLaunchConfig` 本体定义在
//! crate 根 lib.rs（serde 形状以 stub 为准）；本模块只补运行时所必需的
//! `into_network_proxy_config` 转换。

use crate::NetworkProxyConfig;

impl crate::RemoteNetworkProxyConfig {
    pub(crate) fn into_network_proxy_config(self) -> NetworkProxyConfig {
        NetworkProxyConfig {
            enabled: self.enabled,
            enable_socks5: self.enable_socks5,
            enable_socks5_udp: self.enable_socks5_udp,
            allow_upstream_proxy: self.allow_upstream_proxy,
            dangerously_allow_all_unix_sockets: self.dangerously_allow_all_unix_sockets,
            mode: self.mode,
            domains: self.domains,
            unix_sockets: self.unix_sockets,
            allow_local_binding: self.allow_local_binding,
            ..NetworkProxyConfig::default()
        }
    }
}

#[cfg(test)]
#[path = "remote_config_tests.rs"]
mod tests;
