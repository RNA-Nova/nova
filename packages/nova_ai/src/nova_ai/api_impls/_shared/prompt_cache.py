"""
Prompt cache 与 Anthropic 风格 cache_control（实现层共享件）

对齐 TS ``src/api/openai-prompt-cache.ts`` 与 openai-completions 内的
cache_control 段——Anthropic 系模型的缓存标记逻辑对将来的 anthropic-messages
实现同样适用，故收在实现层共享件而非单一实现内部。
"""

from typing import Any, Dict, List, Literal, NotRequired, Optional, TypedDict

from ...types.compat import OpenAICompletionsCompat


def resolve_cache_retention(
    cache_retention: Optional[str], env: Optional[Dict[str, str]] = None
) -> str:
    """解析缓存保留策略（对齐 TS resolveCacheRetention）。

    环境变量只认 ``NOVA_CACHE_RETENTION``。
    """
    if cache_retention:
        return cache_retention
    env_value = None
    if env:
        env_value = env.get("NOVA_CACHE_RETENTION")
    if env_value is None:
        import os

        env_value = os.environ.get("NOVA_CACHE_RETENTION")
    return "long" if env_value == "long" else "short"


# ---------------------------------------------------------------------------
# prompt cache key
# ---------------------------------------------------------------------------

OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64


def clamp_openai_prompt_cache_key(key: Optional[str]) -> Optional[str]:
    """截断 prompt_cache_key 到最大长度（对齐 TS clampOpenAIPromptCacheKey）。

    按 Unicode code point 截断，避免截断多字节字符。
    """
    if key is None:
        return None
    chars = list(key)
    if len(chars) <= OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH:
        return key
    return "".join(chars[:OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH])


# ---------------------------------------------------------------------------
# Anthropic 风格 cache_control
# ---------------------------------------------------------------------------


class CacheControl(TypedDict):
    """Anthropic 风格 cache_control 载荷（规则 10 声明）。"""

    type: Literal["ephemeral"]
    ttl: NotRequired[Literal["1h"]]


def _get_compat_cache_control(
    compat: OpenAICompletionsCompat, cache_retention: str
) -> Optional[CacheControl]:
    """根据 compat 和 cache retention 构造 cache_control（对齐 TS getCompatCacheControl）。"""
    if compat.cache_control_format != "anthropic" or cache_retention == "none":
        return None
    control: CacheControl = {"type": "ephemeral"}
    if cache_retention == "long" and compat.supports_long_cache_retention:
        control["ttl"] = "1h"
    return control


def _add_cache_control_to_text_content(
    message: Dict[str, Any], cache_control: CacheControl
) -> bool:
    """给消息的文本内容添加 cache_control。"""
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return False
        message["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
        return True
    if not isinstance(content, list):
        return False
    for part in reversed(content):
        if isinstance(part, dict) and part.get("type") == "text":
            part["cache_control"] = cache_control
            return True
    return False


def _add_cache_control_to_system_prompt(
    messages: List[Any], cache_control: CacheControl
) -> None:
    """给第一条 system/developer 消息加 cache_control。"""
    for msg in messages:
        role = msg.get("role")
        if role in ("system", "developer"):
            _add_cache_control_to_text_content(msg, cache_control)
            return


def _add_cache_control_to_last_conversation_message(
    messages: List[Any], cache_control: CacheControl
) -> None:
    """给最后一条 user/assistant/tool 消息加 cache_control。

    tool role 必须纳入：openrouter+anthropic 模型最后一轮常是工具结果，
    marker 落空会打到更早的消息上，降低缓存命中。
    """
    for msg in reversed(messages):
        role = msg.get("role")
        if role in ("user", "assistant", "tool"):
            if _add_cache_control_to_text_content(msg, cache_control):
                return


def _add_cache_control_to_last_tool(
    tools: Optional[List[Dict[str, Any]]], cache_control: CacheControl
) -> None:
    """给最后一条 tool 定义加 cache_control。"""
    if not tools:
        return
    tools[-1]["cache_control"] = cache_control


def _apply_anthropic_cache_control(
    messages: List[Any],
    tools: Optional[List[Dict[str, Any]]],
    cache_control: CacheControl,
) -> None:
    """应用 Anthropic 风格缓存标记（system + 最后 tool + 最后对话消息）。"""
    _add_cache_control_to_system_prompt(messages, cache_control)
    _add_cache_control_to_last_tool(tools, cache_control)
    _add_cache_control_to_last_conversation_message(messages, cache_control)
