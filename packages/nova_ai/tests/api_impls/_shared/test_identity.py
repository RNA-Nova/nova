"""请求身份合并、User-Agent 与 provider 归因头测试。"""

from nova_protocol import (
    ClientAttribution,
    ClientIdentity,
    Model,
    ModelCost,
    UserAgentCustom,
)

from nova_ai.api_impls._shared.identity import (
    NOVA_ATTRIBUTION,
    format_user_agent,
    get_identity_headers,
    merge_client_identity,
)


def _model(provider="openrouter", base_url="https://openrouter.ai/api/v1") -> Model:
    return Model(
        id="m",
        name="M",
        api="openai-completions",
        provider=provider,
        base_url=base_url,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(),
        context_window=1000,
        max_tokens=100,
    )


def test_merge_identity_preserves_unset_and_allows_attribution_opt_out():
    default = ClientIdentity(attribution=NOVA_ATTRIBUTION, user_agent="short")
    merged = merge_client_identity(default, ClientIdentity(version="1.2.3"))
    assert merged is not None
    assert merged.attribution == NOVA_ATTRIBUTION
    assert merged.user_agent == "short"
    assert merged.version == "1.2.3"

    disabled = merge_client_identity(default, ClientIdentity(attribution=None))
    assert disabled is not None
    assert disabled.attribution is None


def test_format_user_agent_supports_short_detailed_and_custom():
    assert format_user_agent(ClientIdentity(user_agent="short")) == "nova-ai"
    assert (
        format_user_agent(
            ClientIdentity(user_agent=UserAgentCustom(custom="custom-agent"))
        )
        == "custom-agent"
    )
    detailed = format_user_agent(ClientIdentity(version="1.2.3"))
    assert detailed.startswith("nova-ai/1.2.3 ")
    assert "python/" in detailed


def test_openrouter_identity_headers_include_attribution():
    identity = ClientIdentity(
        user_agent="short",
        attribution=ClientAttribution(
            name="nova-test", url="https://example.com", categories=["cli-agent"]
        ),
    )
    headers = get_identity_headers(_model(), identity)
    assert headers == {
        "User-Agent": "nova-ai",
        "HTTP-Referer": "https://example.com",
        "X-OpenRouter-Title": "nova-test",
        "X-OpenRouter-Categories": "cli-agent",
    }
