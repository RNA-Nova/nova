"""nova_async 结构审计（fitness functions——structure-modeling §五的机械化）。

扫描手法对位 ``nova_protocol/tests/test_purity.py``（ast 解析，不 import
被执行模块）：

1. **依赖方向**：包内只许 stdlib + ``nova_protocol`` + ``anyio``（anyio
   仅许出现在 ``abort.py``——它是 CancelScope 桥的内部执行层）；
2. **API 面 anyio 零泄漏**：``__all__`` 导出对象的 ``__module__`` 不得以
   anyio 开头（asyncio 单后端纪律的机械执法）；
3. **族清单登记制对账**：``__all__`` 精确等于测试内快照——新增/退役件
   必须显式更新快照，多/少即红；
4. **升格旧路径归零**（structure-modeling §六第二条验收）：全仓
   import 扫描（``packages/`` + ``bundles/``——bundle 迁出 packages
   后仍是活跃消费区，执法边界必须跟上）——旧住所引用零命中（注：
   ``nova_harness/config/auth/refresh_locks.py`` 文件锁 adapter 是合法
   存活点，禁配名单里不含它）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import nova_async

PKG_ROOT = Path(__file__).resolve().parents[1] / "src" / "nova_async"
REPO_ROOT = Path(__file__).resolve().parents[3]
#: 归零扫描根（packages/ + bundles/——bundle 是 nova_async 的活跃消费区）
_SCAN_ROOTS = tuple(
    p for p in (REPO_ROOT / "packages", REPO_ROOT / "bundles") if p.exists()
)

#: 依赖方向白名单外即越界：stdlib 由解释器名单判定，第三方只许这两家
_THIRD_PARTY_ALLOWED = {"nova_protocol"}
#: anyio 仅许出现在 abort.py（CancelScope 桥不外渗）
_ANYIO_ALLOWED_FILES = {"abort.py"}

#: 升格批次验收的旧住所禁配名单（import 级精确匹配；子模块前缀同禁）
_OLD_HOME_BAN = frozenset(
    {
        "nova_ai.utils.abort",
        "nova_protocol.task_tracker",
        "nova_ai.auth.refresh_locks",
        "nova_ai.gateway.deadline",
    }
)

#: 族清单快照（登记制——改动 __all__ 必须同步本表；按 sorted 序钉死）
_ALL_SNAPSHOT = [
    "AbortScope",
    "Deadline",
    "KeyedLockFactory",
    "TaskTracker",
    "abortable_sleep",
    "any_signal",
    "await_with_abort",
    "await_with_signal",
    "backoff_delay_ms",
    "default_keyed_locks",
    "in_memory_keyed_locks",
    "operation_signal",
]

_SKIP_DIR_NAMES = {".venv", ".pixi", "__pycache__", ".git", "node_modules"}


def _imports_of(path: Path):
    """yield 一个文件的绝对 import 目标（相对 import 属包内事，不在审计面）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module


def _iter_package_sources(root: Path):
    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if parts & _SKIP_DIR_NAMES:
            continue
        if "dist-info" in path.name or path.name.endswith(".dist-info"):
            continue
        yield path


def test_dependency_direction():
    """nova_async 只许 stdlib + nova_protocol（anyio 仅 abort.py）——
    兄弟包（nova_ai/nova_harness/nova_server/nova_agent/nova_coding_agent/
    nova_exec_server_client）与任何其他第三方 import 即红。"""
    offenders = []
    for path in sorted(PKG_ROOT.rglob("*.py")):
        for mod in _imports_of(path):
            head = mod.split(".", 1)[0]
            if head in sys.stdlib_module_names:
                continue
            if head in _THIRD_PARTY_ALLOWED:
                continue
            if head == "anyio" and path.name in _ANYIO_ALLOWED_FILES:
                continue
            offenders.append(f"{path.relative_to(PKG_ROOT)}: {mod}")
    assert (
        not offenders
    ), "nova_async 依赖方向越界（只许 stdlib + nova_protocol，anyio 仅 abort.py）：\n" + "\n".join(
        offenders
    )


def test_api_surface_has_no_anyio_leak():
    """__all__ 导出对象的 __module__ 不得以 anyio 开头——anyio 是内部
    CancelScope 桥，不外泄（asyncio 单后端纪律的机械执法）。"""
    offenders = []
    for name in nova_async.__all__:
        obj = getattr(nova_async, name)
        module = getattr(obj, "__module__", "") or ""
        if module.startswith("anyio"):
            offenders.append(f"{name}: {module}")
    assert not offenders, "anyio 泄漏进 API 面：\n" + "\n".join(offenders)


def test_all_matches_registry_snapshot():
    """族清单登记制对账：__all__ 必须精确等于快照（新增/退役件先更新本表）。"""
    assert sorted(nova_async.__all__) == _ALL_SNAPSHOT


def test_old_home_imports_zero():
    """升格旧路径引用归零（§六第二条验收）：packages/ + bundles/ import 扫描。

    禁配：``nova_ai.utils.abort`` / ``nova_protocol.task_tracker`` /
    ``nova_ai.auth.refresh_locks`` / ``nova_ai.gateway.deadline`` 的
    import，以及 ``from nova_protocol.signal import await_with_signal``
    （竞速件已迁 nova_async——signal.py docstring 的文字提及不算引用，
    故本条走 AST import 判别而非文本扫描）。
    """
    offenders = []
    for root in _SCAN_ROOTS:
        for path in _iter_package_sources(root):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    targets = [node.module] if node.level == 0 and node.module else []
                    if node.level == 0 and node.module == "nova_protocol.signal":
                        for alias in node.names:
                            if alias.name == "await_with_signal":
                                offenders.append(
                                    f"{path.relative_to(REPO_ROOT)}: "
                                    "from nova_protocol.signal import await_with_signal"
                                )
                else:
                    continue
                for target in targets:
                    for banned in _OLD_HOME_BAN:
                        if target == banned or target.startswith(banned + "."):
                            offenders.append(f"{path.relative_to(REPO_ROOT)}: {target}")
    assert not offenders, "升格旧路径引用未归零：\n" + "\n".join(offenders)
