"""哑管道事件直通序列化测试（rpc/protocol/serialize.py）。

原则：全量、零呈现加工——事件原样进 ``{type, data}`` 信封，
只有 JSON 安全兜底（AbortSignal/Callable 降级为 str）。
"""

from dataclasses import dataclass
from typing import Any, Optional

from nova_ai.types.base_model import NovaBaseModel

from nova_harness.server.protocol.serialize import serialize_event


class _FakeEvent(NovaBaseModel):
    """模拟 pydantic 运行时事件。"""

    type: str = "message_update"
    message: dict = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    turn: int = 1


@dataclass
class _DataclassEvent:
    """模拟 dataclass 运行时事件（含不可序列化字段）。"""

    type: str = "custom_signal"

    class _Signal:
        def __repr__(self) -> str:
            return "<AbortSignal>"

    payload: Any = None

    def __post_init__(self):
        self.payload = self._Signal()


def test_pydantic_event_passthrough_full_fidelity():
    """pydantic 事件：type 透传，data 全量（不挑字段、不丢内容）。"""
    payload = serialize_event(_FakeEvent())
    assert payload is not None
    assert payload["type"] == "message_update"
    assert payload["data"]["message"]["content"][0]["text"] == "hi"
    assert payload["data"]["turn"] == 1


def test_dataclass_event_unserializable_fields_degrade_to_str():
    """AbortSignal 等不可序列化字段降级为 str（传输兜底，非呈现加工）。"""
    payload = serialize_event(_DataclassEvent())
    assert payload is not None
    assert payload["type"] == "custom_signal"
    assert payload["data"]["payload"] == "<AbortSignal>"


def test_event_without_type_dropped():
    """无 type 字符串的对象不是事件，返回 None。"""
    assert serialize_event(object()) is None
    assert serialize_event({"type": "dict-event"}) is None


def test_nested_pydantic_and_collections_serialized():
    """嵌套 pydantic / list / dict 递归序列化。"""

    @dataclass
    class Nested:
        type: str = "nested"
        items: Any = None

        def __post_init__(self):
            self.items = [_FakeEvent(), {"k": (1, 2)}]

    payload = serialize_event(Nested())
    assert payload is not None
    assert payload["data"]["items"][0]["type"] == "message_update"
    assert payload["data"]["items"][1]["k"] == [1, 2]
