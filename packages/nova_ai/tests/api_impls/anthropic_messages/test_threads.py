"""anthropic_messages/threads.py 测试——message threads 服务端续接。

全链路经 MockTransport 假服务器（假在传输、真在协议）：请求体里的
``thread`` 字段与增量消息形状是核心断言面（RequestCapture）。
"""

import pytest
from nova_protocol import (
    AssistantMessage,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    TextContent,
    Usage,
    UserMessage,
)

from nova_ai.api_impls.anthropic_messages import AnthropicOptions, stream
from nova_ai.session_resources import cleanup_session_resources
from tests._helpers.fake_llm import (
    RequestCapture,
    ant_error_response,
    ant_message_start,
    ant_sse_response,
    ant_text_turn,
    anthropic_client_scripted,
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


def _options(client, **over):
    return AnthropicOptions(
        api_key="sk-test", client=client, session_id="session-1", **over
    )


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text=text)],
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        model="claude-x",
        usage=Usage(),
        stop_reason=StopReason.STOP,
    )


def _bodies(capture: RequestCapture):
    return [RequestCapture.body_json(r) for r in capture.requests]


@pytest.fixture(autouse=True)
def _clean_thread_state():
    yield
    cleanup_session_resources()


async def _first_turn(client) -> AssistantMessage:
    es = stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        _options(client),
    )
    return await es.result()


class TestThreadLifecycle:
    async def test_first_request_creates_thread_second_continues(self):
        """首轮 ``thread: create``；次轮前缀未变 → 只发增量 + continue。"""
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_sse_response(ant_text_turn("你好")),
                ant_sse_response(ant_text_turn("又见面了")),
            ],
            capture,
        )

        r1 = await _first_turn(client)
        assert r1.stop_reason == StopReason.STOP

        # 次轮：会话增长一条回复 + 一条新用户消息
        es = stream(
            _model(),
            Context(
                messages=[
                    UserMessage(content="hi"),
                    _assistant("你好"),
                    UserMessage(content="再说一次"),
                ]
            ),
            _options(client),
        )
        r2 = await es.result()
        assert r2.stop_reason == StopReason.STOP

        bodies = _bodies(capture)
        assert len(bodies) == 2
        assert bodies[0]["thread"] == {"type": "create"}
        assert len(bodies[0]["messages"]) == 1
        assert bodies[1]["thread"] == {
            "type": "continue",
            "previous_message_id": "msg_fake",
        }
        # 只发增量（新用户消息一条），system/tools 省略
        assert len(bodies[1]["messages"]) == 1
        assert "system" not in bodies[1]

    async def test_continue_rejected_resends_as_new_thread(self):
        """续接被拒（thread_not_found）→ 新线程全量重发（对齐 pi NEW_THREAD_ERRORS）。"""
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_sse_response(ant_text_turn("你好")),
                ant_error_response(
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": "thread not found",
                            "details": {"error_code": "thread_not_found"},
                        },
                    },
                ),
                ant_sse_response(ant_text_turn("又见面了")),
            ],
            capture,
        )

        await _first_turn(client)
        es = stream(
            _model(),
            Context(
                messages=[
                    UserMessage(content="hi"),
                    _assistant("你好"),
                    UserMessage(content="再说一次"),
                ]
            ),
            _options(client),
        )
        r2 = await es.result()
        assert r2.stop_reason == StopReason.STOP

        bodies = _bodies(capture)
        assert len(bodies) == 3
        assert bodies[1]["thread"]["type"] == "continue"
        # 重发：全量消息 + create
        assert bodies[2]["thread"] == {"type": "create"}
        assert len(bodies[2]["messages"]) == 3
        # 回退留痕
        assert any(
            d.get("type") == "anthropic_thread_fallback" for d in (r2.diagnostics or [])
        )

    async def test_unsupported_turns_threads_off_for_session(self):
        """端点不认 threads → 不带 thread 重发 + 会话关停（后续请求不再携带）。"""
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_sse_response(ant_text_turn("你好")),
                ant_error_response(
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": "thread_unsupported_request",
                            "details": {"error_code": "thread_unsupported_request"},
                        },
                    },
                ),
                ant_sse_response(ant_text_turn("又见面了")),
                ant_sse_response(ant_text_turn("第三次")),
            ],
            capture,
        )

        await _first_turn(client)
        es = stream(
            _model(),
            Context(
                messages=[
                    UserMessage(content="hi"),
                    _assistant("你好"),
                    UserMessage(content="再说一次"),
                ]
            ),
            _options(client),
        )
        r2 = await es.result()
        assert r2.stop_reason == StopReason.STOP

        # 第三轮：会话已关停——不再携带 thread
        es = stream(
            _model(),
            Context(
                messages=[
                    UserMessage(content="hi"),
                    _assistant("你好"),
                    UserMessage(content="再说一次"),
                    _assistant("又见面了"),
                    UserMessage(content="第三次"),
                ]
            ),
            _options(client),
        )
        r3 = await es.result()
        assert r3.stop_reason == StopReason.STOP

        bodies = _bodies(capture)
        assert len(bodies) == 4
        assert "thread" not in bodies[2]
        assert "thread" not in bodies[3]

    async def test_disable_continuation_sends_no_thread(self):
        """disable_continuation：不使用也不记录续接状态（缓存预热语义）。"""
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [ant_sse_response(ant_text_turn("你好"))],
            capture,
        )
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            _options(client, disable_continuation=True),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        bodies = _bodies(capture)
        assert "thread" not in bodies[0]

    async def test_no_session_id_sends_no_thread(self):
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [ant_sse_response(ant_text_turn("你好"))],
            capture,
        )
        es = stream(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP
        bodies = _bodies(capture)
        assert "thread" not in bodies[0]

    async def test_cleanup_forces_new_thread(self):
        """cleanup_session_resources 释放台账 → 下一请求重新 create。"""
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_sse_response(ant_text_turn("你好")),
                ant_sse_response(ant_text_turn("新线程")),
            ],
            capture,
        )
        await _first_turn(client)
        cleanup_session_resources("session-1")

        es = stream(
            _model(),
            Context(
                messages=[
                    UserMessage(content="hi"),
                    _assistant("你好"),
                    UserMessage(content="再来"),
                ]
            ),
            _options(client),
        )
        r2 = await es.result()
        assert r2.stop_reason == StopReason.STOP
        bodies = _bodies(capture)
        assert bodies[1]["thread"] == {"type": "create"}
        assert len(bodies[1]["messages"]) == 3


