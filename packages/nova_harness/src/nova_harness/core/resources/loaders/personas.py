"""Persona 加载实现（persona 升格：人格文本从素材升为资源类目）。

包含两个层次：

1. 文件级：读取单个 ``.md`` 人格文本；目录条目递归收 ``*.md``，按相对
   personas 根的路径去扩展名命名（posix 形态，如 ``coding/core``）。
2. Resource 级：由 ``PackageResolver`` 提供路径（**personas 根目录**或
   显式单文件），处理去重与碰撞诊断（first-wins，与 skills 同语义）。
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nova_harness.core.package.utils import (
    IgnoreSpecWithPrefix,
    is_ignored_by_specs,
    load_ignore_specs,
)
from nova_harness.core.resources.source_info import (
    default_source_info_for_path,
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import ResolvedResource
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.personas import Persona
from nova_harness.core.utils.files import canonicalize_path, load_text_file


def persona_name_from_path(file_path: Path, root_dir: Path) -> str:
    """由文件路径推导 persona 注册名：相对 *root_dir* 去 ``.md``（posix 形态）。"""
    try:
        rel = file_path.resolve().relative_to(root_dir.resolve())
    except ValueError:
        rel = Path(file_path.name)
    if rel.suffix == ".md":
        rel = rel.with_suffix("")
    return rel.as_posix()


def load_persona_from_file(
    file_path: str,
    name: Optional[str] = None,
    source_info: Optional[SourceInfo] = None,
) -> Tuple[Optional[Persona], List[ResourceDiagnostic]]:
    """加载单个 persona 文件，返回 ``(persona, diagnostics)``。

    *name* 缺省时取文件名 stem。文件不可读或内容为空白时拒载（附 warning）。
    """
    diagnostics: List[ResourceDiagnostic] = []
    content = load_text_file(file_path)
    if content is None:
        diagnostics.append(
            ResourceDiagnostic(
                category="warning",
                message="persona 文件读取失败或内容为空",
                path=file_path,
            )
        )
        return None, diagnostics

    # source_info 的 path 指向实际 persona 文件（与 skills 的 path 更新语义一致），
    # 而不是共享的资源根路径。
    if source_info is not None:
        source_info = source_info.model_copy(update={"path": file_path})

    return (
        Persona(
            name=name or Path(file_path).stem,
            content=content,
            file_path=file_path,
            source_info=source_info,
        ),
        diagnostics,
    )


def load_personas_from_dir(
    directory: str,
    source_info: Optional[SourceInfo] = None,
) -> Tuple[List[Persona], List[ResourceDiagnostic]]:
    """递归扫描 personas 根目录加载全部 persona，返回 ``(personas, diagnostics)``。

    发现规则：递归收 ``*.md``，按相对根目录路径去扩展名命名；跳过 ``.``
    开头条目与各生态依赖/缓存目录；应用目录树中的 ignore 规则
    （``.gitignore`` / ``.ignore`` / ``.fdignore``）。
    """
    personas: List[Persona] = []
    diagnostics: List[ResourceDiagnostic] = []
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return personas, diagnostics

    root_dir = str(root.resolve())
    specs: List[IgnoreSpecWithPrefix] = load_ignore_specs(root_dir)

    files = [p for p in root.rglob("*.md") if p.is_file() and _is_visible(p, root)]
    for file_path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = file_path.relative_to(root).as_posix()
        if is_ignored_by_specs(rel, is_dir=False, specs=specs):
            continue
        persona, warns = load_persona_from_file(
            str(file_path),
            name=persona_name_from_path(file_path, root),
            source_info=source_info,
        )
        diagnostics.extend(warns)
        if persona is not None:
            personas.append(persona)

    return personas, diagnostics


def _is_visible(path: Path, root: Path) -> bool:
    """路径任一相对段以 ``.`` 开头即不可见（隐藏文件/目录不收）。"""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


# =============================================================================
# Resource 级加载
# =============================================================================


def _collect_persona_paths(
    additional_paths: Optional[List[str]],
    resolved_resources: Optional[List[ResolvedResource]] = None,
    extension_source_infos: Optional[List[SourceInfo]] = None,
    agent_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[List[Tuple[str, Optional[SourceInfo]]], List[ResourceDiagnostic]]:
    """Return ``(paths, diagnostics)`` to load personas from.

    路径优先级（与其他类目的单通道语义一致）：``resolved_resources``
    （settings/自动发现/包）> ``additional_paths``（扩展贡献等显式传入）。
    显式路径不存在时产生 warning 诊断；自动发现的资源路径不存在则静默。
    """
    paths: List[Tuple[str, Optional[SourceInfo]]] = []
    diagnostics: List[ResourceDiagnostic] = []
    seen: set = set()

    def add(
        path: str,
        source_info: Optional[SourceInfo] = None,
        warn_missing: bool = False,
    ) -> None:
        resolved = Path(path).resolve()
        if not resolved.exists():
            if warn_missing:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="warning",
                        message="persona path does not exist",
                        path=str(resolved),
                    )
                )
            return
        real = canonicalize_path(str(resolved))
        if real not in seen:
            seen.add(real)
            paths.append((str(resolved), source_info))

    for resource in resolved_resources or []:
        if not resource.enabled:
            continue
        add(resource.path, source_info_from_metadata(resource))

    for p in additional_paths or []:
        resolved = Path(p).resolve()
        source_info = None
        if extension_source_infos:
            source_info = find_source_info_for_path(
                str(resolved), extension_source_infos
            )
        if source_info is None:
            source_info = default_source_info_for_path(
                str(resolved), agent_dir=agent_dir, cwd=cwd
            )
        add(p, source_info, warn_missing=True)

    return paths, diagnostics


def load_personas(
    additional_paths: Optional[List[str]] = None,
    resolved_resources: Optional[List[ResolvedResource]] = None,
    extension_source_infos: Optional[List[SourceInfo]] = None,
    agent_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[Dict[str, Persona], List[ResourceDiagnostic]]:
    """加载所有可用 persona。

    返回 ``(personas_by_name, diagnostics)``。同名 persona 按优先级保留第一个，
    后续重复项生成 collision 诊断（first-wins，与 skills 同语义）。

    路径必须由 ``PackageResolver`` 提供；``additional_paths`` 作为补充追加。
    名单过滤不在加载层——persona 的消费（装配/override）归 PersonaManager。

    Args:
        agent_dir / cwd: 用于为 additional 路径合成默认 ``SourceInfo`` 的
            全局/项目基准目录。
    """
    candidates, diagnostics = _collect_persona_paths(
        additional_paths=additional_paths,
        resolved_resources=resolved_resources,
        extension_source_infos=extension_source_infos,
        agent_dir=agent_dir,
        cwd=cwd,
    )

    personas: Dict[str, Persona] = {}

    for path, source_info in candidates:
        if os.path.isdir(path):
            loaded, warns = load_personas_from_dir(path, source_info=source_info)
            diagnostics.extend(warns)
        elif path.endswith(".md"):
            persona, warns = load_persona_from_file(path, source_info=source_info)
            diagnostics.extend(warns)
            loaded = [persona] if persona is not None else []
        else:
            # 显式路径既不是目录也不是 markdown 文件
            diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message="persona path is not a markdown file",
                    path=path,
                )
            )
            continue

        for persona in loaded:
            existing = personas.get(persona.name)
            if existing is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=(
                            f"Persona '{persona.name}' from {persona.file_path} "
                            f"shadowed by {existing.file_path}"
                        ),
                        path=persona.file_path,
                        collision=ResourceCollision(
                            resource_type="persona",
                            name=persona.name,
                            winner_path=existing.file_path,
                            loser_path=persona.file_path,
                        ),
                    )
                )
            else:
                personas[persona.name] = persona

    return personas, diagnostics


__all__ = [
    "load_personas",
    "load_persona_from_file",
    "load_personas_from_dir",
    "persona_name_from_path",
]
