"""ModelRuntime 测试：组合、鉴权链、快照与动态注册。

覆盖：
- models.json 自定义模型与覆盖（credential-blind：不烘焙 api_key/Authorization）
- provider 三层合成（内置 → models.json → 扩展注册）
- 请求时鉴权解析（runtime override → stored → models.json key → env → OAuth）
- 可用性快照（同步近似 + async 精确刷新）
- 扩展 provider 注册/注销与 stream_fn 调度
- login/logout
"""

import json
from pathlib import Path

import pytest
from nova_ai import OpenAICompletionsCompat
from nova_ai.types.auth import OAuthCredential

from nova_harness.core.model import ModelRuntime
from nova_harness.core.model.composer import compose_provider
from nova_harness.core.types.model import ProviderConfigInput
from tests._helpers.auth_storage import auth_storage_in_memory


def _write_models_json(tmp_path: Path, data: dict) -> str:
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _runtime(tmp_path: Path, models_data: dict, storage=None) -> ModelRuntime:
    return ModelRuntime(
        storage or auth_storage_in_memory({}),
        _write_models_json(tmp_path, models_data),
    )


@pytest.fixture
def auth_with_volc_key():
    storage = auth_storage_in_memory({})
    storage.set_runtime_api_key("volcengine", "volc-key")
    return storage


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------


def test_loads_builtin_models(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert model is not None
    assert model.provider == "volcengine"
    assert model.base_url == "https://ark.cn-beijing.volces.com/api/v3/"


def test_custom_models_from_models_json(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "local-key",
                    "api": "openai-completions",
                    "models": [
                        {"id": "local-model", "name": "Local", "context_window": 32000}
                    ],
                }
            }
        },
    )
    model = runtime.find("custom", "local-model")
    assert model is not None
    assert model.base_url == "http://localhost:8000/v1"
    assert model.context_window == 32000


def test_custom_models_do_not_bake_authorization(tmp_path):
    """credential-blind：api_key/auth_header 不写入 Model.headers。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "local-key",
                    "api": "openai-completions",
                    "auth_header": True,
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    model = runtime.find("custom", "m1")
    assert model is not None
    assert not (model.headers or {}).get("Authorization")


def test_custom_models_without_api_key_allowed(tmp_path):
    """对齐 TS：自定义模型不强制 models.json 内写 api_key。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost:8000/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    assert runtime.find("custom", "m1") is not None
    assert runtime.get_error() is None


def test_custom_model_level_base_url_wins(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://provider-level/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m1", "base_url": "http://model-level/v1"}],
                }
            }
        },
    )
    assert runtime.find("custom", "m1").base_url == "http://model-level/v1"


def test_custom_model_thinking_level_map_and_compat(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api": "openai-completions",
                    "compat": {"thinking_format": "openai"},
                    "thinking_level_map": {"low": "L", "medium": "M"},
                    "models": [
                        {"id": "m1"},
                        {"id": "m2", "thinking_level_map": {"high": "H"}},
                    ],
                }
            }
        },
    )
    m1 = runtime.find("custom", "m1")
    assert isinstance(m1.compat, OpenAICompletionsCompat)
    assert m1.compat.thinking_format == "openai"
    assert m1.thinking_level_map == {"low": "L", "medium": "M"}
    assert runtime.find("custom", "m2").thinking_level_map == {"high": "H"}


