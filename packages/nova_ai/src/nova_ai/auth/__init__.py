"""鉴权行为定义、宿主端口与公共入口（公共数据词汇归 ``nova_protocol``）。"""

from typing import TYPE_CHECKING

from .definitions import ApiKeyAuth, ApiKeyAuthInput, OAuthAuth, ProviderAuth
from .ports import AuthContext, AuthInteraction, CredentialStore, LoginCancelledError

if TYPE_CHECKING:
    from .oauth.kimi import kimi_oauth
    from .oauth.openai_codex import openai_codex_oauth

from .context import DefaultAuthContext, default_provider_auth_context
from .credential_store import InMemoryCredentialStore
from .helpers import env_api_key_auth, lazy_oauth
from .oauth import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    DeviceCodePollStatus,
    PkceCodes,
    generate_pkce,
    poll_oauth_device_code_flow,
)
from .oauth.oauth_page import oauth_error_html, oauth_success_html
from .resolve import (
    AuthResolutionOverrides,
    ModelsError,
    ModelsErrorCode,
    resolve_provider_auth,
)

__all__ = [
    "ApiKeyAuth",
    "ApiKeyAuthInput",
    "AuthContext",
    "AuthInteraction",
    "AuthResolutionOverrides",
    "CredentialStore",
    "DefaultAuthContext",
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "DeviceCodePollStatus",
    "InMemoryCredentialStore",
    "LoginCancelledError",
    "ModelsError",
    "ModelsErrorCode",
    "OAuthAuth",
    "PkceCodes",
    "ProviderAuth",
    "default_provider_auth_context",
    "env_api_key_auth",
    "generate_pkce",
    "kimi_oauth",
    "lazy_oauth",
    "oauth_error_html",
    "oauth_success_html",
    "openai_codex_oauth",
    "poll_oauth_device_code_flow",
    "resolve_provider_auth",
]
