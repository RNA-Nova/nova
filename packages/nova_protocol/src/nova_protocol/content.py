"""
内容类型定义
"""

from typing import Annotated, Any, Dict, Literal, Optional, Union

from pydantic import ConfigDict, Field

from .base_model import NovaBaseModel


class TextContent(NovaBaseModel):
    """文本内容块。

    ``text_signature``：provider 的消息级签名（例如 OpenAI 响应中的消息 ID），
    仅同模型重放时保留；``None`` = 无签名。
    """

    type: Literal["text"] = "text"
    text: str = ""
    text_signature: Optional[str] = None


class ThinkingContent(NovaBaseModel):
    """思考内容块。

    ``thinking_signature``：provider 的推理项签名（例如 OpenAI 响应中的
    推理项 ID；OpenRouter 的 reasoning details JSON 数组也归档在此），
    仅同模型重放时保留；``None`` = 无签名。
    ``redacted`` 为 True 时表示思考内容被安全过滤器屏蔽（加密不透明，
    跨模型重放时丢弃）。
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    thinking_signature: Optional[str] = None
    redacted: bool = False


class ToolCall(NovaBaseModel):
    """工具调用内容块。

    双重身份（AGENTS.md 数据建模规则 1 注记）：既是消息契约（随消息
    dump/validate），又是流式累积器（``_stream.py`` 逐 delta 原地写
    ``arguments`` / ``partial_args``）——表示保留 Pydantic 以满足边界
    parse/dump；赋值期校验关闭（校验发生在构造与解析边界，不跟每次增量写）。

    - ``arguments``：工具参数（任意 JSON 对象——线上载体，本层不校验；
      参数的形状归工具所在包声明与校验）；
    - ``thought_signature``：Google 专用的思考上下文签名；``None`` = 无；
    - ``partial_args`` / ``stream_index``：流式解析的瞬态缓冲，
      不参与序列化（``exclude=True``）。
    """

    model_config = ConfigDict(validate_assignment=False)

    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    thought_signature: Optional[str] = None
    partial_args: Optional[str] = Field(
        default=None, exclude=True
    )  # 流式解析时的临时参数缓冲
    stream_index: Optional[int] = Field(
        default=None, exclude=True
    )  # 流式解析时的索引跟踪


class ImageContent(NovaBaseModel):
    """图像内容块（``data`` 为 base64 编码的图像数据）。"""

    type: Literal["image"] = "image"
    mime_type: str = ""
    data: str = ""


# 内容联合类型（判别键 ``type``——规则 6：随消息走 model_validate，显式判别）
ContentUnion = Annotated[
    Union[TextContent, ThinkingContent, ToolCall, ImageContent],
    Field(discriminator="type"),
]
