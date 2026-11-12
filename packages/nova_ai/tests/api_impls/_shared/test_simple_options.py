"""_shared/simple_options.py 的采样分层与公共选项转发测试。"""

from nova_protocol import Context, KnownApi, Model, ModelCost, ThinkingLevel

from nova_ai.api_impls._shared.simple_options import build_base_options
from nova_ai.stream_options import SimpleStreamOptions


def _model() -> Model:
    return Model(
        id="m",
        name="m",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="test",
        base_url="https://example.com/v1",
        reasoning=True,
        thinking_level_map={"off": "none", "high": "high"},
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
        sampling_params={"temperature": 0.2, "top_k": 1},
        sampling_params_by_thinking_level={"high": {"temperature": 0.1, "top_k": 8}},
    )


def test_sampling_params_use_effective_thinking_level_then_request_override():
    options = SimpleStreamOptions(
        reasoning=ThinkingLevel.HIGH,
        sampling_params={"top_p": 0.9, "top_k": 4},
    )
    result = build_base_options(_model(), Context(messages=[]), options)
    assert result.sampling_params == {"temperature": 0.1, "top_k": 4, "top_p": 0.9}


def test_simple_tool_choice_is_forwarded():
    result = build_base_options(
        _model(), Context(messages=[]), SimpleStreamOptions(tool_choice="none")
    )
    assert result.tool_choice == "none"
