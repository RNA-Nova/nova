"""缓存亲和 e2e：prompt cache 参数透传与缓存 usage 字段。

kimi-coding 支持 prompt_cache_key（请求体 extra_body）；其余 provider
只验证缓存参数不破坏请求（厂商各自实现差异大，断言从松）。
"""

import asyncio

import pytest
from nova_ai.stream_options import SimpleStreamOptions
from nova_protocol import CacheRetention, Context, UserMessage

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


async def test_kimi_coding_cache_contract_in_payload():
    """kimi 缓存契约（对齐 pi compat 实况）：缺省零客户端缓存标记。

    kimi-coding 是 openai-completions 且 pi 的 kimi compat 无
    cacheControlFormat——缺省请求（无 session、缺省保留）既不发 anthropic
    形态 cache_control，也不发 prompt_cache_key（auto 启发式：api.openai.com
    或 long+supportsLongCacheRetention 才发，pi 同款）；缓存归端点自动
    （usage 报 cached_tokens）。本用例曾断言 anthropic 方言的 system
    cache_control——协议切换后该前提作废，重写为实况契约。
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
    body = payloads[0]
    extra = body.get("extra_body") or {}
    assert "prompt_cache_key" not in extra
    assert "prompt_cache_retention" not in extra
    # 消息体内也不得出现 anthropic 形态缓存标记
    for message in body.get("messages") or []:
        content = message.get("content")
        blocks = content if isinstance(content, list) else [content]
        for block in blocks:
            if isinstance(block, dict):
                assert "cache_control" not in block
    # 端点自动缓存的 usage 字段正常解析（cache_read ≥ 0，字段存在即可）
    assert result.usage is not None and result.usage.cache_read >= 0
