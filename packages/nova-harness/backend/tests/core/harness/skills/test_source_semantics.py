"""skills 来源分治语义：全场景真实测试。

"真实" = 真实文件系统 + 真实包安装持久化（`install_and_persist` 写真 settings，
pip/npm 打桩）+ 真 resolver/loader 管线 + 真 SDK 会话运行时（核心链路零 mock）。

场景矩阵：
- 发现：user `.agents` / user `~/.nova/agent/skills` / project `.agents`（祖先）/ project `.nova`
- 过滤：空名单全放（含包内）/ 非空名单仅裁包内（命中放行、未命中裁剪）/ 混源非包全放 /
  CLI 显式路径
- 门控：trust=False 只拦 project scope
- 消费：附录含与不含、`/skill:` 展开、`disable_model_invocation`、同名碰撞、
  包内 agent 名单裁剪包内 skill
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.harness.skills import (
    expand_skill_command,
    filter_skills_by_whitelist,
)
from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.package.enums import SourceScope
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.skills import Skill

# ---------------------------------------------------------------------------
# 环境构造
# ---------------------------------------------------------------------------


def _skill_md(
    parent: Path,
    name: str,
    description: str,
    *,
    disable_model_invocation: bool = False,
) -> Path:
    """在 parent/<name>/SKILL.md 写一个真实 skill 文件。"""
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    flag = "disable_model_invocation: true\n" if disable_model_invocation else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{flag}---\n\n# {name} 的正文指令\n",
        encoding="utf-8",
    )
    return d


def _write_project_agent(
    cwd: Path, skills_whitelist: Optional[List[str]] = None
) -> None:
    """写项目级 agent 组合声明（``.nova/agents/test_agent.yaml`` 单文件）。"""
    agents_dir = cwd / ".nova" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    skills_line = (
        "" if skills_whitelist is None else f"skills: [{', '.join(skills_whitelist)}]\n"
    )
    (agents_dir / "test_agent.yaml").write_text(
        f'name: test_agent\nversion: "0.1.0"\ndescription: 测试 agent\ntools: [read]\n{skills_line}',
        encoding="utf-8",
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Path]:
    """全场景文件系统：user 两路 + project 两路真实 skill 文件。"""
    home = tmp_path / "home"
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    home.mkdir()
    agent_dir.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    _skill_md(home / ".agents" / "skills", "home-skill", "用户 agents 技能")
    _skill_md(agent_dir / "backend" / "skills", "user-nova-skill", "用户 nova 技能")
    _skill_md(cwd / ".agents" / "skills", "team-skill", "团队共享技能")
    _skill_md(cwd / ".nova" / "backend" / "skills", "nova-skill", "项目 nova 技能")
    _write_project_agent(cwd)
    return {"home": home, "agent_dir": agent_dir, "cwd": cwd}


@pytest.fixture(autouse=True)
def _no_pip_npm():
    """包安装只物化与登记，不跑真实 pip/npm。"""
    with patch("nova_harness.core.package.install.installer.install_dependencies"):
        with patch("nova_harness.core.package.install.installer.install_package"):
            with patch("nova_harness.core.package.install.installer.uninstall_package"):
                with patch("nova_harness.core.package.manager.uninstall_package"):
                    yield


# ---------------------------------------------------------------------------
# 共享助手
# ---------------------------------------------------------------------------


def _settings_manager(env: Dict[str, Path], trusted: bool = True) -> SettingsManager:
    return SettingsManager.create(
        cwd=str(env["cwd"]), agent_dir=str(env["agent_dir"]), project_trusted=trusted
    )


def _make_loader(
    env: Dict[str, Path], trusted: bool = True, additional_skill_paths=None
):
    sm = _settings_manager(env, trusted)
    pm = PackageManager(
        agent_dir=str(env["agent_dir"]),
        cwd=str(env["cwd"]),
        settings_manager=sm,
        project_trusted=trusted,
    )
    return DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(env["cwd"]),
            agent_dir=str(env["agent_dir"]),
            settings_manager=sm,
            package_manager=pm,
            additional_skill_paths=additional_skill_paths,
        )
    )


async def _loaded_skills(loader: DefaultResourceLoader) -> Dict[str, Skill]:
    await loader.reload()
    return loader.get_skills().get("skills", {})


def _scope_of(skill: Skill) -> Optional[str]:
    return None if skill.source_info is None else skill.source_info.scope


def _origin_of(skill: Skill) -> Optional[str]:
    return None if skill.source_info is None else skill.source_info.origin


def _make_pkg_with_skill(
    path: Path,
    name: str,
    with_agent: bool = False,
    agent_skills: Optional[List[str]] = None,
) -> Path:
    """合法包源：包内 skill +（可选）带 skills 名单的 agent + dummy read 工具。"""
    path.mkdir(parents=True)
    nova_section = '[tool.nova]\nskills = ["./skills"]\ntools = ["./tools/read.py"]\n'
    if with_agent:
        nova_section += 'agents = ["./agents"]\n'
    (path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["poetry-core>=1.0.0"]\n'
        'build-backend = "poetry.core.masonry.api"\n\n'
        f'[tool.poetry]\nname = "{name}"\nversion = "1.0.0"\n\n' + nova_section,
        encoding="utf-8",
    )
    _skill_md(path / "skills", "bundled-skill", "包内技能")
    tools_dir = path / "tools"
    tools_dir.mkdir()
    # dummy read 工具（附录渲染需要 read 工具在场）
    (tools_dir / "read.py").write_text(
        "class Tool:\n"
        '    name = "read"\n'
        '    description = "dummy read"\n'
        '    parameters = {"type": "object", "properties": {}}\n'
        "    def __init__(self, context):\n"
        "        pass\n"
        "    async def execute(self, tool_call_id, params, signal, on_update, ctx):\n"
        '        return {"content": [{"type": "text", "text": "dummy"}]}\n',
        encoding="utf-8",
    )
    if with_agent:
        skills_line = (
            "" if agent_skills is None else f"skills: [{', '.join(agent_skills)}]\n"
        )
        agents_dir = path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "pkged-agent.yaml").write_text(
            'name: pkged-agent\nversion: "0.1.0"\ndescription: 包内 agent\n'
            f"tools: [read]\n{skills_line}",
            encoding="utf-8",
        )
    return path


def _install_pkg(env: Dict[str, Path], pkg_src: Path) -> None:
    """真安装并**持久化到 settings 选择层**（运行时按 settings 加载的关键）。"""
    pm = PackageManager(
        agent_dir=str(env["agent_dir"]),
        cwd=str(env["cwd"]),
        settings_manager=_settings_manager(env),
        project_trusted=True,
    )
    pm.install_and_persist(str(pkg_src))


async def _make_session(
    env: Dict[str, Path], agent_name: Optional[str] = "test_agent", trusted: bool = True
):
    from nova_harness.core.sdk import create_agent_session_runtime
    from nova_harness.core.types.session.config import CreateAgentSessionOptions

    return await create_agent_session_runtime(
        CreateAgentSessionOptions(
            cwd=str(env["cwd"]),
            agent_dir=str(env["agent_dir"]),
            agent_name=agent_name,
            project_trusted=trusted,
        )
    )


# ---------------------------------------------------------------------------
# A. 发现链路（loader 级，真实管线）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_covers_all_user_and_project_roots(env):
    """四个根各自的 skill 都被加载，scope 归属正确。"""
    skills = await _loaded_skills(_make_loader(env))

    assert _scope_of(skills["home-skill"]) == SourceScope.USER.value
    assert _scope_of(skills["user-nova-skill"]) == SourceScope.USER.value
    assert _scope_of(skills["team-skill"]) == SourceScope.PROJECT.value
    assert _scope_of(skills["nova-skill"]) == SourceScope.PROJECT.value
    # 四路全部不是包来源
    assert all(_origin_of(s) != "package" for s in skills.values())


@pytest.mark.asyncio
async def test_discovery_project_roots_blocked_when_untrusted(env):
    """trust=False：project 两路消失，user 两路照常。"""
    skills = await _loaded_skills(_make_loader(env, trusted=False))

    assert sorted(skills.keys()) == ["home-skill", "user-nova-skill"]


# ---------------------------------------------------------------------------
# B. 过滤（loader + 真安装包）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_package_skill_allowed_when_whitelist_none(env, tmp_path):
    """名单未声明（None）→ 不设防：包内与其余来源全部放行。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))

    skills = await _loaded_skills(_make_loader(env))
    assert _origin_of(skills["bundled-skill"]) == "package"  # 安装事实

    kept = filter_skills_by_whitelist(skills, None)
    assert sorted(kept.keys()) == [
        "bundled-skill",
        "home-skill",
        "nova-skill",
        "team-skill",
        "user-nova-skill",
    ]


