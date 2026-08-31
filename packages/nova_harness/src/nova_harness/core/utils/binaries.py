"""托管二进制的统一解析。

Nova 的二进制有三个来源（按解析优先级）：

1. **env bin**（``sys.executable`` 同级目录）——pip wheel 装的（如 ``ripgrep``）；
2. **nova bin**（``~/.nova/agent/bin``）——框架注册表自管理下载的（如 ``fd``）；
3. **PATH**（``shutil.which``）——用户系统安装的版本，兜底。

托管版本优先于系统版本（对齐 pi ``getShellEnv`` 的取舍），保证行为可复现。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Dict, List, Optional

from nova_harness.core.config.defaults import get_agent_dir


def get_env_bin_dir() -> str:
    """当前 Python 解释器所在环境的 bin 目录。"""
    return os.path.dirname(os.path.abspath(sys.executable))


def get_nova_bin_dir() -> str:
    """Nova 自管理二进制目录（框架注册表下载的落点）。"""
    return str(get_agent_dir() / "bin")


def _executable_candidates(name: str) -> List[str]:
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        return [f"{name}.exe", name]
    return [name]


# 系统二进制的别名表（对齐 pi ``systemBinaryNames``）：某些发行版用别的
# 名字分发同一个二进制（如 Debian/Ubuntu 的 fd 叫 fdfind）。
ALTERNATE_BINARY_NAMES: Dict[str, List[str]] = {
    "fd": ["fd", "fdfind"],
}

# 二进制不可用时的安装指引（对齐 kimi-code rgUnavailableMessage 的友好报错）。
_BINARY_INSTALL_GUIDANCE: Dict[str, str] = {
    "fd": (
        "brew install fd  # macOS\n"
        "sudo apt install fd-find  # Debian/Ubuntu（命令名 fdfind）"
    ),
    "rg": (
        "brew install ripgrep  # macOS\n" "sudo apt install ripgrep  # Debian/Ubuntu"
    ),
}


def candidate_names(name: str) -> List[str]:
    """二进制的候选命令名（含发行版别名）。"""
    return ALTERNATE_BINARY_NAMES.get(name, [name])


def binary_install_guidance(name: str) -> str:
    """该二进制缺失时给用户的安装指引。"""
    return _BINARY_INSTALL_GUIDANCE.get(name, f"请参考 {name} 官方安装文档")


def _find_in_dir(directory: str, candidates: List[str]) -> Optional[str]:
    for candidate in candidates:
        path = os.path.join(directory, candidate)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def resolve_binary(name: str) -> Optional[str]:
    """解析二进制可执行文件路径：env bin → nova bin → PATH。

    托管目录（env/nova bin）只用规范名；PATH 层识别发行版别名
    （如 Debian 的 fd 叫 fdfind）。
    """
    candidates = _executable_candidates(name)
    for directory in (get_env_bin_dir(), get_nova_bin_dir()):
        found = _find_in_dir(directory, candidates)
        if found:
            return found

    for alt_name in candidate_names(name):
        for candidate in _executable_candidates(alt_name):
            found = shutil.which(candidate)
            if found:
                return found
    return None


def prepend_managed_bins_to_path(env: Dict[str, str]) -> Dict[str, str]:
    """把托管 bin 目录（env bin → nova bin）前置到 PATH（若尚未包含）。

    spawn 子进程时使用：保证 bash 里直接敲 ``rg``/``fd`` 能命中 Nova
    托管的版本，即使后端是以非激活方式（如绝对路径解释器）启动的。
    顺序与 ``resolve_binary`` 的解析优先级一致（env bin → nova bin）。
    """
    path_key = next((k for k in env if k.lower() == "path"), "PATH")
    current = env.get(path_key, "")
    entries = [e for e in current.split(os.pathsep) if e]
    prepend = [d for d in (get_env_bin_dir(), get_nova_bin_dir()) if d not in entries]
    if not prepend:
        return env
    return {**env, path_key: os.pathsep.join([*prepend, *entries])}


__all__ = [
    "ALTERNATE_BINARY_NAMES",
    "binary_install_guidance",
    "candidate_names",
    "get_env_bin_dir",
    "get_nova_bin_dir",
    "prepend_managed_bins_to_path",
    "resolve_binary",
]
