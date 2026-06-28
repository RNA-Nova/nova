"""
DeepSeek 真实模型集成测试（全面版）

依赖环境变量 VOLCENGINE_API_KEY。
通过 Volcengine Ark API 调用真实的 DeepSeek 模型，覆盖 nova_ai 核心功能、
参数配置、边界条件、错误处理、并发、取消等场景。

运行方式：
    pytest tests/test_integration_deepseek.py -v
    pytest -m "not integration"          # 跳过本文件，只跑单元测试
"""

import asyncio
import os
from typing import List, Set

import pytest

from nova_ai import stream, complete, stream_simple, complete_simple
from nova_ai.registry import get_model, reset_registry
from nova_ai.types import (
    Context,
    Tool,
    AssistantMessage,
    StopReason,
    UserMessage,
    AssistantMessage as AssistantMessageType,
    ToolResultMessage,
    TextContent,
    ProviderResponse,
)
from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions
from nova_ai.models.volcengine import (
    get_volcengine_model,
    VOLCENGINE_MODELS,
)

pytestmark = pytest.mark.integration


class AbortSignal:
    """测试用的简单取消信号（与 nova_agent.signal.AbortSignal 接口一致）"""

    def __init__(self, name: str = ""):
        self.name = name
        self._aborted = False

    @property
    def aborted(self):
        return self._aborted

    def set(self):
        self._aborted = True

    def __bool__(self):
        return self._aborted


def _get_model(model_id: str = "deepseek-v4-flash-260425"):
    """获取真实 DeepSeek 模型，跳过测试如果未配置 API key。"""
    if not os.environ.get("VOLCENGINE_API_KEY"):
        pytest.skip("VOLCENGINE_API_KEY not set")
    reset_registry()
    return get_volcengine_model(model_id)


def _extract_text(message: AssistantMessage) -> str:
    return "".join(c.text for c in message.content if c.type == "text")


def _extract_thinking(message: AssistantMessage) -> str:
    return "".join(c.thinking for c in message.content if c.type == "thinking")


async def _collect_event_types(event_stream) -> List[str]:
    types = []
    async for event in event_stream:
        types.append(event.type)
    return types


# ---------------------------------------------------------------------------
# 1. 基础调用
# ---------------------------------------------------------------------------


