"""功能面真实集成测试：思考档位 / 模板 / 激活工具 / 队列 / 开关 / trust。

全部真 LLM（除开关类为真实状态断言）。
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

MODEL_ID = "deepseek-v4-flash-260425"


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


async def _has_key() -> bool:
    from nova_harness.core.config.auth.storage import AuthStorage

    if os.environ.get("VOLCENGINE_API_KEY"):
        return True
    try:
        return (await AuthStorage.create().read("volcengine")) is not None
    except Exception:
        return False


async def _make_session(cwd: str, trusted: bool = True):
    runtime = await create_agent_session_runtime(
        CreateAgentSessionOptions(
            cwd=cwd, agent_name="coding_agent", project_trusted=trusted
        )
    )
    ok = await runtime.session.set_model(get_volcengine_model(MODEL_ID))
    assert ok
    return runtime


@pytest.mark.asyncio
async def test_thinking_off_vs_high():
    """thinking=off 无思考块；thinking=high 有思考块。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await session.set_thinking_level("off")
            await session.prompt("37*46=?只给数字")
            await session.agent.wait_for_idle()
            off_msg = [m for m in session.messages if m.role == "assistant"][-1]
            assert not _blocks(off_msg, "thinking"), "off 档不应有思考块"

            await session.set_thinking_level("high")
            await session.prompt("89*12=?只给数字")
            await session.agent.wait_for_idle()
            high_msg = [m for m in session.messages if m.role == "assistant"][-1]
            assert _blocks(high_msg, "thinking"), "high 档应有思考块"
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_prompt_template_debug_expands():
    """bundle 的 /debug 模板真实展开：模型按模板步骤回应。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            # 给一个具体可调试的案例，模型才有展开流程的对象（空泛的
            # "程序报错"会让模型把模板当成元内容复述/反问）
            await session.prompt(
                "/debug 程序报错 IndexError: list index out of range。"
                "出错代码：def first(xs): return xs[0]; print(first([]))"
            )
            await session.agent.wait_for_idle()
            reply = _text_of(session.messages[-1])
            # debug.md 的流程（复现/堆栈/源码/假设/根因/验证）应体现在回复中；
            # 模板是英文的，模型可能用任一种语言表述
            lowered = reply.lower()
            hit = sum(
                1
                for kw in (
                    "复现",
                    "reproduce",
                    "堆栈",
                    "stack",
                    "源码",
                    "source",
                    "假设",
                    "hypothes",
                    "根因",
                    "root cause",
                    "验证",
                    "verify",
                )
                if kw in reply or kw in lowered
            )
            assert hit >= 2, f"回复未体现 debug 模板流程（命中 {hit}）：{reply[:120]}"
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_set_active_tools_disables_and_reenables():
    """禁用 bash 后模型**无法成功执行**它（模型可能仍尝试，但只会得到
    "Tool bash not found" 错误）；重新启用后恢复正常执行。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            all_tools = session.get_active_tool_names()
            assert "bash" in all_tools

            session.set_active_tools_by_name([t for t in all_tools if t != "bash"])
            assert "bash" not in session.get_active_tool_names()

            await session.prompt("请执行 shell 命令 echo hello。")
            await session.agent.wait_for_idle()
            bash_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "bash"
            ]
            # 没有一次成功执行：要么根本没调用，要么只是 not-found 错误
            assert all(m.is_error for m in bash_results), "bash 被禁用后仍被成功执行"

            session.set_active_tools_by_name(all_tools)
            assert "bash" in session.get_active_tool_names()
            await session.prompt("请用 bash 工具执行 echo hello-again。")
            await session.agent.wait_for_idle()
            bash_results = [
                m
                for m in session.messages
                if m.role == "toolResult" and m.tool_name == "bash"
            ]
            assert any(
                not m.is_error for m in bash_results
            ), "bash 重新启用后未成功执行"
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_queue_update_event_and_drain():
    """steer 入队产生 queue_update；turn 结束后队列清空。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            events: List[str] = []
            session.subscribe(lambda e: events.append(e.type))

            prompt_task = asyncio.create_task(
                session.prompt("写一段 200 字关于缓存一致性的说明。")
            )
            # 等 turn 真正开始后再 steer（而不是拍固定时长）
            for _ in range(100):
                if "turn_start" in events or "message_update" in events:
                    break
                await asyncio.sleep(0.1)
            await session.steer("队列测试消息")
            assert "queue_update" in events

            await asyncio.wait_for(prompt_task, timeout=120)
            await session.agent.wait_for_idle()
            assert session.get_steering_messages() == []
            assert session.get_follow_up_messages() == []
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_retry_and_compaction_toggles_snapshot():
    """开关写入真实生效（auto_retry_enabled 翻转）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            session.set_auto_retry_enabled(False)
            assert session.auto_retry_enabled is False
            session.set_auto_retry_enabled(True)
            assert session.auto_retry_enabled is True
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_project_untrusted_excludes_project_skills_from_prompt():
    """trust=False：项目级 skill 不进系统提示词附录（真实会话级门控）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        skill_dir = Path(cwd) / ".agents" / "skills" / "proj-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: proj-skill\ndescription: 项目技能\n---\n\n正文\n",
            encoding="utf-8",
        )
        runtime = await _make_session(cwd, trusted=False)
        try:
            session = runtime.session
            assert "proj-skill" not in session.system_prompt
            assert "proj-skill" not in session._get_allowed_skills()
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_abort_user_tool():
    """user tool 长命令可被取消（abort_user_tool 不挂死）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            task = asyncio.create_task(
                session.invoke_user_tool("bash", {"command": "sleep 30 && echo done"})
            )
            await asyncio.sleep(1.0)
            session.abort_user_tool()
            try:
                await asyncio.wait_for(task, timeout=20)
            except Exception:
                pass  # 取消路径允许抛错，只要不挂死
            assert not session.is_user_tool_running()
        finally:
            await runtime.dispose()
