"""资源自动发现辅助函数。

负责从本地目录扫描待加载的资源路径，不解析具体内容。
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

import pathspec

from nova_harness.core.package.metadata.pyproject import (
    read_manifest,
    resolve_extension_entries,
)
from nova_harness.core.package.metadata.validation import (
    is_agent_dir,
    is_extension_path,
    is_skill_path,
    is_tool_dir,
    is_ui_block_dir,
)
from nova_harness.core.package.utils.ignore import (
    is_ignored_by_specs,
    iter_sorted_entries,
    load_ignore_specs,
)
from nova_harness.core.types.package_manager import (
    RESOURCE_TYPE_DIRS,
    NovaManifest,
    ResourceType,
)
from nova_harness.core.utils.git import find_git_root

logger = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"


# ---------------------------------------------------------------------------
# Override 模式匹配
# ---------------------------------------------------------------------------


def is_override_pattern(pattern: str) -> bool:
    """判断是否为 override 模式（!/+/- 开头）。"""
    return pattern.startswith("!") or pattern.startswith("+") or pattern.startswith("-")


def is_glob_pattern(pattern: str) -> bool:
    """判断 pattern 是否包含 glob 通配符（*, ?, [...]）。"""
    stripped = pattern.lstrip("!+-")
    if "*" in stripped or "?" in stripped:
        return True
    # 检测字符类 [...]，但排除字面量 [] 空括号。
    if "[" in stripped and "]" in stripped:
        start = stripped.find("[")
        end = stripped.find("]", start + 1)
        if end > start + 1:
            return True
    return False


def _normalize_path_for_compare(path: str, base_dir: str = ".") -> str:
    """用于精确比较的路径规范化。

    相对路径按 *base_dir* 解析，避免依赖进程当前工作目录。
    """
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(os.path.abspath(expanded))
    return os.path.normpath(os.path.abspath(os.path.join(base_dir, expanded)))


def _candidate_paths(file_path: str, base_dir: str) -> List[str]:
    """生成用于精确路径匹配的路径候选。

    对 SKILL.md 文件额外加入其父目录，方便按 skill 名称过滤。
    """
    abs_path = _normalize_path_for_compare(file_path, base_dir)
    candidates = [abs_path]

    norm_base = _normalize_path_for_compare(base_dir, base_dir)
    try:
        rel = os.path.relpath(abs_path, norm_base)
        candidates.append(rel)
    except ValueError:
        pass

    candidates.append(os.path.basename(abs_path))

    if os.path.basename(abs_path) == "SKILL.md":
        parent = os.path.dirname(abs_path)
        candidates.append(parent)
        try:
            candidates.append(os.path.relpath(parent, norm_base))
        except ValueError:
            pass
        candidates.append(os.path.basename(parent))

    return candidates


def _relative_or_abs(file_path: str, base_dir: str) -> str:
    """返回相对于 base_dir 的路径；如果无法相对则返回绝对路径。"""
    norm_file = _normalize_path_for_compare(file_path, base_dir)
    norm_base = _normalize_path_for_compare(base_dir, base_dir)
    try:
        return os.path.relpath(norm_file, norm_base)
    except ValueError:
        return norm_file


def _pathspec_for_pattern(pattern: str) -> pathspec.PathSpec:
    """为单个模式构建 pathspec。

    对非绝对路径模式同时加入 ``**/pattern`` 变体，使模式可从任意层级匹配。
    """
    stripped = pattern.lstrip("!+-")
    patterns: List[str] = [stripped]
    if not stripped.startswith("/"):
        patterns.append(f"**/{stripped}")
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def matches_pattern(file_path: str, pattern: str, base_dir: str) -> bool:
    """判断 file_path 是否匹配 pattern（glob 或普通路径）。"""
    stripped = pattern.lstrip("!+-")
    if not stripped:
        return False

    # 精确路径匹配优先。
    if matches_exact_pattern(file_path, pattern, base_dir):
        return True

    rel_path = _relative_or_abs(file_path, base_dir)
    spec = _pathspec_for_pattern(pattern)

    # 相对路径匹配。
    if spec.match_file(rel_path):
        return True

    # basename 匹配（非绝对模式时）。
    if not stripped.startswith("/"):
        basename = os.path.basename(rel_path)
        if spec.match_file(basename):
            return True

    return False


def matches_exact_pattern(file_path: str, pattern: str, base_dir: str) -> bool:
    """仅做精确路径匹配（用于 +/- 强制模式）。

    只比较绝对路径或相对于 base_dir 的路径是否相等，不再按 basename 模糊匹配。
    """
    stripped = pattern.lstrip("+-")
    if not stripped:
        return False

    norm_pattern = _normalize_path_for_compare(stripped, base_dir)

    for candidate in _candidate_paths(file_path, base_dir):
        # 绝对路径比较。
        if _normalize_path_for_compare(candidate, base_dir) == norm_pattern:
            return True

        # 相对路径比较（相对于 base_dir）。
        try:
            rel = os.path.relpath(
                _normalize_path_for_compare(candidate, base_dir),
                _normalize_path_for_compare(base_dir, base_dir),
            )
            if rel == stripped:
                return True
        except ValueError:
            pass

    return False


def apply_patterns(
    all_paths: List[str], patterns: List[str], base_dir: str
) -> Set[str]:
    """应用 override 模式返回启用路径集合。"""
    includes: List[str] = []
    excludes: List[str] = []
    force_includes: List[str] = []
    force_excludes: List[str] = []

    for p in patterns:
        if p.startswith("+"):
            force_includes.append(p)
        elif p.startswith("-"):
            force_excludes.append(p)
        elif p.startswith("!"):
            excludes.append(p)
        else:
            includes.append(p)

    # Step 1: includes（无 includes 则取全部）
    if includes:
        result = [
            p
            for p in all_paths
            if any(matches_pattern(p, inc, base_dir) for inc in includes)
        ]
    else:
        result = list(all_paths)

    # Step 2: excludes
    if excludes:
        result = [
            p
            for p in result
            if not any(matches_pattern(p, ex, base_dir) for ex in excludes)
        ]

    # Step 3: force-include（从 all_paths 中找回）
    if force_includes:
        for p in all_paths:
            if p not in result and any(
                matches_exact_pattern(p, fi, base_dir) for fi in force_includes
            ):
                result.append(p)

    # Step 4: force-exclude
    if force_excludes:
        result = [
            p
            for p in result
            if not any(matches_exact_pattern(p, fe, base_dir) for fe in force_excludes)
        ]

    return set(result)


def collect_extension_entries(directory: str) -> List[str]:
    """递归发现目录下的扩展入口路径，应用 ignore 规则。

    扩展入口可以是：
    - ``pyproject.toml`` 中 ``[tool.nova.extensions]`` 显式声明的路径；
    - 包含 ``extension.py`` 或 ``__init__.py`` 的目录；
    - 直接以 ``.py`` 结尾的文件。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())

    # 目录自身若声明了 extensions 入口，按 manifest 展开，不再递归扫描内部。
    manifest_entries = resolve_extension_entries(root)
    if manifest_entries is not None:
        resolved = _collect_explicit(
            manifest_entries, path, resource_type=ResourceType.EXTENSIONS
        )
        return resolved or []

    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_extension_path(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
            elif entry.is_file() and entry.suffix == ".py":
                if is_ignored_by_specs(rel, is_dir=False, specs=specs):
                    continue
                results.append(str(entry.resolve()))
        return results

    return collect(path, "")


def collect_skill_entries(
    directory: str, *, include_root_markdown: bool = True
) -> List[str]:
    """发现目录下的 skill 入口路径。

    规则：
    - 如果目录直接包含 ``SKILL.md``，返回该目录本身。
    - ``include_root_markdown=True`` 时，根目录下所有 ``.md`` 文件也视为 skill 文件。
    - 否则递归子目录查找 ``SKILL.md``，每个含 SKILL.md 的目录作为一个 skill。
    - 递归应用目录树中的 ignore 文件（``.gitignore`` / ``.ignore`` / ``.fdignore``）。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def _is_ignored(rel_path: str, is_dir: bool = False) -> bool:
        return is_ignored_by_specs(rel_path, is_dir, specs)

    def collect(current_dir: Path, rel_prefix: str, is_root: bool) -> List[str]:
        results: List[str] = []
        current_rel = rel_prefix.rstrip("/")

        direct_skill = current_dir / SKILL_FILE
        if direct_skill.exists():
            skill_rel = f"{current_rel}/{SKILL_FILE}" if current_rel else SKILL_FILE
            if not _is_ignored(skill_rel, is_dir=False):
                results.append(str(current_dir.resolve()))
            return results

        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{current_rel}/{entry.name}" if current_rel else entry.name
            if entry.is_dir():
                if _is_ignored(rel, is_dir=True):
                    continue
                sub = entry / SKILL_FILE
                if sub.exists():
                    skill_rel = f"{rel}/{SKILL_FILE}"
                    if not _is_ignored(skill_rel, is_dir=False):
                        results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel}/", is_root=False))
            elif entry.is_file() and entry.suffix == ".md":
                if include_root_markdown and is_root:
                    if not _is_ignored(rel, is_dir=False):
                        results.append(str(entry.resolve()))

        return results

    return collect(path, "", is_root=True)


def collect_prompt_entries(directory: str) -> List[str]:
    """发现目录下的 prompt 模板路径（``.md`` 文件）。

    递归应用目录树中的 ignore 文件。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
            elif entry.is_file() and entry.suffix == ".md":
                if is_ignored_by_specs(rel, is_dir=False, specs=specs):
                    continue
                results.append(str(entry.resolve()))
        return results

    return collect(path, "")


def collect_theme_entries(directory: str) -> List[str]:
    """发现目录下的主题路径（``.json`` 文件），递归并应用 ignore 规则。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
            elif entry.is_file() and entry.suffix == ".json":
                if is_ignored_by_specs(rel, is_dir=False, specs=specs):
                    continue
                results.append(str(entry.resolve()))
        return results

    return collect(path, "")


def collect_tool_entries(directory: str) -> List[str]:
    """递归发现目录下的工具包路径（含 ``schema.json`` 的目录），应用 ignore 规则。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_tool_dir(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
        return results

    return collect(path, "")


def collect_ui_block_entries(directory: str) -> List[str]:
    """递归发现目录下的 UI block 包路径（含 ``schema.py`` 或 ``schema.json`` 的目录），应用 ignore 规则。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_ui_block_dir(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
        return results

    return collect(path, "")


def collect_agent_entries(directory: str) -> List[str]:
    """递归发现目录下的 Agent 配置目录，应用 ignore 规则。

    将任何被 :func:`is_agent_dir` 识别为合法 Agent 目录的子目录视为一个
    Agent 资源，与其具体包含 ``agent.yaml``、``description.md`` 还是 ``sections/``
    无关。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_agent_dir(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
        return results

    return collect(path, "")


def collect_context_file_entries(
    start_dir: str, *, stop_at_git_root: bool = False
) -> List[str]:
    """从 ``start_dir`` 开始向文件系统根目录遍历，发现 ``AGENTS.md`` / ``CLAUDE.md``。

    Args:
        start_dir: 起始目录。
        stop_at_git_root: 为 True 时遇到 git 仓库根目录即停止向上遍历。

    返回的路径按“从近到远”排序（``start_dir`` 自身最先）。
    """
    results: List[str] = []
    path = Path(start_dir).resolve()
    root = Path(os.path.abspath(os.sep))
    git_root = find_git_root(str(path)) if stop_at_git_root else None

    while True:
        if path.exists() and path.is_dir():
            for entry in sorted(path.iterdir(), key=lambda p: p.name):
                if not entry.is_file():
                    continue
                if entry.name.lower() in {"agents.md", "claude.md"}:
                    results.append(str(entry.resolve()))

        if git_root is not None and path == git_root:
            break
        if path == root:
            break
        parent = path.parent
        if parent == path:
            break
        path = parent

    return results


def collect_ancestor_agents_skills_dirs(
    start_dir: str, *, stop_at_git_root: bool = True
) -> List[str]:
    """从 ``start_dir`` 向上收集祖先目录中的 ``.agents/skills`` 目录。

    从 ``start_dir`` 开始向上遍历，直到 git root（若启用）或文件系统根。
    返回的目录路径按"从近到远"排序。
    """
    results: List[str] = []
    path = Path(start_dir).resolve()
    root = Path(os.path.abspath(os.sep))
    git_root = find_git_root(str(path)) if stop_at_git_root else None

    while True:
        candidate = path / ".agents" / "skills"
        if candidate.exists() and candidate.is_dir():
            results.append(str(candidate.resolve()))

        if git_root is not None and path == git_root:
            break
        if path == root:
            break
        parent = path.parent
        if parent == path:
            break
        path = parent

    return results


def _inside_package(path: Path, base: Path) -> bool:
    """Return True if *path* resolves inside *base*."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _collect_explicit(
    items: Optional[List[str]],
    base: Path,
    resource_type: Optional[ResourceType] = None,
) -> Optional[List[str]]:
    """Resolve explicit relative paths/globs inside a package, applying ignore patterns.

    支持的模式：
    - 普通相对路径 / glob：包含匹配资源
    - ``!pattern``：排除匹配路径
    - ``+path``：强制包含精确路径
    - ``-path``：强制排除精确路径
    - manifest 显式声明的目录会被递归扫描；目录本身若即为合法资源（如含
      ``SKILL.md`` 的 skill 目录、含 ``extension.py`` 的扩展目录），也会保留
    - 整个收集过程应用 ``.gitignore`` / ``.ignore`` / ``.fdignore`` 规则

    不存在的路径会被跳过并记录警告。
    """
    if items is None:
        return None

    plain_entries: List[str] = []
    override_patterns: List[str] = []
    for rel in items:
        if is_override_pattern(rel):
            override_patterns.append(rel)
        else:
            plain_entries.append(rel)

    all_paths: List[str] = []
    seen: Set[str] = set()
    specs = load_ignore_specs(str(base.resolve())) if base.exists() else []

    def _normalize_rel(raw: str) -> str:
        """去掉 ./ 前缀，使相对路径与 glob 结果一致。"""
        while raw.startswith("./"):
            raw = raw[2:]
        return raw

    def _is_ignored(path: Path) -> bool:
        """判断 *path*（相对于 base）是否被 ignore 规则匹配。"""
        if not specs:
            return False
        try:
            rel = path.resolve().relative_to(base.resolve())
        except ValueError:
            return False
        return is_ignored_by_specs(str(rel), is_dir=path.is_dir(), specs=specs)

    def _add_path(path: Path) -> None:
        """把单个文件或目录加入候选集合。

        文件直接加入；目录则按 *resource_type* 处理——若目录本身即为该类型的
        合法资源（如含 ``description.md`` 的 agent 目录、含 ``SKILL.md`` 的
        skill 目录），则直接保留；否则递归扫描其内部资源。若 *resource_type*
        未提供，目录本身会被保留为候选。整个过程应用 ignore 规则。
        """
        abs_path = str(path.resolve())
        if abs_path in seen:
            return
        seen.add(abs_path)

        if path.is_file():
            if _is_ignored(path):
                return
            all_paths.append(abs_path)
            return

        if path.is_dir():
            if _is_ignored(path):
                return
            if resource_type is None:
                all_paths.append(abs_path)
                return

            # 判断目录本身是否即为该资源类型的合法入口。
            is_valid_resource = {
                ResourceType.AGENTS: is_agent_dir,
                ResourceType.TOOLS: is_tool_dir,
                ResourceType.SKILLS: is_skill_path,
                ResourceType.EXTENSIONS: is_extension_path,
            }.get(resource_type)

            if is_valid_resource is not None and is_valid_resource(str(path)):
                all_paths.append(abs_path)
                return

            # 目录本身不是合法资源时，递归扫描其内部。
            for discovered in RESOURCE_DISCOVERY[resource_type](str(path)):
                discovered_abs = str(Path(discovered).resolve())
                if discovered_abs in seen:
                    continue
                seen.add(discovered_abs)
                all_paths.append(discovered_abs)

    for rel in plain_entries:
        normalized_rel = _normalize_rel(rel)
        expanded = (base / normalized_rel).expanduser()

        if is_glob_pattern(rel):
            # 拒绝可能逃逸包根的 glob 模式（如 ../*.md）。
            if ".." in Path(normalized_rel).parts:
                raise ValueError(f"Glob pattern escapes package root: {rel}")
            for matched in base.glob(rel):
                if not _inside_package(matched, base):
                    continue
                _add_path(matched)
            continue

        if not _inside_package(expanded, base):
            raise ValueError(f"Path escapes package root: {rel}")

        if not expanded.exists():
            logger.warning(
                "Manifest-declared resource does not exist, skipping: %s", expanded
            )
            continue

        _add_path(expanded)

    if override_patterns:
        # 把路径和模式都转成相对 base 的形式，并统一去掉 ./ 前缀，确保匹配。
        base_str = str(base.resolve())
        rel_paths = []
        for p in all_paths:
            try:
                rel = os.path.relpath(p, base_str)
            except ValueError:
                rel = p
            rel_paths.append(_normalize_rel(rel))

        normalized_patterns = []
        for p in override_patterns:
            prefix = p[0]
            body = _normalize_rel(p[1:])
            normalized_patterns.append(f"{prefix}{body}")

        enabled_rel = apply_patterns(rel_paths, normalized_patterns, base_str)
        enabled_abs = {str(Path(base_str) / rel) for rel in enabled_rel}
        return [p for p in all_paths if p in enabled_abs]

    return all_paths


def collect_package_entries(
    package_dir: str,
    resource_type: ResourceType,
    nova: Optional[NovaManifest] = None,
) -> List[str]:
    """Collect all candidate paths of *resource_type* inside *package_dir*.

    Explicit manifest lists take precedence. An empty list (``agents: []``)
    disables directory scanning for that category. Only when a list is absent do
    we fall back to scanning the standard directory.

    Returned paths are **not filtered**; callers apply pattern overrides if
    needed.
    """
    base = Path(package_dir)

    explicit = None
    if nova is not None:
        explicit = getattr(nova, resource_type.value, None)

    if explicit is not None:
        if len(explicit) == 0:
            return []
        resolved = _collect_explicit(explicit, base, resource_type=resource_type)
        return resolved or []

    std_dir = base / RESOURCE_TYPE_DIRS[resource_type]
    if not std_dir.exists():
        return []
    return RESOURCE_DISCOVERY[resource_type](str(std_dir))


def collect_all_package_entries(
    package_dir: str,
) -> Dict[str, List[str]]:
    """Collect entries of all package resource types inside *package_dir*.

    Returns a mapping from resource-type string (e.g. ``"agents"``) to a list of
    resolved paths. Paths are not filtered. Context files are not included because
    they are discovered via ancestor traversal, not from a package directory.
    """
    manifest = read_manifest(package_dir)
    nova = manifest.nova

    return {
        resource_type.value: collect_package_entries(package_dir, resource_type, nova)
        for resource_type in ResourceType
    }


RESOURCE_DISCOVERY: dict[ResourceType, callable] = {
    ResourceType.EXTENSIONS: collect_extension_entries,
    ResourceType.SKILLS: collect_skill_entries,
    ResourceType.PROMPTS: collect_prompt_entries,
    ResourceType.THEMES: collect_theme_entries,
    ResourceType.TOOLS: collect_tool_entries,
    ResourceType.AGENTS: collect_agent_entries,
    ResourceType.UI_BLOCKS: collect_ui_block_entries,
}


__all__ = [
    "collect_extension_entries",
    "collect_skill_entries",
    "collect_prompt_entries",
    "collect_theme_entries",
    "collect_tool_entries",
    "collect_agent_entries",
    "collect_ui_block_entries",
    "collect_context_file_entries",
    "collect_package_entries",
    "collect_all_package_entries",
    "RESOURCE_DISCOVERY",
    "is_override_pattern",
    "is_glob_pattern",
    "matches_pattern",
    "matches_exact_pattern",
    "apply_patterns",
]
