"""项目上下文文件加载。

加载顺序：
- 先扫描全局 ``<agent_dir>/AGENTS.md`` / ``<agent_dir>/CLAUDE.md``
- 再从 ``cwd`` 向上遍历到 git 仓库根目录，扫描祖先目录中的上下文文件
- 最终顺序：全局优先，然后祖先由远及近，最后 ``cwd``
"""

import os
from pathlib import Path
from typing import List, Optional

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.context_files import ContextFile

CONTEXT_FILE_NAMES = {"AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"}


def find_git_root(start_dir: str) -> Optional[Path]:
    """查找 *start_dir* 所在 git 仓库的根目录。

    从 *start_dir* 向上遍历，直到遇到包含 ``.git`` 的目录或到达文件系统根。
    """
    path = Path(start_dir).resolve()
    root = Path(os.path.abspath(os.sep))

    while True:
        if (path / ".git").exists():
            return path
        if path == root:
            return None
        parent = path.parent
        if parent == path:
            return None
        path = parent


def _find_context_file_in_dir(directory: str) -> Optional[str]:
    """在单个目录中查找第一个存在的上下文文件，返回其路径。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return None
    for name in CONTEXT_FILE_NAMES:
        candidate = path / name
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _read_context_file(path: str) -> Optional[str]:
    """读取上下文文件内容，失败时返回 None。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_project_context_files(
    cwd: str,
    agent_dir: str,
    *,
    stop_at_git_root: bool = True,
) -> List[ContextFile]:
    """
    加载项目上下文文件。

    顺序：
    1. 全局 ``agent_dir`` 中的上下文文件
    2. 从 ``cwd`` 向上到 git 仓库根目录（或文件系统根目录）的祖先目录中的上下文文件

    Args:
        cwd: 当前工作目录。
        agent_dir: 全局 agent 配置目录。
        stop_at_git_root: 为 True 时遇到 git 仓库根目录即停止向上遍历。
    """
    resolved_cwd = Path(cwd).resolve()
    resolved_agent_dir = Path(agent_dir).resolve()

    files: List[ContextFile] = []
    seen_paths: set = set()

    # 1. 全局 agent_dir
    global_path = _find_context_file_in_dir(str(resolved_agent_dir))
    if global_path:
        content = _read_context_file(global_path)
        if content is not None:
            files.append(
                ContextFile(
                    path=global_path,
                    content=content,
                    source_info=SourceInfo(
                        path=global_path,
                        source="local",
                        scope="user",
                        origin="top-level",
                        base_dir=str(resolved_agent_dir),
                    ),
                )
            )
            seen_paths.add(str(Path(global_path).resolve()))

    # 2. 从 cwd 向上收集祖先目录，默认在 git root 处停止
    ancestor_paths: List[str] = []
    current = resolved_cwd
    git_root = find_git_root(str(resolved_cwd)) if stop_at_git_root else None
    root = Path(os.path.abspath(os.sep))

    while True:
        file_path = _find_context_file_in_dir(str(current))
        if file_path:
            resolved_file = str(Path(file_path).resolve())
            if resolved_file not in seen_paths:
                ancestor_paths.append(file_path)
                seen_paths.add(resolved_file)

        if git_root is not None and current == git_root:
            break
        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 祖先路径此时是 cwd -> ... -> git_root，需要反转为由远到近
    for file_path in reversed(ancestor_paths):
        content = _read_context_file(file_path)
        if content is not None:
            files.append(
                ContextFile(
                    path=file_path,
                    content=content,
                    source_info=SourceInfo(
                        path=file_path,
                        source="local",
                        scope="project",
                        origin="top-level",
                        base_dir=str(Path(file_path).parent),
                    ),
                )
            )

    return files


__all__ = [
    "load_project_context_files",
    "find_git_root",
]
