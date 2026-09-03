"""真实 API 集成测试：Kimi Coding 与 Volcengine 全模型。

需要环境变量：
- KIMI_API_KEY：Kimi Coding API key
- VOLCENGINE_API_KEY：Volcengine API key

运行：
    KIMI_API_KEY=xxx VOLCENGINE_API_KEY=yyy pixi run -e dev pytest packages/nova_ai/tests/test_integration_providers.py -v
"""

import asyncio
import os
from typing import Any, Optional

import pytest

from nova_ai import Context, UserMessage, builtin_models
from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.types import (
    CacheRetention,
    Model,
    ProviderResponse,
    SimpleStreamOptions,
    TextContent,
    ThinkingLevel,
    Tool,
    ToolResultMessage,
)
from nova_ai.types.auth import ApiKeyCredential

KIMI_API_KEY = os.environ.get("KIMI_API_KEY")
VOLCENGINE_API_KEY = os.environ.get("VOLCENGINE_API_KEY")


def _extract_text(content):
    return "".join(p.text for p in content if hasattr(p, "text"))


def _extract_thinking(content):
    return "".join(p.thinking for p in content if hasattr(p, "thinking") and p.thinking)


async def _setup_models(provider: str, api_key: str):
    store = InMemoryCredentialStore()

    async def _set(_current):
        return ApiKeyCredential(key=api_key)

    await store.modify(provider, _set)
    models = builtin_models()
    models._credential_store = store
    return models


async def _test_model(
    models,
    provider: str,
    model_id: str,
    cases: Optional[list] = None,
) -> dict:
    model = models.get_model(provider, model_id)
    if model is None:
        return {"model": model_id, "available": False, "error": "model not found"}

    context = Context(
        messages=[UserMessage(content="你好，1+1等于多少？请用一句话简短回答。")]
    )
    results = {
        "model": model_id,
        "provider": provider,
        "base_url": model.base_url,
        "available": True,
        "cases": [],
    }

    if cases is None:
        cases = [
            ("basic_no_reasoning", SimpleStreamOptions()),
            ("reasoning_low", SimpleStreamOptions(reasoning=ThinkingLevel.LOW)),
            ("reasoning_high", SimpleStreamOptions(reasoning=ThinkingLevel.HIGH)),
        ]
        if model.thinking_level_map and model.thinking_level_map.get("max"):
            cases.append(
                ("reasoning_max", SimpleStreamOptions(reasoning=ThinkingLevel.MAX))
            )

    for case_name, options in cases:
        try:
            result = await models.complete_simple(model, context, options)
            text = _extract_text(result.content)
            thinking = _extract_thinking(result.content)
            case_result = {
                "case": case_name,
                "ok": result.error_message is None,
                "text": text[:200],
                "thinking_len": len(thinking),
                "thinking_preview": thinking[:200],
                "stop_reason": str(result.stop_reason),
                "error": result.error_message,
            }
        except Exception as e:
            case_result = {
                "case": case_name,
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
            }
        results["cases"].append(case_result)

    return results


