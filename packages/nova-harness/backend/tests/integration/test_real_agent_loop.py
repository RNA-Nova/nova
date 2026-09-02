"""真实 LLM 集成测试（双 provider 全链路）。

需要真实凭证（环境变量或 ``~/.nova/agent/auth.json``）：volcengine API key、
kimi-coding OAuth。无凭证时自动跳过；全部标记 ``integration``（默认
``-m "not integration"`` 排除，按需 ``-m integration`` 运行）。

覆盖的是"仅真实运行可见"的链路（本学期三个致命 bug 的诞生地）：
真实 agent loop、真实工具调用回环、消息事件流、模型切换出处戳、
思考块、follow_up idle、user tool、skill 注入、abort 恢复、会话持久化。
"""

import asyncio
import os
import tempfile
from typing import Any, Dict, List

import pytest
from nova_ai.providers import get_kimi_coding_model, get_volcengine_model
from nova_harness.core.config.auth.storage import AuthStorage
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions

pytestmark = pytest.mark.integration

VOLCENGINE_ID = "deepseek-v3-2-251201"
KIMI_ID = "k2p7"


async def _has_credential(provider: str) -> bool:
    """凭证可解析（auth.json 存储或环境变量链）即视为可用。"""
    if provider == "volcengine" and os.environ.get("VOLCENGINE_API_KEY"):
        return True
    if provider == "kimi-coding" and os.environ.get("KIMI_API_KEY"):
        return True
    try:
        storage = AuthStorage.create()
        return (await storage.read(provider)) is not None
    except Exception:
        return False


def _text_of(msg: Any) -> str:
    return "".join(
        p.text
        for p in getattr(msg, "content", [])
        if getattr(p, "type", None) == "text"
    )


def _blocks(msg: Any, block_type: str) -> list:
    return [
        p for p in getattr(msg, "content", []) if getattr(p, "type", None) == block_type
    ]


def _assistant_messages(session: Any) -> List[Any]:
    return [m for m in session.messages if m.role == "assistant"]


async def _make_session(tmp_cwd: str, model, agent_name: str = "coding_agent"):
    """真实 ~/.nova/agent（真 coding_agent bundle、真工具）+ 临时 cwd。"""
    runtime = await create_agent_session_runtime(
        CreateAgentSessionOptions(cwd=tmp_cwd, agent_name=agent_name)
    )
    if model is not None:
        ok = await runtime.session.set_model(model)
        assert ok, f"set_model({model.id}) 失败——凭证缺失或不可用"
    return runtime


def _subscribe_events(session: Any) -> List[str]:
    events: List[str] = []

    def on_event(event: Any) -> None:
        events.append(event.type)

    session.subscribe(on_event)
    return events


