"""项目上下文文件类型。"""

from typing import Optional

from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.extensions import SourceInfo


class ContextFile(NovaBaseModel):
    """一个项目上下文文件（如 ``AGENTS.md`` / ``CLAUDE.md``）。"""

    path: str
    content: str
    source_info: Optional[SourceInfo] = None


__all__ = ["ContextFile"]
