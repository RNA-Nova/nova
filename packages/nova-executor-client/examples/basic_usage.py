"""基本使用示例"""

import asyncio

from nova_executor_client import ExecutorClient


async def main():
    # 连接 executor
    async with ExecutorClient("ws://localhost:8080", token="your-token") as client:
        # 获取环境信息
        info = await client.environment_info()
        print(f"Shell: {info.shell.name}, CWD: {info.cwd}")

        # 执行命令
        handle = await client.process.start(
            argv=["echo", "hello from nova-executor"],
            cwd="file:///tmp",
        )
        output = await handle.read(wait_ms=2000)
        print(f"Output: {b''.join(output.chunks).decode()}")

        # 写入文件
        await client.fs.write_file("file:///tmp/test.txt", b"test content\n")

        # 读取文件
        content = await client.fs.read_file("file:///tmp/test.txt")
        print(f"File content: {content.decode()}")

        # 流式读取大文件
        async for chunk in client.fs.read_stream("file:///tmp/large.log"):
            print(f"Chunk: {len(chunk)} bytes")

        # 清理
        await client.fs.remove("file:///tmp/test.txt")


if __name__ == "__main__":
    asyncio.run(main())
