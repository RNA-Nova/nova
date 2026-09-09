"""Exec 模式运行器。

提供非交互式运行单个 agent 的能力，支持 text 与 json 两种输出形态。

驱动方式对位 codex exec：经 ``InProcessNovaClient`` 走**完整协议栈**
（进程内 RpcServer + 全域方法表），exec 的 ``--json`` 输出与前端看到的
item 帧**严格同源**——同一归约层、同一 ``event_seq`` 信封，不存在
"headless 一套格式、前端另一套格式"的漂移。本模块不使用任何 UI 能力
（trust 显式覆盖时以 ``NoOpUIContext`` 决议）。

回合流程：``initialize → createSession → prompt（阻塞至回合结束）
→ 消费通知帧直至 ``agent_end`` → 吸干残余 → shutdown``。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nova_harness.config.defaults import get_agent_dir
from nova_harness.core.utils.child_process import \
    kill_tracked_detached_children
from nova_server.client.in_process import InProcessNovaClient

ClientFactory = Callable[[], Awaitable[InProcessNovaClient]]

# 回合终结信号（server 侧把 agent_end 当 run 终结点）
_TURN_END_EVENT = "agent_end"


class ExecRunner:
    """Exec 模式运行器：非交互式执行 agent 任务并输出结果。"""

    def __init__(
        self,
        *,
        json_output: bool = False,
        no_session: bool = False,
        trust: Optional[bool] = None,
        additional_skill_paths: Optional[List[str]] = None,
        additional_prompt_template_paths: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        exclude_tools: Optional[List[str]] = None,
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self._json_output = json_output
        self._no_session = no_session
        self._trust = trust
        self._additional_skill_paths = additional_skill_paths or []
        self._additional_prompt_template_paths = additional_prompt_template_paths or []
        self._tools = tools
        self._exclude_tools = exclude_tools
        # 进程内协议客户端工厂（可注入替身供测试）
        self._client_factory: ClientFactory = client_factory or (
            InProcessNovaClient.start
        )
        # text 模式的最终回复（item_completed 的最后一条 agentMessage）
        self._last_text = ""

    async def run_task(
        self,
        agent_name: str,
        task: str,
        cwd: Optional[str] = None,
    ) -> int:
        """运行一次 agent 任务并输出结果。"""
        import os

        self._last_text = ""
        client = await self._client_factory()
        try:
            await client.request("initialize", {})

            resolved_cwd = cwd or os.getcwd()
            session: Dict[str, Any] = await client.request(
                "createSession",
                {
                    "cwd": resolved_cwd,
                    "agentDir": str(get_agent_dir()),
                    "agentName": agent_name,
                    "noSession": self._no_session,
                    "additionalSkillPaths": self._additional_skill_paths or None,
                    "additionalPromptTemplatePaths": (
                        self._additional_prompt_template_paths or None
                    ),
                    "tools": self._tools,
                    "excludeTools": self._exclude_tools,
                    "trust": self._trust,
                },
            )

            if self._json_output:
                self._emit_jsonl(
                    "session",
                    {"id": session.get("sessionId"), "cwd": resolved_cwd},
                )

            # prompt RPC 阻塞至回合结束；通知帧经客户端读泵并发到达
            prompt_task = asyncio.create_task(client.request("prompt", {"text": task}))
            final_text = await self._consume_turn(client, prompt_task)
            await prompt_task

            if not self._json_output and final_text:
                sys.stdout.write(final_text + "\n")
                sys.stdout.flush()
            return 0
        finally:
            # 清场 detached 子进程（对齐 pi：print 模式退出时 kill 所有
            # 被跟踪的后台子进程，不留孤儿）
            kill_tracked_detached_children()
            await client.shutdown()

    # ------------------------------------------------------------------
    # 内部：回合事件消费
    # ------------------------------------------------------------------

    async def _consume_turn(
        self, client: InProcessNovaClient, prompt_task: "asyncio.Task[Any]"
    ) -> str:
        """消费回合通知帧；text 模式截取最后一条 agentMessage 定稿文本。

        终结双条件竞速：``agent_end`` 帧 **或** prompt RPC 落定（协议
        报错时不会有终结帧到达——只等事件会永久死锁）。
        """
        next_event = asyncio.create_task(client.next_event())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {prompt_task, next_event}, return_when=asyncio.FIRST_COMPLETED
                )
                if next_event in done:
                    event = next_event.result()
                    if event is None:
                        # 连接关闭：回合提前终结，以 prompt RPC 落定为准
                        prompt_task.result()
                        self._drain_pending(client)
                        return self._last_text
                    self._on_event(event)
                    if event.get("type") == _TURN_END_EVENT:
                        await prompt_task  # 协议错误在此透传
                        self._drain_pending(client)
                        return self._last_text
                    next_event = asyncio.create_task(client.next_event())
                if prompt_task in done:
                    prompt_task.result()  # 协议错误在此透传
                    self._drain_pending(client)
                    return self._last_text
        finally:
            next_event.cancel()

    def _drain_pending(self, client: InProcessNovaClient) -> None:
        """非阻塞吸干已入队但未消费的残余帧。"""
        while True:
            event = client.try_next_event()
            if event is None:
                return
            self._on_event(event)

    def _on_event(self, event: Dict[str, Any]) -> None:
        if self._json_output:
            self._emit_jsonl_event(event)
        if event.get("type") == "item_completed":
            item = event.get("item") or {}
            if item.get("type") == "agentMessage":
                self._last_text = item.get("text") or ""

    def _emit_jsonl(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Write a JSONL event to stdout."""
        event: Dict[str, Any] = {"type": event_type, **payload}
        self._emit_jsonl_event(event)

    def _emit_jsonl_event(self, event: Any) -> None:
        """将任意可序列化对象作为 JSONL 写入 stdout。"""
        data = _serialize_object(event)
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()


async def run_exec_mode(
    agent_name: str,
    task: str,
    cwd: Optional[str] = None,
    *,
    json_output: bool = False,
    no_session: bool = False,
    trust: Optional[bool] = None,
    additional_skill_paths: Optional[List[str]] = None,
    additional_prompt_template_paths: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    exclude_tools: Optional[List[str]] = None,
) -> int:
    """以 exec 模式运行一次 agent 任务。"""
    runner = ExecRunner(
        json_output=json_output,
        no_session=no_session,
        trust=trust,
        additional_skill_paths=additional_skill_paths,
        additional_prompt_template_paths=additional_prompt_template_paths,
        tools=tools,
        exclude_tools=exclude_tools,
    )
    return await runner.run_task(agent_name, task, cwd)


def _serialize_object(obj: Any) -> Any:
    """Serialize an object to a JSON-friendly value."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dict__"):
        return {
            k: _serialize_object(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return obj
