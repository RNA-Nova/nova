"""Auth 模块

对齐 TypeScript ``src/auth``：包含 OAuth、API key、credential store、auth resolve
等鉴权相关能力。类型定义统一住在 ``nova_ai.types.auth``，此处重导出作为
auth 命名空间的公共门面。
"""

from ..types.auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthEvent,
    AuthInfoLink,
    AuthInteraction,
    AuthPrompt,
    AuthPromptOption,
    AuthResult,
    AuthType,
    Credential,
    CredentialInfo,
    CredentialStore,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
    ProviderEnv,
    ProviderHeaders,
)
from .context import DefaultAuthContext, default_provider_auth_context
from .credential_store import InMemoryCredentialStore
from .helpers import env_api_key_auth, lazy_oauth
from .oauth import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
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
    # types（住在 nova_ai.types.auth）
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthCheck",
    "AuthContext",
    "AuthEvent",
    "AuthInfoLink",
    "AuthInteraction",
    "AuthPrompt",
    "AuthPromptOption",
    "AuthResult",
    "AuthType",
    "Credential",
    "CredentialInfo",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
    "ProviderEnv",
    "ProviderHeaders",
]
