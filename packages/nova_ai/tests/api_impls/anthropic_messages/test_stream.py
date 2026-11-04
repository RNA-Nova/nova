"""anthropic_messages 流式线协议测试（httpx2 MockTransport 注入——
anthropic SDK 的传输底座是 httpx2，请求构造/事件解析全真跑）。

对位 codex wiremock 层：假在传输、真在协议。设施在 tests/_helpers/fake_llm.py
（``ant_*`` 帧构造器 + ``anthropic_client`` / ``anthropic_client_scripted`` /
``anthropic_stream_client`` + ``RequestCapture``）；断言两面——产出的事件流
（"回来的"）与 capture 捕获的请求体（"发出去的"）。
"""

import asyncio

import pytest
from nova_protocol import (
    AbortController,
    AbortedError,
    Context,
    ErrorEvent,
    KnownApi,
    Model,
    ModelCost,
    NovaErrorKind,
    StopReason,
    Tool,
    UserMessage,
)

from nova_ai.api_impls.anthropic_messages import AnthropicOptions, stream
from tests._helpers.fake_llm import (
    RequestCapture,
    ant_block_start,
    ant_block_stop,
    ant_error,
    ant_error_response,
    ant_input_json_delta,
    ant_message_delta,
    ant_message_start,
    ant_message_stop,
    ant_redacted_thinking_block,
    ant_scripted_stream,
    ant_signature_delta,
    ant_sse_response,
    ant_text_block,
    ant_text_delta,
    ant_text_turn,
    ant_thinking_block,
    ant_thinking_delta,
    ant_tool_use_block,
    anthropic_client,
    anthropic_client_scripted,
    anthropic_stream_client,
)

pytestmark = pytest.mark.asyncio


def _model() -> Model:
    return Model(
        id="claude-x",
        name="x",
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
    )


def _text_flow_frames():
    """一个完整文本 turn 的帧序列（"你好"，input 12 / output 5）。"""
    return [
        ant_message_start(id="msg_1"),
        ant_text_block(0),
        ant_text_delta("你好", 0),
        ant_block_stop(0),
        ant_message_delta("end_turn", 5),
        ant_message_stop(),
    ]


