"""Anthropic provider 工厂。"""

from typing import Optional, cast

from nova_ai.auth import ApiKeyAuth, ApiKeyAuthInput, ProviderAuth
from nova_protocol import AuthCheck, AuthResult, ModelAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderStreams, create_provider
from .models import ANTHROPIC_MODELS

_FEDERATION_REQUIRED_ENV = (
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)
_FEDERATION_OPTIONAL_ENV = (
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_WORKSPACE_ID",
)


def _anthropic_api_key_auth() -> ApiKeyAuth:
    """Anthropic 的完整 ambient 鉴权链（pi anthropicApiKeyAuth 对位）。"""
    standard = env_api_key_auth(
        "Anthropic API key", ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]
    )

    async def resolve(input: ApiKeyAuthInput) -> Optional[AuthResult]:
        credential = input["credential"]
        if credential and credential.key:
            return await standard.resolve(input)

        # AUTH_TOKEN 是 Bearer 头，不是 x-api-key；它优先于另外两把 key。
        auth_token = await input["ctx"].env("ANTHROPIC_AUTH_TOKEN")
        if auth_token:
            return AuthResult(
                auth=ModelAuth(headers={"Authorization": f"Bearer {auth_token}"}),
                source="ANTHROPIC_AUTH_TOKEN",
            )

        resolved = await standard.resolve(input)
        if resolved is not None:
            return resolved

        # federation 只负责供给配置；短期访问令牌的交换与刷新归官方 SDK。
        federation = {}
        for name in _FEDERATION_REQUIRED_ENV:
            value = await input["ctx"].env(name)
            if not value:
                return None
            federation[name] = value
        for name in _FEDERATION_OPTIONAL_ENV:
            value = await input["ctx"].env(name)
            if value:
                federation[name] = value
        return AuthResult(
            auth=ModelAuth(), env=federation, source="workload identity federation"
        )

    async def check(input: ApiKeyAuthInput) -> Optional[AuthCheck]:
        result = await resolve(input)
        return (
            AuthCheck(type="api_key", source=result.source)
            if result is not None
            else None
        )

    return ApiKeyAuth(
        name=standard.name, resolve=resolve, login=standard.login, check=check
    )


def anthropic_provider() -> "Provider":
    """构造 Anthropic provider 实例（nova_ai 静态出厂；``catalog_source="pi.dev"``
    ——overlay 刷新策略出生自带）。"""
    from ...api_impls import anthropic_messages

    return create_provider(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com",
        models=list(ANTHROPIC_MODELS.values()),
        # 模块结构上满足 ProviderStreams，cast 收口（见 kimi_coding）
        api=cast(ProviderStreams, anthropic_messages),
        auth=ProviderAuth(api_key=_anthropic_api_key_auth()),
        # 运行时 pi.dev 全供（api/级别表/compat）——无运行时 corrections；
        # 种子知识归 nova_catalog/providers/anthropic.py 的 DEF
        catalog_source="pi.dev",
    )