def test_invalid_models_json_sets_load_error(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not json", encoding="utf-8")
    runtime = ModelRuntime(auth_storage_in_memory({}), str(path))
    assert runtime.get_error() is not None


def test_invalid_provider_config_becomes_composition_error(tmp_path):
    """单个 provider 配置错误不拖垮整体加载，错误按 provider 记录。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "broken": {"models": [{"id": "m1"}]},  # 缺 base_url
                "ok": {
                    "base_url": "http://ok/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m2"}],
                },
            }
        },
    )
    assert runtime.find("ok", "m2") is not None
    assert runtime.find("broken", "m1") is None
    error = runtime.get_error()
    assert error is not None and 'Provider "broken"' in error


# ---------------------------------------------------------------------------
# 覆盖
# ---------------------------------------------------------------------------


def test_provider_override_base_url_on_builtin(auth_with_volc_key, tmp_path):
    runtime = _runtime(
        tmp_path,
        {"providers": {"volcengine": {"base_url": "http://proxy/v1"}}},
        storage=auth_with_volc_key,
    )
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert model.base_url == "http://proxy/v1"


def test_model_overrides_on_builtin(auth_with_volc_key, tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "volcengine": {
                    "model_overrides": {
                        "deepseek-v4-flash-260425": {
                            "max_tokens": 9999,
                            "cost": {"input": 1.5},
                        }
                    }
                }
            }
        },
        storage=auth_with_volc_key,
    )
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert model.max_tokens == 9999
    assert model.cost.input == 1.5


def test_model_override_merges_thinking_level_map(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m1", "thinking_level_map": {"low": "L"}}],
                    "model_overrides": {"m1": {"thinking_level_map": {"medium": "M"}}},
                }
            }
        },
    )
    assert runtime.find("custom", "m1").thinking_level_map == {
        "low": "L",
        "medium": "M",
    }


def test_model_override_merges_chat_template_kwargs(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom/v1",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "m1",
                            "compat": {"chat_template_kwargs": {"a": 1, "b": 2}},
                        }
                    ],
                    "model_overrides": {
                        "m1": {"compat": {"chat_template_kwargs": {"b": 3}}}
                    },
                }
            }
        },
    )
    assert runtime.find("custom", "m1").compat.chat_template_kwargs == {
        "a": 1,
        "b": 3,
    }


def test_model_override_preserves_cost_tiers(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom/v1",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "m1",
                            "cost": {
                                "input": 1.0,
                                "tiers": [
                                    {
                                        "input_tokens_above": 1000,
                                        "input": 2.0,
                                        "output": 4.0,
                                        "cache_read": 0.0,
                                        "cache_write": 0.0,
                                    }
                                ],
                            },
                        }
                    ],
                    "model_overrides": {"m1": {"cost": {"input": 9.9}}},
                }
            }
        },
    )
    cost = runtime.find("custom", "m1").cost
    assert cost.input == 9.9
    assert cost.tiers is not None and cost.tiers[0].input == 2.0


# ---------------------------------------------------------------------------
# 请求时鉴权解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_api_key_from_runtime_override(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert await runtime.get_api_key(model) == "volc-key"


@pytest.mark.asyncio
async def test_get_api_key_from_stored_credential():
    storage = auth_storage_in_memory(
        {"volcengine": {"type": "api_key", "key": "stored-key"}}
    )
    runtime = ModelRuntime(storage)
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert await runtime.get_api_key(model) == "stored-key"


@pytest.mark.asyncio
async def test_get_api_key_from_models_json_key(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost/v1",
                    "api_key": "json-key",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    assert await runtime.get_api_key(runtime.find("custom", "m1")) == "json-key"


@pytest.mark.asyncio
async def test_stored_credential_beats_models_json_key(tmp_path):
    storage = auth_storage_in_memory({"custom": {"type": "api_key", "key": "stored"}})
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost/v1",
                    "api_key": "json-key",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
        storage=storage,
    )
    assert await runtime.get_api_key(runtime.find("custom", "m1")) == "stored"


@pytest.mark.asyncio
async def test_auth_header_injects_bearer_at_request_time(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost/v1",
                    "api_key": "json-key",
                    "api": "openai-completions",
                    "auth_header": True,
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    auth = await runtime.get_request_auth(runtime.find("custom", "m1"))
    assert auth is not None
    assert auth.auth["headers"]["Authorization"] == "Bearer json-key"


@pytest.mark.asyncio
async def test_provider_headers_resolved_at_request_time(auth_with_volc_key, tmp_path):
    """provider 级 headers 不烘焙进 Model，请求时经 auth 链注入。"""
    runtime = _runtime(
        tmp_path,
        {"providers": {"volcengine": {"headers": {"X-Custom": "yes"}}}},
        storage=auth_with_volc_key,
    )
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert not (model.headers or {}).get("X-Custom")
    auth = await runtime.get_request_auth(model)
    assert auth.auth["headers"]["X-Custom"] == "yes"


@pytest.mark.asyncio
async def test_get_api_key_resolves_oauth_access_token():
    storage = auth_storage_in_memory({})
    runtime = ModelRuntime(storage)

    async def _set_oauth(_current):
        return OAuthCredential(
            access="kimi-oauth-token", refresh="r", expires=9999999999999
        )

    await storage.modify("kimi-coding", _set_oauth)

    model = runtime.find("kimi-coding", "k3")
    assert model is not None
    assert await runtime.get_api_key(model) == "kimi-oauth-token"
    assert await runtime.get_api_key_for_provider("kimi-coding") == "kimi-oauth-token"


def test_builtin_provider_untouched_without_overlay():
    """无覆盖时内置 provider 原样进入集合（保持 auth/stream 行为精确）。"""
    runtime = ModelRuntime(auth_storage_in_memory({}))
    provider = runtime.get_provider("kimi-coding")
    assert provider is not None
    # 未经 composer 重组：正是构造期捕获的那个内置实例
    assert provider is runtime._builtins["kimi-coding"]


def test_oauth_preserved_under_models_json_overlay(tmp_path):
    """models.json 覆盖内置 OAuth provider 时，OAuth 能力不丢失。"""
    runtime = _runtime(
        tmp_path,
        {"providers": {"kimi-coding": {"headers": {"X-Team": "a"}}}},
    )
    provider = runtime.get_provider("kimi-coding")
    assert provider is not None
    assert provider.auth is not None and provider.auth.oauth is not None


# ---------------------------------------------------------------------------
# 可用性快照
# ---------------------------------------------------------------------------


def test_get_available_sync_snapshot(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    providers = {m.provider for m in runtime.get_available_snapshot()}
    assert "volcengine" in providers


def test_has_configured_auth(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert runtime.has_configured_auth(model)
    kimi = runtime.find("kimi-coding", "k3")
    assert not runtime.has_configured_auth(kimi)


@pytest.mark.asyncio
async def test_refresh_availability_marks_oauth():
    storage = auth_storage_in_memory({})
    runtime = ModelRuntime(storage)

    async def _set_oauth(_current):
        return OAuthCredential(access="a", refresh="r", expires=9999999999999)

    await storage.modify("kimi-coding", _set_oauth)
    await runtime.refresh_availability()

    assert runtime.is_using_oauth("kimi-coding")
    kimi = runtime.find("kimi-coding", "k3")
    assert runtime.has_configured_auth(kimi)


def test_get_provider_auth_status(tmp_path):
    storage = auth_storage_in_memory({"stored-p": {"type": "api_key", "key": "k"}})
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "json-p": {
                    "base_url": "http://x/v1",
                    "api_key": "literal-key",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
        storage=storage,
    )
    assert runtime.get_provider_auth_status("stored-p") == {
        "configured": True,
        "source": "stored",
    }
    assert runtime.get_provider_auth_status("json-p")["source"] == "models_json_key"
    assert runtime.get_provider_auth_status("nonexistent") == {"configured": False}


# ---------------------------------------------------------------------------
# 扩展 provider 注册
# ---------------------------------------------------------------------------


def test_register_provider_replaces_models(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    runtime.register_provider(
        "volcengine",
        {
            "base_url": "http://new",
            "api_key": "new-key",
            "api": "openai-completions",
            "models": [{"id": "only-model"}],
        },
    )
    assert runtime.find("volcengine", "deepseek-v4-flash-260425") is None
    assert runtime.find("volcengine", "only-model") is not None


def test_unregister_provider_restores_builtin(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    runtime.register_provider(
        "volcengine",
        {
            "base_url": "http://new",
            "api_key": "new-key",
            "api": "openai-completions",
            "models": [{"id": "only-model"}],
        },
    )
    runtime.unregister_provider("volcengine")
    assert runtime.find("volcengine", "deepseek-v4-flash-260425") is not None


def test_reregister_merges_defined_fields(auth_with_volc_key):
    """重复注册时新定义的字段覆盖、未定义的保留（对齐 TS 合并语义）。"""
    runtime = ModelRuntime(auth_with_volc_key)
    runtime.register_provider(
        "p",
        {
            "base_url": "http://a",
            "api_key": "k1",
            "api": "openai-completions",
            "models": [{"id": "m1"}],
        },
    )
    runtime.register_provider("p", {"api_key": "k2"})
    config = runtime.get_registered_provider_config("p")
    assert config.api_key == "k2"
    assert config.base_url == "http://a"
    assert runtime.find("p", "m1") is not None


def test_register_stream_fn_dispatched_for_matching_api(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    calls = []

    def my_stream(model, context, options=None):
        calls.append(model.id)
        raise NotImplementedError

    runtime.register_provider(
        "myprovider",
        {
            "base_url": "http://x",
            "api_key": "k",
            "api": "openai-completions",
            "models": [{"id": "m1"}],
        },
        stream_fn=my_stream,
    )
    provider = runtime.get_provider("myprovider")
    model = runtime.find("myprovider", "m1")
    with pytest.raises(NotImplementedError):
        provider.stream_simple(model, context=None)
    assert calls == ["m1"]


def test_register_stream_fn_via_dict_key(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)

    def my_stream(model, context, options=None):
        raise NotImplementedError

    runtime.register_provider(
        "myprovider",
        {
            "base_url": "http://x",
            "api_key": "k",
            "api": "openai-completions",
            "models": [{"id": "m1"}],
            "stream_simple": my_stream,
        },
    )
    model = runtime.find("myprovider", "m1")
    provider = runtime.get_provider("myprovider")
    with pytest.raises(NotImplementedError):
        provider.stream_simple(model, context=None)


def test_register_stream_fn_requires_api(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)

    def my_stream(model, context, options=None):
        raise NotImplementedError

    with pytest.raises(ValueError, match='"api" is required'):
        runtime.register_provider(
            "broken",
            {"base_url": "http://x", "models": [{"id": "m1"}]},
            stream_fn=my_stream,
        )


def test_register_extension_compat_applies_without_models(auth_with_volc_key):
    """扩展无 models 替换时，compat/thinking_level_map 作为整表覆盖合并。"""
    runtime = ModelRuntime(auth_with_volc_key)
    runtime.register_provider(
        "volcengine",
        {
            "compat": {"supports_store": True},
            "thinking_level_map": {"xhigh": "X"},
        },
    )
    model = runtime.find("volcengine", "deepseek-v4-flash-260425")
    assert model is not None
    assert model.compat.supports_store is True
    assert model.thinking_level_map == {"xhigh": "X"}


def test_model_overrides_apply_to_extension_models(auth_with_volc_key, tmp_path):
    """model_overrides 是最顶层用户配置，对扩展注册的模型同样生效。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "ext-p": {
                    "model_overrides": {"e1": {"max_tokens": 1234}},
                }
            }
        },
        storage=auth_with_volc_key,
    )
    runtime.register_provider(
        "ext-p",
        {
            "base_url": "http://x",
            "api_key": "k",
            "api": "openai-completions",
            "models": [{"id": "e1"}],
        },
    )
    assert runtime.find("ext-p", "e1").max_tokens == 1234


