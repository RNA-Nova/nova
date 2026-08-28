"""
Agent class that encapsulates the agent loop.
Provides state management, event subscription, message queuing, and lifecycle control.
"""

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from nova_ai import (
    AbortController,
    AbortSignal,
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    ModelThinkingLevel,
    ProviderResponse,
    SimpleStreamOptions,
    TextContent,
    ThinkingBudgets,
    Transport,
    Usage,
    UserMessage,
    builtin_models,
    to_thinking_level,
)

from .agent_loop import run_agent_loop, run_agent_loop_continue
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentState,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    PrepareNextTurnContext,
    QueueMode,
    ShouldStopAfterTurnContext,
    StreamFn,
    ToolExecutionEndEvent,
    ToolExecutionMode,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from .utils import default_convert_to_llm, invoke_hook

Listener = Callable[[AgentEvent, Optional[AbortSignal]], None]
AsyncListener = Callable[[AgentEvent, Optional[AbortSignal]], Awaitable[None]]
AgentListener = Union[Listener, AsyncListener]


class _PendingMessageQueue:
    """Controls how many queued user messages are injected at a drain point."""

    def __init__(self, mode: QueueMode = "one-at-a-time"):
        self._messages: List[AgentMessage] = []
        self.mode = mode

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return len(self._messages) > 0

    def drain(self) -> List[AgentMessage]:
        if self.mode == "all":
            drained = self._messages[:]
            self._messages.clear()
            return drained

        if not self._messages:
            return []
        first = self._messages.pop(0)
        return [first]

    def clear(self) -> None:
        self._messages.clear()


