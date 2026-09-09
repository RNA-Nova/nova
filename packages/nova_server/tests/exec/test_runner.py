"""ExecRunner 协议路径测试（注入假客户端，不碰真实组装链）。

验证 exec 经 InProcessNovaClient 的完整回合流：请求序列、契约参数
（camelCase 线上形态）、JSONL item 帧透传、text 模式最终回复提取。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest
from nova_server.client.in_process import InProcessServerError
from nova_server.exec.runner import ExecRunner


class _FakeClient:
    """协议客户端替身：记录请求、按剧本回放响应与事件帧。"""

    def __init__(self) -> None:
        self.requests: List[Any] = []
        self.responses: Dict[str, Any] = {}
        self.events_on_prompt: List[Dict[str, Any]] = []
        self.shutdown_called = False
        self._queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()

    async def request(self, method: str, params: Any = None) -> Any:
        self.requests.append((method, params))
        if method == "prompt":
            for event in self.events_on_prompt:
                await self._queue.put(event)
        if method == "explode":
            raise InProcessServerError("-32603", "boom")
        return self.responses.get(method)

    async def next_event(self) -> Optional[Dict[str, Any]]:
        return await self._queue.get()

    def try_next_event(self) -> Optional[Dict[str, Any]]:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _runner(client: _FakeClient, **kwargs: Any) -> ExecRunner:
    async def _factory() -> _FakeClient:
        return client

    return ExecRunner(client_factory=_factory, **kwargs)


@pytest.fixture
def client() -> _FakeClient:
    return _FakeClient()


def _turn_events() -> List[Dict[str, Any]]:
    return [
        {"type": "item_started", "item": {"type": "agentMessage", "text": ""}},
        {"type": "item_delta", "delta": "he"},
        {
            "type": "item_completed",
            "item": {"type": "agentMessage", "text": "最终回复"},
        },
        {"seq": 7, "type": "agent_end"},
    ]


@pytest.mark.asyncio
async def test_run_task_request_sequence_and_contract_params(
    client: _FakeClient, tmp_path
):
    client.responses["createSession"] = {"sessionId": "sid-1"}
    runner = _runner(
        client,
        no_session=True,
        trust=True,
        additional_skill_paths=["/skills"],
        tools=["bash", "read"],
        exclude_tools=["write"],
    )

    rc = await runner.run_task("reviewer", "do things", cwd=str(tmp_path))

    assert rc == 0
    methods = [method for method, _ in client.requests]
    assert methods == ["initialize", "createSession", "prompt"]

    # createSession 契约参数（camelCase 线上形态）逐项核对
    _, create_params = client.requests[1]
    assert create_params["cwd"] == str(tmp_path)
    assert create_params["agentName"] == "reviewer"
    assert create_params["noSession"] is True
    assert create_params["trust"] is True
    assert create_params["additionalSkillPaths"] == ["/skills"]
    assert create_params["tools"] == ["bash", "read"]
    assert create_params["excludeTools"] == ["write"]
    assert client.requests[2] == ("prompt", {"text": "do things"})
    assert client.shutdown_called is True


@pytest.mark.asyncio
async def test_json_mode_emits_protocol_frames(client: _FakeClient, tmp_path, capsys):
    client.responses["createSession"] = {"sessionId": "sid-9"}
    client.events_on_prompt = _turn_events()
    runner = _runner(client, json_output=True)

    await runner.run_task("coder", "task", cwd=str(tmp_path))

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    types = [line["type"] for line in lines]
    # session 头 + 逐帧透传（与前端同源：item 帧 + agent_end 原样）
    assert types[0] == "session"
    assert types[1:] == [
        "item_started",
        "item_delta",
        "item_completed",
        "agent_end",
    ]
    assert lines[4]["seq"] == 7  # event_seq 信封原样保留
    assert lines[3]["item"]["type"] == "agentMessage"


@pytest.mark.asyncio
async def test_text_mode_prints_final_assistant_reply(
    client: _FakeClient, tmp_path, capsys
):
    client.events_on_prompt = _turn_events()
    runner = _runner(client)  # text 模式

    await runner.run_task("coder", "task", cwd=str(tmp_path))

    assert capsys.readouterr().out == "最终回复\n"


@pytest.mark.asyncio
async def test_protocol_error_propagates_and_still_shuts_down(
    client: _FakeClient, tmp_path
):
    """createSession 成功、prompt 阶段协议报错 → 异常透传 + shutdown 兜底"""

    class _ExplodingClient(_FakeClient):
        async def request(self, method: str, params: Any = None) -> Any:
            if method == "prompt":
                self.requests.append((method, params))
                raise InProcessServerError("-32000", "model exploded")
            return await super().request(method, params)

    exploding = _ExplodingClient()
    exploding.responses["createSession"] = {"sessionId": "sid-x"}
    runner = ExecRunner(client_factory=lambda: _resolve(exploding))

    async def _resolve(c: _FakeClient) -> _FakeClient:
        return c

    with pytest.raises(InProcessServerError, match="model exploded"):
        await runner.run_task("coder", "task", cwd=str(tmp_path))
    assert exploding.shutdown_called is True
