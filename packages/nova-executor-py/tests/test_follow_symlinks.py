"""fs 五端点 followSymlinks 参数测试（None=不下发，服务端默认 true；False 显式下发）"""

from __future__ import annotations

import pytest
from fake_transport import FakeTransport

from nova_executor.fs import FileSystemManager


def make_fs() -> tuple[FileSystemManager, FakeTransport]:
    transport = FakeTransport(
        {
            "fs/readFile": {"dataBase64": ""},
            "fs/getMetadata": {
                "isDirectory": False,
                "isFile": True,
                "isSymlink": False,
                "size": 1,
                "createdAtMs": 0,
                "modifiedAtMs": 0,
            },
        }
    )
    return FileSystemManager(transport), transport


@pytest.mark.asyncio
async def test_follow_symlinks_omitted_when_none():
    """缺省 None：五端点均不下发 followSymlinks（旧行为不变）"""
    fs, transport = make_fs()

    await fs.read_file("file:///tmp/a")
    await fs.write_file("file:///tmp/a", b"x")
    await fs.create_dir("file:///tmp/a")
    await fs.metadata("file:///tmp/a")
    await fs.remove("file:///tmp/a")

    assert [method for method, _, _ in transport.requests] == [
        "fs/readFile",
        "fs/writeFile",
        "fs/createDirectory",
        "fs/getMetadata",
        "fs/remove",
    ]
    for _, params, _ in transport.requests:
        assert "followSymlinks" not in params


@pytest.mark.asyncio
async def test_follow_symlinks_false_is_sent():
    """显式 False：no-follow 语义下发 followSymlinks=false"""
    fs, transport = make_fs()

    await fs.read_file("file:///tmp/a", follow_symlinks=False)
    await fs.write_file("file:///tmp/a", b"x", follow_symlinks=False)
    await fs.create_dir("file:///tmp/a", follow_symlinks=False)
    await fs.metadata("file:///tmp/a", follow_symlinks=False)
    await fs.remove("file:///tmp/a", recursive=False, follow_symlinks=False)

    for _, params, _ in transport.requests:
        assert params["followSymlinks"] is False


@pytest.mark.asyncio
async def test_follow_symlinks_true_is_sent():
    """显式 True：照原样下发（与服务端默认一致）"""
    fs, transport = make_fs()

    await fs.metadata("file:///tmp/a", follow_symlinks=True)

    _, params, _ = transport.requests[0]
    assert params["followSymlinks"] is True