class TestStrictToolsFallback:
    async def test_strict_limit_400_downgrades_and_resends(self):
        """prefer-strict 工具被 400 限量 → 降级非严格重发一次 + 留痕。"""
        from nova_protocol import ConstrainedSampling, Tool

        tool = Tool(
            name="search",
            description="d",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict="prefer"
        )

        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_error_response(
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": "Too many strict tools (21 > 20)",
                        },
                    },
                ),
                ant_sse_response(ant_text_turn("降级后成功")),
            ],
            capture,
        )

        from nova_protocol import AnthropicMessagesCompat

        model = _model().model_copy(
            update={"compat": AnthropicMessagesCompat(supports_strict_tools=True)}
        )
        es = stream(
            model,
            Context(messages=[UserMessage(content="hi")], tools=[tool]),
            AnthropicOptions(api_key="sk-test", client=client),
        )
        result = await es.result()
        assert result.stop_reason == StopReason.STOP

        bodies = _bodies(capture)
        assert len(bodies) == 2
        assert bodies[0]["tools"][0].get("strict") is True
        assert "strict" not in bodies[1]["tools"][0]
        assert any(
            d.get("type") == "anthropic_strict_tools_fallback"
            for d in (result.diagnostics or [])
        )

    @pytest.mark.parametrize(
        ("strict", "status", "error_type", "message"),
        [
            ("require", 400, "invalid_request_error", "Too many strict tools"),
            ("prefer", 400, "invalid_request_error", "unrelated request error"),
            ("prefer", 400, "api_error", "Too many strict tools"),
            ("prefer", 500, "invalid_request_error", "Too many strict tools"),
        ],
    )
    async def test_non_limit_or_required_strict_never_resends(
        self, strict, status, error_type, message
    ):
        from nova_protocol import AnthropicMessagesCompat, ConstrainedSampling, Tool

        tool = Tool(
            name="search",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict=strict
        )
        capture = RequestCapture()
        client = anthropic_client_scripted(
            [
                ant_error_response(
                    status,
                    {
                        "type": "error",
                        "error": {"type": error_type, "message": message},
                    },
                )
            ],
            capture,
        )
        model = _model().model_copy(
            update={"compat": AnthropicMessagesCompat(supports_strict_tools=True)}
        )
        result = await stream(
            model,
            Context(messages=[UserMessage(content="hi")], tools=[tool]),
            AnthropicOptions(api_key="sk-test", client=client),
        ).result()
        assert result.stop_reason is StopReason.ERROR
        assert len(capture) == 1

    async def test_second_strict_rejection_returns_second_error(self):
        from nova_protocol import AnthropicMessagesCompat, ConstrainedSampling, Tool

        tool = Tool(
            name="search",
            description="d",
            parameters={"type": "object", "properties": {}},
        )
        tool.constrained_sampling = ConstrainedSampling(
            type="json_schema", strict="prefer"
        )
        response = ant_error_response(
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Too many strict tools (21 > 20)",
                },
            },
        )
        capture = RequestCapture()
        client = anthropic_client_scripted([response, response], capture)
        model = _model().model_copy(
            update={"compat": AnthropicMessagesCompat(supports_strict_tools=True)}
        )
        result = await stream(
            model,
            Context(messages=[UserMessage(content="hi")], tools=[tool]),
            AnthropicOptions(api_key="sk-test", client=client),
        ).result()
        assert result.stop_reason is StopReason.ERROR
        assert len(capture) == 2
        assert "strict" not in _bodies(capture)[1]["tools"][0]
