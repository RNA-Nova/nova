"""kimi-coding fetch_models 回调测试（managed 订阅源双路 + 公开站回落）。"""

import httpx
import pytest
from nova_protocol import ApiKeyCredential, NovaErrorKind, OAuthCredential, WarningKind

from nova_ai.gateway.catalog import CatalogError, CatalogOutcome
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.kimi_coding.catalog import kimi_coding_fetch_models
from nova_ai.providers.kimi_coding.provider import kimi_coding_provider

pytestmark = pytest.mark.asyncio


def _oauth() -> OAuthCredential:
    return OAuthCredential(access="tok", refresh="r", expires=9999999999999)


def _context(credential=None, catalog_fetch=None) -> RefreshModelsContext:
    return RefreshModelsContext(credential=credential, catalog_fetch=catalog_fetch)


def _station_fetch(fields=None, calls=None):
    """假公开站拉取函数（ctx.catalog_fetch 形态：(signal, validator) -> Outcome）。"""

    async def _fetch(signal, validator):
        if calls is not None:
            calls.append({"validator": validator})
        return CatalogOutcome(status=200, models_fields=fields or [])

    return _fetch


async def test_no_credential_uses_public_station():
    """无凭据 → 走公开站回落（ctx.catalog_fetch），不碰 managed 端点。"""
    calls = []
    models = await kimi_coding_fetch_models(
        _context(catalog_fetch=_station_fetch(calls=calls))
    )
    assert models == []
    assert calls == [{"validator": None}]


async def test_no_credential_no_station_returns_none():
    """无凭据且无站（nova_ai 单用无容器）→ None = 本次无发布（缓存保持现状）+ 能力降级诊断，不炸。"""
    context = _context()
    models = await kimi_coding_fetch_models(context)
    assert models is None
    assert len(context.diagnostics) == 1
    diagnostic = context.diagnostics[0]
    assert diagnostic.kind is WarningKind.CAPABILITY
    assert diagnostic.severity == "warning"
    assert "无 OAuth 凭据" in diagnostic.message


async def test_api_key_credential_uses_managed_too(monkeypatch):
    """api_key 凭据同样走订阅端点（coding plan 双通道：OAuth 或 API key——
    Bearer 都是 token，端点同样认）。"""
    payload = {
        "data": [
            {
                "id": "kimi-for-coding",
                "display_name": "Kimi For Coding",
                "context_length": 262144,
                "supports_reasoning": True,
                "supports_image_in": False,
                "think_efforts": {"support": True, "valid_efforts": ["high"]},
            }
        ]
    }

    async def fake_post(method, url, **kwargs):
        # api_key 凭据：Authorization 头用 key 本身
        assert kwargs["headers"]["Authorization"] == "Bearer k"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )
    models = await kimi_coding_fetch_models(
        _context(credential=ApiKeyCredential(key="k"))
    )
    assert [m.id for m in models] == ["kimi-for-coding"]


async def test_station_failure_raises():
    """公开站非 200 → 抛错（refresh 记入 errors，不静默）。

    分类归归一单点：CatalogError 只透传 status_code，不自声明 kind——
    归一按状态码标准映射（500→UPSTREAM_ERROR）。
    """

    from nova_ai.utils.errors import normalize_error

    async def _failing(signal, validator):
        return CatalogOutcome(status=500)

    try:
        await kimi_coding_fetch_models(_context(catalog_fetch=_failing))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "500" in str(e)
        assert isinstance(e, CatalogError)
        assert getattr(e, "nova_error_kind", None) is None  # 不自声明
        assert e.status_code == 500
        assert normalize_error(e).kind is NovaErrorKind.UPSTREAM_ERROR  # 归一分类


async def test_station_transport_failure_preserves_cause_kind():
    """status=0 + cause（传输超时）→ 归一沿 cause 链取真 kind（REQUEST_TIMEOUT），
    不再误标 UPSTREAM_ERROR（分类保真回归）。"""

    from nova_ai.utils.errors import normalize_error

    async def _failing(signal, validator):
        return CatalogOutcome(status=0, cause=TimeoutError("read timed out"))

    try:
        await kimi_coding_fetch_models(_context(catalog_fetch=_failing))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert isinstance(e, CatalogError)
        assert getattr(e, "status_code", None) is None  # 合成码 0 不透传为 HTTP 状态
        assert e.__cause__ is not None
        assert normalize_error(e).kind is NovaErrorKind.REQUEST_TIMEOUT


