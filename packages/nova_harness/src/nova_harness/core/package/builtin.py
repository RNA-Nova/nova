"""内建官方包通道（打包形态首启开箱即用）。

冻结二进制随包携带 nova-base（会话基础设施——slash 命令 + question/todo
工具 + UI 原语糖库）；首次装配会话时把它落地到 ``<agentDir>/builtin/
nova_base/`` 并登记进 settings 包清单——之后与正常安装的 path 包同机制
（注册表/resolver/遮蔽/面板可见）。nova-base 不可经包管理器卸载
（PackageManager.uninstall 的基础包守护）；用户直接编辑 settings 移除
条目的，不回补（``.seeded.json`` 记录已播种名单）。

编程执行能力（nova-coding-agent）**不内建**——产品定案：壳内建、能力
按需装（``nova-server pkg install``）。

纪律：

- 仅冻结形态（``sys.frozen``）生效；开发态返回空（开发由用户环境提供）。
- 幂等：已落地且版本一致则零动作；二进制内 bundle 版本变了才重落地
  （文件层刷新，settings 条目路径不变）。
- 尊重卸载：登记过的条目被用户从 settings 移除后不回补
  （``.seeded.json`` 记录已播种名单；播种过但清单里没有 = 用户明确移除）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, List

from nova_harness.core.package.source.spec import parse_source

if TYPE_CHECKING:
    from nova_harness.core.config.settings.manager import SettingsManager

_BUILTIN_BUNDLES = ("nova_base",)
_SEEDED_FILE = ".seeded.json"
_VERSION_MARKER = ".builtin-version"


def _source_dir_matches(source: str, dest: Path) -> bool:
    """settings 源是否指向 dest 目录（精确判等，非子串——避免
    ``builtin/nova_base_extra`` 误判命中 ``builtin/nova_base``）。

    只有 path 族源可能命中（git/npm 源经 parse_source 归族后排除）；
    ``get_package_sources`` 返回的 path 源已按 scope 基准解析为绝对路径。
    """
    try:
        parsed = parse_source(source)
    except ValueError:
        return False
    if parsed.type != "path" or not parsed.path:
        return False
    try:
        return Path(parsed.path).resolve() == dest.resolve()
    except OSError:
        return False


def _bundled_dir(name: str) -> Path:
    """二进制内携带的 bundle 目录（构建期 PyInstaller --add-data 注入）。"""
    return Path(getattr(sys, "_MEIPASS")) / "bundles" / name


def _bundled_version(src: Path) -> str:
    """读 bundle pyproject 的版本（Poetry 段优先，PEP 621 兜底）。"""
    try:
        data = tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))
        poetry = data.get("tool", {}).get("poetry", {})
        version = poetry.get("version") or data.get("project", {}).get("version")
        return str(version) if version else "0"
    except Exception:
        return "0"


def ensure_builtin_packages(
    settings_manager: "SettingsManager", agent_dir: str
) -> List[str]:
    """确保内建官方包落地 + 登记。返回动作日志（诊断/测试断言用）。"""
    if not getattr(sys, "frozen", False):
        return []

    actions: List[str] = []
    builtin_root = Path(agent_dir) / "builtin"
    builtin_root.mkdir(parents=True, exist_ok=True)

    seeded_file = builtin_root / _SEEDED_FILE
    try:
        seeded: List[str] = json.loads(seeded_file.read_text(encoding="utf-8")).get(
            "seeded", []
        )
    except Exception:
        seeded = []

    existing_sources = [
        str(getattr(spec, "source", spec))
        for spec in settings_manager.get_package_sources(local=False)
    ]
    seen_or_registered: set[str] = set()

    for name in _BUILTIN_BUNDLES:
        src = _bundled_dir(name)
        if not src.is_dir():
            continue  # 构建期未携带——跳过不阻断

        dest = builtin_root / name
        version = _bundled_version(src)
        marker = dest / _VERSION_MARKER
        current = (
            marker.read_text(encoding="utf-8").strip() if marker.exists() else None
        )
        if not dest.is_dir() or current != version:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            marker.write_text(version, encoding="utf-8")
            actions.append(f"landed {name}@{version}")

        # 登记进 settings 包清单：已登记跳过；播种后被用户移除的不回补。
        already = any(_source_dir_matches(source, dest) for source in existing_sources)
        if already:
            seen_or_registered.add(name)
            continue
        removed_by_user = name in seeded
        if not removed_by_user:
            settings_manager.add_package_source(f"path:{dest}")
            actions.append(f"registered {name}")
            existing_sources.append(f"path:{dest}")
            seen_or_registered.add(name)

    # 播种标记只记"已登记或已在清单"的包——播种后被用户移除的名单
    # 据此判定（在标记里但清单没有 = 用户移除，不回补）
    new_seeded = sorted(set(seeded) | seen_or_registered)
    if new_seeded != seeded:
        seeded_file.write_text(
            json.dumps({"seeded": new_seeded}, ensure_ascii=False), encoding="utf-8"
        )

    return actions
