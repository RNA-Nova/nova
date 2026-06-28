"""
Agent 配置加载器（agent_config.py）单元测试。
"""

import json
from pathlib import Path
from typing import Callable, Dict, Optional

import pytest

from nova_harness.core.resources.loaders.agent_config import (
    load_agent_config,
    load_agent_config_from_dir,
    load_agents,
    load_json_file,
    load_sections,
    load_text_file,
    load_tools,
    load_user_sections_recursive,
)
from nova_harness.core.types.agent_config import ToolInfo


@pytest.fixture
def make_agent_dir(tmp_path: Path) -> Callable[..., Path]:
    """
    构造临时 Agent 目录的工厂函数。

    默认在 tmp_path/agents/<name> 下创建目录，可通过 root_dir 覆盖父目录。
    """

    def _make_agent_dir(
        name: str = "agent",
        root_dir: Optional[Path] = None,
        description: Optional[str] = "默认描述",
        sections: Optional[Dict[str, str]] = None,
        tools: Optional[list] = None,
        setup: Optional[str] = None,
        user: Optional[Dict[str, str]] = None,
    ) -> Path:
        base = root_dir or tmp_path / "agents"
        agent_dir = base / name
        agent_dir.mkdir(parents=True, exist_ok=True)

        # description.md：传入 None 表示不创建（模拟缺失）
        if description is not None:
            (agent_dir / "description.md").write_text(description, encoding="utf-8")

        # sections/*.md
        if sections is not None:
            sections_dir = agent_dir / "sections"
            sections_dir.mkdir(exist_ok=True)
            for filename, content in sections.items():
                (sections_dir / filename).write_text(content, encoding="utf-8")

        # tools.json
        if tools is not None:
            (agent_dir / "tools.json").write_text(
                json.dumps(tools, ensure_ascii=False), encoding="utf-8"
            )

        # setup.md
        if setup is not None:
            (agent_dir / "setup.md").write_text(setup, encoding="utf-8")

        # user/**/*.md
        if user is not None:
            user_dir = agent_dir / "user"
            user_dir.mkdir(exist_ok=True)
            for relpath, content in user.items():
                target = user_dir / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

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
# load_json_file
# =============================================================================


def test_load_json_file_missing_returns_none(tmp_path: Path):
    """JSON 文件不存在时返回 None。"""
    assert load_json_file(str(tmp_path / "missing.json")) is None


def test_load_json_file_invalid_returns_none(tmp_path: Path):
    """无效 JSON 返回 None。"""
    path = tmp_path / "bad.json"
    path.write_text("not a json", encoding="utf-8")
    assert load_json_file(str(path)) is None


def test_load_json_file_reads_object(tmp_path: Path):
    """正常读取 JSON 对象。"""
    path = tmp_path / "config.json"
    path.write_text('{"name": "test", "value": 42}', encoding="utf-8")
    assert load_json_file(str(path)) == {"name": "test", "value": 42}


# =============================================================================
# load_tools
# =============================================================================


