"""项目上下文文件类型。"""

from typing import Optional

from nova_harness.types.resources.personas import SourceInfo
from nova_protocol.base_model import NovaBaseModel


class ContextFile(NovaBaseModel):
    """一个项目上下文文件（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""

    path: str
    content: str
    source_info: Optional[SourceInfo] = None


__all__ = ["ContextFile"]
