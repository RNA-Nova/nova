"""tools_common/streams 的无上限行读取测试。

asyncio ``StreamReader.readline`` 的 64KB 单行上限（"Separator is found,
but chunk is longer than limit"）曾让子进程大消息帧炸掉整条读取链——
本模块的 read_lines 按块切行，无此限。
"""

import asyncio

from nova_coding_agent.tools_common.streams import read_lines


def _reader_of(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _collect(data: bytes) -> list[str]:
    async def _run() -> list[str]:
        return [line async for line in read_lines(_reader_of(data))]

    return asyncio.run(_run())


def test_line_longer_than_64k_limit():
    """超 64KB 单行：readline 抛 ValueError 的场景，read_lines 正常产出。"""
    big = "x" * 200_000
    assert _collect(f"{big}\n".encode()) == [big]


def test_lines_split_across_chunk_boundaries():
    """跨块边界的行完整拼接（块大小 64KB，构造跨块行 + 多行混合）。"""
    long_line = "y" * 70_000  # 跨 64KB 块边界
    assert _collect(f"a\n{long_line}\nb\n".encode()) == ["a", long_line, "b"]


def test_crlf_and_final_partial_line():
    """CRLF 去 \\r；EOF 无换行的残余行照常产出；纯空白残余不产出。"""
    assert _collect(b"one\r\ntwo\r\n") == ["one", "two"]
    assert _collect(b"tail-no-newline") == ["tail-no-newline"]
    assert _collect(b"a\n\r\n") == ["a", ""]
    assert _collect(b"a\n ") == ["a"]


def test_empty_lines_preserved():
    """空行照常产出（消费方自行跳过），与 readline 循环语义一致。"""
    assert _collect(b"a\n\nb\n") == ["a", "", "b"]
