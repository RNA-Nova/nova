import re
from typing import Optional, List
from ..types.messages import AssistantMessage

"""
Regex patterns to detect context overflow errors from different providers.

These patterns match error messages returned when the input exceeds
the model's context window.

Provider-specific patterns (with example error messages):

- Anthropic: "prompt is too long: 213462 tokens > 200000 maximum"
- OpenAI: "Your input exceeds the context window of this model"
- Google: "The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)"
- xAI: "This model's maximum prompt length is 131072 but the request contains 537812 tokens"
- Groq: "Please reduce the length of the messages or completion"
- OpenRouter: "This endpoint's maximum context length is X tokens. However, you requested about Y tokens"
- llama.cpp: "the request exceeds the available context size, try increasing it"
- LM Studio: "tokens to keep from the initial prompt is greater than the context length"
- GitHub Copilot: "prompt token count of X exceeds the limit of Y"
- MiniMax: "invalid params, context window exceeds limit"
- Kimi For Coding: "Your request exceeded model token limit: X (requested: Y)"
- Cerebras: Returns "400/413 status code (no body)" - handled separately below
- Mistral: Returns "400/413 status code (no body)" - handled separately below
- z.ai: Does NOT error, accepts overflow silently - handled via usage.input > contextWindow
- Ollama: Silently truncates input - not detectable via error message
"""
OVERFLOW_PATTERNS: List[re.Pattern] = [
    re.compile(r'prompt is too long', re.IGNORECASE),  # Anthropic
    re.compile(r'input is too long for requested model', re.IGNORECASE),  # Amazon Bedrock
    re.compile(r'exceeds the context window', re.IGNORECASE),  # OpenAI (Completions & Responses API)
    re.compile(r'input token count.*exceeds the maximum', re.IGNORECASE),  # Google (Gemini)
    re.compile(r'maximum prompt length is \d+', re.IGNORECASE),  # xAI (Grok)
    re.compile(r'reduce the length of the messages', re.IGNORECASE),  # Groq
    re.compile(r'maximum context length is \d+ tokens', re.IGNORECASE),  # OpenRouter (all backends)
    re.compile(r'exceeds the limit of \d+', re.IGNORECASE),  # GitHub Copilot
    re.compile(r'exceeds the available context size', re.IGNORECASE),  # llama.cpp server
    re.compile(r'greater than the context length', re.IGNORECASE),  # LM Studio
    re.compile(r'context window exceeds limit', re.IGNORECASE),  # MiniMax
    re.compile(r'exceeded model token limit', re.IGNORECASE),  # Kimi For Coding
    re.compile(r'context[_ ]length[_ ]exceeded', re.IGNORECASE),  # Generic fallback
    re.compile(r'too many tokens', re.IGNORECASE),  # Generic fallback
    re.compile(r'token limit exceeded', re.IGNORECASE),  # Generic fallback
]


def is_context_overflow(message: AssistantMessage, context_window: Optional[int] = None) -> bool:
    """
    Check if an assistant message represents a context overflow error.

    This handles two cases:
    1. Error-based overflow: Most providers return stopReason "error" with a
       specific error message pattern.
    2. Silent overflow: Some providers accept overflow requests and return
       successfully. For these, we check if usage.input exceeds the context window.

    ## Reliability by Provider

    **Reliable detection (returns error with detectable message):**
    - Anthropic: "prompt is too long: X tokens > Y maximum"
    - OpenAI (Completions & Responses): "exceeds the context window"
    - Google Gemini: "input token count exceeds the maximum"
    - xAI (Grok): "maximum prompt length is X but request contains Y"
    - Groq: "reduce the length of the messages"
    - Cerebras: 400/413 status code (no body)
    - Mistral: 400/413 status code (no body)
    - OpenRouter (all backends): "maximum context length is X tokens"
    - llama.cpp: "exceeds the available context size"
    - LM Studio: "greater than the context length"
    - Kimi For Coding: "exceeded model token limit: X (requested: Y)"

    **Unreliable detection:**
    - z.ai: Sometimes accepts overflow silently (detectable via usage.input > contextWindow),
      sometimes returns rate limit errors. Pass contextWindow param to detect silent overflow.
    - Ollama: Silently truncates input without error. Cannot be detected via this function.
      The response will have usage.input < expected, but we don't know the expected value.

    ## Custom Providers

    If you've added custom models via settings.json, this function may not detect
    overflow errors from those providers. To add support:

    1. Send a request that exceeds the model's context window
    2. Check the errorMessage in the response
    3. Create a regex pattern that matches the error
    4. The pattern should be added to OVERFLOW_PATTERNS in this file, or
       check the errorMessage yourself before calling this function

    Args:
        message: The assistant message to check
        context_window: Optional context window size for detecting silent overflow (z.ai)

    Returns:
        True if the message indicates a context overflow
    """
    # Case 1: Check error message patterns
    if message.stop_reason == "error" and message.error_message:
        # Check known patterns
        if any(pattern.search(message.error_message) for pattern in OVERFLOW_PATTERNS):
            return True

        # Cerebras and Mistral return 400/413 with no body for context overflow
        # Note: 429 is rate limiting (requests/tokens per time), NOT context overflow
        if re.search(r'^4(00|13)\s*(status code)?\s*\(no body\)', message.error_message, re.IGNORECASE):
            return True

    # Case 2: Silent overflow (z.ai style) - successful but usage exceeds context
    if context_window and message.stop_reason == "stop":
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens > context_window:
            return True

    return False


