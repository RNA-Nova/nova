"""volcengine fetch_models 回调测试（models.dev 基底 + ark 在售筛选层）。"""

import pytest
from nova_protocol import ApiKeyCredential, OAuthCredential, WarningKind

from nova_ai.gateway.catalog import CatalogOutcome
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.volcengine.catalog import volcengine_fetch_models
from nova_ai.providers.volcengine.provider import volcengine_provider

pytestmark = pytest.mark.asyncio


def _context(credential=None, catalog_fetch=None) -> RefreshModelsContext:
    return RefreshModelsContext(credential=credential, catalog_fetch=catalog_fetch)


def _fields(model_id: str):
    return {
        "id": model_id,
        "name": model_id,
        "reasoning": False,
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 8192,
    }


def _station_fetch(models_fields, calls=None):
    async def _fetch(signal, validator):
        if calls is not None:
            calls.append({"validator": validator})
        return CatalogOutcome(status=200, models_fields=models_fields)

    return _fetch


def _patch_ark(monkeypatch, status_map, seen=None):
    async def fake_ark(api_key, signal=None):
        if seen is not None:
            seen.append(api_key)
        return status_map

    monkeypatch.setattr(
        "nova_ai.providers.volcengine.catalog.fetch_ark_status", fake_ark
    )


async def test_no_station_returns_none():
    """无站（nova_ai 单用无容器）→ None = 本次无发布（缓存保持现状）+ 能力降级诊断，不炸。"""
    context = _context()
    models = await volcengine_fetch_models(context)
    assert models is None
    assert len(context.diagnostics) == 1
    diagnostic = context.diagnostics[0]
    assert diagnostic.kind is WarningKind.CAPABILITY
    assert diagnostic.severity == "warning"
    assert "基线" in diagnostic.message


async def test_station_failure_raises():
    """基底非 200 → 抛错（refresh 记入 errors）。"""

    async def _failing(signal, validator):
        return CatalogOutcome(status=500)

    try:
        await volcengine_fetch_models(_context(catalog_fetch=_failing))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "500" in str(e)


async def test_no_credential_skips_ark_filter(monkeypatch):
    """无凭据 → 不拉 ark、不筛选（匿名不筛）。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown"}, seen)
    fields = [_fields("m1"), _fields("m2")]
    models = await volcengine_fetch_models(
        _context(catalog_fetch=_station_fetch(fields))
    )
    assert seen == []  # ark 未被调用
    ids = [m.id for m in models]
    assert "m1" in ids and "m2" in ids  # 不筛；钉住模型另算（finalize 合并）


async def test_credential_applies_ark_filter(monkeypatch):
    """凭据带 key → ark 坏状态（Shutdown/Retiring）剔除，其余保留。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown", "m3": "Retiring"}, seen)
    fields = [_fields("m1"), _fields("m2"), _fields("m3")]
    models = await volcengine_fetch_models(
        _context(
            credential=ApiKeyCredential(key="k"),
            catalog_fetch=_station_fetch(fields),
        )
    )
    assert seen == ["k"]  # ark 用凭据的 key 调用
    ids = [m.id for m in models]
    assert "m2" in ids
    assert "m1" not in ids and "m3" not in ids  # 坏状态剔除
    # 钉住模型经 finalize 合并进来，且不在 ark 坏名单中而保留
    assert "deepseek-v4-flash-260425" in ids


async def test_ark_none_keeps_all(monkeypatch):
    """ark 拉取失败（返回 None）→ 不筛选（best-effort）。"""
    _patch_ark(monkeypatch, None)
    fields = [_fields("m1"), _fields("m2")]
    models = await volcengine_fetch_models(
        _context(
            credential=ApiKeyCredential(key="k"),
            catalog_fetch=_station_fetch(fields),
        )
    )
    ids = [m.id for m in models]
    assert "m1" in ids and "m2" in ids


async def test_oauth_credential_uses_access_token_for_ark(monkeypatch):
    """OAuth 凭据 → ark 用 access token 调（与 api_key 的 key 同通道）。"""
    seen = []
    _patch_ark(monkeypatch, {"m1": "Shutdown"}, seen)
    fields = [_fields("m1"), _fields("m2")]
    models = await volcengine_fetch_models(
        _context(
            credential=OAuthCredential(
                access="oa-tok", refresh="r", expires=9999999999999
            ),
            catalog_fetch=_station_fetch(fields),
        )
    )
    assert seen == ["oa-tok"]
    assert [m.id for m in models if m.id == "m1"] == []


async def test_factory_wires_refresh_and_source():
    """volcengine 出厂：三件套刷新（fetch_models）+ catalog_source 选配。"""
    provider = volcengine_provider()
    assert provider.refresh_models is not None
    assert provider.catalog_source == "models.dev"


async def test_ark_failure_emits_capability_diagnostic(monkeypatch):
    """ark 拉取失败：不静默——目录照常（未筛选）+ CAPABILITY 诊断。"""

    async def _boom(api_key, signal=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr("nova_ai.providers.volcengine.catalog.fetch_ark_status", _boom)
    context = _context(
        credential=ApiKeyCredential(key="k"),
        catalog_fetch=_station_fetch([_fields("m1")]),
    )
    from nova_ai.providers.volcengine.catalog import volcengine_fetch_models

    models = await volcengine_fetch_models(context)
    # 未筛选，目录照常（站拉取 + 钉住模型合并）
    assert "m1" in [m.id for m in models]
    assert any(
        d.kind is WarningKind.CAPABILITY and "ark" in d.message
        for d in context.diagnostics
    )
