"""项目上下文文件加载。

我们的设计（与 pi 不同）：
- **项目封顶**：从 ``cwd`` 向上遍历到**项目根（git root）为止**——不越出
  项目边界读祖先目录（pi 会一路读到文件系统根，把 ``~/AGENTS.md`` 这类
  仓库外文件也拼进系统提示，我们不）；无 git root 时只读 ``cwd``；
- **trust 门控**：项目链上的上下文文件（cwd 至 git root）在项目不被
  信任时不读——上下文文件会进入系统提示，不受信任即注入面；
  全局 ``<agent_dir>/AGENTS.md`` 是用户级配置，不受项目门控。

加载顺序：全局 agent_dir 优先，然后项目链由远及近（git root → cwd）。
"""

from pathlib import Path
from typing import List, Optional

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.utils.git import find_git_root

CONTEXT_FILE_NAMES = {"AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD"}


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


def _make_context_file(
    file_path: str, scope: str, base_dir: str
) -> Optional[ContextFile]:
    content = _read_context_file(file_path)
    if content is None:
        return None
    return ContextFile(
        path=file_path,
        content=content,
        source_info=SourceInfo(
            path=file_path,
            source="local",
            scope=scope,  # type: ignore[arg-type]
            origin="top-level",
            base_dir=base_dir,
        ),
    )


def load_project_context_files(
    cwd: str,
    agent_dir: str,
    project_trusted: bool,
) -> List[ContextFile]:
    """
    加载项目上下文文件。

    顺序：
    1. 全局 ``agent_dir`` 中的上下文文件（用户级，不受项目门控）；
    2. 项目链：``cwd`` 向上到 git root（含）为止的祖先目录——
       仅当 ``project_trusted`` 为真时读取。
    """
    resolved_cwd = Path(cwd).resolve()
    resolved_agent_dir = Path(agent_dir).resolve()

    files: List[ContextFile] = []
    seen_paths: set = set()

    # 1. 全局 agent_dir（用户级——永远读取）
    global_path = _find_context_file_in_dir(str(resolved_agent_dir))
    if global_path:
        entry = _make_context_file(global_path, "user", str(resolved_agent_dir))
        if entry is not None:
            files.append(entry)
            seen_paths.add(str(Path(global_path).resolve()))

    # 2. 项目链（git root 封顶 + trust 门控）
    if project_trusted:
        git_root = find_git_root(str(resolved_cwd))
        # 上溯终点：git root（含）；无 git root 只读 cwd
        stop_dir = git_root if git_root is not None else resolved_cwd

        chain: List[str] = []  # cwd → ... → stop_dir
        current = resolved_cwd
        while True:
            file_path = _find_context_file_in_dir(str(current))
            if file_path:
                resolved_file = str(Path(file_path).resolve())
                if resolved_file not in seen_paths:
                    chain.append(file_path)
                    seen_paths.add(resolved_file)
            if current == stop_dir:
                break
            parent = current.parent
            if parent == current:  # 已到文件系统根（防御：git root 必是 cwd 祖先）
                break
            current = parent

        # chain 此时是 cwd -> ... -> git root，反转为由远及近
        for file_path in reversed(chain):
            entry = _make_context_file(
                file_path, "project", str(Path(file_path).parent)
            )
            if entry is not None:
                files.append(entry)

    return files


__all__ = [
    "load_project_context_files",
]
