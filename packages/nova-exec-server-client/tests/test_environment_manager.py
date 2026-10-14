"""EnvironmentManager 测试（多环境连接管理——对位 codex EnvironmentManager）

连接行为用 fake executor server（stdio NDJSON，tests/fake_executor_server.py）
驱动真握手；探活（status）用 FakeTransport 装配的内存客户端钉线上请求。
"""

import asyncio
import sys
from pathlib import Path

import pytest
from fake_transport import FakeTransport

from nova_exec_server_client import (
    ConfigError,
    ExecutorClient,
    ExecutorConfig,
    ExecutorEnvironment,
)
from nova_exec_server_client.environments import (
    LOCAL_ENVIRONMENT_ID,
    EnvironmentConnectionState,
    EnvironmentManager,
)

pytestmark = pytest.mark.asyncio

FAKE_SERVER = str(Path(__file__).parent / "fake_executor_server.py")


def _fake_env(id_: str) -> ExecutorEnvironment:
    """fake executor server 环境（stdio spawn 形态）"""
    return ExecutorEnvironment(id=id_, program=sys.executable, args=[FAKE_SERVER])


class TestRegistryView:
    async def test_environment_ids_include_local(self):
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("a")]))
        assert manager.environment_ids() == ["a", LOCAL_ENVIRONMENT_ID]

    async def test_environment_ids_without_local(self):
        cfg = ExecutorConfig(include_local=False, environments=[])
        manager = EnvironmentManager(cfg)
        assert manager.environment_ids() == []

    async def test_default_environment_id(self):
        assert EnvironmentManager(ExecutorConfig()).default_environment_id == "local"
        disabled = ExecutorConfig(default_environment="none")
        assert EnvironmentManager(disabled).default_environment_id is None


class TestStatus:
    async def test_pending_before_any_connection(self):
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("a")]))
        status = await manager.status("a")
        assert status.state is EnvironmentConnectionState.PENDING
        assert status.error is None

    async def test_status_probes_live_client(self):
        """已缓存连接：status 实发一次 environment/status 探活（对位 Rust
        Environment::status——可调 client 逐次实况探测，不给缓存的健康结果）"""
        transport = FakeTransport(
            {
                "initialize": {"sessionId": "s", "protocolVersion": "1.13"},
                "environment/status": {"status": "ready"},
            }
        )
        client = ExecutorClient(transport=transport)
        await client.connect()
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("x")]))
        manager._clients["x"] = client
        try:
            status = await manager.status("x")
            assert status.state is EnvironmentConnectionState.CONNECTED
            probes = [m for m, _, _ in transport.requests if m == "environment/status"]
            assert probes == ["environment/status"]
        finally:
            await client.disconnect()

    async def test_status_probe_failure_reports_disconnected(self):
        """探活失败即报 DISCONNECTED 并带原因——不按本地状态机缓存报 CONNECTED"""
        transport = FakeTransport(
            {
                "initialize": {"sessionId": "s", "protocolVersion": "1.13"},
                "environment/status": ConnectionError("probe boom"),
            }
        )
        client = ExecutorClient(transport=transport)
        await client.connect()
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("x")]))
        manager._clients["x"] = client
        try:
            status = await manager.status("x")
            assert status.state is EnvironmentConnectionState.DISCONNECTED
            assert "probe boom" in (status.error or "")
        finally:
            await client.disconnect()


class TestConnectionLifecycle:
    async def test_get_client_connects_and_caches(self):
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("fake")]))
        client = await manager.get_client("fake")
        try:
            assert client.session_id == "fake-session"
            status = await manager.status("fake")
            assert status.state is EnvironmentConnectionState.CONNECTED
            # 缓存：同一环境返回同一实例
            assert await manager.get_client("fake") is client
        finally:
            await manager.close_all()
        # close_all 清空缓存——状态回到 pending（从未连接）
        status = await manager.status("fake")
        assert status.state is EnvironmentConnectionState.PENDING

    async def test_default_resolution_drives_get_client(self):
        cfg = ExecutorConfig(
            default_environment="fake", environments=[_fake_env("fake")]
        )
        manager = EnvironmentManager(cfg)
        client = await manager.get_client()  # 不传名 → 默认环境
        try:
            assert client.session_id == "fake-session"
        finally:
            await manager.close_all()

    async def test_unknown_environment_raises(self):
        manager = EnvironmentManager(ExecutorConfig())
        with pytest.raises(ConfigError, match="未知环境"):
            await manager.get_client("ghost")

    async def test_failed_connection_not_cached(self):
        manager = EnvironmentManager(
            ExecutorConfig(
                environments=[
                    ExecutorEnvironment(
                        id="dead", program="definitely-not-a-real-binary-xyz"
                    )
                ]
            )
        )
        with pytest.raises(Exception):
            await manager.get_client("dead")
        status = await manager.status("dead")
        assert status.state is EnvironmentConnectionState.PENDING


class TestUpsertRemove:
    async def test_upsert_replaces_registry_entry(self):
        manager = EnvironmentManager(ExecutorConfig())
        await manager.upsert_environment(_fake_env("a"))
        assert "a" in manager.environment_ids()
        await manager.upsert_environment(
            ExecutorEnvironment(id="a", url="ws://example.internal:2")
        )
        entries = [e for e in manager.environment_ids() if e == "a"]
        assert entries == ["a"]

    async def test_remove_disconnects_and_drops(self):
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("fake")]))
        await manager.get_client("fake")
        assert await manager.remove_environment("fake") is True
        assert "fake" not in manager.environment_ids()
        status = await manager.status("fake")
        assert status.state is EnvironmentConnectionState.PENDING
        assert await manager.remove_environment("fake") is False

    async def test_remove_waits_for_inflight_lazy_connect(self, monkeypatch):
        """增删与懒创建同锁互斥（竞态窗口修复）：get_client 持锁连接期间
        remove 阻塞等待；连接落地后由 remove 收走，不残留已删环境的缓存连接"""
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("fake")]))
        orig_connect = ExecutorClient.connect
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_connect(self):
            started.set()
            await release.wait()
            await orig_connect(self)

        monkeypatch.setattr(ExecutorClient, "connect", slow_connect)
        getter = asyncio.create_task(manager.get_client("fake"))
        await started.wait()
        remover = asyncio.create_task(manager.remove_environment("fake"))
        # remove 须阻塞在锁上（get_client 持锁连接中），不得抢先空删返回
        await asyncio.sleep(0.05)
        assert not remover.done()
        release.set()
        client = await getter
        assert await remover is True
        assert manager._clients == {}
        assert "fake" not in manager.environment_ids()
        assert not client._control.is_connected

    async def test_close_all_idempotent(self):
        manager = EnvironmentManager(ExecutorConfig())
        await manager.close_all()
        await manager.close_all()
