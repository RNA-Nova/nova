# nova-executor-client

`nova-executor-client` 是 `nova-executor` 的 Python SDK——**executor 栈的客户端运行时**（对位 codex `codex-exec-server` crate 的客户端半边）。

## 定位

- **连接与协议**：WebSocket / stdio 连接、JSON-RPC 调用（initialize 时做 `protocolVersion` major 匹配）、进程/文件系统/PTY 管理、断线重连与会话恢复、多连接通道分离
- **发现与物化**：读取 executor 配置根（user 层 `~/.nova/executor/config.toml` + 项目层 `<cwd>/.nova/settings.json` 的 `executor` 段，trust 门控），把套餐档/网络代理/审批档**物化展开**成线上协议对象——套餐名与配置词汇永不上线
- **不做部署**：不负责上传、安装、启动 nova-executor 到服务器
- **不做裁决交互**：网络/命令裁决只返回结果（allow/deny/ask），弹窗回路归调用方（如 nova 的 bundle 扩展）
- **通用**：不绑定 Nova，可用于任何 Python 项目

## 安装

```sh
pip install nova-executor-client
```

## 快速开始

```python
import asyncio
from nova_executor_client import ExecutorClient

async def main():
    async with ExecutorClient("ws://localhost:8080", token="your-token") as client:
        # 执行命令
        handle = await client.process.start(
            argv=["echo", "hello"],
            cwd="file:///tmp",
        )
        output = await handle.read()
        print(b"".join(output.chunks).decode())

        # 读取文件
        content = await client.fs.read_file("file:///tmp/test.txt")
        print(content.decode())

asyncio.run(main())
```

## 传输方式

### WebSocket（默认）

```python
client = ExecutorClient("ws://localhost:8080", token="your-token")
```

### stdio（本地 / SSH 远程同一实现）

spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC。command 参数化（program + args + env + cwd），本地与 SSH 远程只是 command 不同：

```python
# 本地：等价于 spawn ["nova-executor", "--listen", "stdio"]
async with ExecutorClient.from_stdio() as client:
    ...

# SSH 远程：spawn ["ssh", "user@host", "nova-executor", "--listen", "stdio"]
async with ExecutorClient.from_stdio(
    program="ssh",
    args=["user@host", "nova-executor", "--listen", "stdio"],
) as client:
    ...

# 完全自定义命令用 StdioTransport 直传
from nova_executor_client import StdioTransport
client = ExecutorClient(transport=StdioTransport(program="/path/to/nova-executor"))
```

注意：stdio 模式下服务端进程随连接存活，`disconnect()` 会先关 stdin 让服务端自行退出（宽限 2s 后 terminate/kill 兜底）；进程异常退出会传播为 `ConnectionError`。

## 多连接（控制面/数据面分离）

大文件流式传输（`read_stream` / `write_stream`）默认与其他调用共享同一连接。传 `connections=2` 让数据面方法走独立的第二条连接，不阻塞控制面（LLM 工具调用）：

```python
# 两条 WS 连接：fs/readStream、fs/writeStream* 走数据面，其余走控制面
async with ExecutorClient("ws://localhost:8080", token="t", connections=2) as client:
    ...

# stdio 同理：spawn 两条命令
async with ExecutorClient.from_stdio(connections=2) as client:
    ...
```

`connections=1`（默认）即现状行为。更复杂的通道布局可直接构造 `TransportPool`（按通道持有传输 + 方法名路由表，`TransportPool(channels={"control": t0, "bulk": t1}, method_routes={"fs/walk": "bulk"})`）。注意服务端句柄状态随连接存活，故每通道一条连接，不做通道内轮询。

## 配置与物化（executor 配置根）

执行策略词汇（沙箱套餐/网络代理/审批档）归 executor 栈自持，不进任何 agent
框架的 settings。SDK 负责发现并**物化展开**成协议对象——executor daemon
只收展开结果，不理解配置词汇。

配置层栈（对位 PROTOCOL v1.4 `environmentConfig/read` 层栈）：

- **user 层**：`~/.nova/executor/config.toml`（TOML；`NOVA_EXECUTOR_HOME` 可覆盖根）
- **project 层**：`<cwd>/.nova/settings.json` 的 `executor` 段（JSON；**仅在
  `project_trusted=True` 时读取**——Project Trust 裁决归调用方）

```toml
# ~/.nova/executor/config.toml
sandbox_mode = "workspace-write"        # 或 "read-only"；缺席 = 不沙箱
approval_policy = "on-request"          # on-request / on-failure / never

[sandbox_workspace_write]               # 微调旋钮（对位 codex 同名件）
writable_roots = ["/data"]
network_access = false                  # 网络默认受限；放行归 network_proxy 名单
exclude_tmpdir_env_var = false
exclude_slash_tmp = false

[network_proxy]
enabled = true
mode = "proxy"                          # 经代理按名单放行
allowed_domains = ["*.example.com"]
denied_domains = ["evil.example.com"]   # 展开时 deny 在前（拒绝优先）
```