async def test_station_fields_finalized():
    """公开站字段经 finalize（修正 + 盖戳）成 Model——协议随站（anthropic）。"""
    fields = [
        {
            "id": "kimi-for-coding",
            "name": "Kimi For Coding",
            "api": "anthropic-messages",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            "context_window": 262144,
            "max_tokens": 32768,
            "thinking_level_map": {
                "off": None,
                "minimal": None,
                "low": "low",
                "medium": None,
                "high": "high",
                "xhigh": None,
                "max": "max",
            },
        }
    ]
    models = await kimi_coding_fetch_models(
        _context(catalog_fetch=_station_fetch(fields))
    )
    assert len(models) == 1
    model = models[0]
    assert model.id == "kimi-for-coding"
    assert model.provider == "kimi-coding"
    assert model.base_url == "https://api.kimi.com/coding"
    assert model.api == "anthropic-messages"  # 源数据驱动（站上是啥就是啥）
    # 方言换血已退役：无 KimiCLI UA、无 OpenAI 方言 compat
    assert model.headers is None
    # 修正：COMPAT_DEFAULTS（adaptive）+ MODEL_CORRECTIONS（级别表 + 空签名）
    assert model.compat is not None
    assert model.compat.force_adaptive_thinking is True
    assert model.compat.allow_empty_signature is True
    # 思考级别表：源数据自带（pi.dev 现携——修正只补 adaptive/空签名旗标）
    assert model.thinking_level_map is not None
    assert model.thinking_level_map.get("high") == "high"
    assert model.thinking_level_map.get("max") == "max"


async def test_managed_maps_models(monkeypatch):
    """managed /models 响应 → Model（含思考级别表与输入类型）。"""
    payload = {
        "data": [
            {
                "id": "kimi-k3",
                "display_name": "Kimi K3",
                "context_length": 262144,
                "supports_reasoning": True,
                "supports_image_in": True,
                "think_efforts": {"support": True, "valid_efforts": ["low", "high"]},
            },
            {"id": ""},  # 畸形条目跳过
            {"id": "bad"},  # 缺 context_length 跳过
        ]
    }

    async def fake_post(method, url, **kwargs):
        assert url.endswith("/models")
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )
    models = await kimi_coding_fetch_models(_context(credential=_oauth()))
    assert len(models) == 1
    model = models[0]
    assert model.id == "kimi-k3"
    assert model.name == "Kimi K3"
    assert model.input_types == ["text", "image"]
    assert model.reasoning is True
    assert model.thinking_level_map is not None
    assert model.thinking_level_map.get("low") == "low"
    assert model.thinking_level_map.get("high") == "high"
    assert model.thinking_level_map.get("max") is None
    assert model.provider == "kimi-coding"


async def test_managed_auth_failure_raises(monkeypatch):
    """401/402/403 → 凭据失效，抛错（refresh 记入 errors，不静默）。"""

    async def fake_post(method, url, **kwargs):
        return httpx.Response(401, json={"error": "expired"})

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )
    try:
        await kimi_coding_fetch_models(_context(credential=_oauth()))
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "auth failed" in str(e)
        # typed + kind：401 → UNAUTHORIZED（重登录语义）
        assert isinstance(e, CatalogError)
        assert e.nova_error_kind is NovaErrorKind.UNAUTHORIZED
        assert e.status_code == 401


async def test_managed_non_200_falls_back_to_station(monkeypatch):
    """managed 端点非 200（非鉴权错误）→ 落公开站回落，不视为失败。"""

    async def fake_post(method, url, **kwargs):
        return httpx.Response(503)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )
    calls = []
    models = await kimi_coding_fetch_models(
        _context(credential=_oauth(), catalog_fetch=_station_fetch(calls=calls))
    )
    assert models == []
    assert len(calls) == 1


