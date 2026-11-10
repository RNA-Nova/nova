"""AssistantMessage 的诊断追加件（对齐 pi ``utils/diagnostics.ts``）。

``diagnostics`` 是开放形状列表（失败与恢复路径的留痕——threads 回退、
strict 降级等），随消息落盘、上 RPC 事件流。追加单点：统一补毫秒
时间戳、列表缺席时现建、**字符串值一律过 ``redact_secrets``**（诊断
随消息持久化并上线，与 ``error_message`` 同一出口纪律——第三方网关
的错误报文可能回显请求头）。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from nova_protocol import AssistantMessage, redact_secrets


def _redact_value(value: Any) -> Any:
    """字符串值脱敏（递归容器——诊断条目的嵌套 details 同纪律）。"""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(item) for item in value)
    return value


def append_diagnostic(message: AssistantMessage, entry: Dict[str, Any]) -> None:
    """追加一条诊断（对齐 pi ``appendAssistantMessageDiagnostic``）。

    ``entry`` 至少带 ``type``；时间戳缺省补当前毫秒；字符串值出口即
    脱敏（字段注释"脱敏后的诊断"的实现侧兑现）。
    """
    payload: Dict[str, Any] = {
        "timestamp": int(time.time() * 1000),
        **{key: _redact_value(value) for key, value in entry.items()},
    }
    message.diagnostics = [*(message.diagnostics or []), payload]


__all__ = ["append_diagnostic"]