def test_provider_config_input_is_pure_data():
    from nova_harness.core.types.model import ProviderConfigInput

    assert "stream_simple" not in ProviderConfigInput.model_fields


# ---------------------------------------------------------------------------
# 登录 / 登出
# ---------------------------------------------------------------------------


class _FakeInteraction:
    def __init__(self, answer: str):
        self.answer = answer
        self.signal = None

    async def prompt(self, prompt):
        return self.answer

    def notify(self, event):
        pass


@pytest.mark.asyncio
async def test_login_logout_api_key(tmp_path):
    storage = auth_storage_in_memory({})
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://localhost/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
        storage=storage,
    )

    await runtime.login("custom", "api_key", _FakeInteraction("logged-in-key"))
    assert await runtime.get_api_key_for_provider("custom") == "logged-in-key"
    assert runtime.has_configured_auth(runtime.find("custom", "m1"))

    await runtime.logout("custom")
    assert await runtime.get_api_key_for_provider("custom") is None
    assert not runtime.has_configured_auth(runtime.find("custom", "m1"))


# ---------------------------------------------------------------------------
# composer 单元级
# ---------------------------------------------------------------------------


def test_compose_provider_requires_no_api_key():
    """组合层本身不强制 api_key（auth 可来自 stored/env）。"""
    provider = compose_provider(
        "bare",
        None,
        None,
        ProviderConfigInput.model_validate(
            {
                "base_url": "http://x",
                "api": "openai-completions",
                "models": [{"id": "m1"}],
            }
        ),
    )
    assert provider.get_model("m1") is not None


