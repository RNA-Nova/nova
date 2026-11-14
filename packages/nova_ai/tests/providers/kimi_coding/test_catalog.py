"""kimi-coding kimi_personalize 钩子测试（managed 订阅源覆盖层：名单+活字段）。"""

import httpx
import pytest
from nova_ai.gateway.catalog import CatalogError
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.kimi_coding.catalog import BASE_URL, kimi_personalize
from nova_ai.providers.kimi_coding.provider import kimi_coding_provider
from nova_protocol import (
    KnownApi,
    Model,
    ModelCost,
    NovaErrorKind,
    OAuthCredential,
    OpenAICompletionsCompat,
    ThinkingFormat,
    WarningKind,
)
from tests._helpers.auth import api_key_credential, oauth_credential

pytestmark = pytest.mark.asyncio


def _oauth() -> OAuthCredential:
    return oauth_credential(access="tok", refresh="r", expires=9999999999999)


def _context(credential=None) -> RefreshModelsContext:
    return RefreshModelsContext(credential=credential)


def _model(model_id: str, **overrides) -> Model:
    model = Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="kimi-coding",
        base_url=BASE_URL,
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=0.95, output=4, cache_read=0.19, cache_write=0),
        context_window=1048576,
        max_tokens=32768,
        compat=OpenAICompletionsCompat(thinking_format=ThinkingFormat.OPENAI),
    )
    return model.model_copy(update=overrides)


def _managed_payload(*ids):
    """managed /models 响应体（think_efforts 含两级）。"""
    return {
        "data": [
            {
                "id": model_id,
                "display_name": f"Managed {model_id}",
                "context_length": 262144,
                "supports_reasoning": True,
                "supports_image_in": True,
                "think_efforts": {"support": True, "valid_efforts": ["low", "high"]},
            }
            for model_id in ids
        ]
    }


def _patch_managed(monkeypatch, payload, seen_headers=None):
    async def fake_request(method, url, **kwargs):
        if seen_headers is not None:
            seen_headers.append(kwargs["headers"]["Authorization"])
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )


async def test_no_credential_returns_base(monkeypatch):
    """无凭据 → 基底原样（managed 不被调用）。"""
    called = []

    async def fake_request(method, url, **kwargs):
        called.append(url)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    base = [_model("kimi-for-coding")]
    result = await kimi_personalize(base, _context())
    assert result is base
    assert called == []


async def test_api_key_credential_uses_managed_too(monkeypatch):
    """api_key 凭据同样走订阅端点（coding plan 双通道：Bearer 都是 token）。"""
    seen = []
    _patch_managed(monkeypatch, _managed_payload("kimi-for-coding"), seen)
    result = await kimi_personalize(
        [_model("kimi-for-coding")], _context(credential=api_key_credential("k"))
    )
    assert seen == ["Bearer k"]
    assert [m.id for m in result] == ["kimi-for-coding"]


async def test_managed_overlay_semantics(monkeypatch):
    """覆盖式合并：名单以 managed 为准（基底多余剔除）；活字段（名称/级别表/
    context/输入类型）覆盖；公共事实（价格/max_tokens/compat）整条继承基底。"""
    _patch_managed(monkeypatch, _managed_payload("kimi-for-coding"))
    base = [
        _model("kimi-for-coding"),
        _model("base-only"),  # managed 没有 → 剔除
    ]
    result = await kimi_personalize(base, _context(credential=_oauth()))
    assert len(result) == 1
    merged = result[0]
    assert isinstance(merged, Model)
    # 活字段覆盖（managed 账户真相）
    assert merged.name == "Managed kimi-for-coding"
    assert merged.context_window == 262144
    assert merged.input_types == ["text", "image"]
    levels = merged.thinking_level_map
    assert levels is not None
    assert levels.get("low") == "low"
    assert levels.get("high") == "high"
    assert levels.get("max") is None
    assert levels.get("off") is None
    # 公共事实继承基底（账户层零修正）
    assert merged.cost.input == 0.95 and merged.cost.output == 4
    assert merged.max_tokens == 32768  # 不再被 context_length 充数顶掉
    compat = merged.compat
    assert isinstance(compat, OpenAICompletionsCompat)
    assert compat.thinking_format == "openai"
    assert merged.api == "openai-completions"
    assert merged.provider == "kimi-coding"


