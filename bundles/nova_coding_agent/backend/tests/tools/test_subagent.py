"""subagent 工具测试（tools/subagent.py）——注册表化后的形态。

- Tool.execute：未知名 agent 报错（错误文本含注册表可用名与来源标签）、
  各模式 is_error 标记、details 统一 ``{mode, results}`` 契约（旧
  agent_scope/project_agents_dir 键已删）。
- agent 解析：消费 ``ToolExecContext.agents`` 注册表快照，``agent_source``
  取自 AgentConfig.source_info（package 来源给 ``package``，其余给 scope）。
- _result_to_dict：保留 messages 与独立 stderr 字段。

引擎级（子进程执行 / 聚合流式回调 / 事件解析）测试见
``tests/nova_coding_agent/subagent/test_runner.py``；执行前确认（自治权
检查点）测试见 ``tests/extensions/test_subagent_gate.py``。
"""

import asyncio
import importlib.util
import os

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.tools import ToolExecContext

from nova_coding_agent.subagent.types import SubagentResult


def _load_subagent_tool():
    """加载 tools/subagent.py 并构造 Tool 实例。"""
    tool_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "subagent.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_subagent", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from nova_harness.core.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
    return module, module.Tool(context)


def _agent_config(name: str, scope: str = "user", origin: str = "top-level"):
    return AgentConfig(
        name=name,
        agent_dir="",
        source_info=SourceInfo(path=f"/fake/{name}.yaml", scope=scope, origin=origin),
    )


def _exec_ctx(*configs: AgentConfig) -> ToolExecContext:
    """构造携带 agents 注册表快照的执行期上下文。"""
    return ToolExecContext(agents={c.name: c for c in configs})


# ---------------------------------------------------------------------------
# details 保留过程数据（messages / stderr）
# ---------------------------------------------------------------------------


def test_result_to_dict_preserves_messages_and_stderr():
    """_result_to_dict 序列化保留 messages，并独立暴露 stderr 字段。"""
    module, _ = _load_subagent_tool()
    result = SubagentResult(agent="a1", task="t", output="done")
    result.details["messages"] = [
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
    ]
    result.details["stderr"] = "warn: disk almost full"

    d = module._result_to_dict(result)
    assert d["messages"] == result.details["messages"]
    assert d["stderr"] == "warn: disk almost full"


def test_result_to_dict_defaults_empty_process_data():
    """没有过程数据时 messages/stderr 以空值占位，字段始终存在。"""
    module, _ = _load_subagent_tool()
    d = module._result_to_dict(SubagentResult(agent="a1", task="t"))
    assert d["messages"] == []
    assert d["stderr"] == ""


# ---------------------------------------------------------------------------
# 注册表解析（未知名报错含可用名列表）
# ---------------------------------------------------------------------------


def test_tool_unknown_agent_marks_is_error():
    """请求注册表外的 agent：is_error=True，错误文本列出可用名与来源。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(
        _agent_config("scout", scope="project"),
        _agent_config("worker", origin="package"),
    )

    result = asyncio.run(tool.execute("id", {"agent": "ghost", "task": "t"}, ctx=ctx))
    assert result.is_error is True
    text = result.content[0].text
    assert 'Unknown agent: "ghost"' in text
    # 可用名列表含来源标签（注册表即模型可学名单的兜底）
    assert '"scout" (project)' in text
    assert '"worker" (package)' in text
    # 未知 agent 的 details 也遵循统一契约（results 空列表，无 scope 残留键）
    assert result.details == {"mode": "single", "results": []}


def test_tool_unknown_agent_with_empty_registry():
    """空注册表（含 NULL 上下文兜底）：可用名列表为 none。"""
    module, tool = _load_subagent_tool()

    result = asyncio.run(tool.execute("id", {"agent": "ghost", "task": "t"}))
    assert result.is_error is True
    assert "Available: none." in result.content[0].text


# ---------------------------------------------------------------------------
# 失败路径 is_error 标记
# ---------------------------------------------------------------------------


def test_tool_single_failure_marks_is_error(monkeypatch):
    """single 模式子 agent 失败：is_error=True，details 统一 results 列表。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(_agent_config("a1", scope="user"))

    failed = SubagentResult(
        agent="a1", task="t", error="boom", error_message="boom", exit_code=1
    )

    async def _fake_single(call, agent_dir, signal=None, on_update=None):
        return failed

    monkeypatch.setattr(module, "run_subagent_single", _fake_single)

    result = asyncio.run(tool.execute("id", {"agent": "a1", "task": "t"}, ctx=ctx))
    assert result.is_error is True
    assert result.content[0].text == "Subagent failed: boom"
    # 统一契约：三模式 details 都是 results 列表，含过程数据与来源
    assert len(result.details["results"]) == 1
    item = result.details["results"][0]
    assert item["messages"] == []
    assert item["stderr"] == ""
    assert item["agent_source"] == "user"


