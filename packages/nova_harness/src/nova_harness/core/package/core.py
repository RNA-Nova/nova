"""PackageManager — install / uninstall / list / info for agents, tools & skills.

Typical usage::

    >>> from nova_harness.core.package import PackageManager
    >>> pm = PackageManager()
    >>>
    >>> # Install a single agent config
    >>> pm.install("/path/to/my_agent", kind="agent")
    >>>
    >>> # Install a bundle (agents + tools + skills)
    >>> pm.install("/path/to/nova_coding_agent")
    >>>
    >>> # List everything
    >>> for pkg in pm.list():
    ...     print(pkg.name, pkg.version, pkg.kind)
    >>>
    >>> # Uninstall
    >>> pm.uninstall("my_agent", kind="agent")
    >>> pm.uninstall("nova-coding-agent", kind="bundle")
"""

import os
import shutil
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nova_harness.core.config.defaults import CONFIG_DIR_NAME, get_agent_dir
from nova_harness.core.package.binary_deps import (
    detect_missing_binaries,
    format_binary_hints,
    try_install_binaries,
)
from nova_harness.core.package.deps import (
    check_dependency_conflicts,
    extract_package_name,
    install_dependencies,
    uninstall_dependencies,
)
from nova_harness.core.package.manifest import (
    read_manifest,
    read_requirements,
)
from nova_harness.core.package.pyproject_deps import read_pyproject_dependencies
from nova_harness.core.package.sources import (
    PackageSource,
    SourceResolver,
    parse_source,
)
from nova_harness.core.package.utils import (
    copytree,
    infer_kind,
    is_agent_dir,
    is_tool_dir,
    load_json_file,
    now_iso,
    save_json_file,
)
from nova_harness.core.types.package_manager import (
    BundleView,
    InstalledItem,
    PackageMetadata,
)

DEFAULT_PACKAGES_FILE = "packages.json"

# Valid package kinds after redesign.
KIND_AGENT = "agent"  # single agent config
KIND_TOOL = "tool"  # single tool
KIND_SKILL = "skill"  # single skill
KIND_BUNDLE = "bundle"  # collection of agents/tools/skills
VALID_KINDS = {KIND_AGENT, KIND_TOOL, KIND_SKILL, KIND_BUNDLE}


def _normalize_kind(kind: str) -> str:
    """Map legacy explicit kind names to the new vocabulary."""
    if kind == "definition":
        return KIND_AGENT
    return kind


def _migrate_stored_kind(kind: str) -> str:
    """Map legacy kind names stored in packages.json to the new vocabulary."""
    if kind == "definition":
        return KIND_AGENT
    if kind == "agent":
        # Legacy "agent" packages were bundles of definitions + tools.
        return KIND_BUNDLE
    return kind


