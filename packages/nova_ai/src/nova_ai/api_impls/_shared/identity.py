"""请求身份的合并、User-Agent 与 provider 归因头（对位 pi identity.ts）。"""

import platform
from importlib.metadata import PackageNotFoundError, version
from typing import Optional
from urllib.parse import urlsplit

from nova_protocol import ClientAttribution, ClientIdentity, ModelBase, UserAgentCustom

from .user_agent import get_nova_user_agent

NOVA_ATTRIBUTION = ClientAttribution(
    name="nova",
    url="https://github.com/RNA-Nova/nova",
    categories=["cli-agent"],
)


def merge_client_identity(
    *sources: Optional[ClientIdentity],
) -> Optional[ClientIdentity]:
    """逐字段合并身份；只覆盖调用方显式设置的字段，null 可关闭归因。"""
    merged = {}
    saw_source = False
    for source in sources:
        if source is None:
            continue
        saw_source = True
        merged.update(source.model_dump(exclude_unset=True))
    if not saw_source:
        return None
    return ClientIdentity.model_validate(merged)


def format_user_agent(identity: Optional[ClientIdentity] = None) -> str:
    """按身份生成 UA；未指定 UA 细节时保留 Nova 现有默认格式。"""
    if identity is None or (identity.user_agent is None and identity.version is None):
        return get_nova_user_agent()
    mode = identity.user_agent or "detailed"
    if isinstance(mode, UserAgentCustom):
        return mode.custom
    if mode == "short":
        return "nova-ai"
    client_version = identity.version
    if client_version is None:
        try:
            client_version = version("nova-ai")
        except PackageNotFoundError:
            client_version = "0.1.5"
    return (
        f"nova-ai/{client_version} "
        f"({platform.system().lower()} {platform.release()}; "
        f"python/{platform.python_version()}; {platform.machine()})"
    )


def _attribution_headers(
    provider: str, base_url: str, attribution: Optional[ClientAttribution]
) -> dict[str, str]:
    if attribution is None:
        return {}
    host = urlsplit(base_url).hostname or ""
    if provider == "openrouter" or "openrouter.ai" in base_url:
        return {
            "HTTP-Referer": attribution.url,
            "X-OpenRouter-Title": attribution.name,
            **(
                {"X-OpenRouter-Categories": ",".join(attribution.categories)}
                if attribution.categories
                else {}
            ),
        }
    if provider == "nvidia" or host == "integrate.api.nvidia.com":
        name = attribution.name[:1].upper() + attribution.name[1:]
        return {"X-BILLING-INVOKE-ORIGIN": name}
    if provider in ("opencode", "opencode-go") or host == "opencode.ai":
        return {"x-opencode-client": attribution.name}
    return {}


def get_identity_headers(
    model: ModelBase, identity: Optional[ClientIdentity]
) -> dict[str, str]:
    """身份默认头；协议层把它放在 model/options headers 之前合并。"""
    return {
        "User-Agent": format_user_agent(identity),
        **_attribution_headers(
            model.provider, model.base_url, identity.attribution if identity else None
        ),
    }


__all__ = [
    "NOVA_ATTRIBUTION",
    "format_user_agent",
    "get_identity_headers",
    "merge_client_identity",
]
