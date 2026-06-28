"""Optional binary dependency detection and installation hints.

某些 bundle 中的工具可能依赖外部二进制（如 ``rg``、``fd``）以获得更好性能。
这些二进制无法通过 pip 安装，本模块提供通用检测、提示以及允许时尝试安装的能力。

具体要检测哪些二进制由 bundle 的 ``package.json`` 中 ``nova.binary_dependencies``
字段声明，格式为 ``{命令名: 系统包名}``。
"""

import platform
import shutil
import subprocess
from typing import Dict, List, Optional


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


def _brew_package(package_name: str) -> Optional[str]:
    # Homebrew 中部分包名与 Debian 不同，这里做简单映射。
    mapping = {"fd-find": "fd"}
    return mapping.get(package_name, package_name)


def get_install_hint(cmd: str, package_name: str) -> Optional[str]:
    """返回安装指定二进制命令的提示命令，不支持时返回 None。"""
    family = _platform_family()
    if family == "macos":
        pkg = _brew_package(package_name)
        if pkg and shutil.which("brew"):
            return f"brew install {pkg}"
    elif family == "linux":
        if shutil.which("apt-get"):
            return f"sudo apt-get install {package_name}"
        if shutil.which("apt"):
            return f"sudo apt install {package_name}"
        if shutil.which("dnf"):
            return f"sudo dnf install {package_name}"
        if shutil.which("yum"):
            return f"sudo yum install {package_name}"
        if shutil.which("pacman"):
            return f"sudo pacman -S {package_name}"
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


def try_install_binaries(missing: Dict[str, str]) -> Dict[str, bool]:
    """尝试通过系统包管理器安装二进制依赖。

    返回每个命令是否安装成功。未成功时不会抛出异常，仅返回 False。
    """
    results: Dict[str, bool] = {}
    family = _platform_family()

    if family == "macos" and shutil.which("brew"):
        packages = []
        for cmd, package_name in missing.items():
            pkg = _brew_package(package_name)
            if pkg:
                packages.append(pkg)
        if packages:
            try:
                subprocess.run(["brew", "install"] + packages, check=True)
                for cmd in missing:
                    results[cmd] = shutil.which(cmd) is not None
                return results
            except Exception:
                for cmd in missing:
                    results[cmd] = False
                return results

    if family == "linux":
        manager = None
        install_cmd = None
        if shutil.which("apt-get"):
            manager = "apt-get"
            install_cmd = ["apt-get", "install", "-y"]
        elif shutil.which("apt"):
            manager = "apt"
            install_cmd = ["apt", "install", "-y"]
        elif shutil.which("dnf"):
            manager = "dnf"
            install_cmd = ["dnf", "install", "-y"]
        elif shutil.which("yum"):
            manager = "yum"
            install_cmd = ["yum", "install", "-y"]
        elif shutil.which("pacman"):
            manager = "pacman"
            install_cmd = ["pacman", "-S", "--noconfirm"]

        if install_cmd:
            for cmd, package_name in missing.items():
                try:
                    subprocess.run(install_cmd + [package_name], check=True)
                    results[cmd] = shutil.which(cmd) is not None
                except Exception:
                    results[cmd] = False
            return results

    for cmd in missing:
        results[cmd] = False
    return results


__all__ = [
    "detect_missing_binaries",
    "format_binary_hints",
    "get_install_hint",
    "try_install_binaries",
]
