"""
生成 Agent 在各种场景下的完整事件流日志。

运行：
    python tests/generate_event_flow_log.py

产物：
    tests/EVENT_FLOW_LOG.md
"""

import asyncio
import os
from typing import Any, Callable, List, Optional, Tuple

from nova_agent import (
    Agent,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    AfterToolCallContext,
    AfterToolCallResult,
)
from nova_ai import (
    AssistantMessage,
    DoneEvent,
    EventStream,
    StartEvent,
    TextContent,
    ToolCall,
    ToolCallEndEvent,
    ToolResultMessage,
    UserMessage,
)
from nova_ai.models.volcengine import get_volcengine_model
from nova_ai.registry import reset_registry

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "EVENT_FLOW_LOG.md")


def _extract_text(message: Any) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(c.text for c in message.content if c.type == "text")


def _collapse_updates(events: List[Any]) -> List[str]:
    """把连续的 message_update/tool_execution_update 折叠成 count，减少日志体积。"""
    summaries: List[str] = []
    last_summary = ""
    last_type = ""
    count = 0

    def flush():
        nonlocal last_summary, count
        if count == 0:
            return
        if count == 1:
            summaries.append(last_summary)
        else:
            summaries.append(f"{last_summary} × {count}")
        count = 0

    for event in events:
        summary = _event_summary(event)
        event_type = event.type
        if (
            event_type in ("message_update", "tool_execution_update")
            and event_type == last_type
        ):
            count += 1
        else:
            flush()
            last_summary = summary
            last_type = event_type
            count = 1
    flush()
    return summaries


def _event_summary(event: Any) -> str:
    parts = [event.type]
    if hasattr(event, "tool_name") and event.tool_name:
        parts.append(f"tool={event.tool_name}")
    if hasattr(event, "tool_call_id") and event.tool_call_id:
        parts.append(f"id={event.tool_call_id[:8]}")
    if hasattr(event, "is_error") and event.is_error is not None:
        parts.append(f"is_error={event.is_error}")
    if hasattr(event, "message") and event.message:
        msg = event.message
        role = getattr(msg, "role", "?")
        parts.append(f"role={role}")
        if role == "toolResult":
            parts.append(f"text={_extract_text(msg)!r}")
        elif role == "assistant":
            content_types = [c.type for c in msg.content]
            parts.append(f"content={content_types}")
        else:
            parts.append(f"text={_extract_text(msg)!r}")
    return " | ".join(parts)


def _make_tool_stream(tool_call: ToolCall) -> EventStream:
    """构造一个只返回指定 tool_call 的模拟流。"""
    partial = AssistantMessage(role="assistant", content=[tool_call])
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    stream.push(ToolCallEndEvent(content_index=0, tool_call=tool_call, partial=partial))
    stream.push(DoneEvent(reason="toolUse", message=partial))
    stream.end()
    return stream


async def _capture_events(
    agent_factory: Callable[[], Agent], runner: Callable[[Agent], Any]
) -> Tuple[List[Any], Optional[Exception]]:
    agent = agent_factory()
    events: List[Any] = []
    agent.subscribe(lambda e: events.append(e))
    error: Optional[Exception] = None
    try:
        async with asyncio.timeout(120):
            await runner(agent)
    except Exception as exc:
        error = exc
    return events, error


def _model() -> Any:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        return None
    reset_registry()
    return get_volcengine_model("deepseek-v4-flash-260425")


class EchoTool(AgentTool):
    name: str = "echo"
    description: str = "Echo the input message"
    parameters: dict = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        if on_update:
            on_update(
                AgentToolResult(content=[TextContent(text="partial...")], details={})
            )
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params.get('message', '')}")],
            details={},
        )


class ErrorTool(AgentTool):
    name: str = "error_tool"
    description: str = "Always raises an error"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        raise RuntimeError("intentional tool error")


class SlowTool(AgentTool):
    name: str = "slow_tool"
    description: str = "Waits until aborted"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        for _ in range(200):
            if signal and signal.aborted:
                raise Exception("Operation aborted")
            await asyncio.sleep(0.01)
        return AgentToolResult(content=[TextContent(text="slow done")], details={})


class GetDateTool(AgentTool):
    name: str = "get_date"
    description: str = "Return today's date"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="2026-06-10")], details={})


class GetTimeTool(AgentTool):
    name: str = "get_time"
    description: str = "Return current time"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="12:00")], details={})


