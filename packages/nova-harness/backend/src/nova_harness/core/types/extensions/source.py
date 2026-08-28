"""扩展/命令/工具来源信息类型。"""

from __future__ import annotations

from typing import Literal, Optional

from nova_ai.types.base_model import NovaBaseModel


class SourceInfo(NovaBaseModel):
    """扩展/工具/命令的来源描述。

    跨进程（RPC 透出）故用 Pydantic——线上 camelCase（baseDir）经
    alias_generator 自动产出。
    """

    path: str
    source: str = "extension"
    scope: Literal["user", "project", "temporary"] = "temporary"
    origin: Literal["package", "top-level", "local", "auto"] = "top-level"
    base_dir: Optional[str] = None


__all__ = ["SourceInfo"]
