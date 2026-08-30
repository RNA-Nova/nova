# nova-executor-http-client

executor 各 crate 共享的底层 HTTP 传输：路由感知客户端池（按 ClientRouteClass
分池）、出站代理策略（系统代理尊重/直连）、自定义 CA、TLS 探针。

派生自 OpenAI Codex 的 `codex-http-client`（Apache-2.0）；ChatGPT 后端亲和
（Cloudflare cookie）部分按 nova 纯度边界移除。
