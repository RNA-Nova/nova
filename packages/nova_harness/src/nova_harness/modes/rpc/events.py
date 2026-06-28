"""Agent event serialization for JSON-RPC notifications."""

import dataclasses
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel


class EventSerializer:
    """Serialize Agent/AgentSession events to JSON-RPC notification payloads."""

    _FALLBACK_ATTRS = (
        "message",
        "tool_name",
        "args",
        "partialResult",
        "is_error",
        "toolResults",
        "messages",
        "turnId",
        "attempt",
        "max_attempts",
        "delay_ms",
        "error_message",
        "success",
        "final_error",
        "reason",
        "result",
        "aborted",
        "will_retry",
    )

    @classmethod
    def serialize(cls, event: Any) -> Dict[str, Any]:
        """Best-effort serialization of an arbitrary event object."""
        if hasattr(event, "model_dump"):
            return cls._serialize_value(event.model_dump())

        if dataclasses.is_dataclass(event):
            payload = dataclasses.asdict(event)
            if "type" not in payload and "event_type" in payload:
                payload["type"] = payload.pop("event_type")
            return cls._serialize_value(payload)

        return cls._serialize_fallback(event)

    @classmethod
    def _serialize_value(cls, obj: Any) -> Any:
        if isinstance(obj, BaseModel):
            return cls._serialize_value(obj.model_dump())
        if isinstance(obj, Enum):
            return obj.name
        if isinstance(obj, dict):
            return {k: cls._serialize_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._serialize_value(v) for v in obj]
        return obj

    @classmethod
    def _serialize_fallback(cls, event: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"type": getattr(event, "type", "unknown")}
        for attr in cls._FALLBACK_ATTRS:
            if hasattr(event, attr):
                val = getattr(event, attr)
                if hasattr(val, "model_dump"):
                    payload[attr] = cls._serialize_value(val.model_dump())
                elif hasattr(val, "__dict__"):
                    payload[attr] = val.__dict__
                else:
                    payload[attr] = val
        return payload
