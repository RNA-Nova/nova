"""Agent 配置加载器（agent_config.py）单元测试。"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
import yaml

from nova_harness.core.resources.loaders.agent_config import (
    load_agent_config_from_dir,
    load_agent_configs,
    load_agents,
    load_sections,
    load_text_file,
)
from nova_harness.core.types.agent.config import ToolInfo
from nova_harness.core.types.package_manager import (
    PathMetadata,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)


def _write_agent_yaml(agent_dir: Path, data: Dict[str, Any]) -> None:
    """把字典写入 agent.yaml。"""
    (agent_dir / "agent.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture
def make_agent_dir(tmp_path: Path) -> Callable[..., Path]:
    """构造临时 Agent 目录的工厂函数。"""

    def _make_agent_dir(
        name: str = "agent",
        root_dir: Optional[Path] = None,
        description: Optional[str] = "默认描述",
        sections: Optional[Dict[str, str]] = None,
        agent_yaml: Optional[Dict[str, Any]] = None,
    ) -> Path:
        base = root_dir or tmp_path / "agents"
        agent_dir = base / name
        agent_dir.mkdir(parents=True, exist_ok=True)

        # description.md：传入 None 表示不创建
        if description is not None:
            (agent_dir / "description.md").write_text(description, encoding="utf-8")

        # sections/*.md
        if sections is not None:
            sections_dir = agent_dir / "sections"
            sections_dir.mkdir(exist_ok=True)
            for filename, content in sections.items():
                (sections_dir / filename).write_text(content, encoding="utf-8")

        # agent.yaml
        if agent_yaml is not None:
            _write_agent_yaml(agent_dir, agent_yaml)

        return agent_dir

    return _make_agent_dir


# =============================================================================
# load_text_file
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
# load_sections
# =============================================================================


def test_load_sections_numeric_prefix_sort_and_name_handling(tmp_path: Path):
    """数字前缀按数值排序，名称去除前缀并替换 -/_ 为空格。"""
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "02-instructions.md").write_text("指令内容", encoding="utf-8")
    (sections_dir / "10-summary.md").write_text("总结内容", encoding="utf-8")
    (sections_dir / "01_role_definition.md").write_text("角色定义", encoding="utf-8")
    (sections_dir / "notes.md").write_text("备注内容", encoding="utf-8")

    sections = load_sections(str(sections_dir), source_label="system")

    assert [s.order for s in sections] == [1, 2, 3, 4]
    assert [s.name for s in sections] == [
        "role definition",
        "instructions",
        "summary",
        "notes",
    ]


def test_load_sections_empty_directory(tmp_path: Path):
    """空 sections 目录返回空列表。"""
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    assert load_sections(str(sections_dir)) == []


def test_load_sections_missing_directory(tmp_path: Path):
    """sections 目录不存在返回空列表。"""
    assert load_sections(str(tmp_path / "sections")) == []


# =============================================================================
# load_agent_config_from_dir
# =============================================================================


def test_load_agent_config_from_dir_full(make_agent_dir: Callable[..., Path]):
    """完整 Agent 目录可正确加载所有组件。"""
    agent_dir = make_agent_dir(
        name="full_agent",
        description="完整 Agent 描述",
        sections={
            "01-role.md": "你是助手",
            "02-instructions.md": "请认真工作",
        },
        agent_yaml={
            "name": "full_agent",
            "tools": [{"name": "bash", "description": "执行命令"}],
        },
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.name == "full_agent"
    assert config.agent_dir == str(agent_dir)
    assert config.description == "完整 Agent 描述"
    assert [s.name for s in config.sections] == ["role", "instructions"]
    assert config.tools == [ToolInfo(name="bash", description="执行命令")]


def test_load_agent_config_from_dir_only_agent_yaml(make_agent_dir):
    """只有 agent.yaml 没有 description.md 也能加载。"""
    agent_dir = make_agent_dir(
        name="yaml_only",
        description=None,
        agent_yaml={
            "name": "yaml_only",
            "description": "纯 YAML 描述",
            "tools": ["read"],
        },
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.name == "yaml_only"
    assert config.description == "纯 YAML 描述"
    assert [t.name for t in config.tools] == ["read"]


def test_load_agent_config_from_dir_missing_all_returns_none(tmp_path: Path):
    """既无 agent.yaml 也无 description.md 时返回 None。"""
    agent_dir = tmp_path / "empty"
    agent_dir.mkdir()
    assert load_agent_config_from_dir(str(agent_dir)) is None


# =============================================================================
# agent.yaml 字段解析
# =============================================================================


def test_load_agent_config_from_dir_agent_yaml_all_lists(make_agent_dir):
    """agent.yaml 中的 tools/extensions/skills/subagents 字符串列表被正确加载。"""
    agent_dir = make_agent_dir(
        description="agent",
        agent_yaml={
            "tools": ["read", "write"],
            "extensions": ["session_commands"],
            "skills": ["code-review"],
            "subagents": ["planner"],
        },
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert [t.name for t in config.tools] == ["read", "write"]
    assert config.extensions == ["session_commands"]
    assert config.skills == ["code-review"]
    assert config.subagents == ["planner"]


def test_load_agent_config_from_dir_agent_yaml_tool_objects(make_agent_dir):
    """agent.yaml 中的 tools 支持对象列表。"""
    agent_dir = make_agent_dir(
        description="agent",
        agent_yaml={
            "tools": [
                {"name": "read", "description": "读取文件"},
                {"name": "bash"},
            ]
        },
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.tools == [
        ToolInfo(name="read", description="读取文件"),
        ToolInfo(name="bash", description=""),
    ]


def test_load_agent_config_from_dir_agent_yaml_metadata(make_agent_dir):
    """agent.yaml 中的 name/version/author/model 被正确加载。"""
    agent_dir = make_agent_dir(
        description="agent",
        agent_yaml={
            "name": "custom_name",
            "version": "2.0.0",
            "author": "tester",
            "model": "openai/gpt-4o",
        },
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.name == "custom_name"


# =============================================================================
# load_agents
# =============================================================================


def test_load_agents_empty_directory(tmp_path: Path):
    """空 agents 目录返回空字典。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    assert load_agents(str(agents_dir)) == {}


