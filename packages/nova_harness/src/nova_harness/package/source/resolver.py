"""Source resolution: turn a PackageSource into a local directory.

Git sources are cached under ``<agent_dir>/packages/git/<host>/<path>/``.
npm sources are downloaded to ``<agent_dir>/packages/npm/<safe_name>/``
（同名只留一份——更新即替换；版本记录在 dist-info 快照）。
Path sources are resolved to their absolute path.
Editable sources are referenced in place and are never copied.
"""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from nova_harness.core.config.defaults import (
    GIT_PACKAGES_DIR_NAME,
    NPM_PACKAGES_DIR_NAME,
    PACKAGES_DIR_NAME,
)
from nova_harness.core.types.package import ProgressEvent
from nova_harness.package.source._semver import (
    NpmRange,
    NpmRangeUnion,
    max_satisfying,
    parse_version_spec,
)
from nova_harness.package.source.spec import PackageSource
from nova_harness.package.utils import is_offline_mode_enabled

# Git 命令默认超时（秒）。克隆/更新在网络异常时不应无限 hang 住。
GIT_COMMAND_TIMEOUT = 60

# clone / fetch 等真实网络传输的超时（秒）：大仓库可能远超查询类命令。
GIT_NETWORK_TIMEOUT = 300


def git_env() -> dict:
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


def npm_registry_base() -> str:
    """registry 基址：NPM_CONFIG_REGISTRY 环境变量优先（镜像生态惯例）。"""
    return os.environ.get("NPM_CONFIG_REGISTRY", "https://registry.npmjs.org").rstrip(
        "/"
    )


