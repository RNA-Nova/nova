# nova_ai

`nova_ai` 是 Nova  monorepo 的 LLM 统一抽象层。

## 职责

- **核心类型**：`UserMessage`、`AssistantMessage`、`ToolResultMessage`、`ToolCall`、`Usage`、`Cost` 等。
- **流式接口**：将不同厂商的 API 统一为 `stream` / `stream_simple` / `complete` / `complete_simple`。
- **模型注册表**：动态注册 API 提供商与模型，内置 OpenAI、Anthropic、Google、Volcengine 支持。
- **鉴权辅助**：AWS Bedrock、Google Vertex 等云厂商凭证检测。
- **兼容层**：OpenAI Completions / Responses 兼容路由。

## 安装

```bash
cd packages/nova_ai
poetry install
```

## 主要依赖

- `openai >= 1.0.0`
- `mashumaro >= 3.0`
- `json-repair >= 1.0`
