# nova_ai

`nova_ai` 是 Nova monorepo 的 LLM 统一抽象层。

## 职责

- **核心类型**：`UserMessage`、`AssistantMessage`、`ToolResultMessage`、`ToolCall`、`Usage`、`Cost` 等。
- **流式接口**：将不同厂商的 API 统一为 `stream` / `stream_simple` / `complete` / `complete_simple`。
- **模型注册表**：动态注册 API 提供商与模型；内置 Moonshot（国际/国内）、Kimi Coding、Volcengine 四个 provider，均走 OpenAI Completions 协议。
- **鉴权辅助**：鉴权解析链（调用方 override → 已存储 credential → 环境变量 → OAuth），内置 Kimi device code 与 OpenAI Codex OAuth 登录流程。
- **兼容层**：OpenAI Completions 单协议实现（`api_impls/openai_completions.py`，当前唯一完整实现），按 provider/base_url 自动检测兼容标志，覆盖 DeepSeek、OpenRouter、Together、Moonshot 等第三方服务。
- **取消信号**：`AbortSignal` 触发后主动关闭底层 HTTP 流，已产生内容正常收尾。
- **请求钩子**：`on_payload` / `on_response` 回调分别暴露请求参数和原始 HTTP 响应元数据。

## 安装

```bash
# 推荐：仓库根目录 pixi 统一环境（一把装全部子包）
pixi install --environment dev

# 兼容：子包独立 Poetry
cd packages/nova_ai
poetry install
```

## 运行测试

```bash
# 推荐：pixi 任务（仓库根目录）
pixi run -e dev test-ai

# 或在子包内直接跑
pytest -m "not integration"

# 真实 API 集成测试（tests/integration/，需 VOLCENGINE_API_KEY / KIMI_API_KEY 等）
pytest tests/integration
```

## 示例

详见 [`examples/`](./examples) 目录下的 Python 脚本（默认离线运行，真实 API 未配置 key 时跳过）：

- `01_quickstart.py` — 最小用法：mock 协议模块 + `builtin_models()` 真实调用
- `02_stream_events.py` — 流式事件类型详解与消费顺序
- `03_models_and_providers.py` — Models 注册表、自定义 provider、动态模型目录
- `04_auth.py` — Auth 解析链：环境变量、`options.api_key` 覆盖、动态 key 注入

## 主要依赖

- `openai >= 2.0`
- `pydantic >= 2.0`
- `json-repair >= 0.58.4`
- `httpx >= 0.27.0`
