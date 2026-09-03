"""上下文 token 估算（对齐 TS utils/estimate.ts）。

用于 max_tokens 的上下文钳制等场景：以最近一次 assistant 消息的
usage 为锚点（其统计覆盖了此前全部前缀），只估算其后的尾随消息；
无锚点时退化为全量字符估算（约 4 字符 = 1 token）。
"""

import json
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..types.messages import Context, Message, Tool
from ..types.model import Usage

CHARS_PER_TOKEN = 4
ESTIMATED_IMAGE_CHARS = 4800
# 预留的安全余量，避免 max_tokens 把上下文撑满
CONTEXT_SAFETY_TOKENS = 4096


@dataclass(frozen=True, kw_only=True)
class ContextUsageEstimate:
    """上下文用量估算结果（不可变值对象——规则 5）。"""

    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: Optional[int]


def calculate_context_tokens(usage: Usage) -> int:
    """从 usage 计算上下文 token 数。"""
    return usage.total_tokens or (
        usage.input + usage.output + usage.cache_read + usage.cache_write
    )


def _safe_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "[unserializable]"


def estimate_text_tokens(text: str) -> int:
    """估算纯文本 token 数。"""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _estimate_text_and_image_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    chars = 0
    for block in content:  # type: ignore[union-attr]
        chars += len(block.text) if block.type == "text" else ESTIMATED_IMAGE_CHARS
    return chars


def estimate_message_tokens(message: Message) -> int:
    """估算单条消息 token 数。"""
    if message.role in ("user", "toolResult"):
        return math.ceil(
            _estimate_text_and_image_chars(message.content) / CHARS_PER_TOKEN
        )

    chars = 0
    for block in message.content:
        if block.type == "text":
            chars += len(block.text)
        elif block.type == "thinking":
            chars += len(block.thinking)
        else:
            chars += len(block.name) + len(_safe_json(block.arguments))
    return math.ceil(chars / CHARS_PER_TOKEN)


def _last_assistant_usage_info(
    messages: List[Message],
) -> Optional[Tuple[Usage, int]]:
    """找到最近一条可作为前缀锚点的 assistant usage。

    若该 assistant 之后插入了更新的前缀消息（如 compaction 摘要），
    其 usage 不再能描述当前前缀，跳过。
    """
    latest_prefix_timestamp = float("-inf")
    usage_info: Optional[Tuple[Usage, int]] = None

    for i, message in enumerate(messages):
        if message.role == "assistant":
            applies_to_prefix = message.timestamp >= latest_prefix_timestamp
            if (
                applies_to_prefix
                and message.stop_reason not in ("aborted", "error")
                and calculate_context_tokens(message.usage) > 0
            ):
                usage_info = (message.usage, i)
        latest_prefix_timestamp = max(
            latest_prefix_timestamp, getattr(message, "timestamp", 0) or 0
        )

    return usage_info


def _estimate_messages(messages: List[Message]) -> ContextUsageEstimate:
    usage_info = _last_assistant_usage_info(messages)
    if usage_info is not None:
        usage, index = usage_info
        usage_tokens = calculate_context_tokens(usage)
        trailing = sum(estimate_message_tokens(m) for m in messages[index + 1 :])
        return ContextUsageEstimate(
            tokens=usage_tokens + trailing,
            usage_tokens=usage_tokens,
            trailing_tokens=trailing,
            last_usage_index=index,
        )

    tokens = sum(estimate_message_tokens(m) for m in messages)
    return ContextUsageEstimate(
        tokens=tokens, usage_tokens=0, trailing_tokens=tokens, last_usage_index=None
    )


def _estimate_tools_tokens(tools: Optional[List[Tool]]) -> int:
    if not tools:
        return 0
    return estimate_text_tokens(_safe_json([t.model_dump() for t in tools]))


def estimate_context_tokens(context: Context) -> ContextUsageEstimate:
    """估算整个上下文的 token 数（对齐 TS estimateContextTokens）。

    有 usage 锚点时，追加锚点之后 ``added_tool_names`` 对应的工具定义 tokens；
    无锚点时估算全部消息 + system prompt + 工具定义。
    """
    estimate = _estimate_messages(context.messages)
    if estimate.last_usage_index is not None:
        added_names = set()
        for message in context.messages[estimate.last_usage_index + 1 :]:
            if message.role == "toolResult":
                added_names.update(getattr(message, "added_tool_names", None) or [])
        added_tools = [
            tool for tool in (context.tools or []) if tool.name in added_names
        ]
        added_tool_tokens = _estimate_tools_tokens(added_tools)
        return ContextUsageEstimate(
            tokens=estimate.tokens + added_tool_tokens,
            usage_tokens=estimate.usage_tokens,
            trailing_tokens=estimate.trailing_tokens + added_tool_tokens,
            last_usage_index=estimate.last_usage_index,
        )

    prefix_tokens = (
        estimate_text_tokens(context.system_prompt) if context.system_prompt else 0
    ) + _estimate_tools_tokens(context.tools)

    return ContextUsageEstimate(
        tokens=estimate.tokens + prefix_tokens,
        usage_tokens=estimate.usage_tokens,
        trailing_tokens=estimate.trailing_tokens + prefix_tokens,
        last_usage_index=None,
    )
