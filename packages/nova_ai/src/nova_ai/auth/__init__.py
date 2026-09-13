"""Auth 模块

对齐 TypeScript ``src/auth``：包含 OAuth、API key、credential store、auth resolve
等鉴权相关能力。类型词汇（Credential/OAuth 形状）已迁 ``nova_protocol.auth``——
本命名空间只保留鉴权行为（流程/存储/解析）。
"""

from .context import DefaultAuthContext, default_provider_auth_context
from .credential_store import InMemoryCredentialStore
from .helpers import env_api_key_auth, lazy_oauth
from .oauth import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    DeviceCodePollStatus,
    PkceCodes,
    generate_pkce,
    kimi_oauth,
    openai_codex_oauth,
    poll_oauth_device_code_flow,
)
from .oauth_page import oauth_error_html, oauth_success_html
from .resolve import (
    AuthResolutionOverrides,
    ModelsError,
    ModelsErrorCode,
    resolve_provider_auth,
)

__all__ = [
    # context
    "DefaultAuthContext",
    "default_provider_auth_context",
    # credential_store
    "InMemoryCredentialStore",
    # helpers
    "env_api_key_auth",
    "lazy_oauth",
    # oauth
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "DeviceCodePollStatus",
    "PkceCodes",
    "generate_pkce",
    "kimi_oauth",
    "openai_codex_oauth",
    "poll_oauth_device_code_flow",
    # oauth_page
    "oauth_error_html",
    "oauth_success_html",
    # resolve
    "AuthResolutionOverrides",
    "ModelsError",
    "ModelsErrorCode",
    "resolve_provider_auth",
]