@pytest.mark.integration
@pytest.mark.skipif(not KIMI_API_KEY, reason="KIMI_API_KEY not set")
class TestKimiCodingIntegration:
    """Kimi Coding 真实 API 集成测试。"""

    @pytest.fixture(scope="class")
    @classmethod
    def models(cls):
        import asyncio

        return asyncio.run(_setup_models("kimi-coding", KIMI_API_KEY))

    @pytest.mark.asyncio
    async def test_k3(self, models):
        result = await _test_model(models, "kimi-coding", "k3")
        assert result["available"] is True
        for case in result["cases"]:
            assert case["ok"] is True, f"{case['case']} failed: {case.get('error')}"
            assert len(case["text"]) > 0

    @pytest.mark.asyncio
    async def test_kimi_for_coding(self, models):
        result = await _test_model(models, "kimi-coding", "kimi-for-coding")
        assert result["available"] is True
        for case in result["cases"]:
            assert case["ok"] is True, f"{case['case']} failed: {case.get('error')}"
            assert len(case["text"]) > 0

    @pytest.mark.asyncio
    async def test_kimi_for_coding_highspeed(self, models):
        result = await _test_model(models, "kimi-coding", "kimi-for-coding-highspeed")
        assert result["available"] is True
        for case in result["cases"]:
            assert case["ok"] is True, f"{case['case']} failed: {case.get('error')}"
            assert len(case["text"]) > 0

    @pytest.mark.asyncio
    async def test_k3_thinking_levels(self, models):
        """k3 支持 low/high/max 思考级别。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="9.11 和 9.9 哪个大？")])

        for level, expect_thinking in [
            (ThinkingLevel.LOW, True),
            (ThinkingLevel.HIGH, True),
            (ThinkingLevel.MAX, True),
        ]:
            result = await models.complete_simple(
                model, context, SimpleStreamOptions(reasoning=level)
            )
            assert result.error_message is None
            thinking = _extract_thinking(result.content)
            if expect_thinking:
                assert len(thinking) > 0, f"{level} should have thinking"

    @pytest.mark.asyncio
    async def test_kimi_for_coding_thinking_toggle(self, models):
        """kimi-for-coding 支持思考开关。"""
        model = models.get_model("kimi-coding", "kimi-for-coding")
        context = Context(messages=[UserMessage(content="1+1=?")])

        result_on = await models.complete_simple(
            model, context, SimpleStreamOptions(reasoning=ThinkingLevel.LOW)
        )
        assert result_on.error_message is None

        result_off = await models.complete_simple(model, context, SimpleStreamOptions())
        assert result_off.error_message is None

    @pytest.mark.asyncio
    async def test_k3_cache_prompt_cache_key(self, models):
        """k3 支持 prompt_cache_key 和 cache_retention。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="你好")])

        payloads = []

        def on_payload(payload, _model):
            payloads.append(payload)
            return payload

        options = SimpleStreamOptions(
            session_id="test-session-123",
            cache_retention=CacheRetention.LONG,
            on_payload=on_payload,
        )
        result = await models.complete_simple(model, context, options)
        assert result.error_message is None
        assert len(payloads) == 1
        extra_body = payloads[0].get("extra_body", {})
        assert extra_body.get("prompt_cache_key") == "test-session-123"
        assert extra_body.get("prompt_cache_retention") == "24h"

    @pytest.mark.asyncio
    async def test_k3_cache_usage_fields(self, models):
        """k3 缓存相关 usage 字段。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="你好")])

        # 第一次调用（写缓存）
        session_id = "cache-test-123"
        options = SimpleStreamOptions(
            session_id=session_id, cache_retention=CacheRetention.LONG
        )
        result1 = await models.complete_simple(model, context, options)
        assert result1.error_message is None

        # 第二次调用（可能命中缓存）
        result2 = await models.complete_simple(model, context, options)
        assert result2.error_message is None
        # 检查 usage 字段存在（具体值取决于服务端）
        assert result2.usage is not None
        assert result2.usage.input >= 0
        assert result2.usage.output >= 0

    @pytest.mark.asyncio
    async def test_k3_tool_call(self, models):
        """k3 工具调用。"""
        model = models.get_model("kimi-coding", "k3")
        tool = Tool(
            name="get_weather",
            description="Get weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        context = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[tool],
        )
        result = await models.complete_simple(model, context)
        assert result.error_message is None
        # 模型可能调用工具，也可能直接回答
        tool_calls = [c for c in result.content if c.type == "toolCall"]
        if tool_calls:
            assert tool_calls[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_k3_tool_result_round_trip(self, models):
        """k3 工具调用 + tool result + 继续回复。"""
        model = models.get_model("kimi-coding", "k3")
        tool = Tool(
            name="get_weather",
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        context = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[tool],
        )
        result1 = await models.complete_simple(model, context)
        assert result1.error_message is None

        tool_calls = [c for c in result1.content if c.type == "toolCall"]
        if tool_calls:
            # 模拟 tool result
            tool_result = ToolResultMessage(
                tool_call_id=tool_calls[0].id,
                tool_name="get_weather",
                content=[TextContent(text="Sunny, 25°C")],
            )
            context2 = Context(
                messages=[
                    UserMessage(content="What's the weather in Beijing?"),
                    result1,
                    tool_result,
                ],
                tools=[tool],
            )
            result2 = await models.complete_simple(model, context2)
            assert result2.error_message is None
            assert len(_extract_text(result2.content)) > 0

    @pytest.mark.asyncio
    async def test_k3_multi_turn(self, models):
        """k3 多轮对话。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="My name is Kimi.")])

        result1 = await models.complete_simple(model, context)
        assert result1.error_message is None

        context2 = Context(
            messages=[
                UserMessage(content="My name is Kimi."),
                result1,
                UserMessage(content="What is my name?"),
            ]
        )
        result2 = await models.complete_simple(model, context2)
        assert result2.error_message is None
        assert "Kimi" in _extract_text(result2.content)

    @pytest.mark.asyncio
    async def test_k3_temperature(self, models):
        """k3 temperature 参数（k3 只允许 temperature=1）。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="Say hello")])

        # k3 只允许 temperature=1，传 1.0 应该成功
        result = await models.complete_simple(
            model, context, SimpleStreamOptions(temperature=1.0)
        )
        assert result.error_message is None

        # 传 0.0 应该报错（真实验证服务端限制）
        result_invalid = await models.complete_simple(
            model, context, SimpleStreamOptions(temperature=0.0)
        )
        assert result_invalid.error_message is not None
        assert "temperature" in result_invalid.error_message.lower()

    @pytest.mark.asyncio
    async def test_k3_max_tokens(self, models):
        """k3 max_tokens 参数。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="Tell me a long story")])

        result = await models.complete_simple(
            model, context, SimpleStreamOptions(max_tokens=10)
        )
        assert result.error_message is None
        text = _extract_text(result.content)
        # max_tokens=10 应该限制输出长度
        assert len(text) < 500

    @pytest.mark.asyncio
    async def test_k3_on_payload_on_response(self, models):
        """k3 on_payload / on_response 回调。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="hi")])

        payloads = []
        responses = []

        def on_payload(payload, _model):
            payloads.append(payload)
            return payload

        def on_response(resp: ProviderResponse, _model):
            responses.append(resp)

        options = SimpleStreamOptions(on_payload=on_payload, on_response=on_response)
        result = await models.complete_simple(model, context, options)
        assert result.error_message is None
        assert len(payloads) >= 1
        assert len(responses) >= 1
        assert responses[0].status == 200

    @pytest.mark.asyncio
    async def test_k3_abort(self, models):
        """k3 abort signal。"""
        from nova_ai import AbortController

        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="Tell me a very long story")])

        controller = AbortController()
        options = SimpleStreamOptions(signal=controller.signal)

        # 立即 abort
        controller.abort()

        result = await models.complete_simple(model, context, options)
        assert result.stop_reason.value in ("aborted", "error")

    @pytest.mark.asyncio
    async def test_k3_chinese_input(self, models):
        """k3 中文输入。"""
        model = models.get_model("kimi-coding", "k3")
        context = Context(messages=[UserMessage(content="你好，请用中文回答")])

        result = await models.complete_simple(model, context)
        assert result.error_message is None
        assert len(_extract_text(result.content)) > 0

    @pytest.mark.asyncio
    async def test_k3_concurrent_streams(self, models):
        """k3 并发流式调用。"""
        model = models.get_model("kimi-coding", "k3")
        prompts = ["hi", "hello", "hey"]

        async def run_one(prompt: str):
            context = Context(messages=[UserMessage(content=prompt)])
            result = await models.complete_simple(model, context)
            return result

        results = await asyncio.gather(*(run_one(p) for p in prompts))
        for result in results:
            assert result.error_message is None
            assert len(_extract_text(result.content)) > 0

    @pytest.mark.asyncio
    async def test_k3_image_input(self, models):
        """k3 图片输入测试。"""
        import base64
        from pathlib import Path

        from nova_ai.types import ImageContent

        model = models.get_model("kimi-coding", "k3")
        image_path = Path(__file__).parent.parent / "fixtures" / "test.png"
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        context = Context(
            messages=[
                UserMessage(
                    content=[
                        TextContent(text="What is in this image?"),
                        ImageContent(mime_type="image/png", data=image_data),
                    ]
                )
            ]
        )
        result = await models.complete_simple(model, context)
        assert result.error_message is None
        assert len(_extract_text(result.content)) > 0

    @pytest.mark.asyncio
    async def test_kimi_for_coding_image_input(self, models):
        """kimi-for-coding 图片输入测试。"""
        import base64
        from pathlib import Path

        from nova_ai.types import ImageContent

        model = models.get_model("kimi-coding", "kimi-for-coding")
        image_path = Path(__file__).parent.parent / "fixtures" / "test.png"
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        context = Context(
            messages=[
                UserMessage(
                    content=[
                        TextContent(text="What is in this image?"),
                        ImageContent(mime_type="image/png", data=image_data),
                    ]
                )
            ]
        )
        result = await models.complete_simple(model, context)
        assert result.error_message is None
        assert len(_extract_text(result.content)) > 0


@pytest.mark.integration
@pytest.mark.skipif(not VOLCENGINE_API_KEY, reason="VOLCENGINE_API_KEY not set")
class TestVolcengineIntegration:
    """Volcengine 真实 API 集成测试。"""

    @pytest.fixture(scope="class")
    @classmethod
    def models(cls):
        import asyncio

        return asyncio.run(_setup_models("volcengine", VOLCENGINE_API_KEY))

    @pytest.mark.asyncio
    # deepseek-v3-2 用例已移除：该模型于火山方舟下线（404），2026-08-30 集成确认

    @pytest.mark.asyncio
    async def test_deepseek_v4_flash(self, models):
        result = await _test_model(models, "volcengine", "deepseek-v4-flash-260425")
        assert result["available"] is True
        for case in result["cases"]:
            assert case["ok"] is True, f"{case['case']} failed: {case.get('error')}"
            assert len(case["text"]) > 0

    @pytest.mark.asyncio
    async def test_deepseek_v4_pro(self, models):
        result = await _test_model(models, "volcengine", "deepseek-v4-pro-260425")
        assert result["available"] is True
        for case in result["cases"]:
            assert case["ok"] is True, f"{case['case']} failed: {case.get('error')}"
            assert len(case["text"]) > 0

    @pytest.mark.asyncio
    async def test_deepseek_reasoning(self, models):
        """DeepSeek 思考级别测试。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="9.11 和 9.9 哪个大？")])

        for level in [ThinkingLevel.LOW, ThinkingLevel.HIGH]:
            result = await models.complete_simple(
                model, context, SimpleStreamOptions(reasoning=level)
            )
            assert result.error_message is None
            thinking = _extract_thinking(result.content)
            assert len(thinking) > 0, f"{level} should have thinking"

    @pytest.mark.asyncio
    async def test_deepseek_streaming(self, models):
        """DeepSeek 流式调用测试。"""
        from nova_ai.types import TextDeltaEvent, ThinkingDeltaEvent

        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="你好")])

        event_count = 0
        text_parts = []
        thinking_parts = []
        async for event in models.stream_simple(model, context):
            event_count += 1
            if isinstance(event, TextDeltaEvent) and event.delta:
                text_parts.append(event.delta)
            elif isinstance(event, ThinkingDeltaEvent) and event.delta:
                thinking_parts.append(event.delta)

        assert event_count > 0
        full_text = "".join(text_parts)
        full_thinking = "".join(thinking_parts)
        assert len(full_text) > 0 or len(full_thinking) > 0

    @pytest.mark.asyncio
    async def test_deepseek_tool_call(self, models):
        """DeepSeek 工具调用。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        tool = Tool(
            name="get_weather",
            description="Get weather for a city",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        context = Context(
            messages=[UserMessage(content="What's the weather in Beijing?")],
            tools=[tool],
        )
        result = await models.complete_simple(model, context)
        assert result.error_message is None
        tool_calls = [c for c in result.content if c.type == "toolCall"]
        if tool_calls:
            assert tool_calls[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_deepseek_multi_turn(self, models):
        """DeepSeek 多轮对话。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="My name is DeepSeek.")])

        result1 = await models.complete_simple(model, context)
        assert result1.error_message is None

        context2 = Context(
            messages=[
                UserMessage(content="My name is DeepSeek."),
                result1,
                UserMessage(content="What is my name?"),
            ]
        )
        result2 = await models.complete_simple(model, context2)
        assert result2.error_message is None
        assert "DeepSeek" in _extract_text(result2.content)

    @pytest.mark.asyncio
    async def test_deepseek_temperature(self, models):
        """DeepSeek temperature 参数。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="Say hello")])

        result = await models.complete_simple(
            model, context, SimpleStreamOptions(temperature=0.0)
        )
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_deepseek_max_tokens(self, models):
        """DeepSeek max_tokens 参数。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="Tell me a long story")])

        result = await models.complete_simple(
            model, context, SimpleStreamOptions(max_tokens=10)
        )
        assert result.error_message is None
        text = _extract_text(result.content)
        assert len(text) < 500

    @pytest.mark.asyncio
    async def test_deepseek_abort(self, models):
        """DeepSeek abort signal。"""
        from nova_ai import AbortController

        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="Tell me a very long story")])

        controller = AbortController()
        options = SimpleStreamOptions(signal=controller.signal)

        controller.abort()

        result = await models.complete_simple(model, context, options)
        assert result.stop_reason.value in ("aborted", "error")

    @pytest.mark.asyncio
    async def test_deepseek_chinese_input(self, models):
        """DeepSeek 中文输入。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="你好，请用中文回答")])

        result = await models.complete_simple(model, context)
        assert result.error_message is None
        assert len(_extract_text(result.content)) > 0

    @pytest.mark.asyncio
    async def test_deepseek_concurrent_streams(self, models):
        """DeepSeek 并发流式调用。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        prompts = ["hi", "hello", "hey"]

        async def run_one(prompt: str):
            context = Context(messages=[UserMessage(content=prompt)])
            result = await models.complete_simple(model, context)
            return result

        results = await asyncio.gather(*(run_one(p) for p in prompts))
        for result in results:
            assert result.error_message is None
            assert len(_extract_text(result.content)) > 0

    @pytest.mark.asyncio
    async def test_deepseek_cache_prompt_cache_key(self, models):
        """DeepSeek prompt_cache_key 和 cache_retention（可能因服务端不支持而失败）。"""
        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        context = Context(messages=[UserMessage(content="你好")])

        payloads = []

        def on_payload(payload, _model):
            payloads.append(payload)
            return payload

        options = SimpleStreamOptions(
            session_id="test-session-123",
            cache_retention=CacheRetention.LONG,
            on_payload=on_payload,
        )
        result = await models.complete_simple(model, context, options)

        # 验证 payload 发送了 prompt_cache_key / prompt_cache_retention
        assert len(payloads) == 1
        extra_body = payloads[0].get("extra_body", {})
        assert extra_body.get("prompt_cache_key") == "test-session-123"
        assert extra_body.get("prompt_cache_retention") == "24h"

        # 服务端可能不支持缓存，这里只记录结果，不断言成功
        # 如果失败，通常是服务端返回 4xx/5xx，属于服务端限制而非代码问题
        if result.error_message is not None:
            pytest.skip(
                f"Volcengine cache not supported by server: {result.error_message}"
            )

    @pytest.mark.asyncio
    async def test_deepseek_image_input(self, models):
        """DeepSeek 图片输入测试。"""
        import base64
        from pathlib import Path

        from nova_ai.types import ImageContent

        model = models.get_model("volcengine", "deepseek-v4-flash-260425")
        image_path = Path(__file__).parent.parent / "fixtures" / "test.png"
        image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")

        context = Context(
            messages=[
                UserMessage(
                    content=[
                        TextContent(text="What is in this image?"),
                        ImageContent(mime_type="image/png", data=image_data),
                    ]
                )
            ]
        )
        result = await models.complete_simple(model, context)
        assert result.error_message is None
        assert len(_extract_text(result.content)) > 0
