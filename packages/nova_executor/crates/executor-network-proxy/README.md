# nova-executor-network-proxy

本地网络策略执行代理：托管 HTTP/SOCKS5 代理、按策略放行/拦截/询问、
审计事件（`nova.network_proxy.policy_decision`）。由 `process/start` 的
`enforceManagedNetwork`/`managedNetwork` 参数启用。

移植自 OpenAI Codex 的 `codex-network-proxy`（Apache-2.0）。
