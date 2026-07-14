"""Gitignore-style ignore rule helpers for package resource discovery.

Used by both top-level auto-discovery and package-internal standard-directory
scanning so that ignore rules are applied consistently.
"""

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pathspec

IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")

IgnoreSpecWithPrefix = Tuple[Optional[pathspec.PathSpec], str]


def load_ignore_specs(root_directory: str) -> List[IgnoreSpecWithPrefix]:
    """递归收集 *root_directory* 下所有 ignore 文件规则。

    每个规则条目包含 ``(PathSpec, relative_prefix)``：
    - ``PathSpec`` 是从该目录 ignore 文件解析出的规则集合；
    - ``relative_prefix`` 是规则文件所在目录相对于 *root_directory* 的相对路径
      （为空字符串表示就在根目录），用于把规则中的相对路径转换为
      相对于 *root_directory* 的路径。

    收集顺序为自顶向下，和 gitignore 的嵌套语义一致。
    """
    root = Path(root_directory).resolve()
    if not root.exists() or not root.is_dir():
        return []

    specs: List[IgnoreSpecWithPrefix] = []

    def collect(current: Path, prefix: str) -> None:
        patterns: List[str] = []
        for name in IGNORE_FILE_NAMES:
            path = current / name
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            patterns.append(stripped)
                except (OSError, UnicodeDecodeError):
                    continue
        if patterns:
            specs.append((pathspec.PathSpec.from_lines("gitignore", patterns), prefix))

        for entry in sorted(current.iterdir(), key=lambda p: p.name):
            if entry.name.startswith(".") or entry.name == "node_modules":
                continue
            if entry.is_dir():
                next_prefix = f"{prefix}{entry.name}/" if prefix else f"{entry.name}/"
                collect(entry, next_prefix)

    collect(root, "")
    return specs


def is_ignored_by_specs(
    rel_path: str, is_dir: bool, specs: List[IgnoreSpecWithPrefix]
) -> bool:
    """判断相对于资源根目录的 *rel_path* 是否被任意 ignore 规则匹配。

    规则按自顶向下顺序应用，后者覆盖前者（gitignore 语义）。
    """
    target = rel_path + "/" if is_dir else rel_path
    matched = False
    for spec, prefix in specs:
        # 只考虑可能匹配该路径的规则前缀范围
        if prefix and not (target == prefix or target.startswith(prefix)):
            continue
        relative_to_spec = target[len(prefix) :] if prefix else target
        if not relative_to_spec:
            continue
        if spec.match_file(relative_to_spec):
            matched = True
        elif spec.match_file(relative_to_spec.rstrip("/")):
            # 某些规则写成 file 而非 file/，对目录也尝试无斜杠匹配
            matched = True
    return matched


def iter_sorted_entries(directory: str) -> Iterable[Path]:
    """迭代目录下排序后的条目，跳过隐藏文件和 node_modules。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue
        yield entry


__all__ = [
    "IGNORE_FILE_NAMES",
    "IgnoreSpecWithPrefix",
    "load_ignore_specs",
    "is_ignored_by_specs",
    "iter_sorted_entries",
]
