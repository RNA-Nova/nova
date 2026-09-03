"""
Provider 运行时单元测试

验证 Provider 作为可独立调度的运行时单元（stream/stream_simple）的行为，
以及对齐 TS 的 createProvider / Provider.getModels / Provider.stream 语义。
"""

from typing import Any, AsyncIterator, Dict, List, Optional

import pytest

from nova_ai.providers import (
    Provider,
    ProviderStreams,
    builtin_providers,
    create_provider,
    volcengine_provider,
)
from nova_ai.types import Context, KnownApi, Model, ModelCost, StopReason, UserMessage


class _FakeApiImpl:
    """用于测试 API 调度的伪实现。"""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.calls: List[Dict[str, Any]] = []

    async def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[Any] = None,
    ) -> AsyncIterator[str]:
        self.calls.append({"method": "stream", "model": model.id, "options": options})
        yield f"{self.name}:stream:{model.id}"

    async def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[Any] = None,
    ) -> AsyncIterator[str]:
        self.calls.append(
            {"method": "stream_simple", "model": model.id, "options": options}
        )
        yield f"{self.name}:stream_simple:{model.id}"


def _make_model(model_id: str = "test", api: KnownApi = KnownApi.OPENAI_COMPLETIONS):
    return Model(
        id=model_id,
        name=model_id,
        api=api,
        provider="test",
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


class TestCreateProvider:
    """create_provider 工厂测试"""

    def test_minimal(self):
        p = create_provider(id="x", name="X")
        assert p.id == "x"
        assert p.name == "X"
        assert p.base_url is None
        assert p.headers is None
        assert p.models == []
        assert p.api_impl is None

    def test_with_models(self):
        models = [_make_model("m1")]
        p = create_provider(id="x", name="X", models=models)
        assert p.get_models() == models
        # 传入 list 不应被后续修改影响（内部拷贝）
        models.pop()
        assert p.get_models() == [_make_model("m1")]

    def test_api_impl_single(self):
        api = _FakeApiImpl()
        p = create_provider(id="x", name="X", api=api)
        assert p.api_impl is api

    def test_api_impl_dict(self):
        api = {"openai-completions": _FakeApiImpl()}
        p = create_provider(id="x", name="X", api=api)
        assert isinstance(p.api_impl, dict)


class TestProviderGetModel:
    """Provider 模型查找测试"""

    def test_get_model(self):
        m1 = _make_model("m1")
        m2 = _make_model("m2")
        p = create_provider(id="x", name="X", models=[m1, m2])
        assert p.get_model("m1") == m1
        assert p.get_model("m2") == m2
        assert p.get_model("missing") is None


class TestProviderStreamDispatch:
    """Provider.stream / stream_simple 调度测试"""

    @pytest.mark.asyncio
    async def test_stream_single_impl(self):
        api = _FakeApiImpl()
        p = create_provider(id="x", name="X", api=api)
        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        chunks = [c async for c in p.stream(model, ctx, options={"temperature": 0.7})]

        assert chunks == ["fake:stream:test"]
        assert len(api.calls) == 1
        assert api.calls[0]["method"] == "stream"
        assert api.calls[0]["options"] == {"temperature": 0.7}

    @pytest.mark.asyncio
    async def test_stream_simple_single_impl(self):
        api = _FakeApiImpl()
        p = create_provider(id="x", name="X", api=api)
        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        chunks = [
            c async for c in p.stream_simple(model, ctx, options={"temperature": 0.5})
        ]

        assert chunks == ["fake:stream_simple:test"]
        assert api.calls[0]["method"] == "stream_simple"

    @pytest.mark.asyncio
    async def test_stream_dict_dispatch(self):
        completions = _FakeApiImpl("completions")
        responses = _FakeApiImpl("responses")
        p = create_provider(
            id="x",
            name="X",
            api={
                KnownApi.OPENAI_COMPLETIONS.value: completions,
                KnownApi.OPENAI_RESPONSES.value: responses,
            },
        )

        completion_model = _make_model(api=KnownApi.OPENAI_COMPLETIONS)
        response_model = _make_model(api=KnownApi.OPENAI_RESPONSES)
        ctx = Context(messages=[UserMessage(content="hi")])

        chunks = [c async for c in p.stream(completion_model, ctx)]
        assert chunks == ["completions:stream:test"]
        assert len(completions.calls) == 1
        assert len(responses.calls) == 0

        chunks = [c async for c in p.stream(response_model, ctx)]
        assert chunks == ["responses:stream:test"]
        assert len(responses.calls) == 1

    @pytest.mark.asyncio
    async def test_stream_missing_impl_returns_error_stream(self):
        p = create_provider(id="x", name="X")
        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        # 对齐 TS createProvider dispatch：不抛异常，返回携带 error 事件的流
        stream = p.stream(model, ctx)
        result = await stream.result()

        assert result.stop_reason == StopReason.ERROR
        assert "no API implementation" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_stream_dict_missing_api_returns_error_stream(self):
        p = create_provider(id="x", name="X", api={"other-api": _FakeApiImpl()})
        model = _make_model(api=KnownApi.OPENAI_COMPLETIONS)

        stream = p.stream(model, Context(messages=[UserMessage(content="hi")]))
        result = await stream.result()

        assert result.stop_reason == StopReason.ERROR
        assert "no API implementation" in (result.error_message or "")


class TestVolcengineProvider:
    """Volcengine provider 集成测试（不触发真实网络）"""

    def test_factory_returns_provider(self):
        p = volcengine_provider()
        assert isinstance(p, Provider)
        assert p.id == "volcengine"
        assert p.name == "Volcengine"
        assert p.base_url == "https://ark.cn-beijing.volces.com/api/v3/"
        assert len(p.get_models()) > 0

    def test_factory_binds_openai_completions(self):
        from nova_ai.api_impls import openai_completions

        p = volcengine_provider()
        assert p.api_impl is openai_completions
        assert callable(p.api_impl.stream)
        assert callable(p.api_impl.stream_simple)

    def test_get_volcengine_model(self):
        from nova_ai.providers.volcengine import get_volcengine_model

        m = get_volcengine_model("deepseek-v4-flash-260425")
        assert m is not None
        assert m.provider == "volcengine"


class TestBuiltinProviders:
    """builtin_providers 聚合测试"""

    def test_returns_list(self):
        providers = builtin_providers()
        assert isinstance(providers, list)
        assert len(providers) >= 1
        assert all(isinstance(p, Provider) for p in providers)
        assert any(p.id == "volcengine" for p in providers)