class PackageManager:
    """Manage agent, tool, skill, and bundle packages under the agent directory."""

    def __init__(self, agent_dir: Optional[str] = None, local: bool = False) -> None:
        if local:
            self.agent_dir = Path.cwd() / CONFIG_DIR_NAME / "agent"
        else:
            self.agent_dir = Path(agent_dir) if agent_dir else get_agent_dir()
        self.agents_dir = self.agent_dir / "agents"
        self.tools_dir = self.agent_dir / "tools"
        self.skills_dir = self.agent_dir / "skills"
        self.packages_dir = self.agent_dir / "packages"
        self.git_root = self.packages_dir / "git"
        self.manifest_path = self.agent_dir / DEFAULT_PACKAGES_FILE

        # Ensure directories exist
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Manifest (packages.json)
    # ------------------------------------------------------------------
    def _load_manifest(self) -> dict:
        data = load_json_file(str(self.manifest_path))
        if isinstance(data, dict) and "packages" in data:
            # Migrate legacy kind names in-memory.
            for item in data["packages"]:
                old_kind = item.get("kind")
                if old_kind in ("definition", "agent"):
                    item["kind"] = _migrate_stored_kind(old_kind)
            return data
        return {"packages": []}

    def _save_manifest(self, data: dict) -> None:
        save_json_file(str(self.manifest_path), data)

    def _find_in_manifest(self, name: str, kind: str) -> Optional[PackageMetadata]:
        data = self._load_manifest()
        for item in data["packages"]:
            if item.get("name") == name and item.get("kind") == kind:
                return PackageMetadata.model_validate(item)
        return None

    def _add_to_manifest(self, meta: PackageMetadata) -> None:
        data = self._load_manifest()
        # Remove existing entry with same name+kind
        data["packages"] = [
            p
            for p in data["packages"]
            if not (p.get("name") == meta.name and p.get("kind") == meta.kind)
        ]
        data["packages"].append(meta.model_dump())
        self._save_manifest(data)

    def _remove_from_manifest(self, name: str, kind: str) -> bool:
        data = self._load_manifest()
        original_len = len(data["packages"])
        data["packages"] = [
            p
            for p in data["packages"]
            if not (p.get("name") == name and p.get("kind") == kind)
        ]
        if len(data["packages"]) < original_len:
            self._save_manifest(data)
            return True
        return False

    # ------------------------------------------------------------------
    # Public install / update / uninstall
    # ------------------------------------------------------------------
    def install(
        self,
        source: str,
        kind: Optional[str] = None,
        name: Optional[str] = None,
        no_deps: bool = False,
        with_binaries: bool = False,
        dry_run: bool = False,
        quiet: bool = False,
    ) -> PackageMetadata:
        """Install an agent, tool, skill, or bundle package from a supported source.

        Args:
            source: Source spec. Supports local paths, ``local:/path``,
                ``git:host/repo@ref``, and ``https://...``.
            kind: ``"agent"``, ``"tool"``, ``"skill"``, or ``"bundle"``. If omitted,
                inferred from manifest or directory contents.
            name: Target name (defaults to manifest name or source basename).
            no_deps: Skip Python dependency installation.
            with_binaries: Attempt to install optional binaries.
            dry_run: Preview what would be installed without making changes.
            quiet: Suppress non-essential output (used by JSON mode).

        Returns:
            The installed package metadata (or the metadata that would be installed
            when ``dry_run`` is True).
        """
        source_obj = parse_source(source)
        resolver = SourceResolver(self.agent_dir)
        local_dir = resolver.resolve(source_obj)

        manifest = read_manifest(local_dir)
        resolved_kind = self._resolve_kind(kind, manifest, local_dir)

        if resolved_kind == KIND_BUNDLE:
            return self._install_bundle(
                local_dir,
                source_obj,
                name,
                manifest,
                no_deps,
                with_binaries,
                dry_run,
                quiet,
            )
        return self._install_single(
            local_dir,
            source_obj,
            resolved_kind,
            name,
            manifest,
            no_deps,
            with_binaries,
            dry_run,
            quiet,
        )

    def update(self, name: str, kind: str) -> PackageMetadata:
        """Re-install a package from its recorded source."""
        kind = _normalize_kind(kind)
        meta = self.info(name, kind)
        if meta is None:
            raise ValueError(f"Package '{name}' ({kind}) is not installed.")
        if not meta.source:
            raise ValueError(f"Package '{name}' has no recorded source; cannot update.")
        return self.install(
            meta.source,
            kind=kind,
            name=name,
        )

    def _check_binary_deps(
        self,
        binary_map: Dict[str, str],
        with_binaries: bool,
        dry_run: bool = False,
        quiet: bool = False,
    ) -> None:
        """Detect optional binaries declared by the bundle and hint or install."""
        if not binary_map:
            return

        missing = detect_missing_binaries(binary_map)
        if not missing:
            return

        if quiet:
            return

        if dry_run:
            print(format_binary_hints(missing))
            return

        if with_binaries:
            results = try_install_binaries(missing)
            still_missing = {
                cmd: pkg for cmd, pkg in missing.items() if not results.get(cmd)
            }
            if still_missing:
                print(format_binary_hints(still_missing))
        else:
            print(format_binary_hints(missing))

    def uninstall(self, name: str, kind: str, remove_deps: bool = False) -> bool:
        """Remove an installed package.

        Args:
            name: Package name.
            kind: ``"agent"``, ``"tool"``, ``"skill"``, or ``"bundle"``.
            remove_deps: Also uninstall Python dependencies that are not used by
                other installed packages.

        Returns:
            True if something was removed, False otherwise.
        """
        kind = _normalize_kind(kind)
        meta = self._find_in_manifest(name, kind)
        if meta is None:
            # Also check for unmanaged directories/files
            if kind == KIND_AGENT:
                path = self.agents_dir / name
            elif kind == KIND_TOOL:
                path = self.tools_dir / name
            elif kind == KIND_SKILL:
                path = self.skills_dir / name
            else:
                return False
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                return True
            return False

        if kind == KIND_BUNDLE and meta.installed_items:
            # Bundle: remove all distributed items
            for item in meta.installed_items:
                if os.path.exists(item.path):
                    if os.path.isdir(item.path):
                        shutil.rmtree(item.path)
                    else:
                        os.remove(item.path)
        else:
            # Single package
            if os.path.exists(meta.install_path):
                if os.path.isdir(meta.install_path):
                    shutil.rmtree(meta.install_path)
                else:
                    os.remove(meta.install_path)

        if remove_deps and meta.installed_dependencies:
            self._remove_unused_dependencies(meta)

        return self._remove_from_manifest(name, kind)

    def _remove_unused_dependencies(self, target: PackageMetadata) -> None:
        """Uninstall deps that are not referenced by any other installed package."""
        other_deps: set = set()
        for pkg in self.list():
            if pkg.name == target.name and pkg.kind == target.kind:
                continue
            for dep in pkg.installed_dependencies:
                name = extract_package_name(dep)
                if name:
                    other_deps.add(name)

        removable: List[str] = []
        for dep in target.installed_dependencies:
            name = extract_package_name(dep)
            if name and name not in other_deps:
                removable.append(name)

        if removable:
            print(f"Removing unused Python dependencies: {', '.join(removable)}")
            uninstall_dependencies(removable)

    # ------------------------------------------------------------------
    # List / info
    # ------------------------------------------------------------------
    def list(self, kind: Optional[str] = None) -> List[PackageMetadata]:
        """Return installed packages.

        Args:
            kind: Filter by ``"agent"``, ``"tool"``, ``"skill"``, ``"bundle"``.
                  ``None`` returns all.
        """
        if kind is not None:
            kind = _normalize_kind(kind)
        data = self._load_manifest()
        results: List[PackageMetadata] = []
        for item in data["packages"]:
            if kind is None or item.get("kind") == kind:
                results.append(PackageMetadata.model_validate(item))

        # Also discover unmanaged directories/files (installed manually)
        if kind in (None, KIND_AGENT):
            for entry in sorted(self.agents_dir.iterdir()):
                if entry.is_dir() and is_agent_dir(str(entry)):
                    if not any(
                        p.name == entry.name and p.kind == KIND_AGENT for p in results
                    ):
                        results.append(
                            PackageMetadata(
                                name=entry.name,
                                version="unknown",
                                description="",
                                kind=KIND_AGENT,
                                source="",
                                install_path=str(entry),
                                installed_at="",
                            )
                        )
        if kind in (None, KIND_TOOL):
            for entry in sorted(self.tools_dir.iterdir()):
                if entry.is_dir() and is_tool_dir(str(entry)):
                    if not any(
                        p.name == entry.name and p.kind == KIND_TOOL for p in results
                    ):
                        results.append(
                            PackageMetadata(
                                name=entry.name,
                                version="unknown",
                                description="",
                                kind=KIND_TOOL,
                                source="",
                                install_path=str(entry),
                                installed_at="",
                            )
                        )
        if kind in (None, KIND_SKILL):
            for entry in sorted(self.skills_dir.iterdir()):
                if entry.is_dir() or entry.is_file():
                    if not any(
                        p.name == entry.name and p.kind == KIND_SKILL for p in results
                    ):
                        results.append(
                            PackageMetadata(
                                name=entry.name,
                                version="unknown",
                                description="",
                                kind=KIND_SKILL,
                                source="",
                                install_path=str(entry),
                                installed_at="",
                            )
                        )
        return results

    def list_by_bundle(self) -> Dict[str, "BundleView"]:
        """Return a grouped view of bundles with their agents, tools, and skills."""
        from nova_harness.core.types.package_manager import BundleView

        all_pkgs = self.list()
        bundles = [p for p in all_pkgs if p.kind == KIND_BUNDLE]
        agents = {p.name: p for p in all_pkgs if p.kind == KIND_AGENT}
        tools = {p.name: p for p in all_pkgs if p.kind == KIND_TOOL}
        skills = {p.name: p for p in all_pkgs if p.kind == KIND_SKILL}

        assigned_agents: set = set()
        assigned_tools: set = set()
        assigned_skills: set = set()
        result: Dict[str, BundleView] = {}

        for bundle in bundles:
            bundle_agents: List[PackageMetadata] = []
            bundle_tools: List[PackageMetadata] = []
            bundle_skills: List[PackageMetadata] = []
            for item in bundle.installed_items:
                if item.kind == KIND_AGENT and item.name in agents:
                    bundle_agents.append(agents[item.name])
                    assigned_agents.add(item.name)
                elif item.kind == KIND_TOOL and item.name in tools:
                    bundle_tools.append(tools[item.name])
                    assigned_tools.add(item.name)
                elif item.kind == KIND_SKILL and item.name in skills:
                    bundle_skills.append(skills[item.name])
                    assigned_skills.add(item.name)
            result[bundle.name] = BundleView(
                name=bundle.name,
                version=bundle.version,
                description=bundle.description,
                agents=bundle_agents,
                tools=bundle_tools,
                skills=bundle_skills,
            )

        # Collect standalone packages
        standalone_agents = [agents[n] for n in agents if n not in assigned_agents]
        standalone_tools = [tools[n] for n in tools if n not in assigned_tools]
        standalone_skills = [skills[n] for n in skills if n not in assigned_skills]
        if standalone_agents or standalone_tools or standalone_skills:
            result["(standalone)"] = BundleView(
                name="(standalone)",
                version="",
                description="Packages not managed by any bundle",
                agents=standalone_agents,
                tools=standalone_tools,
                skills=standalone_skills,
            )

        return result

    def info(self, name: str, kind: str) -> Optional[PackageMetadata]:
        """Get metadata for a single installed package."""
        kind = _normalize_kind(kind)
        return self._find_in_manifest(name, kind)

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------
    def validate(self, source: str, kind: Optional[str] = None) -> List[str]:
        """Validate a package directory and return a list of issues (empty if OK)."""
        source_obj = parse_source(source)
        resolver = SourceResolver(self.agent_dir)
        try:
            local_dir = resolver.resolve(source_obj)
        except ValueError as exc:
            return [str(exc)]

        issues: List[str] = []
        manifest = read_manifest(local_dir)
        resolved_kind = self._resolve_kind(kind, manifest, local_dir)

        if resolved_kind == KIND_AGENT:
            if not is_agent_dir(local_dir):
                issues.append(
                    "Missing agent markers (description.md, setup.md, tools.json, sections/, or package.json)"
                )
        elif resolved_kind == KIND_TOOL:
            if not is_tool_dir(local_dir):
                issues.append(
                    "Missing tool markers (schema.json, executor.py, or package.json)"
                )
        elif resolved_kind == KIND_SKILL:
            if not _is_skill_path(local_dir):
                issues.append("Missing skill marker (SKILL.md)")
        elif resolved_kind == KIND_BUNDLE:
            agent_entries, tool_entries, skill_entries = self._collect_bundle_entries(
                local_dir, manifest
            )
            if not agent_entries and not tool_entries and not skill_entries:
                issues.append(
                    "Bundle must declare agents, tools, or skills in manifest, or contain agents/ / tools/ / skills/ directories"
                )
            for src_path in agent_entries:
                if not is_agent_dir(src_path):
                    issues.append(f"Not a valid agent: {src_path}")
            for src_path in tool_entries:
                if not is_tool_dir(src_path):
                    issues.append(f"Not a valid tool: {src_path}")
            for src_path in skill_entries:
                if not _is_skill_path(src_path):
                    issues.append(f"Not a valid skill: {src_path}")
        else:
            issues.append(
                "Cannot infer package kind. Specify kind='agent', kind='tool', kind='skill', or kind='bundle'."
            )

        return issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_kind(self, kind: Optional[str], manifest, local_dir: str) -> str:
        if kind is not None:
            normalized = _normalize_kind(kind)
            if normalized not in VALID_KINDS:
                raise ValueError(f"Invalid package kind: {kind}")
            if kind != normalized:
                warnings.warn(
                    f"Kind '{kind}' is deprecated, use '{normalized}'.",
                    DeprecationWarning,
                    stacklevel=3,
                )
            return normalized

        if manifest.nova is not None:
            if manifest.nova.agents or manifest.nova.tools or manifest.nova.skills:
                return KIND_BUNDLE

        if manifest.kind in VALID_KINDS:
            return manifest.kind

        inferred = infer_kind(local_dir)
        if inferred in VALID_KINDS:
            return inferred

        raise ValueError(
            f"Cannot infer package kind for '{local_dir}'. "
            "Please specify kind='agent', kind='tool', kind='skill', or kind='bundle'."
        )

    def _install_single(
        self,
        abs_src: str,
        source_obj: PackageSource,
        kind: str,
        name: Optional[str],
        manifest,
        no_deps: bool,
        with_binaries: bool,
        dry_run: bool,
        quiet: bool,
    ) -> PackageMetadata:
        pkg_name = name or manifest.name or _basename(abs_src)
        if not pkg_name:
            raise ValueError("Cannot determine package name from source.")

        if kind == KIND_AGENT:
            dest = self.agents_dir / pkg_name
        elif kind == KIND_TOOL:
            dest = self.tools_dir / pkg_name
        else:
            dest = self.skills_dir / pkg_name

        # Check for collision with unmanaged directory/file
        if dest.exists():
            existing = self._find_in_manifest(pkg_name, kind)
            if existing is None:
                raise FileExistsError(
                    f"A path named '{pkg_name}' already exists in {dest.parent}. "
                    "Remove it first or choose a different name."
                )

        binary_map = manifest.nova.binary_dependencies if manifest.nova else {}

        # Phase 1: resolve and install/check Python dependencies first.
        installed_deps = self._install_deps(
            abs_src, manifest, no_deps, dry_run=dry_run, quiet=quiet
        )

        # Phase 2: check optional binaries.
        self._check_binary_deps(binary_map, with_binaries, dry_run=dry_run, quiet=quiet)

        # Phase 3: preview only in dry-run mode.
        if dry_run:
            if not quiet:
                print(f"[dry-run] Would install single {kind}: {pkg_name} -> {dest}")
            return PackageMetadata(
                name=pkg_name,
                version=manifest.version,
                description=manifest.description,
                kind=kind,
                source=source_obj.spec,
                install_path=str(dest),
                installed_at=now_iso(),
                author=manifest.author,
                dependencies=manifest.dependencies,
                installed_dependencies=installed_deps,
            )

        # Phase 4: copy files with rollback on failure.
        copied_paths: List[str] = []
        try:
            _copy_entry(abs_src, str(dest))
            copied_paths.append(str(dest))
        except Exception:
            self._rollback_copy(copied_paths)
            raise

        meta = PackageMetadata(
            name=pkg_name,
            version=manifest.version,
            description=manifest.description,
            kind=kind,
            source=source_obj.spec,
            install_path=str(dest),
            installed_at=now_iso(),
            author=manifest.author,
            dependencies=manifest.dependencies,
            installed_dependencies=installed_deps,
        )
        self._add_to_manifest(meta)
        return meta

    def _install_bundle(
        self,
        abs_src: str,
        source_obj: PackageSource,
        name: Optional[str],
        manifest,
        no_deps: bool,
        with_binaries: bool,
        dry_run: bool,
        quiet: bool,
    ) -> PackageMetadata:
        pkg_name = name or manifest.name or _basename(abs_src)
        if not pkg_name:
            raise ValueError("Cannot determine bundle package name from source.")

        agent_entries, tool_entries, skill_entries = self._collect_bundle_entries(
            abs_src, manifest
        )
        if not agent_entries and not tool_entries and not skill_entries:
            raise ValueError(
                f"Bundle package '{pkg_name}' has no agents, tools, or skills to install."
            )

        binary_map = manifest.nova.binary_dependencies if manifest.nova else {}

        # Phase 1: resolve and install/check Python dependencies first.
        installed_deps = self._install_deps(
            abs_src, manifest, no_deps, dry_run=dry_run, quiet=quiet
        )

        # Phase 2: check optional binaries.
        self._check_binary_deps(binary_map, with_binaries, dry_run=dry_run, quiet=quiet)

        installed_items: List[InstalledItem] = []

        # Phase 3: preview only in dry-run mode.
        if dry_run:
            for src_path in agent_entries:
                entry_name = _basename(src_path)
                dest = self.agents_dir / entry_name
                installed_items.append(
                    InstalledItem(kind=KIND_AGENT, name=entry_name, path=str(dest))
                )
            for src_path in tool_entries:
                entry_name = _basename(src_path)
                dest = self.tools_dir / entry_name
                installed_items.append(
                    InstalledItem(kind=KIND_TOOL, name=entry_name, path=str(dest))
                )
            for src_path in skill_entries:
                entry_name = _basename(src_path)
                dest = self.skills_dir / entry_name
                installed_items.append(
                    InstalledItem(kind=KIND_SKILL, name=entry_name, path=str(dest))
                )
            if not quiet:
                print(
                    f"[dry-run] Would install bundle {pkg_name} with "
                    f"{len(installed_items)} item(s)"
                )
            return PackageMetadata(
                name=pkg_name,
                version=manifest.version,
                description=manifest.description,
                kind=KIND_BUNDLE,
                source=source_obj.spec,
                install_path=abs_src,
                installed_at=now_iso(),
                author=manifest.author,
                dependencies=manifest.dependencies,
                installed_dependencies=installed_deps,
                installed_items=installed_items,
            )

        # Phase 4: copy files with rollback on failure.
        copied_paths: List[str] = []
        try:
            for src_path in agent_entries:
                entry_name = _basename(src_path)
                dest = self.agents_dir / entry_name
                _remove_if_exists(dest)
                copytree(src_path, str(dest))
                copied_paths.append(str(dest))
                installed_items.append(
                    InstalledItem(kind=KIND_AGENT, name=entry_name, path=str(dest))
                )

            for src_path in tool_entries:
                entry_name = _basename(src_path)
                dest = self.tools_dir / entry_name
                _remove_if_exists(dest)
                copytree(src_path, str(dest))
                copied_paths.append(str(dest))
                installed_items.append(
                    InstalledItem(kind=KIND_TOOL, name=entry_name, path=str(dest))
                )

            for src_path in skill_entries:
                entry_name = _basename(src_path)
                dest = self.skills_dir / entry_name
                _remove_if_exists(dest)
                _copy_entry(src_path, str(dest))
                copied_paths.append(str(dest))
                installed_items.append(
                    InstalledItem(kind=KIND_SKILL, name=entry_name, path=str(dest))
                )
        except Exception:
            self._rollback_copy(copied_paths)
            raise

        meta = PackageMetadata(
            name=pkg_name,
            version=manifest.version,
            description=manifest.description,
            kind=KIND_BUNDLE,
            source=source_obj.spec,
            install_path=abs_src,
            installed_at=now_iso(),
            author=manifest.author,
            dependencies=manifest.dependencies,
            installed_dependencies=installed_deps,
            installed_items=installed_items,
        )
        self._add_to_manifest(meta)
        return meta

    def _collect_bundle_entries(self, abs_src: str, manifest):
        """Return (agent_source_paths, tool_source_paths, skill_source_paths)."""
        base = Path(abs_src)
        agents: List[str] = []
        tools: List[str] = []
        skills: List[str] = []

        if manifest.nova is not None:
            for rel in manifest.nova.agents:
                resolved = (base / rel).resolve()
                if not str(resolved).startswith(str(base)):
                    raise ValueError(f"Agent path escapes package root: {rel}")
                agents.append(str(resolved))
            for rel in manifest.nova.tools:
                resolved = (base / rel).resolve()
                if not str(resolved).startswith(str(base)):
                    raise ValueError(f"Tool path escapes package root: {rel}")
                tools.append(str(resolved))
            for rel in manifest.nova.skills:
                resolved = (base / rel).resolve()
                if not str(resolved).startswith(str(base)):
                    raise ValueError(f"Skill path escapes package root: {rel}")
                skills.append(str(resolved))

        # Fallback: scan agents/ and tools/ directories.
        if not agents:
            agents_dir = base / "agents"
            if agents_dir.is_dir():
                for entry in sorted(agents_dir.iterdir()):
                    if entry.is_dir() and is_agent_dir(str(entry)):
                        agents.append(str(entry))
        if not tools:
            tools_dir = base / "tools"
            if tools_dir.is_dir():
                for entry in sorted(tools_dir.iterdir()):
                    if entry.is_dir() and is_tool_dir(str(entry)):
                        tools.append(str(entry))
        if not skills:
            skills_dir = base / "skills"
            if skills_dir.is_dir():
                for entry in sorted(skills_dir.iterdir()):
                    skills.append(str(entry))

        # Legacy fallback for bundles still using definitions/.
        if not agents:
            legacy_defs_dir = base / "definitions"
            if legacy_defs_dir.is_dir():
                for entry in sorted(legacy_defs_dir.iterdir()):
                    if entry.is_dir() and is_agent_dir(str(entry)):
                        agents.append(str(entry))

        return agents, tools, skills

    def _rollback_copy(self, paths: List[str]) -> None:
        """Remove copied paths when install fails mid-way."""
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def _resolve_deps(
        self, package_dir: str, manifest
    ) -> Tuple[List[str], Optional[str]]:
        """Resolve Python dependency specs and optional requirements.txt path."""
        deps: List[str] = []
        deps.extend(manifest.dependencies)
        deps.extend(read_pyproject_dependencies(package_dir))

        requirements_path = os.path.join(package_dir, "requirements.txt")
        has_requirements = os.path.exists(requirements_path)
        if has_requirements:
            deps.extend(read_requirements(package_dir))

        return deps, requirements_path if has_requirements else None

    def _should_install_deps(self, manifest, no_deps: bool) -> bool:
        if no_deps:
            return False
        if manifest.nova is not None and not manifest.nova.auto_install_dependencies:
            return False
        return True

    def _install_deps(
        self,
        package_dir: str,
        manifest,
        no_deps: bool,
        dry_run: bool = False,
        quiet: bool = False,
    ) -> List[str]:
        """Install Python dependencies and return the list of requested specs.

        In dry-run mode only conflict detection is performed.
        """
        deps, requirements_path = self._resolve_deps(package_dir, manifest)
        if not deps and not requirements_path:
            return deps

        if not self._should_install_deps(manifest, no_deps):
            return deps

        if dry_run:
            ok, output = check_dependency_conflicts(deps, requirements_path)
            if ok:
                if not quiet:
                    print("Python dependency dry-run: OK")
                    if output:
                        print(output)
            else:
                if not quiet:
                    print("Python dependency dry-run: conflicts detected")
                    print(output)
            return deps

        # Conflict pre-check before actual install.
        ok, output = check_dependency_conflicts(deps, requirements_path)
        if not ok:
            raise RuntimeError(
                f"Dependency conflict detected; aborting install.\n{output}"
            )

        install_dependencies(deps, requirements_path=requirements_path)
        return deps


# ------------------------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------------------------
def _basename(path: str) -> str:
    return os.path.basename(os.path.normpath(path))


def _is_skill_path(path: str) -> bool:
    """Check whether *path* is a SKILL.md file or a directory containing one."""
    if os.path.isfile(path) and os.path.basename(path) == "SKILL.md":
        return True
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "SKILL.md"))
    return False


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _copy_entry(src: str, dst: str) -> None:
    """Copy a file or directory to *dst*, removing *dst* if it exists."""
    if os.path.isdir(src):
        copytree(src, dst)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


__all__ = ["PackageManager"]
