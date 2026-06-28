# Changelog

## 0.1.0-alpha.x — 2026-06-15

### 新增

- `StreamOptions` 增加 `timeout`、`max_retries`、`metadata`、`on_response` 字段
- 新增 `ProviderResponse` 类型，从 `nova_ai` 顶层导出
- `on_response` 回调通过 `with_raw_response.create()` 暴露原始 HTTP 状态码和响应头

### 变更

- OpenAI Completions 取消机制对齐 TypeScript 原版：`AbortSignal` 触发后主动关闭底层 HTTP 流
- 取消后仍对已产生的 content block 调用 `finish_block()` 收尾，保证事件序列完整
- 请求发送前增加 `signal.aborted` 前置检查，避免无效请求

### 测试

- DeepSeek/Volcengine 集成测试从 19 个扩展至 40 个
- 当前测试状态：单元测试 158 passed；集成测试 40 passed
