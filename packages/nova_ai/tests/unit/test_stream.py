"""openai_completions stream 流式解析测试。"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from nova_ai.api_impls import openai_completions
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    stream,
)
from nova_ai.types import (
    Context,
    DoneEvent,
    ErrorEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessage,
)


def _model(
    model_id: str = "test-model",
    provider: Any = KnownProvider.OPENAI,
    base_url: str = "https://api.openai.com",
    reasoning: bool = False,
) -> Model:
    return Model(
        id=model_id,
        name="Test",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url=base_url,
        reasoning=reasoning,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


def _chunk(
    content: Optional[str] = None,
    reasoning: Optional[str] = None,
    finish: Optional[str] = None,
    tool_calls: Optional[List[Any]] = None,
    reasoning_details: Optional[List[Any]] = None,
    usage: Optional[Any] = None,
):
    """构造伪造的 ChatCompletionChunk。"""
    delta_dict = {}
    if reasoning:
        delta_dict["reasoning_content"] = reasoning
    if reasoning_details:
        delta_dict["reasoning_details"] = reasoning_details

    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda: delta_dict,
    )
    return SimpleNamespace(
        id="chatcmpl-1",
        model="test-model",
        usage=usage,
        choices=[SimpleNamespace(delta=delta, finish_reason=finish, usage=None)],
    )


def _tool_call_delta(
    index: Optional[int] = None,
    id: Optional[str] = None,
    name: Optional[str] = None,
    arguments: Optional[str] = None,
):
    func = None
    if name is not None or arguments is not None:
        func = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=func)


def _usage(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
):
    prompt_details = None
    if cached_tokens or cache_write_tokens:
        prompt_details = SimpleNamespace(
            cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens
        )
    completion_details = None
    if reasoning_tokens:
        completion_details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=prompt_details,
        completion_tokens_details=completion_details,
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for c in self._chunks:
                yield c
                await asyncio.sleep(0.01)

        return _gen()

    async def close(self):
        pass


class _FakeRawResponse:
    def __init__(self, chunks):
        self._chunks = chunks
        self.status_code = 200
        self.headers = {}

    def parse(self):
        return _FakeStream(self._chunks)


class _FakeWithRawResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **_kwargs):
        return _FakeRawResponse(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(
                    create=self._create_with_raw_response
                ),
            )
        )
        self._chunks = chunks

    async def _create_with_raw_response(self, **_kwargs):
        return _FakeRawResponse(self._chunks)

    async def close(self):
        pass


async def _collect(stream):
    events = []
    async for event in stream:
        events.append(event)
    return events


def _setup_fake_client(monkeypatch, chunks):
    monkeypatch.setattr(
        openai_completions, "create_client", lambda *a, **k: _FakeClient(chunks)
    )


class TestStreamToolCalls:
    @pytest.mark.asyncio
    async def test_single_tool_call(self, monkeypatch):
        chunks = [
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]),
            _chunk(tool_calls=[_tool_call_delta(index=0, arguments='{"q"')]),
            _chunk(tool_calls=[_tool_call_delta(index=0, arguments=':"test"}')]),
            _chunk(finish="tool_calls"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
        assert len(starts) == 1

        deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        assert len(deltas) == 3
        assert deltas[0].delta == ""
        assert deltas[1].delta == '{"q"'
        assert deltas[2].delta == ':"test"}'

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call.id == "tc1"
        assert ends[0].tool_call.name == "search"
        assert ends[0].tool_call.arguments == {"q": "test"}

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self, monkeypatch):
        chunks = [
            _chunk(
                tool_calls=[
                    _tool_call_delta(index=0, id="tc1", name="search"),
                    _tool_call_delta(index=1, id="tc2", name="read"),
                ]
            ),
            _chunk(
                tool_calls=[
                    _tool_call_delta(index=0, arguments='{"q":"a"}'),
                    _tool_call_delta(index=1, arguments='{"file":"b"}'),
                ]
            ),
            _chunk(finish="tool_calls"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
        assert len(starts) == 2

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 2
        assert ends[0].tool_call.name == "search"
        assert ends[1].tool_call.name == "read"

    @pytest.mark.asyncio
    async def test_tool_call_id_late_arrival(self, monkeypatch):
        """toolCall id 在 arguments 之后到达。"""
        chunks = [
            _chunk(tool_calls=[_tool_call_delta(index=0, name="search")]),
            _chunk(tool_calls=[_tool_call_delta(index=0, arguments='{"q"')]),
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", arguments=':"x"}')]),
            _chunk(finish="tool_calls"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call.id == "tc1"


class TestStreamReasoningDetails:
    @pytest.mark.asyncio
    async def test_reasoning_details_pending(self, monkeypatch):
        """reasoning_details 在 toolCall 之前到达：pending 后补。"""
        detail = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        chunks = [
            _chunk(reasoning_details=[detail]),  # 先到
            _chunk(
                tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]
            ),  # 后到
            _chunk(finish="tool_calls"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call.thought_signature == json.dumps(detail)

    @pytest.mark.asyncio
    async def test_reasoning_details_direct(self, monkeypatch):
        """reasoning_details 在 toolCall 之后到达：直接应用。"""
        detail = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        chunks = [
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]),
            _chunk(reasoning_details=[detail]),
            _chunk(finish="tool_calls"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call.thought_signature == json.dumps(detail)


class TestStreamUsage:
    @pytest.mark.asyncio
    async def test_usage_parsing(self, monkeypatch):
        usage = _usage(
            prompt_tokens=1000,
            completion_tokens=100,
            cached_tokens=200,
            cache_write_tokens=50,
            reasoning_tokens=30,
        )
        chunks = [
            _chunk(content="hi"),
            _chunk(finish="stop", usage=usage),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1
        usage = done[0].message.usage
        assert usage.input == 750  # 1000 - 200 - 50
        assert usage.output == 100
        assert usage.cache_read == 200
        assert usage.cache_write == 50
        assert usage.reasoning == 30
        assert usage.total_tokens == 1100

    @pytest.mark.asyncio
    async def test_choice_usage_fallback(self, monkeypatch):
        """某些 provider 在 choice.usage 里返回 usage。"""
        usage = _usage(prompt_tokens=100, completion_tokens=10)
        chunk = _chunk(content="hi", finish="stop")
        chunk.choices[0].usage = usage
        chunks = [chunk]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1
        assert done[0].message.usage.input == 100


class TestStreamAbort:
    @pytest.mark.asyncio
    async def test_abort_signal(self, monkeypatch):
        from nova_ai import AbortController

        controller = AbortController()
        chunks = [
            _chunk(content="part1"),
            _chunk(content="part2"),
            _chunk(content="part3"),
            _chunk(content="part4"),
            _chunk(content="part5"),
            _chunk(content="part6"),
            _chunk(content="part7"),
            _chunk(content="part8"),
            _chunk(content="part9"),
            _chunk(content="part10"),
            _chunk(finish="stop"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        events = []
        async for event in event_stream:
            events.append(event)
            if isinstance(event, TextDeltaEvent):
                controller.abort()

        # abort 后应该以 error 结束
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) >= 1
        assert errors[0].reason == "aborted"

    @pytest.mark.asyncio
    async def test_watchdog_closes_blocked_stream_on_abort(self, monkeypatch):
        """上游无数据时，abort 看门狗主动 close 流，而非干等下一个 chunk。"""
        from nova_ai import AbortController

        close_called = asyncio.Event()
        never = asyncio.Event()  # 永不触发，模拟上游一直无数据

        class _BlockingStream:
            def __aiter__(self):
                async def _gen():
                    await never.wait()
                    yield None  # close 后解除阻塞，走正常收尾路径

                return _gen()

            async def close(self):
                close_called.set()
                never.set()

        raw = _FakeRawResponse([])
        raw.parse = lambda: _BlockingStream()

        async def _create(**_kwargs):
            return raw

        fake = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=_create)
                )
            )
        )
        monkeypatch.setattr(openai_completions, "create_client", lambda *a, **k: fake)

        controller = AbortController()
        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        async def _abort_soon():
            await asyncio.sleep(0.05)
            controller.abort()

        asyncio.create_task(_abort_soon())

        # 看门狗失效时这里会超时
        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)
        assert close_called.is_set()
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"


class TestStreamError:
    @pytest.mark.asyncio
    async def test_missing_finish_reason(self, monkeypatch):
        chunks = [_chunk(content="partial"), _chunk(content=" data")]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert not done
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert "finish_reason" in errors[0].error.error_message

    @pytest.mark.asyncio
    async def test_content_filter_error(self, monkeypatch):
        chunks = [_chunk(content="hi", finish="content_filter")]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "error"
        assert "content_filter" in errors[0].error.error_message


class TestStreamInterleaved:
    @pytest.mark.asyncio
    async def test_interleaved_reasoning_and_content(self, monkeypatch):
        """reasoning/content 交错合并为一个 thinking 块 + 一个 text 块。"""
        chunks = [
            _chunk(reasoning="r1"),
            _chunk(reasoning="r2"),
            _chunk(content="t1"),
            _chunk(reasoning="r3"),
            _chunk(content="t2"),
            _chunk(finish="stop"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        thinking_starts = [e for e in events if isinstance(e, ThinkingStartEvent)]
        text_starts = [e for e in events if isinstance(e, TextStartEvent)]
        assert len(thinking_starts) == 1
        assert len(text_starts) == 1

        thinking_ends = [e for e in events if isinstance(e, ThinkingEndEvent)]
        text_ends = [e for e in events if isinstance(e, TextEndEvent)]
        assert len(thinking_ends) == 1
        assert thinking_ends[0].content == "r1r2r3"
        assert len(text_ends) == 1
        assert text_ends[0].content == "t1t2"

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1
        blocks = done[0].message.content
        assert [b.type for b in blocks] == ["thinking", "text"]
        assert blocks[0].thinking == "r1r2r3"
        assert blocks[1].text == "t1t2"


def _assert_start_end_pairing(events):
    """事件配对断言：每个 start 恰好一个 end，不重不漏，done/error 收尾。"""
    starts = {}
    ends = {}
    for e in events:
        etype = e.type
        if etype in ("text_start", "thinking_start", "toolcall_start"):
            starts.setdefault(e.content_index, []).append(etype)
        elif etype in ("text_end", "thinking_end", "toolcall_end"):
            ends.setdefault(e.content_index, []).append(etype)

    assert set(starts.keys()) == set(
        ends.keys()
    ), f"start/end 块不配对: starts={starts}, ends={ends}"
    for idx, start_list in starts.items():
        assert (
            len(start_list) == 1
        ), f"content_index={idx} 的 start 事件重复: {start_list}"
    for idx, end_list in ends.items():
        assert len(end_list) == 1, f"content_index={idx} 的 end 事件重复: {end_list}"

    assert events[-1].type in ("done", "error")


class TestEventPairingGuarantee:
    """无论何种终止路径，事件都不重不漏。"""

    @pytest.mark.asyncio
    async def test_normal_done_pairing(self, monkeypatch):
        chunks = [
            _chunk(reasoning="think"),
            _chunk(content="hello"),
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]),
            _chunk(tool_calls=[_tool_call_delta(index=0, arguments='{"q":"x"}')]),
            _chunk(finish="tool_calls", usage=_usage(10, 5)),
        ]
        _setup_fake_client(monkeypatch, chunks)

        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        _assert_start_end_pairing(events)
        assert events[-1].type == "done"

    @pytest.mark.asyncio
    async def test_abort_pairing(self, monkeypatch):
        """abort 时所有打开的块都有且仅有一个 end，error 收尾。"""
        from nova_ai import AbortController

        controller = AbortController()
        chunks = [
            _chunk(reasoning="thinking"),
            _chunk(content="part1"),
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]),
            _chunk(content="part2"),
            _chunk(content="part3"),
        ]
        _setup_fake_client(monkeypatch, chunks)

        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        events = []
        async for event in event_stream:
            events.append(event)
            if isinstance(event, TextDeltaEvent):
                controller.abort()

        _assert_start_end_pairing(events)
        assert events[-1].type == "error"
        assert events[-1].reason == "aborted"

    @pytest.mark.asyncio
    async def test_network_error_pairing(self, monkeypatch):
        """流中途网络错误：已打开的块同样补齐 end 再 error。"""
        chunks = [
            _chunk(content="part1"),
            _chunk(tool_calls=[_tool_call_delta(index=0, id="tc1", name="search")]),
            _chunk(tool_calls=[_tool_call_delta(index=0, arguments='{"q')]),
        ]

        class _ErrorStream:
            def __aiter__(self):
                async def _gen():
                    for c in chunks:
                        yield c
                    raise ConnectionError("connection reset")

                return _gen()

            async def close(self):
                pass

        raw = _FakeRawResponse([])
        raw.parse = lambda: _ErrorStream()

        async def _create(**_kwargs):
            return raw

        fake = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=_create)
                )
            )
        )
        monkeypatch.setattr(openai_completions, "create_client", lambda *a, **k: fake)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        _assert_start_end_pairing(events)
        assert events[-1].type == "error"
        assert events[-1].reason == "error"

    @pytest.mark.asyncio
    async def test_early_request_failure_no_blocks(self, monkeypatch):
        """请求阶段就失败（无内容块）：只产出一个 error 事件，不崩。"""
        monkeypatch.setattr(
            openai_completions,
            "create_client",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auth failed")),
        )

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].reason == "error"

    @pytest.mark.asyncio
    async def test_watchdog_abort_pairing(self, monkeypatch):
        """看门狗关闭路径同样保证配对（text + thinking 块）。"""
        from nova_ai import AbortController

        controller = AbortController()
        never = asyncio.Event()
        first_chunks = [
            _chunk(reasoning="thinking"),
            _chunk(content="part1"),
        ]

        class _StallStream:
            def __init__(self):
                self._sent = False

            def __aiter__(self):
                async def _gen():
                    for c in first_chunks:
                        yield c
                    await never.wait()

                return _gen()

            async def close(self):
                never.set()

        raw = _FakeRawResponse([])
        raw.parse = lambda: _StallStream()

        async def _create(**_kwargs):
            return raw

        fake = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=_create)
                )
            )
        )
        monkeypatch.setattr(openai_completions, "create_client", lambda *a, **k: fake)

        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        async def _abort_soon():
            await asyncio.sleep(0.05)
            controller.abort()

        asyncio.create_task(_abort_soon())

        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)

        _assert_start_end_pairing(events)
        assert events[-1].type == "error"
        assert events[-1].reason == "aborted"


class TestStreamSimpleGuards:
    def test_no_api_key_and_no_env_raises(self, monkeypatch):
        """无 api_key、无 authorization 头、无环境变量时直接抛错（对齐 TS getClientApiKey）。"""
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.types import SimpleStreamOptions

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="No API key for provider"):
            stream_simple(
                _model(),
                Context(messages=[UserMessage(content="hi")]),
                SimpleStreamOptions(),
            )

    def test_env_key_does_not_rescue_protocol_layer(self, monkeypatch):
        """环境变量有 key 时协议层照样抛错：协议层不读环境变量（对齐 TS getClientApiKey）。

        env 注入是上游（Models.applyAuth）的职责；本层只认 options.api_key 与 headers。
        """
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.types import SimpleStreamOptions

        monkeypatch.setenv("OPENAI_API_KEY", "env-key-present")
        with pytest.raises(ValueError, match="No API key for provider"):
            stream_simple(
                _model(),
                Context(messages=[UserMessage(content="hi")]),
                SimpleStreamOptions(),
            )

    @pytest.mark.asyncio
    async def test_authorization_header_satisfies_key_requirement(self, monkeypatch):
        """headers 里带 authorization 时不需要 api_key（对齐 TS）。"""
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.types import SimpleStreamOptions

        chunks = [_chunk(content="ok", finish="stop", usage=_usage(5, 3))]
        _setup_fake_client(monkeypatch, chunks)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        event_stream = stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            SimpleStreamOptions(headers={"Authorization": "Bearer sk-x"}),
        )
        events = await _collect(event_stream)
        assert events[-1].type == "done"
