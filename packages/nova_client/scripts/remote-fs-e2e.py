"""远程 fs 六工具确定性 e2e（真实服务器 180.184.33.245，无 TUI）。

真实链路：SSH 供给 → ExecutorFileSystemLayer → SDK fs（含新 fs/walk）
→ 隧道 → 远程 executor；六个 operations 实现（同一份代码的远程形态）
逐一实操：write → read → edit → ls → grep → find。

用法：pixi run -e dev python packages/nova-harness/frontend/scripts/remote-fs-e2e.py
"""

import asyncio
import sys

from nova_coding_agent.executor import get_executor_manager, parse_ssh_target
from nova_coding_agent.executor.fs_layer import ExecutorFileSystemLayer
from nova_coding_agent.tools_common.operations import (
    FindOptions,
    GrepOptions,
    LocalEditOperations,
    LocalFindOperations,
    LocalGrepOperations,
    LocalLsOperations,
    LocalReadOperations,
    LocalWriteOperations,
    LsOptions,
)

TARGET = "liujinming@180.184.33.245"
ROOT = "/home/liujinming/.nova/agent/executor/workspaces/fs-e2e"


async def main() -> int:
    manager = get_executor_manager()
    target = parse_ssh_target(TARGET)
    print(f"== provision {TARGET}")
    await manager.provision_ssh(target)
    layer = ExecutorFileSystemLayer(manager, target.canonical_url)

    # 测试隔离：先清可能的遗留目录（幂等）
    client = await manager.get_client(target.canonical_url)
    try:
        await client.fs.remove(f"file://{ROOT}", recursive=True, force=True)
    except Exception:
        pass

    print("== write（远程写文件，父目录自动创建）")
    write_ops = LocalWriteOperations(layer)
    result = await write_ops.write_file(f"{ROOT}/src/hello.py", "def hello():\n    return 'nova'\n")
    assert result.error is None and result.existed is False, result

    print("== read（读回 + 分页）")
    read_ops = LocalReadOperations(layer)
    assert await read_ops.exists(f"{ROOT}/src/hello.py")
    assert await read_ops.is_file(f"{ROOT}/src/hello.py")
    text = (await read_ops.read_text(f"{ROOT}/src/hello.py")).text
    assert "def hello" in text, text
    # is_image_file：非图片 False
    assert not await read_ops.is_image_file(f"{ROOT}/src/hello.py")

    print("== edit（远程精确替换）")
    edit_ops = LocalEditOperations(layer)
    await edit_ops.access(f"{ROOT}/src/hello.py")
    original = await edit_ops.read_text(f"{ROOT}/src/hello.py")
    await edit_ops.write_text(
        f"{ROOT}/src/hello.py", original.replace("hello", "greetings")
    )
    text = (await read_ops.read_text(f"{ROOT}/src/hello.py")).text
    assert "def greetings" in text and "def hello" not in text, text

    print("== ls（远程列目录）")
    ls_ops = LocalLsOperations(layer)
    entries, truncated = await ls_ops.list_dir(LsOptions(path=f"{ROOT}/src"))
    assert [(e.name, e.is_directory) for e in entries] == [("hello.py", False)]
    try:
        await ls_ops.list_dir(LsOptions(path=f"{ROOT}/nope"))
        raise AssertionError("missing dir should raise")
    except FileNotFoundError:
        pass

    print("== grep（便携引擎：walk + read + 正则）")
    await write_ops.write_file(f"{ROOT}/src/other.md", "nothing here\n")
    grep_ops = LocalGrepOperations(layer)
    assert layer.accelerates_search is False  # 便携引擎（不经本机 rg）
    grep_result = await grep_ops.grep(ROOT, GrepOptions(pattern="greetings"))
    assert grep_result.match_count == 1 and "hello.py:1" in grep_result.content, (
        grep_result.content
    )

    print("== find（便携引擎：walk + match）")
    find_ops = LocalFindOperations(layer)
    found = await find_ops.find(FindOptions(path=ROOT, pattern="*.py"))
    assert found == ["src/hello.py"], found

    print("== 清理远程测试目录")
    try:
        await client.fs.remove(f"file://{ROOT}", recursive=True, force=True)
    except Exception:
        pass

    await manager.close_all()
    print("\nALL REMOTE FS ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