async def scenario_text_only(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    events, error = await _capture_events(
        lambda: Agent(),
        lambda agent: (
            agent.set_model(model),
            agent.prompt("用一句话介绍你自己。"),
        )[-1],
    )
    return "A. 纯文本回复（无工具）", events, error, "真实模型，未注册任何工具"


async def scenario_single_tool_success(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    events, error = await _capture_events(
        lambda: Agent(),
        lambda agent: (
            agent.set_model(model),
            agent.set_tools([EchoTool()]),
            agent.prompt(
                '请调用 echo 工具，参数 {"message": "hello"}。只返回工具结果。'
            ),
        )[-1],
    )
    return "B. 单次工具调用成功", events, error, "真实模型 + EchoTool"


async def scenario_tool_error(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    events, error = await _capture_events(
        lambda: Agent(
            initial_state={
                "system_prompt": "无论用户说什么，你都必须调用 error_tool 工具，不要解释。"
            }
        ),
        lambda agent: (
            agent.set_model(model),
            agent.set_tools([ErrorTool()]),
            agent.prompt("请调用 error_tool 工具。"),
        )[-1],
    )
    return "C. 工具执行异常", events, error, "真实模型 + ErrorTool"


async def scenario_before_tool_call_block(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    async def before(ctx: BeforeToolCallContext, signal):
        return BeforeToolCallResult(block=True, reason="blocked by test")

    events, error = await _capture_events(
        lambda: Agent(before_tool_call=before),
        lambda agent: (
            agent.set_model(model),
            agent.set_tools([EchoTool()]),
            agent.prompt('请调用 echo 工具，参数 {"message": "hello"}。'),
        )[-1],
    )
    return (
        "D. before_tool_call 阻断",
        events,
        error,
        "真实模型 + EchoTool + before block",
    )


async def scenario_after_tool_call_override(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    async def after(ctx: AfterToolCallContext, signal):
        return AfterToolCallResult(content=[TextContent(text="overridden")])

    events, error = await _capture_events(
        lambda: Agent(after_tool_call=after),
        lambda agent: (
            agent.set_model(model),
            agent.set_tools([EchoTool()]),
            agent.prompt('请调用 echo 工具，参数 {"message": "hello"}。'),
        )[-1],
    )
    return (
        "E. after_tool_call 覆盖结果",
        events,
        error,
        "真实模型 + EchoTool + after override",
    )


async def scenario_abort_during_tool(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    tool_started = asyncio.Event()

    def make_listener(events: List[Any]):
        def listener(event):
            events.append(event)
            if event.type == "tool_execution_start":
                tool_started.set()

        return listener

    agent = Agent(
        initial_state={
            "system_prompt": "无论用户说什么，你都必须调用 slow_tool 工具，调用后保持等待。"
        }
    )
    agent.set_model(model)
    agent.set_tools([SlowTool()])
    events: List[Any] = []
    agent.subscribe(make_listener(events))

    error: Optional[Exception] = None
    task = asyncio.create_task(agent.prompt("请调用 slow_tool 工具。"))
    try:
        await asyncio.wait_for(tool_started.wait(), timeout=20)
        agent.abort()
        async with asyncio.timeout(120):
            await task
    except Exception as exc:
        error = exc

    return "F. 工具执行过程中 abort", events, error, "真实模型 + SlowTool"


async def scenario_multi_tool(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    events, error = await _capture_events(
        lambda: Agent(),
        lambda agent: (
            agent.set_model(model),
            agent.set_tools([GetDateTool(), GetTimeTool()]),
            agent.prompt(
                "请同时调用 get_date 和 get_time 两个工具，并把结果汇总成一句话。"
            ),
        )[-1],
    )
    return "G. 多个工具调用", events, error, "真实模型 + GetDateTool + GetTimeTool"


async def scenario_tool_not_found() -> Tuple[str, List[Any], Optional[Exception], str]:
    tool_call = ToolCall(id="tc-1", name="nonexistent_tool", arguments={})

    def make_stream():
        call_count = 0

        def stream(m, c, o):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_stream(tool_call)
            return _make_one_text_stream("unknown tool handled")

        return stream

    events, error = await _capture_events(
        lambda: Agent(stream_fn=make_stream()),
        lambda agent: agent.prompt("trigger tool call"),
    )
    return (
        "H. 工具不存在（mock）",
        events,
        error,
        "stream_fn 第一次返回未知 tool call，之后返回文本回复",
    )


async def scenario_validation_error() -> (
    Tuple[str, List[Any], Optional[Exception], str]
):
    tool_call = ToolCall(id="tc-1", name="echo", arguments={})

    def make_stream():
        call_count = 0

        def stream(m, c, o):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_stream(tool_call)
            return _make_one_text_stream("validation handled")

        return stream

    events, error = await _capture_events(
        lambda: Agent(
            stream_fn=make_stream(),
            initial_state={"tools": [EchoTool()]},
        ),
        lambda agent: agent.prompt("trigger tool call"),
    )
    return (
        "I. 参数校验失败（mock）",
        events,
        error,
        "stream_fn 第一次返回非法 echo 调用，之后返回文本回复",
    )


async def scenario_prepare_abort() -> Tuple[str, List[Any], Optional[Exception], str]:
    tool_call = ToolCall(id="tc-1", name="echo", arguments={"message": "hello"})

    async def before(ctx: BeforeToolCallContext, signal):
        signal.set()
        return None

    def make_stream():
        call_count = 0

        def stream(m, c, o):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_tool_stream(tool_call)
            return _make_one_text_stream("aborted handled")

        return stream

    events, error = await _capture_events(
        lambda: Agent(
            stream_fn=make_stream(),
            before_tool_call=before,
            initial_state={"tools": [EchoTool()]},
        ),
        lambda agent: agent.prompt("trigger tool call"),
    )
    return (
        "J. 准备阶段 abort（mock）",
        events,
        error,
        "before hook 第一次调用时设置 signal，之后返回文本回复",
    )


def _make_one_text_stream(text: str) -> EventStream:
    partial = AssistantMessage(role="assistant", content=[TextContent(text="")])
    final = AssistantMessage(role="assistant", content=[TextContent(text=text)])
    stream = EventStream(
        is_complete=lambda e: getattr(e, "type", None) == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    stream.push(DoneEvent(reason="stop", message=final))
    stream.end()
    return stream


async def scenario_continue_from_tool_result() -> (
    Tuple[str, List[Any], Optional[Exception], str]
):
    tool_call = ToolCall(id="tc-1", name="echo", arguments={"message": "continue"})

    def factory() -> Agent:
        agent = Agent(
            stream_fn=lambda m, c, o: _make_one_text_stream("acknowledged"),
            initial_state={"tools": [EchoTool()]},
        )
        agent.replace_messages(
            [
                UserMessage(content='请调用 echo 工具，参数 {"message": "continue"}。'),
                AssistantMessage(content=[tool_call]),
                ToolResultMessage(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=[TextContent(text="echo: continue")],
                ),
            ]
        )
        return agent

    events, error = await _capture_events(
        factory,
        lambda agent: agent.continue_(),
    )
    return "K. continue_ 续跑（mock）", events, error, "上下文已包含 toolResult"


async def scenario_steer(model: Any) -> Tuple[str, List[Any], Optional[Exception], str]:
    async def steer_soon(agent: Agent):
        await asyncio.sleep(0.3)
        agent.steer(UserMessage(content="停止，直接回答：ok"))

    agent = Agent()
    agent.set_model(model)
    events: List[Any] = []
    agent.subscribe(lambda e: events.append(e))

    error: Optional[Exception] = None
    try:
        asyncio.create_task(steer_soon(agent))
        async with asyncio.timeout(120):
            await agent.prompt("请用 50 字以内简要介绍机器学习。")
    except Exception as exc:
        error = exc

    return "L. 运行中 steer 注入", events, error, "真实模型"


async def scenario_follow_up(
    model: Any,
) -> Tuple[str, List[Any], Optional[Exception], str]:
    def factory() -> Agent:
        agent = Agent()
        agent.set_model(model)
        agent.follow_up(UserMessage(content="再问候一次"))
        return agent

    events, error = await _capture_events(
        factory,
        lambda agent: agent.prompt("你好"),
    )
    return "M. follow_up 队列", events, error, "真实模型"


async def main() -> None:
    model = _model()
    lines: List[str] = []
    lines.append("# nova_agent 事件流日志\n")
    lines.append("本文件由 `tests/generate_event_flow_log.py` 自动生成。\n")
    lines.append("每个场景按实际发生顺序列出事件，格式为：`event_type | 附加字段`。\n")

    real_scenarios = [
        lambda: scenario_text_only(model),
        lambda: scenario_single_tool_success(model),
        lambda: scenario_tool_error(model),
        lambda: scenario_before_tool_call_block(model),
        lambda: scenario_after_tool_call_override(model),
        lambda: scenario_abort_during_tool(model),
        lambda: scenario_multi_tool(model),
        lambda: scenario_steer(model),
        lambda: scenario_follow_up(model),
    ]
    mock_scenarios = [
        scenario_tool_not_found,
        scenario_validation_error,
        scenario_prepare_abort,
        scenario_continue_from_tool_result,
    ]

    if model is None:
        lines.append("\n> 未检测到 `VOLCENGINE_API_KEY`，真实模型场景会被跳过。\n")
        sections = [("Mock 场景", mock_scenarios)]
    else:
        sections = [
            ("真实模型场景（依赖 VOLCENGINE_API_KEY）", real_scenarios),
            ("Mock 场景（不依赖真实模型）", mock_scenarios),
        ]

    for section_name, scenario_funcs in sections:
        lines.append(f"\n## {section_name}\n")
        for scenario_func in scenario_funcs:
            title, events, error, note = await scenario_func()
            lines.append(f"\n### {title}")
            lines.append(f"**说明**：{note}\n")
            if error:
                lines.append(f"**运行异常**：{type(error).__name__}: {error}\n")
            if not events:
                lines.append("_无事件_\n")
            else:
                for idx, summary in enumerate(_collapse_updates(events), 1):
                    lines.append(f"{idx:3}. {summary}")
                lines.append("")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"事件流日志已写入：{OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
