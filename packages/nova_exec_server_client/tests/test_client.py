"""客户端基础测试"""

import asyncio

import pytest
from fake_transport import FakeTransport

from nova_exec_server_client import (
    NETWORK_POLICY_DECISION,
    ExecutorClient,
    ProtocolError,
    ReconnectStrategy,
    TimeoutError,
    TransportError,
)
from nova_exec_server_client import client as client_module

ENVIRONMENT_INFO_PAYLOAD = {
    "shell": {"name": "zsh", "path": "/bin/zsh"},
    "cwd": "file:///Users/test",
    "userHomeDir": "file:///Users/test",
    "platformOs": "macos",
    "tempDir": "file:///tmp",
}

FAST = ReconnectStrategy(interval=0.01, timeout=2.0)


@pytest.mark.asyncio
async def test_client_creation():
    """测试客户端创建"""
    client = ExecutorClient("ws://localhost:8080", token="test")
    assert client.transport.url == "ws://localhost:8080"
    assert client.transport.token == "test"


@pytest.mark.asyncio
async def test_client_no_token():
    """测试无 token 客户端"""
    client = ExecutorClient("ws://localhost:8080")
    assert client.transport.token is None


@pytest.mark.asyncio
async def test_managers_exist():
    """测试管理器存在"""
    client = ExecutorClient("ws://localhost:8080")
    assert client.process is not None
    assert client.fs is not None
    assert client.pty is not None


