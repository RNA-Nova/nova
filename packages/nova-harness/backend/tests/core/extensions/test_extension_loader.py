"""ExtensionLoader 加载行为测试。

扩展发现已统一由 PackageResolver 负责；ExtensionLoader 只按传入路径加载。
"""

from pathlib import Path

import pytest

from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.extensions.loader import load_extensions
from nova_harness.core.types.extensions import ExtensionRuntime


@pytest.mark.asyncio
async def test_load_extensions_loads_explicit_paths(tmp_path: Path) -> None:
    """顶层 load_extensions 按传入路径直接加载扩展。"""
    kept = tmp_path / "extension.py"
    kept.write_text("def extension(api): pass", encoding="utf-8")

    runtime = ExtensionRuntime(cwd=str(tmp_path), event_bus=ExtensionEventBus())
    result = await load_extensions(
        paths=[str(kept)],
        cwd=str(tmp_path),
        event_bus=runtime.event_bus,
        runtime=runtime,
    )
    assert len(result.extensions) == 1
    assert result.extensions[0].resolved_path == str(kept.resolve())


@pytest.mark.asyncio
async def test_load_extensions_records_path_errors(tmp_path: Path) -> None:
    """路径加载失败时，错误应包含在返回的 errors 列表中。"""
    missing = tmp_path / "missing.py"
    runtime = ExtensionRuntime(cwd=str(tmp_path), event_bus=ExtensionEventBus())
    result = await load_extensions(
        paths=[str(missing)],
        cwd=str(tmp_path),
        event_bus=runtime.event_bus,
        runtime=runtime,
    )
    assert len(result.extensions) == 0
    assert any(str(missing) in e["path"] for e in result.errors)


def test_package_extension_submodule_purged_on_reload(tmp_path: Path) -> None:
    """包扩展内部子模块在重复加载时重新执行（helper 代码变更生效）。

    无命名空间清理时，第二次加载会复用 ``sys.modules`` 里缓存的
    ``__nova_ext__.*.helper``，VALUE 仍是旧值。
    """
    from nova_harness.core.extensions.loader import _load_module_from_path

    ext_dir = tmp_path / "my-ext"
    ext_dir.mkdir()
    (ext_dir / "__init__.py").write_text("from .helper import VALUE\n")
    (ext_dir / "helper.py").write_text("VALUE = 1\n")

    module = _load_module_from_path(ext_dir)
    assert module.VALUE == 1

    # 注意：改用不同长度的内容，避开 .pyc 字节码缓存的 mtime+size 校验边界
    (ext_dir / "helper.py").write_text("VALUE = 22\n")
    reloaded = _load_module_from_path(ext_dir)
    assert reloaded.VALUE == 22


@pytest.mark.asyncio
async def test_extension_registers_message_types(tmp_path: Path) -> None:
    """扩展装载期经 ``api.register_message_types`` 注册包级消息类型。

    与工具/用户工具的 ``MESSAGE_TYPES`` 类属性约定共用同一注册表——
    扩展（如 permission_gate 的审批留痕消息）由此获得回载身份。
    """
    from nova_agent import CustomAgentMessage

    from nova_harness.core.harness.session.message_types import (
        clear_session_message_types,
        get_session_message_type,
    )

    ext = tmp_path / "extension.py"
    ext.write_text(
        "from typing import Literal\n"
        "from nova_agent import CustomAgentMessage\n"
        "\n"
        "class DecisionMessage(CustomAgentMessage):\n"
        "    role: Literal['permissionDecision'] = 'permissionDecision'\n"
        "    timestamp: int = 0\n"
        "\n"
        "def extension(api):\n"
        "    api.register_message_types([DecisionMessage])\n",
        encoding="utf-8",
    )

    runtime = ExtensionRuntime(cwd=str(tmp_path), event_bus=ExtensionEventBus())
    clear_session_message_types()
    try:
        result = await load_extensions(
            paths=[str(ext)],
            cwd=str(tmp_path),
            event_bus=runtime.event_bus,
            runtime=runtime,
        )
        assert not result.errors
        registered = get_session_message_type("permissionDecision")
        assert registered is not None
        assert issubclass(registered, CustomAgentMessage)
    finally:
        clear_session_message_types()
