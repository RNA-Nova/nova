# nova_ai

`nova_ai` 是 Nova monorepo 的 LLM 统一抽象层。

## 职责

- **核心类型**：`UserMessage`、`AssistantMessage`、`ToolResultMessage`、`ToolCall`、`Usage`、`Cost` 等。
- **流式接口**：将不同厂商的 API 统一为 `stream` / `stream_simple` / `complete` / `complete_simple`。
- **模型注册表**：动态注册 API 提供商与模型，内置 OpenAI、Anthropic、Google、Volcengine 支持。
- **鉴权辅助**：AWS Bedrock、Google Vertex 等云厂商凭证检测。
- **兼容层**：OpenAI Completions / Responses 兼容路由，覆盖 DeepSeek、OpenRouter、Together、Moonshot、Mistral 等第三方服务。
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

# 真实模型集成测试（需配置 VOLCENGINE_API_KEY）
pytest tests/test_integration_deepseek.py
```

## 示例

详见 [`examples/`](./examples) 目录下的 Jupyter Notebook：

- `01-quickstart.ipynb` — 最简流式对话
- `02-multi-provider.ipynb` — 多厂商模型切换
- `03-tools.ipynb` — Tool Calling 完整流程

## 主要依赖

- `openai >= 1.0.0`
- `mashumaro >= 3.0`
- `json-repair >= 1.0`
