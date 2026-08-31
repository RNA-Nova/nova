"""运行时事件的直通序列化（哑管道）。

架构 2.0：RPC 事件桥**不做任何呈现加工**——不挑字段、不造显示块、
不发明状态词汇。运行时事件本身就是公共契约（pydantic、带 ``type``
判别符、会话 JSONL 同源），序列化原样转发；前端自行映射渲染模型。

本模块唯一的加工是 JSON 安全兜底（``_safe_serialize``）——把
AbortSignal / Callable 等不可序列化值降级为 ``str()``，这是传输层
本分，不属于 UI。
"""

from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any, Dict, Optional

from nova_ai.types.base_model import NovaBaseModel


def _safe_serialize(value: Any) -> Any:
    """把任意运行时值安全转换为 JSON 可序列化形式。

    - None / str / int / float / bool：原样；
    - pydantic 模型：``model_dump(mode="json")`` 转 dict；
    - dataclass：vars 后逐字段递归（跳过私有字段）；
    - list/tuple/set/dict：逐元素递归；
    - 其他对象（AbortSignal、Callable、内部实例）：降级为 ``str()``。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, NovaBaseModel):
        return value.dump_wire()  # 线上兜底也走 camel（与显式出口同向）
    if is_dataclass(value) and not isinstance(value, type):
        return {
            k: _safe_serialize(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_serialize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_serialize(v) for k, v in value.items()}
    return str(value)


# 传输层公共出口：任何出站帧（事件/响应/通知）的 JSON 安全兜底。
to_json_safe = _safe_serialize


def serialize_event(event: Any) -> Optional[Dict[str, Any]]:
    """把运行时事件直通序列化为 ``{"type": ..., "data": ...}`` 信封。

    返回 None 表示该对象不是可识别的事件（无 ``type`` 字符串），
    调用方丢弃。除此之外不做任何过滤——全量、不重不漏。
    """
    event_type = getattr(event, "type", None)
    if not isinstance(event_type, str) or not event_type:
        return None
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        # 线上 camelCase（dump_wire）；dataclass 事件走 _safe_serialize 原样
        dump_wire = getattr(event, "dump_wire", None)
        if callable(dump_wire):
            data = _safe_serialize(dump_wire())
        else:
            data = _safe_serialize(model_dump(mode="json"))
    elif is_dataclass(event):
        data = _safe_serialize(event)
    else:
        return None
    return {"type": event_type, "data": data}


__all__ = ["serialize_event", "to_json_safe"]
