"""PersonaManager 单元测试（persona 升格：装配乔迁 + override 旋钮）。

覆盖：
- 路径装配：文件条目按声明顺序、目录条目递归字典序原地展开、混合条目；
- 包根收敛：路径引用 resolve 后不在 base_dir 内 → 诊断并跳过；
- 按名装配：路径解析不了的条目查 persona 注册表；
- 诊断：路径不存在且注册表无名、目录无 .md、文件读取失败；
- CapabilitySelection 报告：注册名未命中 → personas 域 missing，重装后清空；
- override：替换人格部分为单份 content、目标消失回退默认装配、clear、
  未知名 set 抛 ValueError。

（load 期不再装配——解析层断言见 ``tests/core/resources/test_agent_config.py``。）
"""

from pathlib import Path
from typing import Callable, Dict, Optional

import pytest

from nova_harness.core.harness.persona import PersonaManager
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.personas import Persona


class _FakeLoader:
    """最小 ResourceLoader 假件：只承载 persona 注册表。"""

    def __init__(self, personas: Optional[Dict[str, Persona]] = None) -> None:
        self._personas = personas or {}

    def get_personas(self) -> Dict[str, object]:
        return {"personas": self._personas, "diagnostics": []}


def _persona(name: str, content: str, path: Optional[str] = None) -> Persona:
    return Persona(
        name=name,
        content=content,
        file_path=path or f"/fake/personas/{name}.md",
    )


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., AgentConfig]:
    """构造临时 AgentConfig：``agents/<name>.yaml`` 所在目录 + 素材文件。

    ``materials`` 是相对 agents 目录的素材文件映射（路径 → 内容）；
    ``persona`` 为 yaml 原始条目；``base_dir`` 写入 source_info（包根收敛边界）。
    """

    def _make(
        name: str = "agent",
        persona: Optional[list] = None,
        materials: Optional[Dict[str, str]] = None,
        base_dir: Optional[Path] = None,
    ) -> AgentConfig:
        # base_dir 给定时 agents/ 置于其内（包内布局：agents/ 在包根下）
        agents_dir = (base_dir or tmp_path) / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for rel, content in (materials or {}).items():
            file_path = agents_dir / rel
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        source_info = (
            SourceInfo(
                path=str(agents_dir / f"{name}.yaml"),
                source="local",
                scope="user",
                origin="top-level",
                base_dir=str(base_dir),
            )
            if base_dir is not None
            else None
        )
        return AgentConfig(
            name=name,
            agent_dir=str(agents_dir),
            persona=list(persona or []),
            source_info=source_info,
        )

    return _make


# =============================================================================
# 路径装配（自 agent_config loader 乔迁的语义）
# =============================================================================


