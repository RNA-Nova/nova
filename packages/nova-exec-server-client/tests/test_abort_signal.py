"""SDK 取消词汇（AbortSignal/AbortedError）接线测试——对位
docs/crosscutting/concurrency.md（取消原语族/完成优先/死因分型）：

- transport 层：send_request 在飞 signal → AbortedError + pending 表即清 +
  在飞槽归还；预触发信号快失败不发请求；完成优先竞速（同刻就绪返回结果）；
- fs 层：read_stream 消费中 signal → AbortedError + 路由注销 + fs/close
  收摊（收摊纪律不破）；
- process/start 门控：AbortedError 臂——回执已失，后台 RPC 落定后按取消
  分支调度 terminate 清理（对位 test_client_parity 的 cancel 测试形状）；
- recovery：恢复等待（_await_transport）中 signal → AbortedError，恢复
  编排不受扰（取消的是调用方等待，不是恢复过程）；
- http_request：流式消费（bodyDelta queue 等待）中 signal → AbortedError +
  路由注销；
- 探活：预触发 signal → AbortedError，不发请求。

纪律：竞速/中断用 Event 受控完成序（testing-modeling §七）；sleep(0) 仅作
事件循环出让点；就绪探测沿用套件既有的有界轮询习语。
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from fake_transport import FakeTransport
from nova_exec_server_client import ExecutorClient, ReconnectStrategy
from nova_exec_server_client.fs import FileSystemManager
from nova_exec_server_client.recovery import ManagedTransport
from nova_exec_server_client.transport import _JsonRpcTransport
from nova_protocol import AbortController, AbortedError, InitializeResponse

INIT_OK = {"sessionId": "fake-session", "protocolVersion": "1.13"}


class MemTransport(_JsonRpcTransport):
    """内存传输（`_JsonRpcTransport` 基类单测用）：录制写线消息，响应经
    `_resolve_response` 手动注入——不依赖 stdio/WS 真实承载"""

    def __init__(self, request_timeout: float = 5.0):
        super().__init__(request_timeout)
        self.sent: list[dict] = []
        self._closed = False  # 已连接态（基类初值 True = 未连接即关闭态）

    async def _send_message(self, message: dict) -> None:
        self.sent.append(message)


# ---------------------------------------------------------------------------
# transport 层：_JsonRpcTransport.send_request 的取消词汇
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_request_aborted_in_flight_clears_pending():
    """在飞 signal 触发 → AbortedError；pending 表即清 + 在飞槽归还
    （对位 Rust PendingRequestGuard 的 finally 覆盖取消路径）"""
    transport = MemTransport()
    controller = AbortController()
    task = asyncio.create_task(
        transport.send_request(
            "fs/readFile", {"path": "file:///tmp/x"}, signal=controller.signal
        )
    )
    # 出让两轮：任务跑过槽位裁决 → 写线 → 进入响应等待竞速
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(transport.sent) == 1
    assert len(transport._pending) == 1
    assert transport._in_flight_regular == 1

    controller.abort()
    with pytest.raises(AbortedError):
        await task
    assert transport._pending == {}  # 取消残留即清，不等后续流量/断线
    assert transport._in_flight_regular == 0  # 在飞槽归还（SemaphorePermit drop）


@pytest.mark.asyncio
async def test_send_request_pre_aborted_sends_nothing():
    """预触发信号：举旗点快失败——不占槽、不写线、不留 pending"""
    transport = MemTransport()
    controller = AbortController()
    controller.abort()

    with pytest.raises(AbortedError):
        await transport.send_request("fs/readFile", signal=controller.signal)
    assert transport.sent == []
    assert transport._pending == {}
    assert transport._in_flight_regular == 0


@pytest.mark.asyncio
async def test_send_request_completion_wins_over_abort():
    """完成优先竞速（对位 concurrency.md §六）：响应先注入就绪队列、abort
    同 tick 触发——已完成的工作不丢弃，返回结果"""
    transport = MemTransport()
    controller = AbortController()
    task = asyncio.create_task(
        transport.send_request("environment/status", signal=controller.signal)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    request_id = transport.sent[0]["id"]

    # 同 tick 布置：先注入响应（op 侧 wakeup 先入就绪队列）再 abort
    transport._resolve_response({"id": request_id, "result": {"status": "ok"}})
    controller.abort()
    assert await task == {"status": "ok"}
    assert transport._pending == {}


@pytest.mark.asyncio
async def test_send_request_timeout_still_maps_to_timeout_error():
    """超时 ≠ 取消（死因分型）：signal 在侧的 wait_for 超时仍抛 TimeoutError，
    不冒名 AbortedError"""
    from nova_exec_server_client import TimeoutError

    transport = MemTransport(request_timeout=0.05)
    controller = AbortController()
    with pytest.raises(TimeoutError, match="timed out"):
        await transport.send_request("fs/readFile", signal=controller.signal)
    assert controller.signal.aborted is False
    assert transport._pending == {}


# ---------------------------------------------------------------------------
# fs 层：read_stream 消费中取消（queue.get 竞速）
# ---------------------------------------------------------------------------


def _chunk_msg(handle: str, seq: int, data: bytes, eof: bool = False) -> dict:
    return {
        "method": "fs/readStream/chunk",
        "params": {
            "handleId": handle,
            "seq": seq,
            "chunk": base64.b64encode(data).decode(),
            "eof": eof,
        },
    }


@pytest.mark.asyncio
async def test_read_stream_aborted_mid_consume_cleans_up():
    """read_stream 消费中 signal → AbortedError；路由注销 + 未收摊路径补
    fs/close（对位 Rust PushReadRegistration::drop 的收摊纪律不破）"""
    transport = FakeTransport({"fs/readStream": {"handleId": "any", "totalSize": 10}})
    fs = FileSystemManager(transport)
    controller = AbortController()

    agen = fs.read_stream("file:///tmp/x", signal=controller.signal)
    first = asyncio.create_task(agen.__anext__())
    for _ in range(100):
        if transport.requests:
            break
        await asyncio.sleep(0.01)
    assert transport.requests, "fs/readStream 请求未发出"
    handle_id = transport.requests[0][1]["handleId"]

    await transport.handlers[0](_chunk_msg(handle_id, 0, b"ab"))
    assert await first == b"ab"

    # 第二块进入 queue.get 竞速等待后触发 signal
    second = asyncio.create_task(agen.__anext__())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    controller.abort()
    with pytest.raises(AbortedError):
        await second

    assert handle_id not in fs._router._streams  # 路由注销
    # 未收摊路径：best-effort fs/close（即发即忘——死线轮询其落地）
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    close_requests = [r for r in transport.requests if r[0] == "fs/close"]
    while not close_requests:
        assert loop.time() < deadline, "fs/close 收摊任务 5s 内未落地"
        await asyncio.sleep(0.01)
        close_requests = [r for r in transport.requests if r[0] == "fs/close"]
    assert close_requests == [("fs/close", {"handleId": handle_id}, None)]


@pytest.mark.asyncio
async def test_read_stream_pre_aborted_sends_nothing():
    """预触发信号：开流请求不发（举旗点在传输层——用真基类 MemTransport
    才经 throw_if_aborted 快失败），路由不残留"""
    transport = MemTransport()
    fs = FileSystemManager(transport)
    controller = AbortController()
    controller.abort()

    with pytest.raises(AbortedError):
        async for _ in fs.read_stream("file:///tmp/x", signal=controller.signal):
            pass
    assert transport.sent == []
    assert fs._router._streams == {}


# ---------------------------------------------------------------------------
# process/start 门控：AbortedError 臂（对位既有 cancel 测试形状）
# ---------------------------------------------------------------------------


class HangableStartTransport(FakeTransport):
    """process/start 挂起直到测试放行（门控取消清理测试用——形状对位
    test_client_parity.HangableStartTransport）"""

    def __init__(self, responses=None):
        super().__init__(responses)
        self.release = asyncio.Event()

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False, signal=None
    ):
        if method == "process/start":
            self.requests.append((method, params or {}, channel))
            await self.release.wait()
            resp = self.responses.get(method, {})
            if isinstance(resp, Exception):
                raise resp
            return resp
        return await super().send_request(
            method, params, channel=channel, for_cleanup=for_cleanup, signal=signal
        )


@pytest.mark.asyncio
async def test_aborted_start_schedules_cleanup_after_rpc_settles():
    """signal 触发 start 取消（门控的 AbortedError 臂——对位
    test_cancelled_start_schedules_cleanup 的取消词汇版）：AbortedError
    上抛调用方只是丢回执——线上 RPC 在后台落定后才调度 terminate 清理"""
    transport = HangableStartTransport(
        {
            "initialize": INIT_OK,
            "process/start": {"processId": "p-1"},
            "process/terminate": {"running": False},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    controller = AbortController()
    start_task = asyncio.create_task(
        client.process.start(["sleep", "5"], "/tmp", signal=controller.signal)
    )
    for _ in range(100):
        if any(m == "process/start" for m, _, _ in transport.requests):
            break
        await asyncio.sleep(0.01)
    controller.abort()
    with pytest.raises(AbortedError):
        await start_task

    # 取消不打断线上 RPC：在飞计数未归还，terminate 清理尚未调度
    assert client._process_start_gate._active == 1
    assert not any(m == "process/terminate" for m, _, _ in transport.requests)

    # RPC 落定（响应到达但回执已失）后才调度 terminate 清理、归还计数
    transport.release.set()
    start_params = [p for m, p, _ in transport.requests if m == "process/start"][0]
    for _ in range(100):
        terminate_params = [
            p for m, p, _ in transport.requests if m == "process/terminate"
        ]
        if terminate_params:
            break
        await asyncio.sleep(0.01)
    assert terminate_params == [{"processId": start_params["processId"]}]
    assert client._process_start_gate._active == 0
    await client.disconnect()


@pytest.mark.asyncio
async def test_aborted_start_pre_signal_sends_nothing():
    """预触发信号：门控准入段举旗点快失败——start 不上线、不占在飞计数"""
    transport = HangableStartTransport(
        {"initialize": INIT_OK, "process/start": {"processId": "p-1"}}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    controller = AbortController()
    controller.abort()
    with pytest.raises(AbortedError):
        await client.process.start(["true"], "/tmp", signal=controller.signal)
    assert not any(m == "process/start" for m, _, _ in transport.requests)
    assert client._process_start_gate._active == 0
    await client.disconnect()


# ---------------------------------------------------------------------------
# recovery 层：恢复等待可被 signal 打断
# ---------------------------------------------------------------------------


class GatedConnectTransport(FakeTransport):
    """connect 挂起在闸门上的假传输（把恢复编排钉在 recovering 态用）"""

    def __init__(self, gate: asyncio.Event, responses=None):
        super().__init__(responses)
        self._gate = gate

    async def connect(self) -> None:
        await self._gate.wait()
        self.connected = True


@pytest.mark.asyncio
async def test_signal_interrupts_recovery_wait():
    """恢复等待（_await_transport 的 recovering 分支）中 signal →
    AbortedError 直通；恢复编排不受扰——取消的是调用方等待：闸门放行后
    恢复照常完成，后续调用可用"""
    gate = asyncio.Event()
    gate.set()  # 首连放行；断线后恢复重建的候选挂在闸门上
    sink: list[FakeTransport] = []

    async def handshake(transport: FakeTransport, resume_session_id: str | None):
        result = await transport.send_request(
            "initialize",
            {"clientName": "test", "resumeSessionId": resume_session_id},
        )
        response = InitializeResponse.model_validate(result)
        await transport.send_notification("initialized", {})
        return response

    def factory() -> FakeTransport:
        transport = GatedConnectTransport(
            gate,
            {
                "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
                "environment/info": {"environmentId": "env-1"},
            },
        )
        sink.append(transport)
        return transport

    managed = ManagedTransport(
        factory,
        handshake=handshake,
        strategy=ReconnectStrategy(interval=0.01, timeout=2.0),
    )
    await managed.connect()

    gate.clear()  # 恢复重建的候选连接挂起——recovering 态被钉住
    sink[0].drop("test drop")
    for _ in range(100):
        if managed.state == "recovering":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "recovering"

    controller = AbortController()
    call = asyncio.create_task(
        managed.send_request("environment/info", signal=controller.signal)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    controller.abort()
    with pytest.raises(AbortedError):
        await call
    assert managed.state == "recovering"  # 取消的是调用方等待，恢复不受扰

    # 闸门放行：恢复照常完成，后续调用落在新对映代连接上
    gate.set()
    for _ in range(100):
        if managed.state == "connected":
            break
        await asyncio.sleep(0.01)
    assert managed.state == "connected"
    result = await managed.send_request("environment/info")
    assert result == {"environmentId": "env-1"}
    await managed.disconnect()


# ---------------------------------------------------------------------------
# http_request：流式消费中取消（bodyDelta queue.get 竞速）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_request_stream_consume_aborted_unregisters():
    """http 流式响应体消费中 signal → AbortedError；requestId 路由注销
    （对位 Rust HttpBodyStreamRegistration::drop 收摊纪律）"""
    transport = FakeTransport(
        {
            "initialize": INIT_OK,
            "http/request": {"status": 200, "headers": [], "bodyBase64": ""},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    controller = AbortController()
    call = asyncio.create_task(
        client.http_request(
            "GET",
            "https://example.test/x",
            stream_response=True,
            signal=controller.signal,
        )
    )
    for _ in range(100):
        if any(m == "http/request" for m, _, _ in transport.requests):
            break
        await asyncio.sleep(0.01)
    # 响应落定后进入 bodyDelta 消费等待（queue 空——竞速挂在 queue.get 上）
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    controller.abort()
    with pytest.raises(AbortedError):
        await call
    assert client._router._http_streams == {}  # 路由注销不残留
    await client.disconnect()


# ---------------------------------------------------------------------------
# 探活族：预触发快失败
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_pre_aborted_sends_nothing():
    """探活（force_environment_info）预触发 signal → AbortedError，不发请求、
    不触发连接退役（取消是用户语义，非连接死因）"""
    transport = FakeTransport({"initialize": INIT_OK})
    client = ExecutorClient(transport=transport)
    await client.connect()

    controller = AbortController()
    controller.abort()
    with pytest.raises(AbortedError):
        await client.force_environment_info(signal=controller.signal)
    assert not any(m == "environment/info" for m, _, _ in transport.requests)
    assert client._control.state == "connected"  # 取消不退役连接
    await client.disconnect()
