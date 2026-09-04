# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [0.1.0] - 2026-09-04

首个封版。

### Added

- **三层架构**：`Models` 集合 + `Provider` 运行时单元 + `api_impls/` 协议实现，对外统一 `stream` / `complete` / `stream_simple` / `complete_simple` 异步 API。
- **内置四个 provider**：Volcengine（火山方舟）、Moonshot AI（国际/国内双端点）、Kimi Coding，均走 OpenAI Completions 协议；`create_provider()` 支持任意 OpenAI 兼容端点接入。
- **鉴权解析链**：调用方 `api_key` 覆盖 → 已存储 credential（OAuth 剩余有效期不足 5 分钟时在 store 锁内自动刷新）→ 环境变量；内置 Kimi device code 与 OpenAI Codex（浏览器 + device code）OAuth 登录流程；`CredentialStore` 持久化抽象（`read` / `list` / `modify` / `delete`）。
- **完整流式事件体系**：`text_*` / `thinking_*` / `toolcall_*` 增量事件，工具参数携带部分 JSON 增量解析快照（`json-repair` 节流解析）。
- **统一思考级别抽象**：`ThinkingLevel` 一套级别按厂商参数格式自动分派（`reasoning_effort`、`thinking`、`reasoning` 对象等），支持 `thinking_level_map` 级别映射与 `thinking_budgets` token 预算。
- **token 用量与成本统计**：输入/输出/缓存读写分项计数，按模型费率实时算出成本。
- **端点兼容层**：按 provider / base_url 自动检测兼容性（DeepSeek、Z.ai、Together、OpenRouter 等），`Model.compat`（`OpenAICompletionsCompat`）逐字段显式覆盖。
- **动态模型目录**：`create_provider(fetch_models=...)` + `Models.refresh()`（世代校验 + `ModelsStore` 持久化抽象）。
- **工程化细节**：`AbortController` 取消（主动关闭底层 HTTP 流）、`on_payload` / `on_response` 调试钩子、可被取消打断的请求层重试、错误一律编码进事件流终态而非抛出。
- **跨模型交接**：`transform_messages()` 跨厂商规范化（思考块保留/转文本、工具调用 id 规范化、孤立工具调用补全、图片降级）。
