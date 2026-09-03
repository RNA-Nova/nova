"""tools_common/truncate.py 单元测试。"""

from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationOptions,
    format_size,
    truncate_head,
    truncate_line,
    truncate_lines,
    truncate_tail,
)


def test_format_size_bytes():
    assert format_size(512) == "512B"


def test_format_size_kb():
    assert format_size(1536) == "1.5KB"


def test_format_size_mb():
    assert format_size(2 * 1024 * 1024) == "2.0MB"


def test_truncate_head_no_truncation():
    content = "line1\nline2\nline3"
    result = truncate_head(content)
    assert result.truncated is False
    assert result.content == content
    assert result.total_lines == 3


def test_truncate_head_by_lines():
    content = "\n".join(f"line{i}" for i in range(5))
    result = truncate_head(content, TruncationOptions(max_lines=2))
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.content == "line0\nline1"
    assert result.output_lines == 2


def test_truncate_head_by_bytes():
    content = "line0\nline1\nline2"
    result = truncate_head(content, TruncationOptions(max_bytes=8))
    assert result.truncated is True
    assert result.truncated_by == "bytes"
    assert result.content == "line0"


def test_truncate_head_first_line_exceeds():
    content = "a" * 100
    result = truncate_head(content, TruncationOptions(max_bytes=10))
    assert result.truncated is True
    assert result.first_line_exceeds_limit is True
    assert result.content == ""


def test_truncate_tail_no_truncation():
    content = "line1\nline2\nline3"
    result = truncate_tail(content)
    assert result.truncated is False
    assert result.content == content


def test_truncate_tail_by_lines():
    content = "\n".join(f"line{i}" for i in range(5))
    result = truncate_tail(content, TruncationOptions(max_lines=2))
    assert result.truncated is True
    assert result.truncated_by == "lines"
    assert result.content == "line3\nline4"


def test_truncate_tail_by_bytes():
    # "line2" 5 字节，"line1\nline2" 11 字节；限制 10 字节只能保留 line2
    content = "line0\nline1\nline2"
    result = truncate_tail(content, TruncationOptions(max_bytes=10))
    assert result.truncated is True
    assert result.truncated_by == "bytes"
    assert result.content == "line2"


def test_truncate_tail_partial_last_line():
    content = "a" * 100
    result = truncate_tail(content, TruncationOptions(max_lines=10, max_bytes=10))
    assert result.truncated is True
    assert result.last_line_partial is True
    assert len(result.content.encode("utf-8")) <= 10


def test_truncate_line_no_truncation():
    text, was_truncated = truncate_line("short")
    assert text == "short"
    assert was_truncated is False


def test_truncate_line_truncation():
    line = "a" * 600
    text, was_truncated = truncate_line(line, max_chars=500)
    assert was_truncated is True
    assert text.endswith("... [truncated]")
    assert len(text) == 500 + len("... [truncated]")


def test_truncate_lines():
    lines, truncated = truncate_lines(["a", "b", "c", "d"], max_lines=2)
    assert truncated is True
    assert lines == ["a", "b"]


def test_defaults():
    assert DEFAULT_MAX_LINES == 2000
    assert DEFAULT_MAX_BYTES == 50 * 1024


def test_truncate_utf8_bytes():
    content = "中\n文\n测"
    result = truncate_head(content, TruncationOptions(max_bytes=4))
    assert result.content == "中"
