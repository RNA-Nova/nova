# nova-executor-py

`nova-executor-py` 是 `nova-executor` 的 Python SDK，让 Python 开发者能够方便地连接和使用远程执行服务。

## 定位

- **只做连接**：负责 WebSocket / stdio 连接、JSON-RPC 调用、进程/文件系统/PTY 管理
- **不做部署**：不负责上传、安装、启动 nova-executor 到服务器
- **通用**：不绑定 Nova，可用于任何 Python 项目

## 安装

```sh
pip install nova-executor
```

## 快速开始

```python
import asyncio
from nova_executor import ExecutorClient

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
from nova_executor import StdioTransport
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

# 大文件流式读取
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
from nova_executor import (
    ExecutorError,
    ConnectionError,
    AuthError,
    ProcessError,
    FileSystemError,
    TimeoutError,
)

try:
    await client.connect()
except AuthError:
    print("认证失败")
except ConnectionError:
    print("连接失败")
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
