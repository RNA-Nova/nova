"""
EventSerializer 单元测试。

覆盖事件序列化对 Pydantic BaseModel、dataclass、普通对象 fallback、
Enum 以及嵌套结构的支持。
"""

import dataclasses
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel

from nova_harness.modes.rpc.events import EventSerializer


class EventKind(str, Enum):
    """测试用枚举。"""

    TEXT = "text"
    TOOL = "tool"


class NestedPydanticModel(BaseModel):
    """嵌套 Pydantic 模型。"""

    value: int
    kind: EventKind


class SimplePydanticEvent(BaseModel):
    """简单 Pydantic 事件，会被 fallback 处理。"""

    type: str = "simple"
    message: str = "hello"


class PydanticEventWithToDict(BaseModel):
    """显式提供 to_dict 方法的 Pydantic 事件。"""

    type: str = "with_to_dict"
    message: str = "world"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


@dataclasses.dataclass
class DataclassEvent:
    """带 event_type 的 dataclass 事件。"""

    event_type: str = "dataclass_event"
    message: str = "hi"
    kind: EventKind = EventKind.TEXT


@dataclasses.dataclass
class NestedDataclassEvent:
    """包含嵌套 Pydantic 模型的 dataclass 事件。"""

    event_type: str = "nested"
    nested: NestedPydanticModel = dataclasses.field(
        default_factory=lambda: NestedPydanticModel(value=1, kind=EventKind.TOOL)
    )


class FallbackEvent:
    """普通对象，依赖 _FALLBACK_ATTRS 序列化。"""

    def __init__(self):
        self.type = "fallback"
        self.message = "fallback message"
        self.tool_name = "read"
        self.args = {"path": "/tmp"}


class TestEventSerializer:
    """EventSerializer.serialize 行为测试。"""

    def test_pydantic_with_to_dict(self):
        """显式提供 to_dict 的 Pydantic 模型应直接调用 model_dump。"""
        event = PydanticEventWithToDict()
        result = EventSerializer.serialize(event)
        assert result == {"type": "with_to_dict", "message": "world"}

    def test_pydantic_model_dump(self):
        """普通 Pydantic 模型应直接调用 model_dump 序列化。"""
        event = SimplePydanticEvent()
        result = EventSerializer.serialize(event)
        assert result == {"type": "simple", "message": "hello"}

    def test_dataclass_event_type_to_type(self):
        """dataclass 中的 event_type 应映射为 type。"""
        event = DataclassEvent()
        result = EventSerializer.serialize(event)
        assert result["type"] == "dataclass_event"
        assert "event_type" not in result
        assert result["message"] == "hi"

    def test_dataclass_enum_serialization(self):
        """dataclass 中的 Enum 应序列化为 .name。"""
        event = DataclassEvent(kind=EventKind.TOOL)
        result = EventSerializer.serialize(event)
        assert result["kind"] == "TOOL"

    def test_dataclass_nested_pydantic(self):
        """dataclass 嵌套 Pydantic 模型时应递归序列化。"""
        event = NestedDataclassEvent()
        result = EventSerializer.serialize(event)
        assert result["type"] == "nested"
        assert result["nested"] == {"value": 1, "kind": "TOOL"}

    def test_fallback_object(self):
        """普通对象应提取 _FALLBACK_ATTRS 中的属性。"""
        event = FallbackEvent()
        result = EventSerializer.serialize(event)
        assert result["type"] == "fallback"
        assert result["message"] == "fallback message"
        assert result["tool_name"] == "read"
        assert result["args"] == {"path": "/tmp"}

    def test_fallback_unknown_object(self):
        """无任何已知属性的普通对象应返回 type='unknown'。"""
        event = object()
        result = EventSerializer.serialize(event)
        assert result == {"type": "unknown"}

    def test_fallback_nested_pydantic_value(self):
        """fallback 中嵌套 Pydantic 模型值应调用 model_dump（Enum 序列化为 name）。"""

        class MixedEvent:
            type = "mixed"
            message = NestedPydanticModel(value=2, kind=EventKind.TEXT)

        result = EventSerializer.serialize(MixedEvent())
        assert result["message"] == {"value": 2, "kind": "TEXT"}