class Agent:
    """
    Agent that manages conversation state, tools, and message queues.
    Uses the agent loop internally and emits events for UI updates.
    """

    def __init__(
        self,
        *,
        initial_state: Optional[Union[AgentState, Dict[str, Any]]] = None,
        convert_to_llm: Optional[
            Callable[
                [List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]
            ]
        ] = None,
        transform_context: Optional[
            Callable[
                [List[AgentMessage], Optional[AbortSignal]],
                Awaitable[List[AgentMessage]],
            ]
        ] = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
        stream_fn: Optional[StreamFn] = None,
        session_id: Optional[str] = None,
        get_api_key: Optional[
            Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]
        ] = None,
        thinking_budgets: Optional[ThinkingBudgets] = None,
        transport: Transport = Transport.AUTO,
        max_retry_delay_ms: Optional[int] = None,
        timeout: Optional[float] = None,
        tool_execution: ToolExecutionMode = "parallel",
        on_payload: Optional[Callable[[Any, Model], Any]] = None,
        on_response: Optional[Callable[[ProviderResponse, Any], Any]] = None,
        before_tool_call: Optional[
            Callable[
                [BeforeToolCallContext, Optional[AbortSignal]],
                Union[BeforeToolCallResult, Awaitable[BeforeToolCallResult], None],
            ]
        ] = None,
        after_tool_call: Optional[
            Callable[
                [AfterToolCallContext, Optional[AbortSignal]],
                Union[AfterToolCallResult, Awaitable[AfterToolCallResult], None],
            ]
        ] = None,
        prepare_next_turn: Optional[
            Callable[
                [PrepareNextTurnContext, Optional[AbortSignal]],
                Union[AgentLoopTurnUpdate, Awaitable[AgentLoopTurnUpdate], None],
            ]
        ] = None,
        should_stop_after_turn: Optional[
            Callable[
                [ShouldStopAfterTurnContext],
                Union[bool, Awaitable[bool]],
            ]
        ] = None,
    ):
        # Initialise state. AgentState 是普通 class，不再走 Pydantic 校验；
        # 这里只从 initial_state 提取配置字段，运行时字段统一重置。
        if isinstance(initial_state, AgentState):
            # 拷贝一份，避免外部引用污染内部状态（与 TS createMutableAgentState 对齐）
            self._state = AgentState(
                system_prompt=initial_state.system_prompt,
                model=initial_state.model,
                thinking_level=initial_state.thinking_level,
                tools=initial_state.tools,
                messages=initial_state.messages,
            )
        elif initial_state:
            kwargs = dict(initial_state)
            unknown_keys = set(kwargs) - {
                "system_prompt",
                "model",
                "thinking_level",
                "tools",
                "messages",
            }
            if unknown_keys:
                raise TypeError(
                    f"Unknown initial_state keys: {sorted(unknown_keys)}. "
                    "Allowed keys: system_prompt, model, thinking_level, tools, messages."
                )
            model_value = kwargs.get("model")
            if isinstance(model_value, dict):
                kwargs["model"] = Model.model_validate(model_value)
            self._state = AgentState(
                system_prompt=kwargs.get("system_prompt"),
                model=kwargs.get("model"),
                thinking_level=kwargs.get("thinking_level"),
                tools=kwargs.get("tools"),
                messages=kwargs.get("messages"),
            )
        else:
            self._state = AgentState()

        # 用 dict 保存监听器：保序（按订阅顺序派发）且天然去重，对齐 TS Set 语义
        self._listeners: Dict[AgentListener, None] = {}
        self._abort_controller: Optional[AbortController] = None
        self._running_task: Optional[asyncio.Task] = None

        self.convert_to_llm = convert_to_llm or default_convert_to_llm
        self.transform_context = transform_context
        self.steering_mode = steering_mode
        self.follow_up_mode = follow_up_mode
        # 未注入 stream_fn 时惰性构造内置 Models（内存 store 为空 → auth 等价 env-only）
        self.stream_fn = stream_fn or builtin_models().stream_simple
        self._session_id = session_id
        self.get_api_key = get_api_key
        self._thinking_budgets = thinking_budgets
        self._transport = transport
        self.max_retry_delay_ms = max_retry_delay_ms
        self._timeout = timeout
        self.tool_execution = tool_execution
        self.on_payload = on_payload
        self.on_response = on_response
        self.before_tool_call = before_tool_call
        self.after_tool_call = after_tool_call
        self.prepare_next_turn = prepare_next_turn
        self.should_stop_after_turn = should_stop_after_turn

        # Message queues
        self._steering_queue = _PendingMessageQueue(steering_mode)
        self._follow_up_queue = _PendingMessageQueue(follow_up_mode)

    # ----------------------------------------------------------------------
    # Properties (mirroring TypeScript get/set)
    # ----------------------------------------------------------------------

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @session_id.setter
    def session_id(self, value: Optional[str]) -> None:
        self._session_id = value

    @property
    def thinking_budgets(self) -> Optional[ThinkingBudgets]:
        return self._thinking_budgets

    @thinking_budgets.setter
    def thinking_budgets(self, value: Optional[ThinkingBudgets]) -> None:
        self._thinking_budgets = value

    @property
    def transport(self) -> Transport:
        return self._transport

    @transport.setter
    def transport(self, value: Transport) -> None:
        self._transport = value

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def signal(self) -> Optional[AbortSignal]:
        """Active abort signal for the current run, if any."""
        return self._abort_controller.signal if self._abort_controller else None

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------

    def subscribe(self, fn: AgentListener) -> Callable[[], None]:
        """注册事件监听器（支持同步或异步函数）。

        监听器按订阅顺序逐个被 await（与 TS 对齐）。返回取消订阅函数。
        """
        self._listeners[fn] = None
        return lambda: self._listeners.pop(fn, None)

    # State mutators
    def set_system_prompt(self, value: str) -> None:
        self._state.system_prompt = value

    def set_model(self, model: Model) -> None:
        self._state.model = model

    def set_thinking_level(self, level: ModelThinkingLevel) -> None:
        self._state.thinking_level = level

    def set_steering_mode(self, mode: QueueMode) -> None:
        self.steering_mode = mode
        self._steering_queue.mode = mode

    def get_steering_mode(self) -> QueueMode:
        return self.steering_mode

    def set_follow_up_mode(self, mode: QueueMode) -> None:
        self.follow_up_mode = mode
        self._follow_up_queue.mode = mode

    def get_follow_up_mode(self) -> QueueMode:
        return self.follow_up_mode

    def set_tools(self, tools: List[AgentTool[Any, Any]]) -> None:
        self._state.tools = tools

    def replace_messages(self, messages: List[AgentMessage]) -> None:
        self._state.messages = messages[:]

    def append_message(self, message: AgentMessage) -> None:
        self._state.messages.append(message)

    def steer(self, message: AgentMessage) -> None:
        """Queue a steering message to interrupt the agent mid‑run."""
        self._steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        """Queue a follow‑up message to be processed after the agent finishes."""
        self._follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    def has_queued_messages(self) -> bool:
        return self._steering_queue.has_items() or self._follow_up_queue.has_items()

    def clear_messages(self) -> None:
        self._state.messages.clear()

    def abort(self) -> None:
        """Abort the currently running prompt."""
        if self._abort_controller is not None:
            self._abort_controller.abort()

    async def wait_for_idle(self) -> None:
        """Wait until the agent finishes processing the current prompt.

        shield 保证等待方被取消时不会传染给正在运行的 run（对齐 TS 的独立 promise 语义）。
        """
        if self._running_task:
            await asyncio.shield(self._running_task)

    def reset(self) -> None:
        """Reset the agent state (clears messages, queues, and errors)."""
        self._state.messages.clear()
        self._state.is_streaming = False
        self._state.streaming_message = None
        self._state.pending_tool_calls.clear()
        self._state.error_message = None
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    async def prompt(
        self,
        input: Union[str, AgentMessage, List[AgentMessage]],
        images: Optional[List[ImageContent]] = None,
    ) -> None:
        """
        Send a prompt to the agent.
        - If input is a string, it is treated as user text (optional images).
        - If input is an AgentMessage or a list of AgentMessages, they are used directly.
        """
        if self._state.is_streaming:
            raise RuntimeError(
                "Agent is already processing a prompt. Use steer() or follow_up() to queue messages, "
                "or wait for completion."
            )

        if not self._state.has_configured_model():
            raise RuntimeError(
                "No model configured. Call set_model() or provide initial_state "
                "with a model before prompt()."
            )

        messages = self._normalize_prompt_input(input, images)
        await self._run_with_lifecycle(lambda: self._run_prompt_messages(messages))

    async def continue_(self) -> None:
        """
        Continue an agent loop from the current context (used for retries and resuming queued messages).
        In TypeScript this method is named 'continue' (a keyword in Python, hence the trailing underscore).
        """
        if self._state.is_streaming:
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        if not self._state.messages:
            raise RuntimeError("No messages to continue from")

        last_msg = self._state.messages[-1]
        if last_msg.role == "assistant":
            queued_steering = self._steering_queue.drain()
            if queued_steering:
                await self._run_with_lifecycle(
                    lambda: self._run_prompt_messages(
                        queued_steering, skip_initial_steering_poll=True
                    )
                )
                return

            queued_follow_up = self._follow_up_queue.drain()
            if queued_follow_up:
                await self._run_with_lifecycle(
                    lambda: self._run_prompt_messages(queued_follow_up)
                )
                return

            raise RuntimeError("Cannot continue from message role: assistant")

        await self._run_with_lifecycle(self._run_continuation)

    # ----------------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------------

    def _normalize_prompt_input(
        self,
        input: Union[str, AgentMessage, List[AgentMessage]],
        images: Optional[List[ImageContent]],
    ) -> List[AgentMessage]:
        if isinstance(input, list):
            return input

        if not isinstance(input, str):
            return [input]

        content: List[Union[TextContent, ImageContent]] = [TextContent(text=input)]
        if images:
            content.extend(images)
        return [
            UserMessage(
                role="user",
                content=content,
                timestamp=int(time.time() * 1000),
            )
        ]

    def _create_context_snapshot(self) -> AgentContext:
        return AgentContext(
            system_prompt=self._state.system_prompt,
            messages=self._state.messages[:],
            tools=self._state.tools[:],
        )

    def _create_loop_config(
        self, skip_initial_steering_poll: bool = False
    ) -> AgentLoopConfig:
        skip_initial = skip_initial_steering_poll

        async def get_steering() -> List[AgentMessage]:
            nonlocal skip_initial
            if skip_initial:
                skip_initial = False
                return []
            return self._steering_queue.drain()

        async def get_follow_up() -> List[AgentMessage]:
            return self._follow_up_queue.drain()

        async def prepare_next_turn_wrapper(
            context: PrepareNextTurnContext,
        ) -> Optional[AgentLoopTurnUpdate]:
            return await invoke_hook(self.prepare_next_turn, context, self.signal)

        async def should_stop_after_turn_wrapper(
            context: ShouldStopAfterTurnContext,
        ) -> bool:
            result = await invoke_hook(
                self.should_stop_after_turn, context, default=False
            )
            return bool(result)

        stream_options = SimpleStreamOptions(
            # 状态侧级别 → 请求侧：OFF 时 reasoning=None（不发送）
            reasoning=to_thinking_level(self._state.thinking_level),
            session_id=self._session_id,
            transport=self._transport,
            thinking_budgets=self._thinking_budgets,
            max_retry_delay_ms=self.max_retry_delay_ms,
            timeout=self._timeout,
            on_payload=self.on_payload,
            on_response=self.on_response,
        )

        return AgentLoopConfig(
            stream_options=stream_options,
            model=self._state.model,
            tool_execution=self.tool_execution,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            prepare_next_turn=(
                prepare_next_turn_wrapper if self.prepare_next_turn else None
            ),
            should_stop_after_turn=(
                should_stop_after_turn_wrapper if self.should_stop_after_turn else None
            ),
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            get_api_key=self.get_api_key,
            get_steering_messages=get_steering,
            get_follow_up_messages=get_follow_up,
        )

    async def _run_with_lifecycle(
        self, executor: Callable[[], Awaitable[None]]
    ) -> None:
        if self._state.is_streaming:
            raise RuntimeError("Agent is already processing.")

        self._abort_controller = AbortController()
        self._state.is_streaming = True
        self._state.streaming_message = None
        self._state.error_message = None

        async def _run() -> None:
            try:
                await executor()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._handle_run_failure(e)
            finally:
                self._finish_run()

        self._running_task = asyncio.create_task(_run())
        try:
            await self._running_task
        finally:
            self._running_task = None

    async def _run_prompt_messages(
        self,
        messages: List[AgentMessage],
        *,
        skip_initial_steering_poll: bool = False,
    ) -> None:
        await run_agent_loop(
            messages,
            self._create_context_snapshot(),
            self._create_loop_config(skip_initial_steering_poll),
            self._process_event,
            self.signal,
            self.stream_fn,
        )

    async def _run_continuation(self) -> None:
        await run_agent_loop_continue(
            self._create_context_snapshot(),
            self._create_loop_config(),
            self._process_event,
            self.signal,
            self.stream_fn,
        )

    async def _handle_run_failure(self, error: Exception) -> None:
        aborted = self.signal is not None and self.signal.aborted
        model = self._state.model
        failure_message = AssistantMessage(
            role="assistant",
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="aborted" if aborted else "error",
            error_message=str(error),
            timestamp=int(time.time() * 1000),
        )
        await self._process_event(MessageStartEvent(message=failure_message))
        await self._process_event(MessageEndEvent(message=failure_message))
        await self._process_event(
            TurnEndEvent(message=failure_message, tool_results=[])
        )
        await self._process_event(AgentEndEvent(messages=[failure_message]))

    def _finish_run(self) -> None:
        self._state.is_streaming = False
        self._state.streaming_message = None
        self._state.pending_tool_calls.clear()
        self._abort_controller = None

    async def _process_event(self, event: AgentEvent) -> None:
        """Reduce internal state for a loop event, then await listeners."""
        if isinstance(event, MessageStartEvent):
            self._state.streaming_message = event.message
        elif isinstance(event, MessageUpdateEvent):
            self._state.streaming_message = event.message
        elif isinstance(event, MessageEndEvent):
            self._state.streaming_message = None
            self.append_message(event.message)
        elif isinstance(event, ToolExecutionStartEvent):
            self._state.pending_tool_calls.add(event.tool_call_id)
        elif isinstance(event, ToolExecutionEndEvent):
            self._state.pending_tool_calls.discard(event.tool_call_id)
        elif isinstance(event, TurnEndEvent):
            if event.message.role == "assistant" and event.message.error_message:
                self._state.error_message = event.message.error_message
        elif isinstance(event, AgentEndEvent):
            self._state.streaming_message = None

        await self._emit(event)

    async def _emit(self, event: AgentEvent) -> None:
        """异步事件分发。

        与 TS 的 Agent 行为对齐：
        - 按订阅顺序逐个 await listener；
        - 把当前 run 的 abort signal 作为第二个参数传给 listener；
        - 不设置硬编码超时，由 listener 自行通过 signal 响应取消。
        """
        signal = self.signal
        if signal is None:
            raise RuntimeError("Agent listener invoked outside active run")
        # 迭代快照，允许 listener 在回调中订阅/退订而不打断本次派发
        for listener in list(self._listeners):
            result = listener(event, signal)
            if inspect.isawaitable(result):
                await result
