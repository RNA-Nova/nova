# nova-server

Nova 的 JSON-RPC 协议服务层（独立发行包）——内核与前端之间的边界层主体。

- `nova_server/protocol/` —— 协议层：`jsonrpc.py`（信封）、`errors.py`（错误码）、
  `router.py`（MethodRegistry——签名即契约，含 `deprecated` 弃用标记）、
  `serialize.py`（事件哑管道）、`schema_export.py`（契约构建期导出：
  `nova-wire.schema.json` + `gen.ts`，模型变体联合发射 discriminator）、
  `methods/`（9 个域的方法表 + `shapes.py` 形状声明）
- `nova_server/transport/` —— stdio / WebSocket / memory 传输
- `nova_server/connection.py` —— 连接一等公民（状态机、出站队列、写泵、背压分流）
- `nova_server/server.py` —— `RpcServer`（读泵/分派/deprecationNotice 发送/
  initialize 握手门）
- `nova_server/ui_context.py` —— server→client 反向原语（弹窗/选择/输入/
  set_status；词汇定义权归包）
- `nova_server/reduction/` —— 事件流 → `WireItem` 三帧（`item/started|delta|
  completed`）的服务器侧归约
- `nova_server/types/` —— `items.py`（`WireItem` 开放集 + `CustomItem` 兜底）、
  `notifications.py`
- `nova_server/client/` —— client 家族（`base` / `in_process` / `remote`）
- `nova_server/exec/` —— headless 执行（协议客户端形态）

CLI：`nova-server`（`nova_server.rpc.cli:main`）。测试见 `tests/`（含
`tests/conformance/`——黑盒线上契约一致性 + `test_wire_lint.py` 生成物 lint）。

依赖 `nova-harness`（运行时 SDK）、`nova-ai` 与 `nova_protocol`，按仓库约定由根
`pyproject.toml` 的 pixi workspace editable 安装，不在此声明兄弟包 path 依赖。
