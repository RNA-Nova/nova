"""UI 基本类型。"""

from typing import Any, Optional

from nova_ai.types.base_model import NovaBaseModel


class UIResponse(NovaBaseModel):
    """UI request 的统一响应。"""

    value: Any = None
    cancelled: bool = False
    confirmed: Optional[bool] = None


__all__ = ["UIResponse"]
