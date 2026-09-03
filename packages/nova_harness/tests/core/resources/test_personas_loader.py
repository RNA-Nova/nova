"""persona 资源加载器（resources/loaders/personas.py）单元测试。

覆盖：命名规则（相对 personas 根去 .md，posix 嵌套形态）、隐藏条目跳过、
单文件条目、读取失败诊断、碰撞 first-wins、disabled 资源跳过、显式路径
缺失告警与非 md 路径告警、SourceInfo 透传。
"""

from pathlib import Path

import pytest
from nova_harness.core.resources.loaders.personas import (
    load_persona_from_file,
    load_personas,
    load_personas_from_dir,
    persona_name_from_path,
)
from nova_harness.core.types.package import (
    PathMetadata,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)


def _resolved(path: Path, *, enabled: bool = True) -> ResolvedResource:
    return ResolvedResource(
        path=str(path.resolve()),
        enabled=enabled,
        metadata=PathMetadata(
            source="auto",
            scope=SourceScope.USER,
            origin=SourceOrigin.TOP_LEVEL,
            base_dir=str(path.resolve().parent),
        ),
    )


@pytest.fixture
def personas_root(tmp_path: Path) -> Path:
    """构造 personas 根目录：嵌套人格 + 隐藏条目 + 非 md 文件。"""
    root = tmp_path / "personas"
    (root / "coding").mkdir(parents=True)
    (root / "subagents").mkdir(parents=True)
    (root / "coding" / "core.md").write_text("核心人格", encoding="utf-8")
    (root / "coding" / "guide.md").write_text("指南", encoding="utf-8")
    (root / "subagents" / "scout.md").write_text("侦察人格", encoding="utf-8")
    (root / ".hidden.md").write_text("隐藏不收", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "ignored.md").write_text("隐藏目录不收", encoding="utf-8")
    (root / "notes.txt").write_text("非 md 不收", encoding="utf-8")
    return root


# =============================================================================
# 命名规则
# =============================================================================


def test_persona_name_from_path_strips_md_and_uses_posix(tmp_path: Path):
    root = tmp_path / "personas"
    nested = root / "coding" / "core.md"
    assert persona_name_from_path(nested, root) == "coding/core"


def test_load_dir_names_by_relative_path(personas_root: Path):
    """目录递归收 .md：嵌套目录去扩展名命名（coding/core、subagents/scout）。"""
    personas, diagnostics = load_personas_from_dir(str(personas_root))

    assert diagnostics == []
    assert sorted(p.name for p in personas) == [
        "coding/core",
        "coding/guide",
        "subagents/scout",
    ]
    by_name = {p.name: p for p in personas}
    assert by_name["coding/core"].content == "核心人格"
    assert by_name["subagents/scout"].file_path.endswith("subagents/scout.md")


def test_load_dir_missing_returns_empty(tmp_path: Path):
    personas, diagnostics = load_personas_from_dir(str(tmp_path / "ghost"))
    assert personas == []
    assert diagnostics == []


# =============================================================================
# 单文件加载
# =============================================================================


def test_load_single_file_uses_stem_name(tmp_path: Path):
    path = tmp_path / "core.md"
    path.write_text("单文件人格", encoding="utf-8")

    persona, diagnostics = load_persona_from_file(str(path))

    assert diagnostics == []
    assert persona is not None
    assert persona.name == "core"
    assert persona.content == "单文件人格"


def test_load_unreadable_file_produces_diagnostic(tmp_path: Path):
    path = tmp_path / "blank.md"
    path.write_text("  \n\t\n", encoding="utf-8")

    persona, diagnostics = load_persona_from_file(str(path))

    assert persona is None
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"
    assert "读取失败" in diagnostics[0].message


# =============================================================================
# Resource 级加载（load_personas）
# =============================================================================


def test_load_personas_from_resolved_dir(personas_root: Path):
    """resolver 目录条目：展开命名 + SourceInfo 透传到每个 persona。"""
    personas, diagnostics = load_personas(resolved_resources=[_resolved(personas_root)])

    assert diagnostics == []
    assert sorted(personas) == ["coding/core", "coding/guide", "subagents/scout"]
    info = personas["coding/core"].source_info
    assert info is not None
    assert info.scope == "user"
    # source_info.path 指向实际 persona 文件，而不是共享的资源根
    assert info.path.endswith("coding/core.md")


def test_load_personas_skips_disabled_resources(personas_root: Path):
    personas, diagnostics = load_personas(
        resolved_resources=[_resolved(personas_root, enabled=False)]
    )
    assert personas == {}
    assert diagnostics == []


def test_load_personas_collision_first_wins(personas_root: Path, tmp_path: Path):
    """同名碰撞：先加载者胜出，后者记 collision 诊断。"""
    other = tmp_path / "other_personas"
    (other / "coding").mkdir(parents=True)
    (other / "coding" / "core.md").write_text("重复人格", encoding="utf-8")

    personas, diagnostics = load_personas(
        resolved_resources=[_resolved(personas_root), _resolved(other)]
    )

    assert personas["coding/core"].content == "核心人格"
    collisions = [d for d in diagnostics if d.category == "collision"]
    assert len(collisions) == 1
    assert collisions[0].collision is not None
    assert collisions[0].collision.name == "coding/core"


def test_load_personas_additional_file_path(tmp_path: Path):
    """显式单文件路径（扩展贡献通道）：stem 命名。"""
    path = tmp_path / "extra.md"
    path.write_text("扩展人格", encoding="utf-8")

    personas, diagnostics = load_personas(additional_paths=[str(path)])

    assert diagnostics == []
    assert list(personas) == ["extra"]


def test_load_personas_additional_missing_warns(tmp_path: Path):
    """显式路径不存在：warning 诊断（自动发现路径不存在则静默）。"""
    personas, diagnostics = load_personas(additional_paths=[str(tmp_path / "ghost")])
    assert personas == {}
    assert len(diagnostics) == 1
    assert "does not exist" in diagnostics[0].message


def test_load_personas_non_markdown_path_warns(tmp_path: Path):
    """显式路径既不是目录也不是 .md：warning。"""
    path = tmp_path / "notes.txt"
    path.write_text("x", encoding="utf-8")

    personas, diagnostics = load_personas(additional_paths=[str(path)])

    assert personas == {}
    assert len(diagnostics) == 1
    assert "not a markdown file" in diagnostics[0].message


def test_load_personas_merges_resolved_and_additional(
    personas_root: Path, tmp_path: Path
):
    """resolver 结果与扩展贡献合并加载。"""
    extra = tmp_path / "extra.md"
    extra.write_text("扩展人格", encoding="utf-8")

    personas, diagnostics = load_personas(
        resolved_resources=[_resolved(personas_root)],
        additional_paths=[str(extra)],
    )

    assert diagnostics == []
    assert sorted(personas) == [
        "coding/core",
        "coding/guide",
        "extra",
        "subagents/scout",
    ]