```python
from nova_executor_client import load_executor_config, resolve_execution_policy

config = load_executor_config(cwd="/path/to/project", project_trusted=True)
policy = resolve_execution_policy(config, cwd="/path/to/project")
# policy.sandbox / policy.network_proxy 即为可直接下发 process/start 的展开对象
# （套餐名永不上线）；policy.approval_policy 供裁决方做 ask 降级判断
```

合并语义（对位 codex `merge.rs`）：表按键深合并，列表与标量由高层整体覆盖
（不追加）。未知键 warn-and-ignore；坏文件抛 `ConfigError`（带路径）。

## 网络沙箱裁决（policyRequest 回调底座）

executor 托管代理对**未列名**主机发起 `network/policyRequest` 反向裁决
（静态 allow/deny 名单由服务端按 `networkProxy` 配置自行评估）。SDK 提供
可复用裁决门——会话记忆 + ask 注入点 + fail-closed 兜底，UI 弹窗归调用方：

```python
from nova_executor_client import ExecutorClient, NetworkPolicyGate, AskOutcome

async def my_ui_ask(params) -> AskOutcome:   # 调用方实现交互（弹窗等）
    host = params.request.host
    ...  # AskOutcome.ALLOW / DENY / ALLOW_REMEMBER / DENY_REMEMBER

gate = NetworkPolicyGate(on_ask=my_ui_ask)   # approval_policy=never 或无 on_ask
client = ExecutorClient(url, network_policy=gate.decide)  # 时一律 deny（fail-closed）
```

审计通知（executor 每次裁决单向汇报）经类型化糖 API 订阅：

```python
async for event in client.on_policy_decision():   # 可按 process_id 过滤
    print(event.host, event.decision, event.reason)
```

## 多 executor（环境注册表）

executor 环境（执行机）注册表对位 codex `environments.toml`——词汇合并在
同一 `config.toml` 的 `[[environments]]`：

```toml
default_environment = "dev-box"   # "none" 禁用默认；缺席按 include_local 落 local
include_local = true              # 内建 local 环境（本机 stdio 缺省 spawn）

[[environments]]
id = "dev-box"
program = "ssh"                   # stdio spawn（SSH 承载同一形态）
args = ["user@host", "nova-executor", "--listen", "stdio"]
connect_timeout_sec = 5

[[environments]]
id = "server"
url = "wss://example.internal:8443"
```

```python
from nova_executor_client import load_executor_config, resolve_environment, ExecutorClient

config = load_executor_config()
env = resolve_environment(config)            # 默认解析链；或 resolve_environment(config, "server")
async with ExecutorClient.from_environment(env) as client:
    ...
```

校验（对位 codex）：`url`/`program` 二选一、url 必须 ws(s)://、id 唯一且 ≤64
字符、`default_environment` 必须已注册或为 "none"。选择/切换编排归调用方。

需要同时持有多台 executor 的活连接时，用管理器（对位 codex
`EnvironmentManager`：懒创建 + 缓存 + 状态观察 + 清扫）：

```python
from nova_executor_client import EnvironmentManager

manager = EnvironmentManager(config)             # network_policy=gate.decide 可传入
client = await manager.get_client("dev-box")     # 不传名走默认解析链
manager.status("dev-box")                        # connected / disconnected / pending
await manager.upsert_environment(...)            # 运行时增删（写回配置归调用方）
await manager.remove_environment("dev-box")
await manager.close_all()
```

## 断线重连与会话恢复

默认开启：连接意外断开后，SDK 按 `ReconnectStrategy` 自动重连并在 `initialize`
携带 `resumeSessionId` 恢复会话——进程表/输出缓冲/流式句柄随服务端会话存活
（服务端保留窗 30s），恢复期间调用挂起等待、恢复成功后透明续用。

```python
from nova_executor_client import ExecutorClient, ReconnectStrategy

# 默认策略（对齐 Rust 客户端）：100ms 固定间隔、25s 恢复总时限、时限内不限次
async with ExecutorClient("ws://localhost:8080", token="t") as client:
    ...

# 自定义：0.5s 起 2 倍退避、封顶 5s、最多 10 次
client = ExecutorClient(
    "ws://localhost:8080", token="t",
    reconnect=ReconnectStrategy(interval=0.5, backoff=2.0, max_interval=5.0, max_attempts=10),
)

# 关闭恢复：断线即失败（所有调用抛 ConnectionError）
client = ExecutorClient("ws://localhost:8080", token="t", reconnect=None)

# 显式恢复既有会话（首连即带 resumeSessionId）
client = ExecutorClient("ws://localhost:8080", token="t", resume_session_id="<session-id>")
```

注意：

