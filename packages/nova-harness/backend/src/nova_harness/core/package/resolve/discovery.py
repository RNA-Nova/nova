"""资源自动发现辅助函数。

负责从本地目录扫描待加载的资源路径，不解析具体内容。
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

import pathspec
from nova_harness.core.package.manifest import (
    read_manifest,
    resolve_extension_entries,
)
from nova_harness.core.package.utils import (
    is_ignored_by_specs,
    iter_sorted_entries,
    load_ignore_specs,
)
from nova_harness.core.package.validation import (
    is_agent_file,
    is_extension_path,
    is_persona_dir,
    is_skill_path,
    is_tool_dir,
    is_user_tool_dir,
)
from nova_harness.core.types.package import (
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

    只比较绝对路径或相对于 base_dir 的相对路径是否相等，**不匹配
    basename**（对齐 TS：强制模式是精确逃生口，不能按名字模糊命中）。
    对 SKILL.md 文件额外比较其父目录，方便按 skill 目录精确指定。
    """
    stripped = pattern.lstrip("+-")
    if not stripped:
        return False
    stripped = os.path.normpath(stripped)

    norm_pattern = _normalize_path_for_compare(stripped, base_dir)
    norm_base = _normalize_path_for_compare(base_dir, base_dir)

    abs_path = _normalize_path_for_compare(file_path, base_dir)
    candidates = [abs_path]
    if os.path.basename(abs_path) == "SKILL.md":
        candidates.append(os.path.dirname(abs_path))

    for candidate in candidates:
        if candidate == norm_pattern:
            return True
        try:
            if os.path.relpath(candidate, norm_base) == stripped:
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


def apply_autoload_disabled_patterns(
    all_paths: List[str], patterns: List[str], base_dir: str
) -> Dict[str, bool]:
    """``autoload=false`` 的 delta 过滤：返回 ``{path: enabled}`` 映射。

    与 :func:`apply_patterns` 的"全集筛选"不同，这里只返回**被 patterns
    提及**的资源及其 enabled 状态，未提及的资源不出现在结果中——它们由
    调用方的底层条目（user scope 同 identity 包）决定。

    规则（对齐 TS ``applyAutoloadDisabledPatterns``）：

    - ``+path``：强制启用（精确路径匹配）；
    - ``-path``：强制禁用（精确路径匹配）；
    - ``!pattern``：禁用（glob/名称模式匹配）；
    - 普通 pattern：启用（glob/名称模式匹配）。

    同一资源被多个 pattern 命中时，后者覆盖前者。
    """
    result: Dict[str, bool] = {}
    for pattern in patterns:
        if not pattern:
            continue
        prefix = pattern[0] if pattern[0] in ("+", "-", "!") else ""
        target = pattern[1:] if prefix else pattern
        if not target:
            continue
        enabled = prefix not in ("-", "!")
        exact = prefix in ("+", "-")
        for path in all_paths:
            matched = (
                matches_exact_pattern(path, target, base_dir)
                if exact
                else matches_pattern(path, target, base_dir)
            )
            if matched:
                result[path] = enabled
    return result


def collect_extension_entries(directory: str) -> List[str]:
    """发现目录下的扩展入口路径，应用 ignore 规则。

    发现规则（对齐 TS ``collectAutoExtensionEntries``，只看当前层级）：

    - 目录自身声明 ``[tool.nova.extensions]`` → 按 manifest 展开，不再扫描内部；
    - 根级 ``.py`` 文件直接收集；
    - 直接子目录必须是合法扩展入口（含 ``extension.py`` / ``__init__.py``，
      或自身声明 ``[tool.nova.extensions]``）才收集；
    - **不递归非扩展目录**——避免把 ``extensions/lib/helpers.py`` 这类辅助
      模块当扩展加载并执行其模块级代码。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())

    # 目录自身若声明了 extensions 入口，按 manifest 展开，不再递归扫描内部。
    manifest_entries = resolve_extension_entries(root)
    if manifest_entries is not None:
        resolved = collect_explicit(
            manifest_entries, path, resource_type=ResourceType.EXTENSIONS
        )
        return resolved or []

    specs = load_ignore_specs(root)
    results: List[str] = []

    for entry in iter_sorted_entries(root):
        if entry.is_dir():
            if is_ignored_by_specs(entry.name, is_dir=True, specs=specs):
                continue
            sub_manifest = resolve_extension_entries(str(entry.resolve()))
            if sub_manifest is not None:
                results.extend(
                    collect_explicit(
                        sub_manifest,
                        entry,
                        resource_type=ResourceType.EXTENSIONS,
                    )
                    or []
                )
            elif is_extension_path(str(entry)):
                results.append(str(entry.resolve()))
        elif entry.is_file() and entry.suffix == ".py":
            if is_ignored_by_specs(entry.name, is_dir=False, specs=specs):
                continue
            results.append(str(entry.resolve()))

    return results


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
    """发现包内的 prompt 模板路径（``.md`` 文件，递归子目录）。

    递归应用目录树中的 ignore 文件。顶层自动发现请用
    :func:`collect_auto_prompt_entries`（只收当前层级，对齐 TS）。
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


