"""真实长上下文压缩集成测试（真 LLM + 真超窗上下文）。

compaction 只对**超出 keep_recent_tokens 窗口**的历史发摘要（窗口内全保留
则空摘要短路）。本套件用大文件读取把上下文真实推过窗口（默认 20k tokens），
验证：真摘要事件链、CompactionResult、消息数下降、续聊可用、二次压缩边界、
压缩条目持久化。
"""

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


async def _push_context_over_window(session: Any, cwd: str) -> int:
    """多轮粘贴大段资料（短回答快轮次），把上下文推过 keep_recent_tokens
    （20k）窗口——压缩以消息为最小单位，超窗内容必须分布在多条消息里，
    早期消息才能被摘要。"""
    for turn in range(4):
        big = "\n".join(
            f"[第{turn}批资料 行 {i}] 模块{(i % 7)}的部署要点与注意事项说明。"
            for i in range(1500)
        )
        await session.prompt(f"阅读本批资料（无需总结，只回答：收到）：\n{big}")
        await session.agent.wait_for_idle()
    return len(session.messages)


@pytest.mark.asyncio
async def test_real_compaction_full_cycle():
    """真实超窗压缩全链路（手动路径）：事件 → 结果 → 摘要进上下文 → 消息数下降 → 续聊可用。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            # 关掉 auto-compaction，验证手动 compact 路径（否则超窗时它先跑）；
            # finally 恢复——该开关持久化到全局 settings，不能污染后续用例
            session.set_auto_compaction_enabled(False)
            events: List[str] = []
            session.subscribe(lambda e: events.append(e.type))

            before_count = await _push_context_over_window(session, cwd)
            assert before_count >= 6

            result = await session.compact()
            assert result is not None, "超窗上下文下 compact 不应空摘要短路"

            assert "compaction_start" in events
            assert "compaction_end" in events

            dump = result.model_dump() if hasattr(result, "model_dump") else {}
            assert dump.get("summary") or dump.get("tokens_before") is not None

            after_count = len(session.messages)
            assert (
                after_count < before_count
            ), f"压缩后消息数未下降：{before_count} → {after_count}"

            # 压缩后续聊：模型仍能正常回应（压缩摘要+保留窗口在上下文里）
            await session.prompt("只回答一个词：继续")
            await session.agent.wait_for_idle()
            assert _text_of(session.messages[-1]).strip()
        finally:
            session.set_auto_compaction_enabled(True)  # 恢复全局开关，防污染
            await runtime.dispose()


@pytest.mark.asyncio
async def test_auto_compaction_fires_on_overwindow_context():
    """auto-compaction 真实路径：超窗上下文自动触发压缩（last 为 compaction 条目）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            # 显式开启（不假定默认值——开关持久化到全局 settings，前序用例可能改过）
            session.set_auto_compaction_enabled(True)

            await _push_context_over_window(session, cwd)

            import json

            entries = [
                json.loads(line)
                for line in Path(session.session_file)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            assert any(
                e.get("type") == "compaction" for e in entries
            ), "超窗上下文未触发 auto-compaction"
            # auto-compaction 后再手动 compact 应命中 Already compacted 边界
            with pytest.raises(
                RuntimeError, match="Already compacted|Nothing to compact"
            ):
                await session.compact()
        finally:
            await runtime.dispose()


@pytest.mark.asyncio
async def test_second_compaction_hits_clean_boundary():
    """压缩后立刻再次 compact：命中干净边界（Already compacted / Nothing）。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            # 关掉 auto-compaction，验证手动路径的二次边界
            session.set_auto_compaction_enabled(False)
            await _push_context_over_window(session, cwd)

            result = await session.compact()
            assert result is not None

            with pytest.raises(
                RuntimeError, match="Already compacted|Nothing to compact"
            ):
                await session.compact()
        finally:
            session.set_auto_compaction_enabled(True)  # 恢复全局开关，防污染
            await runtime.dispose()


@pytest.mark.asyncio
async def test_compaction_persists_entry_and_continuity():
    """压缩条目持久化到会话文件；压缩后的新 turn 仍正常入档。"""
    if not await _has_key():
        pytest.skip("无 volcengine 凭证")
    with tempfile.TemporaryDirectory() as cwd:
        runtime = await _make_session(cwd)
        try:
            session = runtime.session
            await _push_context_over_window(session, cwd)
            await session.compact()

            import json

            assert session.session_file
            entries = [
                json.loads(line)
                for line in Path(session.session_file)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            assert any(
                e.get("type") == "compaction" for e in entries
            ), "缺少 compaction 条目"

            await session.prompt("只回答：继续")
            await session.agent.wait_for_idle()
            assert session.messages[-1].role == "assistant"
        finally:
            await runtime.dispose()
