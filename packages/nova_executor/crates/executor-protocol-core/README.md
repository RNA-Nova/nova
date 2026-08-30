# nova-executor-protocol-core

执行语义类型库（零运行时行为的契约层）：沙箱策略（`SandboxPolicy`/
`FileSystemSandboxPolicy`）、权限档（`PermissionProfile` 族）、shell 环境策略、
执行输出与错误分类。被 sandboxing / server / 协议 wire 层共同依赖，
单点定义保证线上契约不漂移。

派生自 OpenAI Codex 的 `codex-protocol`（Apache-2.0）；agent 会话面类型
（thread/auth/mcp/items 等）已按 nova 纯度边界移出至 nova-agent-rs 存档。
