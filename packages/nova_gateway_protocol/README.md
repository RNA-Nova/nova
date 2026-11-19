# nova-gateway-protocol

Nova Gateway 的独立公开协议包。只包含请求/响应 DTO、SSE 事件判别联合、错误形状、公共目录词汇以及版本与 JSON Schema，不包含 HTTP、服务端或客户端业务逻辑。

- `models.py`：`ModelRef`、流请求、白名单选项、模型列表与公共目录 manifest。
- `events.py`：文本、思考、工具调用、完成与错误事件。
- `schema.py`：协议 major/minor、版本头解析与基于同一 DTO 的 Schema 导出。

下游只能以 `provider/id` 选择服务端已登记模型；上游地址、凭据和任意请求头不属于公开请求协议。请求模型禁止额外字段，事件使用 `type` 判别，`contentIndex` 必须是非负整数。Optional 字段的 `None` 均表示未指定，出货时省略，不等于显式 `null`。

```python
import json
from nova_gateway_protocol import (
    GATEWAY_PROTOCOL_MAJOR,
    GATEWAY_PROTOCOL_MINOR,
    protocol_schema,
)

assert GATEWAY_PROTOCOL_MAJOR == 1
schema = protocol_schema()
print(json.dumps(schema, ensure_ascii=False, indent=2))
```

当前版本为 `1.2`。请求与响应使用 `x-nova-gateway-protocol` 版本头：major 不等或版本畸形会明确拒绝；缺省版本头用于兼容同 major 的旧端点。每个逻辑请求使用 `x-request-id` 关联响应，非 GET 请求使用 `Idempotency-Key` 关联重试，两值在重试间复用；幂等键不代表服务端去重承诺。新增可选字段或事件成员属于 minor 演进；删除/改义字段、修改判别键或收紧既有线上语义属于 major 演进。协议 DTO 是唯一正典源，服务端与客户端不得各自维护第二份线上形状。
