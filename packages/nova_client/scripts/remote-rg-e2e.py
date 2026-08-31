"""远程 rg 加速真机 e2e（真实服务器 180.184.33.245）。

验证 B 层全链路：SSH 供给（探测到远程 rg）→ ExecutorProcessRunner
→ 远程 rg --json 经 process/start 执行 → 同一份解析出结构化匹配；
以及无 rg 时便携引擎兜底的切换正确性。

用法：pixi run -e dev python packages/nova-harness/frontend/scripts/remote-rg-e2e.py
"""

import asyncio
import sys

from nova_coding_agent.executor import get_executor_manager, parse_ssh_target
from nova_coding_agent.executor.fs_layer import ExecutorFileSystemLayer
from nova_coding_agent.executor.process_runner import ExecutorProcessRunner
from nova_coding_agent.tools_common.operations import (
    GrepOptions,
    LocalFindOperations,
    LocalGrepOperations,
    FindOptions,
)

TARGET = "liujinming@180.184.33.245"
ROOT = "/home/liujinming/.nova/agent/executor/workspaces/rg-e2e"


async def main() -> int:
    manager = get_executor_manager()
    target = parse_ssh_target(TARGET)
    print(f"== provision {TARGET}（探测含 command -v rg）")
    await manager.provision_ssh(target)

    runner = ExecutorProcessRunner(manager, target.canonical_url)
    rg_path = await runner.rg_path()
    print(f"   探测到远程 rg: {rg_path}")
    assert rg_path and rg_path.endswith("rg"), f"未探测到远程 rg: {rg_path!r}"

    layer = ExecutorFileSystemLayer(manager, target.canonical_url)
    client = await manager.get_client(target.canonical_url)
    try:
        await client.fs.remove(f"file://{ROOT}", recursive=True, force=True)
    except Exception:
        pass
    await layer.create_dir(f"{ROOT}/src")
    await layer.write_bytes(
        f"{ROOT}/src/alpha.py", "def alpha():\n    return 1\n".encode()
    )
    await layer.write_bytes(f"{ROOT}/src/beta.md", "no match\n".encode())
    await layer.write_bytes(f"{ROOT}/src/gamma.py", "alpha mentioned\n".encode())

    print("== grep 经远程 rg --json（同一份 _collect_with_rg 解析）")
    grep_ops = LocalGrepOperations(layer, runner)
    result = await grep_ops.grep(ROOT, GrepOptions(pattern="alpha"))
    assert result.match_count == 2, result.content
    assert "alpha.py:1" in result.content and "gamma.py:1" in result.content
    assert "beta.md" not in result.content
    print(f"   matches={result.match_count} ✓（含行号格式）")

    print("== grep glob 过滤经远程 rg")
    result = await grep_ops.grep(ROOT, GrepOptions(pattern="alpha", glob="*.md"))
    assert result.no_matches is True  # *.md 里无 alpha 匹配行
    print("   glob 生效 ✓")

    print("== find 经远程 rg --files")
    find_ops = LocalFindOperations(layer, runner)
    found = await find_ops.find(FindOptions(path=ROOT, pattern="*.py"))
    assert sorted(found) == ["src/alpha.py", "src/gamma.py"], found
    print(f"   {found} ✓")

    print("== 便携兜底切换正确性（runner 无 rg → 同代码走 walk 引擎）")

    class _NoRgRunner:
        async def rg_path(self):
            return None

        async def fd_path(self):
            return None

    fallback_ops = LocalGrepOperations(layer, _NoRgRunner())
    result = await fallback_ops.grep(ROOT, GrepOptions(pattern="alpha"))
    assert result.match_count == 2
    print("   便携引擎结果与 rg 一致 ✓")

    await client.fs.remove(f"file://{ROOT}", recursive=True, force=True)
    await manager.close_all()
    print("\nALL REMOTE RG ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
