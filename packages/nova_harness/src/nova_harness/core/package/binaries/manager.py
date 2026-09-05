"""框架自管理二进制的下载与安装。

由 pin 版本 + sha256 的注册表（``registry.json``，数据文件）驱动：

- 下载（带超时）
- sha256 校验（无签名不安装）
- tarfile/zipfile（stdlib）解压
- staging + rename 原子安装（中途失败不留半成品）
- 锁防并发重复下载
- ``NOVA_OFFLINE`` 离线语义（对齐 pi ``PI_OFFLINE``）

失败一律返回 None——调用方警告降级（工具链有纯 Python 兜底），不阻断安装。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.utils.binaries import get_nova_bin_dir
from nova_harness.core.utils.http import default_ssl_context

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).with_name("registry.json")
_DOWNLOAD_TIMEOUT_S = 600
_ensure_lock = threading.Lock()


def _load_registry() -> Dict[str, Any]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def list_managed_binaries() -> List[str]:
    """注册表已知的自管理二进制名。"""
    return sorted(_load_registry())


def is_offline_mode_enabled() -> bool:
    """``NOVA_OFFLINE`` 环境变量（对齐 pi ``PI_OFFLINE``）。"""
    return os.environ.get("NOVA_OFFLINE", "").lower() in ("1", "true", "yes")


def detect_platform_key() -> Optional[str]:
    """当前平台 → 注册表平台键（``darwin-aarch64`` 等规范化键）。

    注意：注册表用规范化键而非 Rust target triple——上游资产命名
    各项目不一（fd 的 linux x64 用 gnu、rg 用 musl），完整 URL 由
    注册表条目自带，这里只做机器到键的映射。

    Linux 区分 libc：musl 发行版（Alpine 等）走 ``linux-<arch>-musl``
    键（gnu 变体在 musl 系统上无法运行）。
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        return None
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    if sys.platform.startswith("linux"):
        libc, _ = platform.libc_ver()
        if libc == "musl":
            return f"linux-{arch}-musl"
        return f"linux-{arch}"
    if sys.platform == "win32":
        return f"windows-{arch}"
    return None


def ensure_binary(
    name: str,
    bin_dir: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """确保指定二进制可用，返回路径；失败返回 None。

    已在 nova bin 中 → 直接返回；``NOVA_OFFLINE`` → 跳过下载返回 None；
    否则按注册表下载 + 校验 + 原子安装。
    """
    bin_dir = bin_dir or get_nova_bin_dir()
    registry = _load_registry()
    entry = registry.get(name)
    if entry is None:
        logger.warning("未知的自管理二进制: '%s'（注册表无此条目）", name)
        return None

    binary_file = entry["binary_name"] + (".exe" if sys.platform == "win32" else "")
    destination = os.path.join(bin_dir, binary_file)
    if os.path.isfile(destination) and os.access(destination, os.X_OK):
        return destination

    if is_offline_mode_enabled():
        logger.warning("NOVA_OFFLINE 已启用，跳过 '%s' 的下载", name)
        return None

    platform_key = detect_platform_key()
    asset = (entry.get("platforms") or {}).get(platform_key or "")
    if asset is None:
        logger.warning("'%s' 没有适用于当前平台（%s）的注册表条目", name, platform_key)
        return None

    with _ensure_lock:
        # 双重检查：等待锁期间可能已被其他线程装好
        if os.path.isfile(destination) and os.access(destination, os.X_OK):
            return destination
        try:
            return _download_and_install(
                name, entry, asset, binary_file, destination, on_progress
            )
        except Exception as exc:
            logger.warning("下载安装 '%s' 失败: %s", name, exc)
            return None


def _download_and_install(
    name: str,
    entry: Dict[str, Any],
    asset: Dict[str, str],
    binary_file: str,
    destination: str,
    on_progress: Optional[Callable[[str], None]],
) -> str:
    url = asset["url"]
    if on_progress:
        on_progress(f"Downloading {name} {entry['version']}...")

    bin_dir = os.path.dirname(destination)
    os.makedirs(bin_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"nova-{name}-") as tmp:
        archive_path = os.path.join(tmp, "archive")
        _download(url, archive_path)
        _verify_sha256(archive_path, asset["sha256"])
        binary_path = _extract_binary(archive_path, url, binary_file, tmp)

        # 原子安装：staging 到目标目录后 rename，中途失败不留半成品
        staging = tempfile.mkdtemp(prefix=".install-", dir=bin_dir)
        try:
            staged = os.path.join(staging, binary_file)
            shutil.copyfile(binary_path, staged)
            os.chmod(staged, 0o755)
            os.replace(staged, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return destination


def _download(url: str, dest: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "nova-harness"})
    with urllib.request.urlopen(
        request, timeout=_DOWNLOAD_TIMEOUT_S, context=default_ssl_context()
    ) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def _verify_sha256(path: str, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"校验和不匹配: 期望 {expected}，实际 {actual}（下载内容可能被篡改）"
        )


def _extract_binary(archive_path: str, url: str, binary_file: str, tmp: str) -> str:
    extract_dir = os.path.join(tmp, "extract")
    os.makedirs(extract_dir, exist_ok=True)
    if url.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)
    else:
        with tarfile.open(archive_path, "r:gz") as tf:
            # filter="data"：拒绝绝对路径/链接等危险条目（CPython 安全默认）
            tf.extractall(extract_dir, filter="data")
    # 递归找二进制（资产内目录层级随上游而变）
    for root, _, files in os.walk(extract_dir):
        if binary_file in files:
            return os.path.join(root, binary_file)
    raise RuntimeError(f"压缩包中未找到二进制 '{binary_file}'")


__all__ = [
    "detect_platform_key",
    "ensure_binary",
    "is_offline_mode_enabled",
    "list_managed_binaries",
]
