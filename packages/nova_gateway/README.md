# nova-gateway

Nova 原生模型服务与公共目录站。服务端组合 `nova_ai` 的长期 `ModelsManager`、下游鉴权、SSE 生命周期与 `nova_catalog` 的公共构建结果，提供 Nova 原生聊天协议、模型列表、公共目录和 OpenAI Chat Completions 兼容适配。

本包不负责 Agent 会话、工具执行、本地推理部署、多租户计费、请求 fallback 或复杂路由。当前推理面只开放聊天模型；图像与分类模型只出现在公共目录中。

## 配置

保存为 `gateway.yaml`：

```yaml
host: 127.0.0.1
port: 8000
upstreams:
  - provider: volcengine
    api_key_env: VOLCENGINE_API_KEY
  - provider: kimi-coding
    api_key_env: KIMI_API_KEY
allow_models:
  - volcengine/<model-id>  # 替换为已登记且账户可用的模型 ID
  - kimi-coding/kimi-for-coding
catalog_dir: ./public-catalog
allow_origins: []
```

- `upstreams[].provider` 使用 `nova_ai` 的内置 Provider ID；`api_key_env` 只指定环境变量名，YAML 不保存上游密钥。未写 `api_key_env` 时使用该 Provider 的默认鉴权链。
- 自定义 OpenAI 兼容上游可在条目中提供 `base_url` 和完整 `models` 定义；下游仍只能选择这里登记的模型。
- `allow_models: []` 表示允许全部已注册聊天模型；非空时只允许列出的 `provider/id`。
- `allow_origins` 是浏览器 CORS 来源白名单，默认不开放跨源访问。

启动前设置真正的上游密钥：

```bash
export VOLCENGINE_API_KEY='...'
export KIMI_API_KEY='...'
pixi run -e dev nova-gateway serve --config gateway.yaml
```

Gateway Key 与上游 Provider Key 是两套凭据。CLI 按“`--token` / 配置 `api_token` / `NOVA_GATEWAY_TOKEN` → `--token-file` → 默认文件”供给 Gateway Key；默认文件为 `~/.nova/agent/gateway.json`，不存在时生成随机 token 并以 `0600` 保存。公网监听同样必须有 Gateway Key，不能匿名开放推理端点。

## HTTP 接口

| 接口 | 鉴权 | 用途 |
|---|---|---|
| `GET /healthz` | 公开 | 存活检查 |
| `GET /api/models` | Bearer | 当前允许调用的聊天模型 |
| `POST /api/stream` | Bearer | Nova 原生 SSE 聊天流 |
| `GET /api/models/providers` | 公开 | 公共目录 Provider 列表 |
| `GET /api/models/providers/{provider}` | 公开 | 公共目录与 ETag/304 |
| `GET /v1/models` | Bearer | OpenAI 兼容模型列表 |
| `POST /v1/chat/completions` | Bearer | OpenAI 兼容聊天，支持流式与非流式 |

原生请求示例：

```bash
curl -N http://127.0.0.1:8000/api/stream \
  -H 'Authorization: Bearer <gateway-token>' \
  -H 'x-nova-gateway-protocol: 1.2' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": {"provider": "kimi-coding", "id": "kimi-for-coding"},
    "context": {
      "messages": [{"role": "user", "content": "你好", "timestamp": 0}]
    },
    "options": {}
  }'
```

下游不能提交 `base_url`、上游 API Key、上游 headers 或环境变量名。SSE 事件、请求选项白名单与错误形状由 `nova_gateway_protocol` 定义。

OpenAI 官方 SDK 可直接使用兼容入口：

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="<gateway-token>",
)
response = await client.chat.completions.create(
    model="kimi-coding/kimi-for-coding",
    messages=[{"role": "user", "content": "你好"}],
)
```

兼容层支持常用消息、工具调用、`tool_choice`、流式 chunk 与 usage；不支持的字段会明确返回 400，不会静默忽略。流式 usage 按 OpenAI 语义仅在 `stream_options.include_usage=true` 时随末块发送。

## 公共目录

公共目录由构建产物发布，不能直接暴露 `ModelsManager` 的账户个性化目录：

```bash
pixi run -e dev nova-gateway generate-catalog \
  --config gateway.yaml \
  --output-dir ./public-catalog \
  --pretty
```

`--output-dir` 可省略，此时使用配置的 `catalog_dir`，再缺省为 `~/.nova/agent/gateway/catalog`。生成器调用 `nova_catalog.build_catalog()`，经 `nova_ai` 的同一模型验收后原子提交 `models.json`、`models.all.json`、Provider 分片和带 SHA-256 的 `catalog-manifest.json`。服务端只读 `catalog_dir`，支持匿名读取与按实际表示计算的 ETag/304。

## 日志

`nova-gateway serve` 通过 `nova_logging` 统一装配日志：默认向 stdout 写 JSONL，`--log-format console` 切换 stdout 的人类可读输出，`--log-level` 控制默认级别，`--log-target PREFIX=LEVEL` 按 logger 名最长前缀覆盖并可重复。`--log-file` 会**额外**增加一个 JSONL 大小轮转文件，不关闭 stdout；文件默认 10 MiB、保留 5 份，POSIX 权限强制 `0600`。队列默认容量 1000，`close()` 默认有 5 秒等待预算，超时非 daemon 后台线程仍继续排干，不清空日志；阻塞 sink 的强制退出由外层 watchdog 决定。

请求日志覆盖 `gateway.request.started/completed/failed`、`gateway.upstream.connected` 与 `gateway.stream.connected/completed/failed/cancelled/closed`。同一请求及上游流共享 `x-request-id`，记录 provider、模型、状态、错误分类和耗时，不记录鉴权头、消息正文、工具参数、原始响应或 SSE delta。Uvicorn access log 默认关闭，`httpx` / `httpx2` / `httpcore` / `openai` / `anthropic` 全部钳制在 WARNING 以上，即使用户把它们显式设为 DEBUG 也不会放行完整 URL、headers 或请求正文。

## 生命周期与范围

ASGI lifespan 关停时会取消并 join 全部在途请求；客户端断连也会结束对应上游流。当前部署形态是单进程、单事件循环，不包含 WebSocket 多路复用、多 worker 会话共享、动态热更新、本地模型部署、fallback、多租户或计费。