def test_load_tools_normal_array(tmp_path: Path):
    """正常工具数组加载为 ToolInfo 列表。"""
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            [
                {"name": "read_file", "description": "读取文件"},
                {"name": "write_file", "description": "写入文件"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tools = load_tools(str(path))
    assert tools == [
        ToolInfo(name="read_file", description="读取文件"),
        ToolInfo(name="write_file", description="写入文件"),
    ]


def test_load_tools_non_list_returns_empty(tmp_path: Path):
    """JSON 根节点非 list 时返回空列表。"""
    path = tmp_path / "tools.json"
    path.write_text('{"name": "bad"}', encoding="utf-8")
    assert load_tools(str(path)) == []


def test_load_tools_missing_name_skipped(tmp_path: Path):
    """缺少 name 字段的工具条目被跳过。"""
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps(
            [
                {"name": "valid", "description": "有效"},
                {"description": "无 name"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tools = load_tools(str(path))
    assert [t.name for t in tools] == ["valid"]


def test_load_tools_missing_description_defaults_to_empty(tmp_path: Path):
    """缺少 description 时默认空字符串。"""
    path = tmp_path / "tools.json"
    path.write_text(
        json.dumps([{"name": "no_desc"}], ensure_ascii=False),
        encoding="utf-8",
    )
    tools = load_tools(str(path))
    assert tools == [ToolInfo(name="no_desc", description="")]


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
    assert [s.source for s in sections] == [
        "system:01_role_definition.md",
        "system:02-instructions.md",
        "system:10-summary.md",
        "system:notes.md",
    ]


def test_load_sections_no_prefix_sorted_alphabetically(tmp_path: Path):
    """无数字前缀的文件按字母顺序排序。"""
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "zebra.md").write_text("z", encoding="utf-8")
    (sections_dir / "apple.md").write_text("a", encoding="utf-8")
    (sections_dir / "mango.md").write_text("m", encoding="utf-8")

    sections = load_sections(str(sections_dir))
    assert [s.name for s in sections] == ["apple", "mango", "zebra"]


def test_load_sections_empty_directory(tmp_path: Path):
    """空 sections 目录返回空列表。"""
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    assert load_sections(str(sections_dir)) == []


def test_load_sections_missing_directory(tmp_path: Path):
    """sections 目录不存在返回空列表。"""
    assert load_sections(str(tmp_path / "sections")) == []


# =============================================================================
# load_user_sections_recursive
# =============================================================================


def test_load_user_sections_recursive_single_level(tmp_path: Path):
    """单层 user 目录加载所有 .md 文件。"""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "b.md").write_text("B", encoding="utf-8")
    (user_dir / "a.md").write_text("A", encoding="utf-8")

    sections = load_user_sections_recursive(str(user_dir))
    assert [s.name for s in sections] == ["a", "b"]
    assert [s.source for s in sections] == ["user:a.md", "user:b.md"]
    assert [s.order for s in sections] == [1, 2]


def test_load_user_sections_recursive_nested(tmp_path: Path):
    """递归加载嵌套子目录中的 .md 文件，名称保留路径斜杠。"""
    user_dir = tmp_path / "user"
    (user_dir / "sub").mkdir(parents=True)
    (user_dir / "top.md").write_text("top", encoding="utf-8")
    (user_dir / "sub" / "nested.md").write_text("nested", encoding="utf-8")

    sections = load_user_sections_recursive(str(user_dir))
    assert [s.name for s in sections] == ["sub/nested", "top"]
    assert [s.source for s in sections] == ["user:sub/nested.md", "user:top.md"]


def test_load_user_sections_recursive_ignores_non_md(tmp_path: Path):
    """非 .md 文件被忽略。"""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "keep.md").write_text("keep", encoding="utf-8")
    (user_dir / "ignore.txt").write_text("ignore", encoding="utf-8")

    sections = load_user_sections_recursive(str(user_dir))
    assert len(sections) == 1
    assert sections[0].name == "keep"


def test_load_user_sections_recursive_empty_directory(tmp_path: Path):
    """空 user 目录返回空列表。"""
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    assert load_user_sections_recursive(str(user_dir)) == []


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
        tools=[{"name": "bash", "description": "执行命令"}],
        setup="运行前请先检查环境变量。",
        user={"note.md": "用户备注"},
    )

    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.name == "full_agent"
    assert config.agent_dir == str(agent_dir)
    assert config.description == "完整 Agent 描述"
    assert [s.name for s in config.sections] == ["role", "instructions"]
    assert config.tools == [ToolInfo(name="bash", description="执行命令")]
    assert config.setup_content == "运行前请先检查环境变量。"
    assert [s.name for s in config.user_sections] == ["note"]


def test_load_agent_config_from_dir_missing_description_returns_none(
    make_agent_dir: Callable[..., Path],
):
    """缺少 description.md 时返回 None。"""
    agent_dir = make_agent_dir(name="no_desc", description=None)
    assert load_agent_config_from_dir(str(agent_dir)) is None


def test_load_agent_config_from_dir_blank_description_returns_none(
    make_agent_dir: Callable[..., Path],
):
    """description.md 仅含空白时，description 字段为 None。"""
    agent_dir = make_agent_dir(name="blank_desc", description="   \n\t  \n")
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description is None


def test_load_agent_config_from_dir_setup_missing_or_blank(
    make_agent_dir: Callable[..., Path],
):
    """setup.md 缺失或仅含空白时，setup_content 为 None。"""
    no_setup = make_agent_dir(name="no_setup", description="x")
    config = load_agent_config_from_dir(str(no_setup))
    assert config is not None
    assert config.setup_content is None

    blank_setup = make_agent_dir(name="blank_setup", description="x", setup="   \n  ")
    config = load_agent_config_from_dir(str(blank_setup))
    assert config is not None
    assert config.setup_content is None


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
# load_agent_config
# =============================================================================


def test_load_agent_config_merges_global_and_project(tmp_path: Path):
    """全局与项目级 Agent 配置合并，同名时项目级覆盖全局。"""
    global_root = tmp_path / "global"
    project_root = tmp_path / "project" / ".nova"

    # 全局 Agent：global_only 与 shared
    (global_root / "agents" / "global_only").mkdir(parents=True)
    (global_root / "agents" / "global_only" / "description.md").write_text(
        "全局独有", encoding="utf-8"
    )
    (global_root / "agents" / "shared").mkdir(parents=True)
    (global_root / "agents" / "shared" / "description.md").write_text(
        "全局 shared", encoding="utf-8"
    )

    # 项目级 Agent：project_only 与 shared（覆盖）
    (project_root / "agents" / "project_only").mkdir(parents=True)
    (project_root / "agents" / "project_only" / "description.md").write_text(
        "项目独有", encoding="utf-8"
    )
    (project_root / "agents" / "shared").mkdir(parents=True)
    (project_root / "agents" / "shared" / "description.md").write_text(
        "项目 shared", encoding="utf-8"
    )

    agents = load_agent_config(
        cwd=str(tmp_path / "project"),
        agent_dir=str(global_root),
    )

    assert set(agents.keys()) == {"global_only", "project_only", "shared"}
    assert agents["global_only"].description == "全局独有"
    assert agents["project_only"].description == "项目独有"
    assert agents["shared"].description == "项目 shared"


# =============================================================================
# Frontmatter parsing
# =============================================================================


def test_load_agent_config_from_dir_plain_description(make_agent_dir):
    """没有 frontmatter 时 description.md 按纯文本处理。"""
    agent_dir = make_agent_dir(description="plain description")
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "plain description"
    assert config.model is None
    assert config.subagents == []


def test_load_agent_config_from_dir_frontmatter_model_and_subagents(make_agent_dir):
    """frontmatter 中的 model 和 subagents 被正确解析。"""
    agent_dir = make_agent_dir(
        description="---\nmodel: claude-haiku-4-5\nsubagents: [planner, worker]\n---\n\nscout agent"
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "scout agent"
    assert config.model == "claude-haiku-4-5"
    assert config.subagents == ["planner", "worker"]


def test_load_agent_config_from_dir_frontmatter_tools_merge_with_json(make_agent_dir):
    """frontmatter 中的 tools 与 tools.json 合并，按 name 去重。"""
    agent_dir = make_agent_dir(
        description="---\ntools: [find, bash]\n---\n\nagent with tools",
        tools=[{"name": "read", "description": "read files"}],
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    tool_names = {t.name for t in config.tools}
    assert tool_names == {"read", "find", "bash"}


def test_load_agent_config_from_dir_frontmatter_tool_dicts(make_agent_dir):
    """frontmatter 中的 tools 也支持对象列表。"""
    agent_dir = make_agent_dir(
        description="---\ntools:\n  - name: grep\n    description: search text\n---\n\nagent"
    )
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert any(
        t.name == "grep" and t.description == "search text" for t in config.tools
    )


def test_load_agent_config_from_dir_frontmatter_empty_body(make_agent_dir):
    """frontmatter 后无正文时 description 为 None。"""
    agent_dir = make_agent_dir(description="---\nmodel: gpt-4\n---")
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description is None
    assert config.model == "gpt-4"


def test_load_agent_config_from_dir_frontmatter_invalid_yaml_falls_back(make_agent_dir):
    """YAML 解析失败时 body 仍被提取，frontmatter 字段忽略。"""
    agent_dir = make_agent_dir(description="---\nmodel: [unclosed\n---\nstill works")
    config = load_agent_config_from_dir(str(agent_dir))
    assert config is not None
    assert config.description == "still works"
    assert config.model is None


def test_load_agent_config_frontmatter_overrides_global(tmp_path: Path):
    """项目级 frontmatter 正确覆盖全局同名 agent。"""
    global_root = tmp_path / "global"
    project_root = tmp_path / "project" / ".nova"

    (global_root / "agents" / "shared").mkdir(parents=True)
    (global_root / "agents" / "shared" / "description.md").write_text(
        "---\nmodel: global-model\n---\n\nglobal", encoding="utf-8"
    )

    (project_root / "agents" / "shared").mkdir(parents=True)
    (project_root / "agents" / "shared" / "description.md").write_text(
        "---\nmodel: project-model\nsubagents: [worker]\n---\n\nproject",
        encoding="utf-8",
    )

    agents = load_agent_config(
        cwd=str(tmp_path / "project"),
        agent_dir=str(global_root),
    )

    assert agents["shared"].description == "project"
    assert agents["shared"].model == "project-model"
    assert agents["shared"].subagents == ["worker"]
