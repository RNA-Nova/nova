"""SSH 远程 executor 供给 e2e（真实服务器 180.184.33.245）。

全链路：BatchMode 探测 → 缓存二进制 scp 上传 → 单 ssh 隧道 + 远程 executor
→ ExecutorClient 经隧道执行 uname/hostname → 幂等复用 → close_all 回收。

前提（首连引导的手工等价——bootstrap 的交互密码部分无法脚本化）：
- ~/.nova/agent/executor/id_ed25519 已装入服务器 authorized_keys；
- ~/.nova/agent/executor/bin/linux-x86_64/nova-executor 缓存件已就位。

用法：pixi run -e dev python packages/nova-harness/frontend/scripts/ssh-executor-e2e.py
"""

import asyncio
import sys

from nova_coding_agent.executor import (
    ExecutorBashOperations,
    get_executor_manager,
    parse_ssh_target,
)

TARGET = "liujinming@180.184.33.245"


async def main() -> int:
    manager = get_executor_manager()
    target = parse_ssh_target(TARGET)

    steps = []
    print(f"== provision {TARGET}（首次：含二进制上传）")
    client = await manager.provision_ssh(target, on_progress=lambda t: steps.append(t))
    for step in steps:
        print(f"   [progress] {step}")
    print(f"   tunnel ok: {client is not None}")

    print("== 经隧道执行远程命令")
    ops = ExecutorBashOperations(manager, url=target.canonical_url)
    result = await ops.execute("uname -a && hostname && whoami", "/tmp", {})
    assert result.exit_code == 0, f"exit={result.exit_code} out={result.output}"
    out = result.output.strip()
    print(f"   {out}")
    assert "Linux" in out and "liujinming" in out

    print("== 幂等复用（二次 provision 不重传）")
    steps2 = []
    await manager.provision_ssh(target, on_progress=lambda t: steps2.append(t))
    assert steps2 == [], f"重复供给应零步骤，实际：{steps2}"
    print("   reused live tunnel ✓")

    print("== 懒路径（get_client ssh:// 直取）")
    client2 = await manager.get_client(target.canonical_url)
    assert client2 is client
    print("   same client ✓")

    print("== close_all 回收")
    await manager.close_all()
    print("   closed ✓")

    print("\nALL E2E ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
