# nova-exec-server-network-proxy

本地网络策略执行代理：托管 HTTP/SOCKS5 代理、按策略放行/拦截/询问、
审计事件（`nova.network_proxy.policy_decision`）。由 `process/start` 的
`enforceManagedNetwork`/`managedNetwork` 参数启用。

移植自 OpenAI Codex 的 `codex-network-proxy`（Apache-2.0）。

## 追新批（对位 codex 39f4277087，审计 124-F4）

- `connection_lifecycle/`（ProxyListeners + CancelOnShutdown）：监听器优雅关闭
  （drain）取代 JoinHandle::abort。
- `system_dns.rs`：macOS 原生系统 DNS 解析器（补充解析器/VPN split DNS）。
- `request_cancellation.rs` 词汇 + `process_log_metadata.rs`；审计事件补
  launch.trace_id/span_id、tool.call_id、executor.{environment_id,registration_id}
  （conversation.id 支持 thread_id 覆盖）；codex 产品面八字段刻意收窄。
- upstream：`proxy_private_ips_via_upstream` 旋钮 + HTTP/1.1 默认版本降版恢复。
- `LocalBindingPolicy`（DefaultFalse/RequireTrue）+ `allow_local_binding` 线上形状
  bool→Option<bool> + `remote_launch_config(policy)` 参数与 MXC 兜底 ensure。
- `socket_path.rs` 的 executor_os 维度不镜像（裁点注释见 config.rs）；
  `brokered_tunnel.rs` / `main.rs` 不镜像（理由见 lib.rs 裁点）。

## 二轮复核修复批（对位 codex 39f4277087，审计 124-B）

- `dangerously_allow_all_unix_sockets` 线上形状 bool → `Option<bool>`（serde
  default + skip_serializing_if），`apply_to` 三态归一公式恢复（controller 省略
  socket 面时 attachment 的 Allow 授予不再被静默删）；Vec 承载的重复 path 冲突
  按 deny 优先归并（锚点 BTreeMap 为 serde 后写覆盖，nova 更保守）。
- unix socket 白名单 NUL 拒绝恢复（`ValidatedUnixSocketPath::parse`）+ 锚点矩阵
  的 native 语义对位用例随迁。
- `proxy/managed_routing_tests.rs`（e2e 两代理策略隔离）与
  `wait_fails_fast_when_either_listener_fails`（监听器失败快速传播）随迁。
