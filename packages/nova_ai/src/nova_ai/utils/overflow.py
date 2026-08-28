import re
from typing import List, Optional

from ..types.messages import AssistantMessage

"""
Regex patterns to detect context overflow errors from different providers.

These patterns match error messages returned when the input exceeds
the model's context window.
"""
OVERFLOW_PATTERNS: List[re.Pattern] = [
    re.compile(r"prompt is too long", re.IGNORECASE),  # Anthropic
    re.compile(r"request_too_large", re.IGNORECASE),  # Anthropic HTTP 413
    re.compile(r"input is too long for requested model", re.IGNORECASE),  # Bedrock
    re.compile(r"exceeds the context window", re.IGNORECASE),  # OpenAI
    re.compile(
        r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))",
        re.IGNORECASE,
    ),  # OpenAI-compatible proxies
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),  # Google
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),  # xAI
    re.compile(r"reduce the length of the messages", re.IGNORECASE),  # Groq
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),  # OpenRouter
    re.compile(
        r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",
        re.IGNORECASE,
    ),  # OpenRouter/Poolside
    re.compile(
        r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)",
        re.IGNORECASE,
    ),  # Together AI
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),  # GitHub Copilot
    re.compile(r"exceeds the available context size", re.IGNORECASE),  # llama.cpp
    re.compile(r"greater than the context length", re.IGNORECASE),  # LM Studio
    re.compile(r"context window exceeds limit", re.IGNORECASE),  # MiniMax
    re.compile(r"exceeded model token limit", re.IGNORECASE),  # Kimi For Coding
    re.compile(
        r"too large for model with \d+ maximum context length", re.IGNORECASE
    ),  # Mistral
    re.compile(
        r"prompt has [\d,]+ tokens?, but the configured context size is [\d,]+ tokens?",
        re.IGNORECASE,
    ),  # DS4
    re.compile(
        r"model_context_window_exceeded", re.IGNORECASE
    ),  # z.ai non-standard finish_reason
    re.compile(
        r"prompt too long; exceeded (?:max )?context length", re.IGNORECASE
    ),  # Ollama
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),  # Generic
    re.compile(r"too many tokens", re.IGNORECASE),  # Generic
    re.compile(r"token limit exceeded", re.IGNORECASE),  # Generic
    re.compile(
        r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.IGNORECASE
    ),  # Cerebras
]

"""
Patterns that indicate non-overflow errors (e.g. rate limiting, server errors).
"""
NON_OVERFLOW_PATTERNS: List[re.Pattern] = [
    re.compile(r"^(Throttling error|Service unavailable):", re.IGNORECASE),  # Bedrock
    re.compile(r"rate limit", re.IGNORECASE),  # Generic rate limiting
    re.compile(r"too many requests", re.IGNORECASE),  # Generic HTTP 429
]


def is_context_overflow(
    message: AssistantMessage, context_window: Optional[int] = None
) -> bool:
    """
    Check if an assistant message represents a context overflow error.
    """
    # Case 1: Error message patterns
    if message.stop_reason == "error" and message.error_message:
        is_non_overflow = any(
            pattern.search(message.error_message) for pattern in NON_OVERFLOW_PATTERNS
        )
        if not is_non_overflow and any(
            pattern.search(message.error_message) for pattern in OVERFLOW_PATTERNS
        ):
            return True

    # Case 2: Silent overflow (z.ai style) - successful but usage exceeds context
    if context_window and message.stop_reason == "stop":
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens > context_window:
            return True

    # Case 3: Length-stop overflow (Xiaomi MiMo style) - server truncates input
    # to fit context window, leaving no room for output.
    if context_window and message.stop_reason == "length" and message.usage.output == 0:
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens >= context_window * 0.99:
            return True

    return False
