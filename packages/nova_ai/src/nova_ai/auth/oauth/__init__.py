"""OAuth flow 模块。

对齐 TypeScript ``src/auth/oauth``。
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .kimi import kimi_oauth
    from .openai_codex import openai_codex_oauth

from .device_code import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    DeviceCodePollStatus,
    poll_oauth_device_code_flow,
)
from .errors import OAuthFlowError
from .pkce import PkceCodes, generate_pkce

__all__ = [
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "DeviceCodePollStatus",
    "OAuthFlowError",
    "PkceCodes",
    "generate_pkce",
    "kimi_oauth",
    "openai_codex_oauth",
    "poll_oauth_device_code_flow",
]


def __getattr__(name: str):
    """OAuth 完整流程按首次访问加载，避免根导入拖入全部厂商实现。"""
    if name == "kimi_oauth":
        value = import_module(".kimi", __name__).kimi_oauth
    elif name == "openai_codex_oauth":
        value = import_module(".openai_codex", __name__).openai_codex_oauth
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