def test_load_agents_skips_non_directory_entries(
    tmp_path: Path, make_agent_dir: Callable[..., Path]
):
    """非目录条目被跳过。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "ignore.txt").write_text("not an agent", encoding="utf-8")
    make_agent_dir(name="valid", root_dir=agents_dir, description="有效")

    agents = load_agents(str(agents_dir))
    assert list(agents.keys()) == ["valid"]


def test_load_agents_multiple_sorted_by_name(
    tmp_path: Path, make_agent_dir: Callable[..., Path]
):
    """多个 Agent 按目录名排序加载。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    make_agent_dir(name="beta", root_dir=agents_dir, description="B")
    make_agent_dir(name="alpha", root_dir=agents_dir, description="A")
    make_agent_dir(name="gamma", root_dir=agents_dir, description="G")

    agents = load_agents(str(agents_dir))
    assert list(agents.keys()) == ["alpha", "beta", "gamma"]


# =============================================================================
# load_agent_configs
# =============================================================================


def _agent_resource(
    path: Path, scope: SourceScope = SourceScope.PROJECT
) -> ResolvedResource:
    return ResolvedResource(
        path=str(path),
        enabled=True,
        metadata=PathMetadata(
            source="auto",
            scope=scope,
            origin=SourceOrigin.TOP_LEVEL,
        ),
    )


def test_load_agent_configs_from_resolved_resources(tmp_path: Path):
    """根据 resolver 提供的 ResolvedResource 列表加载 agent 配置。"""
    agent_dir = tmp_path / "agents" / "test_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(
        yaml.safe_dump({"name": "test_agent"}, allow_unicode=True),
        encoding="utf-8",
    )
    (agent_dir / "description.md").write_text("desc", encoding="utf-8")

    agents = load_agent_configs([_agent_resource(agent_dir)])
    assert "test_agent" in agents
    assert agents["test_agent"].description == "desc"


def test_load_agent_configs_later_overrides_earlier(tmp_path: Path):
    """后出现的 ResolvedResource 覆盖同名 agent。"""
    global_agent = tmp_path / "agents" / "shared"
    global_agent.mkdir(parents=True)
    (global_agent / "agent.yaml").write_text(
        yaml.safe_dump({"name": "shared", "model": "global"}, allow_unicode=True),
        encoding="utf-8",
    )
    (global_agent / "description.md").write_text("global", encoding="utf-8")

    project_agent = tmp_path / "project_agents" / "shared"
    project_agent.mkdir(parents=True)
    (project_agent / "description.md").write_text(
        "---\nmodel: project\n---\n\nproject", encoding="utf-8"
    )

    agents = load_agent_configs(
        [
            _agent_resource(global_agent, SourceScope.USER),
            _agent_resource(project_agent, SourceScope.PROJECT),
        ]
    )
    assert agents["shared"].description == "project"
    assert agents["shared"].model == "project"


