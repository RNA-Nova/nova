"""公共目录生成、发布与匿名条件请求测试。"""

import json

import httpx
import pytest
from nova_catalog.define import define_provider, models_dev_models
from nova_catalog.sources.load import CatalogSources
from nova_gateway.app import create_app
from nova_gateway.catalog import CatalogPublisher
from nova_gateway.catalog_builder import generate_catalog
from nova_gateway.config import GatewayConfig
from nova_protocol import KnownApi

pytestmark = pytest.mark.asyncio


def _raw_model():
    return {
        "id": "model-a",
        "name": "Model A",
        "type": "chat",
        "capabilities": {
            "tools": {"supported": True},
            "reasoning": {"supported": True, "effort": ["low", "high"]},
        },
        "limit": {"context": 128000, "output": 8192},
        "cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}},
        "modalities": {"input": ["text"]},
    }


def _definition():
    return define_provider(
        id="test",
        models=lambda ctx: models_dev_models(
            ctx,
            "test",
            api=KnownApi.OPENAI_COMPLETIONS,
            base_url="https://upstream.test/v1",
        ),
    )


def _sources():
    return CatalogSources(models_dev={"test": {"models": {"model-a": _raw_model()}}})


async def test_generate_and_serve_public_catalog(tmp_path):
    manifest = generate_catalog(
        tmp_path, provider_definitions=[_definition()], sources=_sources()
    )
    assert manifest.providers == ["test"]
    assert (tmp_path / "models.json").is_file()
    assert (tmp_path / "models.all.json").is_file()
    assert (tmp_path / "providers.json").read_text() == '["test"]\n'
    typed = json.loads((tmp_path / "providers" / "test.all.json").read_text())
    assert typed[0]["id"] == "model-a"
    assert typed[0]["input"] == ["text"]
    assert "inputTypes" not in typed[0]
    assert typed[0]["contextWindow"] == 128000
    assert typed[0]["maxTokens"] == 8192
    manifest_on_disk = json.loads((tmp_path / "catalog-manifest.json").read_text())
    assert manifest_on_disk["providers"] == ["test"]
    assert manifest_on_disk["files"]["providers/test.all.json"]

    publisher = CatalogPublisher(tmp_path)
    result = publisher.get("test")
    assert result.status == 200
    assert result.etag is not None
    assert result.etag == manifest_on_disk["files"]["providers/test.all.json"]
    assert publisher.get("test", validator=result.etag).status == 304
    assert publisher.get("test", types=("chat",)).status == 200
    assert isinstance(publisher.get("test", types=("chat",)).body, dict)
    image_only = publisher.get("test", types=("image",))
    assert image_only.status == 200
    assert image_only.body == []
    assert image_only.etag != result.etag
    assert publisher.get("test", types=("image",), validator=result.etag).status == 200
    assert (
        publisher.get("test", types=("image",), validator=image_only.etag).status == 304
    )
    assert publisher.get("missing").status == 404

    app = create_app(GatewayConfig(catalog_dir=str(tmp_path), api_token="secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        providers = await client.get("/api/models/providers")
        assert providers.status_code == 200
        assert providers.json() == ["test"]
        response = await client.get("/api/models/providers/test")
        assert response.status_code == 200
        assert response.headers["etag"] == result.etag
        assert response.json()[0]["id"] == "model-a"
        not_modified = await client.get(
            "/api/models/providers/test", headers={"If-None-Match": result.etag}
        )
        assert not_modified.status_code == 304
        missing = await client.get("/api/models/providers/missing")
        assert missing.status_code == 404
        invalid = await client.get("/api/models/providers/test?types=embedding")
        assert invalid.status_code == 400
        protected = await client.get("/api/models")
        assert protected.status_code == 401


async def test_generate_catalog_validates_manifest_hashes(tmp_path):
    generate_catalog(tmp_path, provider_definitions=[_definition()], sources=_sources())
    manifest = json.loads((tmp_path / "catalog-manifest.json").read_text())
    for relative, expected_hash in manifest["files"].items():
        content = (tmp_path / relative).read_bytes()
        import hashlib

        assert hashlib.sha256(content).hexdigest() == expected_hash
