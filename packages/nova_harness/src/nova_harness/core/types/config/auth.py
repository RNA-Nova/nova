"""鉴权相关类型。"""

from __future__ import annotations

from typing import Dict

from nova_ai.types.base_model import NovaBaseModel


class ApiKeyCredential(NovaBaseModel):
    """API key credential type."""

    type: str = "api_key"
    key: str

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for serialization."""
        return {"type": self.type, "key": self.key}

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ApiKeyCredential":
        """Create from dictionary."""
        if data.get("type") != "api_key":
            raise ValueError(f"Invalid credential type: {data.get('type')}")
        return cls(key=data["key"])


AuthStorageData = Dict[str, Dict[str, str]]
"""``auth.json`` 文件内容的类型别名。"""


__all__ = ["ApiKeyCredential", "AuthStorageData"]
