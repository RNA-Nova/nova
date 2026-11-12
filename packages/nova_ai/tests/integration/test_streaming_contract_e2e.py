"""流式契约 e2e（事件序/usage/并发/回调——锁协议形态，不锁内容）。

探针矩阵：每个有凭据的 provider 一个最便宜模型（_helpers/probes.py）。
"""

import asyncio

import pytest
from nova_protocol import Context, UserMessage

from nova_ai.stream_options import SimpleStreamOptions

from ._helpers.auth import build_manager
from ._helpers.probes import Probe, collect_probes
from ._helpers.streams import collect_event_types, extract_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),  # 真实网络 + 思考模型（默认 30s 是密封套件的）
]

_PROBES = collect_probes()


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_event_sequence_integrity(probe: Probe):
    """start 首发、done 收尾、text 块 start<delta<end、无重复 end 事件。"""
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="hi")])
    stream = manager.stream_simple(probe.model, ctx)

    async with asyncio.timeout(120):
        events = await collect_event_types(stream)

    assert events[0] == "start"
    assert events[-1] == "done"
    assert "text_start" in events
    assert events.index("text_start") < events.index("text_delta")
    assert events.index("text_delta") < events.index("text_end")
    assert events.index("text_end") < events.index("done")
    end_types = {"text_end", "thinking_end", "toolcall_end"}
    for i in range(1, len(events)):
        assert not (
            events[i] in end_types and events[i - 1] == events[i]
        ), f"duplicate end event at {i}: {events}"


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_stream_result_is_published_terminal_message(probe: Probe):
    """流终态消息与 result() 是同一对象，不缺事件也不二次请求。"""
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="Say 'ok'")])
    stream = manager.stream_simple(probe.model, ctx)
    async with asyncio.timeout(120):
        events = [event async for event in stream]
    result = await stream.result()
    assert result.error_message is None
    assert events[-1].type == "done"
    assert result is events[-1].message
    assert len(extract_text(result)) > 0
    assert result.usage is not None and result.usage.total_tokens > 0


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_usage_cost_matches_model_pricing(probe: Probe):
    """cost 恒等于 usage × 模型定价（计费一致性契约）。"""
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="hi")])
    async with asyncio.timeout(120):
        result = await manager.complete_simple(probe.model, ctx)

    usage = result.usage
    cost = result.usage.cost
    model = probe.model
    rates = model.cost
    total_input = usage.input + usage.cache_read + usage.cache_write
    applicable = [
        tier for tier in model.cost.tiers or [] if total_input > tier.input_tokens_above
    ]
    if applicable:
        rates = max(applicable, key=lambda tier: tier.input_tokens_above)
    long_write = usage.cache_write_1h or 0
    short_write = usage.cache_write - long_write
    expected = (
        usage.input * rates.input / 1_000_000
        + usage.output * rates.output / 1_000_000
        + usage.cache_read * rates.cache_read / 1_000_000
        + (short_write * rates.cache_write + long_write * rates.input * 2) / 1_000_000
    )
    assert pytest.approx(cost.total, abs=1e-9) == expected


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_concurrent_streams(probe: Probe):
    """三并发流各自完整收尾。"""
    manager = await build_manager()

    async def run_one(prompt: str):
        ctx = Context(messages=[UserMessage(content=prompt)])
        return await manager.complete_simple(probe.model, ctx)

    async with asyncio.timeout(180):
        results = await asyncio.gather(*(run_one(p) for p in ["hi", "hello", "hey"]))
    for result in results:
        assert result.error_message is None
        assert len(extract_text(result)) > 0


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_repeated_iteration_yields_nothing(probe: Probe):
    """已完成流重复遍历不产生事件（单消费者语义）。"""
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="hi")])
    stream = manager.stream_simple(probe.model, ctx)

    async with asyncio.timeout(120):
        first = await collect_event_types(stream)
        second = await collect_event_types(stream)

    assert len(first) > 0
    assert second == []


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_payload_and_response_hooks(probe: Probe):
    """on_payload / on_response 回调在请求生命周期各触发至少一次。"""
    manager = await build_manager()
    payloads = []
    responses = []

    def on_payload(payload, _model):
        payloads.append(payload)
        return payload

    def on_response(response, _model):
        responses.append(response)

    options = SimpleStreamOptions(on_payload=on_payload, on_response=on_response)
    ctx = Context(messages=[UserMessage(content="hi")])
    async with asyncio.timeout(120):
        result = await manager.complete_simple(probe.model, ctx, options)
    assert result.error_message is None
    assert len(payloads) >= 1
    assert len(responses) >= 1
    assert responses[0].status == 200