async def test_merged_path_keeps_station_price_and_managed_liveness(monkeypatch):
    """基底 + managed 合并：清单以 managed 为准（pi.dev 独有的剔除），
    活字段（思考级别）来自 managed，价格保留基底（pi.dev）。"""
    payload = {
        "data": [
            {
                "id": "kimi-for-coding",
                "display_name": "Kimi For Coding",
                "context_length": 262144,
                "supports_reasoning": True,
                "supports_image_in": False,
                "think_efforts": {"support": True, "valid_efforts": ["low", "high"]},
            },
            {
                "id": "managed-only",
                "display_name": "Managed Only",
                "context_length": 131072,
                "supports_reasoning": False,
                "supports_image_in": False,
                "think_efforts": {"support": False, "valid_efforts": []},
            },
        ]
    }

    async def fake_post(method, url, **kwargs):
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )
    station_fields = [
        {
            "id": "kimi-for-coding",
            "name": "Kimi For Coding",
            "api": "anthropic-messages",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 0.95, "output": 4, "cache_read": 0.19, "cache_write": 0},
            "context_window": 1048576,
            "max_tokens": 32768,
            "thinking_level_map": {
                "off": None,
                "minimal": None,
                "low": "low",
                "medium": None,
                "high": "high",
                "xhigh": None,
                "max": "max",
            },
        },
        {
            "id": "station-only",
            "name": "Station Only",
            "api": "anthropic-messages",
            "reasoning": False,
            "input_types": ["text"],
            "cost": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
            "context_window": 262144,
            "max_tokens": 8192,
        },
    ]
    models = await kimi_coding_fetch_models(
        _context(
            credential=_oauth(),
            catalog_fetch=_station_fetch(station_fields),
        )
    )
    by_id = {m.id: m for m in models}
    # 清单以 managed 为准：station-only 剔除，managed-only 补入
    assert set(by_id) == {"kimi-for-coding", "managed-only"}
    merged = by_id["kimi-for-coding"]
    # 活字段来自 managed：思考级别表用 managed 的 valid_efforts
    assert merged.thinking_level_map is not None
    assert merged.thinking_level_map.get("low") == "low"
    assert merged.thinking_level_map.get("high") == "high"
    assert merged.thinking_level_map.get("max") is None
    assert merged.context_window == 262144
    # 价格保留基底（pi.dev 的指示性价格——订阅也有成本提醒）
    assert merged.cost.input == 0.95
    assert merged.cost.output == 4
    assert merged.cost.cache_read == 0.19
    # managed-only 无基底价格：零值占位
    assert by_id["managed-only"].cost.input == 0


async def test_managed_transport_error_falls_back_to_station(monkeypatch):
    """managed 端点传输失败（超时/连接失败）→ 不占修正层，落公开站基底。"""

    async def fake_request(method, url, **kwargs):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    fields = [
        {
            "id": "kimi-for-coding",
            "name": "Kimi For Coding",
            "api": "anthropic-messages",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 0.95, "output": 4, "cache_read": 0.19, "cache_write": 0},
            "context_window": 1048576,
            "max_tokens": 32768,
        }
    ]
    models = await kimi_coding_fetch_models(
        _context(credential=_oauth(), catalog_fetch=_station_fetch(fields))
    )
    assert [m.id for m in models] == ["kimi-for-coding"]
    assert models[0].cost.input == 0.95


async def test_managed_transport_error_without_station_returns_none(monkeypatch):
    """managed 传输失败且无站 → None = 本次无发布 + 降级诊断，不炸（传输失败不视为刷新失败）。"""

    async def fake_request(method, url, **kwargs):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_request
    )
    context = _context(credential=_oauth())
    models = await kimi_coding_fetch_models(context)
    assert models is None
    # 传输失败即报（新增——不再静默）+ 无站兜底诊断（既有）
    assert len(context.diagnostics) == 2
    assert "订阅源拉取失败" in context.diagnostics[0].message
    assert "订阅源不可达" in context.diagnostics[1].message


async def test_station_failure_managed_serves_alone_with_diagnostic(monkeypatch):
    """站不可用 + managed 可用 → 订阅源单独供数（价格落 0）+ 能力降级诊断。"""

    async def fake_post(method, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "kimi-for-coding",
                        "context_length": 262144,
                        "supports_reasoning": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "nova_ai.providers.kimi_coding.catalog.request_with_abort", fake_post
    )

    async def _failing(signal, validator):
        return CatalogOutcome(status=500)

    context = _context(credential=_oauth(), catalog_fetch=_failing)
    models = await kimi_coding_fetch_models(context)
    assert [m.id for m in models] == ["kimi-for-coding"]
    # managed 单独服务：价格由订阅折算（IMPLIED_COSTS 经知识应用落位）
    assert models[0].cost.input == 0.95
    assert len(context.diagnostics) == 1
    assert context.diagnostics[0].kind is WarningKind.CAPABILITY
    assert "订阅源单独供数" in context.diagnostics[0].message


async def test_factory_wires_refresh_and_source():
    """kimi-coding 出厂：三件套刷新（fetch_models）+ catalog_source 选配。"""
    provider = kimi_coding_provider()
    assert provider.refresh_models is not None
    assert provider.catalog_source == "pi.dev"
