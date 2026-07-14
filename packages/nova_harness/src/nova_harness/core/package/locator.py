"""Source resolution: turn a PackageSource into a local directory.

Git sources are cached under ``<agent_dir>/packages/git/<host>/<path>/``.
Path sources are resolved to their absolute path.
Editable sources are referenced in place and are never copied.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from nova_harness.core.config.defaults import (
    GIT_PACKAGES_DIR_NAME,
    PACKAGES_DIR_NAME,
)
from nova_harness.core.package.source import PackageSource
from nova_harness.core.package.utils.offline import is_offline_mode_enabled
from nova_harness.core.types.package_manager import ProgressEvent

# Git 命令默认超时（秒）。克隆/更新在网络异常时不应无限 hang 住。
GIT_COMMAND_TIMEOUT = 60


def _git_env() -> dict:
    """返回用于 Git 子进程的环境变量。

    - ``GIT_TERMINAL_PROMPT=0`` 禁用交互式密码提示，避免 headless/RPC/TUI
      场景阻塞。
    - ``GIT_SSH_COMMAND`` 使用 BatchMode，避免 SSH 认证弹窗。
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    ssh_command = env.get("GIT_SSH_COMMAND", "ssh")
    if "BatchMode" not in ssh_command:
        ssh_command = f"{ssh_command} -o BatchMode=yes"
    env["GIT_SSH_COMMAND"] = ssh_command
    return env


