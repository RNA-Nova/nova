"""Optional binary dependency detection and installation hints.

某些工具（如 ``grep``、``find``）优先调用外部二进制 ``rg``、``fd``
以获得更好性能。这些二进制无法通过 pip 安装，本模块负责检测、提示以及
在允许时尝试通过系统包管理器安装。
"""

import platform
import shutil
import subprocess
from typing import Dict, List, Optional

# 命令名 -> 对应的常用系统包名
OPTIONAL_BINARIES: Dict[str, List[str]] = {
    "rg": ["ripgrep"],
    "fd": ["fd", "fd-find"],
}


def detect_missing_binaries() -> List[str]:
    """返回当前环境中缺失的可选二进制命令列表。"""
    return [cmd for cmd in OPTIONAL_BINARIES if shutil.which(cmd) is None]


def _platform_family() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "other"


def _brew_package(cmd: str) -> Optional[str]:
    mapping = {"rg": "ripgrep", "fd": "fd"}
    return mapping.get(cmd)


def _apt_package(cmd: str) -> Optional[str]:
    mapping = {"rg": "ripgrep", "fd": "fd-find"}
    return mapping.get(cmd)


def get_install_hint(cmd: str) -> Optional[str]:
    """返回安装指定二进制命令的提示命令，不支持时返回 None。"""
    family = _platform_family()
    if family == "macos":
        pkg = _brew_package(cmd)
        if pkg and shutil.which("brew"):
            return f"brew install {pkg}"
    elif family == "linux":
        pkg = _apt_package(cmd)
        if pkg:
            if shutil.which("apt-get"):
                return f"sudo apt-get install {pkg}"
            if shutil.which("apt"):
                return f"sudo apt install {pkg}"
    return None


def format_binary_hints(missing: Optional[List[str]] = None) -> str:
    """生成缺失二进制依赖的友好提示文本。"""
    if missing is None:
        missing = detect_missing_binaries()
    if not missing:
        return ""

    lines = ["可选二进制依赖未安装，工具将使用纯 Python fallback（性能较低）："]
    for cmd in missing:
        hint = get_install_hint(cmd)
        if hint:
            lines.append(f"  - {cmd}: {hint}")
        else:
            lines.append(f"  - {cmd}: 请手动安装对应系统包")
    return "\n".join(lines)


def try_install_binaries(binaries: List[str]) -> Dict[str, bool]:
    """尝试通过系统包管理器安装二进制依赖。

    返回每个命令是否安装成功。未成功时不会抛出异常，仅返回 False。
    """
    results: Dict[str, bool] = {}
    family = _platform_family()

    if family == "macos" and shutil.which("brew"):
        packages = []
        for cmd in binaries:
            pkg = _brew_package(cmd)
            if pkg:
                packages.append(pkg)
        if packages:
            try:
                subprocess.run(["brew", "install"] + packages, check=True)
                for cmd in binaries:
                    results[cmd] = shutil.which(cmd) is not None
                return results
            except Exception:
                for cmd in binaries:
                    results[cmd] = False
                return results

    if family == "linux" and shutil.which("apt-get"):
        for cmd in binaries:
            pkg = _apt_package(cmd)
            if not pkg:
                results[cmd] = False
                continue
            try:
                subprocess.run(
                    ["apt-get", "install", "-y", pkg],
                    check=True,
                )
                results[cmd] = shutil.which(cmd) is not None
            except Exception:
                results[cmd] = False
        return results

    for cmd in binaries:
        results[cmd] = False
    return results


__all__ = [
    "detect_missing_binaries",
    "format_binary_hints",
    "get_install_hint",
    "try_install_binaries",
    "OPTIONAL_BINARIES",
]
