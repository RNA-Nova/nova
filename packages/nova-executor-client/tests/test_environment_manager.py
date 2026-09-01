"""EnvironmentManager 测试（多环境连接管理——对位 codex EnvironmentManager）

连接行为用 fake executor server（stdio NDJSON，tests/fake_executor_server.py）
驱动真握手。
"""

import sys
from pathlib import Path

import pytest

from nova_executor_client import (
    ConfigError,
    ExecutorConfig,
    ExecutorEnvironment,
)
from nova_executor_client.environments import (
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
        status = manager.status("a")
        assert status.state is EnvironmentConnectionState.PENDING
        assert status.error is None


class TestConnectionLifecycle:
    async def test_get_client_connects_and_caches(self):
        manager = EnvironmentManager(ExecutorConfig(environments=[_fake_env("fake")]))
        client = await manager.get_client("fake")
        try:
            assert client.session_id == "fake-session"
            assert manager.status("fake").state is EnvironmentConnectionState.CONNECTED
            # 缓存：同一环境返回同一实例
            assert await manager.get_client("fake") is client
        finally:
            await manager.close_all()
        # close_all 清空缓存——状态回到 pending（从未连接）
        assert manager.status("fake").state is EnvironmentConnectionState.PENDING

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
        assert manager.status("dead").state is EnvironmentConnectionState.PENDING


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
        assert manager.status("fake").state is EnvironmentConnectionState.PENDING
        assert await manager.remove_environment("fake") is False

    async def test_close_all_idempotent(self):
        manager = EnvironmentManager(ExecutorConfig())
        await manager.close_all()
        await manager.close_all()
