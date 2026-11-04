"""AssistantMessage 的诊断追加件（对齐 pi ``utils/diagnostics.ts``）。

``diagnostics`` 是开放形状列表（失败与恢复路径的留痕——threads 回退、
strict 降级等），随消息落盘、上 RPC 事件流。追加单点：统一补毫秒
时间戳、列表缺席时现建。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from nova_protocol import AssistantMessage


def append_diagnostic(message: AssistantMessage, entry: Dict[str, Any]) -> None:
    """追加一条诊断（对齐 pi ``appendAssistantMessageDiagnostic``）。

    ``entry`` 至少带 ``type``；时间戳缺省补当前毫秒。
    """
    payload: Dict[str, Any] = {"timestamp": int(time.time() * 1000), **entry}
    message.diagnostics = [*(message.diagnostics or []), payload]


__all__ = ["append_diagnostic"]
