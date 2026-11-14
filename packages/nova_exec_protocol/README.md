# nova_exec_protocol

executor 的版本化线上协议包，只承载 JSON-RPC wire DTO：协议版本、方法常量、请求/响应/通知与线上权限形状。Shell 分析、策略求值、权限编译、连接与传输均不在本包。
