"""全部本地工具的真实调用集成测试（真 LLM + 真磁盘）。

覆盖 coding_agent 全部 7 个工具的真实端到端：write/read/edit/grep/find/ls/bash、
错误路径（非零退出、读不存在文件）、多调用、流式输出事件。
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, List

import pytest
from nova_ai.providers import get_volcengine_model

from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions

pytestmark = pytest.mark.integration

MODEL_ID = "deepseek-v3-2-251201"


def _text_of(msg: Any) -> str:
    return "".join(
        p.text
        for p in getattr(msg, "content", [])
        if getattr(p, "type", None) == "text"
    )


def _tool_calls(session: Any) -> List[str]:
    """按序收集本session发生过的工具名（tool_execution_start 事件驱动更准，
    这里从 toolResult 消息取）。"""
    return [m.tool_name for m in session.messages if m.role == "toolResult"]


async def _has_key() -> bool:
    from nova_harness.core.config.auth.storage import AuthStorage

    if os.environ.get("VOLCENGINE_API_KEY"):
        return True
    try:
        return (await AuthStorage.create().read("volcengine")) is not None
    except Exception:
        return False


async def _make_session(cwd: str):
    runtime = await create_agent_session_runtime(
        CreateAgentSessionOptions(cwd=cwd, agent_name="coding_agent")
    )
    ok = await runtime.session.set_model(get_volcengine_model(MODEL_ID))
    assert ok
    return runtime


@pytest.mark.asyncio
async def test_write_and_read_roundtrip():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt(
                "请先用 write 工具创建文件 notes.txt（内容：alpha beta gamma），"
                "再用 read 工具读回它。"
            )
            await session.agent.wait_for_idle()

            assert (Path(cwd) / "notes.txt").read_text() == "alpha beta gamma"
            tools = _tool_calls(session)
            assert "write" in tools and "read" in tools
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_edit_tool_real_diff():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        target = Path(cwd) / "app.py"
        target.write_text("def answer():\n    return 41\n")
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt(
                "请用 edit 工具把 app.py 里的 `return 41` 改成 `return 42`。"
            )
            await session.agent.wait_for_idle()

            assert "return 42" in target.read_text()
            assert "edit" in _tool_calls(session)
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_grep_and_find_and_ls():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        (Path(cwd) / "a.py").write_text("MARKER_ALPHA = 1\n")
        (Path(cwd) / "b.py").write_text("MARKER_BETA = 2\n")
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt(
                "请完成三个小任务：1) 用 grep 工具搜索 MARKER_ALPHA 在哪个文件；"
                "2) 用 find 工具找到 b.py；3) 用 ls 工具列出当前目录。"
            )
            await session.agent.wait_for_idle()

            tools = _tool_calls(session)
            assert "grep" in tools, f"缺少 grep 调用：{tools}"
            assert "find" in tools, f"缺少 find 调用：{tools}"
            assert "ls" in tools, f"缺少 ls 调用：{tools}"
            grep_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "grep"
            ]
            assert any("a.py" in _text_of(m) for m in grep_results)
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_bash_nonzero_exit_is_error():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt("请用 bash 工具执行 `exit 3`，然后告诉我结果。")
            await session.agent.wait_for_idle()

            bash_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "bash"
            ]
            assert bash_results, "没有 bash 调用"
            assert any(m.is_error for m in bash_results), "非零退出未被标记为错误"
            details = bash_results[-1].details or {}
            assert details.get("exit_code") == 3
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_read_missing_file_is_error():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt("请用 read 工具读取 not-exist.txt（不存在的文件）。")
            await session.agent.wait_for_idle()

            read_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "read"
            ]
            assert read_results and any(m.is_error for m in read_results)
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_streaming_tool_output_events():
    """较长输出的 bash 应产生 tool_execution_update 流式事件（或至少完整 end）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            events: List[str] = []
            session.subscribe(lambda e: events.append(e.type))
            await session.prompt("请用 bash 工具执行 `seq 1 50`，输出全部行。")
            await session.agent.wait_for_idle()

            assert "tool_execution_start" in events
            assert "tool_execution_end" in events
            bash_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "bash"
            ]
            assert bash_results and "50" in _text_of(bash_results[-1])
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_parallel_tool_calls_in_one_message():
    """引导模型并行发起两个独立调用（一条消息两个 toolCall 或先后两轮均可，
    但总数必须 ≥2 且全部正确完结）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt(
                "请**同时**发起两个互不依赖的工具调用（一次消息里两个 tool call）："
                "bash 执行 `echo P1` 和 bash 执行 `echo P2`。"
            )
            await session.agent.wait_for_idle()

            bash_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "bash"
            ]
            assert len(bash_results) >= 2
            texts = [_text_of(m) for m in bash_results]
            assert any("P1" in t for t in texts) and any("P2" in t for t in texts)
            assert all(not m.is_error for m in bash_results)
        finally:
            await runtime.dispose()
