"""subagent 引擎（nova_coding_agent/subagent/runner.py）测试。

注册表化后引擎只剩执行面（发现已删——agent 解析归会话注册表消费，
见 ``tests/tools/test_subagent.py``）：

- run_subagent_single：stderr 独立记录进 details；
- 聚合流式回调：parallel 占位槽位 / chain 已完成+当前步骤的全量列表形态；
- _apply_event_payload：print 模式 JSON 流 snake 键解析。
"""

import asyncio

from nova_coding_agent.subagent import runner
from nova_coding_agent.subagent.types import (
    SubagentCall,
    SubagentResult,
)

# ---------------------------------------------------------------------------
# runner 执行：stderr 独立记录进 details
# ---------------------------------------------------------------------------


def test_run_single_records_stderr_in_details(monkeypatch):
    """非零退出：stderr 独立记录在 details，同时仍出现在 error 文本中。"""

    class _FakeProcess:
        """最小假子进程：预置 stdout/stderr 内容与退出码。"""

        def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
            self._returncode = returncode
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(stdout)
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_data(stderr)
            self.stderr.feed_eof()

        async def wait(self) -> int:
            self.returncode = self._returncode
            return self._returncode

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess(2, b"", b"boom on stderr\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = asyncio.run(
        runner.run_subagent_single(
            SubagentCall(agent="a1", task="t"), agent_dir="/nonexistent"
        )
    )
    assert result.exit_code == 2
    assert result.details["stderr"] == "boom on stderr"
    assert "boom on stderr" in result.error


# ---------------------------------------------------------------------------
# 聚合流式回调（on_update 收全量列表）
# ---------------------------------------------------------------------------


def test_parallel_updates_carry_aggregate_slots(monkeypatch):
    """parallel：首帧全占位（exit_code=-1），逐任务替换后再次聚合emit。"""

    async def _fake_run_single(call, agent_dir, signal=None, on_update=None):
        if on_update is not None:
            on_update([SubagentResult(agent=call.agent, task=call.task, exit_code=-1)])
        return SubagentResult(agent=call.agent, task=call.task, output="done")

    monkeypatch.setattr(runner, "_run_single", _fake_run_single)

    frames: list[list[SubagentResult]] = []

    async def _go():
        return await runner.run_subagent_parallel(
            [SubagentCall(agent="a", task="1"), SubagentCall(agent="b", task="2")],
            agent_dir="/nonexistent",
            on_update=lambda results: frames.append(list(results)),
        )

    results = asyncio.run(_go())
    assert len(results) == 2
    # 首帧：两个占位
    assert len(frames[0]) == 2
    assert all(r.exit_code == -1 for r in frames[0])
    # 末帧：全部完成
    assert all(r.exit_code == 0 for r in frames[-1])
    # 中间帧：一进一占位（槽位数恒定）
    assert all(len(frame) == 2 for frame in frames)


def test_chain_updates_carry_completed_plus_current(monkeypatch):
    """chain：on_update 收 "已完成步骤 + 当前流式步骤" 的全量列表。"""
    seen_lengths: list[int] = []

    async def _fake_run_single(call, agent_dir, signal=None, on_update=None):
        if on_update is not None:
            on_update([SubagentResult(agent=call.agent, task=call.task, exit_code=-1)])
        return SubagentResult(agent=call.agent, task=call.task, output="done")

    monkeypatch.setattr(runner, "_run_single", _fake_run_single)

    async def _go():
        return await runner.run_subagent_chain(
            [
                SubagentCall(agent="a", task="1"),
                SubagentCall(agent="b", task="2 {previous}"),
            ],
            agent_dir="/nonexistent",
            on_update=lambda results: seen_lengths.append(len(results)),
        )

    results = asyncio.run(_go())
    assert len(results) == 2
    # 第一步流式帧只有当前步骤；第二步帧 = 已完成 1 + 当前 1
    assert seen_lengths[0] == 1
    assert 2 in seen_lengths
    # {previous} 占位符被第一步输出替换
    assert results[1].task == "2 done"


# ---------------------------------------------------------------------------
# _apply_event_payload：print 模式 JSON 流解析
# ---------------------------------------------------------------------------


def test_apply_event_payload_parses_snake_wire_keys():
    """回归：print 模式 JSON 流是 model_dump 原生 snake 键（曾误读
    camel——cacheRead/totalTokens/stopReason 静默丢失）。"""
    result = SubagentResult(agent="a1", task="t")
    runner._apply_event_payload(
        result,
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "model": "deepseek-v3-2-251201",
                "stop_reason": "stop",
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cache_read": 5,
                    "cache_write": 7,
                    "total_tokens": 120,
                    "cost": {"total": 0.001},
                },
            },
        },
    )
    assert result.usage.turns == 1
    assert result.usage.input_tokens == 100
    assert result.usage.cache_read == 5
    assert result.usage.cache_write == 7
    assert result.usage.context_tokens == 120
    assert abs(result.usage.cost - 0.001) < 1e-9
    assert result.model == "deepseek-v3-2-251201"
    assert result.stop_reason == "stop"
    assert result.output == "hi"

    # error_message 透传（snake）
    runner._apply_event_payload(
        result,
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stop_reason": "error",
                "error_message": "boom",
            },
        },
    )
    assert result.error_message == "boom"
    assert result.stop_reason == "error"


# ---------------------------------------------------------------------------
# 流式回调形态回归：on_update 收单元素 List[SubagentResult]（非嵌套列表）
# ---------------------------------------------------------------------------


def test_run_single_on_update_receives_flat_single_element_frames(monkeypatch):
    """历史 bug：_apply_event_payload 已把结果包成列表，_emit_update 又包
    一层——回调实收 ``[[SubagentResult]]``，调用方遍历即 AttributeError 并被
    各自的 except 静默吞掉，三模式流式更新全灭（卡片只在完成时一次渲染）。
    """
    import json

    stdout = (
        "\n".join(
            [
                json.dumps(
                    {"type": "message_end", "message": {"role": "user", "content": []}}
                ),
                json.dumps(
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "hi"}],
                        },
                    }
                ),
            ]
        )
        + "\n"
    ).encode()

    class _FakeProcess:
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(stdout)
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    frames = []
    result = asyncio.run(
        runner.run_subagent_single(
            SubagentCall(agent="a1", task="t"),
            agent_dir="/nonexistent",
            on_update=lambda rs: frames.append(rs),
        )
    )

    assert result.exit_code == 0
    # 两条 message_end → 两帧；每帧是单元素列表且元素为 SubagentResult
    assert len(frames) == 2
    for frame in frames:
        assert len(frame) == 1
        assert isinstance(frame[0], SubagentResult)  # 嵌套列表时这里是 list
    assert len(frames[1][0].details["messages"]) == 2


def test_run_single_handles_line_longer_than_64k(monkeypatch):
    """单行超 64KB 的消息帧（大 read/grep 结果）不再炸掉整条读取链。

    历史 bug：readline 的 64KB 上限抛 ``Separator is found, but chunk is
    longer than limit``，scout/长输出任务必现。
    """
    import json

    big_text = "x" * 200_000
    stdout = (
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": big_text}],
                },
            }
        )
        + "\n"
    ).encode()

    class _FakeProcess:
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(stdout)
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = asyncio.run(
        runner.run_subagent_single(
            SubagentCall(agent="a1", task="t"), agent_dir="/nonexistent"
        )
    )
    assert result.exit_code == 0
    assert result.error is None
    assert result.output == big_text