def collect_auto_prompt_entries(directory: str) -> List[str]:
    """顶层自动发现的 prompt 模板路径——只收当前层级的 ``.md`` 文件。

    对齐 TS ``collectAutoPromptEntries``：自动发现的 prompts 不递归子目录
    （包内的 prompts 才递归，见 :func:`collect_prompt_entries`）。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)
    results: List[str] = []

    for entry in iter_sorted_entries(root):
        if entry.is_file() and entry.suffix == ".md":
            if is_ignored_by_specs(entry.name, is_dir=False, specs=specs):
                continue
            results.append(str(entry.resolve()))

    return results


def collect_tool_entries(directory: str) -> List[str]:
    """递归发现目录下的工具路径（``.py`` 单文件或含 ``executor.py`` 的目录）。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_file() and entry.suffix == ".py":
                if not is_ignored_by_specs(rel, is_dir=False, specs=specs):
                    results.append(str(entry.resolve()))
                continue
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_tool_dir(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
        return results

    return collect(path, "")


def collect_user_tool_entries(directory: str) -> List[str]:
    """递归发现目录下的用户工具路径（``.py`` 单文件或含 ``executor.py`` 的目录）。"""
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    def collect(current_dir: Path, rel_prefix: str) -> List[str]:
        results: List[str] = []
        for entry in iter_sorted_entries(str(current_dir)):
            rel = f"{rel_prefix.rstrip('/')}/{entry.name}" if rel_prefix else entry.name
            if entry.is_file() and entry.suffix == ".py":
                if not is_ignored_by_specs(rel, is_dir=False, specs=specs):
                    results.append(str(entry.resolve()))
                continue
            if entry.is_dir():
                if is_ignored_by_specs(rel, is_dir=True, specs=specs):
                    continue
                if is_user_tool_dir(str(entry)):
                    results.append(str(entry.resolve()))
                else:
                    results.extend(collect(entry, f"{rel_prefix}{entry.name}/"))
        return results

    return collect(path, "")


def collect_agent_entries(directory: str) -> List[str]:
    """发现目录顶层的 agent 组合声明（``*.yaml``——一文件一 agent，扁平约定）。

    应用 ignore 规则；不递归（组合层目录保持扁平——一个 agent 一份 yaml）。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []

    root = str(path.resolve())
    specs = load_ignore_specs(root)

    results: List[str] = []
    for entry in iter_sorted_entries(str(path)):
        if not entry.is_file():
            continue
        if not entry.name.endswith((".yaml", ".yml")):
            continue
        if is_ignored_by_specs(entry.name, is_dir=False, specs=specs):
            continue
        results.append(str(entry.resolve()))
    return results


def collect_persona_entries(directory: str) -> List[str]:
    """发现 personas 资源条目：**personas 根目录本身**即一个资源条目。

    persona 的命名 = 相对 personas 根的路径去扩展名（``coding/core``），
    因此收集粒度必须停在根目录（命名根随条目走）；递归展开与逐文件命名
    由 loader（``resources/loaders/personas.py``）完成。
    """
    path = Path(directory)
    if not path.exists() or not path.is_dir():
        return []
    return [str(path.resolve())]


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


def collect_explicit(
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
        合法资源（如含 ``SKILL.md`` 的 skill 目录、工具/扩展的单文件或目录），
        则直接保留；否则递归扫描其内部资源（agent 组合声明为 ``*.yaml`` 文件）。若 *resource_type*
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
            # PERSONAS 必须在此命中：其发现函数返回目录自身（目录即资源），
            # 落入通用递归分支会被 seen 预占自我吞掉。
            is_valid_resource = {
                ResourceType.TOOLS: is_tool_dir,
                ResourceType.SKILLS: is_skill_path,
                ResourceType.EXTENSIONS: is_extension_path,
                ResourceType.USER_TOOLS: is_user_tool_dir,
                ResourceType.PERSONAS: is_persona_dir,
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
            # manifest 条目必须留在包根内：绝对 glob 与 .. 逃逸都直接拒绝
            # （pathlib 不支持绝对模式，会抛 NotImplementedError）。
            if os.path.isabs(normalized_rel):
                raise ValueError(f"Glob pattern escapes package root: {rel}")
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
        resolved = collect_explicit(explicit, base, resource_type=resource_type)
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
    ResourceType.TOOLS: collect_tool_entries,
    ResourceType.AGENTS: collect_agent_entries,
    ResourceType.USER_TOOLS: collect_user_tool_entries,
    ResourceType.PERSONAS: collect_persona_entries,
}

# 顶层自动发现的扫描函数。与包内收集的唯一差异在 prompts：顶层只收当前
# 层级（对齐 TS collectAutoPromptEntries），包内才递归子目录。
RESOURCE_AUTO_DISCOVERY: dict[ResourceType, callable] = {
    **RESOURCE_DISCOVERY,
    ResourceType.PROMPTS: collect_auto_prompt_entries,
}


__all__ = [
    "RESOURCE_AUTO_DISCOVERY",
    "RESOURCE_DISCOVERY",
    "apply_autoload_disabled_patterns",
    "apply_patterns",
    "collect_agent_entries",
    "collect_all_package_entries",
    "collect_ancestor_agents_skills_dirs",
    "collect_auto_prompt_entries",
    "collect_explicit",
    "collect_extension_entries",
    "collect_package_entries",
    "collect_persona_entries",
    "collect_prompt_entries",
    "collect_user_tool_entries",
    "collect_skill_entries",
    "collect_tool_entries",
    "is_glob_pattern",
    "is_override_pattern",
    "matches_exact_pattern",
    "matches_pattern",
]
