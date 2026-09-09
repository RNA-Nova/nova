"""Models 集合测试（对齐 TS Models 接口）。"""

import asyncio
from typing import Any, Optional

import pytest

from nova_ai import Context, UserMessage
from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.auth.helpers import env_api_key_auth
from nova_ai.gateway import InMemoryModelsStore, Models, create_models
from nova_ai.providers import create_provider
from nova_ai.types import KnownApi, Model, ModelCost
from nova_ai.types.auth import ApiKeyCredential, OAuthCredential, ProviderAuth
from nova_ai.types.stream_options import SimpleStreamOptions, StreamOptions


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
        from nova_ai.streaming import create_assistant_message_event_stream

        stream = create_assistant_message_event_stream()
        stream.end(result=None)
        return stream

    def stream_simple(self, model, context, options=None):
        self.stream_simple_calls.append({"model": model, "options": options})
        from nova_ai.streaming import create_assistant_message_event_stream

        stream = create_assistant_message_event_stream()
        stream.end(result=None)
        return stream


def _make_auth(api_key_env: Optional[str] = None, oauth: bool = False) -> ProviderAuth:
    """构造测试用 ProviderAuth。"""
    api_key_auth = (
        env_api_key_auth("Test API Key", [api_key_env]) if api_key_env else None
    )
    return ProviderAuth(api_key=api_key_auth, oauth=None if not oauth else object())


class TestModelsProviders:
    def test_set_get_delete_provider(self):
        models = create_models()
        provider = create_provider(id="p1", name="P1")
        models.set_provider(provider)

        assert models.get_provider("p1") is provider
        assert len(models.get_providers()) == 1

        models.delete_provider("p1")
        assert models.get_provider("p1") is None

    def test_clear_providers(self):
        models = create_models()
        models.set_provider(create_provider(id="p1", name="P1"))
        models.set_provider(create_provider(id="p2", name="P2"))
        models.clear_providers()
        assert len(models.get_providers()) == 0


class TestModelsGetModels:
    def test_get_models_by_provider(self):
        model = _make_model()
        provider = create_provider(id="p1", name="P1", models=[model])
        models = create_models()
        models.set_provider(provider)

        assert models.get_models("p1") == [model]
        assert models.get_models("missing") == []

    def test_get_models_all(self):
        m1 = _make_model("m1", "p1")
        m2 = _make_model("m2", "p2")
        models = create_models()
        models.set_provider(create_provider(id="p1", name="P1", models=[m1]))
        models.set_provider(create_provider(id="p2", name="P2", models=[m2]))

        all_models = models.get_models()
        assert len(all_models) == 2

    def test_get_model(self):
        model = _make_model()
        provider = create_provider(id="p1", name="P1", models=[model])
        models = create_models()
        models.set_provider(provider)

        assert models.get_model("p1", "test-model") == model
        assert models.get_model("p1", "missing") is None
        assert models.get_model("missing", "test-model") is None


class TestModelsAuth:
    @pytest.mark.asyncio
    async def test_get_auth_api_key_credential(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        result = await models.get_auth("test")
        assert result is not None
        assert result.auth.get("api_key") == "sk-test"

    @pytest.mark.asyncio
    async def test_get_auth_for_model_merges_headers(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-Custom": "value"}
        result = await models.get_auth_for_model(model)
        assert result is not None
        assert result.auth.get("headers") == {"X-Custom": "value"}

    @pytest.mark.asyncio
    async def test_check_auth(self):
        store = InMemoryCredentialStore()
        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        # 无 credential 时
        result = await models.check_auth("test")
        assert result is None

        # 有 credential 时
        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)
        result = await models.check_auth("test")
        assert result is not None
        assert result.type == "api_key"

    @pytest.mark.asyncio
    async def test_get_available(self):
        store = InMemoryCredentialStore()
        model = _make_model()
        provider = create_provider(
            id="test",
            name="Test",
            models=[model],
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        # 无 credential
        available = await models.get_available()
        assert available == []

        # 有 credential
        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)
        available = await models.get_available()
        assert available == [model]

    @pytest.mark.asyncio
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
        models = Models(credential_store=store)
        models.set_provider(provider)

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)
        available = await models.get_available()
        assert available == [m1]


