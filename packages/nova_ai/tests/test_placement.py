"""nova_ai 包级住所审计（``docs/crosscutting/structure-modeling.md`` §五的
fitness function）。

两条机械执法：

1. **登记清单对账**——``utils/`` 与 ``api_impls/_shared/`` 的模块集必须与本
   文件的登记清单一致：未登记的新模块即红（加文件先登记），幽灵条目即红
   （删文件同步除名）；
2. **族声明存在**——每个登记模块的 docstring 首行非空（一族一文件的族
   声明，§二第 2 层三条件之一）。
"""

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "nova_ai"

# 登记清单（与磁盘对账；abort.py 随取消/等待机制族升格迁往 nova_async，
# task_tracker.py 同迁——structure-modeling §二 升格出口）
_UTILS_MODULES = frozenset(
    {
        "env.py",
        "errors.py",
        "estimate.py",
        "hash.py",
        "http.py",
        "json_parser.py",
        "model_utils.py",
        "overflow.py",
        "provider_env.py",
        "surrogate.py",
    }
)

_SHARED_MODULES = frozenset(
    {
        "copilot_headers.py",
        "prompt_cache.py",
        "retry.py",
        "simple_options.py",
        "timeouts.py",
        "transform_messages.py",
        "user_agent.py",
    }
)


def _modules_under(directory: Path) -> set[str]:
    return {p.name for p in directory.glob("*.py") if p.name != "__init__.py"}


def _docstring_first_line(path: Path) -> str:
    doc = ast.get_docstring(ast.parse(path.read_text("utf-8"))) or ""
    stripped = doc.strip()
    return stripped.splitlines()[0] if stripped else ""


def _assert_registry(directory: Path, registry: frozenset[str]) -> None:
    disk = _modules_under(directory)
    unregistered = disk - registry
    ghosts = registry - disk
    assert not unregistered and not ghosts, (
        f"{directory.name}/ 住所登记失配——未登记: {sorted(unregistered)}；"
        f"幽灵: {sorted(ghosts)}（新模块先登记进 tests/test_placement.py，"
        "删除同步除名——structure-modeling §五）"
    )


def test_utils_registry_matches_disk():
    _assert_registry(_SRC / "utils", _UTILS_MODULES)


def test_shared_registry_matches_disk():
    _assert_registry(_SRC / "api_impls" / "_shared", _SHARED_MODULES)


def test_every_registered_module_has_family_docstring():
    for directory, registry in (
        (_SRC / "utils", _UTILS_MODULES),
        (_SRC / "api_impls" / "_shared", _SHARED_MODULES),
    ):
        for name in sorted(registry):
            first_line = _docstring_first_line(directory / name)
            assert first_line, f"{directory.name}/{name} 缺 docstring 首行族声明"
