"""UI 基本类型与前端运行模式。"""

from typing import Any, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel


class UIResponse(NovaBaseModel):
    """UI request 的统一响应。"""

    value: Any = None
    cancelled: bool = False
    confirmed: Optional[bool] = None


ExtensionMode = Literal["print", "rpc", "websocket", "tui", "json"]
"""前端运行模式。描述与扩展交互的 UI 形态，而非底层传输协议。"""


__all__ = ["ExtensionMode", "UIResponse"]
