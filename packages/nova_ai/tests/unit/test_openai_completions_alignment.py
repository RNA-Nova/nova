"""openai-completions 重移植（对齐 pi 2026-08 终态）的验收测试。

覆盖本轮修复：reasoning details 拼接与白名单护栏、tool_call_id 两段规范化、
tool_choice 双条件、cache_control 覆盖 tool 结果、thinking budget 字段、
baseten chat_template_args、streamSimple thinking_budgets 透传。
"""

import json

import pytest

from nova_ai.api_impls._shared.simple_options import (
    clamp_thinking_budget_to_answer_room,
    thinking_budget_for_level,
)
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    append_openai_reasoning_detail,
    build_params,
    is_openai_reasoning_detail,
    is_reasoning_field,
    parse_legacy_encrypted_reasoning_detail,
    parse_openai_reasoning_details,
)
from nova_ai.types import (
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    ThinkingContent,
    ToolCall,
    UserMessage,
)
from nova_ai.types.compat import OpenAICompletionsCompat
from nova_ai.types.messages import AssistantMessage


def _model(**overrides) -> Model:
    fields = dict(
        id="test-model",
        name="Test",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://api.openai.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )
    fields.update(overrides)
    return Model(**fields)


def _ctx(*messages) -> Context:
    return Context(messages=list(messages))


# ---------------------------------------------------------------------------
# reasoning details
# ---------------------------------------------------------------------------


class TestReasoningDetails:
    def test_is_reasoning_field_whitelist(self):
        assert is_reasoning_field("reasoning")
        assert is_reasoning_field("reasoning_content")
        assert is_reasoning_field("reasoning_text")
        # 白名单之外一律拒绝——签名会当请求体字段名发送
        assert not is_reasoning_field("reasoning_details")
        assert not is_reasoning_field('{"type":"reasoning.text"}')
        assert not is_reasoning_field("")

    def test_detail_type_guards(self):
        assert is_openai_reasoning_detail({"type": "reasoning.text", "text": "t"})
        assert is_openai_reasoning_detail({"type": "reasoning.summary", "summary": "s"})
        assert is_openai_reasoning_detail({"type": "reasoning.encrypted", "data": "d"})
        assert not is_openai_reasoning_detail({"type": "reasoning.text"})
        assert not is_openai_reasoning_detail({"type": "other", "text": "t"})
        assert not is_openai_reasoning_detail("not-a-dict")

    def test_append_concatenates_same_type_deltas(self):
        """相邻同类型 text/summary delta 拼接为逻辑条目（OpenRouter 流式形态）。"""
        details = []
        append_openai_reasoning_detail(
            details, {"type": "reasoning.text", "text": "Hel", "id": "r1"}
        )
        append_openai_reasoning_detail(
            details, {"type": "reasoning.text", "text": "lo", "id": "r1"}
        )
        # 换类型后另起条目
        append_openai_reasoning_detail(
            details, {"type": "reasoning.summary", "summary": "sum", "id": "r1"}
        )
        assert len(details) == 2
        assert details[0]["text"] == "Hello"
        assert details[1]["summary"] == "sum"

    def test_append_encrypted_stays_discrete(self):
        details = []
        encrypted = {"type": "reasoning.encrypted", "id": "e1", "data": "x"}
        append_openai_reasoning_detail(details, dict(encrypted))
        append_openai_reasoning_detail(details, dict(encrypted))
        # encrypted 不拼接，保持离散
        assert len(details) == 2

    def test_parse_details_roundtrip_and_rejects_garbage(self):
        details = [{"type": "reasoning.text", "text": "t"}]
        signature = json.dumps(details)
        assert parse_openai_reasoning_details(signature) == details
        assert parse_openai_reasoning_details("not json") is None
        assert parse_openai_reasoning_details(json.dumps({"bad": 1})) is None
        assert parse_openai_reasoning_details(None) is None

    def test_parse_legacy_encrypted_detail(self):
        legacy = {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
        assert parse_legacy_encrypted_reasoning_detail(json.dumps(legacy)) == legacy
        # 非加密 detail 不算 legacy 形态
        assert (
            parse_legacy_encrypted_reasoning_detail(
                json.dumps({"type": "reasoning.text", "text": "t"})
            )
            is None
        )

    def test_replay_prefers_signed_details_with_legacy_fallback(self):
        """回放：thinking 签名优先；无签名时兜底读 toolCall 旧挂载。"""
        model = _model()
        signed_block = ThinkingContent(
            type="thinking",
            thinking="because",
            thinking_signature=json.dumps(
                [{"type": "reasoning.text", "text": "because"}]
            ),
        )
        legacy_call = ToolCall(
            type="toolCall",
            id="tc1",
            name="f",
            arguments={},
            thought_signature=json.dumps(
                {"type": "reasoning.encrypted", "id": "tc1", "data": "secret"}
            ),
        )
        # 直接驱动 convert_messages（经 build_params 产物检查）
        from nova_ai.types.messages import AssistantMessage

        same_model_msg = AssistantMessage(
            role="assistant",
            api=model.api,
            provider=model.provider,
            model=model.id,
            content=[signed_block, legacy_call],
        )
        ctx = Context(messages=[UserMessage(content="hi"), same_model_msg])
        params = build_params(model, ctx)
        assistant = next(m for m in params["messages"] if m.get("role") == "assistant")
        # 签名路优先：结构化 details 上线，且不再写原始 reasoning 字段
        assert assistant["reasoning_details"] == [
            {"type": "reasoning.text", "text": "because"}
        ]
        assert "reasoning" not in assistant
        assert "reasoning_content" not in assistant

    def test_signature_whitelist_guard_blocks_arbitrary_field(self):
        """白名单护栏：非法签名值绝不作为请求体字段名发送。"""
        model = _model()
        bad_block = ThinkingContent(
            type="thinking",
            thinking="because",
            thinking_signature='[{"type": "reasoning.text"}]',  # 非法字段名形态
        )
        ctx = Context(
            messages=[
                UserMessage(content="hi"),
                AssistantMessage(role="assistant", content=[bad_block]),
            ]
        )
        params = build_params(model, ctx)
        assistant = next(m for m in params["messages"] if m.get("role") == "assistant")
        # 只发送标准 content，不出现把 JSON 当字段名的键
        assert all(
            key
            in (
                "role",
                "content",
                "tool_calls",
                "reasoning_details",
                "reasoning",
                "reasoning_content",
                "reasoning_text",
            )
            for key in assistant
        )


# ---------------------------------------------------------------------------
# tool_call_id 两段规范化
# ---------------------------------------------------------------------------


def test_normalize_tool_call_id_preserves_item_uniqueness():
    """同回合多个 Responses API 调用共享 call_id——item 段必须保留。"""
    from nova_ai.api_impls.openai_completions import convert_messages

    model = _model(provider="github-copilot")
    assistant = AssistantMessage(
        role="assistant",
        content=[
            ToolCall(type="toolCall", id="call_1|itemA", name="f", arguments={}),
            ToolCall(type="toolCall", id="call_1|itemB", name="f", arguments={}),
        ],
    )
    ctx = Context(
        messages=[
            UserMessage(content="hi"),
            assistant,
        ]
    )
    params = convert_messages(model, ctx, OpenAICompletionsCompat())
    tool_messages = [m for m in params if m.get("role") == "assistant"]
    ids = [tc["id"] for m in tool_messages for tc in m.get("tool_calls", [])]
    assert len(ids) == 2
    assert len(set(ids)) == 2  # 不塌缩


# ---------------------------------------------------------------------------
# tool_choice / cache_control
# ---------------------------------------------------------------------------


def test_tool_choice_omitted_without_tools():
    """tools 为空时不发 tool_choice（严格端点对"有 choice 无 tools"会 400）。"""
    model = _model()
    ctx = _ctx(UserMessage(content="hi"))
    params = build_params(model, ctx, OpenAICompletionsOptions(tool_choice="auto"))
    assert "tool_choice" not in params


def test_cache_control_covers_tool_result_message():
    """openrouter+anthropic 模型：最后一条消息是 tool 结果时 marker 落在它上面。"""
    from nova_ai.types import ToolResultMessage

    model = _model(
        provider="openrouter",
        id="anthropic/claude",
        base_url="https://openrouter.ai/api/v1",
        compat=OpenAICompletionsCompat(cache_control_format="anthropic"),
    )
    ctx = Context(
        messages=[
            UserMessage(content="run it"),
            AssistantMessage(
                role="assistant",
                content=[
                    ToolCall(type="toolCall", id="tc1", name="bash", arguments={})
                ],
            ),
            ToolResultMessage(
                tool_call_id="tc1",
                content=[{"type": "text", "text": "output"}],
            ),
        ]
    )
    params = build_params(
        model,
        ctx,
        OpenAICompletionsOptions(cache_retention="short"),
        cache_retention="short",
    )
    # 最后一条 tool 消息的文本内容带 cache_control
    last_tool = params["messages"][-1]
    assert last_tool["role"] == "tool"
    content = last_tool["content"]
    assert any(part.get("cache_control") for part in content)


# ---------------------------------------------------------------------------
# thinking budget
# ---------------------------------------------------------------------------


class TestThinkingBudget:
    def test_budget_table_and_custom_override(self):
        assert thinking_budget_for_level("low") == 2048
        assert thinking_budget_for_level("xhigh") == 16384  # clamp 到 high
        custom = {"low": 100}
        assert thinking_budget_for_level("low", custom) == 100

    def test_answer_room_clamp(self):
        assert clamp_thinking_budget_to_answer_room(16384, 4096) == 3072
        assert clamp_thinking_budget_to_answer_room(100, 4096) == 100

    def test_budget_field_applied_for_vllm(self):
        """supports_thinking_token_budget 端点：顶层预算字段上线（经 extra_body）。"""
        model = _model(
            reasoning=True,
            max_tokens=65536,
            compat=OpenAICompletionsCompat(
                supports_thinking_token_budget=True,
                thinking_format="chat-template",
            ),
        )
        ctx = _ctx(UserMessage(content="hi"))
        params = build_params(
            model, ctx, OpenAICompletionsOptions(reasoning_effort="high")
        )
        # vLLM 等端点 reasoning 与答案共享 max_tokens，预算必须封顶
        assert params["extra_body"]["thinking_token_budget"] == 16384

    def test_stream_simple_passes_thinking_budgets(self):
        """streamSimple 不再丢弃 thinking_budgets（旧移植的字段丢失 bug）。"""
        from nova_ai.api_impls.openai_completions import _stream as stream_module

        captured = {}

        def _fake_stream(m, c, opts):
            captured["opts"] = opts

            class _Fake:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            return _Fake()

        original = stream_module.stream
        stream_module.stream = _fake_stream
        try:
            from nova_ai.api_impls.openai_completions import stream_simple
            from nova_ai.types import SimpleStreamOptions, ThinkingBudgets

            budgets = ThinkingBudgets(low=999)
            stream_simple(
                _model(reasoning=True),
                _ctx(UserMessage(content="hi")),
                SimpleStreamOptions(
                    api_key="sk-test",
                    reasoning=None,
                    thinking_budgets=budgets,
                ),
            )
            assert captured["opts"].thinking_budgets == budgets
        finally:
            stream_module.stream = original


# ---------------------------------------------------------------------------
# baseten chat_template_args
# ---------------------------------------------------------------------------


def test_baseten_chat_template_args_with_budget_var():
    """baseten 格式：chat_template_args 支持 $var: thinking.budget 变量替换。"""
    model = _model(
        reasoning=True,
        max_tokens=65536,
        compat=OpenAICompletionsCompat(
            thinking_format="baseten",
            supports_thinking_token_budget=True,
            chat_template_args={
                "enable_thinking": {"$var": "thinking.enabled"},
                "budget": {"$var": "thinking.budget"},
            },
        ),
    )
    ctx = _ctx(UserMessage(content="hi"))
    params = build_params(
        model, ctx, OpenAICompletionsOptions(reasoning_effort="medium")
    )
    args = params["extra_body"]["chat_template_args"]
    assert args["enable_thinking"] is True
    assert args["budget"] == 8192  # medium 默认预算（≤ max_tokens-1024）


def test_use_max_tokens_for_deepseek_and_zai():
    """deepseek / zai 端点用 max_tokens 而非 max_completion_tokens。"""
    deepseek = _model(provider="deepseek", base_url="https://api.deepseek.com/v1")
    params = build_params(
        deepseek,
        _ctx(UserMessage(content="hi")),
        OpenAICompletionsOptions(max_tokens=100),
    )
    assert params["max_tokens"] == 100

    zai = _model(provider="zai", base_url="https://api.z.ai/v1")
    params = build_params(
        zai, _ctx(UserMessage(content="hi")), OpenAICompletionsOptions(max_tokens=100)
    )
    assert params["max_tokens"] == 100


# ---------------------------------------------------------------------------
# Models → provider 全链路（集成测试回归的镜像：通用基类选项进协议实现）
# ---------------------------------------------------------------------------


def test_build_params_with_generic_base_options():
    """通用基类选项（StreamOptions/SimpleStreamOptions）进 build_params 不炸。

    Models 高层路径经 applyAuth 后传的是通用选项——协议专属字段
    （reasoning_effort 等）在基类上不存在，必须 getattr 防御。
    集成测试曾抓到此处 AttributeError。
    """
    from nova_ai.types import SimpleStreamOptions, StreamOptions

    model = _model(reasoning=True)
    ctx = _ctx(UserMessage(content="hi"))

    # 基类 StreamOptions（无任何协议专属字段）
    params = build_params(model, ctx, StreamOptions(api_key="sk-test"))
    assert params["model"] == "test-model"

    # SimpleStreamOptions（有 reasoning/thinking_budgets，无 reasoning_effort）
    params = build_params(
        model,
        ctx,
        SimpleStreamOptions(api_key="sk-test", max_tokens=512),
    )
    assert params["max_completion_tokens"] == 512
    # 无 options 对象
    params = build_params(model, ctx, None)
    assert params["model"] == "test-model"
