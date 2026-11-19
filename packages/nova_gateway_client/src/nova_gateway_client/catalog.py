"""公共目录客户端结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from nova_protocol import AnyModel


@dataclass(frozen=True, kw_only=True)
class CatalogResult:
    status: int
    models: List[AnyModel] = field(default_factory=list)
    etag: str | None = None
    last_modified: int | None = None


__all__ = ["CatalogResult"]
