"""auth guidance 文案与 UIAuthInteraction 桥接测试。"""

import asyncio

import pytest
from nova_ai.signal import AbortController
from nova_ai.types.auth import AuthEvent, AuthPrompt, AuthPromptOption
from nova_harness.core.config.auth.guidance import (
    format_no_api_key_found_message,
    format_no_auth_message,
    format_no_model_selected_message,
    format_no_models_available_message,
    format_oauth_reauth_message,
)
from nova_harness.core.config.auth.interaction import (
    LoginCancelledError,
    UIAuthInteraction,
)
from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse

# ---------------------------------------------------------------------------
# guidance
# ---------------------------------------------------------------------------


def test_no_models_available_message():
    assert "No models available" in format_no_models_available_message()
    assert "/login" in format_no_models_available_message()


def test_no_model_selected_message():
    msg = format_no_model_selected_message()
    assert "No model selected" in msg
    assert "/login" in msg and "/model" in msg


def test_no_api_key_found_message():
    msg = format_no_api_key_found_message("volcengine")
    assert "volcengine" in msg and "/login" in msg


def test_oauth_reauth_message():
    msg = format_oauth_reauth_message("kimi-coding")
    assert "kimi-coding" in msg
    assert "/login kimi-coding" in msg


def test_no_auth_message_branches():
    assert format_no_auth_message("p", True) == format_oauth_reauth_message("p")
    assert format_no_auth_message("p", False) == format_no_api_key_found_message("p")


# ---------------------------------------------------------------------------
# UIAuthInteraction
# ---------------------------------------------------------------------------


class _FakeUI(UIContext):
    """测试用 UIContext：返回预置的响应，记录通知。

    遵循泛型契约：request 接受可选 signal 并与其竞速
    （abort → cancelled），对齐 TransportUIContext 的行为。
    """

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.messages = []
        self.statuses = []

    @property
    def capabilities(self):
        return {"select", "input", "confirm", "notify"}

    async def request(self, method, params, signal=None):
        if signal is not None:
            race = asyncio.ensure_future(self._respond(method))
            abort = asyncio.ensure_future(signal.wait())
            try:
                done, _ = await asyncio.wait(
                    {race, abort}, return_when=asyncio.FIRST_COMPLETED
                )
                if abort in done:
                    race.cancel()
                    return UIResponse(cancelled=True)
                return race.result()
            finally:
                abort.cancel()
                race.cancel()
        return await self._respond(method)

    async def _respond(self, method):
        value = self._responses.get(method)
        if value is None:
            return UIResponse(cancelled=True)
        return UIResponse(value=value)

    def notify(self, method, params):
        if method == "notify":
            self.messages.append(params)
        elif method == "setStatus":
            self.statuses.append(params)

    def set_component(self, region, component):
        pass

    def patch_component(self, key, props):
        pass

    def remove_component(self, key):
        pass


@pytest.mark.asyncio
async def test_prompt_select_maps_label_to_id():
    ui = _FakeUI({"select": "Plus"})
    interaction = UIAuthInteraction(ui)
    result = await interaction.prompt(
        AuthPrompt(
            type="select",
            message="Choose account",
            options=[
                AuthPromptOption(id="acc-1", label="Free"),
                AuthPromptOption(id="acc-2", label="Plus"),
            ],
        )
    )
    assert result == "acc-2"


@pytest.mark.asyncio
async def test_prompt_input_returns_text():
    ui = _FakeUI({"input": "sk-typed"})
    interaction = UIAuthInteraction(ui)
    result = await interaction.prompt(AuthPrompt(type="secret", message="Enter key"))
    assert result == "sk-typed"


@pytest.mark.asyncio
async def test_prompt_cancel_raises():
    ui = _FakeUI({})  # 无预置响应 → cancelled
    interaction = UIAuthInteraction(ui)
    with pytest.raises(LoginCancelledError):
        await interaction.prompt(AuthPrompt(type="secret", message="Enter key"))


@pytest.mark.asyncio
async def test_prompt_aborted_signal_raises_immediately():
    controller = AbortController()
    controller.abort()
    ui = _FakeUI({"input_box": "sk-typed"})
    interaction = UIAuthInteraction(ui, signal=controller.signal)
    with pytest.raises(LoginCancelledError):
        await interaction.prompt(AuthPrompt(type="secret", message="Enter key"))


