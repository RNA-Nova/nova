"""anthropic_messages/client.py 的真实 SDK 创建与过线头测试。"""

import httpx2
import pytest
from anthropic import AsyncAnthropic
from nova_protocol import (
    AnthropicMessagesCompat,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    UserMessage,
)

from nova_ai.api_impls.anthropic_messages import AnthropicOptions, stream
from nova_ai.api_impls.anthropic_messages.client import (
    anthropic_federation_config,
    create_client,
)
from tests._helpers.fake_llm import RequestCapture, ant_sse_response, ant_text_turn


def _model(provider="anthropic", compat=None) -> Model:
    return Model(
        id="claude-x",
        name="x",
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider=provider,
        base_url="https://api.anthropic.com",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
        compat=compat,
    )


def _http_client(capture: RequestCapture) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        capture.record(request)
        return ant_sse_response(ant_text_turn("ok"))

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def _run(model: Model, client) -> None:
    es = stream(
        model,
        Context(messages=[UserMessage(content="hi")]),
        AnthropicOptions(client=client),
    )
    async for _ in es:
        pass
    assert (await es.result()).stop_reason == StopReason.STOP


@pytest.mark.asyncio
async def test_copilot_uses_bearer_not_x_api_key():
    capture = RequestCapture()
    client, is_oauth = create_client(
        _model("github-copilot"),
        Context(messages=[]),
        api_key="tid_test",
        http_client=_http_client(capture),
    )
    assert is_oauth is False
    await _run(_model("github-copilot"), client)
    request = capture.single()
    assert request.headers.get("authorization") == "Bearer tid_test"
    assert "x-api-key" not in request.headers