@pytest.mark.asyncio
async def test_environment_info_piggybacked_by_initialize_is_cached():
    """initialize 捎带 environmentInfo：environment_info() 零额外请求"""
    transport = FakeTransport(
        {
            "initialize": {
                "sessionId": "fake-session",
                "protocolVersion": "1.0",
                "environmentInfo": ENVIRONMENT_INFO_PAYLOAD,
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    info = await client.environment_info()
    again = await client.environment_info()

    assert info.user_home_dir == "file:///Users/test"
    assert info.platform_os == "macos"
    assert again is info
    methods = [m for m, _, _ in transport.requests]
    assert "environment/info" not in methods
    await client.disconnect()


@pytest.mark.asyncio
async def test_environment_info_legacy_server_falls_back_once_and_caches():
    """旧服务端未捎带：首个调用回退一次 environment/info，此后读缓存"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.0"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    info = await client.environment_info()
    again = await client.environment_info()

    assert info.user_home_dir == "file:///Users/test"
    assert again is info
    methods = [m for m, _, _ in transport.requests]
    assert methods.count("environment/info") == 1
    await client.disconnect()


@pytest.mark.asyncio
async def test_read_environment_config():
    """read_environment_config 发 environmentConfig/read 并解析层栈响应"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "environmentConfig/read": {
                "userHomeDir": "file:///home/u",
                "executorHomeDir": "file:///home/u/.nova/exec-server",
                "hostname": "devbox",
                "config": {
                    "layers": [
                        {
                            "source": "user:/home/u/.nova/exec-server/config.toml",
                            "baseDir": "file:///home/u/.nova/exec-server",
                            "format": "toml",
                            "content": '[sandbox]\nlevel = "workspace-write"\n',
                        },
                        {
                            "source": "project:/repo/.nova/settings.json",
                            "baseDir": "file:///repo/.nova",
                            "format": "json",
                            "content": "{}",
                        },
                    ],
                    "cloudInsertionIndex": 2,
                },
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    response = await client.read_environment_config(
        "file:///repo", [["sandbox"], ["network", "mode"]]
    )

    requests = [
        (m, p) for m, p, _ in transport.requests if m == "environmentConfig/read"
    ]
    assert len(requests) == 1
    assert requests[0][1] == {
        "cwd": "file:///repo",
        "configPaths": [["sandbox"], ["network", "mode"]],
    }
    assert response.executor_home_dir == "file:///home/u/.nova/exec-server"
    assert [layer.format for layer in response.config.layers] == ["toml", "json"]
    assert response.config.layers[0].error is None
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_connects_fresh_session_and_retires_old():
    """refresh（对位 Rust refresh_connection）：全新会话不 resume + 退役旧传输"""
    made: list[FakeTransport] = []

    def factory() -> FakeTransport:
        transport = FakeTransport(
            {
                "initialize": {
                    "sessionId": f"session-{len(made)}",
                    "protocolVersion": "1.0",
                },
                "environment/status": {"status": "ready"},
            }
        )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory)
    await client.connect()
    assert client.session_id == "session-0"

    await client.refresh_connection()

    assert client.session_id == "session-1"
    init_params = [p for m, p, _ in made[1].requests if m == "initialize"][0]
    assert init_params.get("resumeSessionId") is None
    assert not made[0].is_connected
    assert made[1].is_connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_failure_leaves_disconnected_and_later_connect_retries():
    """refresh 失败不恢复旧会话：状态落 disconnected，后续 connect 全新重试"""

    class FailingTransport(FakeTransport):
        async def connect(self) -> None:
            raise ConnectionError("dial refused")

    made: list[FakeTransport] = []

    def factory() -> FakeTransport:
        if len(made) == 1:
            transport: FakeTransport = FailingTransport({})
        else:
            transport = FakeTransport(
                {
                    "initialize": {
                        "sessionId": f"session-{len(made)}",
                        "protocolVersion": "1.0",
                    },
                    "environment/status": {"status": "ready"},
                }
            )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory)
    await client.connect()
    assert client.session_id == "session-0"

    import pytest as _pytest

    with _pytest.raises(Exception):
        await client.refresh_connection()
    assert client._control.state == "disconnected"

    await client.connect()
    assert client.session_id == "session-2"
    await client.disconnect()


@pytest.mark.asyncio
async def test_force_environment_info_bypasses_cache():
    """force_environment_info（对位 rust 同名）：不读缓存、不写缓存——
    environment_info 依旧缓存，force 每次都发请求"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.0"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    await client.environment_info()
    await client.environment_info()
    cached_calls = [m for m, _, _ in transport.requests if m == "environment/info"]
    assert len(cached_calls) == 1

    await client.force_environment_info()
    await client.force_environment_info()
    forced_calls = [m for m, _, _ in transport.requests if m == "environment/info"]
    assert len(forced_calls) == 3  # 1 次缓存填充 + 2 次强制
    # 缓存未被强刷污染
    await client.environment_info()
    assert len([m for m, _, _ in transport.requests if m == "environment/info"]) == 3
    await client.disconnect()


class HangingEnvironmentInfoTransport(FakeTransport):
    """environment/info 永不应答的假传输（探活超时触发恢复测试用）"""

    async def send_request(
        self, method, params=None, *, channel=None, for_cleanup=False
    ):
        if method == "environment/info":
            await asyncio.sleep(60)
        return await super().send_request(method, params, channel=channel)


def make_probe_factory(sink: list[FakeTransport]):
    """首个传输 environment/info 不应答，重连件正常应答的传输工场"""

    def factory() -> FakeTransport:
        responses = {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
        transport = (
            HangingEnvironmentInfoTransport(responses)
            if not sink
            else FakeTransport(responses)
        )
        sink.append(transport)
        return transport

    return factory


@pytest.mark.asyncio
async def test_force_environment_info_timeout_retires_connection_and_recovers(
    monkeypatch,
):
    """对位上游 67352b5b4a：environment/info 超时 → 关闭所探连接并触发会话
    恢复，调用方拿超时错误（不重试失败请求）；恢复回到同一会话、客户端实例不变"""
    monkeypatch.setattr(client_module, "ENVIRONMENT_INFO_TIMEOUT", 0.05)
    sink: list[FakeTransport] = []
    client = ExecutorClient(transport_factory=make_probe_factory(sink), reconnect=FAST)
    await client.connect()

    with pytest.raises(TimeoutError, match="timed out"):
        await client.force_environment_info()

    # 探活超时的连接被客户端主动退役；按策略重连并 resume 同一会话
    for _ in range(100):
        if len(sink) == 2 and not sink[0].connected:
            break
        await asyncio.sleep(0.01)
    assert not sink[0].connected
    assert len(sink) == 2
    assert sink[1].requests[0] == (
        "initialize",
        {"clientName": "nova-exec-server-client", "resumeSessionId": "fake-session"},
        None,
    )
    assert client.session_id == "fake-session"

    # 恢复后探活走新连接照常
    info = await client.force_environment_info()
    assert info.platform_os == "macos"
    await client.disconnect()


@pytest.mark.asyncio
async def test_environment_info_cache_miss_timeout_also_recovers(monkeypatch):
    """缓存未命中的 environment_info() 走同一探活路径（对位 Rust
    get_or_try_init(force_environment_info)）：超时同样退役连接并恢复，
    恢复后缓存正常填充"""
    monkeypatch.setattr(client_module, "ENVIRONMENT_INFO_TIMEOUT", 0.05)
    sink: list[FakeTransport] = []
    client = ExecutorClient(transport_factory=make_probe_factory(sink), reconnect=FAST)
    await client.connect()

    with pytest.raises(TimeoutError, match="timed out"):
        await client.environment_info()
    assert client._environment_info is None  # 超时不写缓存

    for _ in range(100):
        if len(sink) == 2:
            break
        await asyncio.sleep(0.01)
    info = await client.environment_info()
    assert info.platform_os == "macos"
    again = await client.environment_info()
    assert again is info  # 恢复后读缓存，不重复请求
    await client.disconnect()


# =============================================================================
# 二轮审计修复族：双通道 resume 隔离 / 刷新原子性与缓存 / 握手会话校验 /
# 探针实发同钉 / 裁决订阅畸形容忍 / 准入存活检查 / cwd 归一 / http 终态真值
# =============================================================================


@pytest.mark.asyncio
async def test_connections_two_resume_only_control_plane():
    """connections=2 + resume_session_id：resume 只落控制面首连（服务端
    session_registry 拒绝同一会话的重复附着 -32010）；数据面开新会话"""
    sink: list[FakeTransport] = []

    def factory() -> FakeTransport:
        # 控制面如实回到被 resume 的会话；数据面新会话
        session_id = "s-9" if not sink else f"session-{len(sink)}"
        transport = FakeTransport(
            {"initialize": {"sessionId": session_id, "protocolVersion": "1.4"}}
        )
        sink.append(transport)
        return transport

    client = ExecutorClient(
        transport_factory=factory, connections=2, resume_session_id="s-9"
    )
    await client.connect()

    control_init = [p for m, p, _ in sink[0].requests if m == "initialize"][0]
    data_init = [p for m, p, _ in sink[1].requests if m == "initialize"][0]
    assert control_init["resumeSessionId"] == "s-9"
    assert "resumeSessionId" not in data_init
    assert client.session_id == "s-9"
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_failure_clears_environment_info_cache():
    """刷新起手即清元数据缓存（域2-A3）：首通道即失败时不留旧会话缓存值"""
    made: list[FakeTransport] = []

    class FailingTransport(FakeTransport):
        async def connect(self) -> None:
            raise ConnectionError("dial refused")

    def factory() -> FakeTransport:
        idx = len(made)
        transport: FakeTransport = (
            FailingTransport({})
            if idx == 2  # 控制面刷新候选连接失败
            else FakeTransport(
                {
                    "initialize": {
                        "sessionId": f"session-{idx}",
                        "protocolVersion": "1.0",
                        "environmentInfo": ENVIRONMENT_INFO_PAYLOAD,
                    },
                    "environment/status": {"status": "ready"},
                }
            )
        )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, connections=2)
    await client.connect()
    assert client._environment_info is not None  # initialize 捎带填充

    with pytest.raises(Exception):
        await client.refresh_connection()
    assert client._environment_info is None  # 旧会话缓存不留
    await client.disconnect()


@pytest.mark.asyncio
async def test_refresh_failure_retires_already_refreshed_channels():
    """双通道刷新非原子收口（域5-B2）：数据面失败时把已刷新的控制面一并
    退役——不留"控制面新会话 + 数据面断连"的分裂态"""
    made: list[FakeTransport] = []

    class FailingTransport(FakeTransport):
        async def connect(self) -> None:
            raise ConnectionError("dial refused")

    def factory() -> FakeTransport:
        idx = len(made)
        transport: FakeTransport = (
            FailingTransport({})
            if idx == 3  # 数据面刷新候选（控制面 2 / 数据面 3）连接失败
            else FakeTransport(
                {
                    "initialize": {
                        "sessionId": f"session-{idx}",
                        "protocolVersion": "1.0",
                    },
                    "environment/status": {"status": "ready"},
                }
            )
        )
        made.append(transport)
        return transport

    client = ExecutorClient(transport_factory=factory, connections=2)
    await client.connect()
    assert client._control.state == "connected"

    with pytest.raises(Exception, match="dial refused"):
        await client.refresh_connection()

    # 已刷新的控制面一并退役，整体落回 disconnected（后续 connect 全新拉起）
    assert client._control.state == "disconnected"
    assert not made[2].is_connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_handshake_resume_mismatch_rejected_before_initialized():
    """resume 会话一致性在 INITIALIZED 之前校验（对位 Rust initialize_rpc：
    session 检查先于 notify）——违约即拒，不发 initialized 通知；
    recovery._install 的同款检查保留兜底"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "other-session", "protocolVersion": "1.4"}}
    )
    client = ExecutorClient(transport=transport, resume_session_id="s-9")
    with pytest.raises(ProtocolError, match="unexpected session"):
        await client.connect()
    assert not any(m == "initialized" for m, _, _ in transport.notifications)
    await client.disconnect()