def npm_fetch_json(url: str) -> dict:
    """GET JSON（registry 元数据查询——resolver 与 updates 共用）。"""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "nova-pkg"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class SourceResolver:
    """Resolve a PackageSource to a local directory."""

    def __init__(
        self,
        agent_dir: Path,
        cwd: Optional[Path] = None,
        on_progress: Optional[Callable[[ProgressEvent], None]] = None,
    ) -> None:
        self.agent_dir = Path(agent_dir)
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.git_root = self.agent_dir / PACKAGES_DIR_NAME / GIT_PACKAGES_DIR_NAME
        self._on_progress = on_progress

    def set_progress_callback(
        self, on_progress: Optional[Callable[[ProgressEvent], None]]
    ) -> None:
        """设置/替换进度回调。"""
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
        kwargs.setdefault("env", git_env())
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

    def _git_rev_parse(self, cache_dir: Path, ref: str) -> Optional[str]:
        """把 *ref* 解析为 commit hash；解析失败返回 ``None``。"""
        try:
            result = self._git_run(
                ["git", "-C", str(cache_dir), "rev-parse", ref],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return None
        return (result.stdout or "").strip() or None

    def _git_head_matches(self, cache_dir: Path, ref: str) -> bool:
        """HEAD 已等于 *ref* 时返回 True。

        用于 up-to-date 短路（对齐 TS ``ensureGitRef``）：目标 commit 与
        HEAD 相同则跳过 ``reset --hard`` 与 ``clean -fdx``，避免对缓存目录
        做无谓的写操作。
        """
        head = self._git_rev_parse(cache_dir, "HEAD")
        target = self._git_rev_parse(cache_dir, ref)
        return head is not None and head == target

    def resolve(self, source: PackageSource, *, update: bool = False) -> str:
        """Resolve *source* and return the absolute path to a local directory.

        ``update=False``（默认）是纯只读：path 源只验证存在性，git 源只返回
        已存在的缓存——缓存缺失即报错，**绝不触发任何网络操作**。clone 与
        远端同步只属于安装/更新流程（``update=True``）。
        """
        if source.type == "path":
            return self._resolve_local(source)
        if source.type == "git":
            return self._resolve_git(source, update=update)
        if source.type == "npm":
            return self._resolve_npm(source, update=update)
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

    def _resolve_git(self, source: PackageSource, *, update: bool = False) -> str:
        if not source.host or not source.repo_path:
            raise ValueError(f"Invalid git source: {source.spec}")

        cache_dir = self.git_root / source.host / source.repo_path

        if (cache_dir / ".git").exists():
            if update:
                self._git_update(cache_dir, source)
            return str(cache_dir)

        # 只读解析（update=False）：缓存缺失即"未安装"，直接报错——clone
        # 只属于安装/更新流程。资源解析、离线模式与 validate 之外的查询
        # 路径都依赖这一保证（不触网）。
        if not update:
            raise ValueError(f"Git source is not installed: {source.spec}")

        # 缓存不存在时才 clone；clone 内部用临时目录 + move，失败不留残骸。
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._git_clone(
            source.remote_url or source.spec, cache_dir, source.spec, source.ref
        )
        return str(cache_dir)

    # ------------------------------------------------------------------
    # npm 源（registry metadata → tarball 下载校验解压；缓存目录即安装态）
    # ------------------------------------------------------------------

    @property
    def npm_root(self) -> Path:
        return self.agent_dir / PACKAGES_DIR_NAME / NPM_PACKAGES_DIR_NAME

    @staticmethod
    def _npm_safe_name(name: str) -> str:
        """npm 包名 → 目录名（@scope/name → scope__name）。"""
        return name.replace("/", "__").lstrip("@").replace("@", "")

    def _resolve_npm(self, source: PackageSource, *, update: bool = False) -> str:
        if not source.npm_name:
            raise ValueError(f"Invalid npm source: {source.spec}")
        cache_dir = self.npm_root / self._npm_safe_name(source.npm_name)

        # 只读解析（update=False）：缓存存在即用，不触网
        if cache_dir.exists() and (cache_dir / "package.json").exists():
            if not update:
                return str(cache_dir)
            shutil.rmtree(cache_dir)
        elif not update:
            raise ValueError(f"npm source is not installed: {source.spec}")

        if is_offline_mode_enabled():
            raise ValueError(
                f"Cannot download npm package in offline mode: {source.spec}"
            )

        registry = npm_registry_base()
        self._emit_progress(
            "start", "npm", source.spec, f"Resolving {source.npm_name}..."
        )
        # ① metadata：dist-tags.latest / 精确版本直取 / range 求 max satisfying
        try:
            metadata = npm_fetch_json(f"{registry}/{source.npm_name}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Failed to query npm registry for {source.npm_name}: {exc}"
            ) from exc
        versions = metadata.get("versions") or {}
        dist_tags = metadata.get("dist-tags") or {}
        version = source.npm_version
        if version is None:
            version = dist_tags.get("latest")
        else:
            if version in dist_tags:
                # dist-tag 直取（beta/next/canary——npm 约定 tag 名不与
                # semver 冲突，命中即解析为具体版本）
                version = dist_tags[version]
            else:
                try:
                    version_spec = parse_version_spec(version)
                except ValueError:
                    # 既不是已注册 dist-tag 也不是合法版本语法——报出可用
                    # tag 清单，避免"语法错误"式的不可行动报错
                    available = ", ".join(sorted(dist_tags)) or "none"
                    raise ValueError(
                        f"npm dist-tag or version not found: "
                        f"{source.npm_name}@{version} "
                        f"(available dist-tags: {available})"
                    ) from None
                if isinstance(version_spec, (NpmRange, NpmRangeUnion)):
                    # range / 并集 / 裸部分版本 / 通配：按 semver precedence 选 max
                    # satisfying（精确版本不走这里——保持原 versions 直取逻辑，
                    # prerelease 精确版同理）
                    selected = max_satisfying(versions.keys(), version_spec)
                    if selected is None:
                        latest = dist_tags.get("latest")
                        raise ValueError(
                            f"No npm version satisfies {source.npm_name}@{version}"
                            + (f" (latest available: {latest})" if latest else "")
                        )
                    version = selected
                else:
                    # 精确版本：归一（v 前缀 / build metadata）后按 registry 键直取
                    version = str(version_spec)
        entry = versions.get(version or "")
        if entry is None:
            raise ValueError(
                f"npm version not found: {source.npm_name}@{version or 'latest'}"
            )
        dist = entry.get("dist") or {}
        tarball_url = dist.get("tarball")
        integrity = dist.get("integrity")  # "sha512-<base64>"
        if not tarball_url:
            raise ValueError(f"npm metadata missing tarball: {source.spec}")

        # ② 下载 + integrity 校验
        self._emit_progress(
            "progress",
            "npm",
            source.spec,
            f"Downloading {source.npm_name}@{version}...",
        )
        try:
            with urllib.request.urlopen(tarball_url, timeout=120) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise ValueError(f"Failed to download {tarball_url}: {exc}") from exc
        if integrity and integrity.startswith("sha512-"):
            expected = integrity[len("sha512-") :]
            actual = base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")
            if actual != expected:
                raise ValueError(f"npm integrity mismatch: {source.spec}")

        # ③ 解压（npm tarball 内容在 package/ 前缀下——剥离）；临时目录 + move 防残骸
        self.npm_root.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="nova-npm-", dir=str(self.npm_root)))
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
                for member in tar.getmembers():
                    # 防路径逃逸（恶意 tarball）
                    target = (temp_dir / member.name).resolve()
                    if not str(target).startswith(str(temp_dir.resolve())):
                        raise ValueError(f"Unsafe path in npm tarball: {member.name}")
                tar.extractall(temp_dir, filter="data")
            package_dir = temp_dir / "package"
            extracted = package_dir if package_dir.is_dir() else temp_dir
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(extracted), str(cache_dir))
            if extracted is package_dir:
                # move 走了 package/ 子目录——清掉临时壳
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        self._emit_progress(
            "complete", "npm", source.spec, f"Installed {source.npm_name}@{version}"
        )
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
                timeout=GIT_NETWORK_TIMEOUT,
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
                # 显式 ref：在线时抓取该 ref 并 reset 到 FETCH_HEAD。
                if not offline:
                    # 在线抓取失败是硬错误（对齐 TS ensureGitRef）——不能
                    # 静默回退到本地陈旧 ref 还报告"Updated"。只有离线模式
                    # 才回退本地 ref。
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
                        timeout=GIT_NETWORK_TIMEOUT,
                    )
                    if self._git_head_matches(cache_dir, "FETCH_HEAD^{commit}"):
                        self._emit_progress(
                            "complete",
                            "pull",
                            source_spec,
                            f"{source_spec} is up to date",
                        )
                        return
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

                # 离线：尽量使用本地 ref。
                self._emit_progress(
                    "progress",
                    "pull",
                    source_spec,
                    f"Using local ref {source.ref}...",
                )
                if self._git_head_matches(cache_dir, f"{source.ref}^{{commit}}"):
                    self._emit_progress(
                        "complete", "pull", source_spec, f"{source_spec} is up to date"
                    )
                    return
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
                # 在线 fetch 失败同样是硬错误（对齐 TS runCommand 抛错）。
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
                    timeout=GIT_NETWORK_TIMEOUT,
                )

            if not self._git_ref_exists(cache_dir, target_ref):
                target_ref = "HEAD"

            if not self._git_head_matches(cache_dir, f"{target_ref}^{{commit}}"):
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


__all__ = ["SourceResolver", "git_env"]