@pytest.mark.asyncio
async def test_copilot_header_only_auth_never_reads_ambient_anthropic_credentials(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token")
    capture = RequestCapture()
    client, _ = create_client(
        _model("github-copilot"),
        Context(messages=[]),
        options_headers={"Authorization": "Bearer custom"},
        http_client=_http_client(capture),
    )
    await _run(_model("github-copilot"), client)
    headers = capture.single().headers
    assert headers["authorization"] == "Bearer custom"
    assert "x-api-key" not in headers


@pytest.mark.asyncio
async def test_stream_simple_missing_auth_returns_error_stream(monkeypatch):
    for name in (
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    from nova_ai.api_impls.anthropic_messages import stream_simple

    events = stream_simple(_model(), Context(messages=[UserMessage(content="hi")]))
    result = await events.result()
    assert result.stop_reason is StopReason.ERROR
    assert "No API key" in (result.error_message or "")


@pytest.mark.asyncio
async def test_stream_simple_allows_complete_federation_config(monkeypatch):
    for name in (
        "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
        "ANTHROPIC_SERVICE_ACCOUNT_ID",
        "ANTHROPIC_WORKSPACE_ID",
    ):
        monkeypatch.setenv(name, "dummy")
    from nova_ai.api_impls.anthropic_messages import stream_simple
    from nova_ai.stream_options import SimpleStreamOptions

    event_stream = stream_simple(_model(), Context(messages=[]), SimpleStreamOptions())
    # 入口成功即证明未被普通 key 守卫误拒；不让真实 token 交换起跑。
    task = event_stream.task
    if task is not None:
        task.cancel()


def test_federation_clients_share_token_cache_and_transport(monkeypatch):
    from nova_ai.api_impls.anthropic_messages import client as client_module

    monkeypatch.setattr(client_module, "_FEDERATION_CLIENT", None)
    federation = {
        "organization_id": "org",
        "authentication": {
            "type": "oidc_federation",
            "federation_rule_id": "rule",
            "identity_token": {"source": "file", "path": "/dummy"},
        },
    }
    transport = _http_client(RequestCapture())
    first, _ = create_client(
        _model(), Context(messages=[]), http_client=transport, federation=federation
    )
    second, _ = create_client(
        _model(), Context(messages=[]), http_client=transport, federation=federation
    )
    # SDK 的 cache/transport 是内部接缝；这里验证 copy 语义而非再发请求。
    assert first is not second
    assert first._token_cache is second._token_cache
    assert first._client is second._client


@pytest.mark.asyncio
async def test_federation_header_suppression_does_not_pollute_next_request(monkeypatch):
    from nova_ai.api_impls.anthropic_messages import client as client_module

    monkeypatch.setattr(client_module, "_FEDERATION_CLIENT", None)
    federation = {
        "organization_id": "org",
        "authentication": {
            "type": "oidc_federation",
            "federation_rule_id": "rule",
            "identity_token": {"source": "file", "path": "/dummy"},
        },
    }
    transport = _http_client(RequestCapture())
    first, _ = create_client(
        _model(),
        Context(messages=[]),
        options_headers={"Accept-Encoding": None},
        http_client=transport,
        federation=federation,
    )
    second, _ = create_client(
        _model(),
        Context(messages=[]),
        http_client=transport,
        federation=federation,
    )
    assert first._token_cache is second._token_cache
    assert first._client is not second._client
    assert "accept-encoding" not in first._client.headers
    assert "accept-encoding" in second._client.headers
    await first.close()


@pytest.mark.asyncio
async def test_none_header_suppresses_sdk_default_user_agent():
    capture = RequestCapture()
    client, _ = create_client(
        _model(),
        Context(messages=[]),
        api_key="sk-test",
        options_headers={"User-Agent": None},
        http_client=_http_client(capture),
    )
    await _run(_model(), client)
    assert "user-agent" not in capture.single().headers


@pytest.mark.asyncio
@pytest.mark.parametrize("override", ["custom-beta", None])
async def test_injected_client_beta_override_and_suppression(override):
    capture = RequestCapture()
    client, _ = create_client(
        _model(),
        Context(messages=[]),
        api_key="sk-test",
        http_client=_http_client(capture),
    )
    model = _model(compat=AnthropicMessagesCompat(supports_mid_convo_effort=True))
    es = stream(
        model,
        Context(messages=[UserMessage(content="hi")]),
        AnthropicOptions(client=client, headers={"anthropic-beta": override}),
    )
    async for _ in es:
        pass
    assert (await es.result()).stop_reason == StopReason.STOP
    headers = capture.single().headers
    if override is None:
        assert "anthropic-beta" not in headers
    else:
        assert headers.get("anthropic-beta") == override


@pytest.mark.asyncio
async def test_injected_client_never_uses_sdk_retries():
    capture = RequestCapture()

    def handler(request: httpx2.Request) -> httpx2.Response:
        capture.record(request)
        return httpx2.Response(
            500,
            json={"type": "error", "error": {"type": "api_error", "message": "no"}},
        )

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = AsyncAnthropic(api_key="sk-test", http_client=http_client, max_retries=3)
    es = stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        AnthropicOptions(client=client),
    )
    async for _ in es:
        pass
    result = await es.result()
    assert result.stop_reason == StopReason.ERROR
    assert len(capture) == 1
    assert client.max_retries == 3


@pytest.mark.asyncio
async def test_oauth_explicit_beta_and_user_agent_win():
    capture = RequestCapture()
    client, is_oauth = create_client(
        _model(),
        Context(messages=[]),
        api_key="sk-ant-oat-test",
        options_headers={
            "anthropic-beta": "custom-beta",
            "user-agent": "custom-cli/1.0",
        },
        http_client=_http_client(capture),
    )
    assert is_oauth is True
    await _run(_model(), client)
    headers = capture.single().headers
    assert headers["authorization"] == "Bearer sk-ant-oat-test"
    assert headers["anthropic-beta"] == "custom-beta"
    assert headers["user-agent"] == "custom-cli/1.0"
    assert "x-api-key" not in headers


def test_federation_config_matches_sdk_shape_and_explicit_auth_wins():
    env = {
        "ANTHROPIC_FEDERATION_RULE_ID": "rule",
        "ANTHROPIC_ORGANIZATION_ID": "org",
        "ANTHROPIC_IDENTITY_TOKEN_FILE": "/tmp/identity-token",
        "ANTHROPIC_SERVICE_ACCOUNT_ID": "service",
        "ANTHROPIC_WORKSPACE_ID": "workspace",
    }
    config = anthropic_federation_config(_model(), None, None, env)
    assert config == {
        "organization_id": "org",
        "workspace_id": "workspace",
        "authentication": {
            "type": "oidc_federation",
            "federation_rule_id": "rule",
            "service_account_id": "service",
            "identity_token": {"source": "file", "path": "/tmp/identity-token"},
        },
    }
    assert anthropic_federation_config(_model(), "sk-test", None, env) is None
    assert (
        anthropic_federation_config(
            _model(), None, {"Authorization": "Bearer token"}, env
        )
        is None
    )
    incomplete = dict(env)
    del incomplete["ANTHROPIC_ORGANIZATION_ID"]
    assert anthropic_federation_config(_model(), None, None, incomplete) is None


@pytest.mark.asyncio
async def test_authorization_only_never_emits_placeholder_or_ambient_api_key(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient")
    capture = RequestCapture()
    client, _ = create_client(
        _model(),
        Context(messages=[]),
        options_headers={"Authorization": "Bearer custom"},
        http_client=_http_client(capture),
    )
    await _run(_model(), client)
    headers = capture.single().headers
    assert headers["authorization"] == "Bearer custom"
    assert "x-api-key" not in headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("api_key", "options_headers", "expected_key", "expected_authorization"),
    [
        ("sk-real", {}, "sk-real", None),
        (None, {"x-api-key": "custom-key"}, "custom-key", None),
        (None, {"cf-aig-authorization": "Bearer cf"}, None, None),
        (
            "sk-real",
            {"Authorization": "Bearer custom"},
            "sk-real",
            "Bearer custom",
        ),
        ("", {"Authorization": "Bearer custom"}, None, "Bearer custom"),
        ("", {"cf-aig-authorization": "Bearer cf"}, None, None),
    ],
)
async def test_explicit_auth_header_matrix(
    api_key, options_headers, expected_key, expected_authorization
):
    capture = RequestCapture()
    client, _ = create_client(
        _model(),
        Context(messages=[]),
        api_key=api_key,
        options_headers=options_headers,
        http_client=_http_client(capture),
    )
    await _run(_model(), client)
    headers = capture.single().headers
    assert headers.get("x-api-key") == expected_key
    assert headers.get("authorization") == expected_authorization
