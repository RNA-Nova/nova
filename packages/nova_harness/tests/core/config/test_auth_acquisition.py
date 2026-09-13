"""授权码获取装配器（acquisition）测试——竞速/取消/降级全链。

本地监听通道用真实端口起服务器验证；宿主监听通道用预置应答的假 UI。
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from nova_protocol.auth import AuthorizationRequest, AuthPrompt, LoginCancelledError
from nova_protocol.signal import AbortController

from nova_harness.config.auth.acquisition import acquire_authorization_code
from nova_harness.types.ui.context import UIContext, UIResponse


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeUI(UIContext):
    """可宣告 host:listenOnce 能力的假 UI（预置应答）。"""

    def __init__(self, listen_response=None, listen_caps=True):
        self._listen_response = listen_response
        self._listen_caps = listen_caps
        self.listen_calls = []

    @property
    def capabilities(self):
        caps = {"select", "input", "confirm", "notify"}
        if self._listen_caps:
            caps.add("host:listenOnce")
        return caps

    async def request(self, method, params, scope=None):
        if method == "host:listenOnce":
            self.listen_calls.append(params)
            # 模拟宿主收货：直接以预置应答落定
            resp = self._listen_response or {"status": "timeout"}
            return UIResponse(value=resp)
        return UIResponse(value=None)

    def notify(self, method, params):
        pass


def _request(
    port: int, timeout: float = 30.0, manual: bool = True
) -> AuthorizationRequest:
    return AuthorizationRequest(
        auth_url="https://example.com/auth",
        state="s0",
        redirect_port=port,
        timeout_seconds=timeout,
        manual_prompt=(
            AuthPrompt(type="manual_code", message="paste here") if manual else None
        ),
    )


async def _http_get(port: int, path: str) -> int:
    """真实发一个 GET 到本地服务器（urllib 同步转线程）。"""

    import urllib.request

    def _do() -> int:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=5
            ) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    return await asyncio.to_thread(_do)


class TestLocalCallbackChannel:
    """本地一次性监听通道（无 listenOnce 能力时的进程内收货）。"""

    @pytest.mark.asyncio
    async def test_callback_wins(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _never_prompt(_p):
            await asyncio.sleep(60)
            return ""

        task = asyncio.ensure_future(
            acquire_authorization_code(ui, None, _request(port), _never_prompt)
        )
        await asyncio.sleep(0.1)  # 等服务器起来
        status = await _http_get(port, "/auth/callback?code=abc&state=s0")
        assert status == 200
        assert await asyncio.wait_for(task, 5) == "abc"

    @pytest.mark.asyncio
    async def test_state_mismatch_does_not_settle(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _never_prompt(_p):
            await asyncio.sleep(60)
            return ""

        task = asyncio.ensure_future(
            acquire_authorization_code(ui, None, _request(port), _never_prompt)
        )
        await asyncio.sleep(0.1)
        # state 不符 → 400，竞速继续（后到的合法回调仍可收货）
        assert await _http_get(port, "/auth/callback?code=x&state=wrong") == 400
        assert not task.done()
        assert await _http_get(port, "/auth/callback?code=ok&state=s0") == 200
        assert await asyncio.wait_for(task, 5) == "ok"

    @pytest.mark.asyncio
    async def test_port_occupied_degrades_to_manual(self):
        # 先占住端口
        blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = blocker.sockets[0].getsockname()[1]
        ui = _FakeUI(listen_caps=False)

        async def _paste(_p):
            await asyncio.sleep(0.05)
            return "pasted-code#s0"

        code = await acquire_authorization_code(ui, None, _request(port), _paste)
        assert code == "pasted-code"
        blocker.close()


class TestManualChannel:
    @pytest.mark.asyncio
    async def test_manual_paste_wins_and_parses_url(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _paste(_p):
            await asyncio.sleep(0.05)
            return f"http://127.0.0.1:{port}/auth/callback?code=pasted&state=s0"

        code = await acquire_authorization_code(ui, None, _request(port), _paste)
        assert code == "pasted"

    @pytest.mark.asyncio
    async def test_manual_state_mismatch_raises(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _paste(_p):
            return "code-x#wrong-state"

        with pytest.raises(RuntimeError, match="State mismatch"):
            await acquire_authorization_code(ui, None, _request(port), _paste)


class TestHostChannel:
    @pytest.mark.asyncio
    async def test_host_listen_once_received(self):
        port = _free_port()
        ui = _FakeUI(
            listen_response={"status": "received", "code": "host-code", "state": "s0"}
        )

        async def _never_prompt(_p):
            return ""

        code = await acquire_authorization_code(ui, None, _request(port), _never_prompt)
        assert code == "host-code"
        assert len(ui.listen_calls) == 1
        assert ui.listen_calls[0]["port"] == port

    @pytest.mark.asyncio
    async def test_host_state_mismatch_not_accepted(self):
        port = _free_port()
        ui = _FakeUI(
            listen_response={"status": "received", "code": "x", "state": "wrong"}
        )

        async def _never_prompt(_p):
            return ""

        with pytest.raises(TimeoutError):
            await acquire_authorization_code(
                ui, None, _request(port, timeout=0.3, manual=False), _never_prompt
            )


class TestCancellation:
    @pytest.mark.asyncio
    async def test_host_task_cancel_translates(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _never_prompt(_p):
            await asyncio.sleep(60)
            return ""

        task = asyncio.ensure_future(
            acquire_authorization_code(ui, None, _request(port), _never_prompt)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(LoginCancelledError):
            await task
        # 清扫生效：端口可立即重用
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", port)
        probe.close()

    @pytest.mark.asyncio
    async def test_signal_abort(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)
        controller = AbortController("test")

        async def _never_prompt(_p):
            await asyncio.sleep(60)
            return ""

        async def _abort_soon():
            await asyncio.sleep(0.1)
            controller.abort()

        asyncio.ensure_future(_abort_soon())
        with pytest.raises(LoginCancelledError):
            await acquire_authorization_code(
                ui, controller.signal, _request(port), _never_prompt
            )

    @pytest.mark.asyncio
    async def test_timeout(self):
        port = _free_port()
        ui = _FakeUI(listen_caps=False)

        async def _never_prompt(_p):
            await asyncio.sleep(60)
            return ""

        with pytest.raises(TimeoutError):
            await acquire_authorization_code(
                ui, None, _request(port, timeout=0.2, manual=False), _never_prompt
            )


class TestInteractionSeam:
    """端到端接缝：UIAuthInteraction.acquire_authorization_code 全链。"""

    @pytest.mark.asyncio
    async def test_acquire_through_interaction(self):
        from nova_harness.config.auth.interaction import UIAuthInteraction

        port = _free_port()
        ui = _FakeUI(listen_caps=False)
        interaction = UIAuthInteraction(ui)

        task = asyncio.ensure_future(
            interaction.acquire_authorization_code(_request(port, manual=False))
        )
        await asyncio.sleep(0.1)
        assert await _http_get(port, "/auth/callback?code=seam&state=s0") == 200
        assert await asyncio.wait_for(task, 5) == "seam"
