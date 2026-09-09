"""OpenAI Completions 选项类型与协议标识（对齐 TS OpenAICompletionsOptions）。"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from ...types.enums import KnownApi
from ...types.stream_options import StreamOptions, ThinkingBudgets

# API 协议标识（对齐 TS Provider.api 自描述）
api = KnownApi.OPENAI_COMPLETIONS


@dataclass
class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Completions 特定选项（对齐 TS OpenAICompletionsOptions）。"""

    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    reasoning_effort: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None
    # 各思考级别的 token 预算（compat 声明预算字段或 chat-template
    # 的 $var: thinking.budget 时消费；对齐 TS thinkingBudgets）
    thinking_budgets: Optional[ThinkingBudgets] = None
