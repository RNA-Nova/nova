"""fs.open(mode=replace) / fs.write_block 单元测试（对位 codex e7798c9944
`writable_file_streams_require_executor_capability`）：

- fileWriteStreaming=false（或 capabilities 缺失的旧服务端）时，replace 打开与
  写块在本地拒绝（ProtocolError，文案与上游一致），不发线上请求；
- 只读打开不受门控，照常到达 executor；
- 能力位 true 时 replace 打开与写块正常上线（chunk 为裸 base64 字符串）。
"""

from __future__ import annotations

import pytest
from fake_transport import FakeTransport
from nova_protocol import EnvironmentInfo, FsOpenMode

from nova_exec_server_client import ProtocolError
from nova_exec_server_client.fs import FileSystemManager


def env_info(*, file_write_streaming: bool) -> dict:
    return {
        "shell": {"name": "sh", "path": "/bin/sh"},
        "capabilities": {"fileWriteStreaming": file_write_streaming},
    }


def make_fs(responses: dict) -> tuple[FileSystemManager, FakeTransport]:
    transport = FakeTransport(responses)
    return FileSystemManager(transport), transport


@pytest.mark.asyncio
async def test_writable_open_and_write_block_rejected_locally_without_capability():
    """旧 executor（无 fileWriteStreaming 位）：写侧两个方法本地报错、不上线"""
    fs, transport = make_fs({"environment/info": env_info(file_write_streaming=False)})

    with pytest.raises(
        ProtocolError, match="exec-server does not support writable file streams"
    ):
        await fs.open("file:///tmp/out.bin", mode=FsOpenMode.REPLACE)
    with pytest.raises(
        ProtocolError, match="exec-server does not support writable file streams"
    ):
        await fs.write_block("w-1", 0, b"x")

    # 只发过 environment/info 探测；fs/open 与 fs/writeBlock 均未上线
    methods = [m for m, _, _ in transport.requests]
    assert methods == ["environment/info"]


@pytest.mark.asyncio
async def test_writable_open_rejected_when_capabilities_missing_entirely():
    """更旧的 executor（environment/info 无 capabilities 字段）：同样本地拒绝"""
    fs, transport = make_fs(
        {"environment/info": {"shell": {"name": "sh", "path": "/bin/sh"}}}
    )

    with pytest.raises(
        ProtocolError, match="exec-server does not support writable file streams"
    ):
        await fs.write_block("w-1", 0, b"x")
    assert [m for m, _, _ in transport.requests] == ["environment/info"]


@pytest.mark.asyncio
async def test_read_only_open_reaches_executor_without_capability():
    """只读打开不受能力门约束（对位上游：read-only opens still reach the executor）"""
    fs, transport = make_fs(
        {
            "environment/info": env_info(file_write_streaming=False),
            "fs/open": {"handleId": "r-1"},
        }
    )

    handle_id = await fs.open("file:///tmp/existing.txt", handle_id="r-1")

    assert handle_id == "r-1"
    open_params = next(p for m, p, _ in transport.requests if m == "fs/open")
    assert open_params["mode"] == "read"
    # 只读路径不触发能力位探测
    assert [m for m, _, _ in transport.requests] == ["fs/open"]


@pytest.mark.asyncio
async def test_replace_open_and_write_block_go_online_with_capability():
    """能力位 true：replace 打开带 mode=replace 上线，写块 chunk 为裸 base64"""
    fs, transport = make_fs(
        {
            "environment/info": env_info(file_write_streaming=True),
            "fs/open": {"handleId": "w-1"},
            "fs/writeBlock": {},
        }
    )

    handle_id = await fs.open(
        "file:///tmp/out.bin",
        handle_id="w-1",
        mode=FsOpenMode.REPLACE,
    )
    await fs.write_block(handle_id, 42, b"abc")

    open_params = next(p for m, p, _ in transport.requests if m == "fs/open")
    assert open_params["mode"] == "replace"
    write_params = next(p for m, p, _ in transport.requests if m == "fs/writeBlock")
    assert write_params == {"handleId": "w-1", "offset": 42, "chunk": "YWJj"}


@pytest.mark.asyncio
async def test_capability_gate_uses_injected_environment_info_provider():
    """client 装配路径：能力查询走注入的 environment_info 提供方（initialize
    捎带缓存），不再发 environment/info 请求"""
    transport = FakeTransport({"fs/open": {"handleId": "w-1"}})
    info = EnvironmentInfo.model_validate(env_info(file_write_streaming=False))
    calls = 0

    async def provider() -> EnvironmentInfo:
        nonlocal calls
        calls += 1
        return info

    fs = FileSystemManager(transport, environment_info=provider)
    with pytest.raises(
        ProtocolError, match="exec-server does not support writable file streams"
    ):
        await fs.open("file:///tmp/out.bin", mode=FsOpenMode.REPLACE)

    assert calls == 1
    assert transport.requests == []