# ---------------------------------------------------------------------------
# 动态模型刷新（对齐 TS ModelRuntime.refresh 链路）
# ---------------------------------------------------------------------------


def _make_model(model_id: str, provider: str = "dyn", max_tokens: int = 8192):
    from nova_ai import Model, ModelCost

    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="http://dyn/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0),
        context_window=128000,
        max_tokens=max_tokens,
    )


def _make_dynamic_base(fetch):
    from nova_ai import create_provider
    from nova_ai.api_impls import openai_completions
    from nova_ai.auth.helpers import env_api_key_auth
    from nova_ai.types.auth import ProviderAuth

    return create_provider(
        id="dyn",
        name="dyn",
        base_url="http://dyn/v1",
        models=[_make_model("old-m")],
        api=openai_completions,
        auth=ProviderAuth(api_key=env_api_key_auth("Dyn key", ["NOVA_TEST_DYN_KEY"])),
        fetch_models=fetch,
    )


@pytest.mark.asyncio
async def test_composed_provider_preserves_dynamic_refresh(tmp_path, monkeypatch):
    """被 models.json 覆盖后，内置动态 provider 的刷新能力不丢失，
    且刷新到达的新模型同样经过 model_overrides。"""
    monkeypatch.setenv("NOVA_TEST_DYN_KEY", "dyn-key")

    async def fetch(_context):
        return [_make_model("new-m")]

    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "dyn": {
                    "headers": {"X-Team": "a"},
                    "model_overrides": {"new-m": {"max_tokens": 111}},
                }
            }
        },
    )
    runtime._builtins["dyn"] = _make_dynamic_base(fetch)
    runtime._recompose_provider("dyn")

    provider = runtime.get_provider("dyn")
    assert provider is not None
    assert getattr(provider, "refresh_models", None) is not None

    await runtime.refresh(allow_network=True)

    model_ids = [m.id for m in provider.get_models()]
    assert "old-m" in model_ids and "new-m" in model_ids
    assert provider.get_model("new-m").max_tokens == 111