class TestStreamHappyPath:
    async def test_text_flow_events_and_usage(self):
        """文本流：事件序 + usage 双段拼合 + 成本计算 + 响应标识。"""
        capture = RequestCapture()
        client = anthropic_client(_text_flow_frames(), capture)
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e.type async for e in es]
        assert events == ["start", "text_start", "text_delta", "text_end", "done"]
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        assert result.response_id == "msg_1"
        # 实际服务模型与请求一致 → response_model 不落（仅被改写时落，对齐 TS）
        assert result.response_model is None
        assert result.content[0].text == "你好"
        usage = result.usage
        assert (usage.input, usage.output, usage.total_tokens) == (12, 5, 17)
        # 成本 = 12*3 + 5*15 每百万
        assert usage.cost.total == pytest.approx((12 * 3 + 5 * 15) / 1_000_000)

        # 请求体面（"发出去的"）
        body = RequestCapture.body_json(capture.single())
        assert body["model"] == "claude-x"
        assert body["stream"] is True
        assert body["messages"][0]["role"] == "user"

    async def test_thinking_signature_accumulated(self):
        """思考块：thinking_delta 累积文本，signature_delta 累积签名（回放防线）。"""
        frames = [
            ant_message_start(input_tokens=1),
            ant_thinking_block(0, thinking="先", signature=""),
            ant_thinking_delta("想", 0),
            ant_signature_delta("sig-1", 0),
            ant_signature_delta("-2", 0),
            ant_block_stop(0),
            ant_message_delta("end_turn", 3),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        events = [e.type async for e in es]
        # signature_delta 不产生事件（签名非呈现内容）
        assert events == [
            "start",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "done",
        ]
        result = await es.result()
        block = result.content[0]
        assert block.thinking == "先想"
        assert block.thinking_signature == "sig-1-2"
        assert len(capture) == 1

    async def test_tool_use_json_assembled(self):
        """tool_use：input_json_delta 增量拼装，收尾后 arguments 为解析值、
        瞬态缓冲清空。"""
        frames = [
            ant_message_start(input_tokens=1),
            ant_tool_use_block(0, id="toolu_1", name="get_weather"),
            ant_input_json_delta('{"city"', 0),
            ant_input_json_delta(': "bj"}', 0),
            ant_block_stop(0),
            ant_message_delta("tool_use", 2),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(
                messages=[UserMessage(content="hi")],
                tools=[
                    Tool(
                        name="get_weather",
                        description="w",
                        parameters={"type": "object", "properties": {}},
                    )
                ],
            ),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        events = [e.type async for e in es]
        assert events == [
            "start",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_delta",
            "toolcall_end",
            "done",
        ]
        result = await es.result()
        assert result.stop_reason == StopReason.TOOL_USE
        call = result.content[0]
        assert call.arguments == {"city": "bj"}
        assert call.partial_args is None  # 瞬态缓冲清零

        body = RequestCapture.body_json(capture.single())
        assert body["tools"][0]["name"] == "get_weather"

    async def test_redacted_thinking_block(self):
        """redacted_thinking：占位文本 + 不透明载荷收签名位。"""
        frames = [
            ant_message_start(input_tokens=1),
            ant_redacted_thinking_block(0, data="opaque"),
            ant_block_stop(0),
            ant_message_delta("end_turn", 1),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        async for _ in es:
            pass
        result = await es.result()
        block = result.content[0]
        assert block.redacted is True
        assert block.thinking_signature == "opaque"
        assert len(capture) == 1

    async def test_cache_usage_fields(self):
        """message_start 的缓存读写字段进入 usage（含 1h 细粒度）。"""
        frames = [
            ant_message_start(
                input_tokens=10,
                cache_read_input_tokens=7,
                cache_creation_input_tokens=5,
                ephemeral_1h_input_tokens=5,
            ),
            ant_block_start(0, {"type": "text", "text": "x"}),
            ant_block_stop(0),
            ant_message_delta("end_turn", 1),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        async for _ in es:
            pass
        usage = (await es.result()).usage
        assert usage.cache_read == 7
        assert usage.cache_write == 5
        assert usage.cache_write_1h == 5
        assert usage.total_tokens == 23
        assert len(capture) == 1


class TestStreamStopReasons:
    async def test_max_tokens_maps_length(self):
        frames = [
            ant_message_start(input_tokens=1),
            ant_block_start(0, {"type": "text", "text": "x"}),
            ant_block_stop(0),
            ant_message_delta("max_tokens", 1),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        async for _ in es:
            pass
        assert (await es.result()).stop_reason == StopReason.LENGTH
        assert len(capture) == 1

    async def test_refusal_maps_error_with_message(self):
        frames = [
            ant_message_start(input_tokens=1),
            ant_message_delta(
                "refusal", 0, stop_details={"explanation": "不合适的内容"}
            ),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        async for _ in es:
            pass
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert "不合适的内容" in (result.error_message or "")
        assert len(capture) == 1

    async def test_unknown_stop_reason_fails_loud(self):
        """未知 stop_reason：响亮失败（API 新增值尽早暴露——对齐 TS default）。"""
        frames = [
            ant_message_start(input_tokens=1),
            ant_message_delta("brand_new", 0),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        async for _ in es:
            pass
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert "brand_new" in (result.error_message or "")
        assert len(capture) == 1


class TestStreamFailurePaths:
    async def test_truncated_stream_fails_loud(self):
        """有头无 stop_reason：截断响亮失败（SDK 不产出 message_stop——
        终态信号即 stop_reason，见 _stream.py 注释）。"""
        frames = [
            ant_message_start(input_tokens=1),
            ant_block_start(0, {"type": "text", "text": "半截"}),
        ]
        capture = RequestCapture()
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(
                api_key="sk-test", client=anthropic_client(frames, capture)
            ),
        )
        events = [e.type async for e in es]
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert "without a stop reason" in (result.error_message or "")
        # 未闭合的块在异常路径也补发 end（不重不漏）
        assert "text_end" in events
        assert len(capture) == 1

    async def test_429_retried_then_succeeds(self):
        """429（可重试状态）经本层重试后成功——响应带 retry-after-ms: 1 头。

        （注：anthropic SDK 的错误对象不暴露 ``headers`` 属性——只有
        ``response.headers``——本层重试读取 ``error.headers`` 落不到头，
        实际走指数退避；与 openai 侧 retry_after_ms 上消息同属已知缺口。）
        """
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_error_response(
                    429,
                    {
                        "type": "error",
                        "error": {"type": "rate_limit_error", "message": "slow down"},
                    },
                    headers={"retry-after-ms": "1"},
                ),
                ant_sse_response(_text_flow_frames()),
            ],
            capture,
        )
        options = AnthropicOptions(api_key="sk-test", client=client)
        options.max_retries = 2  # 本层重试默认 0（对齐 TS maxRetries ?? 0）
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)
        async for _ in es:
            pass
        assert (await es.result()).stop_reason == StopReason.STOP
        assert len(capture) == 2

    async def test_stream_error_event_fails(self):
        """流内 error 事件（超载等）按失败收场。"""
        capture = RequestCapture()
        client = anthropic_client(
            [
                ant_message_start(input_tokens=1),
                ant_error("overloaded_error", "Overloaded"),
            ],
            capture,
        )
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        async for _ in es:
            pass
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert "Overloaded" in (result.error_message or "")
        # 归一上 kind：overloaded_error 帧 → OVERLOADED（可重试类）
        assert result.error_kind is NovaErrorKind.OVERLOADED
        assert len(capture) == 1

    async def test_pre_aborted_signal(self):
        """举旗进入：aborted 终态，不发请求。"""
        controller = AbortController()
        controller.abort()
        capture = RequestCapture()
        client = anthropic_client(_text_flow_frames(), capture)
        options = AnthropicOptions(
            api_key="sk-test", client=client, signal=controller.signal
        )
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)
        async for _ in es:
            pass
        result = await es.result()
        assert result.stop_reason == StopReason.ABORTED
        assert len(capture) == 0

    async def test_abort_midflight_closes_stream(self):
        """在飞 abort：watchdog 关流（``closed`` 观测口在传输层字节流上——
        SDK 的 ``stream.close()`` 全真跑），流以 aborted 收尾不挂死。"""
        controller = AbortController()
        capture = RequestCapture()
        byte_stream = ant_scripted_stream(
            [ant_message_start(input_tokens=1)], hang=True
        )
        client = anthropic_stream_client(byte_stream, capture)
        options = AnthropicOptions(
            api_key="sk-test", client=client, signal=controller.signal
        )
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)

        async def _abort_when_blocked():
            # 确认消费者已阻塞在挂起字节流的读上再举旗（Event 受控完成序，
            # 替代原 0.05s 引导睡眠）
            await byte_stream.waiting.wait()
            controller.abort()

        asyncio.ensure_future(_abort_when_blocked())
        async with asyncio.timeout(10):
            async for _ in es:
                pass
        result = await es.result()
        assert result.stop_reason == StopReason.ABORTED
        assert byte_stream.closed.is_set()

    async def test_abort_during_create_injected_client(self):
        """在飞 create 期间 abort（注入客户端）：scope **真杀**（create 亲见
        CancelledError）；注入的 client 不被关闭（属主纪律）。

        保留手搓假客户端（不过线）的理由：被测主体即注入 client 的所有权
        纪律——"不被关闭"的观测口只在假 client 上；注入走公开
        ``options.client`` 接缝（非函数缝 patch），符合假缝阶梯。
        """
        controller = AbortController()
        started = asyncio.Event()
        killed = asyncio.Event()
        never = asyncio.Event()
        client_close_calls = []

        class _FakeWithRaw:
            async def create(self, **_kw):
                started.set()
                try:
                    await never.wait()
                except asyncio.CancelledError:
                    killed.set()
                    raise
                raise AssertionError("真杀语义下 create 不会等到放行")

        class _FakeMessages:
            with_raw_response = _FakeWithRaw()

        class _FakeClient:
            def __init__(self) -> None:
                self.messages = _FakeMessages()

            async def close(self):
                client_close_calls.append(1)

        client = _FakeClient()
        options = AnthropicOptions(
            api_key="sk-test", client=client, signal=controller.signal
        )
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)

        await started.wait()  # 确认请求已在飞（create 内挂起）再举旗
        controller.abort()

        async with asyncio.timeout(10):
            async for _ in es:
                pass
        result = await es.result()
        assert result.stop_reason == StopReason.ABORTED
        assert killed.is_set()  # 真杀——不是"不等了"，是代码死了
        assert client_close_calls == []  # 注入的客户端属主是调用方，不归我们关

    async def test_idle_stream_dies_with_request_timeout_kind(self):
        """SSE 长沉默（服务端半死）：idle 看门狗处死当前流——死因
        REQUEST_TIMEOUT（错误终态，不是取消）。"""
        capture = RequestCapture()
        byte_stream = ant_scripted_stream(
            [ant_message_start(input_tokens=1)], hang=True
        )
        client = anthropic_stream_client(byte_stream, capture)
        options = AnthropicOptions(api_key="sk-test", client=client)
        options.timeout = 0.05
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)

        async with asyncio.timeout(10):
            async for _ in es:
                pass
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert result.error_kind is NovaErrorKind.REQUEST_TIMEOUT
        assert byte_stream.closed.is_set()  # finally 收尾关流（放行挂起字节流）


