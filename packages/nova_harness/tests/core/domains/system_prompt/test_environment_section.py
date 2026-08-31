"""环境段渲染测试（executor 接入——设计定案 R5/R6）。

覆盖：本地默认形态、远程 executor 形态、cwd/timestamp 从 Meta 搬进环境段、
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
    assert "environment_id" not in section


def test_environment_section_remote_executor():
    context = DynamicContext(
        cwd="file:///home/user/project",
        backend="executor",
        environment_id="wss://gpu-01:8080",
        shell="bash",
        permission="read-only",
        network="managed (allowed: api.example.com)",
        timestamp="2026-08-19 12:00",
    )
    section = render_environment_section(context)
    assert "<backend>executor</backend>" in section
    assert "<environment_id>wss://gpu-01:8080</environment_id>" in section
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