def test_composed_provider_without_refresh_capability():
    """无 base 刷新、无扩展刷新、无 modify_models 时不可刷新。"""
    provider = compose_provider(
        "static-p",
        None,
        None,
        ProviderConfigInput.model_validate(
            {
                "base_url": "http://x",
                "api": "openai-completions",
                "models": [{"id": "m1"}],
            }
        ),
    )
    assert getattr(provider, "refresh_models", None) is None


@pytest.mark.asyncio
async def test_extension_refresh_models_fn(tmp_path):
    """扩展 refresh_models_fn 的结果进入模型列表（先校验再发布）。"""
    from nova_harness.core.types.model import ModelDefinition

    calls = []

    async def refresh_fn(context):
        calls.append(context)
        return [ModelDefinition(id="e2")]

    runtime = _runtime(tmp_path, {})
    runtime.register_provider(
        "ext-p",
        {
            "base_url": "http://x",
            "api_key": "k",
            "api": "openai-completions",
            "models": [{"id": "e1"}],
        },
        refresh_models_fn=refresh_fn,
    )

    provider = runtime.get_provider("ext-p")
    assert getattr(provider, "refresh_models", None) is not None

    await runtime.refresh(allow_network=True)

    assert len(calls) == 1
    model_ids = [m.id for m in provider.get_models()]
    assert model_ids == ["e2"] or model_ids == ["e1", "e2"]


