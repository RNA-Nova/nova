#!/usr/bin/env python3
"""打包产物 RPC 握手冒烟（stdlib only——CI runner 裸 python3 可跑）。

流程：spawn ``nova-server``（默认 rpc 模式）→ stdio NDJSON 发 ``initialize``
→ 校验应答（version 为语义化版本戳、契约 major/minor 在场、能力位非空）
→ ``shutdown`` + stdin EOF 收尸 → 断言退出码 0。

用法：
    python3 smoke-rpc-handshake.py [nova-server 路径]

环境变量：NOVA_AGENT_DIR 可沙盒后端状态根（不设则用默认 ~/.nova/agent——
CI 上建议设到临时目录）。
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time


class Wire:
    """最小 NDJSON wire 客户端（行读泵 + 请求/应答配对）。"""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._lines.put(line)

    def call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
            + "\n"
        )
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} 未在 {timeout}s 内应答")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            msg = json.loads(line)
            if msg.get("id") == 1 and ("result" in msg or "error" in msg):
                return msg


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else "runtime/nova-server"
    proc = subprocess.Popen(
        [binary],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  #  stderr 已被进程自重定向到日志文件
        text=True,
        bufsize=1,
    )
    ok = False
    try:
        wire = Wire(proc)
        started = time.monotonic()
        resp = wire.call("initialize")
        elapsed = time.monotonic() - started
        if "error" in resp:
            print(f"✘ initialize 出错: {json.dumps(resp['error'], ensure_ascii=False)}")
        else:
            result = resp["result"]
            version = str(result.get("version", ""))
            major = result.get("contractVersionMajor")
            minor = result.get("contractVersionMinor")
            methods = result.get("capabilities", {}).get("methods", [])
            print(
                f"✔ initialize 应答（{elapsed:.1f}s）: "
                f"version={version} 契约={major}.{minor} 方法数={len(methods)}"
            )
            if not re.match(r"^\d+\.\d+\.\d+", version):
                print(f"✘ version 非语义化版本戳: {version!r}")
            elif not isinstance(major, int) or not isinstance(minor, int):
                print("✘ 契约版本字段缺失")
            elif not methods:
                print("✘ 能力位方法表为空")
            else:
                ok = True
    except Exception as exc:
        print(f"✘ 握手异常: {exc!r}")
    # 收尸：stdin EOF → stdio 连接关闭 → 进程应自行退出
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        rc = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        print("✘ 后端未在 stdin EOF 后自行退出（已强杀）")
        return 1
    if rc != 0:
        print(f"✘ 后端退出码异常: {rc}")
        return 1
    print(f"✔ 后端退出码: {rc}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
