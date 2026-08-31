"""Nova 工具共享辅助模块。

供各 bundle 的 tool executor 内部使用，避免重复实现路径解析、文件队列、
输出截断/累加、shell 解析等通用逻辑。
"""

from nova_coding_agent.tools_common.file_queue import with_file_write_lock
from nova_coding_agent.tools_common.path_utils import resolve_path

__all__ = [
    "with_file_write_lock",
    "resolve_path",
]
