"""黑盒线上契约一致性套件（v1）。

与白盒测试的根本区别：本套件**从管道另一端说话**——spawn 真实后端进程
（``python -m nova_harness.modes.rpc.cli``），经 stdio NDJSON 发命令、收事件，
并用 ``nova-wire.schema.json`` 校验响应与事件形状。后端内部实现一概不碰。

这就是多后端时代的"入网认证"雏形：任何语言的后端实现，只要能被本套件
驱动并跑绿，即为契约兼容。（v1 不依赖 LLM：事件流用 thinking/session_info
变更触发；LLM 事件（message/tool_execution）的覆盖待假模型通道补齐。）
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from jsonschema import validate

from nova_harness.core.rpc.protocol.schema_export import (
    CONTRACT_VERSION_MAJOR,
    CONTRACT_VERSION_MINOR,
)

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "packages"
    / "nova-tui"
    / "protocol"
    / "nova-wire.schema.json"
)

INVALID_PARAMS = -32602


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _with_defs(schema: dict, fragment: dict) -> dict:
    """把 $defs 并入片段，使片段内的 $ref 可解析。"""
    return {**fragment, "$defs": schema["$defs"]}


class Wire:
    """最小黑盒客户端：NDJSON 请求/响应 + 通知捕获 + 反向原语应答。

    单线程顺序模型：``call`` 在等待响应期间消化间插的通知（事件入
    ``events``，``ui/request`` 按 ``ui_answer`` 自动应答）——与服务器
    的并发分派正好互补。
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._next_id = 0
        self.events: list[dict] = []
        self.ui_requests: list[dict] = []
        self.ui_answer: object = "a"
        # 读侧：专职 reader 线程 + 队列。不用 select+readline——TextIOWrapper
        # 有用户态预读缓冲，多帧合并到达（连接化后写泵批量吐帧）时后到的行
        # 会卡在缓冲里、select 永不再报可读（实证挂起）；线程阻塞读无此问题
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump_lines, daemon=True).start()

    def _pump_lines(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self._lines.put(line)

    # ------------------------------------------------------------------
    # 基础 IO
    # ------------------------------------------------------------------

    def _write(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _readline(self, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(
        self, method: str, params: dict | None = None, timeout: float = 15.0
    ) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        )
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} 未在 {timeout}s 内应答")
            line = self._readline(remaining)
            if line is None:
                continue
            msg = json.loads(line)
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
            self._handle_notification(msg)

    def drain_events(self, seconds: float = 0.5) -> list[dict]:
        """短时排干通知（事件与响应的相对顺序故意不做假设）。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = self._readline(0.1)
            if line is None:
                continue
            self._handle_notification(json.loads(line))
        return list(self.events)

    def _handle_notification(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "agent/event":
            self.events.append(msg["params"])
        elif method == "ui/request":
            self.ui_requests.append(msg["params"])
            request_id = msg["params"]["id"]
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": f"ui-resp-{request_id}",
                    "method": "ui/response",
                    "params": {"id": request_id, "result": {"value": self.ui_answer}},
                }
            )


class WsWire(Wire):
    """WebSocket 版黑盒客户端（连接化 P1 双传输参数化）。

    与 stdio 版共用 call/notify/drain_events 语义；读侧同样是
    "专职线程 + 队列"（websockets sync client 的迭代器直接排干）。
    """

    def __init__(self, proc: subprocess.Popen, conn) -> None:
        # 先挂 conn 再进父类 __init__——后者立即启动读泵线程，
        # _pump_lines 重写版要用 _conn
        self._conn = conn
        super().__init__(proc)

    def _pump_lines(self) -> None:
        for message in self._conn:
            self._lines.put(message)

    def _write(self, msg: dict) -> None:
        self._conn.send(json.dumps(msg, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------


def _spawn_backend(extra_args: list[str] | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "nova_harness.modes.rpc.cli", *(extra_args or [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    # stderr 排干线程（异常栈不进协议通道；防止管道缓冲写满死锁）；
    # 内容留档——teardown 断言失败时带出后端死因
    stderr_lines: list[str] = []

    def _drain() -> None:
        for line in proc.stderr:
            stderr_lines.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    proc.stderr_lines = stderr_lines  # type: ignore[attr-defined]
    return proc


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(params=["stdio", "ws"])
def backend(request, tmp_path):
    """一个隔离的后端进程 + 黑盒客户端（tmp cwd + tmp agentDir）。

    双传输参数化（P1）：同一套契约用例跑 stdio 与 WebSocket 两种接入——
    传输无关性（任一传输跑绿即契约兼容）的实证。
    """
    (tmp_path / "proj").mkdir()
    (tmp_path / "agent").mkdir()
    # 免 trust 询问（一致性测试聚焦协议，不测 trust 流程）
    (tmp_path / "agent" / "settings.json").write_text(
        json.dumps({"defaultProjectTrust": "always"}), encoding="utf-8"
    )
    if request.param == "ws":
        from websockets.sync.client import connect as ws_connect

        token = "conformance-token"
        port = _free_port()
        proc = _spawn_backend(["--listen", f"ws://127.0.0.1:{port}", "--token", token])
        # 等服务起来（acceptor 绑定端口后再连）；建链失败必须杀掉进程——
        # setup 期异常不会跑 teardown，不杀就是孤儿
        try:
            conn = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    conn = ws_connect(
                        f"ws://127.0.0.1:{port}",
                        additional_headers={"Authorization": f"Bearer {token}"},
                    )
                    break
                except OSError:
                    time.sleep(0.1)
            assert conn is not None, "WS 后端 10s 内未就绪"
        except BaseException:
            proc.kill()
            proc.wait(timeout=5)
            raise
        wire = WsWire(proc, conn)
    else:
        proc = _spawn_backend()
        wire = Wire(proc)
    wire.cwd = str(tmp_path / "proj")
    wire.agent_dir = str(tmp_path / "agent")
    # 协议握手：连接化后 initialize 是事件广播/UI 寻址的门（真实客户端
    # 开工前必先握手——黑盒客户端同款纪律）
    wire.call("initialize")
    yield wire
    # 关停语义：shutdown 命令 + stdin EOF（stdio 后端主循环随管道断开退出；
    # WS 形态 stdin 不是生命线，POSIX 走 SIGTERM——顺带覆盖信号关停路径；
    # Windows 的 terminate() 是硬杀，仅超时兜底才动用）
    try:
        wire.call("shutdown", timeout=5)
    except Exception:
        pass
    try:
        assert proc.stdin is not None
        proc.stdin.close()
    except Exception:
        pass
    timed_out = False
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if timed_out and sys.platform == "win32":
        # Windows 的 terminate/kill 无优雅退出码（TerminateProcess 恒非零）——
        # 超时强杀不裁决退出码；响应面（shutdown 应答）已在上方验证
        return
    tail = "".join(getattr(proc, "stderr_lines", [])[-40:])
    assert proc.returncode == 0, f"后端退出码 {proc.returncode}；stderr 尾：\n{tail}"


def _create_session(wire: Wire) -> dict:
    return wire.call("createSession", {"cwd": wire.cwd, "agentDir": wire.agent_dir})


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------


def test_handshake_contract(backend):
    schema = _load_schema()
    resp = backend.call("initialize")
    assert "error" not in resp
    result = resp["result"]

    # 契约版本（major/minor）与入仓 schema 一致
    assert result["contractVersionMajor"] == CONTRACT_VERSION_MAJOR
    assert result["contractVersionMinor"] == CONTRACT_VERSION_MINOR
    assert result["contractVersionMajor"] == schema["contractVersionMajor"]
    assert result["contractVersionMinor"] == schema["contractVersionMinor"]

    # 能力位真实：8 域、68 方法（与方法表一致）
    caps = result["capabilities"]
    assert sorted(caps["domains"]) == sorted(
        [
            "session",
            "model",
            "auth",
            "resources",
            "settings",
            "system",
            "user_tools",
            "package",
        ]
    )
    assert sorted(caps["methods"]) == sorted(schema["methods"].keys())


def test_session_lifecycle_and_method_shapes(backend):
    schema = _load_schema()

    resp = _create_session(backend)
    assert "error" not in resp
    assert resp["result"]["sessionId"]

    # getSessionState 响应符合方法表形状
    resp = backend.call("getSessionState")
    assert "error" not in resp
    validate(
        resp["result"],
        _with_defs(schema, schema["methods"]["getSessionState"]["result"]),
    )

    # 参数校验：非法 thinking level → INVALID_PARAMS
    resp = backend.call("setThinkingLevel", {"level": "banana"})
    assert resp["error"]["code"] == INVALID_PARAMS

    # 合法调用 → ok
    resp = backend.call("setThinkingLevel", {"level": "high"})
    assert resp["result"]["ok"] is True

    # getSessionEntries：条目符合 sessionEntry schema
    resp = backend.call("getSessionEntries")
    assert "error" not in resp
    entry_schema = _with_defs(schema, schema["sessionEntry"])
    for entry in resp["result"]["entries"]:
        validate(entry, entry_schema)

    # listSessions：数组形状
    resp = backend.call("listSessions", {"cwd": backend.cwd})
    assert "error" not in resp
    validate(
        resp["result"],
        _with_defs(schema, schema["methods"]["listSessions"]["result"]),
    )

    # getSettings：设置对象
    resp = backend.call("getSettings")
    assert "error" not in resp
    assert isinstance(resp["result"]["settings"], dict)

    # pkgCheckUpdates：只读更新检查——tmp 环境无包配置，返回空列表且形状合法
    resp = backend.call("pkgCheckUpdates")
    assert "error" not in resp
    validate(
        resp["result"],
        _with_defs(schema, schema["methods"]["pkgCheckUpdates"]["result"]),
    )
    assert resp["result"]["updates"] == []


def test_events_conform_to_schema(backend):
    schema = _load_schema()
    event_schema = _with_defs(schema, schema["novaEvent"])

    _create_session(backend)
    backend.events.clear()

    # 选一个与当前不同的级别（初始级别随环境模型而变，不做假设）
    state = backend.call("getSessionState")["result"]
    current = state["thinkingLevel"]
    candidates = [
        level for level in state["availableThinkingLevels"] if level != current
    ]
    target = candidates[0] if candidates else ("high" if current != "high" else "low")

    backend.call("setThinkingLevel", {"level": target})
    backend.call("setSessionName", {"name": "conformance"})
    events = backend.drain_events(1.0)

    types = {e["type"] for e in events}
    # 无可切换级别时事件无从产生（无推理模型的极简环境——如干净 CI，
    # 唯一可用级别即当前级别，setThinkingLevel 吸附回原级不发事件）；
    # 有候选则必须发出（开发机带模型路径）。
    if candidates:
        assert "thinking_level_changed" in types
    assert "session_info_changed" in types
    for event in events:
        validate(event, event_schema)


def test_reverse_primitives_roundtrip(backend):
    """扩展经 session_start 发起 select → ui/request → ui/response 应答。"""
    extensions_dir = Path(backend.agent_dir) / "extensions"
    extensions_dir.mkdir(parents=True, exist_ok=True)
    (extensions_dir / "probe.py").write_text(
        "def extension(nova):\n"
        "    async def on_start(event, ctx):\n"
        "        if ctx.has_ui:\n"
        "            await ctx.ui.request('select', {'title': 'probe', 'options': ['a', 'b']})\n"
        "    nova.on('session_start', on_start)\n",
        encoding="utf-8",
    )

    backend.call("dispose")
    backend.notify("system/capabilities", {"capabilities": ["select"]})
    resp = backend.call(
        "createSession", {"cwd": backend.cwd, "agentDir": backend.agent_dir}
    )
    assert "error" not in resp

    # 反向原语确实到达且已按测试侧应答（Wire 自动回 ui/response）
    assert any(
        req.get("component", {}).get("componentType") == "select"
        or req.get("method") == "select"
        for req in backend.ui_requests
    )


def test_sync_session_anchor_and_paging(backend):
    """syncSession（连接化 P2）：原子快照（state + entries + eventSeq 高水位）
    与条目分页（getSessionEntries limit/offset）。"""
    _create_session(backend)

    # 原子快照：三件套一帧拿齐
    sync = backend.call("syncSession", {})
    assert "error" not in sync
    result = sync["result"]
    assert result["state"]["sessionId"]
    assert isinstance(result["entries"], list)
    assert result["total"] == len(result["entries"])  # 缺省全量
    assert isinstance(result["eventSeq"], int) and result["eventSeq"] >= 0

    # 高水位语义：sync 之后再触发事件，其 seq 严格大于水位
    backend.events.clear()
    backend.call("setSessionName", {"name": "anchored"})
    events = backend.drain_events(1.0)
    assert events, "setSessionName 应产生 session_info_changed 事件"
    assert all(e["seq"] > result["eventSeq"] for e in events)
    assert all(isinstance(e["ts"], int) and "sessionId" in e for e in events)

    # 分页：limit=1 逐页翻完，拼起来等于此刻的全量（注意 total 是快照时点
    # 值——setSessionName 之后条目已增长，终止判定以翻页翻空 + 全量对照为准）
    total = result["total"]
    if total >= 2:  # 会话太短（只有 header）就跳过翻页断言
        collected = []
        offset = 0
        while True:
            page = backend.call("getSessionEntries", {"offset": offset, "limit": 1})
            chunk = page["result"]["entries"]
            if not chunk:
                break
            collected.extend(chunk)
            offset += len(chunk)
        full = backend.call("getSessionEntries", {})
        full_entries = full["result"]["entries"]
        assert len(collected) == len(full_entries)
        assert [e["id"] for e in collected] == [e["id"] for e in full_entries]
