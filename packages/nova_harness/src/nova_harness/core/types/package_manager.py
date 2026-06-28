"""包管理器类型。

对应原 `nova_harness.package_manager.types`。
"""

from typing import Any, Dict, List

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class InstalledItem(NovaBaseModel):
    """A single agent config or tool installed as part of a bundle."""

    kind: str
    name: str
    path: str

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "path": self.path}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstalledItem":
        return cls(
            kind=data.get("kind", ""),
            name=data.get("name", ""),
            path=data.get("path", ""),
        )


class PackageMetadata(NovaBaseModel):
    """Metadata for an installed agent config, tool, or bundle package."""

    name: str
    version: str
    description: str
    kind: str  # "agent" | "tool" | "bundle"
    source: str
    install_path: str
    installed_at: str
    author: str = ""
    dependencies: List[str] = Field(default_factory=list)
    installed_dependencies: List[str] = Field(default_factory=list)
    installed_items: List[InstalledItem] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "kind": self.kind,
            "source": self.source,
            "install_path": self.install_path,
            "installed_at": self.installed_at,
            "author": self.author,
            "dependencies": self.dependencies,
            "installed_dependencies": self.installed_dependencies,
            "installed_items": [item.model_dump() for item in self.installed_items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PackageMetadata":
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            kind=data.get("kind", ""),
            source=data.get("source", ""),
            install_path=data.get("install_path", ""),
            installed_at=data.get("installed_at", ""),
            author=data.get("author", ""),
            dependencies=data.get("dependencies", []),
            installed_dependencies=data.get("installed_dependencies", []),
            installed_items=[
                InstalledItem.model_validate(i) for i in data.get("installed_items", [])
            ],
        )


class BundleView(NovaBaseModel):
    """A grouped view of a bundle with its agents, tools, and skills."""

    name: str
    version: str
    description: str
    agents: List[PackageMetadata]
    tools: List[PackageMetadata]
    skills: List[PackageMetadata]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "agents": [a.model_dump() for a in self.agents],
            "tools": [t.model_dump() for t in self.tools],
            "skills": [s.model_dump() for s in self.skills],
        }


__all__ = [
    "InstalledItem",
    "PackageMetadata",
    "BundleView",
]
