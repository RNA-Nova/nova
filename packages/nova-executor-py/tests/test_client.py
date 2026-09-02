"""客户端基础测试"""

import pytest
from nova_executor import ExecutorClient


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