def test_file_entries_keep_declaration_order(make_config: Callable[..., AgentConfig]):
    """文件条目按声明顺序组装（与文件名字典序无关），stem 不做前缀剥离。"""
    config = make_config(
        persona=["p/zz-last.md", "p/01-first.md"],
        materials={"p/zz-last.md": "后声明但文件名靠前", "p/01-first.md": "先声明"},
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [(s.name, s.order, s.content) for s in sections] == [
        ("zz-last", 1, "后声明但文件名靠前"),
        ("01-first", 2, "先声明"),
    ]
    # source 记录素材的真实路径
    assert sections[0].source == str(
        (Path(config.agent_dir) / "p" / "zz-last.md").resolve()
    )


def test_directory_entry_expands_sorted(make_config: Callable[..., AgentConfig]):
    """目录条目：递归收 *.md，按相对路径字典序在该位置展开。"""
    config = make_config(
        persona=["personas"],
        materials={
            "personas/02-b.md": "B",
            "personas/01-a.md": "A",
            "personas/sub/03-c.md": "C",
            "personas/notes.txt": "非 md 不收",
        },
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [(s.name, s.order) for s in sections] == [
        ("01-a", 1),
        ("02-b", 2),
        ("03-c", 3),
    ]
    assert [s.content for s in sections] == ["A", "B", "C"]


def test_mixed_entries_expand_in_place(make_config: Callable[..., AgentConfig]):
    """文件与目录混合：目录在声明位置原地展开，整体顺序不变。"""
    config = make_config(
        persona=["intro.md", "parts", "outro.md"],
        materials={
            "intro.md": "开场",
            "parts/01-x.md": "X",
            "parts/02-y.md": "Y",
            "outro.md": "收尾",
        },
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [s.content for s in sections] == ["开场", "X", "Y", "收尾"]
    assert [s.order for s in sections] == [1, 2, 3, 4]


def test_empty_persona_assembles_to_nothing(make_config: Callable[..., AgentConfig]):
    """无 persona 条目：空 sections、零诊断。"""
    config = make_config()
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert sections == []
    assert diagnostics == []


# =============================================================================
# 包根收敛
# =============================================================================


def test_path_escaping_base_dir_rejected(
    make_config: Callable[..., AgentConfig], tmp_path: Path
):
    """路径引用 resolve 后不在 source_info.base_dir 内 → 诊断并跳过。"""
    package_root = tmp_path / "pkg"
    config = make_config(
        persona=["../../outside.md", "ok.md"],
        materials={"ok.md": "包内素材"},
        base_dir=package_root,
    )
    # agents/ 在包根内；outside.md 写到包根之外（tmp_path 下）
    (tmp_path / "outside.md").write_text("包外素材", encoding="utf-8")
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert [s.content for s in sections] == ["包内素材"]
    assert len(diagnostics) == 1
    assert "逃逸包根" in diagnostics[0].message


def test_path_within_base_dir_allowed(
    make_config: Callable[..., AgentConfig], tmp_path: Path
):
    """包内 ``..`` 引用（agents/../backend/personas）合法——安装即信任的包内资源。"""
    package_root = tmp_path / "pkg"
    backend = package_root / "backend" / "personas"
    backend.mkdir(parents=True)
    (backend / "core.md").write_text("包内人格", encoding="utf-8")
    config = make_config(
        persona=["../backend/personas/core.md"],
        base_dir=package_root,
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [s.content for s in sections] == ["包内人格"]


# =============================================================================
# 按名装配（注册表查找）
# =============================================================================


def test_registry_name_entry_assembles_from_registry(
    make_config: Callable[..., AgentConfig],
):
    """路径解析不了的条目按注册名查 persona 注册表。"""
    config = make_config(persona=["coding/core"])
    manager = PersonaManager(
        resource_loader=_FakeLoader(
            {"coding/core": _persona("coding/core", "注册表人格")}
        )
    )

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [(s.name, s.order, s.content) for s in sections] == [
        ("coding/core", 1, "注册表人格")
    ]


def test_path_entry_wins_over_registry_name(make_config: Callable[..., AgentConfig]):
    """同名歧义：能解析为路径的条目按路径装配（路径优先于注册名）。"""
    config = make_config(
        persona=["core.md"],
        materials={"core.md": "路径人格"},
    )
    manager = PersonaManager(
        resource_loader=_FakeLoader({"core.md": _persona("core.md", "注册表人格")})
    )

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [s.content for s in sections] == ["路径人格"]


# =============================================================================
# 诊断
# =============================================================================


def test_unresolvable_entry_produces_diagnostic(
    make_config: Callable[..., AgentConfig],
):
    """路径不存在且注册表无名：诊断并跳过，其余条目照常装配。"""
    config = make_config(
        persona=["ghost.md", "real.md"],
        materials={"real.md": "真实素材"},
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert [s.content for s in sections] == ["真实素材"]
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"
    assert "无法解析" in diagnostics[0].message


# =============================================================================
# CapabilitySelection 报告（personas 域）
# =============================================================================


def test_selection_report_marks_unresolvable_entry_missing(
    make_config: Callable[..., AgentConfig],
):
    """按名引用查不到注册表（且非路径）的 yaml 条目 → personas 域 missing。"""
    config = make_config(
        persona=["ghost_persona", "real.md"],
        materials={"real.md": "真实素材"},
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    manager.assemble(config)

    assert [(s.resource_type, s.name, s.status) for s in manager.selection_report] == [
        ("personas", "ghost_persona", "missing")
    ]


def test_selection_report_cleared_after_clean_assemble(
    make_config: Callable[..., AgentConfig],
):
    """再次装配成功（或条目消失）后报告清空——报告反映最近一次装配。"""
    manager = PersonaManager(resource_loader=_FakeLoader())
    manager.assemble(make_config(persona=["ghost_persona"]))
    assert len(manager.selection_report) == 1

    manager.assemble(make_config(persona=[], materials={}))
    assert manager.selection_report == []


def test_directory_without_markdown_produces_diagnostic(
    make_config: Callable[..., AgentConfig],
):
    """persona 目录没有任何 .md：产生诊断。"""
    config = make_config(
        persona=["empty_dir"],
        materials={"empty_dir/readme.txt": "不是 md"},
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert sections == []
    assert len(diagnostics) == 1
    assert "无 .md" in diagnostics[0].message


def test_unreadable_file_produces_diagnostic(make_config: Callable[..., AgentConfig]):
    """persona 文件读不出内容（纯空白）：产生读取失败诊断。"""
    config = make_config(
        persona=["blank.md"],
        materials={"blank.md": "   \n"},
    )
    manager = PersonaManager(resource_loader=_FakeLoader())

    sections, diagnostics = manager.assemble(config)

    assert sections == []
    assert len(diagnostics) == 1
    assert "读取失败" in diagnostics[0].message


def test_last_diagnostics_tracked(make_config: Callable[..., AgentConfig]):
    """最近一次的装配诊断在 manager 上可读。"""
    config = make_config(persona=["ghost.md"])
    manager = PersonaManager(resource_loader=_FakeLoader())

    manager.assemble(config)

    assert len(manager.last_diagnostics) == 1


# =============================================================================
# override 旋钮
# =============================================================================


def test_override_replaces_persona_sections(make_config: Callable[..., AgentConfig]):
    """override 生效：人格部分 = override persona 的单份 content（能力面不动）。"""
    config = make_config(
        persona=["role.md"],
        materials={"role.md": "默认人格"},
    )
    manager = PersonaManager(
        resource_loader=_FakeLoader({"alt": _persona("alt", "替代人格")})
    )
    manager.set_persona_override("alt")

    sections, diagnostics = manager.assemble(config)

    assert diagnostics == []
    assert [(s.name, s.order, s.content) for s in sections] == [("alt", 1, "替代人格")]
    assert manager.current_override == "alt"


def test_clear_override_restores_default(make_config: Callable[..., AgentConfig]):
    """clear 后恢复角色默认装配。"""
    config = make_config(persona=["role.md"], materials={"role.md": "默认人格"})
    manager = PersonaManager(
        resource_loader=_FakeLoader({"alt": _persona("alt", "替代人格")})
    )
    manager.set_persona_override("alt")
    manager.clear_persona_override()

    sections, _ = manager.assemble(config)

    assert manager.current_override is None
    assert [s.content for s in sections] == ["默认人格"]


def test_set_unknown_override_raises():
    """set_persona_override 未知名：抛 ValueError 并附可用名单。"""
    manager = PersonaManager(
        resource_loader=_FakeLoader({"core": _persona("core", "x")})
    )

    with pytest.raises(ValueError, match="persona 不存在"):
        manager.set_persona_override("ghost")


def test_override_target_missing_falls_back(make_config: Callable[..., AgentConfig]):
    """override 目标已不在注册表（reload 后被裁）：回退默认装配 + 诊断。"""
    config = make_config(persona=["role.md"], materials={"role.md": "默认人格"})
    loader = _FakeLoader({"alt": _persona("alt", "替代人格")})
    manager = PersonaManager(resource_loader=loader)
    manager.set_persona_override("alt")
    # 注册表收缩（如包卸载后 reload）
    loader._personas = {}

    sections, diagnostics = manager.assemble(config)

    assert [s.content for s in sections] == ["默认人格"]
    assert any("回退角色默认装配" in d.message for d in diagnostics)


def test_personas_view_without_loader():
    """无 loader（standalone）：注册表为空，override set 抛错。"""
    manager = PersonaManager(resource_loader=None)

    assert manager.personas == {}
    with pytest.raises(ValueError):
        manager.set_persona_override("x")
