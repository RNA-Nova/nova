"""Gateway 配置的严格输入、默认值与 secret 卫生测试。"""

import pytest
from nova_gateway.config import GatewayConfig, UpstreamConfig
from pydantic import ValidationError


def test_config_defaults_are_independent_and_token_repr_is_redacted():
    first = GatewayConfig(api_token="secret")
    second = GatewayConfig()
    first.allow_models.append("model-a")
    first.allow_origins.append("https://app.example")
    first.upstreams.append(UpstreamConfig(provider="test"))
    assert second.allow_models == []
    assert second.allow_origins == []
    assert second.upstreams == []
    assert "secret" not in repr(first)


def test_config_rejects_unknown_fields_and_invalid_port():
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate({"unknown": True})
    with pytest.raises(ValidationError):
        GatewayConfig(port=0)
    with pytest.raises(ValidationError):
        GatewayConfig(port=65536)
