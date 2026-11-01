"""OpenAI Completions 选项类型与协议标识（对齐 TS OpenAICompletionsOptions）。"""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, TypedDict, Union

from nova_protocol import KnownApi

from ...stream_options import StreamOptions, ThinkingBudgets

# API 协议标识（对齐 TS Provider.api 自描述）
api = KnownApi.OPENAI_COMPLETIONS


class ToolChoiceFunction(TypedDict):
    """强制调用指定工具的 tool_choice 形状（OpenAI 线上契约）。"""

    type: Literal["function"]
    function: Dict[str, str]  # {"name": <工具名>}——OpenAI 侧只有 name 一键


@dataclass
class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Completions 特定选项（对齐 TS OpenAICompletionsOptions）。"""

    tool_choice: Optional[Union[str, ToolChoiceFunction]] = None
    reasoning_effort: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None
    # 各思考级别的 token 预算（compat 声明预算字段或 chat-template
    # 的 $var: thinking.budget 时消费；对齐 TS thinkingBudgets）
    thinking_budgets: Optional[ThinkingBudgets] = None
