"""测试用假 executor：stdio NDJSON JSON-RPC 服务端（一行一条消息）。

行为表（按行读 JSON）：
- initialize → result {sessionId, protocolVersion}（版本取 FAKE_PROTOCOL_VERSION，缺省 "1.0"）
- echo → result 原样返回 params
- envinfo → result {fakeVar, cwd, hasHome}（验证 env 叠加与 cwd 传递）
- environment/status → result {status: "ready"}（EnvironmentManager.status 探活用）
- fail → error {code: -32600, message: "boom"}
- notify → 先回 result，再主动推一条 {method: "fake/notice"} 通知
- sleep → 延迟 params.ms 毫秒后回 {"slept": true}（客户端超时测试用）
- garbage → 先写一行非 JSON 再回 {}（客户端畸形帧断连测试——对位 Rust
  MalformedMessage：坏帧即连接级致命，客户端不会读到 {}）
- http/request → 回 {status, headers, bodyBase64}；streamResponse 时先回包再推 bodyDelta 通知流
- pushraw → 先回 result {}，再按 params.messages 逐条原样推送（入站限流/畸形帧测试用）
- stderr_spam → 向 stderr 写一行 params.len 长的内容后正常回包（stderr 泵分块读测试）
- close_stdout → 关 stdout（客户端见 EOF）但进程长眠不死（旧进程收割测试）
- spawn_grandchild → 拉起一个长眠孙进程并把 pid 回给客户端（进程组组杀测试）
- exit → 不回包直接以退出码 3 退出（进程死亡传播测试）
- 环境变量 FAKE_STDERR=1 时每条消息向 stderr 写一行（stderr 消费测试）

严格姿态（对位真实服务端 handler.rs / request_dispatcher.rs 与
PROTOCOL.md:45-46）：
- initialize 每连接仅一次——重复 initialize 报 -32600
  （"initialize may only be sent once per connection"）
- 未知方法 → error {code: -32601}（method_not_found），不回空结果冒充成功
- 未知通知 → 直接关闭连接（stdio 形态下即进程退出——本进程即本连接）；
  已知通知仅 {"initialized"}
"""

import base64
import json
import os
import subprocess
import sys

#: 已知通知白名单（对位真实服务端 notification_route 注册表）
KNOWN_NOTIFICATIONS = {"initialized"}