# ---------------------------------------------------------------------------
# 基本对话（双 provider）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_volcengine_basic_conversation():
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            await runtime.session.prompt("用一个词回答：pong")
            await runtime.session.agent.wait_for_idle()
            replies = _assistant_messages(runtime.session)
            assert replies and _text_of(replies[-1]).strip()
            assert replies[-1].stop_reason == "stop"
            # 出处戳：消息由 volcengine 配置盖章
            assert replies[-1].provider == "volcengine"
            assert replies[-1].model == VOLCENGINE_ID
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_kimi_basic_conversation():
    if not await _has_credential("kimi-coding"):
        pytest.skip("无 kimi-coding 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_kimi_coding_model(KIMI_ID))
        try:
            await runtime.session.prompt("用一个词回答：pong")
            await runtime.session.agent.wait_for_idle()
            replies = _assistant_messages(runtime.session)
            assert replies and _text_of(replies[-1]).strip()
            assert replies[-1].stop_reason == "stop"
            assert replies[-1].provider == "kimi-coding"
            assert replies[-1].model == KIMI_ID
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# 工具调用全链路（本学期三个致命 bug 的案发地）
# ---------------------------------------------------------------------------


async def _tool_cycle_assert(runtime, provider: str) -> None:
    session = runtime.session
    events = _subscribe_events(session)
    await session.prompt("请用 bash 工具执行 `echo integration-ok`，并把输出告诉我。")
    await session.agent.wait_for_idle()

    # 事件链完整：tool_execution_start/end 都真实发生
    assert "tool_execution_start" in events
    assert "tool_execution_end" in events
    # 消息链完整：user → assistant(toolCall) → toolResult → assistant(总结)
    roles = [m.role for m in session.messages]
    assert "toolResult" in roles
    tool_results = [m for m in session.messages if m.role == "toolResult"]
    assert any("integration-ok" in _text_of(m) for m in tool_results)
    # toolResult 之后必须有总结 assistant（should_stop bug 曾让它消失）
    last_tool_idx = max(i for i, r in enumerate(roles) if r == "toolResult")
    assert any(
        r == "assistant" for r in roles[last_tool_idx + 1 :]
    ), "工具结果后缺少总结消息（should_stop 回归？）"
    summary = _assistant_messages(session)[-1]
    assert summary.provider == provider


@pytest.mark.asyncio
async def test_tool_call_full_cycle_volcengine():
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            await _tool_cycle_assert(runtime, "volcengine")
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_tool_call_full_cycle_kimi():
    if not await _has_credential("kimi-coding"):
        pytest.skip("无 kimi-coding 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_kimi_coding_model(KIMI_ID))
        try:
            await _tool_cycle_assert(runtime, "kimi-coding")
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# 流式事件与思考块
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_events_and_thinking_blocks():
    """message_update 流式事件 + 思考块（reasoning 模型）。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            await session.set_thinking_level("high")
            events = _subscribe_events(session)
            await session.prompt("心算 37*46 等于多少？直接给答案。")
            await session.agent.wait_for_idle()

            assert "message_update" in events  # 流式增量真实发生
            replies = _assistant_messages(session)
            assert any(_blocks(m, "thinking") for m in replies), "缺少思考块"
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# 模型切换（出处戳判据，不看模型自报家门）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_switch_provenance():
    creds = await asyncio.gather(
        _has_credential("volcengine"), _has_credential("kimi-coding")
    )
    if not all(creds):
        pytest.skip("需要双 provider 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_kimi_coding_model(KIMI_ID))
        try:
            session = runtime.session
            await session.prompt("用一个词回答：你好")
            await session.agent.wait_for_idle()

            assert await session.set_model(get_volcengine_model(VOLCENGINE_ID))
            await session.prompt("再用一个词回答：再见")
            await session.agent.wait_for_idle()

            last_two = _assistant_messages(session)[-2:]
            assert last_two[0].provider == "kimi-coding"
            assert last_two[1].provider == "volcengine"
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# follow_up / user tool / skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_follow_up_idle_starts_new_turn():
    """idle 时 follow_up 立即开新一轮（本学期修复的语义）。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            events = _subscribe_events(session)
            await session.prompt("记住数字 42，只回答：记住了")
            await session.agent.wait_for_idle()
            agent_starts_before = events.count("agent_start")

            await session.follow_up("我刚才让你记住的数字是？")
            await session.agent.wait_for_idle()

            # idle 的 follow_up 驱动了新的一轮（而不是滞留队列）
            assert events.count("agent_start") > agent_starts_before
            assert "42" in _text_of(_assistant_messages(session)[-1])
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_user_tool_injects_context():
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            await session.invoke_user_tool(
                "bash", {"command": "echo usertool-marker-7"}
            )
            await session.prompt("刚才用户工具执行的输出是什么？原样引用。")
            await session.agent.wait_for_idle()
            assert "usertool-marker-7" in _text_of(_assistant_messages(session)[-1])
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_skill_injection_marker_real():
    """散装 .agents skill 经 /skill: 注入真实生效（标记验证法）。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        from pathlib import Path

        skill_dir = Path(cwd) / ".agents" / "skills" / "marker"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: marker\ndescription: 标记技能\n---\n\n"
            "【重要】你的所有回答必须以「[marker 生效]」开头。\n",
            encoding="utf-8",
        )
        runtime = await create_agent_session_runtime(
            CreateAgentSessionOptions(
                cwd=cwd, agent_name="coding_agent", project_trusted=True
            )
        )
        try:
            session = runtime.session
            assert await session.set_model(get_volcengine_model(VOLCENGINE_ID))
            await session.prompt("/skill:marker 一句话：1+1=?")
            await session.agent.wait_for_idle()
            assert "[marker 生效]" in _text_of(_assistant_messages(session)[-1])
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# 多工具调用与 abort 恢复
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_tool_calls():
    """需要两个命令的任务 → 至少两次真实工具执行。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            await session.prompt(
                "请依次做两件事：1) 用 bash 执行 `echo first`；2) 用 bash 执行 `echo second`。"
                "做完后告诉我两个输出。"
            )
            await session.agent.wait_for_idle()
            tool_results = [m for m in session.messages if m.role == "toolResult"]
            assert len(tool_results) >= 2, f"工具执行次数不足：{len(tool_results)}"
            texts = [_text_of(m) for m in tool_results]
            assert any("first" in t for t in texts)
            assert any("second" in t for t in texts)
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_abort_mid_turn_and_recover():
    """长 turn 中 abort：不挂死、状态复位、后续 prompt 正常工作。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            prompt_task = asyncio.create_task(
                session.prompt("从 1 数到 500，每个数字一行，慢慢来。")
            )
            await asyncio.sleep(2.0)
            await session.abort()
            await asyncio.wait_for(prompt_task, timeout=60)
            await session.agent.wait_for_idle()

            assert not session.is_streaming
            # abort 后会话可继续：再来一轮正常对话
            await session.prompt("用一个词回答：ok")
            await session.agent.wait_for_idle()
            replies = _assistant_messages(session)
            assert _text_of(replies[-1]).strip()
        finally:
            await runtime.dispose()


# ---------------------------------------------------------------------------
# 会话持久化与 compact 边界
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_persisted_jsonl():
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            await session.prompt("用一个词回答：存证")
            await session.agent.wait_for_idle()

            assert session.session_file
            import json
            from pathlib import Path

            entries = [
                json.loads(line)
                for line in Path(session.session_file)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            roles = [
                e.get("message", {}).get("role")
                for e in entries
                if e.get("type") == "message"
            ]
            assert "user" in roles and "assistant" in roles
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_compact_boundary_real():
    """短会话 compact 抛干净的边界异常（或成功）——不许是脏失败。"""
    if not await _has_credential("volcengine"):
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd, get_volcengine_model(VOLCENGINE_ID))
        try:
            session = runtime.session
            await session.prompt("用一个词回答：短")
            await session.agent.wait_for_idle()
            try:
                result = await session.compact()
                assert result is not None  # 长会话时成功路径
            except RuntimeError as e:
                assert "compact" in str(e).lower() or "Nothing" in str(e)
        finally:
            await runtime.dispose()
