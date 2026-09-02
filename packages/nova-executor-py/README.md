# nova-executor-py

`nova-executor-py` 是 `nova-executor` 的 Python SDK，让 Python 开发者能够方便地连接和使用远程执行服务。

## 定位

- **只做连接**：负责 WebSocket 连接、JSON-RPC 调用、进程/文件系统/PTY 管理
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
2. 启动服务：`nova-executor --listen ws://0.0.0.0:8080 --auth bearer --auth-token your-token`
3. 使用 SDK 连接

## License

MIT
