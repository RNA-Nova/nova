"""ExecutorClientManager 的 SSH 路由测试（get_client ssh:// 分流 + 隧道复用）。

provision 与 ExecutorClient 均替换为假实现——不触真实 ssh/WS。
"""

import asyncio

import pytest

from nova_coding_agent.executor import manager as manager_module
from nova_coding_agent.executor import provision
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


class _FakeHandle:
    def __init__(self, target):
        self.target = target
        self.url = "ws://127.0.0.1:31000"
        self.token = "t" * 32
        self._alive = True
        self.stopped = False

    def alive(self):
        return self._alive

    def stop(self):
        self.stopped = True
        self._alive = False


@pytest.fixture
def patched(monkeypatch):
    """替换 manager 命名空间内的 provision 与 ExecutorClient。"""
    state = {"provision_calls": [], "clients": []}

    async def _fake_provision(target, on_progress=None, bootstrap=None):
        state["provision_calls"].append(
            {"target": target, "on_progress": on_progress, "bootstrap": bootstrap}
        )
        return _FakeHandle(target)

    def _fake_client(url, token=None):
        client = _FakeClient(url, token)
        state["clients"].append(client)
        return client

    monkeypatch.setattr(manager_module, "provision", _fake_provision)
    monkeypatch.setattr(manager_module, "ExecutorClient", _fake_client)
    return state


class TestSshRouting:
    def test_ssh_url_provisions_and_caches(self, patched):
        async def run():
            mgr = ExecutorClientManager()
            client = await mgr.get_client("ssh://alice@gpu-01")
            assert client.url == "ws://127.0.0.1:31000"
            assert client.token == "t" * 32
            assert client.connected
            # 第二次复用（隧道活着 → 不重供给）
            again = await mgr.get_client("ssh://alice@gpu-01")
            assert again is client
            assert len(patched["provision_calls"]) == 1

        asyncio.run(run())

    def test_dead_tunnel_reprovisions(self, patched):
        async def run():
            mgr = ExecutorClientManager()
            first = await mgr.get_client("ssh://alice@gpu-01")
            # 隧道死亡（断网/休眠）→ 下次获取自动重供给
            mgr._ssh_handles["ssh://alice@gpu-01"]._alive = False
            second = await mgr.get_client("ssh://alice@gpu-01")
            assert second is not first
            assert len(patched["provision_calls"]) == 2

        asyncio.run(run())

    def test_lazy_path_has_no_bootstrap(self, patched):
        """执行期懒路径：BatchMode-only（首连引导归 /executor 命令）。"""

        async def run():
            mgr = ExecutorClientManager()
            await mgr.get_client("ssh://alice@gpu-01")

        asyncio.run(run())
        assert patched["provision_calls"][0]["bootstrap"] is None

    def test_provision_ssh_passes_progress_and_bootstrap(self, patched):
        async def run():
            mgr = ExecutorClientManager()
            progress = []
            boot = object()
            client = await mgr.provision_ssh(
                provision.parse_ssh_target("alice@gpu-01"),
                on_progress=progress.append,
                bootstrap=boot,
            )
            assert client.connected
            call = patched["provision_calls"][0]
            assert call["bootstrap"] is boot
            assert call["on_progress"] is not None
            # 幂等：重复供给复用活隧道
            await mgr.provision_ssh(provision.parse_ssh_target("alice@gpu-01"))
            assert len(patched["provision_calls"]) == 1

        asyncio.run(run())

    def test_ws_url_untouched(self, patched):
        async def run():
            mgr = ExecutorClientManager()
            client = await mgr.get_client("wss://gpu-01:8080")
            assert client.url == "wss://gpu-01:8080"
            assert patched["provision_calls"] == []

        asyncio.run(run())

    def test_close_all_stops_ssh_handles(self, patched):
        async def run():
            mgr = ExecutorClientManager()
            await mgr.get_client("ssh://alice@gpu-01")
            handle = mgr._ssh_handles["ssh://alice@gpu-01"]
            await mgr.close_all()
            assert handle.stopped
            assert mgr._clients == {}
            assert mgr._ssh_handles == {}

        asyncio.run(run())
