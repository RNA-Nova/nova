"""扩展加载冲突检测测试。"""

from pathlib import Path

import pytest

from nova_harness.core.resources.loaders.extensions import detect_extension_conflicts
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionFlag,
    ExtensionRuntime,
    SourceInfo,
)


def test_detect_extension_conflicts_flag_conflict():
    ext1 = Extension(
        path="/ext/a",
        flags={"f1": ExtensionFlag(name="f1")},
        commands={},
    )
    ext2 = Extension(
        path="/ext/b",
        flags={"f1": ExtensionFlag(name="f1")},
        commands={},
    )

    conflicts = detect_extension_conflicts([ext1, ext2])
    assert len(conflicts) == 1
    assert 'Flag "--f1" conflicts with /ext/a' in conflicts[0].message
    assert conflicts[0].path == "/ext/b"


def test_detect_extension_conflicts_no_duplicate_for_same_ext():
    ext = Extension(
        path="/ext/a",
        flags={"f1": ExtensionFlag(name="f1")},
        commands={},
    )

    conflicts = detect_extension_conflicts([ext])
    assert conflicts == []


def test_detect_extension_conflicts_first_wins():
    ext1 = Extension(path="/ext/a", flags={"f1": ExtensionFlag(name="f1")})
    ext2 = Extension(path="/ext/b", flags={"f1": ExtensionFlag(name="f1")})
    ext3 = Extension(path="/ext/c", flags={"f1": ExtensionFlag(name="f1")})

    conflicts = detect_extension_conflicts([ext1, ext2, ext3])
    assert len(conflicts) == 2
    assert all("conflicts with /ext/a" in c.message for c in conflicts)


@pytest.mark.asyncio
async def test_load_extensions_includes_conflict_diagnostics(tmp_path: Path):
    """加载两个同名 flag 的扩展，结果 diagnostics 中应包含冲突。"""
    from nova_harness.core.resources.loaders.extensions import load_extensions

    ext_a = tmp_path / "ext_a.py"
    ext_a.write_text(
        "def extension(api):\n"
        "    api.registerFlag('same_flag', {'type': 'boolean'})\n",
        encoding="utf-8",
    )
    ext_b = tmp_path / "ext_b.py"
    ext_b.write_text(
        "def extension(api):\n"
        "    api.registerFlag('same_flag', {'type': 'boolean'})\n",
        encoding="utf-8",
    )

    from nova_harness.core.types.package import (
        PathMetadata,
        ResolvedResource,
        SourceScope,
    )

    resolved = [
        ResolvedResource(
            path=str(ext_a),
            enabled=True,
            metadata=PathMetadata(
                source="settings", scope=SourceScope.PROJECT, origin="top-level"
            ),
        ),
        ResolvedResource(
            path=str(ext_b),
            enabled=True,
            metadata=PathMetadata(
                source="settings", scope=SourceScope.PROJECT, origin="top-level"
            ),
        ),
    ]

    result = await load_extensions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model_runtime=None,
        event_bus=None,
        resolved_paths=resolved,
    )

    assert len(result.extensions) == 2
    assert any('Flag "--same_flag" conflicts' in d.message for d in result.diagnostics)
