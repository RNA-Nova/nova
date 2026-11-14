"""volcengine ark_personalize 钩子测试（ark 在售筛选——账户生命周期事实）。"""

import pytest
from nova_ai.gateway.catalog import CatalogError
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.volcengine.catalog import (
    BASE_URL,
    ark_personalize,
    fetch_ark_status,
)
from nova_ai.providers.volcengine.provider import volcengine_provider
from nova_protocol import (
    ApiKeyCredential,
    KnownApi,
    Model,
    ModelCost,
    OAuthCredential,
    WarningKind,
)
from tests._helpers.auth import api_key_credential, oauth_credential

pytestmark = pytest.mark.asyncio


def _model(model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="volcengine",
        base_url=BASE_URL,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=262144,
        max_tokens=8192,
    )


def _base(*ids: str):
    return [_model(i) for i in ids]


def _context(credential=None) -> RefreshModelsContext:
    return RefreshModelsContext(credential=credential)


def _patch_ark(monkeypatch, status_map, seen=None):
    async def fake_ark(api_key, signal=None, base_url=None):
        if seen is not None:
            seen.append(api_key)
        return status_map

    monkeypatch.setattr(
        "nova_ai.providers.volcengine.catalog.fetch_ark_status", fake_ark
    )


async def test_no_credential_skips_ark_filter(monkeypatch):
    """无凭据 → 不拉 ark、不筛选（匿名不筛，基底原样）。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown"}, seen)
    result = await ark_personalize(_base("m1", "m2"), _context())
    assert seen == []  # ark 未被调用
    assert [m.id for m in result] == ["m1", "m2"]


async def test_credential_applies_ark_filter(monkeypatch):
    """凭据带 key → ark 坏状态（Shutdown/Retiring）剔除，其余保留。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown", "m3": "Retiring"}, seen)
    result = await ark_personalize(
        _base("m1", "m2", "m3"),
        _context(credential=api_key_credential("k")),
    )
    assert seen == ["k"]  # ark 用凭据的 key 调用
    assert [m.id for m in result] == ["m2"]


async def test_ark_invalid_payload_raises(monkeypatch):
    """ark 载荷无效即失败，不把未筛选基底冒充 personalized 成品。"""
    _patch_ark(monkeypatch, None)
    with pytest.raises(CatalogError, match="payload"):
        await ark_personalize(
            _base("m1", "m2"), _context(credential=api_key_credential("k"))
        )


async def test_oauth_credential_uses_access_token_for_ark(monkeypatch):
    """OAuth 凭据 → ark 用 access token 调（与 api_key 的 key 同通道）。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown"}, seen)
    result = await ark_personalize(
        _base("m1", "m2"),
        _context(
            credential=oauth_credential(
                access="oa-tok", refresh="r", expires=9999999999999
            )
        ),
    )
    assert seen == ["oa-tok"]
    assert [m.id for m in result] == ["m2"]


async def test_ark_failure_raises_and_keeps_cause(monkeypatch):
    """ark 失败沿 cause 保留真因；策略层据此保留旧 personalized 成品。"""

    async def _boom(api_key, signal=None, base_url=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr("nova_ai.providers.volcengine.catalog.fetch_ark_status", _boom)
    with pytest.raises(CatalogError, match="lifecycle") as exc_info:
        await ark_personalize(_base("m1"), _context(credential=api_key_credential("k")))
    assert isinstance(exc_info.value.__cause__, TimeoutError)


async def test_ark_models_url_derives_from_effective_provider_base_url(monkeypatch):
    seen = []

    async def fake_fetch_json(url, *, api_key, signal):
        seen.append(url)
        return {"data": []}

    monkeypatch.setattr(
        "nova_ai.providers.volcengine.catalog.fetch_json", fake_fetch_json
    )
    result = await fetch_ark_status("k", base_url="https://ark.example.com/custom/v3/")
    assert result == {}
    assert seen == ["https://ark.example.com/custom/v3/models"]


async def test_factory_wires_personalized_refresh_strategy():
    """volcengine 出厂：personalized 刷新策略与 models.dev 站选配。"""
    provider = volcengine_provider()
    assert provider.refresh_models is not None
    assert provider.catalog_source == "models.dev"
