"""Tests for package_manager/manifest.py."""

import json

from nova_harness.core.package.manifest import (
    read_manifest,
    read_requirements,
)


def test_read_legacy_manifest(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "legacy-pkg",
                "version": "1.2.3",
                "description": "old style",
                "author": "nova",
                "kind": "bundle",
                "dependencies": ["requests"],
            }
        ),
        encoding="utf-8",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.name == "legacy-pkg"
    assert manifest.version == "1.2.3"
    assert manifest.kind == "bundle"
    assert manifest.dependencies == ["requests"]
    assert manifest.nova is None


def test_read_modern_manifest(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "modern-pkg",
                "version": "2.0.0",
                "description": "new style",
                "author": "nova",
                "dependencies": {"requests": ">=2.0"},
                "nova": {
                    "agents": ["./agents/coding"],
                    "tools": ["./tools/bash"],
                    "auto_install_dependencies": False,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.name == "modern-pkg"
    assert manifest.nova is not None
    assert manifest.nova.agents == ["./agents/coding"]
    assert manifest.nova.tools == ["./tools/bash"]
    assert manifest.nova.auto_install_dependencies is False
    assert manifest.dependencies == ["requests>=2.0"]


def test_read_legacy_definitions_field(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "legacy-defs",
                "nova": {
                    "definitions": ["./defs/coding"],
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.nova is not None
    assert manifest.nova.agents == ["./defs/coding"]


def test_read_manifest_missing(tmp_path):
    manifest = read_manifest(str(tmp_path))
    assert manifest.name is None
    assert manifest.version == "0.0.0"


def test_read_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "requests>=2.0\n\n# comment\nhttpx\n",
        encoding="utf-8",
    )
    assert read_requirements(str(tmp_path)) == ["requests>=2.0", "httpx"]


def test_read_requirements_missing(tmp_path):
    assert read_requirements(str(tmp_path)) == []


def test_coerce_npm_dependencies(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "x",
                "dependencies": {"a": "1.0", "b": "*", "c": ""},
            }
        ),
        encoding="utf-8",
    )
    manifest = read_manifest(str(tmp_path))
    assert "a==1.0" in manifest.dependencies
    assert "b" in manifest.dependencies
    assert "c" in manifest.dependencies
