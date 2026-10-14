"""fs 一元/二元方法族的按请求沙箱参数测试（A17——对位 Rust
`RemoteFileSystem` 全方法透传 `sandbox.cloned()`）：

- 九个方法（readFile/writeFile/readDirectory/walk/createDirectory/remove/
  copy/getMetadata/canonicalize）全部暴露 `sandbox` kwarg，上线键名
  "sandbox"、值为 FileSystemSandboxContext 的线上 dict（None 项不上线）；
- 缺省 None 时与现状一致：exclude_none 出货路径的方法省略该键，其余
  方法带 "sandbox": None（对位 Rust serde Option 无 skip_serializing_if
  的 null 上线）。
"""

import base64

import pytest
from fake_transport import FakeTransport
from nova_protocol import FileSystemSandboxContext

from nova_exec_server_client.fs import FileSystemManager

SANDBOX_PATH = "/tmp/proj"

#: 各方法的假响应（ response 模型必填字段齐备）
RESPONSES = {
    "fs/readFile": {"dataBase64": base64.b64encode(b"hi").decode()},
    "fs/writeFile": {},
    "fs/readDirectory": {"entries": []},
    "fs/walk": {"entries": [], "errors": [], "truncated": False},
    "fs/createDirectory": {},
    "fs/remove": {},
    "fs/copy": {},
    "fs/getMetadata": {
        "isDirectory": False,
        "isFile": True,
        "isSymlink": False,
        "size": 2,
        "createdAtMs": 0,
        "modifiedAtMs": 0,
    },
    "fs/canonicalize": {"path": "file:///tmp/x"},
}

#: 出货路径带 exclude_none 的方法（None 字段不上线）
EXCLUDE_NONE_METHODS = {
    "fs/readFile",
    "fs/writeFile",
    "fs/createDirectory",
    "fs/remove",
    "fs/getMetadata",
}


async def _call(fs: FileSystemManager, method: str, **kwargs) -> None:
    """按方法名分发到对应管理器方法（参数取最小合法集）"""
    match method:
        case "fs/readFile":
            await fs.read_file("file:///tmp/x", **kwargs)
        case "fs/writeFile":
            await fs.write_file("file:///tmp/x", b"hi", **kwargs)
        case "fs/readDirectory":
            await fs.read_dir("file:///tmp", **kwargs)
        case "fs/walk":
            await fs.walk("file:///tmp", **kwargs)
        case "fs/createDirectory":
            await fs.create_dir("file:///tmp/d", **kwargs)
        case "fs/remove":
            await fs.remove("file:///tmp/x", **kwargs)
        case "fs/copy":
            await fs.copy("file:///tmp/a", "file:///tmp/b", **kwargs)
        case "fs/getMetadata":
            await fs.metadata("file:///tmp/x", **kwargs)
        case "fs/canonicalize":
            await fs.canonicalize("file:///tmp/x", **kwargs)
        case _:
            raise AssertionError(f"未覆盖的方法：{method}")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(RESPONSES))
async def test_sandbox_context_goes_online(method: str):
    """sandbox kwarg 上线：键名 "sandbox"，值为线上 dict（exclude_none 形态）"""
    transport = FakeTransport(RESPONSES)
    fs = FileSystemManager(transport)
    ctx = FileSystemSandboxContext.read_only(SANDBOX_PATH)

    await _call(fs, method, sandbox=ctx)

    seen_method, params, _ = transport.requests[0]
    assert seen_method == method
    assert params["sandbox"] == ctx.model_dump(by_alias=True, exclude_none=True)
    # 线上形状抽查：managed 权限档 + None 项（如 sandboxOverride）不上线
    assert params["sandbox"]["permissions"]["type"] == "managed"
    assert "sandboxOverride" not in params["sandbox"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(RESPONSES))
async def test_sandbox_default_none_matches_rust_wire(method: str):
    """缺省 None：exclude_none 出货路径省略该键；其余方法带 "sandbox": None
    （对位 Rust serde Option 无 skip_serializing_if——null 照常上线）"""
    transport = FakeTransport(RESPONSES)
    fs = FileSystemManager(transport)

    await _call(fs, method)

    _, params, _ = transport.requests[0]
    if method in EXCLUDE_NONE_METHODS:
        assert "sandbox" not in params
    else:
        assert params["sandbox"] is None
