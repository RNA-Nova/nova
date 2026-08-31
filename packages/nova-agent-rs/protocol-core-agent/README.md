# protocol-core agent 面（纯度批次 1 存档）

executor-protocol-core 原 src 全量快照（批次 1 切除时点）。nova-executor
只用其中的执行语义类型（permissions / 沙箱策略族 / 权限档 / exec_output /
shell_environment 洗刷 / config_types 沙箱枚举 / error 沙箱变体），其余为
codex agent 面类型（auth/mcp/approvals/items/thread 等），供将来 Rust 版
agent 内核参考。切除记录见 nova 主仓库批次 1 提交。