class TestModelsLoginLogout:
    @pytest.mark.asyncio
    async def test_login_api_key(self):
        store = InMemoryCredentialStore()

        class _LoginAuth:
            async def login(self, interaction):
                return ApiKeyCredential(key="sk-login")

        provider = create_provider(
            id="test",
            name="Test",
            auth=type("Auth", (), {"api_key": _LoginAuth(), "oauth": None})(),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        credential = await models.login("test", "api_key", None)
        assert credential.key == "sk-login"

        stored = await store.read("test")
        assert stored is not None
        assert stored.key == "sk-login"

    @pytest.mark.asyncio
    async def test_login_unknown_provider(self):
        models = create_models()
        with pytest.raises(Exception):
            await models.login("missing", "api_key", None)

    @pytest.mark.asyncio
    async def test_logout(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        await models.logout("test")
        stored = await store.read("test")
        assert stored is None


class TestModelsRefresh:
    @pytest.mark.asyncio
    async def test_refresh_no_dynamic_providers(self):
        models = create_models()
        result = await models.refresh()
        assert result["aborted"] is False
        assert len(result["errors"]) == 0


class TestModelsStream:
    @pytest.mark.asyncio
    async def test_stream_returns_stream_sync(self):
        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        models = Models(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)

        # stream 是同步返回的
        assert stream is not None
        # auth resolve 在后台执行
        await asyncio.sleep(0.1)
        assert len(api.stream_calls) == 1

    @pytest.mark.asyncio
    async def test_stream_simple_returns_stream_sync(self):
        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        models = Models(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream_simple(model, ctx)

        assert stream is not None
        await asyncio.sleep(0.1)
        assert len(api.stream_simple_calls) == 1

    @pytest.mark.asyncio
    async def test_stream_auth_failure_emits_error_event(self):
        provider = create_provider(
            id="test",
            name="Test",
            api=_FakeApiImpl(),
            auth=_make_auth(api_key_env=None),  # 无 auth
        )
        models = create_models()
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)

        events = []
        async for event in stream:
            events.append(event)

        from nova_ai.types.events import ErrorEvent

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(errors) == 1
        assert "not configured" in errors[0].error.error_message


class TestModelsApplyAuth:
    @pytest.mark.asyncio
    async def test_apply_auth_merges_headers(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        api = _FakeApiImpl()
        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-Model": "model"}
        ctx = Context(messages=[UserMessage(content="hi")])
        options = StreamOptions(headers={"X-Options": "options"})

        stream = models.stream(model, ctx, options)
        await asyncio.sleep(0.1)

        assert len(api.stream_calls) == 1
        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-Model": "model", "X-Options": "options"}

    @pytest.mark.asyncio
    async def test_apply_auth_base_url_override(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return OAuthCredential(
                access="token", refresh="refresh", expires=9999999999999
            )

        await store.modify("test", _set)

        api = _FakeApiImpl()

        async def _to_auth(self, credential):
            return {"api_key": "token", "base_url": "https://oauth.example.com"}

        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=ProviderAuth(
                api_key=None,
                oauth=type(
                    "OAuth",
                    (),
                    {
                        "name": "Test OAuth",
                        "login": None,
                        "refresh": None,
                        "to_auth": _to_auth,
                    },
                )(),
            ),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        model = _make_model()
        ctx = Context(messages=[UserMessage(content="hi")])
        stream = models.stream(model, ctx)
        await asyncio.sleep(0.1)

        assert len(api.stream_calls) == 1
        called_model = api.stream_calls[0]["model"]
        assert called_model.base_url == "https://oauth.example.com"


class TestModelsTransformHeaders:
    """transform_headers：auth 合并后运行、派发前剥离（对齐 TS ModelsStreamTransforms）。"""

    async def _run_stream(self, api, options):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            api=api,
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        stream = models.stream(
            _make_model(), Context(messages=[UserMessage(content="hi")]), options
        )
        await asyncio.sleep(0.1)
        return stream

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_transform_headers_async(self):
        api = _FakeApiImpl()

        async def _transform(headers):
            return {**headers, "X-Async": "yes"}

        await self._run_stream(api, StreamOptions(transform_headers=_transform))

        assert len(api.stream_calls) == 1
        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-Async": "yes"}
        assert called_options.transform_headers is None

    @pytest.mark.asyncio
    async def test_no_transform_keeps_headers_untouched(self):
        api = _FakeApiImpl()

        await self._run_stream(api, StreamOptions(headers={"X-A": "a"}))

        called_options = api.stream_calls[0]["options"]
        assert called_options.headers == {"X-A": "a"}


class TestModelHeadersResolver:
    """model_headers_resolver 钩子：请求时解析 per-model headers。"""

    @pytest.mark.asyncio
    async def test_resolver_headers_merged_into_auth(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        def resolver(model, env):
            return {"X-Resolved": f"{model.id}-v"}

        models = Models(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model("m1"))
        assert result is not None
        assert result.auth["headers"]["X-Resolved"] == "m1-v"

    @pytest.mark.asyncio
    async def test_resolver_overrides_static_model_headers(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        def resolver(model, env):
            return {"X-K": "from-resolver"}

        models = Models(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        model = _make_model()
        model.headers = {"X-K": "static"}
        result = await models.get_auth_for_model(model)
        assert result.auth["headers"]["X-K"] == "from-resolver"

    @pytest.mark.asyncio
    async def test_async_resolver_receives_env(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test", env={"E1": "v1"})

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )

        seen = {}

        async def resolver(model, env):
            seen["env"] = env
            return None

        models = Models(credential_store=store, model_headers_resolver=resolver)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model())
        assert result is not None
        assert seen["env"] is not None

    @pytest.mark.asyncio
    async def test_no_resolver_unchanged(self):
        store = InMemoryCredentialStore()

        async def _set(_current):
            return ApiKeyCredential(key="sk-test")

        await store.modify("test", _set)

        provider = create_provider(
            id="test",
            name="Test",
            auth=_make_auth(api_key_env="TEST_API_KEY"),
        )
        models = Models(credential_store=store)
        models.set_provider(provider)

        result = await models.get_auth_for_model(_make_model())
        assert result is not None
        assert "headers" not in result.auth or result.auth.get("headers") is None
