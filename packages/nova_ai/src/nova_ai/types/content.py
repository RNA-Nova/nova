"""
内容类型定义
"""

from typing import Literal, Optional, Dict, Any, Union
from pydantic import Field
from .base_model import NovaBaseModel


class TextContent(NovaBaseModel):
    type: Literal["text"] = "text"
    text: str = ""
    text_signature: Optional[str] = None  # 例如OpenAI响应中的消息ID


class ThinkingContent(NovaBaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    thinking_signature: Optional[str] = None  # 例如OpenAI响应中的推理项ID
    redacted: bool = False  # 当为True时，表示思考内容被安全过滤器屏蔽

    def model_post_init(self, __context: Any) -> None:
        """加密的载荷存储在thinking_signature中，以便在多轮对话中传回API"""
        pass


class ToolCall(NovaBaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    thought_signature: Optional[str] = None  # Google专用：重用思考上下文的签名
    partial_args: Optional[str] = Field(default=None, exclude=True)  # 流式解析时的临时参数缓冲
    stream_index: Optional[int] = Field(default=None, exclude=True)  # 流式解析时的索引跟踪


class ImageContent(NovaBaseModel):
    type: Literal["image"] = "image"
    mime_type: str = ""
    data: str = ""  # base64编码的图像数据


# 内容联合类型
ContentUnion = Union[TextContent, ThinkingContent, ToolCall, ImageContent]
