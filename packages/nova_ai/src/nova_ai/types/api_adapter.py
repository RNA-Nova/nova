"""
API 适配器协议
定义 API 协议实现者必须满足的接口契约
"""

from typing import TYPE_CHECKING, Optional, Protocol
from .model import Model
from .messages import Context
from .stream_options import StreamOptions, SimpleStreamOptions

if TYPE_CHECKING:
    from ..streaming.event_stream import AssistantMessageEventStream


class ApiAdapter(Protocol):
    """
    API 适配器协议

    每个 API 类型（如 openai-completions、anthropic-messages 等）
    都需要注册一个实现了该协议的适配器。
    """

    api: str

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None
    ) -> "AssistantMessageEventStream":
        """流式调用"""
        ...

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None
    ) -> "AssistantMessageEventStream":
        """简化的流式调用"""
        ...
