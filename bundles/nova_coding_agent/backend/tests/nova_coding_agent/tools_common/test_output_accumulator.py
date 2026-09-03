"""tools_common/output_accumulator.py 单元测试。"""

import os

from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
)


def test_accumulator_no_truncation():
    acc = OutputAccumulator()
    acc.append(b"line1\nline2\nline3")
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.content == "line1\nline2\nline3"
    assert snapshot.truncation.truncated is False
    assert snapshot.full_output_path is None
    acc.close_temp_file()


def test_accumulator_tail_truncation_by_lines():
    acc = OutputAccumulator(OutputAccumulatorOptions(max_lines=2))
    acc.append(b"\n".join(f"line{i}".encode() for i in range(5)))
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.truncation.truncated is True
    assert snapshot.truncation.truncated_by == "lines"
    assert snapshot.content == "line3\nline4"
    acc.close_temp_file()


def test_accumulator_tail_truncation_by_bytes():
    acc = OutputAccumulator(OutputAccumulatorOptions(max_bytes=12))
    acc.append(b"hello world\nsecond line\n")
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.truncation.truncated is True
    assert snapshot.content == "second line"
    acc.close_temp_file()


def test_accumulator_temp_file_created_when_truncated():
    acc = OutputAccumulator(OutputAccumulatorOptions(max_bytes=5))
    acc.append(b"hello world\nsecond line\n")
    acc.finish()
    snapshot = acc.snapshot(persist_if_truncated=True)
    assert snapshot.full_output_path is not None
    assert os.path.exists(snapshot.full_output_path)
    with open(snapshot.full_output_path, "rb") as f:
        full = f.read()
    assert full == b"hello world\nsecond line\n"
    acc.close_temp_file()
    os.unlink(snapshot.full_output_path)


def test_accumulator_append_after_finish_raises():
    acc = OutputAccumulator()
    acc.append(b"hello")
    acc.finish()
    try:
        acc.append(b"world")
        assert False, "should raise"
    except RuntimeError:
        pass
    acc.close_temp_file()


def test_accumulator_multibyte_utf8():
    acc = OutputAccumulator()
    acc.append("中".encode("utf-8"))
    acc.append("文\n".encode("utf-8"))
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.content == "中文\n"
    assert snapshot.truncation.total_lines == 1
    acc.close_temp_file()


def test_accumulator_last_line_bytes():
    acc = OutputAccumulator()
    acc.append(b"hello world")
    assert acc.get_last_line_bytes() == len("hello world".encode("utf-8"))
    acc.finish()
    acc.close_temp_file()


def test_accumulator_multibyte_char_split_across_chunks():
    """多字节字符被拆到两个 chunk 时不得产生乱码（流式增量解码）。

    "你" = E4 BD A0（3 字节）；拆成 E4 BD / A0 两个 chunk 追加，
    逐 chunk 解码会产生两个 U+FFFD，增量解码应还原为一个"你"。
    """
    acc = OutputAccumulator()
    acc.append("你".encode("utf-8")[:2])  # E4 BD（不完整）
    acc.append("你".encode("utf-8")[2:])  # A0
    acc.append("好\n".encode("utf-8"))
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.content == "你好\n"
    assert "\ufffd" not in snapshot.content  # 无替换字符
    acc.close_temp_file()


def test_accumulator_emoji_split_across_chunks():
    """4 字节 emoji 跨 chunk 同样正确拼接。"""
    emoji = "🎉".encode("utf-8")  # F0 9F 8E 89
    acc = OutputAccumulator()
    acc.append(emoji[:1])
    acc.append(emoji[1:3])
    acc.append(emoji[3:] + b"\n")
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.content == "🎉\n"
    assert "\ufffd" not in snapshot.content
    acc.close_temp_file()


def test_accumulator_incomplete_tail_flushed_as_replacement():
    """finish 时仍不完整的尾巴按 replace 输出（不丢数据）。"""
    acc = OutputAccumulator()
    acc.append(b"abc")
    acc.append("你".encode("utf-8")[:2])  # E4 BD 无后续
    acc.finish()
    snapshot = acc.snapshot()
    assert snapshot.content.startswith("abc")
    assert "\ufffd" in snapshot.content  # 不完整尾巴变替换字符
    acc.close_temp_file()
