"""Provider attribution headers 单元测试。"""

from typing import Any, Optional

import pytest

from nova_ai import Model
from nova_ai.types.enums import KnownApi
from nova_ai.types.model import ModelCost
from nova_harness.core.provider_attribution import merge_provider_attribution_headers


class _FakeSettingsManager:
    """测试用的 SettingsManager 替身，只实现 telemetry 开关。"""

    def __init__(self, install_telemetry_enabled: bool = True) -> None:
        self._install_telemetry_enabled = install_telemetry_enabled

    def get_enable_install_telemetry(self) -> bool:
        return self._install_telemetry_enabled


def _make_settings_manager(enabled: bool = True) -> Any:
    return _FakeSettingsManager(enabled)


def _make_model(
    provider: str,
    base_url: str,
    headers: Optional[dict] = None,
) -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url=base_url,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(),
        context_window=4096,
        max_tokens=4096,
        headers=headers,
    )


def test_openrouter_by_provider() -> None:
    model = _make_model("openrouter", "https://openrouter.ai/api/v1")
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers == {
        "HTTP-Referer": "https://nova.dev",
        "X-OpenRouter-Title": "nova",
        "X-OpenRouter-Categories": "cli-agent",
    }


def test_openrouter_by_host() -> None:
    model = _make_model("custom", "https://openrouter.ai/api/v1")
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers is not None
    assert headers["X-OpenRouter-Title"] == "nova"


def test_nvidia_nim_by_provider() -> None:
    model = _make_model("nvidia", "https://integrate.api.nvidia.com/v1")
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers == {"X-BILLING-INVOKE-ORIGIN": "Nova"}


def test_cloudflare_ai_gateway_by_host() -> None:
    model = _make_model(
        "custom",
        "https://gateway.ai.cloudflare.com/v1/account/tag/model",
    )
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers == {"User-Agent": "nova-coding-agent"}


def test_opencode_session_headers() -> None:
    model = _make_model("opencode", "https://opencode.ai/api")
    headers = merge_provider_attribution_headers(
        model, _make_settings_manager(), session_id="sess-123"
    )
    assert headers == {
        "x-opencode-session": "sess-123",
        "x-opencode-client": "nova",
    }


def test_opencode_no_session_id_returns_none() -> None:
    model = _make_model("opencode", "https://opencode.ai/api")
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers is None


def test_unknown_provider_returns_none() -> None:
    model = _make_model("openai", "https://api.openai.com/v1")
    headers = merge_provider_attribution_headers(model, _make_settings_manager())
    assert headers is None


def test_header_sources_override_attribution() -> None:
    model = _make_model("openrouter", "https://openrouter.ai/api/v1")
    headers = merge_provider_attribution_headers(
        model,
        _make_settings_manager(),
        None,
        {"X-OpenRouter-Title": "custom"},
    )
    assert headers is not None
    # 调用方传入的 source 优先级最高，覆盖默认值
    assert headers["X-OpenRouter-Title"] == "custom"
    assert headers["HTTP-Referer"] == "https://nova.dev"


def test_default_attribution_disabled_when_telemetry_off() -> None:
    """安装遥测关闭时，不应附加默认 provider attribution headers。"""
    model = _make_model("openrouter", "https://openrouter.ai/api/v1")
    settings = _make_settings_manager(enabled=False)
    headers = merge_provider_attribution_headers(model, settings)
    assert headers is None


def test_session_headers_remain_when_telemetry_off() -> None:
    """安装遥测关闭时，OpenCode session headers 仍应附加。"""
    model = _make_model("opencode", "https://opencode.ai/api")
    settings = _make_settings_manager(enabled=False)
    headers = merge_provider_attribution_headers(
        model, settings, session_id="sess-123"
    )
    assert headers == {
        "x-opencode-session": "sess-123",
        "x-opencode-client": "nova",
    }


def test_header_sources_still_apply_when_telemetry_off() -> None:
    """安装遥测关闭时，调用方显式传入的 headers 仍应保留。"""
    model = _make_model("openrouter", "https://openrouter.ai/api/v1")
    settings = _make_settings_manager(enabled=False)
    headers = merge_provider_attribution_headers(
        model,
        settings,
        None,
        {"X-Custom": "value"},
    )
    assert headers == {"X-Custom": "value"}
