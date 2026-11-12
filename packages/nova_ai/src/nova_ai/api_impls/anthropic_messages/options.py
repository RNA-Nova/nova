"""Anthropic Messages 选项类型与协议标识（对齐 TS ``AnthropicOptions``）。"""

from dataclasses import dataclass
from typing import Any, Literal, Optional, TypedDict, Union

from nova_protocol import KnownApi

from ...stream_options import StreamOptions

# API 协议标识（对齐 TS Provider.api 自描述）
api = KnownApi.ANTHROPIC_MESSAGES

# adaptive thinking 的 effort 线值（对齐 TS AnthropicEffort）
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]

# thinking 内容的返回形态（对齐 TS AnthropicThinkingDisplay）：
# "summarized" 返回摘要思考文本；"omitted" 思考字段为空但签名照样回传
# （多轮连续性不断）。默认 "summarized"（对齐 pi——与 Claude 4 旧行为一致）
AnthropicThinkingDisplay = Literal["summarized", "omitted"]


class ToolChoiceTool(TypedDict):
    """强制调用指定工具的 tool_choice 形状（Anthropic 线上契约）。"""

    type: Literal["tool"]
    name: str


@dataclass
class AnthropicOptions(StreamOptions):
    """Anthropic Messages 特定选项（对齐 TS AnthropicOptions）。"""

    # 显式开启/关闭扩展思考；None = 不携带 thinking 字段（服务端默认）
    thinking_enabled: Optional[bool] = None
    # 预算制思考（旧模型）的 token 预算；adaptive 模型忽略。默认 1024
    thinking_budget_tokens: Optional[int] = None
    # adaptive thinking 的 effort 级别（adaptive 模型专用）
    effort: Optional[AnthropicEffort] = None
    # 思考内容返回形态（默认 summarized，见上）
    thinking_display: Optional[AnthropicThinkingDisplay] = None
    # 非 adaptive 模型是否请求 interleaved-thinking beta 头。默认 True
    interleaved_thinking: Optional[bool] = None
    # 工具选择："auto"/"any"/"none" 或强制指定 {"type": "tool", "name": ...}
    tool_choice: Optional[Union[str, ToolChoiceTool]] = None
    # 预构建的 SDK 客户端实例（对齐 TS ``client?``）——注入替代实现
    # （如 Vertex 变体或测试的 MockTransport 客户端）；提供则跳过内部构造，
    # 属主归调用方（本层不关闭）
    client: Optional[Any] = None
