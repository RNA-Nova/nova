"""套餐网络编译落点——codex `NetworkProxyConfig` 族的纯词汇移植。

对位 codex 锚点 39f4277087 `network-proxy/src/config.rs:130-175`
（`NetworkProxyConfig`）与 `mitm_hook.rs:28-64`（`MitmHookConfig` 族）。

本模块是**配置侧**词汇（snake_case、不经 dump_wire）：套餐 `network` 段的
编译产物（`NetworkToml.to_network_proxy_config()` 对位件）落在
`ProfileNetworkProxyConfig`；上线投影（→ 线上 `RemoteNetworkProxyConfig`）
归后续接线批次（nova 线上枚举暂无 Limited 模式与 MITM 面——见
exec-server-network-proxy NetworkMode 注释）。

字段裁剪登记：codex `NetworkProxyConfig` 的托管凭据经纪面
（`mitm_ca`/`credential_broker*`/`credential_providers`/
`credential_broker_openai_*`/`dangerously_allow_plaintext_credential_injection`）
属 codex 托管登录面，套餐编译路径不触碰（`apply_to_network_proxy_config`
不写这些字段），本批次不收。
"""

from __future__ import annotations

import ipaddress
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..exec_server_wire import NetworkDomainPermission


class PermissionNetworkMode(str, Enum):
    """对位 codex-network-proxy `NetworkMode`（config.rs:384-395，serde
    lowercase）——注意与线上 `NetworkMode`（none/proxy）是两族词汇：

    - `Limited`：只读 HTTP 方法 + 非 MITM 下禁 CONNECT（nova 线上枚举未收，
      对位 exec-server-network-proxy lib.rs 的裁切注释）；
    - `Full`：全方法放行（对位线上 `proxy`）。
    """

    LIMITED = "limited"
    FULL = "full"


def normalize_host(host: str) -> str:
    """域名/主机归一（逐行对位 network-proxy/src/domain_matcher.rs:90-109
    `normalize_host`：trim、去端口/方括号、小写、去尾点、IP 字面量归一）"""
    host = host.strip()
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return _normalize_dns_host_or_ip_literal(host[1:end])
    # 防御性去 `:port`（恰好一个冒号时；多冒号视为未加括号 IPv6）
    if host.count(":") == 1:
        return _normalize_dns_host_or_ip_literal(host.split(":")[0])
    return _normalize_dns_host_or_ip_literal(host)


def _normalize_dns_host_or_ip_literal(host: str) -> str:
    """对位 domain_matcher.rs:111-117"""
    host = host.lower().rstrip(".")
    return _normalize_ip_literal(host) or host


def _normalize_ip_literal(host: str) -> str | None:
    """对位 domain_matcher.rs:124-137（IP 原样保留；scope 分隔符 %25/% 归一
    为 %）"""
    if _is_ip_literal(host):
        return host
    for delimiter in ("%25", "%"):
        if delimiter in host:
            ip, _, scope = host.partition(delimiter)
            if _is_ip_literal(ip):
                return f"{ip}%{scope}"
    return None


def _is_ip_literal(host: str) -> bool:
    """对位 Rust `IpAddr::parse`"""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class ProxyNetworkDomainPermissionEntry(BaseModel):
    """对位 codex-network-proxy `NetworkDomainPermissionEntry`
    （config.rs:32-35）——字段名 `pattern`（勿与线上 wire 的 `domain`
    字段混用）"""

    pattern: str
    permission: NetworkDomainPermission


class ProxyNetworkDomainPermissions(BaseModel):
    """对位 codex-network-proxy `NetworkDomainPermissions`（config.rs:37-40）
    ——**有序 Vec** 容器（upsert 语义见
    `ProfileNetworkProxyConfig.upsert_domain_permission`）"""

    entries: list[ProxyNetworkDomainPermissionEntry] = Field(default_factory=list)


class MitmHookBodyConfig(BaseModel):
    """对位 `MitmHookBodyConfig`（mitm_hook.rs:62-64）——透明包装任意 JSON"""

    value: Any


class InjectedHeaderConfig(BaseModel):
    """对位 `InjectedHeaderConfig`（mitm_hook.rs:53-60）"""

    name: str = ""
    secret_env_var: str | None = None
    secret_file: str | None = None
    prefix: str | None = None


class MitmHookActionsConfig(BaseModel):
    """对位 `MitmHookActionsConfig`（mitm_hook.rs:46-51）"""

    strip_request_headers: list[str] = Field(default_factory=list)
    inject_request_headers: list[InjectedHeaderConfig] = Field(default_factory=list)


class MitmHookMatchConfig(BaseModel):
    """对位 `MitmHookMatchConfig`（mitm_hook.rs:38-44）"""

    methods: list[str] = Field(default_factory=list)
    path_prefixes: list[str] = Field(default_factory=list)
    query: dict[str, list[str]] = Field(default_factory=dict)
    headers: dict[str, list[str]] = Field(default_factory=dict)
    body: MitmHookBodyConfig | None = None


class MitmHookConfig(BaseModel):
    """对位 `MitmHookConfig`（mitm_hook.rs:28-32——host + matcher（线上键
    `match`）+ actions）"""

    host: str = ""
    matcher: MitmHookMatchConfig = Field(default_factory=MitmHookMatchConfig)
    actions: MitmHookActionsConfig = Field(default_factory=MitmHookActionsConfig)


class ProfileNetworkProxyConfig(BaseModel):
    """对位 codex-network-proxy `NetworkProxyConfig`（config.rs:130-175 +
    `Default` 实现 177-203）——套餐 network 编译产物形状。

    缺省值逐字段对位 codex Default：proxy_url/socks_url 回环占位、socks5
    与 udp 默认开、上游代理默认允许、unix socket/本地绑定 Option 三态。
    """

    enabled: bool = False
    proxy_url: str = "http://127.0.0.1:3128"
    enable_socks5: bool = True
    socks_url: str = "http://127.0.0.1:8081"
    enable_socks5_udp: bool = True
    allow_upstream_proxy: bool = True
    dangerously_allow_non_loopback_proxy: bool = False
    #: Option 三态（None = 委托挂接策略，执行默认 false——对位 config.rs:142-144）
    dangerously_allow_all_unix_sockets: bool | None = None
    mode: PermissionNetworkMode = PermissionNetworkMode.FULL
    domains: ProxyNetworkDomainPermissions | None = None
    unix_sockets: dict[str, NetworkDomainPermission] | None = None
    #: Option 三态（None = 按所选沙箱解析——对位 config.rs:151-153）
    allow_local_binding: bool | None = None
    mitm: bool = False
    mitm_hooks: list[MitmHookConfig] = Field(default_factory=list)

    def upsert_domain_permission(
        self, host: str, permission: NetworkDomainPermission
    ) -> None:
        """按归一主机名去重后追加（逐行对位 config.rs:317-333——保留原始
        拼写入条目，归一只用于判重）"""
        normalized_host = normalize_host(host)
        domains = self.domains or ProxyNetworkDomainPermissions()
        domains.entries = [
            entry
            for entry in domains.entries
            if normalize_host(entry.pattern) != normalized_host
        ]
        domains.entries.append(
            ProxyNetworkDomainPermissionEntry(pattern=host, permission=permission)
        )
        self.domains = domains if domains.entries else None