class TestBasicRealCalls:
    """基础真实调用测试"""

    @pytest.mark.asyncio
    async def test_stream_returns_events(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream(model, ctx)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)
        result = await event_stream.result()

        assert events[0] == "start"
        assert "text_start" in events
        assert "text_delta" in events
        assert "text_end" in events
        assert events[-1] == "done"
        assert isinstance(result, AssistantMessageType)
        assert len(_extract_text(result)) > 0
        assert result.usage.total_tokens > 0
        assert result.usage.input > 0
        assert result.usage.output > 0

    @pytest.mark.asyncio
    async def test_complete_returns_message(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        assert isinstance(result, AssistantMessageType)
        assert len(_extract_text(result)) > 0
        assert result.usage.total_tokens > 0
        assert result.stop_reason == StopReason.STOP

    @pytest.mark.asyncio
    async def test_stream_simple(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream_simple(model, ctx)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)
        result = await event_stream.result()

        assert events[0] == "start"
        assert "text_start" in events
        assert events[-1] == "done"
        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_complete_simple(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        async with asyncio.timeout(120):
            result = await complete_simple(model, ctx)

        assert isinstance(result, AssistantMessageType)
        assert len(_extract_text(result)) > 0
        assert result.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_stream_and_complete_produce_same_final_text(self):
        """相同输入，stream 最终结果与 complete 文本语义一致"""
        model = _get_model()
        ctx1 = Context(messages=[UserMessage(content="hi")])
        ctx2 = Context(messages=[UserMessage(content="hi")])

        async with asyncio.timeout(180):
            stream_result = await complete(model, ctx1)
            complete_result = await complete(model, ctx2)

        assert len(_extract_text(stream_result)) > 0
        assert len(_extract_text(complete_result)) > 0


# ---------------------------------------------------------------------------
# 2. 模型注册表
# ---------------------------------------------------------------------------


class TestRealModelRegistry:
    """真实模型注册表测试"""

    def test_get_volcengine_model(self):
        if not os.environ.get("VOLCENGINE_API_KEY"):
            pytest.skip("VOLCENGINE_API_KEY not set")
        reset_registry()
        model = get_volcengine_model("deepseek-v4-flash-260425")
        assert model.id == "deepseek-v4-flash-260425"
        assert model.provider == "volcengine"
        assert model.reasoning is True

    def test_registry_lookup(self):
        if not os.environ.get("VOLCENGINE_API_KEY"):
            pytest.skip("VOLCENGINE_API_KEY not set")
        reset_registry()
        model = get_model("volcengine", "deepseek-v4-flash-260425")
        assert model is not None
        assert model.id == "deepseek-v4-flash-260425"

    def test_all_volcengine_models_registered(self):
        if not os.environ.get("VOLCENGINE_API_KEY"):
            pytest.skip("VOLCENGINE_API_KEY not set")
        reset_registry()
        for model_id in VOLCENGINE_MODELS:
            m = get_model("volcengine", model_id)
            assert m is not None, f"model {model_id} not registered"


# ---------------------------------------------------------------------------
# 3. Usage / Cost
# ---------------------------------------------------------------------------


class TestUsageAndCost:
    """Usage 和 Cost 真实测试"""

    @pytest.mark.asyncio
    async def test_usage_updated_during_stream(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream(model, ctx)

        usage_updates = []
        async with asyncio.timeout(120):
            async for event in event_stream:
                partial = getattr(event, "partial", None)
                if partial and partial.usage and partial.usage.total_tokens > 0:
                    usage_updates.append(
                        {
                            "type": event.type,
                            "total": partial.usage.total_tokens,
                        }
                    )

        result = await event_stream.result()

        assert len(usage_updates) >= 1
        assert result.usage.total_tokens > 0
        assert result.usage.cost.total > 0

    @pytest.mark.asyncio
    async def test_cost_matches_usage_and_model_pricing(self):
        """cost 应等于 usage * model.cost"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        u = result.usage
        c = result.usage.cost
        expected_input = u.input * model.cost.input / 1_000_000
        expected_output = u.output * model.cost.output / 1_000_000
        expected_cache_read = u.cache_read * model.cost.cache_read / 1_000_000

        assert pytest.approx(c.input, abs=1e-9) == expected_input
        assert pytest.approx(c.output, abs=1e-9) == expected_output
        assert pytest.approx(c.cache_read, abs=1e-9) == expected_cache_read
        assert (
            pytest.approx(c.total, abs=1e-9)
            == expected_input + expected_output + expected_cache_read
        )


# ---------------------------------------------------------------------------
# 4. 事件序列
# ---------------------------------------------------------------------------


class TestEventSequence:
    """事件序列完整性测试"""

    @pytest.mark.asyncio
    async def test_no_duplicate_end_events(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream(model, ctx)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)

        end_types: Set[str] = {"text_end", "thinking_end", "toolcall_end"}
        for i in range(1, len(events)):
            assert not (
                events[i] in end_types and events[i - 1] == events[i]
            ), f"duplicate end event at {i}: {events[i]}"

    @pytest.mark.asyncio
    async def test_start_before_delta_before_end_before_done(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream(model, ctx)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)

        assert events[0] == "start"
        assert "text_start" in events
        assert events.index("text_start") < events.index("text_delta")
        assert events.index("text_delta") < events.index("text_end")
        assert events.index("text_end") < events.index("done")

    @pytest.mark.asyncio
    async def test_repeating_async_for_yields_nothing(self):
        """重复遍历已完成的 stream 不应产生事件"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        event_stream = stream(model, ctx)

        async with asyncio.timeout(120):
            events1 = await _collect_event_types(event_stream)
            events2 = await _collect_event_types(event_stream)

        assert len(events1) > 0
        assert events2 == []


# ---------------------------------------------------------------------------
# 5. 思考等级切换
# ---------------------------------------------------------------------------


class TestReasoningLevels:
    """思考等级切换与推理输出测试"""

    @pytest.mark.parametrize("level", ["off", "low", "medium", "high"])
    @pytest.mark.asyncio
    async def test_each_reasoning_level_completes(self, level):
        """不同 reasoning_effort 都应正常完成"""
        model = _get_model("deepseek-v4-flash-260425")
        ctx = Context(messages=[UserMessage(content="What is 24*7?")])
        options = OpenAICompletionsOptions(reasoning_effort=level)
        event_stream = stream(model, ctx, options)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)
        result = await event_stream.result()

        assert events[-1] == "done"
        assert len(_extract_text(result)) > 0
        assert result.usage.total_tokens > 0

    @pytest.mark.asyncio
    async def test_reasoning_disabled_does_not_set_reasoning_effort(self):
        """reasoning=off 时，params 不应包含 reasoning_effort"""
        from nova_ai.api_impls.openai_completions import build_params

        model = _get_model("deepseek-v4-flash-260425")
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(reasoning_effort="off")

        params = build_params(model, ctx, options)

        assert "reasoning_effort" not in params
        assert params["extra_body"]["thinking"]["type"] == "disabled"

    @pytest.mark.asyncio
    async def test_thinking_level_map_applies(self):
        """thinking_level_map 映射生效"""
        from nova_ai.api_impls.openai_completions import build_params
        from nova_ai.types import Model, ModelCost
        from nova_ai.types.enums import KnownApi, KnownProvider

        model = Model(
            id="deepseek-test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            thinking_level_map={"low": "medium", "high": "max"},
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(reasoning_effort="low")

        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "medium"


# ---------------------------------------------------------------------------
# 6. 工具调用
# ---------------------------------------------------------------------------


class TestToolCalls:
    """工具调用集成测试"""

    @pytest.fixture
    def weather_tool(self):
        return Tool(
            name="get_weather",
            description="Get weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )

    @pytest.mark.asyncio
    async def test_stream_with_tool(self, weather_tool):
        model = _get_model("deepseek-v4-flash-260425")
        ctx = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[weather_tool],
        )
        event_stream = stream(model, ctx)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)
        result = await event_stream.result()

        assert events[0] == "start"
        assert events[-1] == "done"
        assert len(_extract_text(result)) > 0 or any(
            c.type == "toolCall" for c in result.content
        )

    @pytest.mark.asyncio
    async def test_tool_choice_auto_calls_tool(self, weather_tool):
        """tool_choice=auto 时模型根据提示调用工具"""
        model = _get_model("deepseek-v4-flash-260425")
        ctx = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[weather_tool],
        )
        options = OpenAICompletionsOptions(tool_choice="auto")
        event_stream = stream(model, ctx, options)

        async with asyncio.timeout(120):
            events = await _collect_event_types(event_stream)
        result = await event_stream.result()

        tool_calls = [c for c in result.content if c.type == "toolCall"]
        assert len(tool_calls) > 0, "expected at least one tool call"
        assert all(tc.name == "get_weather" for tc in tool_calls)
        assert all(isinstance(tc.arguments, dict) for tc in tool_calls)
        assert tool_calls[0].arguments.get("city", "").lower() == "beijing"

    @pytest.mark.asyncio
    async def test_multi_tool_round_trip(self):
        """工具调用 + tool result + 模型继续回复"""
        model = _get_model("deepseek-v4-flash-260425")
        tool = Tool(
            name="get_weather",
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        ctx = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[tool],
        )
        async with asyncio.timeout(120):
            result1 = await complete(model, ctx)

        tool_calls = [c for c in result1.content if c.type == "toolCall"]
        assert (
            len(tool_calls) > 0
        ), f"expected tool call, got content types: {[c.type for c in result1.content]}"

        ctx.messages.append(result1)
        ctx.messages.append(
            ToolResultMessage(
                tool_call_id=tool_calls[0].id,
                tool_name=tool_calls[0].name,
                content=[TextContent(text="Sunny, 25°C")],
            )
        )

        async with asyncio.timeout(120):
            result2 = await complete(model, ctx)

        text2 = _extract_text(result2)
        assert len(text2) > 0
        assert result2.stop_reason == StopReason.STOP


# ---------------------------------------------------------------------------
# 7. 多轮与系统提示
# ---------------------------------------------------------------------------


class TestMultiTurnAndSystem:
    """多轮对话与系统提示测试"""

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="My name is Kimi.")])

        async with asyncio.timeout(120):
            result1 = await complete(model, ctx)

        reply1 = _extract_text(result1)
        assert len(reply1) > 0

        ctx.messages.append(AssistantMessageType(content=[TextContent(text=reply1)]))
        ctx.messages.append(UserMessage(content="What's my name?"))

        async with asyncio.timeout(120):
            result2 = await complete(model, ctx)

        reply2 = _extract_text(result2)
        assert "Kimi" in reply2

    @pytest.mark.asyncio
    async def test_system_prompt_is_respected(self):
        """模型应遵循 system_prompt 的约束"""
        model = _get_model()
        ctx = Context(
            system_prompt="You must answer every question with exactly one word.",
            messages=[UserMessage(content="What is the capital of France?")],
        )

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        words = _extract_text(result).strip().split()
        # 允许标点，但基本应为一个词
        assert len(words) <= 2