@pytest.mark.asyncio
async def test_extension_oauth_login_and_modify_models(tmp_path):
    """扩展 OAuth：login 持久化 credential，modify_models 在刷新后生效。"""
    from nova_harness.core.types.model import ExtensionOAuthConfig

    async def oauth_login(interaction):
        return {"access": "ext-tok", "refresh": "r", "expires": 9999999999999}

    async def oauth_refresh(credential):
        return credential

    oauth = ExtensionOAuthConfig(
        name="Ext OAuth",
        login=oauth_login,
        refresh_token=oauth_refresh,
        get_api_key=lambda credential: credential.access,
        modify_models=lambda models, _cred: [m for m in models if m.id != "secret-m"],
    )

    runtime = _runtime(tmp_path, {})
    runtime.register_provider(
        "oauth-p",
        {
            "base_url": "http://x",
            "api": "openai-completions",
            "models": [{"id": "secret-m"}, {"id": "pub-m"}],
        },
        oauth=oauth,
    )

    provider = runtime.get_provider("oauth-p")
    assert provider.auth is not None and provider.auth.oauth is not None

    await runtime.login("oauth-p", "oauth", _FakeInteraction("ignored"))
    assert await runtime.get_api_key_for_provider("oauth-p") == "ext-tok"

    model_ids = [m.id for m in provider.get_models()]
    assert "secret-m" not in model_ids and "pub-m" in model_ids


# ---------------------------------------------------------------------------
# Runtime credential 管理与 models-store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_remove_runtime_api_key_updates_snapshot(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    model = runtime.find("custom", "m1")
    assert not runtime.has_configured_auth(model)

    await runtime.set_runtime_api_key("custom", "rt-key")
    assert runtime.has_configured_auth(model)
    credentials = await runtime.list_credentials()
    assert any(c.provider_id == "custom" for c in credentials)

    await runtime.remove_runtime_api_key("custom")
    assert not runtime.has_configured_auth(model)


@pytest.mark.asyncio
async def test_file_models_store_roundtrip(tmp_path):
    from nova_ai.gateway.store import ModelsStoreEntry

    from nova_harness.core.model.store import FileModelsStore

    store = FileModelsStore(str(tmp_path / "models-store.json"))
    entry = ModelsStoreEntry(models=[_make_model("m1")], checked_at=123)

    await store.write("dyn", entry)
    loaded = await store.read("dyn")
    assert loaded is not None
    assert loaded.models[0].id == "m1"
    assert loaded.checked_at == 123

    await store.delete("dyn")
    assert await store.read("dyn") is None


@pytest.mark.asyncio
async def test_refresh_restores_models_from_store_when_offline(tmp_path, monkeypatch):
    """离线刷新：从 models-store 缓存恢复动态模型，不触网。"""
    from nova_ai.gateway.store import InMemoryModelsStore, ModelsStoreEntry

    monkeypatch.setenv("NOVA_TEST_DYN_KEY", "dyn-key")

    models_store = InMemoryModelsStore()
    await models_store.write(
        "dyn", ModelsStoreEntry(models=[_make_model("cached-m")], checked_at=1)
    )

    async def fetch(_context):  # 离线时不应被调用
        raise AssertionError("network should not be touched when offline")

    runtime = ModelRuntime(
        auth_storage_in_memory({}),
        _write_models_json(tmp_path, {}),
        models_store=models_store,
        allow_model_network=False,
    )
    runtime._builtins["dyn"] = _make_dynamic_base(fetch)
    runtime._recompose_provider("dyn")

    await runtime.refresh()
    model_ids = [m.id for m in runtime.get_provider("dyn").get_models()]
    assert "cached-m" in model_ids


# ---------------------------------------------------------------------------
# 配置值语法（$VAR 语义在鉴权链中的表现）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_json_env_ref_resolves_at_request_time(tmp_path, monkeypatch):
    """models.json 中 ``$VAR`` 引用在请求时解析为环境变量值。"""
    monkeypatch.setenv("NOVA_TEST_CFG_KEY", "from-env")
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "$NOVA_TEST_CFG_KEY",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    assert await runtime.get_api_key(runtime.find("custom", "m1")) == "from-env"


@pytest.mark.asyncio
async def test_models_json_literal_key_used_as_is(tmp_path, monkeypatch):
    """裸字符串按字面量处理，即使恰好有同名环境变量也不替换。"""
    monkeypatch.setenv("literal-key", "should-not-win")
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "literal-key",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    assert await runtime.get_api_key(runtime.find("custom", "m1")) == "literal-key"


