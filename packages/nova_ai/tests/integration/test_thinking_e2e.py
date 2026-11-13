"""思考（reasoning）契约 e2e：级别映射/开关/回放。

探针矩阵中无思考能力的模型自动跳过对应断言。
"""

import asyncio

import pytest
from nova_protocol import Context, ThinkingLevel, UserMessage

from nova_ai.stream_options import SimpleStreamOptions

from ._helpers.auth import build_manager
from ._helpers.probes import collect_probes
from ._helpers.streams import extract_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),  # 真实网络 + 思考模型（默认 30s 是密封套件的）
]

# 探针 = 每 provider 最便宜的"支持思考级别"模型（级别表非空且有生效档）
_PROBES = collect_probes(
    require=lambda m: bool(
        m.thinking_level_map
        and any(v is not None for v in m.thinking_level_map.values())
    )
)

# ThinkingLevel 枚举值 → thinking_level_map 键（枚举类型层面不含 off——
# 关闭思考用 reasoning=None 表达，见 nova_protocol enums.py）
_LEVEL_KEYS = {
    ThinkingLevel.MINIMAL: "minimal",
    ThinkingLevel.LOW: "low",
    ThinkingLevel.MEDIUM: "medium",
    ThinkingLevel.HIGH: "high",
    ThinkingLevel.XHIGH: "xhigh",
    ThinkingLevel.MAX: "max",
}


def _supported_levels(model):
    """模型实际支持的思考级别（map 中值为非 None 的键，off 除外）。"""
    level_map = model.thinking_level_map or {}
    return [
        level
        for level, key in _LEVEL_KEYS.items()
        if key != "off" and level_map.get(key) is not None
    ]


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_supported_thinking_levels_complete(probe):
    """模型声明支持的每个思考级别都能正常完成，不要求特定输出文本。"""
    levels = _supported_levels(probe.model)
    assert levels  # 探针选型已保证（require 谓词）
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="9.11 和 9.9 哪个大？")])
    for level in levels:
        async with asyncio.timeout(180):
            result = await manager.complete_simple(
                probe.model, ctx, SimpleStreamOptions(reasoning=level)
            )
        assert result.error_message is None, f"{level} 请求应正常完成"
        assert result.content, f"{level} 应返回 assistant 内容块"


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_thinking_off_completes_with_text_only(probe):
    """思考关闭：正常完成，且请求不启用任何思考控制。"""
    level_map = probe.model.thinking_level_map or {}
    if "off" not in level_map:
        pytest.skip(f"{probe.label} 不支持思考开关")
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="1+1=?")])
    payloads = []

    def capture(payload, _identity):
        payloads.append(payload)

    async with asyncio.timeout(120):
        result = await manager.complete_simple(
            probe.model,
            ctx,
            SimpleStreamOptions(on_payload=capture),
        )
    assert result.error_message is None
    assert len(extract_text(result)) > 0
    assert len(payloads) == 1

    off = level_map["off"]
    payload = payloads[0]
    extra = payload.get("extra_body") or {}
    # 关闭语义在请求面：各协议只能缺席或发送目录声明的 off 值，
    # 不接受厂商是否额外输出 thinking 文本作为判据。
    assert payload.get("reasoning_effort") in (None, off)
    assert payload.get("reasoning") in (None, off)
    assert extra.get("thinking") in (None, off)


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_reasoning_multi_turn_replay(probe):
    """多轮对话：思考模型携带上轮消息继续作答（回放路径真实可用）。"""
    levels = _supported_levels(probe.model)
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="My name is Nova.")])
    options = SimpleStreamOptions(reasoning=levels[0])
    async with asyncio.timeout(180):
        result1 = await manager.complete_simple(probe.model, ctx, options)
    assert result1.error_message is None

    ctx2 = Context(
        messages=[
            UserMessage(content="My name is Nova."),
            result1,
            UserMessage(content="What is my name?"),
        ]
    )
    async with asyncio.timeout(180):
        result2 = await manager.complete_simple(probe.model, ctx2, options)
    assert result2.error_message is None
    assert "Nova" in extract_text(result2)