class TestStreamHooks:
    async def test_on_payload_and_on_response(self):
        """on_payload 可改写请求体；on_response 收到真实状态与**已过滤**的头
        （凭据/会话类响应头永不出钩子的手——security 出口纪律）。"""
        capture = RequestCapture()
        client = anthropic_client(
            _text_flow_frames(),
            capture,
            response_headers={
                "set-cookie": "session=secret",
                "x-api-key": "sk-secret",
            },
        )

        def on_payload(payload, _model):
            payload["max_tokens"] = 128  # 钩子改写
            return payload

        responses = []

        def on_response(response, _model):
            responses.append(response)

        options = AnthropicOptions(api_key="sk-test", client=client)
        options.on_payload = on_payload
        options.on_response = on_response
        es = stream(_model(), Context(messages=[UserMessage(content="hi")]), options)
        async for _ in es:
            pass
        # 钩子改写后的请求体真上了线（capture 即"发出去的"）
        body = RequestCapture.body_json(capture.single())
        assert body["max_tokens"] == 128
        assert len(responses) == 1
        assert responses[0].status == 200
        # 敏感头被过滤，普通头保留（httpx2 会自动补 content-length，不做全等）
        assert "set-cookie" not in responses[0].headers
        assert "x-api-key" not in responses[0].headers
        assert responses[0].headers["content-type"] == "text/event-stream"


