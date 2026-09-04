"""冻结形态的包运行时装配路径挂载。

开发态：``nova-pkg`` 经 ``pip install -e .`` 把包的 Python 半区写进环境
（.pth 路径钩子），第三方依赖装进当前环境 site-packages。
冻结态没有可写环境——等价效果靠 ``sys.path`` 挂载：

- ``<base>/packages/.site/``：第三方依赖（pip --target 的落点）；
- ``<包>/backend/``：各已安装包的共享模块目录（.pth 等价物）。

挂载点来源：包存储目录扫描（path/git/npm 三个族）+ settings 里的
editable path 源（原地引用不复制，不在存储目录里）。幂等，已挂载不重复；
一律 ``append``（冻结内部优先——包不能遮蔽 nova_* 内建模块）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from nova_harness.core.config.settings.manager import SettingsManager

from nova_harness.core.config.defaults import PACKAGES_DIR_NAME

# 包存储族目录（installer 的落点族）
_STORE_FAMILIES = ("path", "git", "npm")


def _mount(path: Path, mounted: List[str]) -> None:
    text = str(path)
    if text not in sys.path:
        sys.path.append(text)
        mounted.append(text)


def _source_to_dir(spec: object, base: Path) -> Optional[Path]:
    """把 settings 里的 path: 源解析为目录（get_package_sources 返回已解析 spec）。"""
    text = str(getattr(spec, "source", spec))
    if text.startswith("path:"):
        text = text[5:]
    if not text or "://" in text or text.startswith("git:") or text.startswith("npm:"):
        return None
    path = Path(text)
    return path if path.is_absolute() else base / path


def ensure_package_paths(
    agent_dir: str,
    settings_manager: "SettingsManager",
    project_base_dir: Optional[str] = None,
) -> List[str]:
    """冻结形态：把 .site/ 与已安装包的 backend/ 挂进 sys.path。

    返回本次挂载的路径列表（诊断/测试用）；非冻结形态零动作。
    """
    if not getattr(sys, "frozen", False):
        return []

    mounted: List[str] = []
    bases = [Path(agent_dir)]
    if project_base_dir:
        bases.append(Path(project_base_dir))

    for base in bases:
        # 1. 第三方依赖落点
        site = base / PACKAGES_DIR_NAME / ".site"
        if site.is_dir():
            _mount(site, mounted)

        # 2. 存储族目录下各包的 backend/（copy 安装的包）
        packages_root = base / PACKAGES_DIR_NAME
        if packages_root.is_dir():
            for family in _STORE_FAMILIES:
                family_dir = packages_root / family
                if not family_dir.is_dir():
                    continue
                for pkg_dir in sorted(family_dir.iterdir()):
                    backend = pkg_dir / "backend"
                    if backend.is_dir():
                        _mount(backend, mounted)

        # 3. editable path 源（原地引用，不进存储目录）
        local = base != Path(agent_dir)
        for spec in settings_manager.get_package_sources(
            local=local, base_dir=str(base)
        ):
            pkg_dir = _source_to_dir(spec, base)
            if pkg_dir is None:
                continue
            backend = pkg_dir / "backend"
            if backend.is_dir():
                _mount(backend, mounted)

    return mounted