def test_tool_single_success_not_is_error(monkeypatch):
    """single 模式成功：is_error 保持 False；包来源标 package。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(_agent_config("a1", origin="package"))

    async def _fake_single(call, agent_dir, signal=None, on_update=None):
        return SubagentResult(agent="a1", task="t", output="done")

    monkeypatch.setattr(module, "run_subagent_single", _fake_single)

    result = asyncio.run(tool.execute("id", {"agent": "a1", "task": "t"}, ctx=ctx))
    assert result.is_error is False
    assert result.content[0].text == "done"
    assert result.details["results"][0]["agent_source"] == "package"


def test_tool_chain_failure_marks_is_error(monkeypatch):
    """chain 模式中途失败：is_error=True。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(_agent_config("a1"))

    failed = SubagentResult(agent="a1", task="t", error="step exploded", exit_code=1)

    async def _fake_chain(calls, agent_dir, signal=None, on_update=None):
        return [failed]

    monkeypatch.setattr(module, "run_subagent_chain", _fake_chain)

    result = asyncio.run(
        tool.execute("id", {"chain": [{"agent": "a1", "task": "t"}]}, ctx=ctx)
    )
    assert result.is_error is True
    assert "Chain stopped at a1" in result.content[0].text


def test_tool_parallel_any_failure_marks_is_error(monkeypatch):
    """parallel 模式任一子任务失败即整体 is_error。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(_agent_config("a1"), _agent_config("a2"))

    async def _fake_parallel(calls, agent_dir, signal=None, on_update=None):
        return [
            SubagentResult(agent="a1", task="t", output="ok"),
            SubagentResult(agent="a2", task="t", error="boom", exit_code=1),
        ]

    monkeypatch.setattr(module, "run_subagent_parallel", _fake_parallel)

    result = asyncio.run(
        tool.execute(
            "id",
            {"tasks": [{"agent": "a1", "task": "t"}, {"agent": "a2", "task": "t"}]},
            ctx=ctx,
        )
    )
    assert result.is_error is True
    assert len(result.details["results"]) == 2


def test_tool_parallel_unknown_agent_in_tasks(monkeypatch):
    """parallel 中任一 agent 未知名：整体报错（执行前校验，不落子进程）。"""
    module, tool = _load_subagent_tool()
    ctx = _exec_ctx(_agent_config("a1"))

    async def _fake_parallel(calls, agent_dir, signal=None, on_update=None):
        raise AssertionError("不应执行——未知名应先被拦下")

    monkeypatch.setattr(module, "run_subagent_parallel", _fake_parallel)

    result = asyncio.run(
        tool.execute(
            "id",
            {"tasks": [{"agent": "a1", "task": "t"}, {"agent": "ghost", "task": "t"}]},
            ctx=ctx,
        )
    )
    assert result.is_error is True
    assert 'Unknown agent: "ghost"' in result.content[0].text