@pytest.mark.asyncio
async def test_missing_env_ref_surfaces_named_error(tmp_path):
    """env 引用缺失时抛出带变量名的错误，而非静默当字面量。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "$NOVA_TEST_MISSING_CFG_KEY",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    with pytest.raises(Exception, match="NOVA_TEST_MISSING_CFG_KEY"):
        await runtime.get_api_key(runtime.find("custom", "m1"))


def test_missing_env_ref_reports_not_configured(tmp_path):
    """env 引用缺失时快照与 auth status 都报告未配置。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "$NOVA_TEST_MISSING_CFG_KEY",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    assert not runtime.has_configured_auth(runtime.find("custom", "m1"))
    assert runtime.get_provider_auth_status("custom") == {"configured": False}


def test_env_ref_auth_status_reports_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_TEST_CFG_KEY", "from-env")
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "$NOVA_TEST_CFG_KEY",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    status = runtime.get_provider_auth_status("custom")
    assert status["configured"] is True
    assert status["source"] == "environment"
    assert status["label"] == "NOVA_TEST_CFG_KEY"


# ---------------------------------------------------------------------------
# 可用性并发合并 / runtime auth status / overrides / async get_available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_availability_coalesces_concurrent_calls(tmp_path):
    """并发 refresh_availability 合并到同一个 inflight 任务。"""
    runtime = _runtime(tmp_path, {})

    calls = []
    original = runtime._run_availability_refresh

    async def counting():
        calls.append(1)
        await original()

    runtime._run_availability_refresh = counting
    import asyncio

    await asyncio.gather(
        runtime.refresh_availability(),
        runtime.refresh_availability(),
        runtime.refresh_availability(),
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_force_refresh_waits_for_inflight(tmp_path):
    """force 刷新排在当前 inflight 之后（mutation 不读旧状态）。"""
    runtime = _runtime(tmp_path, {})
    order = []

    async def slow():
        order.append("first")
        import asyncio

        await asyncio.sleep(0.05)
        runtime._configured_providers = {"a"}

    runtime._run_availability_refresh = slow
    await runtime.refresh_availability()
    assert order == ["first"]

    async def fast():
        order.append("second")
        runtime._configured_providers = {"b"}

    runtime._run_availability_refresh = fast
    await runtime._force_refresh_availability()
    assert order == ["first", "second"]
    assert runtime._configured_providers == {"b"}


@pytest.mark.asyncio
async def test_get_provider_auth_status_runtime_source(tmp_path):
    runtime = _runtime(tmp_path, {})
    await runtime.set_runtime_api_key("volcengine", "rt-key")
    assert runtime.get_provider_auth_status("volcengine") == {
        "configured": True,
        "source": "runtime",
    }
    await runtime.remove_runtime_api_key("volcengine")


@pytest.mark.asyncio
async def test_get_request_auth_with_overrides(tmp_path):
    """overrides 的 api_key 优先于配置链。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "json-key",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        },
    )
    model = runtime.find("custom", "m1")
    assert (await runtime.get_request_auth(model)).auth["api_key"] == "json-key"
    overridden = await runtime.get_request_auth(model, api_key="override-key")
    assert overridden.auth["api_key"] == "override-key"


@pytest.mark.asyncio
async def test_get_available_async_with_provider_filter(auth_with_volc_key):
    runtime = ModelRuntime(auth_with_volc_key)
    volc = await runtime.get_available("volcengine")
    assert volc and all(m.provider == "volcengine" for m in volc)
    assert await runtime.get_available("nonexistent") == []
    snapshot = runtime.get_available_snapshot()
    assert any(m.provider == "volcengine" for m in snapshot)


# ---------------------------------------------------------------------------
# 自定义 AuthContext 与配置冻结
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_ref_resolves_via_custom_auth_context(tmp_path, monkeypatch):
    """$VAR 引用经 AuthContext 解析：值不在 os.environ 中也能命中。"""
    monkeypatch.delenv("CTX_ONLY_KEY", raising=False)

    class CustomCtx:
        async def env(self, name):
            return {"CTX_ONLY_KEY": "from-custom-ctx"}.get(name)

        async def fileExists(self, path):
            return False

    runtime = ModelRuntime(
        auth_storage_in_memory({}),
        _write_models_json(
            tmp_path,
            {
                "providers": {
                    "custom": {
                        "base_url": "http://x/v1",
                        "api_key": "$CTX_ONLY_KEY",
                        "api": "openai-completions",
                        "headers": {"X-Token": "$CTX_ONLY_KEY"},
                        "models": [{"id": "m1"}],
                    }
                }
            },
        ),
        auth_context=CustomCtx(),
    )
    auth = await runtime.get_request_auth(runtime.find("custom", "m1"))
    assert auth is not None
    assert auth.auth["api_key"] == "from-custom-ctx"
    assert auth.auth["headers"]["X-Token"] == "from-custom-ctx"


def test_config_types_are_frozen():
    """models.json 配置类型冻结：顶层赋值被拒绝（对齐 TS deepFreeze 的顶层语义）。"""
    from pydantic import ValidationError

    from nova_harness.core.types.model import (
        ModelsConfig,
        ProviderConfig,
    )

    cfg = ModelsConfig.model_validate({"providers": {"p": {"base_url": "http://x"}}})
    with pytest.raises(ValidationError):
        cfg.providers = {}

    provider_cfg = ProviderConfig(base_url="http://x")
    with pytest.raises(ValidationError):
        provider_cfg.base_url = "http://y"


# ---------------------------------------------------------------------------
# per-model headers 请求时解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_model_headers_resolve_at_request_time(tmp_path):
    """models.json 的 per-model headers 不烘焙进 Model，请求时解析注入。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [{"id": "m1", "headers": {"X-Team": "blue"}}],
                }
            }
        },
    )
    model = runtime.find("custom", "m1")
    assert model.headers is None

    auth = await runtime.get_request_auth(model)
    assert auth.auth["headers"]["X-Team"] == "blue"