class TestStreamErrorKindMapping:
    """流内 SSE error 事件的 error.type 映射 + 取消类型兜底（转换点回归）。"""

    async def test_overloaded_error_maps_kind(self):
        """error.type=overloaded_error（下划线形）→ OVERLOADED，不落文案词表。"""
        capture = RequestCapture()
        client = anthropic_client(
            [ant_error("overloaded_error", "Overloaded")], capture
        )
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.error_kind is NovaErrorKind.OVERLOADED
        assert len(capture) == 1

    async def test_rate_limit_error_maps_kind(self):
        capture = RequestCapture()
        client = anthropic_client([ant_error("rate_limit_error", "slow down")], capture)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert errors[0].error.error_kind is NovaErrorKind.RATE_LIMITED
        assert len(capture) == 1

    async def test_unknown_error_type_falls_back(self):
        """未收录的 error.type：归一按文案/兜底照常（不炸）。"""
        capture = RequestCapture()
        client = anthropic_client([ant_error("mystery_error", "weird")], capture)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.error_kind is NovaErrorKind.INTERNAL
        assert len(capture) == 1

    async def test_aborted_error_type_fallback_when_flag_unset(self):
        """AbortedError 在旗标未置位时到达（看门狗竞态）：判取消终态，
        不上 kind 不归一。

        过线表达：响应头到达后、首帧前字节流即抛 AbortedError——传输层
        中途注入不被 SDK 包装（实测直达转换点），与看门狗竞态同形。
        """
        capture = RequestCapture()
        byte_stream = ant_scripted_stream([], error=AbortedError("Request was aborted"))
        client = anthropic_stream_client(byte_stream, capture)
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].reason == "aborted"
        assert errors[0].error.error_kind is None
        assert errors[0].error.retry_after_ms is None
        assert len(capture) == 1

    async def test_rate_limit_with_usage_limit_text_routes_to_usage_limit(self):
        """rate_limit_error + 额度用尽文案 → USAGE_LIMIT_EXCEEDED（限额词表
        优先于限流声明——同 type 两语义的分流纪律）。"""
        capture = RequestCapture()
        client = anthropic_client(
            [ant_error("rate_limit_error", "usage limit exceeded for this plan")],
            capture,
        )
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert errors[0].error.error_kind is NovaErrorKind.USAGE_LIMIT_EXCEEDED
        assert len(capture) == 1

    async def test_missing_api_key_login_required(self):
        """无凭据守卫（assert_request_auth 拒绝）：LOGIN_REQUIRED 上消息。"""
        event_stream = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(),  # 无 api_key 无 client
        )
        events = [e async for e in event_stream]
        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert errors[0].error.error_kind is NovaErrorKind.LOGIN_REQUIRED


