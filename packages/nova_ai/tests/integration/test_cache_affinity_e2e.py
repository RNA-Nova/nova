"""缓存亲和 e2e：prompt cache 参数透传与缓存 usage 字段。

kimi-coding 支持 prompt_cache_key（请求体 extra_body）；其余 provider
只验证缓存参数不破坏请求（厂商各自实现差异大，断言从松）。
"""

import asyncio

import pytest
from nova_protocol import CacheRetention, Context, UserMessage

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


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_cache_options_accepted(probe):
    """session_id + cache_retention 参数被接受，连续两调正常完成。"""
    manager = await build_manager()
    ctx = Context(messages=[UserMessage(content="你好")])
    options = SimpleStreamOptions(
        session_id="e2e-cache-session", cache_retention=CacheRetention.LONG
    )
    async with asyncio.timeout(120):
        first = await manager.complete_simple(probe.model, ctx, options)
        second = await manager.complete_simple(probe.model, ctx, options)
    for result in (first, second):
        assert result.error_message is None
        assert result.usage is not None and result.usage.input >= 0
    assert len(extract_text(second)) > 0


async def test_kimi_coding_cache_control_in_payload():
    """kimi 缓存契约（anthropic 形态）：cache_control 落在 system 块上。

    经 on_payload 观测真实请求体（方言换血退役后 kimi 走 anthropic 缓存标记）。
    """
    if not has_credential("kimi-coding"):
        pytest.skip("kimi-coding 无凭据")
    manager = await build_manager()
    model = manager.get_model("kimi-coding", "kimi-for-coding")
    if model is None:
        pytest.skip("kimi-for-coding 模型缺席")

    payloads = []

    def on_payload(payload, _model):
        payloads.append(payload)
        return payload

    ctx = Context(system_prompt="你是助手", messages=[UserMessage(content="你好")])
    async with asyncio.timeout(120):
        result = await manager.complete_simple(
            model, ctx, SimpleStreamOptions(on_payload=on_payload)
        )
    assert result.error_message is None
    assert len(payloads) == 1
    system = payloads[0].get("system") or []
    assert system and system[0].get("cache_control", {}).get("type") == "ephemeral"
