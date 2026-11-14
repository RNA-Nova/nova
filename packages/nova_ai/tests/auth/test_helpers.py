"""auth/helpers.py 测试（lazy_oauth 的延迟加载与复用语义）。"""

import asyncio

import pytest
from nova_ai.auth import OAuthAuth
from nova_ai.auth.helpers import lazy_oauth
from nova_async import AbortSignal
from nova_protocol import AuthorizationRequest, AuthPrompt, ModelAuth, OAuthCredential
from tests._helpers.auth import oauth_credential

pytestmark = pytest.mark.asyncio


class _Interaction:
    signal: AbortSignal | None = None

    async def prompt(self, prompt: AuthPrompt) -> str:
        return "device_code"

    async def acquire_authorization_code(self, request: AuthorizationRequest) -> str:
        return "code"

    def notify(self, event) -> None:
        pass


def _fake_oauth(name: str = "fake") -> OAuthAuth:
    async def login(_interaction):
        return oauth_credential(access="a", refresh="r", expires=1)

    async def refresh(credential, _signal=None):
        return credential

    async def to_auth(credential):
        return ModelAuth(api_key=credential.access)

    return OAuthAuth(name=name, login=login, refresh=refresh, to_auth=to_auth)


async def test_lazy_oauth_forwards_subscription_metadata():
    async def load():
        return _fake_oauth()

    oauth = lazy_oauth("Fake", load, is_subscription=True)
    assert oauth.is_subscription is True


async def test_lazy_oauth_loads_once_and_is_reawaitable():
    """load 只跑一次；login/refresh/to_auth 重复调用不炸（Task 可重复 await）。"""
    load_calls = []

    async def load():
        load_calls.append(1)
        return _fake_oauth()

    oauth = lazy_oauth("Fake", load)

    await oauth.login(_Interaction())
    credential = oauth_credential(access="a", refresh="r", expires=1)
    await oauth.refresh(credential, None)
    auth = await oauth.to_auth(credential)
    assert auth.get("api_key") == "a"
    assert len(load_calls) == 1


async def test_lazy_oauth_concurrent_first_access_shares_one_load():
    """并发首访共享同一次加载（Task 记忆化，无双驱动裸协程）。"""
    load_calls = []

    async def load():
        load_calls.append(1)
        await asyncio.sleep(0)  # 让出事件循环，制造真实并发窗口
        return _fake_oauth()

    oauth = lazy_oauth("Fake", load)

    credential = oauth_credential(access="a", refresh="r", expires=1)
    results = await asyncio.gather(
        oauth.refresh(credential, None),
        oauth.to_auth(credential),
        oauth.refresh(credential, None),
    )
    assert len(results) == 3
    assert len(load_calls) == 1


@pytest.mark.asyncio
async def test_lazy_oauth_survives_waiter_cancellation():
    """lazy_oauth 共享加载任务不被等待者取消传染（shield）——首个等待者
    被取消后，后续调用照常完成（此前会永久毒化：缓存的已取消 task 让
    一切后续调用自发 CancelledError）。"""
    from nova_protocol import OAuthCredential, RedactedString

    load_count = 0

    async def _load():
        nonlocal load_count
        load_count += 1
        await asyncio.sleep(0)  # 给取消一个窗口
        return _fake_oauth()

    auth = lazy_oauth("test", _load)
    credential = oauth_credential(
        access=RedactedString("a"), refresh=RedactedString("r"), expires=1
    )

    first = asyncio.ensure_future(auth.to_auth(credential))
    await asyncio.sleep(0)  # 引导：首个调用落到加载等待点
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    # 共享加载未被传染杀死——后续调用照常完成
    assert (await auth.to_auth(credential)).get("api_key") == "a"
    assert load_count == 1  # 加载只发生一次（缓存未被毒化重载）


async def test_lazy_oauth_reload_after_load_failure():
    """加载失败后死 task 出缓存——下次调用重新加载（不永久毒化）。"""
    from nova_protocol import OAuthCredential, RedactedString

    load_count = 0

    async def _load():
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            raise RuntimeError("first load boom")
        return _fake_oauth()

    auth = lazy_oauth("test", _load)
    credential = oauth_credential(
        access=RedactedString("a"), refresh=RedactedString("r"), expires=1
    )

    with pytest.raises(RuntimeError, match="first load boom"):
        await auth.to_auth(credential)
    # 失败的 task 已出缓存——下次调用重新加载并成功
    assert (await auth.to_auth(credential)).get("api_key") == "a"
    assert load_count == 2
