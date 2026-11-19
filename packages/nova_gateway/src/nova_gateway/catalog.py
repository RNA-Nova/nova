"""公共目录发布物的读取、表示级条件请求与账户信息隔离。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from typing import Any, Optional

from nova_gateway_protocol import CatalogManifest

_FULL_TYPES = frozenset({"chat", "image", "classifier"})


@dataclass(frozen=True, kw_only=True)
class CatalogResult:
    status: int
    body: Any = None
    etag: Optional[str] = None
    last_modified: Optional[int] = None


def _body_etag(body: Any) -> str:
    content = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CatalogPublisher:
    """只读发布器；绝不读取 ModelsManager 的账户个性化目录。"""

    def __init__(self, catalog_dir: str | Path) -> None:
        self.catalog_dir = Path(catalog_dir)

    @property
    def manifest_path(self) -> Path:
        return self.catalog_dir / "catalog-manifest.json"

    def _manifest(self) -> Optional[CatalogManifest]:
        try:
            return CatalogManifest.model_validate_json(
                self.manifest_path.read_text("utf-8")
            )
        except (OSError, ValueError):
            return None

    def providers(self) -> list[str]:
        manifest = self._manifest()
        return manifest.providers if manifest is not None else []

    def _entry_file(self, provider: str, typed: bool) -> Path:
        suffix = ".all.json" if typed else ".json"
        return self.catalog_dir / "providers" / f"{provider}{suffix}"

    def get(
        self,
        provider: str,
        *,
        types: tuple[str, ...] = ("chat", "image", "classifier"),
        validator: Optional[str] = None,
    ) -> CatalogResult:
        manifest = self._manifest()
        if manifest is None or provider not in manifest.providers:
            return CatalogResult(status=404)
        typed = types != ("chat",)
        path = self._entry_file(provider, typed)
        relative = path.relative_to(self.catalog_dir).as_posix()
        file_etag = manifest.files.get(relative)
        if file_etag is None or not path.is_file():
            return CatalogResult(status=404)

        requested = frozenset(types)
        filtered = typed and requested != _FULL_TYPES
        body = None
        if filtered:
            entries = json.loads(path.read_text("utf-8"))
            body = [entry for entry in entries if entry.get("type") in requested]
            etag = _body_etag(body)
        else:
            etag = file_etag
        if validator is not None and validator == etag:
            return CatalogResult(status=304, etag=etag)

        if body is None:
            body = json.loads(path.read_text("utf-8"))
        generated_at = datetime.fromisoformat(manifest.generated_at)
        last_modified = int(generated_at.timestamp() * 1000)
        return CatalogResult(
            status=200,
            body=body,
            etag=etag,
            last_modified=last_modified,
        )


def http_date(timestamp_ms: int) -> str:
    return formatdate(timestamp_ms / 1000, usegmt=True)


__all__ = ["CatalogPublisher", "CatalogResult", "http_date"]