@pytest.mark.asyncio
async def test_force_environment_info_sends_on_pinned_transport():
    """探针钉与实发同一条传输（域3-B1，对位 Rust
    timeout(ENVIRONMENT_INFO_TIMEOUT, rpc_client.call(...))）——不经
    连接池二次解析，恒落控制面对映代连接"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.0"},
            "environment/info": ENVIRONMENT_INFO_PAYLOAD,
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    async def forbidden(method, params=None, *, channel=None, for_cleanup=False):
        raise AssertionError("probe must not re-resolve through the pool")

    client._pool.send_request = forbidden  # type: ignore[method-assign]
    info = await client.force_environment_info()
    assert info.platform_os == "macos"
    assert any(m == "environment/info" for m, _, _ in transport.requests)
    await client.disconnect()


@pytest.mark.asyncio
async def test_policy_decision_subscription_skips_malformed_and_fails_on_channel():
    """裁决审计订阅（域3-B5）：畸形通知丢弃不杀订阅（对位 Rust
    let Ok(params) = ... else 丢弃分支）；fail_channel 合成终态事件
    （{"error": message} 形态）→ TransportError 终止迭代而非干等"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    valid = {
        "processId": "p1",
        "timestamp": "2026-01-01T00:00:00Z",
        "scope": "process",
        "decision": "allow",
        "source": "proxy",
        "reason": "",
        "protocol": "https_connect",
        "host": "pypi.org",
        "port": 443,
    }
    gen = client.on_policy_decision()
    pending = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)  # 生成体起跑注册订阅队列后再分发
    await client._router.dispatch(
        {"method": NETWORK_POLICY_DECISION, "params": {"garbage": True}}
    )
    await client._router.dispatch({"method": NETWORK_POLICY_DECISION, "params": valid})
    first = await asyncio.wait_for(pending, 1)
    assert first.host == "pypi.org"  # 畸形被跳过，订阅存活

    client._router.fail_channel("control", "exec-server transport disconnected")
    with pytest.raises(TransportError, match="disconnected"):
        await gen.__anext__()
    await gen.aclose()
    await client.disconnect()


