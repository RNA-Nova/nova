"""会话树节点类型。"""

from typing import List, Optional

from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.session.entries import SessionEntry
from pydantic import Field


class SessionTreeNode(NovaBaseModel):
    """会话树节点"""

    entry: SessionEntry
    children: List["SessionTreeNode"] = Field(default_factory=list)
    label: Optional[str] = None
    label_timestamp: Optional[str] = None


# 由于 SessionTreeNode 存在自引用，需要重建模型图
SessionTreeNode.model_rebuild()


__all__ = ["SessionTreeNode"]
