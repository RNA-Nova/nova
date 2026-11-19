"""stdlib/structlog 共用 formatter：白名单、异常格式化与最终出口清洗。"""

from __future__ import annotations

import logging
from collections.abc import Callable, MutableMapping
from typing import Any, Optional

import structlog

from .events import METADATA_FIELDS, OUTPUT_FIELDS

Redactor = Callable[[str], str]
EventDict = MutableMapping[str, Any]


def _sanitize_value(value: Any, redactor: Optional[Redactor]) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else str(value)
    if redactor is None:
        return text
    try:
        return redactor(text)
    except Exception:
        # 清洗器自身失败时宁可丢字段，也不能把未清洗文本写出去。
        return "[REDACTED]"


def _sanitize_event(
    redactor: Optional[Redactor],
) -> Callable[[logging.Logger, str, EventDict], EventDict]:
    def sanitize(
        _logger: logging.Logger, _method_name: str, event_dict: EventDict
    ) -> EventDict:
        for key, value in tuple(event_dict.items()):
            if not key.startswith("_"):
                event_dict[key] = _sanitize_value(value, redactor)
        return event_dict

    return sanitize


def _keep_whitelisted_fields(
    _logger: logging.Logger, _method_name: str, event_dict: EventDict
) -> EventDict:
    for key in tuple(event_dict):
        if key not in OUTPUT_FIELDS and not key.startswith("_"):
            del event_dict[key]
    return event_dict


def build_formatter(
    *, json_output: bool, redactor: Optional[Redactor]
) -> structlog.stdlib.ProcessorFormatter:
    """构造唯一出口；foreign pre-chain 同时接管既有 stdlib logger。"""
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False, timestamp_key="ts")
    )
    foreign_pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.ExtraAdder(
            allow=METADATA_FIELDS | frozenset({"exception", "stack"})
        ),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _sanitize_event(redactor),
        _keep_whitelisted_fields,
    ]
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=foreign_pre_chain,
    )


__all__ = ["Redactor", "build_formatter"]
