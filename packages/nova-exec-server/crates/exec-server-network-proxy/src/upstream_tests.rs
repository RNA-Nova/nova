//! 移植自 codex network-proxy `upstream_tests.rs`。
//! 裁点：`mitm_upstream_client_trusts_startup_custom_ca` 依赖 certs.rs 的自定义 CA
//! 根存储（MITM 面），第一期未移植，随之裁掉。

use super::*;
use pretty_assertions::assert_eq;
use rama_net::address::Host;

// 对位 codex 39f4277087：私网地址经上行代理是显式 opt-in（VPN 场景），
// loopback/链路本地/组播等特用地址任何情况下都直连。
#[test]
fn private_ip_upstream_routing_is_opt_in() {
    let proxy = ProxyAddress::try_from("http://127.0.0.1:43128").unwrap();

    for enabled in [false, true] {
        let config = ProxyConfig {
            http: Some(proxy.clone()),
            https: Some(proxy.clone()),
            proxy_private_ips_via_upstream: enabled,
            ..ProxyConfig::default()
        };
        for (host, use_proxy) in [
            ("10.0.0.1", enabled),
            ("172.16.0.1", enabled),
            ("192.168.0.1", enabled),
            ("100.68.58.50", enabled),
            ("fd00::1", enabled),
            ("::ffff:100.68.58.50", enabled),
            ("localhost", false),
            ("127.0.0.1", false),
            ("::1", false),
            ("169.254.169.254", false),
            ("fe80::1", false),
            ("0.0.0.0", false),
            ("224.0.0.1", false),
            ("192.0.2.1", false),
            ("240.0.0.1", false),
            ("example.com", true),
            ("100.63.255.255", true),
            ("100.128.0.0", true),
        ] {
            let target = HostWithPort::new(host.parse::<Host>().unwrap(), /*port*/ 443);
            for is_secure in [false, true] {
                assert_eq!(
                    config.proxy_for_target(&target, is_secure),
                    use_proxy.then(|| proxy.clone()),
                    "host={host}, enabled={enabled}, secure={is_secure}"
                );
            }
        }
    }
}
