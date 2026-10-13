# nova-exec-server-http-client

executor 各 crate 共享的底层 HTTP 传输：路由感知客户端池（按 ClientRouteClass
分池）、出站代理策略（系统代理尊重/直连）、自定义 CA、TLS 探针。

派生自 OpenAI Codex 的 `codex-http-client`（Apache-2.0）；ChatGPT 后端亲和
（Cloudflare cookie）部分按 nova 纯度边界移除。

## 追新批（对位 codex 39f4277087，审计 124-F1/F2）

网络策略面换代：`network_policy.rs`（NetworkPolicyController/NetworkPolicy/
NetworkPermit/DestinationPolicy 可撤销原语）、`HttpClientBackend::{Direct,Routed}`
分发 + `TransportClient` + `apply_default_headers`、`request_draft.rs` /
`request_builder.rs` / `response.rs` 请求构造拆分、`client_tls.rs`
（HttpClientTlsConfig）、client_builder 的 http2_prior_knowledge / redirect_observed /
system_proxy_fallback / legacy custom-CA fallback、route_aware_client_pool 的
`execution.rs` 拆分 + CustomCaFallback + client_build 构建信号量 + `failure_class()`。
对位提交族：973ec2942c / 3c6f32ca82 / 6ea62c4396 / 888e02db34 / 22a3f6d5d8 /
db3cb33eca / 3bb0a530d1 / 421082e79d。裁点不变：ChatGPT cookie 亲和不镜像。
