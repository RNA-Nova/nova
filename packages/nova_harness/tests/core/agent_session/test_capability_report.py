"""CapabilitySelection 报告：全资源域的会话级汇集测试。

链路：各 yaml 名单过滤点产出报告 → ``_build_runtime`` 末尾汇集 →
AgentManager 经注入 provider 透出（manager 互不调用，编排在 AgentSession）。

覆盖：
- extensions：ok / missing / disabled_by_settings（settings 路径级 pattern
  裁掉的扩展经 loader 命名推导归因）+ 首建过滤生效（回归：首次
  ``_build_runtime`` 时 SystemPromptManager 尚未创建，过滤须走 AgentManager）；
- user_tools：settings 名字级 pattern 精确归因 disabled_by_settings；
- commands：宇宙 = 扩展命令 + prompt/skill 命令；disabled_commands 归因；
- skills：包内 skill 点名缺失 → missing；
- personas：yaml persona 条目注册表未命中 → missing；
- 汇集：多域报告经 AgentManager 一次透出。
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from nova_harness.core.agent_session.agent import AgentSession
from nova_harness.core.types.extensions import Extension, RegisteredCommand
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.session.config import AgentSessionConfig


def _extension(name: str, commands: Optional[List[str]] = None) -> Extension:
    return Extension(
        path=f"/fake/ext/{name}.py",
        name=name,
        commands={c: RegisteredCommand(name=c, description=c) for c in commands or []},
    )


def _user_tool_resource() -> Any:
    return SimpleNamespace(
        create=lambda session: SimpleNamespace(name="ut", execute=lambda *a: None)
    )


def _package_skill(name: str) -> Any:
    return SimpleNamespace(name=name, source_info=SimpleNamespace(origin="package"))


def _make_session(
    *,
    agent_config: AgentConfig,
    extensions: Optional[List[Extension]] = None,
    disabled_extension_names: Optional[set] = None,
    user_tools: Optional[Dict[str, Any]] = None,
    skills: Optional[Dict[str, Any]] = None,
    settings_user_tools: Optional[List[str]] = None,
    disabled_commands: Optional[List[str]] = None,
) -> AgentSession:
    """构造真实内部 manager 的会话（loader/settings 为可控假件）。"""
    loader = MagicMock()
    loader.get_extensions.return_value = SimpleNamespace(
        extensions=list(extensions or []), runtime=None
    )
    loader.get_disabled_extension_names.return_value = set(
        disabled_extension_names or ()
    )
    loader.get_agents.return_value = {agent_config.name: agent_config}
    loader.get_tools.return_value = {"tools": {}}
    loader.get_user_tools.return_value = {"user_tools": dict(user_tools or {})}
    loader.get_skills.return_value = {"skills": dict(skills or {})}
    loader.get_personas.return_value = {"personas": {}}
    loader.get_prompts.return_value = {"prompts": []}
    loader.get_context_files.return_value = []

    settings = MagicMock()
    settings.get_settings.return_value = SimpleNamespace(
        user_tools=settings_user_tools,
        disabled_commands=disabled_commands or [],
    )

    agent = MagicMock()
    agent.state.messages = []

    return AgentSession(
        AgentSessionConfig(
            agent=agent,
            session_manager=MagicMock(),
            settings_manager=settings,
            cwd="/tmp",
            resource_loader=loader,
            model_runtime=MagicMock(),
            agent_name=agent_config.name,
        )
    )


def _report_by_name(session: AgentSession, resource_type: str) -> Dict[str, str]:
    return {
        s.name: s.status
        for s in session.agent_manager.get_capability_report()
        if s.resource_type == resource_type
    }


# =============================================================================
# extensions
# =============================================================================


def test_extensions_report_statuses_and_first_build_filter():
    """yaml 点名扩展：ok / missing / disabled_by_settings；首建过滤即生效。

    回归断言：首次 ``_build_runtime``（SystemPromptManager 尚未创建）扩展
    名单过滤也必须生效——config 经 AgentManager 现取。
    """
    session = _make_session(
        agent_config=AgentConfig(
            name="a",
            agent_dir="/x",
            extensions=["ext_a", "ghost_ext", "disabled_ext"],
        ),
        extensions=[_extension("ext_a"), _extension("ext_b")],
        disabled_extension_names={"disabled_ext"},
    )
    # 首建过滤生效：名单外（ext_b）不进注册表
    assert [e.name for e in session._extension_runner.extensions] == ["ext_a"]
    assert _report_by_name(session, "extensions") == {
        "ext_a": "ok",
        "ghost_ext": "missing",
        "disabled_ext": "disabled_by_settings",
    }


def test_extensions_report_empty_without_yaml_list():
    session = _make_session(
        agent_config=AgentConfig(name="a", agent_dir="/x"),
        extensions=[_extension("ext_a")],
    )
    assert _report_by_name(session, "extensions") == {}


# =============================================================================
# user_tools
# =============================================================================


def test_user_tools_report_missing_and_disabled_by_settings():
    """settings 名字级 pattern 裁掉的精确归因 disabled_by_settings。"""
    session = _make_session(
        agent_config=AgentConfig(
            name="a", agent_dir="/x", user_tools=["ut_ok", "ut_cut", "ghost_ut"]
        ),
        user_tools={"ut_ok": _user_tool_resource(), "ut_cut": _user_tool_resource()},
        settings_user_tools=["!ut_cut"],
    )
    assert _report_by_name(session, "user_tools") == {
        "ut_ok": "ok",
        "ut_cut": "disabled_by_settings",
        "ghost_ut": "missing",
    }


# =============================================================================
# commands
# =============================================================================


def test_commands_report_statuses():
    """宇宙 = 扩展命令 + prompt/skill 命令；disabled_commands 归因 settings。"""
    session = _make_session(
        agent_config=AgentConfig(
            name="a", agent_dir="/x", commands=["tree", "skill:s1", "ghost_cmd"]
        ),
        extensions=[_extension("ext_a", commands=["tree"])],
        skills={"s1": _package_skill("s1")},
        disabled_commands=["tree"],
    )
    assert _report_by_name(session, "commands") == {
        "tree": "disabled_by_settings",
        "skill:s1": "ok",
        "ghost_cmd": "missing",
    }


# =============================================================================
# skills
# =============================================================================


def test_skills_report_package_skill_missing():
    """yaml 点名的包内 skill 不存在 → missing；存在且过白名单 → ok。"""
    session = _make_session(
        agent_config=AgentConfig(
            name="a", agent_dir="/x", skills=["s1", "ghost_skill"]
        ),
        skills={"s1": _package_skill("s1")},
    )
    assert _report_by_name(session, "skills") == {
        "s1": "ok",
        "ghost_skill": "missing",
    }


# =============================================================================
# personas
# =============================================================================


def test_persona_report_missing_entry():
    """yaml persona 条目注册表未命中（且非路径）→ personas 域 missing。"""
    session = _make_session(
        agent_config=AgentConfig(name="a", agent_dir="/x", persona=["ghost_persona"]),
    )
    assert _report_by_name(session, "personas") == {"ghost_persona": "missing"}


# =============================================================================
# 汇集
# =============================================================================


def test_capability_report_aggregates_all_resource_types():
    """多域报告经 AgentManager 一次透出（provider 由会话注入）。"""
    session = _make_session(
        agent_config=AgentConfig(
            name="a",
            agent_dir="/x",
            extensions=["ghost_ext"],
            user_tools=["ghost_ut"],
            commands=["ghost_cmd"],
            skills=["ghost_skill"],
            persona=["ghost_persona"],
        ),
    )
    report = {
        (s.resource_type, s.name, s.status)
        for s in session.agent_manager.get_capability_report()
    }
    assert report == {
        ("extensions", "ghost_ext", "missing"),
        ("user_tools", "ghost_ut", "missing"),
        ("commands", "ghost_cmd", "missing"),
        ("skills", "ghost_skill", "missing"),
        ("personas", "ghost_persona", "missing"),
    }
