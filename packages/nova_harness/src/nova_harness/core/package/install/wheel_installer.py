"""免 pip 的 wheel 安装通道（冻结形态无 pip 宿主时的兜底）。

冻结二进制不内嵌 pip，但 wheel 本质是 zip——本模块自己完成全链：
PyPI JSON 解析版本与平台 wheel → 下载 + sha256 校验（数据源自带摘要）
→ 安全解包进 ``.site/``（wheel 布局即 site-packages 布局，dist-info 随行）
→ 按 METADATA ``Requires-Dist`` 递归解析运行期依赖。

与 pip 的能力边界（刻意收窄，不逐一对齐）：
- 只装 ``bdist_wheel``，不构建 sdist（无编译链的冻结形态本来也构建不了）；
- 环境 marker 全量评估（``sys_platform``/``python_version`` 等），
  ``extra`` 门控依赖按调用方声明的 extras 求值（与 pip 装 ``name[extra]``
  语义一致）；
- 不做冲突 dry-run（有 pip 宿主时不会走本通道，冲突检查归宿主 pip）；
- 平台 tag 用 ``packaging.tags.sys_tags()``（manylinux/macOS 版本阶梯/
  musl 判别与 pip 同数据）。

失败一律抛 ``WheelInstallError``——调用方（安装器）警告降级，不阻断
包安装本身（依赖缺失的工具在运行时再报错，与缺二进制的降级语义对齐）。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from nova_harness.core.package.binaries.manager import is_offline_mode_enabled
from nova_harness.core.utils.http import default_ssl_context

logger = logging.getLogger(__name__)

_PYPI_JSON = "https://pypi.org/pypi/{name}/json"
_DOWNLOAD_TIMEOUT_S = 600
_install_lock = threading.Lock()


class WheelInstallError(RuntimeError):
    """wheel 通道安装失败（离线/网络/无匹配 wheel/校验失败/规格不支持）。"""


def install_wheels(
    targets: List[str],
    *,
    site_dir: str,
    requirements_path: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[str]:
    """把 requirement 规格列表装进 *site_dir*，返回装好的 ``name==version`` 列表。

    非 requirement 形态的目标（``-e <path>`` / 路径 / 选项行）静默跳过——
    冻结形态下 path 依赖由 sys.path 挂载承载，与本通道无关。
    """
    requirements = _parse_requirements(targets, requirements_path)
    if not requirements:
        return []
    if is_offline_mode_enabled():
        raise WheelInstallError("NOVA_OFFLINE 已启用——wheel 通道需要网络访问 PyPI")

    os.makedirs(site_dir, exist_ok=True)
    installed: List[str] = []
    # (requirement, 该 requirement 自身声明的 extras) 工作队列：递归解析
    # 运行期依赖；canonical name + extras 组合去重
    queue: List[Tuple[Requirement, Tuple[str, ...]]] = [
        (req, tuple(sorted(req.extras))) for req in requirements
    ]
    seen: set[Tuple[str, Tuple[str, ...]]] = set()

    with _install_lock:
        while queue:
            req, extras = queue.pop(0)
            key = (str(canonicalize_name(req.name)), extras)
            if key in seen:
                continue
            seen.add(key)
            if _already_satisfied(req, site_dir):
                continue
            if on_progress:
                on_progress(f"Resolving {req.name} ...")
            name, version, url, sha256, _filename = _resolve_wheel(req)
            wheel_path = _download_verified(name, version, url, sha256)
            dist_info = _unpack(wheel_path, site_dir)
            installed.append(f"{name}=={version}")
            logger.info("wheel 通道安装: %s==%s -> %s", name, version, site_dir)
            queue.extend(_runtime_requires(site_dir, dist_info, extras))
    return installed


# ---------------------------------------------------------------------------
# requirement 解析
# ---------------------------------------------------------------------------


def _parse_requirements(
    targets: List[str], requirements_path: Optional[str]
) -> List[Requirement]:
    lines: List[str] = list(targets)
    if requirements_path:
        with open(requirements_path, "r", encoding="utf-8") as fh:
            lines.extend(fh.readlines())

    requirements: List[Requirement] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 选项/editable/路径目标不归本通道（冻结形态 path 依赖走挂载）
        if line.startswith("-") or os.sep in line or (os.altsep and os.altsep in line):
            continue
        try:
            requirements.append(Requirement(line))
        except InvalidRequirement:
            logger.warning("wheel 通道跳过无法解析的依赖规格: %r", line)
    return requirements


def _already_satisfied(req: Requirement, site_dir: str) -> bool:
    """当前环境（含已挂载 .site）或 site_dir 内已有满足规格的发行版。"""
    name = str(canonicalize_name(req.name))
    candidates: List[str] = []
    try:
        candidates.append(importlib.metadata.version(req.name))
    except importlib.metadata.PackageNotFoundError:
        pass
    site = Path(site_dir)
    if site.is_dir():
        for dist_info in site.glob("*.dist-info"):
            # dist-info 目录名：{normalized_with_underscores}-{version}.dist-info
            # （名内 dash 已归一为下划线、版本不含 dash——剥后缀后右劈一刀）
            stem = dist_info.name[: -len(".dist-info")]
            parts = stem.rsplit("-", 1)
            if len(parts) == 2 and canonicalize_name(parts[0]) == name:
                candidates.append(parts[1])
    for raw in candidates:
        try:
            if Version(raw) in req.specifier:
                return True
        except InvalidVersion:
            continue
    return False


# ---------------------------------------------------------------------------
# PyPI 解析
# ---------------------------------------------------------------------------


def _resolve_wheel(req: Requirement) -> Tuple[str, str, str, str, str]:
    """按平台 tag 偏好序为 *req* 选出最佳 wheel，返回 (name, version, url, sha256, filename)。

    版本从新到旧、tag 按 ``sys_tags()`` 偏好序（与 pip 同数据源）。
    """
    name = str(canonicalize_name(req.name))
    data = _http_json(_PYPI_JSON.format(name=name))
    releases: Dict[str, List[Dict[str, Any]]] = data.get("releases") or {}

    # 候选版本：可解析 + 满足规格 + 有未 yanked 的 wheel 文件
    candidates: List[Tuple[Version, List[Dict[str, Any]]]] = []
    for version_str, files in releases.items():
        try:
            version = Version(version_str)
        except InvalidVersion:
            continue
        if version not in req.specifier:
            continue
        wheels = [f for f in files if _is_wheel_file(f)]
        if wheels:
            candidates.append((version, wheels))
    if not candidates:
        raise WheelInstallError(f"PyPI 上没有满足 {req} 的可用版本（含 wheel）")

    for version, files in sorted(candidates, key=lambda item: item[0], reverse=True):
        tagged: List[Tuple[Dict[str, Any], frozenset]] = []
        for f in files:
            try:
                _, _, _, file_tags = parse_wheel_filename(f["filename"])
            except (InvalidWheelFilename, ValueError):
                continue
            tagged.append((f, file_tags))
        for tag in _sys_tags():
            for f, file_tags in tagged:
                if tag in file_tags:
                    return (
                        name,
                        str(version),
                        f["url"],
                        f["digests"]["sha256"],
                        f["filename"],
                    )
    raise WheelInstallError(f"{req} 没有匹配当前平台的 wheel（仅 sdist/异平台）")


def _is_wheel_file(file_info: Dict[str, Any]) -> bool:
    return file_info.get("packagetype") == "bdist_wheel" and not file_info.get("yanked")


_sys_tags_cache: Optional[List[Any]] = None


def _sys_tags() -> List[Any]:
    """本平台 wheel tag 偏好序（缓存——进程内不变）。"""
    global _sys_tags_cache
    if _sys_tags_cache is None:
        from packaging.tags import sys_tags

        _sys_tags_cache = list(sys_tags())
    return _sys_tags_cache


# ---------------------------------------------------------------------------
# 下载 / 校验 / 解包
# ---------------------------------------------------------------------------


def _http_json(url: str) -> Dict[str, Any]:
    import json

    request = urllib.request.Request(url, headers={"User-Agent": "nova-harness"})
    try:
        with urllib.request.urlopen(
            request, timeout=_DOWNLOAD_TIMEOUT_S, context=default_ssl_context()
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]
    except WheelInstallError:
        raise
    except Exception as exc:
        raise WheelInstallError(f"PyPI 查询失败（{url}）：{exc}") from exc


def _download_verified(name: str, version: str, url: str, sha256: str) -> str:
    """下载 wheel 到临时文件并边下边算 sha256，校验通过返回路径。"""
    request = urllib.request.Request(url, headers={"User-Agent": "nova-harness"})
    digest = hashlib.sha256()
    tmp_path = ""
    try:
        with urllib.request.urlopen(
            request, timeout=_DOWNLOAD_TIMEOUT_S, context=default_ssl_context()
        ) as resp:
            with tempfile.NamedTemporaryFile(
                prefix=f"nova-wheel-{name}-", suffix=".whl", delete=False
            ) as tmp:
                tmp_path = tmp.name
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    tmp.write(chunk)
        if digest.hexdigest() != sha256:
            raise WheelInstallError(
                f"wheel 校验和不匹配（{name}=={version}）——下载内容可能被篡改"
            )
        return tmp_path
    except BaseException:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _unpack(wheel_path: str, site_dir: str) -> str:
    """解包 wheel 进 site_dir，返回 dist-info 目录名。

    安全约束：条目必须落在 site_dir 内（拒绝绝对路径/``..`` 逃逸）。
    wheel 已过 sha256 校验，此处防的是数据损坏/边界输入。
    """
    base = os.path.abspath(site_dir)
    dist_info = ""
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            for info in zf.infolist():
                dest = os.path.abspath(os.path.join(base, info.filename))
                if dest != base and not dest.startswith(base + os.sep):
                    raise WheelInstallError(f"wheel 含逃逸条目: {info.filename!r}")
                if info.filename.endswith(".dist-info/METADATA"):
                    dist_info = os.path.dirname(info.filename)
                zf.extract(info, base)
    finally:
        os.unlink(wheel_path)
    if not dist_info:
        raise WheelInstallError("wheel 缺 dist-info/METADATA——包结构非法")
    return dist_info


def _runtime_requires(
    site_dir: str, dist_info: str, extras: Tuple[str, ...]
) -> List[Tuple[Requirement, Tuple[str, ...]]]:
    """从 dist-info METADATA 读运行期依赖，按环境 marker 过滤。

    ``extra`` marker 按调用方声明的 extras 逐个求值（pip 装 ``name[extra]``
    同款语义）；无 extras 时 ``extra`` 在默认环境中为空串——extra 门控
    依赖一律不装。子依赖自身声明的 extras 随队列传递。
    """
    meta_path = os.path.join(site_dir, dist_info, "METADATA")
    if not os.path.isfile(meta_path):
        return []
    with open(meta_path, "r", encoding="utf-8") as fh:
        message = Parser().parsestr(fh.read())
    base_env = default_environment()
    extra_envs = [dict(base_env, extra=e) for e in extras] if extras else []

    requires: List[Tuple[Requirement, Tuple[str, ...]]] = []
    for raw in message.get_all("Requires-Dist") or []:
        try:
            child = Requirement(raw)
        except InvalidRequirement:
            continue
        if child.marker is None:
            requires.append((child, tuple(sorted(child.extras))))
            continue
        # 无 extras：默认环境求值（extra 为空串 → extra 门控为假）
        if not extra_envs:
            if child.marker.evaluate(base_env):
                requires.append((child, tuple(sorted(child.extras))))
            continue
        # 任一声明 extra 使命中即纳入
        if any(child.marker.evaluate(env) for env in extra_envs):
            requires.append((child, tuple(sorted(child.extras))))
    return requires


__all__ = [
    "WheelInstallError",
    "install_wheels",
]
