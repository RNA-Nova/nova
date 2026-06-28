# nova_ai 开发日志

## 2026-06-15 取消机制对齐 TypeScript 原版

将 Python `openai_completions.py` 的取消行为从"等流自然结束后再标记 aborted"改为"abort 时主动切断底层 HTTP 流"，与 TS 版本一致。

- chunk 循环内检测 `signal.aborted`，触发时 `await openai_stream.close()` 并 break
- 已产生的 content block 仍通过 `finish_block()` 正常收尾，保证事件序列不混乱
- 请求发送前也前置检查 `signal.aborted`，避免无效请求
- 异常路径统一推送 `ErrorEvent(reason=StopReason.ABORTED)`

## 2026-06-15 OpenAI Completions 请求选项扩展

对齐 OpenAI Python SDK 2.29.0 和 TS 原版能力：

- `StreamOptions` 新增 `timeout`、`max_retries`、`metadata`、`on_response`
- `timeout` / `max_retries` 透传给 `AsyncOpenAI` 请求
- `metadata` 放到 SDK 顶层参数
- `on_response` 回调通过 `with_raw_response.create()` 拿到原始 HTTP 状态码和响应头，封装为 `ProviderResponse` 后回传
- `ProviderResponse` 类型从 `nova_ai.types` 和 `nova_ai` 顶层导出

## 2026-06-15 DeepSeek 真实模型集成测试扩展

`tests/test_integration_deepseek.py` 从 19 个增至 **40 个**测试用例，覆盖：

- 基础流式 / 非流式调用
- 注册表查询
- usage / cost 校验
- 事件序列与重复 end 检测
- off/low/medium/high reasoning 级别
- 工具调用、auto tool_choice、多轮 round-trip
- 多轮对话与 system prompt
- 取消信号（abort 后主动断流）
- temperature、max_tokens、headers、timeout、max_retries、on_response
- 并发请求
- 中文 / emoji / 长输入
- 无效 API key 错误处理
- 三个模型参数化：`deepseek-v4-flash-260425`、`deepseek-v4-pro-260425`、`deepseek-v3-2-251201`

当前测试状态：单元测试 158 passed；集成测试 40 passed。

## 2026-06-11 测试套件建设

为 `nova_ai` 包编写 comprehensive pytest 测试 suite，覆盖全部核心模块。

- 新增 9 个测试文件、159 个测试用例：
  - `test_types.py` — 枚举、内容块、消息、事件、兼容性配置
  - `test_utils.py` — 成本计算、思考级别、溢出检测、消息转换
  - `test_providers.py` — `build_params`、`convert_messages`、`detect_compat`
  - `test_registry.py` — API/模型注册表操作
  - `test_streaming.py` — 流式事件类型校验
  - `test_models.py` — Volcengine 模型数据校验
  - `test_compat.py` — 兼容性配置模型
  - `test_env.py` — 环境变量 API 密钥获取
  - `test_imports.py` — 根包公共 API 可导入性（importlib 动态验证）
- 全库通过 pyflakes 零冗余导入检查
- 清理 `src/nova_ai/registry/builtins.py` 中三个死导入（`openai.py`/`anthropic.py`/`google.py` 模型文件不存在）

## 2026-06-10 Volcengine DeepSeek V3.2 / V4 支持

- `models/volcengine.py` 新增 3 个模型：
  - `deepseek-v3-2-251201`
  - `deepseek-v4-flash-260425`
  - `deepseek-v4-pro-260425`
- `detect_compat` 识别 `volcengine` + `deepseek` 前缀为 `thinking_format="deepseek"`
- 三个模型均通过真实 API 测试，正常返回 thinking + text/toolCall

## 2026-06-10 OpenAI Completions Provider 对齐 TypeScript 原版

修复多项与 TypeScript 原版行为不一致的问题：

- `ToolCall.partial_args` 缺失字段导致 `validate_assignment` 崩溃
- `output_tokens` 重复计数（`completion_tokens` 已含 `reasoning_tokens`）
- `map_stop_reason` 对未知 reason 抛 `ValueError` → 返回 `(StopReason.ERROR, msg)`
- `getattr(..., None)` 返回 `None` 导致算术 `TypeError`
- `get_compat` 遇到 `OpenAIResponsesCompat` 时 `AttributeError`
- assistant text 被包装为 list → 纯字符串
- `ToolCall.arguments` 接收 `List` 时崩溃
- 引入 `tool_call_blocks_by_index/by_id` Map 支持并行 tool calls
- 添加 `choice.usage` fallback（Moonshot 等 provider）
- 纯图片消息过滤后为空时插入占位
- 补充 `deepseek`/`openrouter`/`together`/`qwen`/`qwen-chat-template` thinking format
- 添加 `prompt_cache_key` / `prompt_cache_retention` 支持
- 添加 session affinity headers 和 `cloudflare-ai-gateway` 特殊鉴权
- 捕获 `chunk.id`/`chunk.model` 到 `response_id`/`response_model`
- `thinking_level_map` 映射逻辑补上（`_map_reasoning_effort`, `_get_off_value`）
- `parallel_tool_calls` 改为仅用户显式指定时才传（对齐 TS）
- `finish_block` 重复调用修复：流结束只 `finish_block(current_block)`
- `ensure_tool_call_block` 更新 `current_block`：并行 tool calls 时正确跟踪当前块

## 2026-06-10 Python 3.9 兼容

全库替换 `X | Y` 语法为 `Union`/`Optional`，移除 `dict[str, Any]` / `tuple[...]` 等 3.10+ 语法，确保 Python 3.9–3.12 兼容。

## 2026-06-10 目录重组与 Pydantic 迁移

- **Pydantic 迁移**：22 个 dataclass → `NovaBaseModel`
- **目录重组**：
  - `core/` → `types/`
  - `compat/` 合并到 `types/compat.py`
  - `models/base.py` 拆分
  - `events.py`, `registry.py` 从 `streaming/` / `registry/` 移入 `types/`
- **根 `__init__.py` 重构**：从 `.types` 统一导入，97 个公共导出全部可访问
- 全库 pyflakes clean，移除所有未使用导入