@pytest.mark.asyncio
async def test_package_skill_disabled_when_whitelist_empty(env, tmp_path):
    """显式空名单（[]）→ 包内全禁；其余来源不受 yaml 管辖，仍然放行。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))

    skills = await _loaded_skills(_make_loader(env))
    kept = filter_skills_by_whitelist(skills, [])
    assert sorted(kept.keys()) == [
        "home-skill",
        "nova-skill",
        "team-skill",
        "user-nova-skill",
    ]


@pytest.mark.asyncio
async def test_package_skill_allowed_when_whitelisted(env, tmp_path):
    """包内 skill 命中名单 → 放行。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))

    skills = await _loaded_skills(_make_loader(env))
    kept = filter_skills_by_whitelist(skills, ["bundled-skill"])

    assert sorted(kept.keys()) == [
        "bundled-skill",
        "home-skill",
        "nova-skill",
        "team-skill",
        "user-nova-skill",
    ]


@pytest.mark.asyncio
async def test_package_skill_filtered_when_whitelist_misses(env, tmp_path):
    """非空名单未点名包内 skill → 包内被裁；其余来源不受名单约束。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))

    skills = await _loaded_skills(_make_loader(env))
    kept = filter_skills_by_whitelist(skills, ["some-other-skill"])

    assert "bundled-skill" not in kept
    assert sorted(kept.keys()) == [
        "home-skill",
        "nova-skill",
        "team-skill",
        "user-nova-skill",
    ]


@pytest.mark.asyncio
async def test_explicit_cli_skill_path_allowed(env, tmp_path):
    """CLI/SDK 显式路径（additional_skill_paths）不受白名单约束。"""
    extra = tmp_path / "extra-skills"
    _skill_md(extra, "cli-skill", "显式路径技能")

    skills = await _loaded_skills(
        _make_loader(env, additional_skill_paths=[str(extra / "cli-skill")])
    )
    kept = filter_skills_by_whitelist(skills, [])

    assert "cli-skill" in kept


# ---------------------------------------------------------------------------
# C. 会话端到端（真 SDK runtime，无 LLM）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_allowed_skills_cover_all_non_package_sources(env):
    """会话内 _get_allowed_skills：四路非包 skill 全在（白名单为空的 agent）。"""
    runtime = await _make_session(env)
    try:
        allowed = runtime.session._get_allowed_skills()
        assert sorted(allowed.keys()) == [
            "home-skill",
            "nova-skill",
            "team-skill",
            "user-nova-skill",
        ]
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_session_untrusted_drops_project_skills_keeps_user(env):
    """trust=False 的会话：project skill 被门控，user skill 仍在。

    （项目 agent 同样被 trust 门控，故本用例用 base_agent——无 agent 配置。）
    """
    runtime = await _make_session(env, agent_name=None, trusted=False)
    try:
        allowed = runtime.session._get_allowed_skills()
        assert sorted(allowed.keys()) == ["home-skill", "user-nova-skill"]
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_system_prompt_appendix_contains_allowed_skills(env, tmp_path):
    """名单为空时附录包含全部 skill（含包内）：name/location 均在。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))
    runtime = await _make_session(env)
    try:
        prompt = runtime.session.system_prompt
        assert "team-skill" in prompt
        assert "home-skill" in prompt
        assert "nova-skill" in prompt
        assert "user-nova-skill" in prompt
        # 空名单默认不裁剪：包内 skill 同样进附录
        assert "bundled-skill" in prompt
        # 附录是渐进式披露：location 指向真实 SKILL.md
        assert "SKILL.md" in prompt
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_skill_command_expands_with_real_content(env):
    """/skill:<name> 展开读取真实 SKILL.md 正文为 XML block。"""
    runtime = await _make_session(env)
    try:
        allowed = runtime.session._get_allowed_skills()
        expanded = expand_skill_command("/skill:team-skill 分析这个路径", allowed)
        assert expanded.startswith('<skill name="team-skill"')
        assert "# team-skill 的正文指令" in expanded
        assert expanded.endswith("分析这个路径")
        assert "location=" in expanded
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_disable_model_invocation_excluded_from_appendix_but_expandable(env):
    """disable_model_invocation：不进附录，仍可 /skill: 显式调用。"""
    _skill_md(
        env["home"] / ".agents" / "skills",
        "manual-only",
        "仅显式调用",
        disable_model_invocation=True,
    )
    runtime = await _make_session(env)
    try:
        prompt = runtime.session.system_prompt
        assert "manual-only" not in prompt

        allowed = runtime.session._get_allowed_skills()
        assert "manual-only" in allowed
        expanded = expand_skill_command("/skill:manual-only 开始", allowed)
        assert "# manual-only 的正文指令" in expanded
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_project_skill_wins_over_user_same_name(env):
    """同名碰撞：project > user（来源裁决）。"""
    _skill_md(env["home"] / ".agents" / "skills", "team-skill", "用户侧同名")
    runtime = await _make_session(env)
    try:
        allowed = runtime.session._get_allowed_skills()
        winner = allowed["team-skill"]
        assert str(env["cwd"]) in winner.file_path
        assert str(env["home"]) not in winner.file_path
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_packaged_agent_whitelist_enables_bundled_skill(env, tmp_path):
    """包内 agent 的名单命中同包 skill → 放行（人格面自洽）。"""
    _install_pkg(
        env,
        _make_pkg_with_skill(
            tmp_path / "pkg-s",
            "pkg-s",
            with_agent=True,
            agent_skills=["bundled-skill"],
        ),
    )
    runtime = await _make_session(env, agent_name="pkged-agent")
    try:
        allowed = runtime.session._get_allowed_skills()
        assert "bundled-skill" in allowed
        assert "bundled-skill" in runtime.session.system_prompt
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_packaged_agent_whitelist_excludes_unlisted_bundled_skill(env, tmp_path):
    """包内 agent 的名单未点名同包 skill → 包内被裁；其余来源不受影响。"""
    _install_pkg(
        env,
        _make_pkg_with_skill(
            tmp_path / "pkg-s",
            "pkg-s",
            with_agent=True,
            agent_skills=["other-skill"],
        ),
    )
    runtime = await _make_session(env, agent_name="pkged-agent")
    try:
        allowed = runtime.session._get_allowed_skills()
        assert "bundled-skill" not in allowed
        assert "bundled-skill" not in runtime.session.system_prompt
        # 非包来源不受名单约束
        assert "team-skill" in allowed
        assert "home-skill" in allowed
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_agent_yaml_skills_whitelist_is_single_source(env, tmp_path):
    """组合声明 yaml 是 skills 名单的唯一来源（frontmatter 合并语义已退役）。"""
    _install_pkg(env, _make_pkg_with_skill(tmp_path / "pkg-s", "pkg-s"))
    # 项目 agent 的组合声明直接给出 skills 名单
    _write_project_agent(env["cwd"], skills_whitelist=["bundled-skill"])

    runtime = await _make_session(env)
    try:
        # 名单单源来自 yaml（不再有 description.md frontmatter 增量合并）
        config = runtime.session.system_prompt_manager.get_agent_config()
        assert config.skills == ["bundled-skill"]
        # 名单命中：包内 bundled-skill 放行
        allowed = runtime.session._get_allowed_skills()
        assert "bundled-skill" in allowed
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_no_skills_flag_disables_resolved_but_not_explicit(env, tmp_path):
    """no_skills=True 全局开关：禁用 resolver/自动发现的 skill，不禁显式路径。"""
    extra = tmp_path / "extra-skills"
    _skill_md(extra, "cli-skill", "显式路径技能")

    sm = _settings_manager(env)
    pm = PackageManager(
        agent_dir=str(env["agent_dir"]),
        cwd=str(env["cwd"]),
        settings_manager=sm,
        project_trusted=True,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(env["cwd"]),
            agent_dir=str(env["agent_dir"]),
            settings_manager=sm,
            package_manager=pm,
            no_skills=True,
            additional_skill_paths=[str(extra / "cli-skill")],
        )
    )
    skills = await _loaded_skills(loader)

    # 四路自动发现全部被禁用
    for name in ("home-skill", "user-nova-skill", "team-skill", "nova-skill"):
        assert name not in skills
    # 显式路径不受 no_skills 影响
    assert "cli-skill" in skills
