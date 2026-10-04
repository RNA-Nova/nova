"""客户端基础测试"""

import asyncio

import pytest
from fake_transport import FakeTransport

from nova_exec_server_client import (
    ExecutorClient,
    ReconnectStrategy,
    TimeoutError,
)
from nova_exec_server_client import client as client_module

ENVIRONMENT_INFO_PAYLOAD = {
    "shell": {"name": "zsh", "path": "/bin/zsh"},
    "cwd": "file:///Users/test",
    "userHomeDir": "file:///Users/test",
    "platformOs": "macos",
    "tempDir": "file:///tmp",
}

FAST = ReconnectStrategy(interval=0.01, timeout=2.0)


@pytest.mark.asyncio
async def test_client_creation():
    """测试客户端创建"""
    client = ExecutorClient("ws://localhost:8080", token="test")
    assert client.transport.url == "ws://localhost:8080"
    assert client.transport.token == "test"


@pytest.mark.asyncio
async def test_client_no_token():
    """测试无 token 客户端"""
    client = ExecutorClient("ws://localhost:8080")
    assert client.transport.token is None


@pytest.mark.asyncio
async def test_managers_exist():
    """测试管理器存在"""
    client = ExecutorClient("ws://localhost:8080")
    assert client.process is not None
    assert client.fs is not None
    assert client.pty is not None


@pytest.mark.asyncio
async def test_environment_info_piggybacked_by_initialize_is_cached():
    """initialize 捎带 environmentInfo：environment_info() 零额外请求"""
    transport = FakeTransport(
        {
            "initialize": {
                "sessionId": "fake-session",
                "protocolVersion": "1.0",
                "environmentInfo": ENVIRONMENT_INFO_PAYLOAD,
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    info = await client.environment_info()
    again = await client.environment_info()

    assert info.user_home_dir == "file:///Users/test"
    assert info.platform_os == "macos"
    assert again is info
    methods = [m for m, _, _ in transport.requests]
    assert "environment/info" not in methods
    await client.disconnect()


@pytest.mark.asyncio
async def test_environment_info_legacy_server_falls_back_once_and_caches():
    """旧服务端未捎带：首个调用回退一次 environment/info，此后读缓存"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.0"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    info = await client.environment_info()
    again = await client.environment_info()

    assert info.user_home_dir == "file:///Users/test"
    assert again is info
    methods = [m for m, _, _ in transport.requests]
    assert methods.count("environment/info") == 1
    await client.disconnect()


@pytest.mark.asyncio
async def test_read_environment_config():
    """read_environment_config 发 environmentConfig/read 并解析层栈响应"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "environmentConfig/read": {
                "userHomeDir": "file:///home/u",
                "executorHomeDir": "file:///home/u/.nova/exec-server",
                "hostname": "devbox",
                "config": {
                    "layers": [
                        {
                            "source": "user:/home/u/.nova/exec-server/config.toml",
                            "baseDir": "file:///home/u/.nova/exec-server",
                            "format": "toml",
                            "content": '[sandbox]\nlevel = "workspace-write"\n',
                        },
                        {
                            "source": "project:/repo/.nova/settings.json",
                            "baseDir": "file:///repo/.nova",
                            "format": "json",
                            "content": "{}",
                        },
                    ],
                    "cloudInsertionIndex": 2,
                },
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    response = await client.read_environment_config(
        "file:///repo", [["sandbox"], ["network", "mode"]]
    )

    requests = [
        (m, p) for m, p, _ in transport.requests if m == "environmentConfig/read"
    ]
    assert len(requests) == 1
    assert requests[0][1] == {
        "cwd": "file:///repo",
        "configPaths": [["sandbox"], ["network", "mode"]],
    }
    assert response.executor_home_dir == "file:///home/u/.nova/exec-server"
    assert [layer.format for layer in response.config.layers] == ["toml", "json"]
    assert response.config.layers[0].error is None
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_connects_fresh_session_and_retires_old():
    """refresh（对位 Rust refresh_connection）：全新会话不 resume + 退役旧传输"""
    made: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": {
                    "sessionId": f"session-{len(made)}",
                    "protocolVersion": "1.0",
                },
                "environment/status": {"status": "ready"},
            }
        )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory)
    await client.connect()
    assert client.session_id == "session-0"

    await client.refresh_connection()

    assert client.session_id == "session-1"
    init_params = [p for m, p, _ in made[1].requests if m == "initialize"][0]
    assert init_params.get("resumeSessionId") is None
    assert not made[0].is_connected
    assert made[1].is_connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_failure_leaves_disconnected_and_later_connect_retries():
    """refresh 失败不恢复旧会话：状态落 disconnected，后续 connect 全新重试"""

    class FailingTransport(FakeTransport):
        async def connect(self) -> None:
            raise ConnectionError("dial refused")

    made: list[FakeTransport] = []

    def factory() -> FakeTransport:
        if len(made) == 1:
            transport: FakeTransport = FailingTransport({})
        else:
            transport = FakeTransport(
                {
                    "initialize": {
                        "sessionId": f"session-{len(made)}",
                        "protocolVersion": "1.0",
                    },
                    "environment/status": {"status": "ready"},
                }
            )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory)
    await client.connect()
    assert client.session_id == "session-0"

    import pytest as _pytest

    with _pytest.raises(Exception):
        await client.refresh_connection()
    assert client._control.state == "disconnected"

    await client.connect()
    assert client.session_id == "session-2"
    await client.disconnect()


