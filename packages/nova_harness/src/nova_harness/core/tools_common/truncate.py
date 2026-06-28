"""文本/输出截断辅助函数。"""

from typing import List, Tuple

# 与 pi 参考实现对齐的默认截断阈值
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB
DEFAULT_MAX_OUTPUT_CHARS = 10000


def truncate_lines(
    lines: List[str],
    max_lines: int = DEFAULT_MAX_LINES,
) -> Tuple[List[str], bool]:
    """按行数截断，返回 (截断后的行, 是否发生过截断)。"""
    if len(lines) <= max_lines:
        return lines, False
    return lines[:max_lines], True


def truncate_bytes(
    text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    encoding: str = "utf-8",
) -> Tuple[str, bool]:
    """按字节数截断文本，尽量在换行处截断。"""
    encoded = text.encode(encoding)
    if len(encoded) <= max_bytes:
        return text, False

    # 先按 max_bytes 截断字节，再解码回文本
    truncated_bytes = encoded[:max_bytes]
    # 避免截断到多字节字符中间：从末尾向前找完整字符边界
    while truncated_bytes:
        try:
            truncated_text = truncated_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            truncated_bytes = truncated_bytes[:-1]
    else:
        truncated_text = ""

    # 尽量在最后一个换行处截断，保持行完整
    last_nl = truncated_text.rfind("\n")
    if last_nl > 0:
        truncated_text = truncated_text[: last_nl + 1]

    return truncated_text, True


def truncate_output(
    text: str,
    max_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> Tuple[str, bool]:
    """按字符数截断输出，保留头部和尾部。"""
    if len(text) <= max_chars:
        return text, False

    head_len = max_chars // 2
    tail_len = max_chars - head_len - 100  # 留一点空间给提示语
    if tail_len < 100:
        tail_len = 100

    head = text[:head_len]
    tail = text[-tail_len:]
    truncated = f"{head}\n\n...（中间省略 {len(text) - head_len - tail_len} 个字符）...\n\n{tail}"
    return truncated, True


def trim_trailing_empty_lines(lines: List[str]) -> List[str]:
    """去掉末尾空行。"""
    end = len(lines)
    while end > 0 and lines[end - 1] == "":
        end -= 1
    return lines[:end]