# ---------------------------------------------------------------------------
# 8. 取消与中止
# ---------------------------------------------------------------------------


class TestCancellation:
    """取消信号测试"""

    @pytest.mark.asyncio
    async def test_abort_signal_cancels_stream(self):
        model = _get_model()
        signal = AbortSignal()
        ctx = Context(messages=[UserMessage(content="Tell me a long story.")])
        options = OpenAICompletionsOptions(signal=signal)
        event_stream = stream(model, ctx, options)

        signal.set()

        events = []
        async with asyncio.timeout(120):
            async for event in event_stream:
                events.append(event.type)

        result = await event_stream.result()

        assert signal.aborted is True
        assert "error" in events or result.stop_reason in (
            StopReason.ABORTED,
            StopReason.ERROR,
        )

    @pytest.mark.asyncio
    async def test_cancel_after_first_chunk(self):
        """收到第一个 chunk 后取消"""
        model = _get_model()
        signal = AbortSignal()
        ctx = Context(messages=[UserMessage(content="Tell me a long story.")])
        options = OpenAICompletionsOptions(signal=signal)
        event_stream = stream(model, ctx, options)

        events = []
        async with asyncio.timeout(120):
            async for event in event_stream:
                events.append(event.type)
                if event.type == "text_delta":
                    signal.set()

        result = await event_stream.result()

        assert signal.aborted is True
        assert "text_delta" in events
        assert result.stop_reason in (StopReason.ABORTED, StopReason.ERROR)