def main() -> None:
    initialized = False
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
            if method not in KNOWN_NOTIFICATIONS:
                # 未知通知 → 直接关闭连接（PROTOCOL.md:46 严格姿态；
                # stdio 一进程一连接，退出即关连接）
                print(
                    f"fake server closing connection: unknown notification {method}",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(0)
            continue

        if method == "initialize":
            if initialized:
                # 每连接仅一次（PROTOCOL.md:45；对位 handler.rs invalid_request）
                print(
                    json.dumps(
                        {
                            "id": msg["id"],
                            "error": {
                                "code": -32600,
                                "message": "initialize may only be sent once per connection",
                            },
                        }
                    ),
                    flush=True,
                )
                continue
            initialized = True
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
                # Windows 没有 HOME（家目录变量是 USERPROFILE），按平台回退检测
                "hasHome": bool(
                    os.environ.get("HOME") or os.environ.get("USERPROFILE")
                ),
            }
        elif method == "environment/status":
            # 对位真实服务端的探活回包（EnvironmentStatusKind::Ready）
            result = {"status": "ready"}
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
        elif method == "http/request":
            params = msg.get("params") or {}
            body = base64.b64encode(
                f"echo:{params.get('method')}:{params.get('url')}".encode()
            ).decode()
            if params.get("streamResponse"):
                result = {"status": 200, "headers": [], "bodyBase64": ""}
                print(
                    json.dumps({"id": msg["id"], "result": result}),
                    flush=True,
                )
                for seq, (delta, done) in enumerate(
                    [(body[:4], False), (body[4:], True)], 1
                ):
                    print(
                        json.dumps(
                            {
                                "method": "http/request/bodyDelta",
                                "params": {
                                    "requestId": params.get("requestId"),
                                    "seq": seq,
                                    "deltaBase64": delta,
                                    "done": done,
                                },
                            }
                        ),
                        flush=True,
                    )
                continue
            result = {"status": 200, "headers": [], "bodyBase64": body}
        elif method == "sleep":
            # 延迟响应（客户端超时测试用）
            import time

            time.sleep((msg.get("params") or {}).get("ms", 1000) / 1000)
            result = {"slept": True}
        elif method == "garbage":
            # 先写一行非 JSON 再回正常结果（客户端畸形帧断连测试——对位 Rust
            # MalformedMessage：客户端读到坏帧即断开，不会读到后面的 {}）
            print("this-line-is-not-json", flush=True)
            result = {}
        elif method == "policy/probe":
            # 反向裁决测试：先向客户端发 network/policyRequest 并读回应答，
            # 把客户端的裁决（或错误）装进自己的结果里。
            # id 用整数——真实服务端只发 RequestId::Integer（对位
            # rpc_server_requests.rs），客户端准入校验拒绝字符串 id
            probe_id = 1
            print(
                json.dumps(
                    {
                        "id": probe_id,
                        "method": "network/policyRequest",
                        "params": {
                            "processId": "p1",
                            "request": {
                                "protocol": "https_connect",
                                "host": "api.example.com",
                                "port": 443,
                            },
                        },
                    }
                ),
                flush=True,
            )
            decision_seen = None
            for raw_line in sys.stdin:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    reply = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if "method" in reply:
                    continue  # 客户端自己的请求，不是给我们的应答
                if reply.get("id") == probe_id:
                    if "error" in reply:
                        decision_seen = {"error": reply["error"]["message"]}
                    else:
                        decision_seen = reply["result"]["decision"]
                    break
            result = {"saw": decision_seen}
        elif method == "policy/probe_batch":
            # 并发/准入/满员测试：连发 params.count 个 network/policyRequest
            # （整数 id 1..N），再收齐全部应答装进结果
            count = int((msg.get("params") or {}).get("count", 1))
            for probe_id in range(1, count + 1):
                print(
                    json.dumps(
                        {
                            "id": probe_id,
                            "method": "network/policyRequest",
                            "params": {
                                "processId": f"p{probe_id}",
                                "request": {
                                    "protocol": "https_connect",
                                    "host": f"h{probe_id}.example.com",
                                    "port": 443,
                                },
                            },
                        }
                    ),
                    flush=True,
                )
            saw = {}
            while len(saw) < count:
                raw_line = sys.stdin.readline()
                if not raw_line:
                    break  # 客户端已断开（准入违规等测试路径）
                try:
                    reply = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if "method" in reply:
                    continue  # 客户端自己的请求，不是给我们的应答
                reply_id = reply.get("id")
                if (
                    isinstance(reply_id, int)
                    and 1 <= reply_id <= count
                    and reply_id not in saw
                ):
                    if "error" in reply:
                        saw[reply_id] = {"error": reply["error"]["message"]}
                    else:
                        saw[reply_id] = reply["result"]["decision"]
            result = {"saw": saw}
        elif method == "policy/probe_bad_id":
            # 准入违规测试：字符串 id 的反向请求（真实服务端只发非负整数 id）
            print(
                json.dumps(
                    {
                        "id": "bad-1",
                        "method": "network/policyRequest",
                        "params": {
                            "processId": "p1",
                            "request": {
                                "protocol": "https_connect",
                                "host": "bad.example.com",
                                "port": 443,
                            },
                        },
                    }
                ),
                flush=True,
            )
            result = {}
        elif method == "policy/probe_dup":
            # 在飞去重测试：同一 id 连发两次（第二个到达时第一个仍在飞）
            dup_request = {
                "id": 1,
                "method": "network/policyRequest",
                "params": {
                    "processId": "p1",
                    "request": {
                        "protocol": "https_connect",
                        "host": "dup.example.com",
                        "port": 443,
                    },
                },
            }
            print(json.dumps(dup_request), flush=True)
            print(json.dumps(dup_request), flush=True)
            result = {}
        elif method == "pushraw":
            # 入站限流/畸形帧测试：先回 result，再按 params.messages 逐条原样推送
            print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
            for pushed in (msg.get("params") or {}).get("messages", []):
                print(json.dumps(pushed), flush=True)
            continue
        elif method == "stderr_spam":
            # stderr 泵测试：向 stderr 写一行 params.len 长的内容后正常回包
            line_len = int((msg.get("params") or {}).get("len", 100))
            sys.stderr.write("s" * line_len + "\n")
            sys.stderr.flush()
            result = {}
        elif method == "close_stdout":
            # 旧进程收割测试：关 stdout（客户端见 EOF）但进程长眠不死。
            # 须关底层 fd——sys.stdout.close() 不关 fd（FileIO closefd=False）
            import time

            sys.stdout.flush()
            os.close(sys.stdout.fileno())
            time.sleep(30)
            sys.exit(0)
        elif method == "spawn_grandchild":
            # 进程组组杀测试：拉起一个长眠孙进程（继承本进程组），pid 回给客户端
            grandchild = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            result = {"grandchildPid": grandchild.pid}
        elif method == "policy/audit":
            # 审计推送测试：先回 result，再推一条 network/policyDecision 通知
            print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
            print(
                json.dumps(
                    {
                        "method": "network/policyDecision",
                        "params": {
                            "processId": "p1",
                            "timestamp": "2026-08-31T00:00:00Z",
                            "scope": "process",
                            "decision": "allow",
                            "source": "allowlist",
                            "reason": "listed domain",
                            "protocol": "https_connect",
                            "host": "pypi.org",
                            "port": 443,
                        },
                    }
                ),
                flush=True,
            )
            continue
        else:
            # 未知方法 → method_not_found（-32601；对位 request_dispatcher.rs），
            # 不回空结果冒充成功
            print(
                json.dumps(
                    {
                        "id": msg["id"],
                        "error": {
                            "code": -32601,
                            "message": f"exec-server stub does not implement `{method}` yet",
                        },
                    }
                ),
                flush=True,
            )
            continue
        print(json.dumps({"id": msg["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
