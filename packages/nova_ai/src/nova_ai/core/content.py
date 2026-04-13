"""
内容类型定义
"""

from typing import Literal, Dict, Any, Optional
from dataclasses import dataclass, field
from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class TextContent(DataClassJSONMixin):
    """文本内容"""
    type: Literal["text"] = "text"
    text: str = ""
    text_signature: Optional[str] = None  # 例如OpenAI响应中的消息ID


@dataclass
class ThinkingContent(DataClassJSONMixin):
    """思考过程内容（推理过程）"""
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    thinking_signature: Optional[str] = None  # 例如OpenAI响应中的推理项ID
    redacted: bool = False  # 当为True时，表示思考内容被安全过滤器屏蔽
    
    def __post_init__(self):
        """加密的载荷存储在thinking_signature中，以便在多轮对话中传回API"""
        pass


@dataclass
class ToolCall(DataClassJSONMixin):
    """工具调用"""
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    thought_signature: Optional[str] = None  # Google专用：重用思考上下文的签名


@dataclass
class ImageContent(DataClassJSONMixin):
    """图像内容（用于用户消息和工具结果）"""
    type: Literal["image"] = "image"
    data: str = ""  # base64 encoded image data
    mime_type: str = ""


# 内容联合类型
ContentUnion = TextContent | ThinkingContent | ToolCall | ImageContent