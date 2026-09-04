"""环境段渲染测试。

覆盖：本地默认形态、全字段形态、cwd/timestamp 从 Meta 搬进环境段、
Meta 段不再含 cwd。
"""

from nova_harness.core.harness.system_prompt.builder import (
    compose_system_prompt,
    render_dynamic_section,
    render_environment_section,
)
from nova_harness.core.types.resources.agents import AgentConfig, DynamicContext


def _compose(context: DynamicContext) -> str:
    return compose_system_prompt(
        AgentConfig(name="test", description="test agent", agentDir=""),
        context=context,
    )


def test_environment_section_local_default():
    context = DynamicContext(cwd="/repo", session_id="s1")
    section = render_environment_section(context)
    assert "<environment>" in section
    assert "<backend>local</backend>" in section
    assert "<cwd>/repo</cwd>" in section
    assert "<root>/repo</root>" in section
    assert "<permission>workspace-write</permission>" in section
    assert "<network>unmanaged</network>" in section


def test_environment_section_full_fields():
    context = DynamicContext(
        cwd="/repo",
        backend="local",
        shell="bash",
        permission="read-only",
        network="managed (allowed: api.example.com)",
        timestamp="2026-08-19 12:00",
    )
    section = render_environment_section(context)
    assert "<backend>local</backend>" in section
    assert "<shell>bash</shell>" in section
    assert "<permission>read-only</permission>" in section
    assert "api.example.com" in section
    assert "<current_time>2026-08-19 12:00</current_time>" in section


def test_meta_section_no_longer_carries_cwd_and_time():
    context = DynamicContext(cwd="/repo", timestamp="t", session_id="s1")
    meta = render_dynamic_section(context)
    assert "Working directory" not in meta
    assert "Current time" not in meta
    assert "s1" in meta


def test_compose_includes_environment_before_meta():
    context = DynamicContext(cwd="/repo", session_id="s1")
    prompt = _compose(context)
    env_index = prompt.index("# Environment")
    meta_index = prompt.index("# Meta (Dynamic)")
    assert env_index < meta_index