- 每条连接各自恢复各自的会话（`connections=2` 时控制面/数据面互不影响）。
- resume 命中未知/过期会话（-32600）或会话仍附着他处以外的协议错误时不可
  重试，立即转失败；恢复失败后在途读流消费者收到 `FileSystemError`。
- stdio 传输重连即重 spawn——新服务端进程没有旧会话，resume 一次失败即转
  失败；该路径保证调用方拿到明确断线错误而非干等（WS 长驻服务端才是 resume
  的真正受益者）。
- `client.transport` 兼容属性在重连后自动指向新底层传输实例。

## 核心功能

### 进程管理

```python
# 启动进程
handle = await client.process.start(
    argv=["ls", "-la"],
    cwd="file:///home/user/project",
    env={"PATH": "/usr/bin"},
)

# 读取输出
output = await handle.read(wait_ms=1000)

# 写入 stdin
await handle.write(b"input\n")

# 终止进程
await handle.terminate()

# 等待退出
exit_code = await handle.wait()
```

### 文件系统

```python
# 小文件读取
content = await client.fs.read_file("file:///tmp/test.txt")

# 大文件流式读取（done 收尾校验：服务端报错/字节收不齐都会抛 FileSystemError；
# 消费过慢触发背压断流同样报错，不静默截断）
async for chunk in client.fs.read_stream("file:///tmp/large.log"):
    process(chunk)

# 写入文件
await client.fs.write_file("file:///tmp/test.txt", b"content")

# 大文件流式写入（分片推，服务端按 seq 严格序落盘，返回总字节数）
total = await client.fs.write_stream(
    "file:///tmp/large.bin",
    chunk_source,          # 同步/异步字节迭代器，自动按 block_size 切块
    block_size=256 * 1024,
)
# 中断不产生可见文件：本地异常自动中止（服务端删除半截文件），
# 服务端错误（乱序/超限/写盘失败）在收尾时以 FileSystemError 抛出

# 列出目录
entries = await client.fs.read_dir("file:///tmp")

# 创建目录
await client.fs.create_dir("file:///tmp/new-dir", recursive=True)

# 删除
await client.fs.remove("file:///tmp/test.txt")

# 复制
await client.fs.copy("file:///tmp/a.txt", "file:///tmp/b.txt")

# 元数据
meta = await client.fs.metadata("file:///tmp/test.txt")
```

### PTY

```python
# 启动交互式 shell
pty = await client.pty.spawn(
    argv=["bash"],
    cwd="file:///tmp",
    env={"TERM": "xterm", "PATH": "/usr/bin"},
)

# 写入命令
await pty.write(b"echo hello\n")

# 读取输出
async for chunk in pty.read():
    print(chunk.decode())
```

## 错误处理

```python
from nova_executor_client import (
    ExecutorError,
    ConnectionError,
    AuthError,
    ProcessError,
    FileSystemError,
    TimeoutError,
    ProtocolError,
)

try:
    await client.connect()
except AuthError:
    print("认证失败")
except ConnectionError:
    print("连接失败")
```

`ProtocolError` 结构化携带线上 `error.code`（`exc.code: int | None`），可按码
分流；码表常量对位 Rust 服务端 rpc.rs：

```python
from nova_executor_client import (
    ProtocolError,
    JSON_RPC_INVALID_REQUEST,    # -32600 无效请求（含 resume 未知/过期会话）
    JSON_RPC_METHOD_NOT_FOUND,   # -32601 方法不存在
    JSON_RPC_INVALID_PARAMS,     # -32602 参数无效
    JSON_RPC_INTERNAL_ERROR,     # -32603 服务端内部错误
    EXECUTOR_NOT_FOUND,          # -32004 资源不存在
    SESSION_ALREADY_ATTACHED,    # -32010 会话仍附着在别的连接上
)

try:
    await client.fs.read_file("file:///missing")
except ProtocolError as e:
    if e.code == EXECUTOR_NOT_FOUND:
        ...
```

## 部署说明

本 SDK **不负责部署** nova-executor。你需要：

1. 在服务器上编译或下载 `nova-executor` 二进制
2. 启动服务：`nova-executor --listen ws://0.0.0.0:8080 --auth bearer --auth-token your-token`（stdio 模式为 `nova-executor --listen stdio`，单连接，由 SDK 直接 spawn 时无需手动启动）
3. 使用 SDK 连接

## 测试

```sh
# 单元测试（假传输 / 假子进程，不需要真实服务端）
pytest tests -m "not integration"

# stdio 集成测试：spawn 真实二进制（定位顺序：NOVA_EXECUTOR_BIN 环境变量 →
# 仓库 target/debug/nova-executor → PATH；找不到自动 skip）
pytest tests/test_stdio_integration.py

# WebSocket 集成测试（需先启动 ws 服务端，见上节）
pytest tests/test_integration.py
```

## License

MIT
