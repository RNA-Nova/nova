# nova-harness-server

Nova Harness 的 JSON-RPC 服务器宿主（独立发行包）。

- `nova_harness_server.protocol/` —— JSON-RPC 协议层（methods / shapes / router / schema 导出）
- `nova_harness_server.transport/` —— stdio / WebSocket / memory 传输
- `nova_harness_server.reduction/` —— 事件 → wire 条目归约
- `nova_harness_server.rpc/` —— `nova-harness-rpc` CLI 装配入口

依赖 `nova-harness`（运行时 SDK）与 `nova-ai`，按仓库约定由根 `pyproject.toml`
的 pixi workspace editable 安装，不在此声明兄弟包 path 依赖。
