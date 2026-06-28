"""Package source resolution.

Supports::

    local:/absolute/path
    local:./relative/path
    /absolute/path                (implicit local)
    ./relative/path               (implicit local)
    git:github.com/user/repo@ref
    git:git@github.com:user/repo.git@ref
    https://github.com/user/repo
    https://github.com/user/repo@ref

Resolved sources are cached under ``<agent_dir>/git/<host>/<path>/``.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


@dataclass
class PackageSource:
    """A normalized package source specification."""

    type: str  # "local" | "git"
    spec: str  # original / canonical spec string
    path: Optional[str] = None  # for local: absolute or relative path
    remote_url: Optional[str] = None  # for git: clone URL
    host: Optional[str] = None  # for git: host part, used for cache dir
    repo_path: Optional[str] = None  # for git: "user/repo" part
    ref: Optional[str] = None  # for git: branch/tag/commit


def parse_source(spec: str) -> PackageSource:
    """Parse a source specification into a PackageSource."""
    spec = spec.strip()

    if spec.startswith("local:"):
        return PackageSource(type="local", spec=spec, path=spec[6:].strip())

    if spec.startswith("git:"):
        return _parse_git_spec(spec[4:].strip(), original=spec)

    if spec.startswith(("http://", "https://")):
        return _parse_git_spec(spec, original=spec)

    # Default to local filesystem path.
    return PackageSource(type="local", spec=spec, path=spec)


def _parse_git_spec(rest: str, original: str) -> PackageSource:
    """Parse the git-specific portion of a source spec."""
    # Split trailing @ref if present. Avoid splitting on @ inside URL credentials.
    ref: Optional[str] = None
    if "@" in rest:
        # Find the rightmost @ that is not part of "git@" prefix or credentials.
        parts = rest.rsplit("@", 1)
        candidate_ref = parts[1]
        # If the candidate contains / or : it is likely part of the URL, not a ref.
        if "/" not in candidate_ref and ":" not in candidate_ref:
            rest = parts[0]
            ref = candidate_ref

    # SCP-like: git@github.com:user/repo.git
    scp_match = re.match(r"git@([^:]+):(.+)$", rest)
    if scp_match:
        host = scp_match.group(1)
        repo_path = _normalize_repo_path(scp_match.group(2))
        remote_url = f"git@{host}:{repo_path}"
        return PackageSource(
            type="git",
            spec=original,
            remote_url=remote_url,
            host=host,
            repo_path=repo_path,
            ref=ref,
        )

    # Plain host/path or full URL.
    parsed = urlparse(rest)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc
        repo_path = parsed.path.lstrip("/")
        remote_url = rest
    else:
        # e.g. github.com/user/repo
        segments = rest.split("/")
        if len(segments) < 2:
            raise ValueError(f"Invalid git source: {original}")
        host = segments[0]
        repo_path = "/".join(segments[1:])
        remote_url = f"https://{rest}"

    repo_path = _normalize_repo_path(repo_path)
    return PackageSource(
        type="git",
        spec=original,
        remote_url=remote_url,
        host=host,
        repo_path=repo_path,
        ref=ref,
    )


def _normalize_repo_path(repo_path: str) -> str:
    """Strip .git suffix and leading/trailing slashes from a repo path."""
    repo_path = repo_path.strip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return repo_path


class SourceResolver:
    """Resolve a PackageSource to a local directory."""

    def __init__(self, agent_dir: Path, local: bool = False) -> None:
        self.agent_dir = Path(agent_dir)
        self.local = local
        self.git_root = self.agent_dir / "packages" / "git"

    def resolve(self, source: PackageSource) -> str:
        """Resolve *source* and return the absolute path to a local directory."""
        if source.type == "local":
            return self._resolve_local(source)
        if source.type == "git":
            return self._resolve_git(source)
        raise ValueError(f"Unsupported source type: {source.type}")

    def _resolve_local(self, source: PackageSource) -> str:
        path = source.path or ""
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            raise ValueError(f"Local source not found: {source.spec}")
        if not os.path.isdir(abs_path):
            raise ValueError(f"Local source is not a directory: {source.spec}")
        return abs_path

    def _resolve_git(self, source: PackageSource) -> str:
        if not source.host or not source.repo_path:
            raise ValueError(f"Invalid git source: {source.spec}")

        cache_dir = self.git_root / source.host / source.repo_path
        cache_dir.mkdir(parents=True, exist_ok=True)

        if not (cache_dir / ".git").exists():
            self._git_clone(source.remote_url or source.spec, cache_dir, source.ref)
        else:
            self._git_update(cache_dir, source.ref)

        return str(cache_dir)

    def _git_clone(self, remote_url: str, cache_dir: Path, ref: Optional[str]) -> None:
        # Clone into a temp sibling directory and move on success, so a failed
        # clone does not leave a dirty cache_dir behind.
        temp_dir = cache_dir.with_name(f"{cache_dir.name}.clone-tmp")
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        try:
            cmd = ["git", "clone", "--quiet", remote_url, str(temp_dir)]
            if ref and self._looks_like_branch_or_tag(ref):
                cmd[2:2] = ["--branch", ref, "--single-branch"]
            subprocess.run(cmd, check=True)

            if ref and not self._looks_like_branch_or_tag(ref):
                # ref is likely a commit SHA; check it out after clone.
                subprocess.run(
                    ["git", "-C", str(temp_dir), "checkout", "--quiet", ref],
                    check=True,
                )

            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            shutil.move(str(temp_dir), str(cache_dir))
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            raise

    def _git_update(self, cache_dir: Path, ref: Optional[str]) -> None:
        try:
            subprocess.run(
                ["git", "-C", str(cache_dir), "fetch", "--quiet", "origin"],
                check=True,
            )
        except subprocess.CalledProcessError:
            # fetch may fail offline; still attempt checkout if ref exists.
            pass

        if ref:
            subprocess.run(
                ["git", "-C", str(cache_dir), "checkout", "--quiet", ref],
                check=True,
            )
        else:
            # No ref requested: stay on the default branch and pull.
            subprocess.run(
                ["git", "-C", str(cache_dir), "pull", "--quiet", "origin"],
                check=True,
            )

    @staticmethod
    def _looks_like_branch_or_tag(ref: str) -> bool:
        """Heuristic: commit SHAs are 7-40 hex chars; everything else is a branch/tag."""
        return not (
            len(ref) >= 7
            and len(ref) <= 40
            and all(c in "0123456789abcdefABCDEF" for c in ref)
        )


__all__ = ["PackageSource", "SourceResolver", "parse_source"]
