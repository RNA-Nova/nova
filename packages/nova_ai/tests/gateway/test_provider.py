"""Provider 运行时单元测试（gateway/provider.py 镜像）。

调度语义（对齐 TS createProvider）：同步返回流、按 ``model.api`` 路由、
缺实现时以 error 流收尾而非抛异常。工厂接线冒烟归 providers/ 各家目录。
"""

from typing import Any, Dict, List, Optional

import pytest
from nova_ai.providers import create_provider
from nova_ai.stream_options import StreamOptions
from nova_ai.streaming import (
    AssistantMessageEventStream,
    create_assistant_message_event_stream,
)
from nova_protocol import (
    AssistantMessage,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    Usage,
    UserMessage,
)


class _FakeApiImpl:
    """ProviderStreams 契约形态的假实现（记录调用 + 返回自持事件流）。

    判别方式：返回流实例的**身份**（dispatch 直通不包装）与调用记录。
    """

    def __init__(self, name: str = "fake"):
        self.name = name
        self.calls: List[Dict[str, Any]] = []
        self.last_stream: Optional[AssistantMessageEventStream] = None

    def _open(
        self, method: str, model: Model, options: Any
    ) -> AssistantMessageEventStream:
        self.calls.append({"method": method, "model": model.id, "options": options})
        stream = create_assistant_message_event_stream()
        stream.end(result=_final_message(model, self.name))
        self.last_stream = stream
        return stream

    def stream(self, model, context, options=None):
        return self._open("stream", model, options)

    def stream_simple(self, model, context, options=None):
        return self._open("stream_simple", model, options)


def _final_message(model: Model, marker: str) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=marker,
        usage=Usage(),
        stop_reason=StopReason.STOP,
    )


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
    """create_provider 工厂测试（行为面，不探私有字段）。"""

    def test_minimal(self):
        p = create_provider(id="x", name="X")
        assert p.id == "x"
        assert p.name == "X"
        assert p.get_models() == []

    def test_with_models(self):
        models = [_make_model("m1")]
        p = create_provider(id="x", name="X", models=models)
        assert p.get_models() == models
        # 传入 list 不应被后续修改影响（内部拷贝）
        models.pop()
        assert p.get_models() == [_make_model("m1")]


class TestProviderGetModel:
    """Provider 模型查找测试"""

    def test_get_model(self):
        m1 = _make_model("m1")
        m2 = _make_model("m2")
        p = create_provider(id="x", name="X", models=[m1, m2])
        assert p.get_model("m1") == m1
        assert p.get_model("m2") == m2
        assert p.get_model("missing") is None


@pytest.mark.asyncio
class TestProviderStreamDispatch:
    """Provider.stream / stream_simple 调度测试"""

    async def test_stream_single_impl(self):
        api = _FakeApiImpl()
        p = create_provider(id="x", name="X", api=api)
        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        options = StreamOptions()

        stream = p.stream(model, ctx, options=options)

        assert stream is api.last_stream  # dispatch 直通不包装
        assert api.calls == [{"method": "stream", "model": "test", "options": options}]
        result = await stream.result()
        assert result.stop_reason == StopReason.STOP

    async def test_stream_simple_single_impl(self):
        api = _FakeApiImpl()
        p = create_provider(id="x", name="X", api=api)
        stream = p.stream_simple(
            _make_model(), Context(messages=[UserMessage(content="hi")])
        )
        assert stream is api.last_stream
        assert api.calls[0]["method"] == "stream_simple"

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
        ctx = Context(messages=[UserMessage(content="hi")])

        stream = p.stream(_make_model(api=KnownApi.OPENAI_COMPLETIONS), ctx)
        assert stream is completions.last_stream
        assert len(responses.calls) == 0

        stream = p.stream(_make_model(api=KnownApi.OPENAI_RESPONSES), ctx)
        assert stream is responses.last_stream
        assert len(completions.calls) == 1
        assert len(responses.calls) == 1

    async def test_stream_missing_impl_returns_error_stream(self):
        p = create_provider(id="x", name="X")
        # 对齐 TS createProvider dispatch：不抛异常，返回携带 error 事件的流
        stream = p.stream(_make_model(), Context(messages=[UserMessage(content="hi")]))
        result = await stream.result()
        assert result.stop_reason == StopReason.ERROR
        assert "no API implementation" in (result.error_message or "")

    async def test_stream_dict_missing_api_returns_error_stream(self):
        p = create_provider(id="x", api={"other-api": _FakeApiImpl()}, name="X")
        model = _make_model(api=KnownApi.OPENAI_COMPLETIONS)

        stream = p.stream(model, Context(messages=[UserMessage(content="hi")]))
        result = await stream.result()
        assert result.stop_reason == StopReason.ERROR
        assert "no API implementation" in (result.error_message or "")

    async def test_sync_dispatch_failure_becomes_error_stream(self):
        class BrokenApi:
            def stream(self, model, context, options=None):
                raise RuntimeError("dispatch boom")

            def stream_simple(self, model, context, options=None):
                raise RuntimeError("dispatch boom")

        provider = create_provider(id="x", api=BrokenApi(), name="X")
        stream = provider.stream(_make_model(), Context(messages=[]))
        result = await stream.result()
        assert result.stop_reason == StopReason.ERROR
        assert "dispatch boom" in (result.error_message or "")

    async def test_image_model_cannot_enter_chat_dispatch(self):
        from nova_protocol import ImageModel

        api = _FakeApiImpl()
        provider = create_provider(id="test", name="Test", api=api)
        image = ImageModel(
            id="image",
            name="Image",
            type="image",
            api="openai-completions",
            provider="test",
            base_url="https://example.com",
            input_types=["text"],
            output_types=["image"],
            cost=ModelCost(),
        )
        from typing import cast

        # 故意绕过静态类型，验证公共运行时的 chat 守卫。
        result = await provider.stream(
            cast(Model, image), Context(messages=[])
        ).result()
        assert result.stop_reason == StopReason.ERROR
        assert "not a chat model" in (result.error_message or "")
        assert api.calls == []
