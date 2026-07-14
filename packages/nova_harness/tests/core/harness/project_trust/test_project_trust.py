"""Project Trust 单元测试。"""

from pathlib import Path

import pytest

from nova_harness.core.harness.project_trust import (
    ProjectTrustStore,
    has_trust_requiring_project_resources,
    resolve_project_trusted,
)
from nova_harness.core.types.project_trust import ResolveProjectTrustedOptions


def test_has_trust_requiring_project_resources_detects_settings(tmp_path):
    """检测到 .nova/settings.json 时返回 True。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    assert has_trust_requiring_project_resources(str(project_dir)) is True


def test_has_trust_requiring_project_resources_empty(tmp_path):
    """没有 .nova 目录时返回 False。"""
    assert has_trust_requiring_project_resources(str(tmp_path)) is False


def test_trust_store_round_trip(tmp_path):
    """TrustStore 读写与查找最近父目录。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set("/a/b/c", True)
    store.set("/a/b", False)

    assert store.get("/a/b/c") is True
    assert store.get("/a/b/d") is False  # 向上找到 /a/b
    assert store.get("/a") is None


def test_trust_store_set_many_and_remove(tmp_path):
    """set_many 支持更新和删除记录。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set("/a/b", True)

    class Update:
        def __init__(self, path, decision):
            self.path = path
            self.decision = decision

    store.set_many([Update("/a/b", None)])
    assert store.get("/a/b") is None


@pytest.mark.asyncio
async def test_resolve_project_trusted_no_resources(tmp_path):
    """无项目资源时始终信任。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(tmp_path),
            trust_store=store,
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_override(tmp_path):
    """trust_override 优先级最高。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            trust_override=True,
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_saved_decision(tmp_path):
    """已保存的信任记录生效。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set(str(project_dir), False)

    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
        )
    )
    assert trusted is False


@pytest.mark.asyncio
async def test_resolve_project_trusted_no_ui_defaults_to_false(tmp_path):
    """无 UI 时存在项目资源默认不信任（与 TS 对齐）。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="ask",
        )
    )
    assert trusted is False


@pytest.mark.asyncio
async def test_resolve_project_trusted_default_always(tmp_path):
    """default_project_trust=always 时直接信任。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="always",
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_default_never(tmp_path):
    """default_project_trust=never 时直接不信任。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="never",
        )
    )
    assert trusted is False


def test_has_trust_requiring_project_resources_agents_skills_empty(tmp_path):
    """空的 .agents/skills 目录也会触发信任检查。"""
    agents_skills = tmp_path / "project" / ".agents" / "skills"
    agents_skills.mkdir(parents=True)

    assert has_trust_requiring_project_resources(str(tmp_path / "project")) is True


def test_has_trust_requiring_project_resources_excludes_home_agents_skills(
    monkeypatch, tmp_path
):
    """~/.agents/skills 不触发信任检查。"""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    agents_skills = home / ".agents" / "skills"
    agents_skills.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: home)
    assert has_trust_requiring_project_resources(str(home)) is False
