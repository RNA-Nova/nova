"""StreamOptions 家族（stream_options.py）测试——dataclass 选型与构造纪律。"""

import pytest
from nova_protocol import CacheRetention, Model, ThinkingLevel, Transport

from nova_ai.stream_options import (
    ModelIdentity,
    SimpleStreamOptions,
    StreamOptions,
    ThinkingBudgets,
)


class TestStreamOptions:
    def test_default(self):
        opts = StreamOptions()
        assert opts.temperature is None
        assert opts.max_tokens is None
        assert opts.transport is None
        assert opts.cache_retention is None

    def test_with_values(self):
        opts = StreamOptions(
            temperature=0.7,
            max_tokens=1000,
            transport=Transport.SSE,
            cache_retention=CacheRetention.SHORT,
        )
        assert opts.temperature == 0.7
        assert opts.transport == Transport.SSE
        assert opts.cache_retention == CacheRetention.SHORT

    def test_signal_and_callbacks_are_plain_fields(self):
        """signal/on_payload/on_response/transform_headers 是普通 dataclass 字段。"""
        opts = StreamOptions(signal="dummy", on_payload=lambda x: x)
        assert opts.signal == "dummy"
        assert callable(opts.on_payload)
        assert opts.on_response is None
        assert opts.transform_headers is None

    def test_extra_fields_forbidden(self):
        """选项类禁止未声明字段，写错字段立即抛错而非静默丢弃。"""
        with pytest.raises(TypeError):
            StreamOptions(bogus_field=1)


class TestSimpleStreamOptions:
    def test_with_values(self):
        opts = SimpleStreamOptions(
            temperature=0.5,
            reasoning=ThinkingLevel.HIGH,
        )
        assert opts.temperature == 0.5
        assert opts.reasoning == ThinkingLevel.HIGH

    def test_max_retry_delay_ms_preserved(self):
        """max_retry_delay_ms 是已声明字段，构造后保留原值。"""
        opts = SimpleStreamOptions(max_retry_delay_ms=5000)
        assert opts.max_retry_delay_ms == 5000

    def test_extra_fields_forbidden(self):
        with pytest.raises(TypeError):
            SimpleStreamOptions(unknown_option=1)
        with pytest.raises(TypeError):
            ThinkingBudgets(loww=100)


def test_options_repr_hides_api_key_and_headers():
    """repr 防线：StreamOptions 的 api_key/headers 不进 dataclass 默认 repr
    （security 纪律：含密结构白名单/排除 repr——"顺手打印"不泄）。"""
    options = StreamOptions(
        api_key="sk-test-SECRETKEY",
        headers={"Authorization": "Bearer sk-live-SECRETKEY"},
        env={"PROVIDER_TOKEN": "sk-env-SECRETKEY"},
    )
    text = repr(options)
    assert "sk-test-SECRETKEY" not in text
    assert "sk-live-SECRETKEY" not in text
    assert "sk-env-SECRETKEY" not in text


def test_model_identity_strips_url_credentials_query_and_fragment():
    model = Model.model_construct(
        provider="p",
        id="m",
        base_url=(
            "https://user:pass@example.com/v1?api_key=sk-query-secret#refresh-secret"
        ),
    )
    identity = ModelIdentity.from_model(model)
    assert identity.base_url == "https://example.com/v1"
    assert "secret" not in repr(identity)
    assert "user:pass" not in repr(identity)
