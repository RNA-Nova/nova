"""Agent 组合声明加载器（agent_config.py）单元测试。

组合层模型：agent = 单个 yaml 组合声明文件（``agents/<name>.yaml``）。
**persona 升格后本加载器只做解析不做装配**：``persona:`` 条目原样保留进
``AgentConfig.persona``（不触文件系统、不产生装配诊断），``sections`` 恒
为空——装配归会话期 ``PersonaManager``（测试见
``tests/core/harness/persona/test_manager.py``）。description.md frontmatter、
sections/ 目录约定等旧模型已退役，不再覆盖。
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
import yaml

from nova_harness.core.resources.loaders.agent_config import (
    load_agent_config_from_yaml,
    load_agents,
)
from nova_harness.core.types.resources.tools import ToolInfo
from nova_harness.core.utils.files import load_text_file


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """把字典写入 yaml 文件（自动建父目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture
def make_agent(tmp_path: Path) -> Callable[..., Path]:
    """构造临时 agent 组合声明的工厂：写 ``agents/<name>.yaml`` + persona 素材。

    返回 yaml 文件路径。``persona`` 字段由调用方经 yaml_data 显式给出；
    ``materials`` 是相对 agents 目录的素材文件映射（路径 → 内容）。
    """

    def _make_agent(
        name: str = "agent",
        root_dir: Optional[Path] = None,
        yaml_data: Optional[Dict[str, Any]] = None,
        materials: Optional[Dict[str, str]] = None,
    ) -> Path:
        base = root_dir or tmp_path / "agents"
        base.mkdir(parents=True, exist_ok=True)
        for rel, content in (materials or {}).items():
            file_path = base / rel
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        yaml_path = base / f"{name}.yaml"
        _write_yaml(yaml_path, yaml_data if yaml_data is not None else {"name": name})
        return yaml_path

    return _make_agent


# =============================================================================
# load_text_file（persona 素材读取依赖的工具函数）
# =============================================================================


def test_load_text_file_missing_returns_none(tmp_path: Path):
    """文件不存在时返回 None。"""
    assert load_text_file(str(tmp_path / "missing.md")) is None


def test_load_text_file_empty_returns_none(tmp_path: Path):
    """空文件返回 None。"""
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    assert load_text_file(str(path)) is None


def test_load_text_file_whitespace_returns_none(tmp_path: Path):
    """仅包含空白字符的文件返回 None。"""
    path = tmp_path / "blank.md"
    path.write_text("   \n\t  \n", encoding="utf-8")
    assert load_text_file(str(path)) is None


def test_load_text_file_reads_and_strips_content(tmp_path: Path):
    """正常读取并去除首尾空白。"""
    path = tmp_path / "content.md"
    path.write_text("  Hello, Agent!  \n\n", encoding="utf-8")
    assert load_text_file(str(path)) == "Hello, Agent!"


# =============================================================================
# load_agent_config_from_yaml：基本加载与诊断
# =============================================================================


def test_load_full_yaml_all_fields(make_agent: Callable[..., Path]):
    """完整组合声明：元数据 + persona + 全部能力名单。"""
    yaml_path = make_agent(
        name="full_agent",
        yaml_data={
            "name": "full_agent",
            "version": "1.2.0",
            "description": "完整 Agent 描述",
            "author": "tester",
            "model": "openai/gpt-4o",
            "persona": ["personas/role.md", "personas/instructions.md"],
            "tools": [{"name": "bash", "description": "执行命令"}, "read"],
            "extensions": ["session_commands"],
            "user_tools": ["bash"],
            "commands": ["tree", "export"],
            "skills": ["code-review"],
        },
        materials={
            "personas/role.md": "你是助手",
            "personas/instructions.md": "请认真工作",
        },
    )

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.name == "full_agent"
    assert config.agent_dir == str(yaml_path.parent)
    assert config.description == "完整 Agent 描述"
    assert config.model == "openai/gpt-4o"
    # persona 升格：load 只保留原始条目，sections 由 PersonaManager 会话期装配
    assert config.persona == ["personas/role.md", "personas/instructions.md"]
    assert config.sections == []
    assert config.tools == [
        ToolInfo(name="bash", description="执行命令"),
        ToolInfo(name="read", description=""),
    ]
    assert config.extensions == ["session_commands"]
    assert config.user_tools == ["bash"]
    assert config.commands == ["tree", "export"]
    assert config.skills == ["code-review"]


def test_load_name_defaults_to_file_stem(make_agent: Callable[..., Path]):
    """yaml 未声明 name 时取文件名 stem。"""
    yaml_path = make_agent(name="coding_agent", yaml_data={"description": "无名 agent"})

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.name == "coding_agent"


def test_load_minimal_yaml_defaults_none(make_agent: Callable[..., Path]):
    """最小 yaml：名单键缺席 → None（三态之"不设防"）。"""
    yaml_path = make_agent(yaml_data={"name": "minimal"})

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.description is None
    assert config.model is None
    assert config.sections == []
    assert config.persona == []
    assert config.tools is None
    assert config.extensions is None
    assert config.user_tools is None
    assert config.commands is None
    assert config.skills is None


