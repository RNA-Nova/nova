"""ExecutorClientManager 的本地 spawn 与 ws(s) 路由测试。"""

import asyncio

import pytest

from nova_coding_agent.executor import manager as manager_module
from nova_coding_agent.executor.manager import ExecutorClientManager


class _FakeClient:
    def __init__(self, url, token=None):
        self.url = url
        self.token = token
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_clients(monkeypatch):
    clients = []

    class _FakeClientShim:
        """SDK 经 ExecutorClient.from_environment 构造——假实现挂类方法。"""

        @classmethod
        def from_environment(cls, environment, **kwargs):
            client = _FakeClient(environment.url, token=environment.token)
            clients.append(client)
            return client

    monkeypatch.setattr(
        "nova_exec_server_client.client.ExecutorClient", _FakeClientShim
    )
    return clients


def test_ws_url_uses_direct_environment(fake_clients):
    async def run():
        mgr = ExecutorClientManager()
        client = await mgr.get_client("wss://gpu-01:8080")
        assert client.url == "wss://gpu-01:8080"
        assert client.connected

    asyncio.run(run())


def test_ssh_product_scheme_is_rejected(fake_clients):
    async def run():
        mgr = ExecutorClientManager()
        await mgr.get_client("ssh://alice@gpu-01")

    with pytest.raises(ValueError, match="仅支持 ws"):
        asyncio.run(run())
    assert fake_clients == []


def test_close_all_disconnects_sdk_clients(fake_clients):
    async def run():
        mgr = ExecutorClientManager()
        await mgr.get_client("wss://gpu-01:8080")
        await mgr.close_all()
        assert mgr._env_manager._clients == {}
        assert all(client.disconnected for client in fake_clients)

    asyncio.run(run())


class TestSpawnLocal:
    def test_spawn_local_uses_ws_auth_capability_token_flags(self, monkeypatch):
        """spawn 旗标对位 exec-server-cli 的 --ws-auth 族（capability-token +
        --ws-token-sha256）：服务端只存 token 的 SHA-256，原 token 只发客户端
        （Authorization: Bearer）。旧 `--auth bearer/--auth-token` 拼写在 CLI
        不存在（clap 必拒）——曾使本地 executor 后端 spawn 即死且无测试覆盖。
        """
        import hashlib

        monkeypatch.setattr(
            manager_module, "resolve_executor_binary", lambda: "/fake/nova-exec-server"
        )
        captured: list[list[str]] = []
        captured_kwargs: list[dict] = []

        class _FakeProc:
            def __init__(self, args, kwargs):
                captured.append(args)
                captured_kwargs.append(kwargs)
                self.returncode = None

        monkeypatch.setattr(
            manager_module.subprocess,
            "Popen",
            lambda args, **kw: _FakeProc(args, kw),
        )

        async def run():
            mgr = ExecutorClientManager()

            async def _fake_url(proc):
                return "ws://127.0.0.1:31001"

            monkeypatch.setattr(mgr, "_read_listen_url", _fake_url)
            return await mgr._spawn_local()

        url, token = asyncio.run(run())

        args = captured[0]
        assert args[0] == "/fake/nova-exec-server"
        assert args[1:3] == ["--listen", "ws://127.0.0.1:0"]
        assert args[3:7] == [
            "--ws-auth",
            "capability-token",
            "--ws-token-sha256",
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        ]
        # 原 token 不出现在旗标里（服务端只存摘要；客户端持原文发 Bearer）
        assert token not in args
        assert url == "ws://127.0.0.1:31001"
        # 父死子随：显式注入 leash，stdin 保持为父进程私管（父死时 EOF）
        assert captured_kwargs[0]["env"]["NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE"] == "1"
        assert captured_kwargs[0]["stdin"] == manager_module.subprocess.PIPE
