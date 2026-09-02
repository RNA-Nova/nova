"""目录布局迁移测试（前后端分治 §9——``core/config/migration.py``）。

语义钉板：
- 旧位散养资源目录（``<base>/extensions`` 等四类）整体搬入 ``<base>/backend/``；
- mv 语义：旧位不留副本；
- 幂等：二次运行零副作用；
- 新位已有内容则不搬（不合并不覆盖），返回诊断消息；
- agents 两半共享不动；user/project 两级 base 各自迁移。
"""

from pathlib import Path

import pytest
from nova_harness.core.config.migration import (
    MIGRATED_RESOURCE_DIR_NAMES,
    migrate_backend_layout,
    migrate_backend_resource_dirs,
)


def _write_old_resource(base: Path, name: str, marker: str = "x") -> Path:
    """在旧位写一个资源目录（含一个内容文件）。"""
    target = base / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "entry.md").write_text(marker, encoding="utf-8")
    return target


def test_migrates_all_four_resource_dirs(tmp_path: Path) -> None:
    base = tmp_path / "agent"
    for name in MIGRATED_RESOURCE_DIR_NAMES:
        _write_old_resource(base, name)

    messages = migrate_backend_resource_dirs(base)

    assert len(messages) == 4
    for name in MIGRATED_RESOURCE_DIR_NAMES:
        assert not (base / name).exists()  # mv 语义：旧位不留副本
        assert (base / "backend" / name / "entry.md").read_text() == "x"


def test_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    base = tmp_path / "agent"
    _write_old_resource(base, "skills")

    first = migrate_backend_resource_dirs(base)
    second = migrate_backend_resource_dirs(base)

    assert len(first) == 1
    assert second == []  # 幂等：没有可搬的旧位
    assert (base / "backend" / "skills" / "entry.md").exists()


def test_no_legacy_dirs_is_noop(tmp_path: Path) -> None:
    base = tmp_path / "agent"
    base.mkdir()

    assert migrate_backend_resource_dirs(base) == []
    assert not (base / "backend").exists()  # 零副作用：不创建空 backend/


def test_conflict_not_merged_not_overwritten(tmp_path: Path) -> None:
    """新位已有内容：不搬、不合并不覆盖，返回诊断消息，旧位原样保留。"""
    base = tmp_path / "agent"
    _write_old_resource(base, "prompts", marker="old")
    new_prompts = base / "backend" / "prompts"
    new_prompts.mkdir(parents=True)
    (new_prompts / "entry.md").write_text("new", encoding="utf-8")

    messages = migrate_backend_resource_dirs(base)

    assert len(messages) == 1
    assert "不合并不覆盖" in messages[0]
    assert (base / "prompts" / "entry.md").read_text() == "old"  # 旧位原样保留
    assert (new_prompts / "entry.md").read_text() == "new"  # 新位未被覆盖


def test_agents_dir_untouched(tmp_path: Path) -> None:
    """agents 两半共享平级保留，不参与迁移。"""
    base = tmp_path / "agent"
    agents = base / "agents"
    agents.mkdir(parents=True)
    (agents / "coding.yaml").write_text("description: x\n", encoding="utf-8")

    assert migrate_backend_resource_dirs(base) == []
    assert (base / "agents" / "coding.yaml").exists()
    assert not (base / "backend" / "agents").exists()


def test_migrate_backend_layout_covers_user_and_project(tmp_path: Path) -> None:
    """user（agent_dir）与 project（<cwd>/.nova）两级 base 各自迁移。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    _write_old_resource(agent_dir, "extensions")
    _write_old_resource(cwd / ".nova", "personas")

    messages = migrate_backend_layout(cwd=cwd, agent_dir=agent_dir)

    assert len(messages) == 2
    assert (agent_dir / "backend" / "extensions" / "entry.md").exists()
    assert (cwd / ".nova" / "backend" / "personas" / "entry.md").exists()
    assert not (agent_dir / "extensions").exists()
    assert not (cwd / ".nova" / "personas").exists()