@pytest.mark.asyncio
async def test_abort_during_on_response_closes_stream():
    """on_response 钩子 await 期间 abort：finally 必关已建成的流。

    回归锁（先建流再发钩子的次序）：钩子在 await 中时取消——注入 client
    （owns_client=False）路径没有 client.close() 兜底，若流未建成，连接
    只能靠 GC；先 parse 后发钩子，窗口结构性消灭。
    """
    byte_stream = ant_scripted_stream(ant_text_turn("hi"))
    capture = RequestCapture()
    client = anthropic_stream_client(byte_stream, capture)  # 注入 client 路径

    hook_entered = asyncio.Event()
    release_hook = asyncio.Event()

    async def slow_hook(_response, _identity):
        hook_entered.set()
        await release_hook.wait()  # 挂起窗口：abort 在此到达

    controller = AbortController()
    options = AnthropicOptions(
        api_key="sk-test",
        client=client,
        signal=controller.signal,
        on_response=slow_hook,
    )
    event_stream = stream(
        _model(), Context(messages=[UserMessage(content="hi")]), options
    )

    consumed = asyncio.Event()

    async def consume():
        async for _ in event_stream:
            pass
        consumed.set()

    consumer = asyncio.ensure_future(consume())
    await hook_entered.wait()  # 钩子在 await 中（响应已收、流已建）
    controller.abort()
    await asyncio.wait_for(consumed.wait(), timeout=1.0)
    await consumer
    assert byte_stream.closed.is_set()  # 流被关——不留 GC 悬念