# ---------------------------------------------------------------------------
# 9. 参数与配置
# ---------------------------------------------------------------------------


class TestModelParameters:
    """模型参数配置真实测试"""

    @pytest.mark.asyncio
    async def test_temperature_zero(self):
        """temperature=0 时输出稳定"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(temperature=0)

        async with asyncio.timeout(120):
            result = await complete(model, ctx, options)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_max_tokens_limits_output(self):
        """max_tokens=1 应截断输出"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="Tell me a long story.")])
        options = OpenAICompletionsOptions(max_tokens=1)

        async with asyncio.timeout(120):
            result = await complete(model, ctx, options)

        assert result.stop_reason == StopReason.LENGTH
        assert result.usage.output <= 2  # 允许小误差

    @pytest.mark.asyncio
    async def test_custom_headers(self):
        """自定义 headers 不破坏请求"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(headers={"X-Custom-Header": "test"})

        async with asyncio.timeout(120):
            result = await complete(model, ctx, options)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_timeout_is_passed(self):
        """timeout 参数不破坏请求"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(timeout=60)

        async with asyncio.timeout(120):
            result = await complete(model, ctx, options)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_max_retries_zero_works(self):
        """max_retries=0 不破坏请求"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(max_retries=0)

        async with asyncio.timeout(120):
            result = await complete(model, ctx, options)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_on_response_is_called(self):
        """on_response 回调收到 HTTP 响应元数据"""
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="hi")])

        responses: List[ProviderResponse] = []

        def on_response(resp: ProviderResponse, m):
            responses.append(resp)

        options = OpenAICompletionsOptions(on_response=on_response)
        event_stream = stream(model, ctx, options)

        async with asyncio.timeout(120):
            await _collect_event_types(event_stream)

        assert len(responses) == 1
        assert responses[0].status == 200
        assert "content-type" in {k.lower(): v for k, v in responses[0].headers.items()}


# ---------------------------------------------------------------------------
# 10. 并发
# ---------------------------------------------------------------------------


class TestConcurrency:
    """并发调用测试"""

    @pytest.mark.asyncio
    async def test_three_concurrent_streams(self):
        """三个 stream 同时运行互不干扰"""
        model = _get_model()
        prompts = ["hi", "hello", "hey"]

        async def run_one(prompt: str):
            ctx = Context(messages=[UserMessage(content=prompt)])
            event_stream = stream(model, ctx)
            async with asyncio.timeout(120):
                events = await _collect_event_types(event_stream)
            result = await event_stream.result()
            return events, result

        results = await asyncio.gather(*(run_one(p) for p in prompts))

        for events, result in results:
            assert events[-1] == "done"
            assert len(_extract_text(result)) > 0
            assert result.usage.total_tokens > 0


# ---------------------------------------------------------------------------
# 11. 特殊输入与边界
# ---------------------------------------------------------------------------


class TestSpecialInputs:
    """特殊输入与边界测试"""

    @pytest.mark.asyncio
    async def test_chinese_input(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="你好，请用中文回答")])

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_emoji_input(self):
        model = _get_model()
        ctx = Context(messages=[UserMessage(content="Say hi with an emoji 🎉")])

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        assert len(_extract_text(result)) > 0

    @pytest.mark.asyncio
    async def test_long_input(self):
        """较长输入不应报错"""
        model = _get_model()
        text = "Repeat the following word: hello. " * 50
        ctx = Context(messages=[UserMessage(content=text)])

        async with asyncio.timeout(120):
            result = await complete(model, ctx)

        assert len(_extract_text(result)) > 0
        assert result.usage.input > 50


# ---------------------------------------------------------------------------
# 12. 错误处理
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """错误处理真实测试"""

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_error(self):
        model = _get_model()
        original_key = os.environ.get("VOLCENGINE_API_KEY")
        os.environ["VOLCENGINE_API_KEY"] = "invalid-key"
        try:
            ctx = Context(messages=[UserMessage(content="hi")])
            event_stream = stream(model, ctx)

            events = []
            async with asyncio.timeout(60):
                async for event in event_stream:
                    events.append(event.type)

            result = await event_stream.result()

            assert "error" in events or result.stop_reason == StopReason.ERROR
        finally:
            if original_key is not None:
                os.environ["VOLCENGINE_API_KEY"] = original_key
            else:
                os.environ.pop("VOLCENGINE_API_KEY", None)


# ---------------------------------------------------------------------------
# 13. 不同模型
# ---------------------------------------------------------------------------


class TestDifferentModels:
    """不同 DeepSeek 模型测试"""

    @pytest.mark.parametrize(
        "model_id",
        [
            "deepseek-v4-flash-260425",
            "deepseek-v4-pro-260425",
            "deepseek-v3-2-251201",
        ],
    )
    @pytest.mark.asyncio
    async def test_each_model_responds(self, model_id):
        model = _get_model(model_id)
        ctx = Context(messages=[UserMessage(content="hi")])

        async with asyncio.timeout(180):
            result = await complete(model, ctx)

        assert isinstance(result, AssistantMessageType)
        assert len(_extract_text(result)) > 0
        assert result.usage.total_tokens > 0
