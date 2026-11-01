"""ModelsManager 集合测试（对齐 TS Models 接口）。"""

import asyncio
from typing import Any, Optional

import pytest
from nova_protocol import (
    ApiKeyAuth,
    ApiKeyCredential,
    Context,
    ErrorEvent,
    KnownApi,
    Model,
    ModelCost,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
    UserMessage,
)

from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.auth.helpers import env_api_key_auth
from nova_ai.auth.resolve import ModelsError
from nova_ai.gateway import InMemoryModelsStore, ModelsManager, create_models_manager
from nova_ai.providers import create_provider
from nova_ai.stream_options import SimpleStreamOptions, StreamOptions
from nova_ai.streaming import create_assistant_message_event_stream
from nova_ai.utils.errors import RequestTimeoutError


def _make_model(
    model_id: str = "test-model",
    provider: str = "test",
    api: Any = KnownApi.OPENAI_COMPLETIONS,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=provider,
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
    )


class _FakeApiImpl:
    def __init__(self):
        self.stream_calls = []
        self.stream_simple_calls = []

    def stream(self, model, context, options=None):
        self.stream_calls.append({"model": model, "options": options})
        stream = create_assistant_message_event_stream()
        stream.end(result=None)
        return stream

    def stream_simple(self, model, context, options=None):
        self.stream_simple_calls.append({"model": model, "options": options})
        stream = create_assistant_message_event_stream()
        stream.end(result=None)
        return stream


def _make_auth(api_key_env: Optional[str] = None) -> ProviderAuth:
    """构造测试用 ProviderAuth。"""
    api_key_auth = (
        env_api_key_auth("Test API Key", [api_key_env]) if api_key_env else None
    )
    return ProviderAuth(api_key=api_key_auth)


class TestModelsProviders:
    def test_set_get_delete_provider(self):
        models = create_models_manager()
        provider = create_provider(id="p1", name="P1")
        models.set_provider(provider)

        assert models.get_provider("p1") is provider
        assert len(models.get_providers()) == 1

        models.delete_provider("p1")
        assert models.get_provider("p1") is None

    def test_clear_providers(self):
        models = create_models_manager()
        models.set_provider(create_provider(id="p1", name="P1"))
        models.set_provider(create_provider(id="p2", name="P2"))
        models.clear_providers()
        assert len(models.get_providers()) == 0


class TestModelsGetModels:
    def test_get_models_by_provider(self):
        model = _make_model()
        provider = create_provider(id="p1", name="P1", models=[model])
        models = create_models_manager()
        models.set_provider(provider)

        assert models.get_models("p1") == [model]
        assert models.get_models("missing") == []

    def test_get_models_all(self):
        m1 = _make_model("m1", "p1")
        m2 = _make_model("m2", "p2")
        models = create_models_manager()
        models.set_provider(create_provider(id="p1", name="P1", models=[m1]))
        models.set_provider(create_provider(id="p2", name="P2", models=[m2]))

        all_models = models.get_models()
        assert len(all_models) == 2

    def test_get_model(self):
        model = _make_model()
        provider = create_provider(id="p1", name="P1", models=[model])
        models = create_models_manager()
        models.set_provider(provider)

        assert models.get_model("p1", "test-model") == model
        assert models.get_model("p1", "missing") is None
        assert models.get_model("missing", "test-model") is None


