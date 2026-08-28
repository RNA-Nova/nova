"""会话管理真实集成测试：持久化恢复 / newSession / fork / 命名 / 导出 / 队列与插话。

全部真 LLM + 真会话文件。
"""

import asyncio
import json
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
async def test_switch_session_resume_history_and_continue():
    """持久化后 switch_session 恢复全部历史，并能基于历史继续对话。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        session_file = None
        try:
            session = runtime.session
            await session.prompt("记住暗号：灯塔-7，只回答：已记住")
            await session.agent.wait_for_idle()
            session_file = session.session_file
            assert session_file and Path(session_file).exists()

            result = await runtime.switch_session(session_file)
            assert result.get("cancelled") is False

            resumed = runtime.session
            texts = [_text_of(m) for m in resumed.messages]
            assert any("灯塔-7" in t for t in texts), "恢复后历史缺少早期消息"

            await resumed.prompt("暗号是什么？")
            await resumed.agent.wait_for_idle()
            assert "灯塔-7" in _text_of(resumed.messages[-1])
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_new_session_is_clean():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            await runtime.session.prompt("先聊一句")
            await runtime.session.agent.wait_for_idle()
            assert len(runtime.session.messages) >= 2

            result = await runtime.new_session()
            assert result.get("cancelled") is False
            fresh = runtime.session
            assert len(fresh.messages) == 0
            assert fresh.session_id != ""

            await fresh.prompt("新会话第一句")
            await fresh.agent.wait_for_idle()
            assert len(fresh.messages) >= 2
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_fork_at_entry_keeps_prefix():
    """在指定条目 fork：新会话包含该条目之前的历史。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt("记住关键词：方舟，只回答：好")
            await session.agent.wait_for_idle()
            await session.prompt("再记住关键词：绿洲，只回答：好")
            await session.agent.wait_for_idle()

            entries = session.session_manager.get_entries()
            user_entries = [
                e
                for e in entries
                if e.type == "message" and getattr(e.message, "role", None) == "user"
            ]
            assert len(user_entries) >= 2
            # position="before"（默认）：fork 保留所选条目**之前**的全部历史——
            # 选第二条用户消息，fork 出的分支含第一轮（方舟）不含第二轮（绿洲）
            fork_point = user_entries[1].id

            result = await runtime.fork(fork_point)
            assert result.get("cancelled") is False
            forked = runtime.session
            texts = [_text_of(m) for m in forked.messages]
            assert any("方舟" in t for t in texts), "fork 前缀缺少第一轮内容"
            assert not any(
                "绿洲" in t for t in texts
            ), "fork 越界带入了所选条目之后的内容"

            await forked.prompt("第一个关键词是？")
            await forked.agent.wait_for_idle()
            assert "方舟" in _text_of(forked.messages[-1])
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_set_session_name_emits_event():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            events: List[str] = []
            session.subscribe(lambda e: events.append(e.type))
            session.set_session_name("真实集成测试会话")  # 同步方法
            assert session.session_name == "真实集成测试会话"
            assert "session_info_changed" in events
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_export_to_jsonl_contains_entries():
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.prompt("用一个词回答：导出")
            await session.agent.wait_for_idle()

            exported = session.export_to_jsonl()
            lines = [json.loads(x) for x in exported.splitlines() if x.strip()]
            assert any(e.get("type") == "message" for e in lines)
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_steer_during_turn_inserts_message():
    """长 turn 进行中 steer：steering 消息真实进入队列并被消费。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            events: List[str] = []
            session.subscribe(lambda e: events.append(e.type))

            prompt_task = asyncio.create_task(
                session.prompt("写一篇 300 字左右关于分布式锁的说明文。")
            )
            # 等 turn 真正开始后再 steer（而不是拍固定时长）
            for _ in range(100):
                if "turn_start" in events or "message_update" in events:
                    break
                await asyncio.sleep(0.1)
            await session.steer("补充一句：结尾带上关键词【锁匠】")
            await asyncio.wait_for(prompt_task, timeout=120)
            await session.agent.wait_for_idle()

            # steering 消息确实进入了对话（user 消息含 steer 文本）
            texts = [_text_of(m) for m in session.messages if m.role == "user"]
            assert any("锁匠" in t for t in texts), "steer 未进入消息流"
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_extension_command_session_info():
    """扩展命令 /session（session_commands 扩展）经 prompt 真实执行——零 LLM 调用。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            before = len(session.messages)
            await session.prompt("/session")
            await session.agent.wait_for_idle()
            # 命令路径直接处理：不产生新的 LLM 对话消息
            assistant_after = [
                m for m in session.messages[before:] if m.role == "assistant"
            ]
            assert not assistant_after, "扩展命令不应触发 LLM 对话"
        finally:
            await runtime.dispose()