# =============================================================================
# Frontmatter 增量覆盖
# =============================================================================


def test_load_agent_config_from_dir_plain_description(make_agent_dir):
    """没有 frontmatter 时 description.md 按纯文本处理。"""
    agent_dir = make_agent_dir(
        description="plain description",
        agent_yaml={"name": "plain"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "plain description"
    assert config.model is None
    assert config.subagents == []


def test_load_agent_config_from_dir_frontmatter_model_and_subagents(make_agent_dir):
    """frontmatter 中的 model 和 subagents 被正确解析。"""
    agent_dir = make_agent_dir(
        description="---\nmodel: claude-haiku-4-5\nsubagents: [planner, worker]\n---\n\nscout agent",
        agent_yaml={"name": "scout"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "scout agent"
    assert config.model == "claude-haiku-4-5"
    assert config.subagents == ["planner", "worker"]


def test_load_agent_config_from_dir_frontmatter_tools_merge_with_yaml(make_agent_dir):
    """frontmatter 中的 tools 与 agent.yaml 合并，按 name 去重。"""
    agent_dir = make_agent_dir(
        description="---\ntools: [find, bash]\n---\n\nagent with tools",
        agent_yaml={
            "name": "merge",
            "tools": [{"name": "read", "description": "read files"}],
        },
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    tool_names = {t.name for t in config.tools}
    assert tool_names == {"read", "find", "bash"}


def test_load_agent_config_from_dir_frontmatter_tool_dicts(make_agent_dir):
    """frontmatter 中的 tools 也支持对象列表。"""
    agent_dir = make_agent_dir(
        description="---\ntools:\n  - name: grep\n    description: search text\n---\n\nagent",
        agent_yaml={"name": "dict_tools"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert any(
        t.name == "grep" and t.description == "search text" for t in config.tools
    )


def test_load_agent_config_from_dir_frontmatter_overrides_yaml(make_agent_dir):
    """frontmatter 可覆盖 agent.yaml 中的列表型字段。"""
    agent_dir = make_agent_dir(
        description="---\nextensions: [extra-ext]\n---\n\nagent",
        agent_yaml={
            "name": "override",
            "extensions": ["base-ext"],
        },
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.extensions == ["base-ext", "extra-ext"]


def test_load_agent_config_from_dir_frontmatter_empty_body(make_agent_dir):
    """frontmatter 后无正文时 description 为 None。"""
    agent_dir = make_agent_dir(
        description="---\nmodel: gpt-4\n---",
        agent_yaml={"name": "empty_body"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description is None
    assert config.model == "gpt-4"


def test_load_agent_config_from_dir_frontmatter_invalid_yaml_falls_back(make_agent_dir):
    """YAML 解析失败时 body 仍被提取，frontmatter 字段忽略。"""
    agent_dir = make_agent_dir(
        description="---\nmodel: [unclosed\n---\nstill works",
        agent_yaml={"name": "invalid_fm"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "still works"
    assert config.model is None


def test_load_agent_configs_frontmatter_overrides_earlier(tmp_path: Path):
    """后出现的项目级 agent 通过 frontmatter 覆盖同名全局 agent。"""
    global_agent = tmp_path / "agents" / "shared"
    global_agent.mkdir(parents=True)
    (global_agent / "agent.yaml").write_text(
        yaml.safe_dump({"name": "shared", "model": "global-model"}, allow_unicode=True),
        encoding="utf-8",
    )
    (global_agent / "description.md").write_text("global", encoding="utf-8")

    project_agent = tmp_path / "project_agents" / "shared"
    project_agent.mkdir(parents=True)
    (project_agent / "description.md").write_text(
        "---\nmodel: project-model\nsubagents: [worker]\n---\n\nproject",
        encoding="utf-8",
    )

    agents = load_agent_configs(
        [
            _agent_resource(global_agent, SourceScope.USER),
            _agent_resource(project_agent, SourceScope.PROJECT),
        ]
    )

    assert agents["shared"].description == "project"
    assert agents["shared"].model == "project-model"
    assert agents["shared"].subagents == ["worker"]


# =============================================================================
# 默认值与空列表
# =============================================================================


def test_load_agent_config_from_dir_whitelist_defaults_empty(make_agent_dir):
    """未提供白名单时，skills / extensions / subagents 默认为空。"""
    agent_dir = make_agent_dir(
        description="plain agent",
        agent_yaml={"name": "plain"},
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.subagents == []
    assert config.skills == []
    assert config.extensions == []
    assert config.tools == []
