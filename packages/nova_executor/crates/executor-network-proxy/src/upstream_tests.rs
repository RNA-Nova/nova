//! 移植自 codex network-proxy `upstream_tests.rs`。
//! 裁点：`mitm_upstream_client_trusts_startup_custom_ca` 依赖 certs.rs 的自定义 CA
//! 根存储（MITM 面），第一期未移植，随之裁掉。

use super::*;
use pretty_assertions::assert_eq;
use rama_net::address::Host;

#[test]
fn inherited_upstream_proxy_is_bypassed_for_non_public_targets() {
    let proxy = ProxyAddress::try_from("http://127.0.0.1:43128").unwrap();
    let config = ProxyConfig {
        http: Some(proxy.clone()),
        https: Some(proxy),
        all: None,
    };

    for target in [
        HostWithPort::new(Host::LOCALHOST_NAME, 8080),
        HostWithPort::new(Host::LOCALHOST_IPV4, 8080),
        HostWithPort::new(Host::Address("10.0.0.1".parse().unwrap()), 8080),
    ] {
        assert_eq!(config.proxy_for_target(&target, /*is_secure*/ false), None);
        assert_eq!(config.proxy_for_target(&target, /*is_secure*/ true), None);
    }

    let public = HostWithPort::new(Host::EXAMPLE_NAME, 443);
    assert!(
        config
            .proxy_for_target(&public, /*is_secure*/ true)
            .is_some()
    );
}
