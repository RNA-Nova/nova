"""项目上下文文件类型。"""

from typing import Optional

from nova_protocol import NovaBaseModel

from nova_harness.resources.personas import SourceInfo


class ContextFile(NovaBaseModel):
    """一个项目上下文文件（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""

    path: str
    content: str
    source_info: Optional[SourceInfo] = None


__all__ = ["ContextFile"]