async def test_managed_only_id_skipped_with_diagnostic(monkeypatch):
    """站未收录的 id（订阅抢先上架）→ 跳过 + 诊断（站不认的当作没有）。"""
    _patch_managed(monkeypatch, _managed_payload("kimi-for-coding", "brand-new"))
    context = _context(credential=_oauth())
    result = await kimi_personalize([_model("kimi-for-coding")], context)
    assert [m.id for m in result] == ["kimi-for-coding"]
    assert len(context.diagnostics) == 1
    diagnostic = context.diagnostics[0]
    assert diagnostic.kind is WarningKind.CAPABILITY
    assert "brand-new" in diagnostic.message
    assert "not present in the public catalog" in diagnostic.message


async def test_managed_auth_failure_raises(monkeypatch):
    """401/402/403 → 凭据失效，抛错（refresh 记入 errors，不静默）。"""

    async def fake_request(method, url, **kwargs):
        return httpx.Response(401, json={"error": "expired"})

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    try:
        await kimi_personalize(
            [_model("kimi-for-coding")], _context(credential=_oauth())
        )
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "auth failed" in str(e)
        assert isinstance(e, CatalogError)
        assert e.nova_error_kind is NovaErrorKind.UNAUTHORIZED
        assert e.status_code == 401


async def test_managed_transport_error_raises_instead_of_publishing_base(monkeypatch):
    """有凭据的 managed 传输失败必须报错，不能发布未过滤基底。"""

    async def fake_request(method, url, **kwargs):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    with pytest.raises(CatalogError, match="transport failed") as exc_info:
        await kimi_personalize(
            [_model("kimi-for-coding")], _context(credential=_oauth())
        )
    assert isinstance(exc_info.value.__cause__, RuntimeError)


async def test_managed_non_200_raises(monkeypatch):
    """managed 非 200 是本次个性化失败，旧成品由策略层继续服役。"""

    async def fake_request(method, url, **kwargs):
        return httpx.Response(503)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    with pytest.raises(CatalogError) as exc_info:
        await kimi_personalize(
            [_model("kimi-for-coding")], _context(credential=_oauth())
        )
    assert exc_info.value.status_code == 503


async def test_factory_wires_personalized_strategy():
    """kimi-coding 出厂：personalized 刷新策略 + replace 读取位 + 站选配。"""
    provider = kimi_coding_provider()
    assert provider.refresh_models is not None
    assert provider.catalog_source == "pi.dev"


@pytest.mark.parametrize(
    "entry",
    [
        {**_managed_payload("kimi-for-coding")["data"][0], "context_length": True},
        {**_managed_payload("kimi-for-coding")["data"][0], "display_name": 123},
        {
            **_managed_payload("kimi-for-coding")["data"][0],
            "supports_reasoning": "yes",
        },
        {
            **_managed_payload("kimi-for-coding")["data"][0],
            "supports_image_in": "yes",
        },
        {**_managed_payload("kimi-for-coding")["data"][0], "think_efforts": []},
        {
            **_managed_payload("kimi-for-coding")["data"][0],
            "think_efforts": {"valid_efforts": "high"},
        },
    ],
)
async def test_malformed_managed_entries_fail_whole_refresh(monkeypatch, entry):
    """replace 名单中的坏条目不得被静默删掉；整轮失败以保留旧成品。"""
    _patch_managed(monkeypatch, {"data": [entry]})
    with pytest.raises(CatalogError):
        await kimi_personalize(
            [_model("kimi-for-coding")], _context(credential=_oauth())
        )
