"""Nova 工具共享辅助模块。

供各 bundle 的 tool executor 内部使用，避免重复实现路径解析、截断、文件队列等通用逻辑。
"""

from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.output_accumulator import (
    OutputAccumulator,
    OutputAccumulatorOptions,
    OutputSnapshot,
)
from nova_coding_agent.tools_common.path_utils import is_path_traversal, resolve_path
from nova_coding_agent.tools_common.truncate import (
    TruncationOptions,
    TruncationResult,
    format_size,
    trim_trailing_empty_lines,
    truncate_head,
    truncate_line,
    truncate_lines,
    truncate_tail,
)

__all__ = [
    "with_file_write_lock",
    "OutputAccumulator",
    "OutputAccumulatorOptions",
    "OutputSnapshot",
    "resolve_path",
    "is_path_traversal",
    "TruncationOptions",
    "TruncationResult",
    "format_size",
    "truncate_head",
    "truncate_tail",
    "truncate_line",
    "truncate_lines",
    "trim_trailing_empty_lines",
]
