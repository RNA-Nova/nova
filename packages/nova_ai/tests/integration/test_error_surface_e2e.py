"""错误面与参数面 e2e：无效凭据的错误终态、参数透传不破坏请求。

断言从松（锁错误类型/终态形态，不锁错误文案——文案是厂商的）。
"""

import asyncio
import os

import pytest
from nova_protocol import AbortController, Context, StopReason, UserMessage

from nova_ai import builtin_models
from nova_ai.api_impls.openai_completions import OpenAICompletionsOptions
from nova_ai.stream_options import SimpleStreamOptions

from ._helpers.auth import build_manager, has_credential
from ._helpers.probes import collect_probes
from ._helpers.streams import extract_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),  # 真实网络 + 思考模型（默认 30s 是密封套件的）
]

_PROBES = collect_probes()


async def test_invalid_api_key_ends_with_error(monkeypatch):
    """无效 key：流以 error 终态收场（不是挂死、不是异常逃逸）。

    经 env 链注入坏 key（manager 无存储凭据时 env 生效——本用例自建
    空凭据店的 manager，与 build_manager 的真实凭据链隔离）。
    """
    monkeypatch.setenv("VOLCENGINE_API_KEY", "invalid-key")
    manager = builtin_models()
    model = manager.get_model("volcengine", "deepseek-v4-flash-260425")
    assert model is not None

    ctx = Context(messages=[UserMessage(content="hi")])
    stream = manager.stream(model, ctx)
    async with asyncio.timeout(60):
        async for _event in stream:
            pass
    result = await stream.result()
    assert result.stop_reason == StopReason.ERROR
    assert result.error_message  # 有错误信息


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_common_options_accepted(probe):
    """max_retries=0/timeout/自定义 headers 不破坏请求。

    （temperature 不在此列——厂商限制各异（kimi k3 只接受 1），
    专属用例归本文件的 test_kimi_coding_temperature_restriction。）
    """
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="hi")])
    options = OpenAICompletionsOptions(
        max_retries=0,
        timeout=60,
        headers={"X-E2E-Probe": "nova"},
    )
    async with asyncio.timeout(120):
        result = await manager.complete(probe.model, ctx, options)
    assert result.error_message is None
    assert len(extract_text(result)) > 0


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_max_tokens_truncates(probe):
    """max_tokens=1：输出截断并以 LENGTH 收尾。

    思考模型例外（pi ``adjustMaxTokensForThinking`` 同款语义）：
    ``max_tokens`` 会加上思考预算再上线（1 + budget），截断语义不成立
    ——只钉"调整生效 + 正常完成"。
    """
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="Tell me a long story.")])
    async with asyncio.timeout(120):
        result = await manager.complete_simple(
            probe.model, ctx, SimpleStreamOptions(max_tokens=1)
        )
    assert result.error_message is None
    if not probe.model.reasoning:
        assert result.usage.output <= 2  # 允许小误差
    # LENGTH 是契约主形态；kimi 对 max_tokens=1 会提前 end_turn（厂商差异）
    assert result.stop_reason in (StopReason.LENGTH, StopReason.STOP)


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_abort_before_start(probe):
    """举旗进入：流必须以 ABORTED 收尾，且不得携带错误分类。"""
    manager = await build_manager()
    controller = AbortController()
    ctx = Context(messages=[UserMessage(content="Tell me a very long story")])
    options = SimpleStreamOptions(signal=controller.signal)
    controller.abort()
    result = await manager.complete_simple(probe.model, ctx, options)
    assert result.stop_reason == StopReason.ABORTED
    assert result.error_kind is None


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_abort_after_first_chunk(probe):
    """在飞中断：收到首个 text_delta 后 abort——流必须收尾而不是挂死。"""
    from ._helpers.streams import collect_event_types

    manager = await build_manager()
    controller = AbortController()
    ctx = Context(messages=[UserMessage(content="Tell me a very long story.")])
    stream = manager.stream_simple(
        probe.model, ctx, SimpleStreamOptions(signal=controller.signal)
    )

    events = []
    async with asyncio.timeout(120):
        async for event in stream:
            events.append(event.type)
            if event.type == "text_delta":
                controller.abort()

    result = await stream.result()
    assert "text_delta" in events  # 确实进入了在飞状态
    assert result.stop_reason == StopReason.ABORTED
    assert result.error_kind is None