class TestServerSideFallback:
    """服务端 refusal fallback（对齐 pi）：fallback 标记块 + fallback 定价。"""

    def _fallback_model(self):
        from nova_protocol import AnthropicAllowedFallbackModel, AnthropicMessagesCompat

        return _model().model_copy(
            update={
                "compat": AnthropicMessagesCompat(
                    allowed_fallback_models=[
                        AnthropicAllowedFallbackModel(
                            provider="anthropic",
                            model="claude-fallback-x",
                            cost=ModelCost(
                                input=1, output=2, cache_read=0, cache_write=0
                            ),
                        )
                    ]
                )
            }
        )

    async def test_fallback_response_uses_fallback_cost(self):
        """fallback 模型应答：response_model 落 + 按 fallback 费率计成本 +
        标记块不进内容。"""
        frames = [
            ant_message_start(model="claude-fallback-x"),
            ant_block_start(0, {"type": "fallback"}),
            ant_block_stop(0),
            ant_text_block(1),
            ant_text_delta("兜底应答", 1),
            ant_block_stop(1),
            ant_message_delta("end_turn", 5),
            ant_message_stop(),
        ]
        capture = RequestCapture()
        client = anthropic_client(frames, capture)
        es = stream(
            self._fallback_model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        assert result.response_model == "claude-fallback-x"
        # 内容只有文本块（fallback 标记不进）
        assert [c.type for c in result.content] == ["text"]
        assert result.content[0].text == "兜底应答"
        # 成本按 fallback 费率：12*1 + 5*2 每百万
        assert result.usage.cost.total == pytest.approx((12 * 1 + 5 * 2) / 1_000_000)

    async def test_fallback_params_field(self):
        """名单非空 → params.fallbacks 只带模型 id（对齐 TS buildParams 收尾）。"""
        from nova_ai.api_impls.anthropic_messages.params import build_params

        params = build_params(
            self._fallback_model(), Context(messages=[UserMessage(content="hi")])
        )
        assert params["fallbacks"] == [{"model": "claude-fallback-x"}]

    async def test_mid_output_fallback_rejected(self):
        """fallback 标记出现在已有内容之后 → 报错（对齐 TS 不支持中途换模型）。"""
        frames = [
            ant_message_start(),
            ant_text_block(0),
            ant_text_delta("先出内容", 0),
            ant_block_stop(0),
            ant_block_start(1, {"type": "fallback"}),
            ant_message_delta("end_turn", 5),
            ant_message_stop(),
        ]
        capture = RequestCapture()
        client = anthropic_client(frames, capture)
        es = stream(
            self._fallback_model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.ERROR
        assert "mid-output model fallback" in (result.error_message or "")

    async def test_input_transformations_recorded_as_diagnostic(self):
        """服务端入参改写留痕（对齐 TS anthropic_input_transformations）：
        message_delta 携带 input_transformations → 成功收尾落诊断。"""
        frames = [
            ant_message_start(),
            ant_text_block(0),
            ant_text_delta("ok", 0),
            ant_block_stop(0),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 5},
                    "input_transformations": [
                        {
                            "type": "thinking_dropped",
                            "path": "messages[1].content[0]",
                            "reason": "signature_mismatch",
                        }
                    ],
                },
            ),
            ant_message_stop(),
        ]
        capture = RequestCapture()
        client = anthropic_client(frames, capture)
        es = stream(
            self._fallback_model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        diagnostics = result.diagnostics or []
        entry = next(
            d for d in diagnostics if d.get("type") == "anthropic_input_transformations"
        )
        assert entry["details"]["transformations"] == [
            {
                "type": "thinking_dropped",
                "path": "messages[1].content[0]",
                "reason": "signature_mismatch",
            }
        ]

    async def test_no_input_transformations_no_diagnostic(self):
        """无改写：无诊断（不污染常态路径）。"""
        capture = RequestCapture()
        client = anthropic_client(_text_flow_frames(), capture)
        es = stream(
            self._fallback_model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        assert not any(
            d.get("type") == "anthropic_input_transformations"
            for d in (result.diagnostics or [])
        )