class SourceResolver:
    """Resolve a PackageSource to a local directory."""

    def __init__(
        self,
        agent_dir: Path,
        cwd: Optional[Path] = None,
        local: bool = False,
        on_progress: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> None:
        self.agent_dir = Path(agent_dir)
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.local = local
        self.git_root = self.agent_dir / PACKAGES_DIR_NAME / GIT_PACKAGES_DIR_NAME
        self._on_progress = on_progress

    def _emit_progress(
        self,
        event_type: str,
        action: str,
        source: str,
        message: str,
        percent: Optional[float] = None,
    ) -> None:
        """Emit a progress event if a callback is registered."""
        if self._on_progress is not None:
            self._on_progress(
                ProgressEvent(
                    type=event_type,
                    action=action,
                    source=source,
                    message=message,
                    percent=percent,
                )
            )

    def _git_run(
        self,
        args: list,
        *,
        check: bool = True,
        capture_output: bool = False,
        text: bool = False,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> subprocess.CompletedProcess:
        """Run a git subprocess with consistent timeout and non-interactive env."""
        if timeout is None:
            timeout = GIT_COMMAND_TIMEOUT
        kwargs.setdefault("env", _git_env())
        return subprocess.run(
            args,
            check=check,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            **kwargs,
        )

    def _git_ref_exists(self, cache_dir: Path, ref: str) -> bool:
        """检查 *ref* 是否能在本地仓库中解析为有效对象。"""
        try:
            self._git_run(
                [
                    "git",
                    "-C",
                    str(cache_dir),
                    "rev-parse",
                    "--verify",
                    f"{ref}^{{commit}}",
                ],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def resolve(self, source: PackageSource) -> str:
        """Resolve *source* and return the absolute path to a local directory."""
        if source.type == "path":
            return self._resolve_local(source)
        if source.type == "git":
            return self._resolve_git(source)
        raise ValueError(f"Unsupported source type: {source.type}")

    def _resolve_local(self, source: PackageSource) -> str:
        path = source.path or ""
        # 先展开 ~，再判断是绝对路径还是相对路径。
        expanded = Path(os.path.expanduser(path))
        if expanded.is_absolute():
            abs_path = expanded
        else:
            abs_path = self.cwd / expanded
        abs_path = abs_path.resolve()
        if not abs_path.exists():
            raise ValueError(f"Local source not found: {source.spec}")
        if not abs_path.is_dir():
            raise ValueError(f"Local source is not a directory: {source.spec}")
        return str(abs_path)

    def _resolve_git(self, source: PackageSource) -> str:
        if not source.host or not source.repo_path:
            raise ValueError(f"Invalid git source: {source.spec}")

        cache_dir = self.git_root / source.host / source.repo_path
        cache_dir.mkdir(parents=True, exist_ok=True)

        if not (cache_dir / ".git").exists():
            self._git_clone(
                source.remote_url or source.spec, cache_dir, source.spec, source.ref
            )
        else:
            self._git_update(cache_dir, source)

        return str(cache_dir)

    def _git_clone(
        self, remote_url: str, cache_dir: Path, source_spec: str, ref: Optional[str]
    ) -> None:
        # Clone into a temp sibling directory and move on success, so a failed
        # clone does not leave a dirty cache_dir behind.
        temp_dir = cache_dir.with_name(f"{cache_dir.name}.clone-tmp")
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        self._emit_progress("start", "clone", source_spec, f"Cloning {source_spec}...")
        try:
            self._git_run(
                ["git", "clone", "--quiet", remote_url, str(temp_dir)],
                check=True,
            )

            if ref:
                self._emit_progress(
                    "progress",
                    "clone",
                    source_spec,
                    f"Checking out {ref}...",
                )
                self._git_run(
                    ["git", "-C", str(temp_dir), "checkout", "--quiet", ref],
                    check=True,
                )

            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            shutil.move(str(temp_dir), str(cache_dir))
            self._emit_progress(
                "complete", "clone", source_spec, f"Cloned {source_spec}"
            )
        except Exception as exc:
            self._emit_progress(
                "error", "clone", source_spec, f"Failed to clone {source_spec}: {exc}"
            )
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            raise

    def _git_update(self, cache_dir: Path, source: PackageSource) -> None:
        """Update a cached git source to its latest remote target.

        - fetch 使用 ``--prune --no-tags``，避免累积失效 remote ref。
        - 无显式 ref 时优先跟踪当前分支的 ``@{upstream}``；失败时回退到
          ``origin/HEAD``（先执行 ``remote set-head origin -a``）。
        - reset 目标统一用 ``<ref>^{commit}`` 解析，确保落到精确 commit。
        """
        self._ensure_git_remote(cache_dir, source)
        offline = is_offline_mode_enabled()
        source_spec = source.spec

        self._emit_progress("start", "pull", source_spec, f"Updating {source_spec}...")
        try:
            if source.ref:
                # 显式 ref：在线时抓取该 ref 并 reset 到 FETCH_HEAD；离线/失败时回退本地 ref。
                if not offline:
                    try:
                        self._emit_progress(
                            "progress",
                            "pull",
                            source_spec,
                            f"Fetching {source.ref}...",
                        )
                        self._git_run(
                            [
                                "git",
                                "-C",
                                str(cache_dir),
                                "fetch",
                                "--prune",
                                "--no-tags",
                                "origin",
                                source.ref,
                            ],
                            check=True,
                        )
                        self._git_run(
                            [
                                "git",
                                "-C",
                                str(cache_dir),
                                "reset",
                                "--hard",
                                "--quiet",
                                "FETCH_HEAD^{commit}",
                            ],
                            check=True,
                        )
                        self._git_clean(cache_dir)
                        self._emit_progress(
                            "complete", "pull", source_spec, f"Updated {source_spec}"
                        )
                        return
                    except subprocess.CalledProcessError:
                        pass

                # 离线或 fetch 失败：尽量使用本地 ref。
                self._emit_progress(
                    "progress",
                    "pull",
                    source_spec,
                    f"Using local ref {source.ref}...",
                )
                self._git_run(
                    ["git", "-C", str(cache_dir), "checkout", "--quiet", source.ref],
                    check=True,
                )
                self._git_run(
                    ["git", "-C", str(cache_dir), "reset", "--hard", "--quiet"],
                    check=True,
                )
                self._git_clean(cache_dir)
                self._emit_progress(
                    "complete", "pull", source_spec, f"Updated {source_spec}"
                )
                return

            # 无显式 ref：跟踪 upstream。
            target = self._git_update_target(cache_dir)
            target_ref = target["ref"]

            if not offline:
                try:
                    self._emit_progress(
                        "progress", "pull", source_spec, "Fetching latest changes..."
                    )
                    self._git_run(
                        [
                            "git",
                            "-C",
                            str(cache_dir),
                            "fetch",
                            "--prune",
                            "--no-tags",
                            "origin",
                        ]
                        + target["fetch_refspec"],
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    pass

            if not self._git_ref_exists(cache_dir, target_ref):
                target_ref = "HEAD"

            self._git_run(
                [
                    "git",
                    "-C",
                    str(cache_dir),
                    "reset",
                    "--hard",
                    "--quiet",
                    f"{target_ref}^{{commit}}",
                ],
                check=True,
            )
            self._git_clean(cache_dir)
            self._emit_progress(
                "complete", "pull", source_spec, f"Updated {source_spec}"
            )
        except Exception as exc:
            self._emit_progress(
                "error", "pull", source_spec, f"Failed to update {source_spec}: {exc}"
            )
            raise

    def _git_update_target(self, cache_dir: Path) -> dict:
        """Resolve the reset target for a branch-tracking git update."""
        try:
            upstream = self._git_run(
                [
                    "git",
                    "-C",
                    str(cache_dir),
                    "rev-parse",
                    "--abbrev-ref",
                    "@{upstream}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if upstream.startswith("origin/"):
                branch = upstream[len("origin/") :]
                return {
                    "ref": "@{upstream}",
                    "fetch_refspec": [
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
                    ],
                }
        except subprocess.CalledProcessError:
            pass

        # 回退：刷新 origin/HEAD 并解析默认分支。
        try:
            self._git_run(
                [
                    "git",
                    "-C",
                    str(cache_dir),
                    "remote",
                    "set-head",
                    "origin",
                    "-a",
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass

        try:
            origin_head_ref = self._git_run(
                [
                    "git",
                    "-C",
                    str(cache_dir),
                    "symbolic-ref",
                    "refs/remotes/origin/HEAD",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            branch = origin_head_ref.replace("refs/remotes/origin/", "")
            if branch:
                return {
                    "ref": "origin/HEAD",
                    "fetch_refspec": [
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
                    ],
                }
        except subprocess.CalledProcessError:
            pass

        return {
            "ref": "origin/HEAD",
            "fetch_refspec": ["+HEAD:refs/remotes/origin/HEAD"],
        }

    def _git_clean(self, cache_dir: Path) -> None:
        """Remove untracked files so the checkout is pristine."""
        self._git_run(
            ["git", "-C", str(cache_dir), "clean", "-fdx", "--quiet"],
            check=True,
        )

    def _ensure_git_remote(self, cache_dir: Path, source: PackageSource) -> None:
        """Ensure the ``origin`` remote points to the URL declared by *source*."""
        expected = source.remote_url
        if not expected:
            return
        try:
            result = self._git_run(
                ["git", "-C", str(cache_dir), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            )
            current = result.stdout.strip()
            if current == expected:
                return
            self._git_run(
                ["git", "-C", str(cache_dir), "remote", "set-url", "origin", expected],
                check=True,
            )
        except subprocess.CalledProcessError:
            # origin may not exist yet; add it.
            self._git_run(
                ["git", "-C", str(cache_dir), "remote", "add", "origin", expected],
                check=True,
            )


__all__ = ["SourceResolver", "_git_env"]