@pytest.mark.asyncio
async def test_per_model_headers_precedence(tmp_path):
    """优先级：model_overrides < models[] 定义 < 扩展注册定义。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "ext-p": {
                    "base_url": "http://x/v1",
                    "api_key": "k",
                    "model_overrides": {"e1": {"headers": {"X-K": "override"}}},
                    "models": [
                        {
                            "id": "e1",
                            "api": "openai-completions",
                            "headers": {"X-K": "definition"},
                        }
                    ],
                }
            }
        },
    )
    # models.json 定义（definition）覆盖 override
    auth = await runtime.get_request_auth(runtime.find("ext-p", "e1"))
    assert auth.auth["headers"]["X-K"] == "definition"

    # 扩展注册定义最优先
    runtime.register_provider(
        "ext-p",
        {
            "base_url": "http://x/v1",
            "api_key": "k",
            "api": "openai-completions",
            "models": [{"id": "e1", "headers": {"X-K": "extension"}}],
        },
    )
    auth = await runtime.get_request_auth(runtime.find("ext-p", "e1"))
    assert auth.auth["headers"]["X-K"] == "extension"


@pytest.mark.asyncio
async def test_per_model_header_env_ref_resolves_without_reload(tmp_path, monkeypatch):
    """per-model headers 中的 $VAR 在请求时解析：env 变化无需 reload。"""
    monkeypatch.setenv("NOVA_MODEL_HEADER", "v1")
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [
                        {"id": "m1", "headers": {"X-Ver": "$NOVA_MODEL_HEADER"}}
                    ],
                }
            }
        },
    )
    model = runtime.find("custom", "m1")
    assert (await runtime.get_request_auth(model)).auth["headers"]["X-Ver"] == "v1"

    monkeypatch.setenv("NOVA_MODEL_HEADER", "v2")
    assert (await runtime.get_request_auth(model)).auth["headers"]["X-Ver"] == "v2"


@pytest.mark.asyncio
async def test_per_model_header_missing_env_raises_named_error(tmp_path):
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [
                        {"id": "m1", "headers": {"X-Ver": "$NOVA_MISSING_MODEL_HDR"}}
                    ],
                }
            }
        },
    )
    with pytest.raises(Exception, match="NOVA_MISSING_MODEL_HDR"):
        await runtime.get_request_auth(runtime.find("custom", "m1"))


def test_override_headers_not_baked_into_model(tmp_path):
    """model_overrides 的 headers 不写入 Model.headers（请求时由 resolver 注入）。"""
    runtime = _runtime(
        tmp_path,
        {
            "providers": {
                "custom": {
                    "base_url": "http://x/v1",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                    "model_overrides": {"m1": {"headers": {"X-O": "1"}}},
                }
            }
        },
    )
    assert runtime.find("custom", "m1").headers is None
