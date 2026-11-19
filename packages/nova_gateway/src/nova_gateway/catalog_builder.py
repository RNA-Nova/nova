"""调用 nova_catalog 构建公共 JSON 目录并原子提交。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from nova_ai.model_catalog import finalize_catalog_models
from nova_catalog.build import ProviderCatalog, build_catalog
from nova_catalog.define import ProviderDefinition
from nova_catalog.providers import PROVIDERS
from nova_catalog.sources.load import CatalogSources, load_catalog_sources
from nova_gateway_protocol import CatalogManifest
from nova_protocol import AnyModel

CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class PublicCatalog:
    """验收后的公共目录（三操作分群，不允许原地改写）。"""

    chat: tuple[AnyModel, ...]
    images: tuple[AnyModel, ...]
    classifiers: tuple[AnyModel, ...]


def _json(value: Any, *, pretty: bool = False) -> str:
    return (
        json.dumps(
            value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True
        )
        + "\n"
    )


def _entry(model: AnyModel) -> dict[str, Any]:
    payload = model.dump_wire(exclude_none=True)
    if "inputTypes" in payload:
        payload["input"] = payload.pop("inputTypes")
    if "outputTypes" in payload:
        payload["output"] = payload.pop("outputTypes")
    return payload


def _entries(catalog: PublicCatalog) -> list[dict[str, Any]]:
    return [
        _entry(model)
        for model in [*catalog.chat, *catalog.images, *catalog.classifiers]
    ]


def _chat_entries(catalog: PublicCatalog) -> dict[str, Any]:
    return {model.id: _entry(model) for model in catalog.chat}


def _build_documents(
    built: Dict[str, PublicCatalog], *, pretty: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    chat_providers: Dict[str, Any] = {}
    all_providers: Dict[str, Any] = {}
    provider_files: Dict[str, str] = {}
    for provider_id, catalog in sorted(built.items()):
        if not (catalog.chat or catalog.images or catalog.classifiers):
            continue
        chat_providers[provider_id] = _chat_entries(catalog)
        all_providers[provider_id] = _entries(catalog)
        provider_files[f"providers/{provider_id}.json"] = _json(
            chat_providers[provider_id], pretty=pretty
        )
        provider_files[f"providers/{provider_id}.all.json"] = _json(
            all_providers[provider_id], pretty=pretty
        )
    return chat_providers, all_providers, provider_files


def _commit(output_dir: Path, documents: Dict[str, str]) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".catalog-stage-", dir=output_dir.parent))
    backup = output_dir.with_name(output_dir.name + ".previous")
    has_backup = False
    try:
        for relative, content in documents.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if backup.exists():
            shutil.rmtree(backup)
        if output_dir.exists():
            os.replace(output_dir, backup)
            has_backup = True
        os.replace(stage, output_dir)
        if has_backup:
            shutil.rmtree(backup)
    except BaseException:
        if has_backup:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            os.replace(backup, output_dir)
        elif stage.exists():
            shutil.rmtree(stage)
        raise


def generate_catalog(
    output_dir: Path,
    *,
    strict: bool = True,
    pretty: bool = False,
    provider_definitions: Optional[Sequence[ProviderDefinition]] = None,
    sources: Optional[CatalogSources] = None,
) -> CatalogManifest:
    providers = sorted(
        PROVIDERS if provider_definitions is None else list(provider_definitions),
        key=lambda provider: provider.id,
    )
    if sources is None:
        sources = asyncio.run(load_catalog_sources(strict=strict))
    built = build_catalog(providers, sources, strict=strict)
    built = {
        provider_id: catalog
        for provider_id, catalog in built.items()
        if catalog.chat or catalog.images or catalog.classifiers
    }
    # finalize_catalog_models 与运行时共用同一身份盖戳和 Pydantic 验收。
    finalized: Dict[str, PublicCatalog] = {}
    for provider_id, catalog in built.items():
        finalized[provider_id] = PublicCatalog(
            chat=tuple(finalize_catalog_models(provider_id, None, catalog.chat)),
            images=tuple(finalize_catalog_models(provider_id, None, catalog.images)),
            classifiers=tuple(
                finalize_catalog_models(provider_id, None, catalog.classifiers)
            ),
        )
    chat_providers, all_providers, provider_files = _build_documents(
        finalized, pretty=pretty
    )
    documents: Dict[str, str] = {
        "models.json": _json(chat_providers, pretty=pretty),
        "models.all.json": _json(all_providers, pretty=pretty),
        "providers.json": _json(sorted(finalized), pretty=pretty),
        **provider_files,
    }
    manifest = CatalogManifest(
        schema_version=CATALOG_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        providers=sorted(finalized),
        files={
            relative: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative, content in documents.items()
        },
    )
    documents["catalog-manifest.json"] = _json(manifest.dump_wire(), pretty=True)
    _commit(output_dir, documents)
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Nova Gateway 公共模型目录")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    manifest = generate_catalog(args.output_dir, strict=True, pretty=args.pretty)
    print(f"Generated public catalog for {len(manifest.providers)} providers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
