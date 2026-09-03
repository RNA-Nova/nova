"""项目上下文文件加载测试。

我们的设计：**git root 封顶**（不越出项目读祖先目录）+ **trust 门控**
（项目不被信任时项目链不读；全局 agent_dir 是用户级配置不受门控）。
顺序：全局 -> 项目链由远及近（git root → cwd）。
"""

from pathlib import Path

import pytest
from nova_harness.core.resources.loaders.context_files import (
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


def test_context_files_stop_at_git_root(context_dirs: dict[str, Path]) -> None:
    """git root 封顶：项目外祖先目录的上下文文件不收集。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent  # tmp/project（git root）
    (project / ".git").mkdir(parents=True, exist_ok=True)

    (tmp / "AGENTS.md").write_text("outside", encoding="utf-8")  # 项目外
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")
    (agent_dir / "AGENTS.md").write_text("global", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), project_trusted=True)
    contents = [f.content for f in files]

    assert "global" in contents
    assert "project" in contents
    assert "cwd" in contents
    assert "outside" not in contents  # git root 之外不读


def test_context_files_order_global_then_far_to_near(
    context_dirs: dict[str, Path],
) -> None:
    """结果顺序：全局 -> 项目链由远及近（git root → cwd）。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)

    (agent_dir / "AGENTS.md").write_text("global", encoding="utf-8")
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), project_trusted=True)
    assert [f.content for f in files] == ["global", "project", "cwd"]


def test_context_files_untrusted_project_only_global(
    context_dirs: dict[str, Path],
) -> None:
    """trust 门控：项目不被信任时只读全局 agent_dir，项目链不读。"""
    tmp = context_dirs["tmp"]
    cwd = context_dirs["cwd"]
    agent_dir = context_dirs["agent_dir"]

    project = cwd.parent.parent
    (project / ".git").mkdir(parents=True, exist_ok=True)
    (project / "AGENTS.md").write_text("project", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")
    (agent_dir / "AGENTS.md").write_text("global", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), project_trusted=False)
    assert [f.content for f in files] == ["global"]


def test_context_files_no_git_root_reads_cwd_only(tmp_path: Path) -> None:
    """无 git root：只读 cwd（不向文件系统根上溯）。"""
    cwd = tmp_path / "plain" / "dir"
    agent_dir = tmp_path / "agent"
    cwd.mkdir(parents=True)
    agent_dir.mkdir(parents=True)

    (tmp_path / "AGENTS.md").write_text("ancestor", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd", encoding="utf-8")

    files = load_project_context_files(str(cwd), str(agent_dir), project_trusted=True)
    assert [f.content for f in files] == ["cwd"]


def test_context_files_dedupes_global_and_project(tmp_path: Path) -> None:
    """agent_dir 位于项目链上时，同一文件不重复出现。"""
    project = tmp_path / "repo"
    cwd = project / "pkg"
    cwd.mkdir(parents=True)
    (project / ".git").mkdir(exist_ok=True)

    (project / "AGENTS.md").write_text("shared", encoding="utf-8")

    # agent_dir 即 git root——全局与项目链命中同一文件
    files = load_project_context_files(str(cwd), str(project), project_trusted=True)
    matching = [f for f in files if f.content == "shared"]
    assert len(matching) == 1
    assert matching[0].source_info.scope == "user"  # 全局优先，项目链去重


def test_context_files_empty_when_none_exist(context_dirs: dict[str, Path]) -> None:
    """没有任何上下文文件时返回空列表。"""
    files = load_project_context_files(
        str(context_dirs["cwd"]), str(context_dirs["agent_dir"]), project_trusted=True
    )
    assert files == []