@pytest.mark.asyncio
async def test_prompt_signal_race_aborts_pending_request():
    """abort 时挂起的 request 按 cancelled 收尾 → LoginCancelledError。

    竞速语义在 UIContext 实现侧（本测试的 fake 与 TransportUIContext 同款）；
    UIAuthInteraction 只负责把 cancelled 翻译为 LoginCancelledError。
    """
    controller = AbortController()

    class _SlowUI(_FakeUI):
        async def _respond(self, method):
            await asyncio.sleep(10)
            return UIResponse(value="never")

    ui = _SlowUI()
    interaction = UIAuthInteraction(ui, signal=controller.signal)

    async def abort_soon():
        await asyncio.sleep(0.02)
        controller.abort()

    asyncio.ensure_future(abort_soon())
    with pytest.raises(LoginCancelledError):
        await interaction.prompt(AuthPrompt(type="secret", message="Enter key"))


def test_notify_device_code_renders_code_and_url():
    ui = _FakeUI()
    interaction = UIAuthInteraction(ui)
    interaction.notify(
        AuthEvent(
            type="device_code",
            userCode="ABCD-1234",
            verificationUri="https://example.com/device",
            expiresInSeconds=900,
        )
    )
    assert ui.messages
    text = ui.messages[0]["message"]
    assert "ABCD-1234" in text and "https://example.com/device" in text
    # 等待状态由授权等待框（type="auth"）承载——不再另发 progress 通知
    # （轮询期重发会常驻状态行，登录完成后残留 "Waiting for authentication..."）
    progress = [m for m in ui.messages if m.get("type") == "progress"]
    assert not progress


def test_notify_progress_sets_status():
    ui = _FakeUI()
    interaction = UIAuthInteraction(ui)
    interaction.notify(AuthEvent(type="progress", message="Polling..."))
    assert ui.messages[-1]["message"] == "Polling..."
    assert ui.messages[-1]["type"] == "progress"


def test_notify_auth_url_renders_url_and_instructions():
    ui = _FakeUI()
    interaction = UIAuthInteraction(ui)
    interaction.notify(
        AuthEvent(
            type="auth_url", url="https://login.example.com", instructions="Sign in"
        )
    )
    assert "https://login.example.com" in ui.messages[0]["message"]
    assert "Sign in" in ui.messages[0]["message"]


# 浏览器自动打开（auth_url / device_code → webbrowser.open）
# ---------------------------------------------------------------------------


def test_auth_url_auto_opens_browser_once_per_url(monkeypatch):
    """auth_url 事件自动打开浏览器；同一 URL 不重复打开。"""
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    interaction = UIAuthInteraction(_FakeUI())
    interaction.notify(AuthEvent(type="auth_url", url="https://auth.example/abc"))
    interaction.notify(AuthEvent(type="auth_url", url="https://auth.example/abc"))
    assert opened == ["https://auth.example/abc"]


def test_device_code_prefers_complete_uri_and_dedupes_with_auth_url(monkeypatch):
    """device_code 优先 verificationUriComplete；与 auth_url 同 URL 不重复开。"""
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    interaction = UIAuthInteraction(_FakeUI())
    interaction.notify(
        AuthEvent(
            type="device_code",
            verificationUri="https://auth.example/device",
            verificationUriComplete="https://auth.example/complete?code=1",
            userCode="ABCD",
        )
    )
    assert opened == ["https://auth.example/complete?code=1"]


def test_browser_open_failure_silent(monkeypatch):
    """无浏览器环境（webbrowser.open 抛错）静默——URL 仍在等待框通知里。"""
    import webbrowser

    def _boom(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(webbrowser, "open", _boom)
    ui = _FakeUI()
    interaction = UIAuthInteraction(ui)
    interaction.notify(AuthEvent(type="auth_url", url="https://auth.example/x"))
    # 通知照常发出（URL 在结构化字段里）
    assert any(m.get("url") == "https://auth.example/x" for m in ui.messages)
