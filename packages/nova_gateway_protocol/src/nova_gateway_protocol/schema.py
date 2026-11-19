"""协议版本、握手词汇与 JSON Schema 导出。"""

from __future__ import annotations

from typing import Any, Dict

from .events import GATEWAY_EVENT_ADAPTER
from .models import StreamRequest

GATEWAY_PROTOCOL_MAJOR = 1
GATEWAY_PROTOCOL_MINOR = 2
GATEWAY_PROTOCOL_VERSION = f"{GATEWAY_PROTOCOL_MAJOR}.{GATEWAY_PROTOCOL_MINOR}"
GATEWAY_PROTOCOL_HEADER = "x-nova-gateway-protocol"
"""请求与响应共用的版本头；缺省头按同 major 的旧端点兼容处理。"""
GATEWAY_REQUEST_ID_HEADER = "x-request-id"
"""每次逻辑请求的唯一标识，重试复用；服务端原样回显。"""
GATEWAY_IDEMPOTENCY_HEADER = "idempotency-key"
"""非 GET 请求的幂等键，重试复用；不改变上游 Provider 的计费语义。"""


def protocol_major(version: str) -> int:
    """解析 `major.minor` 的 major；畸形版本响亮失败，不静默降级。"""
    try:
        return int(version.split(".", 1)[0])
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid gateway protocol version: {version!r}") from error


def protocol_schema() -> Dict[str, Any]:
    """以同一份 DTO 导出请求与事件 Schema，避免线上契约双写。"""
    return {
        "protocol": {
            "major": GATEWAY_PROTOCOL_MAJOR,
            "minor": GATEWAY_PROTOCOL_MINOR,
            "header": GATEWAY_PROTOCOL_HEADER,
        },
        "streamRequest": StreamRequest.model_json_schema(),
        "assistantMessageEvent": GATEWAY_EVENT_ADAPTER.json_schema(),
    }


__all__ = [
    "GATEWAY_IDEMPOTENCY_HEADER",
    "GATEWAY_PROTOCOL_HEADER",
    "GATEWAY_REQUEST_ID_HEADER",
    "GATEWAY_PROTOCOL_MAJOR",
    "GATEWAY_PROTOCOL_MINOR",
    "GATEWAY_PROTOCOL_VERSION",
    "protocol_major",
    "protocol_schema",
]
