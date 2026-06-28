"""Optional binary dependency detection and installation hints.

某些 bundle 中的工具可能依赖外部二进制（如 ``rg``、``fd``）以获得更好性能。
这些二进制无法通过 pip 安装，本模块提供通用检测、提示以及允许时尝试安装的能力。

具体要检测哪些二进制由 bundle 的 ``package.json`` 中 ``nova.binary_dependencies``
字段声明，格式为 ``{命令名: 系统包名}``。
"""

import os
import platform
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple


def detect_missing_binaries(binary_map: Dict[str, str]) -> Dict[str, str]:
    """返回当前环境中缺失的二进制命令及其对应系统包名。

    Args:
        binary_map: ``{命令名: 系统包名}`` 映射。
    """
    return {cmd: pkg for cmd, pkg in binary_map.items() if shutil.which(cmd) is None}


def _platform_family() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "other"


def _is_root() -> bool:
    """Check whether the current process has root privileges."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _has_sudo() -> bool:
    """Check whether ``sudo`` is available."""
    return shutil.which("sudo") is not None


def _brew_package(package_name: str) -> Optional[str]:
    # Homebrew 中部分包名与 Debian 不同，这里做简单映射。
    mapping = {"fd-find": "fd"}
    return mapping.get(package_name, package_name)


def _linux_manager() -> Optional[Tuple[str, List[str]]]:
    """Return (manager_name, install_command_prefix) for the current Linux distro."""
    if shutil.which("apt-get"):
        return "apt-get", ["apt-get", "install", "-y"]
    if shutil.which("apt"):
        return "apt", ["apt", "install", "-y"]
    if shutil.which("dnf"):
        return "dnf", ["dnf", "install", "-y"]
    if shutil.which("yum"):
        return "yum", ["yum", "install", "-y"]
    if shutil.which("pacman"):
        return "pacman", ["pacman", "-S", "--noconfirm"]
    return None


def get_install_hint(cmd: str, package_name: str) -> Optional[str]:
    """返回安装指定二进制命令的提示命令，不支持时返回 None。"""
    family = _platform_family()
    if family == "macos":
        pkg = _brew_package(package_name)
        if pkg and shutil.which("brew"):
            return f"brew install {pkg}"
    elif family == "linux":
        manager_info = _linux_manager()
        if manager_info:
            manager, prefix = manager_info
            sudo_prefix = "sudo " if not _is_root() else ""
            return f"{sudo_prefix}{' '.join(prefix)} {package_name}"
    return None


def format_binary_hints(missing: Dict[str, str]) -> str:
    """生成缺失二进制依赖的友好提示文本。"""
    if not missing:
        return ""

    lines = ["可选二进制依赖未安装，相关工具将使用 fallback（性能可能较低）："]
    for cmd, package_name in missing.items():
        hint = get_install_hint(cmd, package_name)
        if hint:
            lines.append(f"  - {cmd} ({package_name}): {hint}")
        else:
            lines.append(f"  - {cmd} ({package_name}): 请手动安装对应系统包")
    return "\n".join(lines)


def _build_install_command(
    package_name: str,
) -> Optional[Tuple[List[str], bool]]:
    """Build a subprocess command to install a binary package.

    Returns:
        ``(command_args, needs_sudo)`` or None if platform is unsupported.
    """
    family = _platform_family()
    if family == "macos" and shutil.which("brew"):
        pkg = _brew_package(package_name)
        if pkg:
            return ["brew", "install", pkg], False
        return None

    if family == "linux":
        manager_info = _linux_manager()
        if manager_info:
            _, prefix = manager_info
            if _is_root():
                return prefix + [package_name], False
            if _has_sudo():
                return ["sudo"] + prefix + [package_name], True
            return prefix + [package_name], True

    return None


def try_install_binaries(missing: Dict[str, str]) -> Dict[str, bool]:
    """尝试通过系统包管理器安装二进制依赖。

    返回每个命令是否安装成功。未成功时不会抛出异常，仅返回 False。
    """
    results: Dict[str, bool] = {}
    if not missing:
        return results

    for cmd, package_name in missing.items():
        command_info = _build_install_command(package_name)
        if command_info is None:
            results[cmd] = False
            continue

        args, needs_sudo = command_info
        if needs_sudo and not _is_root() and not _has_sudo():
            results[cmd] = False
            continue

        try:
            subprocess.run(args, check=True)
            results[cmd] = shutil.which(cmd) is not None
        except Exception:
            results[cmd] = False

    return results


__all__ = [
    "detect_missing_binaries",
    "format_binary_hints",
    "get_install_hint",
    "try_install_binaries",
]
