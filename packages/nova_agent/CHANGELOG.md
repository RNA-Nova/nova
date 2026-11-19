# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [Unreleased]

### Added

- **转录与生命周期**：`SystemMessage` 承载系统提示词与工具差分；新增 `prepare_request` / `prepare_next_turn` / `finish_turn` 与 `run_tool_call`。
- **工具结果**：`structured_content`、`usage`、`duration_ms`、`output_schema` 与 `replay`；公开 `AgentToolCallOutcome`。
- **请求身份与可观测性**：`ClientIdentity`、provider 归因头、错误分类和敏感信息清洗。
- **统一运行句柄**：`AgentRun` 持有 `task`、`events`、`signal` 与 `abort()`；`prompt()` / `continue_()` 同步返回句柄，`run.events` 是单次 run 的权威事件流，`on_message_end` 承载显式消息干预。

### Fixed

- **运行收尾**：provider abandonment、并行工具异常排水与 run 代际清理；driver、工具及各运行作用域的取消后 join 均有界，迟到事件不能跨代污染。
- **启动与终止**：`prompt()` / `continue_()` 同步登记并返回 `AgentRun`；公开 Task 与 `wait_for_idle()` 共用单一 settle 边界，首步前取消也会发布权威事件流终态；abort 采用 100ms 优雅窗口，超时硬取消并告警。
- **并发与观测**：sequential 终态在门内、通知在门外；工具 update observer 异常转为工具错误结果。
- **参数与契约**：单次调用工具集、联合输入保留、optional null 归一及 `ToolCall.namespace` 往返。

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
- **取消传播**：`AbortSignal`（来自 `nova_protocol`）贯穿 provider 流与工具执行。
