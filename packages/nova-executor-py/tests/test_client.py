"""客户端基础测试"""

import pytest
from fake_transport import FakeTransport

from nova_executor import ExecutorClient

ENVIRONMENT_INFO_PAYLOAD = {
    "shell": {"name": "zsh", "path": "/bin/zsh"},
    "cwd": "file:///Users/test",
    "userHomeDir": "file:///Users/test",
    "platformOs": "macos",
    "tempDir": "file:///tmp",
}


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
                "executorHomeDir": "file:///home/u/.nova/executor",
                "hostname": "devbox",
                "config": {
                    "layers": [
                        {
                            "source": "user:/home/u/.nova/executor/config.toml",
                            "baseDir": "file:///home/u/.nova/executor",
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
    assert response.executor_home_dir == "file:///home/u/.nova/executor"
    assert [layer.format for layer in response.config.layers] == ["toml", "json"]
    assert response.config.layers[0].error is None
    await client.disconnect()
