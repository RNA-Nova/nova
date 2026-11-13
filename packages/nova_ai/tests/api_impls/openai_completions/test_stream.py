"""openai_completions stream 流式解析测试（respx 假 LLM——假在传输、真在协议）。

除注明保留的用例外一律过线：真 SDK 客户端 + respx 拦 HTTP（设施在
tests/_helpers/fake_llm.py），序列化/反序列化全真跑。断言两面：产出的事件流
（"回来的"）与 capture 捕获的请求体（"发出去的"）。

保留 ``create_client`` patch 的用例（被测主体即 client 创建/注入行为本身，
或场景被 SDK 解析形状卡住、线级设施表达不了）逐处注释理由。
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any, List, Optional

import httpx
import pytest
import respx
from nova_protocol import (
    Context,
    DoneEvent,
    ErrorEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    NovaErrorKind,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    UserMessage,
)

# 注意：patch 目标是流式实现子模块（create_client 的消费方绑定处）——仅
# 注明理由的保留用例使用，其余一律过线
import nova_ai.api_impls.openai_completions._stream as stream_module
from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions, stream
from tests._helpers.fake_llm import (
    RequestCapture,
    mount_openai_chat,
    mount_openai_hanging_request,
    mount_openai_stream,
    mount_openai_transport_error,
    oai_chunk,
    oai_scripted_stream,
    oai_tool_call,
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


# ---------------------------------------------------------------------------
# 保留用例的假件（不过线——逐用例注释理由；新用例一律走 fake_llm 设施）
# ---------------------------------------------------------------------------


def _chunk(
    content: Optional[str] = None,
    reasoning: Optional[str] = None,
    finish: Optional[str] = None,
    tool_calls: Optional[List[Any]] = None,
    reasoning_details: Optional[List[Any]] = None,
    usage: Optional[Any] = None,
):
    """构造 ChatCompletionChunk——线级 JSON 经真 SDK 解析（SimpleNamespace
    假形状会骗过实现层，F1/F2 教训：假件一律 SDK model_validate 产出）。

    ``usage`` 传线级 dict（SDK 模型实例亦可——模型字段直通）。
    """
    from openai.types.chat import ChatCompletionChunk

    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    if reasoning_details:
        delta["reasoning_details"] = reasoning_details
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls

    wire: dict = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        wire["usage"] = usage
    return ChatCompletionChunk.model_validate(wire)


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
        self.closed = False

    async def _create_with_raw_response(self, **_kwargs):
        return _FakeRawResponse(self._chunks)

    async def close(self):
        self.closed = True


async def _collect(stream):
    events = []
    async for event in stream:
        events.append(event)
    return events


class TestStreamToolCalls:
    @pytest.mark.asyncio
    @respx.mock
    async def test_single_tool_call(self):
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
                oai_chunk(tool_calls=[oai_tool_call(0, arguments='{"q"')]),
                oai_chunk(tool_calls=[oai_tool_call(0, arguments=':"test"}')]),
                oai_chunk(finish="tool_calls"),
            ],
            capture,
        )

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

        # 请求体面（过线后"发出去的"可见）
        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"
        assert body["stream"] is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_multiple_tool_calls(self):
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(
                    tool_calls=[
                        oai_tool_call(0, id="tc1", name="search"),
                        oai_tool_call(1, id="tc2", name="read"),
                    ]
                ),
                oai_chunk(
                    tool_calls=[
                        oai_tool_call(0, arguments='{"q":"a"}'),
                        oai_tool_call(1, arguments='{"file":"b"}'),
                    ]
                ),
                oai_chunk(finish="tool_calls"),
            ],
            capture,
        )

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

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"

    @pytest.mark.asyncio
    @respx.mock
    async def test_tool_call_id_late_arrival(self):
        """toolCall id 在 arguments 之后到达。"""
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(tool_calls=[oai_tool_call(0, name="search")]),
                oai_chunk(tool_calls=[oai_tool_call(0, arguments='{"q"')]),
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", arguments=':"x"}')]),
                oai_chunk(finish="tool_calls"),
            ],
            capture,
        )

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert ends[0].tool_call.id == "tc1"

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"


class TestStreamReasoningDetails:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reasoning_precedes_content_in_same_delta(self):
        """同一 delta 混合 reasoning + content：thinking 块先于文本块
        （对齐 TS——LiteLLM 聚合场景的块序契约）。"""
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(reasoning="想", content="答"),
                oai_chunk(finish="stop"),
            ],
            capture,
        )
        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        result = await event_stream.result()

        types = [b.type for b in result.content]
        assert types.index("thinking") < types.index("text")

    @pytest.mark.asyncio
    @respx.mock
    async def test_reasoning_details_pending(self):
        """reasoning_details 在 toolCall 之前到达：归档进 thinking 块签名。

        （终态契约：details 拼接后存 thinking_signature 的 JSON 数组，
        不再挂载 toolCall.thought_signature——跨模型重放随块保留。）
        """
        detail = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(reasoning_details=[detail]),  # 先到
                # 后到
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
                oai_chunk(finish="tool_calls"),
            ],
            capture,
        )

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        thinking_ends = [e for e in events if isinstance(e, ThinkingEndEvent)]
        assert len(thinking_ends) == 1
        thinking_blocks = [
            b for b in thinking_ends[0].partial.content if b.type == "thinking"
        ]
        signature = thinking_blocks[0].thinking_signature
        assert signature is not None
        archived = json.loads(signature)
        assert archived == [detail]
        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert not ends[0].tool_call.thought_signature

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"

    @pytest.mark.asyncio
    @respx.mock
    async def test_reasoning_details_direct(self):
        """reasoning_details 在 toolCall 之后到达：同样归档进 thinking 签名。"""
        detail = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
                oai_chunk(reasoning_details=[detail]),
                oai_chunk(finish="tool_calls"),
            ],
            capture,
        )

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        thinking_ends = [e for e in events if isinstance(e, ThinkingEndEvent)]
        assert len(thinking_ends) == 1
        thinking_blocks = [
            b for b in thinking_ends[0].partial.content if b.type == "thinking"
        ]
        signature = thinking_blocks[0].thinking_signature
        assert signature is not None
        archived = json.loads(signature)
        assert archived == [detail]
        ends = [e for e in events if isinstance(e, ToolCallEndEvent)]
        assert len(ends) == 1
        assert not ends[0].tool_call.thought_signature

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"


class TestStreamUsage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_usage_parsing(self):
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(content="hi"),
                oai_chunk(
                    finish="stop",
                    usage={
                        "prompt_tokens": 1000,
                        "completion_tokens": 100,
                        "prompt_tokens_details": {
                            "cached_tokens": 200,
                            "cache_write_tokens": 50,
                        },
                        "completion_tokens_details": {"reasoning_tokens": 30},
                    },
                ),
            ],
            capture,
        )

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

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"

    @pytest.mark.asyncio
    @respx.mock
    async def test_choice_usage_fallback(self):
        """某些 provider 在 choice.usage 里返回 usage（Moonshot 先例）。

        线级回归锁（F2）：choice 级 usage 经 SDK 解析落地为裸 dict
        （pydantic extra 残余形状）——生产入口的 ``_namespace`` 归一让它
        与类型化对象同读法。
        """
        capture = RequestCapture()
        chunk = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "hi"},
                    "finish_reason": "stop",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                }
            ],
        }
        mount_openai_chat([chunk], capture)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        done = [e for e in events if isinstance(e, DoneEvent)]
        assert len(done) == 1
        assert done[0].message.usage.input == 100
        assert done[0].message.usage.output == 10


class TestStreamAbort:
    @pytest.mark.asyncio
    @respx.mock
    async def test_abort_signal(self):
        from nova_protocol import AbortController

        controller = AbortController()
        # 首帧后挂起的受控流：消费者见 TextDelta 举旗时流确定在飞（原 0.01s
        # 节拍睡眠的确定性替代——Event 受控完成序）
        byte_stream = oai_scripted_stream([oai_chunk(content="part1")], hang=True)
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

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
        assert len(errors) == 1
        assert errors[0].reason == "aborted"
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_abort_closes_blocked_stream(self):
        """上游无数据时，abort 后流被关闭（finally 清理段），而非干等下一个 chunk。

        过线版断言更强：``closed`` 观测口在传输层字节流上——SDK 的
        ``stream.close()`` 沿 response 一路关到字节流，全真跑。
        """
        from nova_protocol import AbortController

        byte_stream = oai_scripted_stream([], hang=True)
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

        controller = AbortController()
        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        async def _abort_when_blocked():
            # 确认消费者已阻塞在挂起字节流的读上再举旗（不用 sleep 表达等待）
            await byte_stream.waiting.wait()
            controller.abort()

        asyncio.create_task(_abort_when_blocked())

        # 看门狗失效时这里会超时
        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)
        assert byte_stream.closed.is_set()
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"

    @pytest.mark.asyncio
    async def test_abort_during_create_closes_client_and_returns(self, monkeypatch):
        """create 阶段（响应头未到）abort：竞速即时返回 + 看门狗关 client
        杀在飞请求（connect→TTFB 盲区治理）。

        保留 create_client patch（不过线）的理由：被测主体含"finally 关自建
        client"——``client.close()`` 是 SDK 客户端对象的行为，线级设施
        （respx 拦传输）没有观测口；本用例即 client 生命周期治理本身。
        """
        from nova_protocol import AbortController

        started = asyncio.Event()
        never = asyncio.Event()
        close_called = asyncio.Event()

        async def _create(**_kwargs):
            started.set()
            await never.wait()
            raise AssertionError("看门狗生效时 create 不会等到结果")

        async def _close():
            close_called.set()

        fake = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=_create)
                )
            ),
            close=_close,
        )
        monkeypatch.setattr(stream_module, "create_client", lambda *a, **k: fake)

        controller = AbortController()
        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        await started.wait()  # 确认请求已在飞（create 内挂起）再举旗
        controller.abort()

        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"
        assert close_called.is_set()  # 在飞请求被看门狗杀（client 关闭）
        assert not any(isinstance(e, DoneEvent) for e in events)  # 收尾不发布

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_truly_killed_on_abort(self):
        """create 阶段 abort：在飞请求被 scope **真杀**（传输层 handler 亲见
        CancelledError），不存在迟到响应（任何补关/parse 都无对象）。"""
        from nova_protocol import AbortController

        capture = RequestCapture()
        started, killed = mount_openai_hanging_request(capture)

        controller = AbortController()
        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        await started.wait()  # 确认请求已在飞（handler 内挂起）再举旗
        controller.abort()

        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"
        assert killed.is_set()  # 真杀——不是"不等了"，是代码死了
        assert not any(isinstance(e, DoneEvent) for e in events)  # 收尾不发布


class TestStreamIdleTimeout:
    @pytest.mark.asyncio
    @respx.mock
    async def test_idle_stream_dies_with_request_timeout_kind(self):
        """SSE 长沉默（服务端半死）：idle 看门狗处死当前流——死因
        REQUEST_TIMEOUT（错误终态，不是取消；回合层按可重试裁决）。"""
        byte_stream = oai_scripted_stream([oai_chunk(content="part1")], hang=True)
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

        options = OpenAICompletionsOptions(api_key="sk-test", timeout=0.05)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )
        events = await asyncio.wait_for(_collect(event_stream), timeout=2.0)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "error"  # 超时是错误终态，不是取消
        assert errors[0].error.error_kind is NovaErrorKind.REQUEST_TIMEOUT
        assert byte_stream.closed.is_set()  # finally 收尾关流（放行挂起字节流）


class TestStreamError:
    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_finish_reason(self):
        capture = RequestCapture()
        mount_openai_chat(
            [oai_chunk(content="partial"), oai_chunk(content=" data")],
            capture,
        )

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
        assert "finish_reason" in (errors[0].error.error_message or "")
        assert errors[0].error.error_kind is NovaErrorKind.STREAM_DISCONNECTED

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"

    @pytest.mark.asyncio
    @respx.mock
    async def test_response_model_and_raw_stop_reason(self):
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(content="hi", model="upstream-model"),
                oai_chunk(finish="stop", model="upstream-model"),
            ],
            capture,
        )
        result = await stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        ).result()
        assert result.response_model == "upstream-model"
        assert result.raw_stop_reason == "stop"

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("with_tool", [False, True])
    async def test_missing_finish_reason_uses_content_when_compat_allows(
        self, with_tool
    ):
        from nova_protocol import OpenAICompletionsCompat

        tool_chunks = (
            [
                oai_chunk(
                    tool_calls=[oai_tool_call(id="call-1", name="read", arguments="{}")]
                )
            ]
            if with_tool
            else []
        )
        capture = RequestCapture()
        mount_openai_chat([oai_chunk(content="hi"), *tool_chunks], capture)
        model = _model().model_copy(
            update={"compat": OpenAICompletionsCompat(supports_finish_reason=False)}
        )
        result = await stream(
            model,
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        ).result()
        assert result.stop_reason is (
            StopReason.TOOL_USE if with_tool else StopReason.STOP
        )
        assert result.raw_stop_reason is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_options_retry_reaches_real_openai_stream(self):
        success = (
            f"data: {json.dumps(oai_chunk(content='ok', finish='stop'))}\n\n"
            "data: [DONE]\n\n"
        ).encode()
        route = respx.post(url__regex=r".*/chat/completions$").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after-ms": "1"}),
                httpx.Response(
                    200,
                    content=success,
                    headers={"content-type": "text/event-stream"},
                ),
            ]
        )
        result = await stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(
                api_key="sk-test", max_retries=1, max_retry_delay_ms=0
            ),
        ).result()
        assert result.stop_reason is StopReason.STOP
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_content_filter_error(self):
        capture = RequestCapture()
        mount_openai_chat(
            [oai_chunk(content="hi", finish="content_filter")],
            capture,
        )

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
        # 内容过滤无自然归类——归一落 INTERNAL（未分类，不硬套政策类）
        assert errors[0].error.error_kind is NovaErrorKind.INTERNAL
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_finish_reason_kind(self):
        """finish_reason=network_error：预标 STREAM_DISCONNECTED（供应商自述断流）。"""
        capture = RequestCapture()
        mount_openai_chat(
            [oai_chunk(content="hi", finish="network_error")],
            capture,
        )

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.error_kind is NovaErrorKind.STREAM_DISCONNECTED
        assert len(capture) == 1


class TestStreamInterleaved:
    @pytest.mark.asyncio
    @respx.mock
    async def test_interleaved_reasoning_and_content(self):
        """reasoning/content 交错合并为一个 thinking 块 + 一个 text 块。"""
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(reasoning="r1"),
                oai_chunk(reasoning="r2"),
                oai_chunk(content="t1"),
                oai_chunk(reasoning="r3"),
                oai_chunk(content="t2"),
                oai_chunk(finish="stop"),
            ],
            capture,
        )

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
        assert isinstance(blocks[0], ThinkingContent)
        assert blocks[0].thinking == "r1r2r3"
        assert isinstance(blocks[1], TextContent)
        assert blocks[1].text == "t1t2"

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"


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
    @respx.mock
    async def test_normal_done_pairing(self):
        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(reasoning="think"),
                oai_chunk(content="hello"),
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
                oai_chunk(tool_calls=[oai_tool_call(0, arguments='{"q":"x"}')]),
                oai_chunk(
                    finish="tool_calls",
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                ),
            ],
            capture,
        )

        event_stream = stream(
            _model(reasoning=True),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        _assert_start_end_pairing(events)
        assert events[-1].type == "done"

        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "test-model"

    @pytest.mark.asyncio
    @respx.mock
    async def test_abort_pairing(self):
        """abort 时所有打开的块都有且仅有一个 end，error 收尾。"""
        from nova_protocol import AbortController

        controller = AbortController()
        # 三块打开后挂起：消费者见 TextDelta 举旗时三块确定在飞
        byte_stream = oai_scripted_stream(
            [
                oai_chunk(reasoning="thinking"),
                oai_chunk(content="part1"),
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
            ],
            hang=True,
        )
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

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
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_network_error_pairing(self):
        """流中途网络错误：已打开的块同样补齐 end 再 error。"""
        byte_stream = oai_scripted_stream(
            [
                oai_chunk(content="part1"),
                oai_chunk(tool_calls=[oai_tool_call(0, id="tc1", name="search")]),
                oai_chunk(tool_calls=[oai_tool_call(0, arguments='{"q')]),
            ],
            error=ConnectionError("connection reset"),
        )
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        _assert_start_end_pairing(events)
        assert events[-1].type == "error"
        assert events[-1].reason == "error"
        # 归一上 kind：内建 ConnectionError → HTTP_CONNECTION_FAILED（可重试类）
        assert events[-1].error.error_kind is NovaErrorKind.HTTP_CONNECTION_FAILED
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_early_request_failure_no_blocks(self):
        """请求阶段就失败（无内容块）：只产出一个 error 事件，不崩。

        过线表达：连接层失败（connect 即被拒）——请求阶段失败的真实形态；
        归一上 kind HTTP_CONNECTION_FAILED（可重试类）。
        """
        capture = RequestCapture()
        mount_openai_transport_error(httpx.ConnectError("refused"), capture)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].reason == "error"
        assert events[0].error.error_kind is NovaErrorKind.HTTP_CONNECTION_FAILED
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_watchdog_abort_pairing(self):
        """看门狗关闭路径同样保证配对（text + thinking 块）。

        举旗时机从 0.05s 引导睡眠改为消费者见 TextDelta 即举旗——流挂起
        保证此时两块确定在飞（Event 受控完成序，确定性等价）。
        """
        from nova_protocol import AbortController

        controller = AbortController()
        byte_stream = oai_scripted_stream(
            [
                oai_chunk(reasoning="thinking"),
                oai_chunk(content="part1"),
            ],
            hang=True,
        )
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

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
        assert len(capture) == 1


class TestStreamSimpleGuards:
    @pytest.mark.asyncio
    async def test_no_api_key_and_no_env_returns_error_stream(self, monkeypatch):
        """协议直接入口的鉴权失败也进入事件流，不在调用时同步抛出。"""
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.stream_options import SimpleStreamOptions

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        events = stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            SimpleStreamOptions(),
        )
        result = await events.result()
        assert result.stop_reason is StopReason.ERROR
        assert "No API key for provider" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_env_key_does_not_rescue_protocol_layer(self, monkeypatch):
        """协议层不读环境变量；env 注入仍是上游 ModelsManager 的职责。"""
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.stream_options import SimpleStreamOptions

        monkeypatch.setenv("OPENAI_API_KEY", "env-key-present")
        events = stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            SimpleStreamOptions(),
        )
        result = await events.result()
        assert result.stop_reason is StopReason.ERROR
        assert "No API key for provider" in (result.error_message or "")

    @pytest.mark.asyncio
    @respx.mock
    async def test_authorization_header_satisfies_key_requirement(self, monkeypatch):
        """headers 里带 authorization 时不需要 api_key（对齐 TS）。"""
        from nova_ai.api_impls.openai_completions import stream_simple
        from nova_ai.stream_options import SimpleStreamOptions

        capture = RequestCapture()
        mount_openai_chat(
            [
                oai_chunk(
                    content="ok",
                    finish="stop",
                    usage={"prompt_tokens": 5, "completion_tokens": 3},
                )
            ],
            capture,
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        event_stream = stream_simple(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            SimpleStreamOptions(headers={"Authorization": "Bearer sk-x"}),
        )
        events = await _collect(event_stream)
        assert events[-1].type == "done"
        # 过线后可见：调用方的 authorization 头确实随请求发出
        request = capture.single()
        assert request.headers["authorization"] == "Bearer sk-x"


class TestClientLifecycle:
    """客户端按次现造、用完即关（finally 里 close——openai 2.x 异步关闭方法）——
    连接池的异步生成器不得活到事件循环拆除（冻结态进程收尾的 athrow 噪音根因）。

    保留 create_client patch（不过线）的理由：被测主体即 client 创建/关闭
    行为本身——"client 被关闭"的观测口只在假 client 上，线级设施给不出。
    """

    @pytest.mark.asyncio
    async def test_client_closed_after_done(self, monkeypatch):
        chunks = [_chunk(content="ok", finish="stop")]
        holder = {}

        def _make(*_a, **_k):
            holder["client"] = _FakeClient(chunks)
            return holder["client"]

        monkeypatch.setattr(stream_module, "create_client", _make)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        assert events[-1].type == "done"
        assert holder["client"].closed is True

    @pytest.mark.asyncio
    async def test_client_closed_on_error(self, monkeypatch):
        """错误路径（finally 兜底）同样关闭。"""
        holder = {}

        class _BoomClient(_FakeClient):
            def __init__(self):
                super().__init__([])
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(
                        with_raw_response=SimpleNamespace(create=self._boom)
                    )
                )

            async def _boom(self, **_k):
                raise RuntimeError("boom")

        def _make(*_a, **_k):
            holder["client"] = _BoomClient()
            return holder["client"]

        monkeypatch.setattr(stream_module, "create_client", _make)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        assert events[-1].type == "error"
        assert holder["client"].closed is True


class TestStreamErrorTerminal:
    """catch 转换点（_stream.py）回归——取消与错误分家 + retry_after_ms 上消息。"""

    @pytest.mark.asyncio
    @respx.mock
    async def test_aborted_error_type_fallback_when_flag_unset(self):
        """AbortedError 在旗标未置位时到达（看门狗竞态）：仍判取消终态——
        不上 kind/retry_after，不归一（铁律三的类型兜底）。

        过线表达：响应头到达后、首帧前字节流即抛 AbortedError——传输层
        中途注入不被 SDK 包装（实测直达），与看门狗竞态同形。
        """
        from nova_protocol import AbortedError

        byte_stream = oai_scripted_stream([], error=AbortedError("Request was aborted"))
        capture = RequestCapture()
        mount_openai_stream(byte_stream, capture)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"
        assert errors[0].error.error_kind is None
        assert errors[0].error.retry_after_ms is None
        assert errors[0].error.error_message == "Request was aborted"
        assert len(capture) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_retry_after_ms_reaches_message(self):
        """429 的 retry-after-ms 头经归一上消息（int 毫秒）。

        线级回归锁（F1）：真 SDK 的 ``APIStatusError`` 无 ``.headers``——
        头在 ``.response.headers``，归一经 ``headers_of`` 边界归一读取。
        """
        from nova_protocol import NovaErrorKind

        capture = RequestCapture()
        mount_openai_chat([], capture, status=429, headers={"retry-after-ms": "1500"})
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "error"
        assert errors[0].error.error_kind is NovaErrorKind.RATE_LIMITED
        assert errors[0].error.retry_after_ms == 1500

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_without_retry_after_leaves_none(self):
        """无退避指引的错误：retry_after_ms 保持 None（缺席语义）。"""
        capture = RequestCapture()
        mount_openai_chat([], capture, status=500)

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(api_key="sk-test"),
        )
        events = await _collect(event_stream)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.retry_after_ms is None
        assert len(capture) == 1

    @pytest.mark.asyncio
    async def test_missing_api_key_login_required(self):
        """无凭据守卫（create_client 拒绝）：LOGIN_REQUIRED 上消息 + 可行动文案。"""
        from nova_protocol import NovaErrorKind

        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            OpenAICompletionsOptions(),  # 无 api_key
        )
        events = await _collect(event_stream)
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.error_kind is NovaErrorKind.LOGIN_REQUIRED
        assert "/login" in (errors[0].error.error_message or "")


class TestStreamHooks:
    @pytest.mark.asyncio
    @respx.mock
    async def test_on_response_headers_filtered(self):
        """on_response 收到**已过滤**的响应头（set-cookie/x-api-key 等凭据类
        永不出钩子的手——security 出口纪律）。"""
        seen = []
        capture = RequestCapture()
        mount_openai_chat(
            [oai_chunk(finish="stop")],
            capture,
            headers={"set-cookie": "session=secret", "x-api-key": "sk-secret"},
        )

        def on_response(response, _model):
            seen.append(response)

        options = OpenAICompletionsOptions(api_key="sk-test", on_response=on_response)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )
        await _collect(event_stream)
        assert len(seen) == 1
        assert seen[0].status == 200
        # 敏感头被过滤，普通头保留（真传输会补 content-length 等，不做全等）
        assert "set-cookie" not in seen[0].headers
        assert "x-api-key" not in seen[0].headers
        assert seen[0].headers["content-type"] == "text/event-stream"
        assert len(capture) == 1


class TestDoneThenAbortRace:
    @pytest.mark.asyncio
    @respx.mock
    async def test_abort_after_stream_done_does_not_rewrite_message(self):
        """Done→abort 竞态（回归）：举旗落在"内容已完整、终态已发布"的窗口
        ——已发布消息不得被改写为 ABORTED，也不得有 ErrorEvent 顶掉 DoneEvent。

        过线版编排：消费者收到 DoneEvent 即举旗——此刻 ``event_stream.done``
        已置位，迟到的取消无论落在 ``raise_if_aborted`` 检查前后都被同一
        守卫拦住（两种时序共享同一断言集，确定性绿；原假流在最后一个
        __anext__ 内同步引爆同属该窗口）。
        """
        from nova_protocol import AbortController

        controller = AbortController()
        capture = RequestCapture()
        mount_openai_chat([oai_chunk(content="hi", finish="stop")], capture)

        options = OpenAICompletionsOptions(api_key="sk-test", signal=controller.signal)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            options,
        )

        events = []
        async for event in event_stream:
            events.append(event)
            if isinstance(event, DoneEvent):
                controller.abort()

        assert any(isinstance(e, DoneEvent) for e in events)
        assert not any(isinstance(e, ErrorEvent) for e in events)
        result = await event_stream.result()
        assert result.stop_reason == StopReason.STOP  # 不被迟到取消改写
        assert len(capture) == 1
