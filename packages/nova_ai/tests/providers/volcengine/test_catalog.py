"""volcengine ark_personalize 钩子测试（ark 在售筛选——账户生命周期事实）。"""

import pytest
from nova_protocol import (
    ApiKeyCredential,
    KnownApi,
    Model,
    ModelCost,
    OAuthCredential,
    WarningKind,
)

from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.volcengine.catalog import BASE_URL, ark_personalize
from nova_ai.providers.volcengine.provider import volcengine_provider

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
    async def fake_ark(api_key, signal=None):
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
        _context(credential=ApiKeyCredential(key="k")),
    )
    assert seen == ["k"]  # ark 用凭据的 key 调用
    assert [m.id for m in result] == ["m2"]


async def test_ark_none_keeps_all(monkeypatch):
    """ark 拉取失败（返回 None）→ 不筛选（best-effort，基底原样）。"""
    _patch_ark(monkeypatch, None)
    result = await ark_personalize(
        _base("m1", "m2"),
        _context(credential=ApiKeyCredential(key="k")),
    )
    assert [m.id for m in result] == ["m1", "m2"]


async def test_oauth_credential_uses_access_token_for_ark(monkeypatch):
    """OAuth 凭据 → ark 用 access token 调（与 api_key 的 key 同通道）。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown"}, seen)
    result = await ark_personalize(
        _base("m1", "m2"),
        _context(
            credential=OAuthCredential(
                access="oa-tok", refresh="r", expires=9999999999999
            )
        ),
    )
    assert seen == ["oa-tok"]
    assert [m.id for m in result] == ["m2"]


async def test_ark_failure_emits_capability_diagnostic(monkeypatch):
    """ark 拉取失败：不静默——基底原样（未筛选）+ CAPABILITY 诊断。"""

    async def _boom(api_key, signal=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr("nova_ai.providers.volcengine.catalog.fetch_ark_status", _boom)
    context = _context(credential=ApiKeyCredential(key="k"))
    result = await ark_personalize(_base("m1"), context)
    assert [m.id for m in result] == ["m1"]
    assert any(
        d.kind is WarningKind.CAPABILITY and "ark" in d.message
        for d in context.diagnostics
    )


async def test_factory_wires_personalized_strategy():
    """volcengine 出厂：personalized 刷新策略 + replace 读取位 + 站选配。"""
    provider = volcengine_provider()
    assert provider.refresh_models is not None
    assert provider.catalog_source == "models.dev"
    assert provider._dynamic_replaces_baseline is True
