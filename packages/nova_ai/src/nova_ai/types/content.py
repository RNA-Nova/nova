"""
内容类型定义
"""

from typing import Annotated, Any, Dict, Literal, Optional, Union

from pydantic import ConfigDict, Field

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


class ToolCall(NovaBaseModel):
    """工具调用内容块。

    双重身份（AGENTS.md 数据建模规则 1 注记）：既是消息契约（随消息
    dump/validate），又是流式累积器（``_stream.py`` 逐 delta 原地写
    ``arguments`` / ``partial_args``）——表示保留 Pydantic 以满足边界
    parse/dump；赋值期校验关闭（校验发生在构造与解析边界，不跟每次增量写）。
    """

    model_config = ConfigDict(validate_assignment=False)

    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    thought_signature: Optional[str] = None  # Google专用：重用思考上下文的签名
    partial_args: Optional[str] = Field(
        default=None, exclude=True
    )  # 流式解析时的临时参数缓冲
    stream_index: Optional[int] = Field(
        default=None, exclude=True
    )  # 流式解析时的索引跟踪


class ImageContent(NovaBaseModel):
    type: Literal["image"] = "image"
    mime_type: str = ""
    data: str = ""  # base64编码的图像数据


# 内容联合类型（判别键 ``type``——规则 6：随消息走 model_validate，显式判别）
ContentUnion = Annotated[
    Union[TextContent, ThinkingContent, ToolCall, ImageContent],
    Field(discriminator="type"),
]
