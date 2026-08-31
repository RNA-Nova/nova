"""隧道死亡 → 懒重供给 e2e：kill 本地 ssh 模拟崩溃，验证自动重连。"""

import asyncio
import os
import signal
import sys

from nova_coding_agent.executor import ExecutorBashOperations, get_executor_manager, parse_ssh_target

TARGET = "liujinming@180.184.33.245"


async def main() -> int:
    manager = get_executor_manager()
    target = parse_ssh_target(TARGET)
    url = target.canonical_url

    client1 = await manager.get_client(url)
    handle1 = manager._ssh_handles[url]
    print(f"first tunnel: {handle1.url} (ssh pid {handle1.process.pid})")

    # SIGKILL 本地 ssh——模拟崩溃/断电（非 terminate 的温和路径）
    os.kill(handle1.process.pid, signal.SIGKILL)
    await handle1.process.wait()
    print("tunnel killed (SIGKILL)")

    # 懒路径应检测死隧道并重供给
    client2 = await manager.get_client(url)
    handle2 = manager._ssh_handles[url]
    assert client2 is not client1
    assert handle2.process.pid != handle1.process.pid
    print(f"re-provisioned: {handle2.url} (ssh pid {handle2.process.pid})")

    ops = ExecutorBashOperations(manager, url=url)
    result = await ops.execute("echo reconnect-ok && hostname", "/tmp", {})
    assert result.exit_code == 0 and "reconnect-ok" in result.output
    print(f"remote exec after reconnect: {result.output.strip()}")

    await manager.close_all()
    print("RECONNECT E2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
