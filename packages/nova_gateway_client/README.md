# nova-gateway-client

独立的 Nova Gateway 官方客户端 SDK。提供模型列表、公共目录、公开 SSE DTO 事件流、重试、响应元数据和完整的单次调用生命周期。

本包只依赖 `httpx`、`pydantic`、`nova_gateway_protocol` 和纯数据的 `nova_protocol`，**不依赖 `nova_ai` 或 `nova_async`**。标准 `AssistantMessageEventStream` 的重建由 `nova_ai/api_impls/nova_gateway` 负责。

## 公开协议调用

```python
from nova_gateway_client import NovaGatewayClient
from nova_gateway_protocol import ModelRef, StreamOptions, StreamRequest
from nova_protocol import Context, UserMessage

request = StreamRequest(
    model=ModelRef(provider="kimi-coding", id="kimi-for-coding"),
    context=Context(messages=[UserMessage(content="你好")]),
    options=StreamOptions(),
)

async with NovaGatewayClient(
    "http://127.0.0.1:8000",
    "<gateway-token>",
    timeout=30,
    max_retries=2,
    max_retry_delay_ms=60000,
) as client:
    models = await client.list_models()
    catalog = await client.catalog("kimi-coding", types=("chat",))

    # 同步工厂；首次消费时才建连与重试。
    stream = client.stream(request)
    async with stream:
        async for event in stream:
            # event 是 GatewayAssistantMessageEvent，不是 nova_ai 的运行时事件。
            print(event.type)
```

`catalog()` 默认拉取聊天、图像和分类公共目录；支持 `types=("chat",)` 与 ETag `validator`。公共目录接口不需要 Gateway Key，模型列表与流式推理需要。

下游只传 `provider/id`、上下文和白名单选项，不传上游地址或上游凭据。

## 重试与请求身份

- `max_retries` 默认 2，`max_retry_delay_ms` 默认 60000；`x-should-retry` 可覆盖状态码策略，408/409/429/5xx 与连接错误会重试。
- Retry-After 支持毫秒、秒和 HTTP 日期；其他情况使用指数退避 + jitter。`sleep_fn` 与 `jitter_fn` 可注入，便于确定性测试。
- 普通请求和 SSE **建连阶段**可重试；已开始消费的 SSE 事件绝不重放。
- 非 GET 请求自动携带 `Idempotency-Key`，所有请求携带 `x-request-id`；同一逻辑请求的重试复用这两个值。`Idempotency-Key` 目前用于重试关联，服务端不做去重。
- `StreamOptions.max_retries` 仍是**上游 Provider 的重试选项**，与 SDK 构造器的本地重试不是一回事。

## 响应与异常

`list_models_with_response()` 和 `catalog_with_response()` 返回 `GatewayResponse[T]`，包含解析结果、status、headers、request、request_id、elapsed 和 `retries_taken`。`GatewayEventStream` 额外公开 `response`、`request`、`status_code`、`request_id`、`retries_taken` 与 `closed`。

主要异常：

- `GatewayHttpError`：`status_code`、`response`、`body`、`kind`、`retry_after_ms`、`request_id`；400/401/403/404/409/422/429/5xx 有对应子类。
- `GatewayConnectionError` / `GatewayTimeoutError`：保留原始 `httpx.Request` 和 cause 链。
- `GatewayProtocolError`：JSON、判别联合或版本校验失败。
- `GatewayDisconnectedError`：终态前断连，AI 适配层映射为 `STREAM_DISCONNECTED`。

## 流生命周期与 HTTP 配置

- `GatewayEventStream` 支持 `async with`、独立幂等 `close()` 和未消费放弃；`close()` 不影响同 client 的其他请求。
- `close()` 在首次消费前调用时不会发出 HTTP 请求；`client.close()` 会中断全部在途流。
- `http_client` 可注入自定义 `httpx.AsyncClient`；外部注入的 client 由调用方负责关闭，SDK 只关闭自己创建的 client。
- `timeout`、`default_headers`、`max_retries`、`max_retry_delay_ms` 均可构造配置；`on_response` 支持同步或异步观察钩子。
- `repr` 不输出 token、URL userinfo、query 或 fragment。

## 接入 `nova_ai`

`nova_ai.api_impls.nova_gateway` 调用本 SDK，并将公开 DTO 事件重建为标准 `AssistantMessageEventStream`：

```python
from nova_ai.api_impls import nova_gateway
from nova_ai.auth import ProviderAuth, env_api_key_auth
from nova_ai.gateway import create_provider
from nova_protocol import Model


def gateway_provider(model: Model):
    return create_provider(
        id="volcengine",
        name="Volcengine via Nova Gateway",
        base_url="http://127.0.0.1:8000",
        models=[model],  # provider 与 base_url 必须匹配 Gateway 及其登记模型
        api=nova_gateway,
        auth=ProviderAuth(
            api_key=env_api_key_auth("nova-gateway", ["NOVA_GATEWAY_TOKEN"])
        ),
    )
```

`Model.base_url` 必须是 Gateway 地址；`SimpleStreamOptions.api_key` 或 Provider 鉴权链提供 Gateway Key，而不是上游 Provider Key。AI 的 payload/response/event 三钩子在适配层接线。

## 协议

请求与响应使用 `x-nova-gateway-protocol: 1.2`；major 不等或版本畸形抛 `GatewayProtocolError`，缺省版本头兼容同 major 旧端点。服务端回显或生成 `x-request-id`。
