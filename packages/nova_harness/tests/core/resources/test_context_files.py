"""项目上下文文件加载测试。"""

from pathlib import Path

import pytest

from nova_harness.core.resources.loaders.context_files import (
    find_git_root,
    load_project_context_files,
)


@pytest.fixture
def context_dirs(tmp_path: Path) -> dict[str, Path]:
    """构造包含全局/项目/祖先上下文文件的目录结构。"""
    cwd = tmp_path / "project" / "src" / "app"
    agent_dir = tmp_path / "agent"
    cwd.mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    return {"cwd": cwd, "agent_dir": agent_dir, "tmp": tmp_path}


def test_load_project_context_files_default_stops_at_git_root(
    context_dirs: dict[str, Path],
) -> None:
    """默认行为停在 git root。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)

    outside = project.parent
    (outside / "AGENTS.md").write_text("outside", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")
    (agent_dir / "AGENTS.md").write_text("global", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir))
    contents = [f.content for f in files]

    assert "global" in contents
    assert "project" in contents
    assert "cwd" in contents
    assert "outside" not in contents


def test_load_project_context_files_order(
    context_dirs: dict[str, Path],
) -> None:
    """结果顺序：全局 -> 祖先由远及近 -> cwd。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)

    (agent_dir / "AGENTS.md").write_text("global", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir))
    contents = [f.content for f in files]

    assert contents == ["global", "project", "cwd"]


def test_load_project_context_files_can_traverse_to_root(
    context_dirs: dict[str, Path],
) -> None:
    """显式 stop_at_git_root=False 时遍历到文件系统根。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)

    outside = project.parent
    (outside / "AGENTS.md").write_text("outside", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), stop_at_git_root=False)
    paths = {f.path.lower() for f in files}

    assert str((project / "AGENTS.md").resolve()).lower() in paths
    assert str((outside / "AGENTS.md").resolve()).lower() in paths


def test_load_project_context_files_stop_at_git_root_explicit(
    context_dirs: dict[str, Path],
) -> None:
    """显式 stop_at_git_root=True 时停在 git root。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)

    outside = project.parent
    (outside / "AGENTS.md").write_text("outside", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), stop_at_git_root=True)
    paths = {f.path.lower() for f in files}

    assert str((project / "AGENTS.md").resolve()).lower() in paths
    assert str((outside / "AGENTS.md").resolve()).lower() not in paths


def test_find_git_root_returns_none_outside_repo(
    context_dirs: dict[str, Path],
) -> None:
    """在普通目录中查找 git root 应返回 None。"""
    assert find_git_root(str(context_dirs["cwd"])) is None