def test_load_missing_file_returns_none_with_diagnostic(tmp_path: Path):
    """yaml 不存在：返回 None 并产生诊断。"""
    config, diagnostics = load_agent_config_from_yaml(str(tmp_path / "ghost.yaml"))

    assert config is None
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"
    assert "不存在" in diagnostics[0].message


def test_load_invalid_yaml_returns_none_with_diagnostic(make_agent: Callable):
    """非法 yaml：返回 None 并产生解析失败诊断。"""
    yaml_path = make_agent(name="broken")
    yaml_path.write_text("name: [unclosed\n  - bad", encoding="utf-8")

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert config is None
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"
    assert "解析失败" in diagnostics[0].message


def test_load_non_mapping_yaml_returns_none(make_agent: Callable):
    """yaml 顶层不是 mapping：返回 None 并产生诊断。"""
    yaml_path = make_agent(name="list_top")
    yaml_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert config is None
    assert len(diagnostics) == 1
    assert "mapping" in diagnostics[0].message


# =============================================================================
# persona 条目解析（原样保留；装配归会话期 PersonaManager）
# =============================================================================


def test_persona_entries_kept_raw_without_touching_filesystem(
    make_agent: Callable[..., Path],
):
    """persona 条目按声明顺序原样保留；load 不读素材文件（缺失也不产生诊断）。"""
    yaml_path = make_agent(
        yaml_data={"persona": ["ghost.md", "personas", "coding/core"]},
        materials={"personas/01-a.md": "A"},
    )

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.persona == ["ghost.md", "personas", "coding/core"]
    assert config.sections == []


def test_persona_non_string_entries_filtered(make_agent: Callable[..., Path]):
    """persona 条目经字符串名单解析：非字符串项忽略、去重去空白。"""
    yaml_path = make_agent(
        yaml_data={"persona": [42, {"path": "x.md"}, " real.md ", "real.md"]},
    )

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.persona == ["real.md"]


def test_persona_absent_defaults_to_empty_list(make_agent: Callable[..., Path]):
    """persona 键缺席 → 空列表（persona 不是名单，无三态——缺席即无人格引用）。"""
    yaml_path = make_agent(yaml_data={"name": "no_persona"})

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert diagnostics == []
    assert config is not None
    assert config.persona == []
    assert config.sections == []


# =============================================================================
# 名单解析（tools / extensions / user_tools / commands / skills）
# =============================================================================


def test_tools_entries_string_object_and_dedup(make_agent: Callable[..., Path]):
    """tools 支持字符串与对象条目，按 name 去重（先出现者胜）。"""
    yaml_path = make_agent(
        yaml_data={
            "tools": [
                "read",
                {"name": "bash", "description": "执行命令"},
                {"name": "read", "description": "重复条目被丢弃"},
                {"no_name": "缺 name 忽略"},
                "  ",
            ]
        },
    )

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert config is not None
    assert config.tools == [
        ToolInfo(name="read", description=""),
        ToolInfo(name="bash", description="执行命令"),
    ]


def test_string_lists_filtered_and_deduped(make_agent: Callable[..., Path]):
    """字符串名单：去重、去空白、忽略非字符串项；``!`` 前缀原样保留。"""
    yaml_path = make_agent(
        yaml_data={
            "extensions": ["a", "a", " b ", 3],
            "user_tools": ["bash", "bash"],
            "commands": ["tree", "!debug"],
            "skills": ["s1", "s2"],
        },
    )

    config, diagnostics = load_agent_config_from_yaml(str(yaml_path))

    assert config is not None
    assert config.extensions == ["a", "b"]
    assert config.user_tools == ["bash"]
    assert config.commands == ["tree", "!debug"]
    assert config.skills == ["s1", "s2"]


# =============================================================================
# load_agents（目录扫描）
# =============================================================================


def test_load_agents_scans_top_level_yaml_files(tmp_path: Path):
    """扫描目录顶层 *.yaml/*.yml（一文件一 agent），不递归子目录。"""
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir / "alpha.yaml", {"name": "alpha"})
    _write_yaml(agents_dir / "beta.yml", {"description": "无 name 取 stem"})
    _write_yaml(agents_dir / "nested" / "gamma.yaml", {"name": "gamma"})
    (agents_dir / "notes.md").write_text("不是组合声明", encoding="utf-8")

    agents = load_agents(str(agents_dir))

    assert sorted(agents.keys()) == ["alpha", "beta"]
    assert agents["beta"].description == "无 name 取 stem"


def test_load_agents_keys_by_config_name(tmp_path: Path):
    """返回 dict 以 config.name 为键（yaml 内 name 优先于文件名 stem）。"""
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir / "file_name.yaml", {"name": "declared_name"})

    agents = load_agents(str(agents_dir))

    assert list(agents.keys()) == ["declared_name"]


def test_load_agents_skips_invalid_yaml(tmp_path: Path):
    """坏 yaml 被跳过，好 yaml 正常返回。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "broken.yaml").write_text("name: [unclosed", encoding="utf-8")
    _write_yaml(agents_dir / "good.yaml", {"name": "good"})

    agents = load_agents(str(agents_dir))

    assert list(agents.keys()) == ["good"]


def test_load_agents_missing_dir_returns_empty(tmp_path: Path):
    """目录不存在返回空 dict。"""
    assert load_agents(str(tmp_path / "missing")) == {}
