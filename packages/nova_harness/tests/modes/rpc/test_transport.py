"""
StdioTransport 单元测试。

覆盖 NDJSON 写入与读取行为；open() 涉及真实 stdin，不在本文件中测试。
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from nova_harness.modes.rpc.transport import StdioTransport


class TestStdioTransport:
    """StdioTransport 行为测试。"""

    def test_write_outputs_valid_json_line(self):
        """write 应将字典序列化为单行 JSON 并刷新 stdout。"""
        transport = StdioTransport()
        fake_stdout = MagicMock()
        obj = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

        with patch("nova_harness.modes.rpc.transport.sys.stdout", fake_stdout):
            transport.write(obj)

        fake_stdout.write.assert_called_once_with(
            json.dumps(obj, ensure_ascii=False) + "\n"
        )
        fake_stdout.flush.assert_called_once()

    def test_write_swallows_exception(self):
        """write 在 stdout 异常时不应抛出。"""
        transport = StdioTransport()
        fake_stdout = MagicMock()
        fake_stdout.write.side_effect = OSError("broken pipe")

        with patch("nova_harness.modes.rpc.transport.sys.stdout", fake_stdout):
            transport.write({"hello": "world"})

        fake_stdout.write.assert_called_once()

    async def test_readline_raises_when_not_opened(self):
        """_reader 为 None 时 readline 应抛出 RuntimeError。"""
        transport = StdioTransport()
        with pytest.raises(RuntimeError, match="Transport not opened"):
            await transport.readline()

    async def test_readline_returns_line(self):
        """readline 应解码 StreamReader 中的一行内容。"""
        transport = StdioTransport()
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"method":"init"}\n')
        transport._reader = reader

        line = await transport.readline()
        assert line == '{"method":"init"}'

    async def test_readline_returns_none_on_eof(self):
        """StreamReader 到达 EOF 时 readline 应返回 None。"""
        transport = StdioTransport()
        reader = asyncio.StreamReader()
        reader.feed_eof()
        transport._reader = reader

        line = await transport.readline()
        assert line is None

    async def test_readline_strips_whitespace(self):
        """readline 应去除首尾空白字符。"""
        transport = StdioTransport()
        reader = asyncio.StreamReader()
        reader.feed_data(b'  {"id": 42}  \r\n')
        transport._reader = reader

        line = await transport.readline()
        assert line == '{"id": 42}'