@pytest.mark.asyncio
async def test_try_begin_rejects_dead_transport():
    """准入查 is_connected（域3-B6，对位 begin_process_start 的
    expected.is_disconnected()）：底层已死而断线回调未至的窗口不予准入"""
    transport = FakeTransport(
        {"initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"}}
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    assert client._process_start_gate.try_begin(client.transport)
    client._process_start_gate.finish()

    transport.connected = False  # 底层已死、断线回调未至的窗口
    assert not client._process_start_gate.try_begin(client.transport)
    await client.disconnect()


@pytest.mark.asyncio
async def test_read_environment_config_normalizes_cwd_to_file_url():
    """cwd 上线前归一 PathUri（域5-B1）：原生绝对路径转 file:// scheme
    （保留集外字符百分号编码）；已是 file:// 前缀的原样透传"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "environmentConfig/read": {
                "userHomeDir": "file:///home/u",
                "executorHomeDir": "file:///home/u/.nova/exec-server",
                "hostname": "devbox",
                "config": {"layers": [], "cloudInsertionIndex": 0},
            },
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    await client.read_environment_config("/repo dir/配置", [["sandbox"]])
    await client.read_environment_config("file:///already", [["sandbox"]])
    params = [p for m, p, _ in transport.requests if m == "environmentConfig/read"]
    assert params[0]["cwd"] == "file:///repo%20dir/%E9%85%8D%E7%BD%AE"
    assert params[1]["cwd"] == "file:///already"
    await client.disconnect()


@pytest.mark.asyncio
async def test_http_stream_empty_error_string_is_terminal():
    """http body delta 终态真值判定（域5-C3）：error 字段在场即终态
    （is not None——空串也判错，与 fs 侧同形）"""
    transport = FakeTransport(
        {
            "initialize": {"sessionId": "fake-session", "protocolVersion": "1.4"},
            "http/request": {"status": 200, "headers": [], "bodyBase64": ""},
        }
    )
    client = ExecutorClient(transport=transport)
    await client.connect()

    task = asyncio.create_task(
        client.http_request(
            "GET",
            "http://example.com",
            stream_response=True,
            request_id="r-1",
        )
    )
    for _ in range(100):
        if "r-1" in client._router._http_streams:
            break
        await asyncio.sleep(0.01)
    queue = client._router._http_streams["r-1"].queue
    queue.put_nowait(
        {
            "method": "http/request/bodyDelta",
            "params": {
                "requestId": "r-1",
                "seq": 1,
                "deltaBase64": "aGk=",
                "done": False,
                "error": "",
            },
        }
    )
    with pytest.raises(ProtocolError, match="http body stream error"):
        await asyncio.wait_for(task, 1)
    await client.disconnect()
