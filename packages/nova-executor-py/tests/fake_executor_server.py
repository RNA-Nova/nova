"""测试用假 executor：stdio NDJSON JSON-RPC 服务端（一行一条消息）。

行为表（按行读 JSON）：
- initialize → result {sessionId, protocolVersion}（版本取 FAKE_PROTOCOL_VERSION，缺省 "1.0"）
- echo → result 原样返回 params
- envinfo → result {fakeVar, cwd, hasHome}（验证 env 叠加与 cwd 传递）
- fail → error {code: -32600, message: "boom"}
- notify → 先回 result，再主动推一条 {method: "fake/notice"} 通知
- exit → 不回包直接以退出码 3 退出（进程死亡传播测试）
- 环境变量 FAKE_STDERR=1 时每条消息向 stderr 写一行（stderr 消费测试）
"""

import json
import os
import sys


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if os.environ.get("FAKE_STDERR"):
            print(f"fake stderr: {msg.get('method')}", file=sys.stderr, flush=True)

        method = msg.get("method")
        if method == "exit":
            sys.exit(3)
        if msg.get("id") is None:  # 通知无回执
            continue

        if method == "initialize":
            result = {
                "sessionId": "fake-session",
                "protocolVersion": os.environ.get("FAKE_PROTOCOL_VERSION", "1.0"),
            }
        elif method == "echo":
            result = msg.get("params")
        elif method == "envinfo":
            result = {
                "fakeVar": os.environ.get("FAKE_VAR"),
                "cwd": os.getcwd(),
                "hasHome": "HOME" in os.environ,
            }
        elif method == "fail":
            print(
                json.dumps(
                    {"id": msg["id"], "error": {"code": -32600, "message": "boom"}}
                ),
                flush=True,
            )
            continue
        elif method == "notify":
            print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
            print(
                json.dumps(
                    {"method": "fake/notice", "params": {"from": "fake-server"}}
                ),
                flush=True,
            )
            continue
        elif method == "sleep":
            # 延迟响应（客户端超时测试用）
            import time

            time.sleep((msg.get("params") or {}).get("ms", 1000) / 1000)
            result = {"slept": True}
        else:
            result = {}
        print(json.dumps({"id": msg["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
