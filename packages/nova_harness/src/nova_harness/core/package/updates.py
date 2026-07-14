"""Package update availability checking.

扫描已配置包，检查 git 来源是否有新的远程提交，返回可更新列表。
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from nova_harness.core.package.locator import _git_env
from nova_harness.core.package.source import PackageSource, _source_str, parse_source
from nova_harness.core.package.store import _install_path_for_source
from nova_harness.core.package.utils.offline import is_offline_mode_enabled
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package_manager import PackageUpdate, SourceScope

logger = logging.getLogger(__name__)

# 并发检查数
UPDATE_CHECK_CONCURRENCY = 4

# 默认 git 命令超时（秒）
GIT_REMOTE_TIMEOUT = 30


def _is_pinned_git_ref(ref: Optional[str]) -> bool:
    """Return True when *ref* looks like a full commit SHA (pinned)."""
    if not ref:
        return False
    return bool(re.fullmatch(r"[0-9a-f]{40}", ref.lower()))


def _git_run(
    args: List[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
    capture_output: bool = False,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a git subprocess with non-interactive env and timeout."""
    return subprocess.run(
        args,
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=_git_env(),
        timeout=timeout or GIT_REMOTE_TIMEOUT,
    )


def _git_local_head(cache_dir: Path) -> Optional[str]:
    """Return the local HEAD commit hash, or None if not a valid repo."""
    try:
        result = _git_run(
            ["git", "rev-parse", "HEAD"],
            cwd=cache_dir,
            check=True,
            capture_output=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _git_upstream_ref(cache_dir: Path) -> Optional[str]:
    """Return the upstream ref like ``refs/heads/main`` if tracked."""
    try:
        result = _git_run(
            ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
            cwd=cache_dir,
            check=True,
            capture_output=True,
        )
        upstream = result.stdout.strip()
        if upstream.startswith("origin/"):
            return f"refs/heads/{upstream[7:]}"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _git_remote_head(cache_dir: Path, ref: Optional[str]) -> Optional[str]:
    """Return the remote commit hash for *ref* or the repository default HEAD."""
    refs_to_try: List[str] = []
    if ref and not _is_pinned_git_ref(ref):
        refs_to_try.append(ref)
    upstream = _git_upstream_ref(cache_dir)
    if upstream:
        refs_to_try.append(upstream)
    refs_to_try.append("HEAD")

    for remote_ref in refs_to_try:
        try:
            result = _git_run(
                ["git", "ls-remote", "origin", remote_ref],
                cwd=cache_dir,
                check=True,
                capture_output=True,
            )
            match = re.search(r"^([0-9a-f]{40})\s+", result.stdout, re.MULTILINE)
            if match:
                return match.group(1)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _check_git_update(
    source: PackageSource,
    path_root: Path,
    git_root: Path,
) -> Optional[PackageUpdate]:
    """Check whether a git package has remote commits not present locally."""
    if not source.host or not source.repo_path:
        return None

    install_path = _install_path_for_source(source, "", path_root, git_root)
    if not (install_path / ".git").exists():
        return None

    local_head = _git_local_head(install_path)
    if not local_head:
        return None

    remote_head = _git_remote_head(install_path, source.ref)
    if not remote_head:
        return None

    if local_head == remote_head:
        return None

    display_name = f"{source.host}/{source.repo_path}"
    return PackageUpdate(
        source=source.spec,
        display_name=display_name,
        type="git",
        scope=SourceScope.USER,  # caller overrides when needed
    )


def _package_update_scope(
    update: PackageUpdate,
    scope: SourceScope,
) -> PackageUpdate:
    """Return a copy of *update* with scope set."""
    return PackageUpdate(
        source=update.source,
        display_name=update.display_name,
        type=update.type,
        scope=scope,
    )


async def check_for_available_updates(
    scoped_sources: List[Tuple[SourceScope, PackageSourceSpec, Path, Path]],
    *,
    max_concurrency: int = UPDATE_CHECK_CONCURRENCY,
) -> List[PackageUpdate]:
    """Check configured packages for available updates.

    Args:
        scoped_sources: List of ``(scope, spec, path_root, git_root)`` tuples.
        max_concurrency: Maximum concurrent remote checks.

    Returns:
        A list of packages that have updates available. Path sources and
        pinned git commits are skipped.
    """
    if is_offline_mode_enabled():
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _check_one(
        scope: SourceScope,
        spec: PackageSourceSpec,
        path_root: Path,
        git_root: Path,
    ) -> Optional[PackageUpdate]:
        source_str = _source_str(spec)
        source_obj = parse_source(source_str)

        if source_obj.type == "path":
            return None
        if source_obj.type == "git" and _is_pinned_git_ref(source_obj.ref):
            return None

        async with semaphore:
            try:
                update = await asyncio.to_thread(
                    _check_git_update, source_obj, path_root, git_root
                )
            except Exception as exc:
                logger.debug("Failed to check updates for %s: %s", source_str, exc)
                return None

        if update is None:
            return None
        return _package_update_scope(update, scope)

    tasks = [
        _check_one(scope, spec, path_root, git_root)
        for scope, spec, path_root, git_root in scoped_sources
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    updates: List[PackageUpdate] = []
    for result in results:
        if isinstance(result, PackageUpdate):
            updates.append(result)
        elif isinstance(result, Exception):
            logger.debug("Update check task failed: %s", result)
    return updates


__all__ = ["check_for_available_updates", "UPDATE_CHECK_CONCURRENCY"]
