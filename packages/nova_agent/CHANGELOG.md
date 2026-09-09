# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [0.1.0] - 2026-09-04

首个封版。

### Added

- **`Agent` 类**：状态容器（`AgentState`）、事件订阅（订阅者按注册顺序逐个 `await`，屏障语义）、消息队列与生命周期控制（`prompt` / `continue_` / `abort` / `wait_for_idle`）。
- **低层循环 API**：`agent_loop()` / `agent_loop_continue()` 返回观察式 `AgentEventStream`，`run_agent_loop*()` 直接推事件给 emit sink，便于自管状态。
- **工具系统**：`AgentTool`（pydantic 模型）+ JSON Schema 参数校验与类型矫正；并行/串行执行模式（公平读写门，sequential 工具不毒化整批）；`on_update` 执行进度回调。
- **四个拦截钩子**：`before_tool_call`（拦截/终止）、`after_tool_call`（结果改写）、`prepare_next_turn`（替换下一轮运行时）、`should_stop_after_turn`（优雅收尾）。
- **消息队列**：steering（运行中插队）与 follow-up（收尾后续跑）双队列，`one-at-a-time` / `all` 两种注入模式。
- **自定义消息类型**：`CustomAgentMessage` 扩展基类 + `transform_context` / `convert_to_llm` 上下文管线。
- **错误处理约定**：工具异常、校验失败、hook 拦截一律落账为 `is_error` 工具结果，provider 失败生成失败 assistant 消息，均不中断 run；`terminate` 提前终止提示。
- **取消传播**：`AbortSignal`（re-export 自 `nova_ai`）贯穿 provider 流与工具执行。