@pytest.mark.asyncio
class TestModelsAuth:
    async def test_get_auth_api_key_credential(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        result = await models.get_auth("test")
        assert result is not None
        assert result.auth.get("api_key") == "sk-test"

    async def test_get_auth_for_model_merges_headers(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-Custom": "value"}
        result = await models.get_auth_for_model(model)
        assert result is not None
        assert result.auth.get("headers") == {"X-Custom": "value"}

    async def test_check_auth(self):
        store = InMemoryCredentialStore()
        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        # 无 credential 时
        result = await models.check_auth("test")
        assert result is None

        # 有 credential 时
        store.write("test", ApiKeyCredential(key="sk-test"))
        result = await models.check_auth("test")
        assert result is not None
        assert result.type == "api_key"

    async def test_get_available(self):
        store = InMemoryCredentialStore()
        model = _make_model()
        provider = create_provider(
            id="test",
            name="Test",
            models=[model],
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        # 无 credential
        available = await models.get_available()
        assert available == []

        # 有 credential
        store.write("test", ApiKeyCredential(key="sk-test"))
        available = await models.get_available()
        assert available == [model]

    async def test_get_available_with_filter_models(self):
        store = InMemoryCredentialStore()
        m1 = _make_model("m1")
        m2 = _make_model("m2")

        def filter_models(models, credential):
            return [m for m in models if m.id == "m1"]

        provider = create_provider(
            id="test",
            name="Test",
            models=[m1, m2],
            auth=_make_auth(api_key_env="TEST_API_KEY"),
            filter_models=filter_models,
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        store.write("test", ApiKeyCredential(key="sk-test"))
        available = await models.get_available()
        assert available == [m1]


async def _never_resolve(_input):
    """占位 resolve（login/to_auth 专用测试不经此路）。"""
    return None


async def _never_login(_interaction):
    raise AssertionError("本测试不触发 oauth login")


async def _never_refresh(_credential, _signal=None):
    raise AssertionError("本测试不触发 oauth refresh")


class _FakeInteraction:
    """最小 AuthInteraction 桩（login 路径只读 signal；prompt/notify 不消费）。"""

    signal = None

    async def prompt(self, prompt):  # pragma: no cover - 本测试不触发
        raise AssertionError("unexpected prompt")

    def notify(self, event):  # pragma: no cover - 本测试不触发
        raise AssertionError("unexpected notify")


@pytest.mark.asyncio
class TestModelsLoginLogout:
    async def test_login_api_key(self):
        store = InMemoryCredentialStore()

        async def _login(_interaction):
            return ApiKeyCredential(key="sk-login")

        provider = create_provider(
            id="test",
            name="Test",
            auth=ProviderAuth(
                api_key=ApiKeyAuth(name="Test", resolve=_never_resolve, login=_login)
            ),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        credential = await models.login("test", "api_key", _FakeInteraction())
        assert credential.key == "sk-login"

        stored = store.read("test")
        assert stored is not None
        assert stored.key == "sk-login"

    async def test_login_unknown_provider(self):
        models = create_models_manager()
        with pytest.raises(ModelsError, match="Unknown provider"):
            await models.login("missing", "api_key", None)

    async def test_logout(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        await models.logout("test")
        stored = store.read("test")
        assert stored is None


@pytest.mark.asyncio
class TestModelsRefresh:
    async def test_refresh_no_dynamic_providers(self):
        models = create_models_manager()
        result = await models.refresh()
        assert result["aborted"] is False
        assert len(result["errors"]) == 0

    async def test_refresh_oauth_timeout_recorded_not_aborted(self, monkeypatch):
        """refresh 的 OAuth 刷新挂死：超时帽触发——errors 记
        RequestTimeoutError（超时≠取消），result["aborted"] 为 False。"""
        import nova_ai.gateway.models as models_module

        monkeypatch.setattr(models_module, "DEFAULT_OAUTH_REFRESH_TIMEOUT_MS", 50)

        store = InMemoryCredentialStore()
        store.write(
            "test",
            OAuthCredential(access="old-access", refresh="refresh-token", expires=1000),
        )

        never = asyncio.Event()

        async def _hanging_refresh(_credential, _signal=None):
            await never.wait()
            raise AssertionError("超时帽生效时不会走到")

        async def _to_auth(_credential):  # pragma: no cover - 刷新挂死走不到
            raise AssertionError("unexpected to_auth")

        async def _fetch(_context):
            return []

        provider = create_provider(
            id="test",
            name="Test",
            fetch_models=_fetch,
            auth=ProviderAuth(
                oauth=OAuthAuth(
                    name="Test OAuth",
                    login=_never_login,
                    refresh=_hanging_refresh,
                    to_auth=_to_auth,
                ),
            ),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        result = await models.refresh()
        assert result["aborted"] is False
        assert isinstance(result["errors"]["test"], RequestTimeoutError)

    async def test_refresh_deadline_exceeded_records_timeout(self):
        """整轮预算耗尽：网络阶段被 deadline 处死——RequestTimeoutError
        （不冒名 aborted）。"""
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        never = asyncio.Event()

        async def _hanging_fetch(_context):
            await never.wait()
            return []

        provider = create_provider(
            id="test",
            name="Test",
            fetch_models=_hanging_fetch,
            auth=_make_auth(api_key_env="TEST_DEADLINE_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        result = await models.refresh(deadline_ms=50)
        assert result["aborted"] is False
        assert isinstance(result["errors"]["test"], RequestTimeoutError)


@pytest.mark.asyncio
class TestModelsStream:
    async def test_stream_returns_stream_sync(self):
        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)

        # stream 是同步返回的；auth resolve 在后台装配
        assert stream is not None
        async for _ in stream:  # 消费至结束 = 装配完成（确定性，不睡觉）
            pass
        assert len(api.stream_calls) == 1

    async def test_stream_simple_returns_stream_sync(self):
        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream_simple(model, ctx)

        assert stream is not None
        async for _ in stream:  # 消费至结束 = 装配完成
            pass
        assert len(api.stream_simple_calls) == 1

    async def test_stream_auth_failure_emits_error_event(self):
        provider = create_provider(
            id="test",
            name="Test",
            api=_FakeApiImpl(),
            auth=_make_auth(api_key_env=None),  # 无 auth
        )
        models = create_models_manager()
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)

        events = []
        async for event in stream:
            events.append(event)

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert "not configured" in errors[0].error.error_message


@pytest.mark.asyncio
class TestModelsApplyAuth:
    async def test_apply_auth_merges_headers(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-Model": "model"}
        ctx = Context(messages=[UserMessage(content="hi")])
        options = StreamOptions(headers={"X-Options": "options"})

        stream = models.stream(model, ctx, options)
        async for _ in stream:
            pass

        assert len(api.stream_calls) == 1
        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-Model": "model", "X-Options": "options"}

    async def test_apply_auth_base_url_override(self):
        store = InMemoryCredentialStore()
        store.write(
            "test",
            OAuthCredential(access="token", refresh="refresh", expires=9999999999999),
        )

        api = _FakeApiImpl()

        async def _to_auth(credential):
            return {"api_key": "token", "base_url": "https://oauth.example.com"}

        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=ProviderAuth(
                oauth=OAuthAuth(
                    name="Test OAuth",
                    login=_never_login,
                    refresh=_never_refresh,
                    to_auth=_to_auth,
                ),
            ),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)
        async for _ in stream:
            pass

        assert len(api.stream_calls) == 1
        called_model = api.stream_calls[0]["model"]
        assert called_model.base_url == "https://oauth.example.com"


@pytest.mark.asyncio
class TestModelsTransformHeaders:
    """transform_headers：auth 合并后运行、派发前剥离（对齐 TS ModelsStreamTransforms）。"""

    async def _run_stream(self, api, options):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        stream = models.stream(
            _make_model(), Context(messages=[UserMessage(content="hi")]), options
        )
        async for _ in stream:
            pass
        return stream

    async def test_transform_headers_sync(self):
        api = _FakeApiImpl()

        def _transform(headers):
            return {**headers, "X-Transformed": "yes"}

        await self._run_stream(
            api, StreamOptions(headers={"X-A": "a"}, transform_headers=_transform)
        )

        assert len(api.stream_calls) == 1
        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-A": "a", "X-Transformed": "yes"}
        # transform_headers 不会派发给 provider
        assert called_options.transform_headers is None

    async def test_transform_headers_async(self):
        api = _FakeApiImpl()

        async def _transform(headers):
            return {**headers, "X-Async": "yes"}

        await self._run_stream(api, StreamOptions(transform_headers=_transform))

        assert len(api.stream_calls) == 1
        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-Async": "yes"}
        assert called_options.transform_headers is None

    async def test_no_transform_keeps_headers_untouched(self):
        api = _FakeApiImpl()

        await self._run_stream(api, StreamOptions(headers={"X-A": "a"}))

        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-A": "a"}


@pytest.mark.asyncio
class TestModelHeadersResolver:
    """model_headers_resolver 钩子：请求时解析 per-model headers。"""

    async def test_resolver_headers_merged_into_auth(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        def resolver(model, env):
            return {"X-Resolved": f"{model.id}-v"}

        models = ModelsManager(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model("m1"))
        assert result is not None
        assert result.auth["headers"]["X-Resolved"] == "m1-v"

    async def test_resolver_overrides_static_model_headers(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        def resolver(model, env):
            return {"X-K": "from-resolver"}

        models = ModelsManager(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-K": "static"}
        result = await models.get_auth_for_model(model)
        assert result.auth["headers"]["X-K"] == "from-resolver"

    async def test_async_resolver_receives_env(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test", env={"E1": "v1"}))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        seen = {}

        async def resolver(model, env):
            seen["env"] = env
            return None

        models = ModelsManager(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model())
        assert result is not None
        assert seen["env"] is not None

    async def test_no_resolver_unchanged(self):
        store = InMemoryCredentialStore()

        store.write("test", ApiKeyCredential(key="sk-test"))

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = ModelsManager(credential_store=store)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model())
        assert result is not None
        assert "headers" not in result.auth or result.auth.get("headers") is None