@pytest.mark.asyncio
async def test_force_environment_info_bypasses_cache():
    """force_environment_info（对位 rust 同名）：不读缓存、不写缓存——
    environment_info 依旧缓存，force 每次都发请求"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.0"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    await client.environment_info()
    await client.environment_info()
    cached_calls = [m for m, _, _ in transport.requests if m == "environment/info"]
    assert len(cached_calls) == 1

    await client.force_environment_info()
    await client.force_environment_info()
    forced_calls = [m for m, _, _ in transport.requests if m == "environment/info"]
    assert len(forced_calls) == 3  # 1 次缓存填充 + 2 次强制
    # 缓存未被强刷污染
    await client.environment_info()
    assert len([m for m, _, _ in transport.requests if m == "environment/info"]) == 3
    await client.disconnect()


class HangingEnvironmentInfoTransport(FakeTransport):
    """environment/info 永不应答的假传输（探活超时触发恢复测试用）"""

    async def send_request(self, method, params=None, *, channel=None):
        if method == "environment/info":
            await asyncio.sleep(60)
        return await super().send_request(method, params, channel=channel)


def make_probe_factory(sink: list[FakeTransport]):
    """首个传输 environment/info 不应答，重连件正常应答的传输工场"""

    def factory() -> FakeTransport:
        responses = {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
        transport = (
            HangingEnvironmentInfoTransport(responses)
            if not sink
            else FakeTransport(responses)
        )
        sink.append(transport)
        return transport

    return factory


@pytest.mark.asyncio
async def test_force_environment_info_timeout_retires_connection_and_recovers(
    monkeypatch,
):
    """对位上游 67352b5b4a：environment/info 超时 → 关闭所探连接并触发会话
    恢复，调用方拿超时错误（不重试失败请求）；恢复回到同一会话、客户端实例不变"""
    monkeypatch.setattr(client_module, "ENVIRONMENT_INFO_TIMEOUT", 0.05)
    sink: list[FakeTransport] = []
    client = ExecutorClient(transport_factory=make_probe_factory(sink), reconnect=FAST)
    await client.connect()

    with pytest.raises(TimeoutError, match="timed out"):
        await client.force_environment_info()

    # 探活超时的连接被客户端主动退役；按策略重连并 resume 同一会话
    for _ in range(100):
        if len(sink) == 2 and not sink[0].connected:
            break
        await asyncio.sleep(0.01)
    assert not sink[0].connected
    assert len(sink) == 2
    assert sink[1].requests[0] == (
        "initialize",
        {"clientName": "nova-exec-server-client", "resumeSessionId": "fake-session"},
        None,
    )
    assert client.session_id == "fake-session"

    # 恢复后探活走新连接照常
    info = await client.force_environment_info()
    assert info.platform_os == "macos"
    await client.disconnect()


@pytest.mark.asyncio
async def test_environment_info_cache_miss_timeout_also_recovers(monkeypatch):
    """缓存未命中的 environment_info() 走同一探活路径（对位 Rust
    get_or_try_init(force_environment_info)）：超时同样退役连接并恢复，
    恢复后缓存正常填充"""
    monkeypatch.setattr(client_module, "ENVIRONMENT_INFO_TIMEOUT", 0.05)
    sink: list[FakeTransport] = []
    client = ExecutorClient(transport_factory=make_probe_factory(sink), reconnect=FAST)
    await client.connect()

    with pytest.raises(TimeoutError, match="timed out"):
        await client.environment_info()
    assert client._environment_info is None  # 超时不写缓存

    for _ in range(100):
        if len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    info = await client.environment_info()
    assert info.platform_os == "macos"
    again = await client.environment_info()
    assert again is info  # 恢复后读缓存，不重复请求
    await client.disconnect()
