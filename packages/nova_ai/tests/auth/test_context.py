"""AuthContext 测试。"""

import os
from pathlib import Path

import pytest

from nova_ai.auth.context import default_provider_auth_context


@pytest.mark.asyncio
async def test_default_auth_context_reads_env(monkeypatch):
    ctx = default_provider_auth_context()
    monkeypatch.setenv("TEST_NOVA_AUTH_CONTEXT", "value")
    assert await ctx.env("TEST_NOVA_AUTH_CONTEXT") == "value"


@pytest.mark.asyncio
async def test_default_auth_context_skips_empty_env(monkeypatch):
    ctx = default_provider_auth_context()
    monkeypatch.setenv("TEST_NOVA_AUTH_CONTEXT_EMPTY", "  ")
    assert await ctx.env("TEST_NOVA_AUTH_CONTEXT_EMPTY") is None


@pytest.mark.asyncio
async def test_default_auth_context_file_exists(tmp_path):
    ctx = default_provider_auth_context()
    file = tmp_path / "exists.txt"
    file.write_text("ok")
    assert await ctx.fileExists(str(file)) is True


@pytest.mark.asyncio
async def test_default_auth_context_file_not_exists(tmp_path):
    ctx = default_provider_auth_context()
    assert await ctx.fileExists(str(tmp_path / "missing.txt")) is False
