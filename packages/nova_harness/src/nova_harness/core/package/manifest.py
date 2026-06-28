"""Package manifest parsing.

A Nova package is declared via ``package.json``. The modern format uses a
``nova`` section to list agents/tools; legacy root-level fields and the old
``definitions`` key are still supported for backward compatibility.

Example manifest::

    {
      "name": "my-agent",
      "version": "1.0.0",
      "description": "...",
      "author": "...",
      "dependencies": ["requests>=2.0"],
      "nova": {
        "agents": ["./agents/coding_agent"],
        "tools": ["./tools/bash"],
        "auto_install_dependencies": true,
        "binary_dependencies": {
          "rg": "ripgrep",
          "fd": "fd-find"
        }
      }
    }
"""

import os
from typing import Any, Dict, List, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, model_validator


class NovaManifest(NovaBaseModel):
    """The ``nova`` section inside ``package.json``."""

    agents: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    auto_install_dependencies: bool = True
    binary_dependencies: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_definitions(cls, data: Any) -> Any:
        """Migrate legacy ``definitions`` to ``agents``."""
        if isinstance(data, dict) and "definitions" in data and "agents" not in data:
            data = dict(data)
            data["agents"] = data.pop("definitions")
        return data


class PackageManifest(NovaBaseModel):
    """Normalized view of a package manifest."""

    name: Optional[str] = None
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = Field(default_factory=list)
    kind: Optional[str] = None  # legacy / explicit: "agent" | "tool" | "bundle"
    nova: Optional[NovaManifest] = None


def load_json_file(path: str) -> Optional[Dict[str, Any]]:
    """Load a JSON file if it exists and is valid."""
    if not os.path.exists(path):
        return None
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_manifest(package_dir: str) -> PackageManifest:
    """Read and normalize ``package.json`` from *package_dir*.

    Falls back to legacy root-level fields when a ``nova`` section is absent.
    """
    data = load_json_file(os.path.join(package_dir, "package.json")) or {}

    nova_data = data.get("nova")
    if nova_data is None:
        return PackageManifest(
            name=data.get("name"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            dependencies=_coerce_dependencies(data.get("dependencies")),
            kind=_normalize_legacy_kind(data.get("kind")),
        )

    return PackageManifest(
        name=data.get("name"),
        version=data.get("version", "0.0.0"),
        description=data.get("description", ""),
        author=data.get("author", ""),
        dependencies=_coerce_dependencies(data.get("dependencies")),
        kind=_normalize_legacy_kind(data.get("kind")),
        nova=NovaManifest.model_validate(nova_data),
    )


def _normalize_legacy_kind(kind: Any) -> Optional[str]:
    """Map old package kind names to the new vocabulary."""
    if kind is None:
        return None
    if kind == "definition":
        return "agent"
    if kind == "agent":
        return "bundle"
    return str(kind)


def _coerce_dependencies(value: Any) -> List[str]:
    """Accept list or dict of dependencies and return a list of spec strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        result: List[str] = []
        for name, ver in value.items():
            ver_str = str(ver) if ver is not None else ""
            if not ver_str or ver_str == "*":
                result.append(str(name))
            elif ver_str[:2] in (">=", "<=", "==", "!=", "~=") or ver_str[0] in (
                ">",
                "<",
            ):
                result.append(f"{name}{ver_str}")
            else:
                result.append(f"{name}=={ver_str}")
        return result
    return []


def read_requirements(package_dir: str) -> List[str]:
    """Read ``requirements.txt`` lines, ignoring blanks and comments."""
    req_path = os.path.join(package_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return []
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except Exception:
        return []


__all__ = [
    "NovaManifest",
    "PackageManifest",
    "read_manifest",
    "read_requirements",
]
