"""
Agent class that encapsulates the agent loop.
Provides state management, event subscription, message queuing, and lifecycle control.
"""

import asyncio
import inspect
from typing import (
    Any, Callable, List, Literal, Optional, Set, Union, Awaitable, cast
)
from dataclasses import dataclass, field
from .signal import AbortSignal

# Import from nova_ai (replaces pi-ai)
from nova_ai import (
    get_model,
    TextContent,
    ImageContent,
    Message,
    UserMessage,
    AssistantMessage,
    Model,
    stream_simple,
    TextContent,
    ThinkingBudgets,
    Transport,
    Usage,
    Cost
)

# Import our custom agent types and loop functions
from .events import (
    AgentMessage,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentState,
    AgentTool,
    StreamFn,
    ThinkingLevel,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
)
from .agent_loop import agent_loop, agent_loop_continue

Listener = Callable[[AgentEvent], None]
AsyncListener = Callable[[AgentEvent], Awaitable[None]]
AgentListener = Union[Listener, AsyncListener]

async def _default_convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
    """Default converter: keep only LLM‑compatible messages."""
    return [m for m in messages if m.role in ("user", "assistant", "toolResult")]


class Agent:
    """
    Agent that manages conversation state, tools, and message queues.
    Uses the agent loop internally and emits events for UI updates.
    """

    def __init__(
        self,
        *,
        initial_state: Optional[AgentState] = None,
        convert_to_llm: Optional[Callable[[List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]]] = None,
        transform_context: Optional[Callable[[List[AgentMessage], Optional[AbortSignal]], Awaitable[List[AgentMessage]]]] = None,
        steering_mode: Literal["all", "one-at-a-time"] = "one-at-a-time",
        follow_up_mode: Literal["all", "one-at-a-time"] = "one-at-a-time",
        stream_fn: Optional[StreamFn] = None,
        session_id: Optional[str] = None,
        get_api_key: Optional[Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]] = None,
        thinking_budgets: Optional[ThinkingBudgets] = None,
        transport: Transport = "sse",
        max_retry_delay_ms: Optional[int] = None,
    ):
        # Default model (matching the TypeScript example)
        default_model = get_model("volcengine", "deepseek-r1-250528")

        # Initialise state
        self._state = AgentState(
            system_prompt=initial_state.get("system_prompt", "") if initial_state else "",
            model=initial_state.get("model", default_model) if initial_state else default_model,
            thinking_level=initial_state.get("thinking_level", None) if initial_state else None,
            tools=initial_state.get("tools", []) if initial_state else [],
            messages=initial_state.get("messages", []) if initial_state else [],
            is_streaming=False,
            stream_message=None,
            pending_tool_calls=set(),
            error=None,
        )

        self._listeners: Set[Callable[[AgentEvent], None]] = set()
        self._abort_event: Optional[AbortSignal] = None
        self._running_task: Optional[asyncio.Task] = None

        self.convert_to_llm = convert_to_llm or _default_convert_to_llm
        self.transform_context = transform_context
        self.steering_mode = steering_mode
        self.follow_up_mode = follow_up_mode
        self.stream_fn = stream_fn or stream_simple
        self._session_id = session_id
        self.get_api_key = get_api_key
        self._thinking_budgets = thinking_budgets
        self._transport = transport
        self._max_retry_delay_ms = max_retry_delay_ms

        # Message queues
        self._steering_queue: List[AgentMessage] = []
        self._follow_up_queue: List[AgentMessage] = []

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
    def max_retry_delay_ms(self) -> Optional[int]:
        return self._max_retry_delay_ms

    @max_retry_delay_ms.setter
    def max_retry_delay_ms(self, value: Optional[int]) -> None:
        self._max_retry_delay_ms = value

    @property
    def state(self) -> AgentState:
        return self._state

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------

    def subscribe(self, fn: AgentListener) -> Callable[[], None]:
        """注册事件监听器（支持同步或异步函数）。返回取消订阅函数。"""
        self._listeners.add(fn)
        return lambda: self._listeners.discard(fn)

    # State mutators
    def set_system_prompt(self, value: str) -> None:
        self._state.system_prompt = value

    def set_model(self, model: Model) -> None:
        self._state.model = model

    def set_thinking_level(self, level: ThinkingLevel) -> None:
        self._state.thinking_level = level

    def set_steering_mode(self, mode: Literal["all", "one-at-a-time"]) -> None:
        self.steering_mode = mode

    def get_steering_mode(self) -> Literal["all", "one-at-a-time"]:
        return self.steering_mode

    def set_follow_up_mode(self, mode: Literal["all", "one-at-a-time"]) -> None:
        self.follow_up_mode = mode

    def get_follow_up_mode(self) -> Literal["all", "one-at-a-time"]:
        return self.follow_up_mode

    def set_tools(self, tools: List[AgentTool[Any, Any]]) -> None:
        self._state.tools = tools

    def replace_messages(self, messages: List[AgentMessage]) -> None:
        self._state.messages = messages[:]

    def append_message(self, message: AgentMessage) -> None:
        self._state.messages.append(message)

    def steer(self, message: AgentMessage) -> None:
        """Queue a steering message to interrupt the agent mid‑run."""
        self._steering_queue.append(message)

    def follow_up(self, message: AgentMessage) -> None:
        """Queue a follow‑up message to be processed after the agent finishes."""
        self._follow_up_queue.append(message)

    def clear_steering_queue(self) -> None:
        self._steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self._steering_queue.clear()
        self._follow_up_queue.clear()

    def has_queued_messages(self) -> bool:
        return bool(self._steering_queue or self._follow_up_queue)

    def clear_messages(self) -> None:
        self._state.messages.clear()

    def abort(self) -> None:
        """Abort the currently running prompt."""
        if self._abort_event:
            self._abort_event.set()

    async def wait_for_idle(self) -> None:
        """Wait until the agent finishes processing the current prompt."""
        if self._running_task:
            await self._running_task

    def reset(self) -> None:
        """Reset the agent state (clears messages, queues, and errors)."""
        self._state.messages.clear()
        self._state.is_streaming = False
        self._state.stream_message = None
        self._state.pending_tool_calls.clear()
        self._state.error = None
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

        if not self._state.model:
            raise RuntimeError("No model configured")

        # Convert input to a list of AgentMessages
        messages: List[AgentMessage]
        if isinstance(input, str):
            content: List[Union[TextContent, ImageContent]] = [
                TextContent(
                    type="text", 
                    text=input
                )
            ]
            if images:
                content.extend(images)
            messages = [
                UserMessage(
                    role="user",
                    content=content,
                    timestamp=asyncio.get_event_loop().time(),  # approximate; use int if needed
                )
            ]
        elif isinstance(input, list):
            messages = input
        else:
            messages = [input]

        await self._run_loop(messages)

    async def continue_(self) -> None:
        """
        Continue from the current context (used for retries and resuming queued messages).
        In TypeScript this method is named 'continue' (a keyword in Python, hence the trailing underscore).
        """
        if self._state.is_streaming:
            raise RuntimeError("Agent is already processing. Wait for completion before continuing.")

        if not self._state.messages:
            raise RuntimeError("No messages to continue from")

        last_msg = self._state.messages[-1]
        if last_msg.role == "assistant":
            # If the last message is assistant, first try steering messages
            queued_steering = self._dequeue_steering_messages()
            if queued_steering:
                await self._run_loop(queued_steering, skip_initial_steering_poll=True)
                return

            queued_follow_up = self._dequeue_follow_up_messages()
            if queued_follow_up:
                await self._run_loop(queued_follow_up)
                return

            raise RuntimeError("Cannot continue from message role: assistant")

        await self._run_loop(None)

    # ----------------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------------

    def _dequeue_steering_messages(self) -> List[AgentMessage]:
        """Retrieve messages from the steering queue according to the current mode."""
        if self.steering_mode == "one-at-a-time":
            if self._steering_queue:
                return [self._steering_queue.pop(0)]
            return []
        else:  # "all"
            messages = self._steering_queue[:]
            self._steering_queue.clear()
            return messages

    def _dequeue_follow_up_messages(self) -> List[AgentMessage]:
        """Retrieve messages from the follow‑up queue according to the current mode."""
        if self.follow_up_mode == "one-at-a-time":
            if self._follow_up_queue:
                return [self._follow_up_queue.pop(0)]
            return []
        else:  # "all"
            messages = self._follow_up_queue[:]
            self._follow_up_queue.clear()
            return messages

    async def _run_loop(
        self,
        messages: Optional[List[AgentMessage]] = None,
        *,
        skip_initial_steering_poll: bool = False,
    ) -> None:
        """
        Run the agent loop.
        If messages is None, continue from existing context (agent_loop_continue).
        Otherwise, start a new turn with the given messages.
        """
        model = self._state.model
        if not model:
            raise RuntimeError("No model configured")

        # Mark as streaming and set up cancellation
        self._state.is_streaming = True
        self._state.stream_message = None
        self._state.error = None
        self._abort_event = AbortSignal()

        # Prepare context
        context = AgentContext(
            system_prompt=self._state.system_prompt,
            messages=self._state.messages[:],
            tools=self._state.tools,
        )

        # Steering and follow‑up closures for the config
        skip_initial = skip_initial_steering_poll

        async def get_steering() -> List[AgentMessage]:
            nonlocal skip_initial
            if skip_initial:
                skip_initial = False
                return []
            return self._dequeue_steering_messages()

        async def get_follow_up() -> List[AgentMessage]:
            return self._dequeue_follow_up_messages()

        # Build the loop configuration
        config = AgentLoopConfig(
            model=model,
            reasoning=self._state.thinking_level,
            session_id=self._session_id,
            transport=self._transport,
            thinking_budgets=self._thinking_budgets,
            max_retry_delay_ms=self._max_retry_delay_ms,
            convert_to_llm=self.convert_to_llm,
            transform_context=self.transform_context,
            get_api_key=self.get_api_key,
            get_steering_messages=get_steering,
            get_follow_up_messages=get_follow_up,
        )

        # Choose the appropriate loop starter
        if messages is not None:
            stream = agent_loop(messages, context, config, self._abort_event, self.stream_fn)
        else:
            stream = agent_loop_continue(context, config, self._abort_event, self.stream_fn)

        # Create a task that runs the loop and updates state
        async def _run() -> None:
            partial: Optional[AgentMessage] = None
            try:
                async for event in stream:
                    # Update internal state based on event type
                    if isinstance(event, MessageStartEvent):
                        partial = event.message
                        self._state.stream_message = event.message
                    elif isinstance(event, MessageUpdateEvent):
                        partial = event.message
                        self._state.stream_message = event.message
                    elif isinstance(event, MessageEndEvent):
                        partial = None
                        self._state.stream_message = None
                        self.append_message(event.message)
                    elif isinstance(event, ToolExecutionStartEvent):
                        self._state.pending_tool_calls.add(event.tool_call_id)
                    elif isinstance(event, ToolExecutionEndEvent):
                        self._state.pending_tool_calls.discard(event.tool_call_id)
                    elif isinstance(event, TurnEndEvent):
                        # Check for error message (assistant message may carry error info)
                        if event.message.role == "assistant" and hasattr(event.message, "error_message"):
                            self._state.error = event.message.error_message  # type: ignore
                    elif isinstance(event, AgentEndEvent):
                        self._state.is_streaming = False
                        self._state.stream_message = None

                    # Emit to listeners
                    await self._emit(event)

                # Handle any remaining partial message (non‑empty)
                if partial and partial.role == "assistant" and partial.content:
                    # Check if it contains any non‑empty thinking/text/toolCall
                    non_empty = any(
                        (c.type == "thinking" and c.thinking.strip()) or
                        (c.type == "text" and c.text.strip()) or
                        (c.type == "toolCall" and c.name.strip())
                        for c in partial.content
                    )
                    if non_empty:
                        self.append_message(partial)
                    elif self._abort_event and self._abort_event.is_set():
                        raise asyncio.CancelledError("Request was aborted")
            except asyncio.CancelledError:
                # Abort handled
                raise
            except Exception as e:
                # Build an error assistant message
                error_msg: AgentMessage = AssistantMessage(
                    role="assistant",
                    content=[
                        TextContent(
                            type="text", 
                            text=""
                        )
                    ],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                    usage=Usage(
                        input=0,
                        output=0,
                        cache_read=0,
                        cache_write=0,
                        total_tokens=0,
                        cost=Cost(
                            input=0, 
                            output=0, 
                            cache_read=0, 
                            cache_write=0, 
                            total=0
                        ),
                    ),
                    stop_reason="aborted" if (self._abort_event and self._abort_event.is_set()) else "error",
                    error_message=str(e),
                    timestamp=asyncio.get_event_loop().time(),
                )
                self.append_message(error_msg)
                self._state.error = str(e)
                await self._emit(AgentEndEvent(messages=[error_msg]))
            finally:
                self._state.is_streaming = False
                self._state.stream_message = None
                self._state.pending_tool_calls.clear()
                self._abort_event = None

        # Run the task and store it
        self._running_task = asyncio.create_task(_run())
        try:
            await self._running_task
        finally:
            self._running_task = None

    async def _emit(self, event: AgentEvent) -> None:
        """带超时的异步事件分发"""
        tasks = []
        
        for listener in self._listeners:
            if inspect.iscoroutinefunction(listener):
                # 包装任务，添加超时保护
                task = asyncio.create_task(
                    asyncio.wait_for(listener(event), timeout=120)
                )
                tasks.append(task)
            else:
                # 同步函数在线程池中运行，避免阻塞事件循环
                loop = asyncio.get_event_loop()
                task = loop.run_in_executor(None, listener, event)
                tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)