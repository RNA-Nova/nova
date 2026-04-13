"""
Agent Session 模块 - TypeScript 到 Python 的转换实现
使用 dataclass 替代 interface，消息传输使用 DataClassJSONMixin 实例
"""
from typing import Any, Callable, Dict, List, Literal, Optional, Union
import asyncio
import re

# 消息类型均来自 nova_ai/pi_agent 的 DataClassJSONMixin 继承类
from json_repair import repair_json
from pi_agent import Agent, AgentEvent, AgentMessage, AgentState, AgentTool, AbortSignal
from nova_ai import (
    AssistantMessage, ImageContent, Message, Model, TextContent, ThinkingLevel,
    UserMessage, ToolResultMessage  # DataClassJSONMixin 继承类
)
from ..messages import CustomMessage, FrontendToAgentMessage, AgentToFrontendMessage
from nova_ai import (
    is_context_overflow, models_are_equal, reset_api_registry, supports_xhigh_thinking
)
# from ..config import get_docs_path
from ..utils.sleep import sleep
from ..compaction import (
    CompactionResult, calculate_context_tokens, collect_entries_for_branch_summary,
    compact, estimate_context_tokens, generate_branch_summary, prepare_compaction,
    should_compact, GenerateBranchSummaryOptions, CompactionDetails
)
from ..model_registry import ModelRegistry
from ..resource import ResourceLoader, expand_prompt_template, PromptTemplate
from ..session import SessionManager, get_latest_compaction_entry
from ..setting import SettingsManager
from ..computex import ComputexManager
from ..tools import create_all_tools
from .events import (
    AutoCompactionReason, AutoCompactionStartEvent, AutoCompactionEndEvent,
    AutoRetryStartEvent, AutoRetryEndEvent,
    AgentSessionEvent, AgentSessionEventListener
)
from .options import (
    ScopedModelConfig, AgentSessionConfig ,
    PromptOptions, ModelCycleResult, SessionStats, SessionTokens,
    NavigateOptions
)
from ..definition import DynamicContext

DEFAULT_THINKING_LEVEL = ThinkingLevel.MEDIUM
THINKING_LEVELS = [ThinkingLevel.OFF, ThinkingLevel.MINIMAL, ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH]
THINKING_LEVELS_WITH_XHIGH = [ThinkingLevel.OFF, ThinkingLevel.MINIMAL, ThinkingLevel.LOW, ThinkingLevel.MEDIUM, ThinkingLevel.HIGH, ThinkingLevel.XHIGH]

class AgentSession:
    def __init__(self, config: AgentSessionConfig):
        self._agent = config.agent
        self._build_system_prompt = config.system_prompt_fn
        self._session_manager = config.session_manager
        self._settings_manager = config.settings_manager
        self._computex_manager = config.computex_manager
        self._scoped_models = config.scoped_models or []
        self._resource_loader = config.resource_loader
        self._cwd = config.cwd
        self._model_registry = config.model_registry
        self._initial_active_tool_names = config.initial_active_tool_names
        self._base_tools_override = config.base_tools_override

        self._unsubscribe_agent: Optional[Callable[[], None]] = None
        self._event_listeners: List[AgentSessionEventListener] = []

        self._steering_messages: List[str] = []
        self._follow_up_messages: List[str] = []
        self._pending_next_turn_messages: List[CustomMessage] = []

        self._compaction_abort_controller: Optional[AbortSignal] = None
        self._auto_compaction_abort_controller: Optional[AbortSignal] = None
        self._branch_summary_abort_controller: Optional[AbortSignal] = None

        self._retry_abort_controller: Optional[AbortSignal] = None
        self._retry_attempt = 0
        self._retry_promise: Optional[asyncio.Future[None]] = None
        self._retry_resolve: Optional[Callable[[], None]] = None

        self._tool_registry: Dict[str, AgentTool] = {}
        self._base_system_prompt = ""
        self._last_assistant_message: Optional[AssistantMessage] = None

        self._unsubscribe_agent = self._agent.subscribe(self._handle_agent_event)
        self._build_runtime({'active_tool_names': self._initial_active_tool_names})

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def session_manager(self) -> SessionManager:
        return self._session_manager

    @property
    def settings_manager(self) -> SettingsManager:
        return self._settings_manager
    
    @property
    def computex_manager(self) -> ComputexManager:
        return self._computex_manager

    @property
    def model_registry(self) -> ModelRegistry:
        return self._model_registry

    @property
    def state(self) -> AgentState:
        return self._agent.state

    @property
    def model(self) -> Optional[Model]:
        return self._agent.state.model

    @property
    def thinking_level(self) -> ThinkingLevel:
        return self._agent.state.thinking_level

    @property
    def is_streaming(self) -> bool:
        return self._agent.state.is_streaming

    @property
    def system_prompt(self) -> str:
        return self._agent.state.system_prompt

    @property
    def retry_attempt(self) -> int:
        return self._retry_attempt

    @property
    def is_compacting(self) -> bool:
        return (
            self._auto_compaction_abort_controller is not None or
            self._compaction_abort_controller is not None
        )

    @property
    def messages(self) -> List[AgentMessage]:
        return self._agent.state.messages

    @property
    def steering_mode(self) -> str:
        return self._agent.get_steering_mode()

    @property
    def follow_up_mode(self) -> str:
        return self._agent.get_follow_up_mode()

    @property
    def session_file(self) -> Optional[str]:
        return self._session_manager.get_session_file()

    @property
    def session_id(self) -> str:
        return self._session_manager.get_session_id()

    @property
    def session_name(self) -> Optional[str]:
        return self._session_manager.get_session_name()

    @property
    def scoped_models(self) -> List[ScopedModelConfig]:
        return self._scoped_models.copy()

    def set_scoped_models(self, scoped_models: List[ScopedModelConfig]) -> None:
        self._scoped_models = scoped_models

    @property
    def prompt_templates(self) -> List[PromptTemplate]:
        return self._resource_loader.get_prompts().prompts

    @property
    def resource_loader(self) -> ResourceLoader:
        return self._resource_loader

    @property
    def pending_message_count(self) -> int:
        return len(self._steering_messages) + len(self._follow_up_messages)

    @property
    def is_retrying(self) -> bool:
        return self._retry_promise is not None

    @property
    def auto_retry_enabled(self) -> bool:
        return self._settings_manager.get_retry_enabled()

    @property
    def auto_compaction_enabled(self) -> bool:
        return self._settings_manager.get_compaction_enabled()

    # -------------------------------------------------------------------------
    # 事件管理
    # -------------------------------------------------------------------------

    def subscribe(self, listener: AgentSessionEventListener) -> Callable[[], None]:
        self._event_listeners.append(listener)

        def unsubscribe():
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: AgentSessionEvent) -> None:
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception:
                pass

    def _disconnect_from_agent(self) -> None:
        if self._unsubscribe_agent:
            self._unsubscribe_agent()
            self._unsubscribe_agent = None

    def _reconnect_to_agent(self) -> None:
        if self._unsubscribe_agent:
            return
        self._unsubscribe_agent = self._agent.subscribe(self._handle_agent_event)

    def dispose(self) -> None:
        self._disconnect_from_agent()
        self._event_listeners.clear()

    async def _handle_agent_event(self, event: AgentEvent) -> None:
        """处理 agent 事件，使用 isinstance 检查消息类型"""
        if hasattr(event, 'type') and event.type == "message_start":
            msg = event.message
            if isinstance(msg, UserMessage):
                message_text = self._get_user_message_text(msg)
                if message_text:
                    if message_text in self._steering_messages:
                        self._steering_messages.remove(message_text)
                    elif message_text in self._follow_up_messages:
                        self._follow_up_messages.remove(message_text)

        self._emit(event)

        if hasattr(event, 'type') and event.type == "message_end":
            msg = event.message
            
            # 使用 isinstance 检查类型，而非 role 字段
            if isinstance(msg, CustomMessage):
                self._session_manager.append_custom_message_entry(
                    msg.custom_type, msg.content, msg.display, msg.details
                )
            elif isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage)):
                self._session_manager.append_message(msg)
                if isinstance(msg,ToolResultMessage):
                    if msg.details.get("agent_to_frontend"):
                        agent_to_frontend_message_json = msg.content[0].text
                        agent_to_frontend_message_dict = repair_json(agent_to_frontend_message_json, return_objects=True)
                        agent_to_frontend_message = AgentToFrontendMessage.from_dict(agent_to_frontend_message_dict)
                        self._session_manager.append_agent_to_frontend_message(
                            content=agent_to_frontend_message.content,
                            display=agent_to_frontend_message.display,

                        )

            if isinstance(msg, AssistantMessage):
                self._last_assistant_message = msg
                if msg.stop_reason != "error" and self._retry_attempt > 0:
                    self._emit(AutoRetryEndEvent(
                        success=True, attempt=self._retry_attempt
                    ))
                    self._retry_attempt = 0
                    self._resolve_retry()

        if (hasattr(event, 'type') and event.type == "agent_end" and 
            self._last_assistant_message):
            msg = self._last_assistant_message
            self._last_assistant_message = None

            if self._is_retryable_error(msg):
                did_retry = await self._handle_retryable_error(msg)
                if did_retry:
                    return

            await self._check_compaction(msg)

    def _get_user_message_text(self, message: Message) -> str:
        """从 UserMessage dataclass 提取文本"""
        if not isinstance(message, UserMessage):
            return ""
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join([
                c.text for c in content 
                if isinstance(c, TextContent)
            ])
        return ""

    def _find_last_assistant_message(self) -> Optional[AssistantMessage]:
        messages = self._agent.state.messages
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, AssistantMessage):
                return msg
        return None

    def _resolve_retry(self) -> None:
        if self._retry_resolve:
            self._retry_resolve()
            self._retry_resolve = None
            self._retry_promise = None

    # -------------------------------------------------------------------------
    # 工具管理
    # -------------------------------------------------------------------------

    def get_active_tool_names(self) -> List[str]:
        return [t.name for t in self._agent.state.tools]

    def get_all_tools(self) -> List[Dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tool_registry.values()
        ]

    def set_active_tools_by_name(self, tool_names: List[str]) -> None:
        tools = []
        valid_tool_names = []
        for name in tool_names:
            tool = self._tool_registry.get(name)
            if tool:
                tools.append(tool)
                valid_tool_names.append(name)

        self._agent.set_tools(tools)
        self._base_system_prompt = self._rebuild_system_prompt(valid_tool_names)
        self._agent.set_system_prompt(self._base_system_prompt)

    def _rebuild_system_prompt(self, tool_names: List[str]) -> str:
        valid_tool_names = [n for n in tool_names if n in self._tool_registry]
        return self._build_system_prompt(
            DynamicContext(
                cwd = self._cwd
            ),
            valid_tool_names,
        )

    def _build_runtime(self, options: Dict[str, Any]) -> None:
        # auto_resize = self._settings_manager.get_image_auto_resize()
        # shell_prefix = self._settings_manager.get_shell_command_prefix()

        if self._base_tools_override:
            base_tools = self._base_tools_override
        else:
            base_tools = create_all_tools(self._computex_manager)

        self._tool_registry = dict(base_tools)
        default_active = (
            list(self._base_tools_override.keys()) if self._base_tools_override
            else ["read", "bash", "edit", "write"]
        )
        base_active = options.get('active_tool_names', default_active)
        active_set = set(base_active)

        active_tools = [
            self._tool_registry[n] for n in active_set if n in self._tool_registry
        ]
        self._agent.set_tools(active_tools)

        system_prompt_names = [n for n in active_set if n in self._tool_registry]
        self._base_system_prompt = self._rebuild_system_prompt(system_prompt_names)
        self._agent.set_system_prompt(self._base_system_prompt)

    # -------------------------------------------------------------------------
    # 消息处理（使用 dataclass 实例）
    # -------------------------------------------------------------------------

    async def prompt(self, text: str, options: Optional[PromptOptions] = None) -> None:
        opts = options or PromptOptions()
        expanded_text = text
        if opts.expand_prompt_templates:
            expanded_text = expand_prompt_template(expanded_text, list(self.prompt_templates))

        if self.is_streaming:
            if not opts.streaming_behavior:
                raise ValueError(
                    "Agent is already processing. Specify streamingBehavior "
                    "('steer' or 'followUp') to queue the message."
                )
            if opts.streaming_behavior == "follow_up":
                await self._queue_follow_up(expanded_text, opts.images)
            else:
                await self._queue_steer(expanded_text, opts.images)
            return

        if not self.model:
            raise ValueError(
                f"No model selected.\n\nUse /login or set an API key environment variable. "
                # f"See {get_docs_path()}/providers.md\n\nThen use /model to select a model."
            )

        api_key = await self._model_registry.get_api_key(self.model)
        if not api_key:
            raise ValueError(
                f"No API key found for {self.model.provider}.\n\n"
                # f"Use /login or set an API key environment variable. "
                # f"See {get_docs_path()}/providers.md"
            )

        last_assistant = self._find_last_assistant_message()
        if last_assistant:
            await self._check_compaction(last_assistant, False)

        # 使用 dataclass 构造消息列表
        messages: List[AgentMessage] = []
        
        # 构造 UserMessage dataclass 实例（而非 dict）
        user_content: List[Union[TextContent, ImageContent]] = [TextContent(text=expanded_text)]
        if opts.images:
            user_content.extend(opts.images)
        
        user_message = UserMessage(
            content=user_content,
            timestamp=asyncio.get_event_loop().time()
        )
        messages.append(user_message)

        # 添加待处理的 CustomMessage
        for msg in self._pending_next_turn_messages:
            messages.append(msg)
        self._pending_next_turn_messages.clear()

        await self._agent.prompt(messages)
        await self._wait_for_retry()

    async def steer(self, text: str, images: Optional[List[ImageContent]] = None) -> None:
        expanded_text = expand_prompt_template(text, list(self.prompt_templates))
        await self._queue_steer(expanded_text, images)

    async def follow_up(self, text: str, images: Optional[List[ImageContent]] = None) -> None:
        expanded_text = expand_prompt_template(text, list(self.prompt_templates))
        await self._queue_follow_up(expanded_text, images)

    async def _queue_steer(self, text: str, images: Optional[List[ImageContent]] = None) -> None:
        """使用 UserMessage dataclass 而非 dict"""
        self._steering_messages.append(text)
        
        content: List[Union[TextContent, ImageContent]] = [TextContent(text=text)]
        if images:
            content.extend(images)
        
        # 构造 UserMessage 实例
        user_message = UserMessage(
            content=content,
            timestamp=asyncio.get_event_loop().time()
        )
        self._agent.steer(user_message)

    async def _queue_follow_up(self, text: str, images: Optional[List[ImageContent]] = None) -> None:
        """使用 UserMessage dataclass 而非 dict"""
        self._follow_up_messages.append(text)
        
        content: List[Union[TextContent, ImageContent]] = [TextContent(text=text)]
        if images:
            content.extend(images)
        
        # 构造 UserMessage 实例
        user_message = UserMessage(
            content=content,
            timestamp=asyncio.get_event_loop().time()
        )
        self._agent.follow_up(user_message)

    async def send_custom_message(
        self,
        message_data: Dict[str, Any],  # 输入参数，构造为 CustomMessage
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """构造 CustomMessage dataclass 实例"""
        opts = options or {}
        
        # 使用 CustomMessage dataclass 构造实例（而非 dict）
        custom_message = CustomMessage(
            custom_type=message_data.get('custom_type'),
            content=message_data.get('content'),
            display=message_data.get('display'),
            details=message_data.get('details'),
            timestamp=asyncio.get_event_loop().time()
        )

        deliver_as = opts.get('deliver_as')

        if deliver_as == "next_turn":
            self._pending_next_turn_messages.append(custom_message)
        elif self.is_streaming:
            if deliver_as == "follow_up":
                self._agent.follow_up(custom_message)
            else:
                self._agent.steer(custom_message)
        elif opts.get('trigger_turn'):
            await self._agent.prompt([custom_message])
        else:
            self._agent.append_message(custom_message)
            self._session_manager.append_custom_message_entry(
                custom_message.custom_type,
                custom_message.content,
                custom_message.display,
                custom_message.details
            )

    async def send_frontend_to_agent_message(
        self,
        message_data: Dict[str, Any],  # 输入参数，构造为 CustomMessage
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """构造 CustomMessage dataclass 实例"""
        opts = options or {}
        
        # 使用 CustomMessage dataclass 构造实例（而非 dict）
        frontend_to_agent_message = FrontendToAgentMessage.from_dict(
            message_data
        )
        frontend_to_agent_message.timestamp = asyncio.get_event_loop().time()
        deliver_as = opts.get('deliver_as')

        if deliver_as == "next_turn":
            self._pending_next_turn_messages.append(frontend_to_agent_message)
        elif self.is_streaming:
            if deliver_as == "follow_up":
                self._agent.follow_up(frontend_to_agent_message)
            else:
                self._agent.steer(frontend_to_agent_message)
        elif opts.get('trigger_turn'):
            await self._agent.prompt([frontend_to_agent_message])
        else:
            self._agent.append_message(frontend_to_agent_message)
            self._session_manager.append_frontend_to_agent_message(
                frontend_to_agent_message.content,
                frontend_to_agent_message.display,
            )

    async def send_user_message(
        self,
        content: Union[str, List[Union[TextContent, ImageContent]]],
        options: Optional[Dict[str, str]] = None,
    ) -> None:
        """处理用户消息输入，转换为 UserMessage"""
        opts = options or {}
        text = ""
        images: Optional[List[ImageContent]] = None

        if isinstance(content, str):
            text = content
        else:
            text_parts = []
            images = []
            for part in content:
                if isinstance(part, TextContent):
                    text_parts.append(part.text)
                elif isinstance(part, ImageContent):
                    images.append(part)
            text = "\n".join(text_parts)
            if not images:
                images = None

        await self.prompt(text, PromptOptions(
            expand_prompt_templates=False,
            streaming_behavior=opts.get('deliver_as'),
            images=images or [],
        ))

    def clear_queue(self) -> Dict[str, List[str]]:
        steering = self._steering_messages.copy()
        follow_up = self._follow_up_messages.copy()
        self._steering_messages.clear()
        self._follow_up_messages.clear()
        self._agent.clear_all_queues()
        return {'steering': steering, 'follow_up': follow_up}

    def get_steering_messages(self) -> List[str]:
        return self._steering_messages.copy()

    def get_follow_up_messages(self) -> List[str]:
        return self._follow_up_messages.copy()

    async def abort(self) -> None:
        self.abort_retry()
        self._agent.abort()
        await self._agent.wait_for_idle()

    # -------------------------------------------------------------------------
    # 会话管理
    # -------------------------------------------------------------------------

    async def new_session(self, options: Optional[Dict[str, Any]] = None) -> bool:
        opts = options or {}
        self._disconnect_from_agent()
        await self.abort()
        self._agent.reset()
        self._session_manager.new_session({'parent_session': opts.get('parent_session')})
        self._agent.session_id = self._session_manager.get_session_id()
        self._steering_messages.clear()
        self._follow_up_messages.clear()
        self._pending_next_turn_messages.clear()

        self._session_manager.append_thinking_level_change(self.thinking_level)

        setup = opts.get('setup')
        if setup:
            await setup(self._session_manager)
            session_context = self._session_manager.build_session_context()
            self._agent.replace_messages(session_context.messages)

        self._reconnect_to_agent()
        return True

    async def switch_session(self, session_path: str) -> bool:
        self._disconnect_from_agent()
        await self.abort()
        self._steering_messages.clear()
        self._follow_up_messages.clear()
        self._pending_next_turn_messages.clear()

        self._session_manager.set_session_file(session_path)
        self._agent.session_id = self._session_manager.get_session_id()

        session_context = self._session_manager.build_session_context()
        self._agent.replace_messages(session_context.messages)

        if session_context.model:
            available_models = self._model_registry.get_available()
            provider, model_id = session_context.model
            match = next((
                m for m in available_models
                if m.provider == provider and
                m.id == model_id
            ), None)
            if match:
                self._agent.set_model(match)

        has_thinking_entry = any(
            e.type == "thinking_level_change"
            for e in self._session_manager.get_branch()
        )
        default_level = self._settings_manager.get_default_thinking_level() or DEFAULT_THINKING_LEVEL

        if has_thinking_entry:
            self.set_thinking_level(session_context.thinking_level)
        else:
            available = self.get_available_thinking_levels()
            effective = (
                default_level if default_level in available
                else self._clamp_thinking_level(default_level, available)
            )
            self._agent.set_thinking_level(effective)
            self._session_manager.append_thinking_level_change(effective)

        self._reconnect_to_agent()
        return True

    def set_session_name(self, name: str) -> None:
        self._session_manager.append_session_info(name)

    async def fork(self, entry_id: str) -> Dict[str, str]:
        selected_entry = self._session_manager.get_entry(entry_id)

        if not selected_entry or selected_entry.type != "message":
            raise ValueError("Invalid entry ID for forking")
        
        # 确保是 UserMessage
        if not isinstance(selected_entry.message, UserMessage):
            raise ValueError("Invalid entry ID for forking")

        selected_text = self._get_user_message_text(selected_entry.message)

        self._pending_next_turn_messages.clear()

        if not selected_entry.parent_id:
            self._session_manager.new_session({'parent_session': self.session_file})
        else:
            self._session_manager.create_branched_session(selected_entry.parent_id)
        self._agent.session_id = self._session_manager.get_session_id()

        session_context = self._session_manager.build_session_context()
        self._agent.replace_messages(session_context.messages)

        return {'selected_text': selected_text}

    async def navigate_tree(
        self, target_id: str, options: Optional[NavigateOptions] = None
    ) -> Dict[str, Any]:
        opts = options or NavigateOptions()
        old_leaf_id = self._session_manager.get_leaf_id()

        if target_id == old_leaf_id:
            return {}

        if opts.summarize and not self.model:
            raise ValueError("No model available for summarization")

        target_entry = self._session_manager.get_entry(target_id)
        if not target_entry:
            raise ValueError(f"Entry {target_id} not found")

        collect_entries_result = collect_entries_for_branch_summary(
            self._session_manager, old_leaf_id, target_id
        )
        entries_to_summarize = collect_entries_result.entries
        print(entries_to_summarize)
        common_ancestor_id = collect_entries_result.common_ancestor_id
        self._branch_summary_abort_controller = AbortSignal()

        summary_text = None
        summary_details = None

        if opts.summarize and entries_to_summarize:
            model = self.model
            api_key = await self._model_registry.get_api_key(model)
            if not api_key:
                raise ValueError(f"No API key for {model.provider}")

            branch_summary_settings = self._settings_manager.get_branch_summary_settings()
            result = await generate_branch_summary(
                entries_to_summarize, 
                GenerateBranchSummaryOptions(
                    model = model,
                    api_key = api_key,
                    signal = self._branch_summary_abort_controller,
                    custom_instructions = opts.custom_instructions,
                    replace_instructions = opts.replace_instructions,
                    reserve_tokens = branch_summary_settings.reserve_tokens,
                )
            )
            self._branch_summary_abort_controller = None

            if result.aborted:
                return {'aborted': True}
            if result.error:
                raise ValueError(result.error)

            summary_text = result.summary
            summary_details = CompactionDetails.from_dict({
                'read_files': result.read_files or [],
                'modified_files': result.modified_files or [],
            })

        new_leaf_id = None
        editor_text = None

        if target_entry.type == "message" and isinstance(target_entry.message, UserMessage):
            new_leaf_id = target_entry.parent_id
            editor_text = self._get_user_message_text(target_entry.message)
        elif target_entry.type == "custom_message":
            new_leaf_id = target_entry.parent_id
            content = target_entry.content
            if isinstance(content, str):
                editor_text = content
            else:
                editor_text = "".join([
                    c.text for c in content if isinstance(c, TextContent)
                ])
        else:
            new_leaf_id = target_id

        summary_entry = None
        if summary_text:
            summary_id = self._session_manager.branch_with_summary(
                new_leaf_id, summary_text, summary_details, False
            )
            summary_entry = self._session_manager.get_entry(summary_id)
            if opts.label:
                self._session_manager.append_label_change(summary_id, opts.label)
        elif new_leaf_id is None:
            self._session_manager.reset_leaf()
        else:
            self._session_manager.branch(new_leaf_id)

        if opts.label and not summary_text:
            self._session_manager.append_label_change(target_id, opts.label)

        session_context = self._session_manager.build_session_context()
        self._agent.replace_messages(session_context.messages)

        self._branch_summary_abort_controller = None
        return {'editor_text': editor_text, 'summary_entry': summary_entry}

    def get_user_messages_for_forking(self) -> List[Dict[str, str]]:
        entries = self._session_manager.get_entries()
        result = []

        for entry in entries:
            if entry.type != "message":
                continue
            if not isinstance(entry.message, UserMessage):
                continue
            text = self._get_user_message_text(entry.message)
            if text:
                result.append({'entry_id': entry.id, 'text': text})

        return result

    def get_session_stats(self) -> SessionStats:
        state = self.state
        user_messages = len([m for m in state.messages if isinstance(m, UserMessage)])
        assistant_messages = len([m for m in state.messages if isinstance(m, AssistantMessage)])
        tool_results = len([m for m in state.messages if isinstance(m, ToolResultMessage)])

        tool_calls = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_cost = 0.0

        for message in state.messages:
            if isinstance(message, AssistantMessage):
                tool_calls += len([c for c in message.content if hasattr(c, 'type') and c.type == "toolCall"])
                total_input += message.usage.input
                total_output += message.usage.output
                total_cache_read += message.usage.cache_read
                total_cache_write += message.usage.cache_write
                total_cost += message.usage.cost.total

        return SessionStats(
            session_file=self.session_file,
            session_id=self.session_id,
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            tool_results=tool_results,
            total_messages=len(state.messages),
            tokens=SessionTokens(
                input_tokens=total_input,
                output_tokens=total_output,
                cache_read=total_cache_read,
                cache_write=total_cache_write,
                total=total_input + total_output + total_cache_read + total_cache_write,
            ),
            cost=total_cost,
        )

    def get_context_usage(self) -> Optional[Dict[str, Any]]:
        model = self.model
        if not model:
            return None

        context_window = model.context_window or 0
        if context_window <= 0:
            return None

        branch_entries = self._session_manager.get_branch()
        latest_compaction = get_latest_compaction_entry(branch_entries)

        if latest_compaction:
            compaction_index = -1
            for i, entry in enumerate(branch_entries):
                if entry == latest_compaction:
                    compaction_index = i
                    break

            has_post_compaction_usage = False
            for i in range(len(branch_entries) - 1, compaction_index, -1):
                entry = branch_entries[i]
                if entry.type == "message" and isinstance(entry.message, AssistantMessage):
                    assistant = entry.message
                    if assistant.stop_reason not in ("aborted", "error"):
                        context_tokens = calculate_context_tokens(assistant.usage)
                        if context_tokens > 0:
                            has_post_compaction_usage = True
                        break

            if not has_post_compaction_usage:
                return {'tokens': None, 'context_window': context_window, 'percent': None}

        estimate = estimate_context_tokens(self.messages)
        percent = (estimate.tokens / context_window) * 100

        return {
            'tokens': estimate.tokens,
            'context_window': context_window,
            'percent': percent,
        }

    def get_last_assistant_text(self) -> Optional[str]:
        reversed_messages = list(reversed(self.messages))
        last_assistant = None

        for m in reversed_messages:
            if not isinstance(m, AssistantMessage):
                continue
            if m.stop_reason == "aborted" and len(m.content) == 0:
                continue
            last_assistant = m
            break

        if not last_assistant:
            return None

        text = "".join([
            c.text for c in last_assistant.content if isinstance(c, TextContent)
        ])

        return text.strip() if text.strip() else None
    # -------------------------------------------------------------------------
    # 工作机管理
    # -------------------------------------------------------------------------
    async def set_computex(self, host: str, port: int):
        self._computex_manager.set_proxy(host,port)

    # -------------------------------------------------------------------------
    # 模型管理
    # -------------------------------------------------------------------------

    async def set_model(self, model: Model) -> None:
        api_key = await self._model_registry.get_api_key(model)
        if not api_key:
            raise ValueError(f"No API key for {model.provider}/{model.id}")

        self._agent.set_model(model)
        self._session_manager.append_model_change(model.provider, model.id)
        self._settings_manager.set_default_model_and_provider(model.provider, model.id)
        self.set_thinking_level(self.thinking_level)

    async def cycle_model(self, direction: str = "forward") -> Optional[ModelCycleResult]:
        if self._scoped_models:
            return await self._cycle_scoped_model(direction)
        return await self._cycle_available_model(direction)

    async def _get_scoped_models_with_api_key(self) -> List[ScopedModelConfig]:
        api_keys_by_provider: Dict[str, Optional[str]] = {}
        result = []

        for scoped in self._scoped_models:
            provider = scoped.model.provider
            if provider not in api_keys_by_provider:
                api_keys_by_provider[provider] = await self._model_registry.get_api_key_for_provider(provider)
            if api_keys_by_provider[provider]:
                result.append(scoped)
        return result

    async def _cycle_scoped_model(self, direction: str) -> Optional[ModelCycleResult]:
        scoped_models = await self._get_scoped_models_with_api_key()
        if len(scoped_models) <= 1:
            return None

        current_model = self.model
        current_index = -1
        for i, sm in enumerate(scoped_models):
            if models_are_equal(sm.model, current_model):
                current_index = i
                break

        if current_index == -1:
            current_index = 0

        length = len(scoped_models)
        next_index = (current_index + 1) % length if direction == "forward" else (current_index - 1 + length) % length
        next_config = scoped_models[next_index]

        self._agent.set_model(next_config.model)
        self._session_manager.append_model_change(next_config.model.provider, next_config.model.id)
        self._settings_manager.set_default_model_and_provider(next_config.model.provider, next_config.model.id)
        self.set_thinking_level(next_config.thinking_level)

        return ModelCycleResult(
            model=next_config.model,
            thinking_level=self.thinking_level,
            is_scoped=True
        )

    async def _cycle_available_model(self, direction: str) -> Optional[ModelCycleResult]:
        available_models = self._model_registry.get_available()
        if len(available_models) <= 1:
            return None

        current_model = self.model
        current_index = -1
        for i, m in enumerate(available_models):
            if models_are_equal(m, current_model):
                current_index = i
                break

        if current_index == -1:
            current_index = 0

        length = len(available_models)
        next_index = (current_index + 1) % length if direction == "forward" else (current_index - 1 + length) % length
        next_model = available_models[next_index]

        api_key = await self._model_registry.get_api_key(next_model)
        if not api_key:
            raise ValueError(f"No API key for {next_model.provider}/{next_model.id}")

        self._agent.set_model(next_model)
        self._session_manager.append_model_change(next_model.provider, next_model.id)
        self._settings_manager.set_default_model_and_provider(next_model.provider, next_model.id)
        self.set_thinking_level(self.thinking_level)

        return ModelCycleResult(
            model=next_model,
            thinking_level=self.thinking_level,
            is_scoped=False
        )

    # -------------------------------------------------------------------------
    # 思考级别管理
    # -------------------------------------------------------------------------

    def set_thinking_level(self, level: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh"]] = None) -> None:
        level = ThinkingLevel(level) if level else level
        available_levels = self.get_available_thinking_levels()
        effective_level = level if level in available_levels else self._clamp_thinking_level(level, available_levels)

        is_changing = effective_level != self._agent.state.thinking_level
        self._agent.set_thinking_level(effective_level)

        if is_changing:
            self._session_manager.append_thinking_level_change(effective_level)
            self._settings_manager.set_default_thinking_level(effective_level)

    def cycle_thinking_level(self) -> Optional[ThinkingLevel]:
        if not self.supports_thinking():
            return None

        levels = self.get_available_thinking_levels()
        current_index = levels.index(self.thinking_level)
        next_index = (current_index + 1) % len(levels)
        next_level = levels[next_index]

        self.set_thinking_level(next_level)
        return next_level

    def get_available_thinking_levels(self) -> List[ThinkingLevel]:
        if not self.supports_thinking():
            return []
        return THINKING_LEVELS_WITH_XHIGH if self.supports_xhigh_thinking() else THINKING_LEVELS

    def supports_xhigh_thinking(self) -> bool:
        return supports_xhigh_thinking(self.model) if self.model else False

    def supports_thinking(self) -> bool:
        return bool(self.model and self.model.reasoning)

    def _clamp_thinking_level(self, level: ThinkingLevel, available_levels: List[ThinkingLevel]) -> ThinkingLevel:
        ordered = THINKING_LEVELS_WITH_XHIGH
        available_set = set(available_levels)

        try:
            requested_index = ordered.index(level)
        except ValueError:
            return available_levels[0] if available_levels else None

        for i in range(requested_index, len(ordered)):
            if ordered[i] in available_set:
                return ordered[i]

        for i in range(requested_index - 1, -1, -1):
            if ordered[i] in available_set:
                return ordered[i]

        return available_levels[0] if available_levels else None

    def set_steering_mode(self, mode: str) -> None:
        self._agent.set_steering_mode(mode)
        self._settings_manager.set_steering_mode(mode)

    def set_follow_up_mode(self, mode: str) -> None:
        self._agent.set_follow_up_mode(mode)
        self._settings_manager.set_follow_up_mode(mode)

    # -------------------------------------------------------------------------
    # 压缩管理
    # -------------------------------------------------------------------------

    async def compact(self, custom_instructions: Optional[str] = None) -> CompactionResult:
        self._disconnect_from_agent()
        await self.abort()
        self._compaction_abort_controller = AbortSignal()

        try:
            if not self.model:
                raise ValueError("No model selected")

            api_key = await self._model_registry.get_api_key(self.model)
            if not api_key:
                raise ValueError(f"No API key for {self.model.provider}")

            path_entries = self._session_manager.get_branch()
            settings = self._settings_manager.get_compaction_settings()

            preparation = prepare_compaction(path_entries, settings)
            if not preparation:
                last_entry = path_entries[-1] if path_entries else None
                if last_entry and last_entry.type == "compaction":
                    raise ValueError("Already compacted")
                raise ValueError("Nothing to compact (session too small)")
            result = await compact(
                preparation, self.model, api_key, custom_instructions,
                self._compaction_abort_controller
            )

            if self._compaction_abort_controller.aborted:
                raise ValueError("Compaction cancelled")
            self._session_manager.append_compaction(
                result.summary, result.first_kept_entry_id,
                result.tokens_before, result.details, False
            )
            session_context = self._session_manager.build_session_context()
            self._agent.replace_messages(session_context.messages)

            return result
        finally:
            self._compaction_abort_controller = None
            self._reconnect_to_agent()

    def abort_compaction(self) -> None:
        if self._compaction_abort_controller:
            self._compaction_abort_controller.set()
        if self._auto_compaction_abort_controller:
            self._auto_compaction_abort_controller.set()

    def abort_branch_summary(self) -> None:
        if self._branch_summary_abort_controller:
            self._branch_summary_abort_controller.set()

    async def _check_compaction(
        self, assistant_message: AssistantMessage, skip_aborted_check: bool = True
    ) -> None:
        settings = self._settings_manager.get_compaction_settings()
        if not settings.enabled:
            return

        if skip_aborted_check and assistant_message.stop_reason == "aborted":
            return

        context_window = self.model.context_window if self.model else 0

        same_model = (
            self.model and
            assistant_message.provider == self.model.provider and
            assistant_message.model == self.model.id
        )

        compaction_entry = get_latest_compaction_entry(self._session_manager.get_branch())
        error_is_from_before_compaction = (
            compaction_entry is not None and
            assistant_message.timestamp < compaction_entry.timestamp
        )

        if (
            same_model and not error_is_from_before_compaction and
            is_context_overflow(assistant_message, context_window)
        ):
            messages = self._agent.state.messages
            if messages and isinstance(messages[-1], AssistantMessage):
                self._agent.replace_messages(messages[:-1])
            await self._run_auto_compaction("overflow", True)
            return

        if assistant_message.stop_reason == "error":
            return

        context_tokens = calculate_context_tokens(assistant_message.usage)
        if should_compact(context_tokens, context_window, settings):
            await self._run_auto_compaction("threshold", False)

    async def _run_auto_compaction(self, reason: str, will_retry: bool) -> None:
        settings = self._settings_manager.get_compaction_settings()

        enum_reason = AutoCompactionReason.OVERFLOW if reason == "overflow" else AutoCompactionReason.THRESHOLD
        self._emit(AutoCompactionStartEvent(reason=enum_reason))
        self._auto_compaction_abort_controller = AbortSignal()

        try:
            if not self.model:
                self._emit(AutoCompactionEndEvent(result=None, aborted=False, will_retry=False))
                return

            api_key = await self._model_registry.get_api_key(self.model)
            if not api_key:
                self._emit(AutoCompactionEndEvent(result=None, aborted=False, will_retry=False))
                return

            path_entries = self._session_manager.get_branch()
            preparation = prepare_compaction(path_entries, settings)

            if not preparation:
                self._emit(AutoCompactionEndEvent(result=None, aborted=False, will_retry=False))
                return

            result = await compact(
                preparation, self.model, api_key, None,
                self._auto_compaction_abort_controller
            )

            if self._auto_compaction_abort_controller.aborted:
                self._emit(AutoCompactionEndEvent(result=None, aborted=True, will_retry=False))
                return

            self._session_manager.append_compaction(
                result.summary, result.first_kept_entry_id,
                result.tokens_before, result.details, False
            )
            session_context = self._session_manager.build_session_context()
            self._agent.replace_messages(session_context.messages)

            self._emit(AutoCompactionEndEvent(result=result, aborted=False, will_retry=will_retry))

            if will_retry:
                messages = self._agent.state.messages
                last_msg = messages[-1] if messages else None
                if last_msg and isinstance(last_msg, AssistantMessage) and last_msg.stop_reason == "error":
                    self._agent.replace_messages(messages[:-1])

                asyncio.get_event_loop().call_later(0.1, lambda: asyncio.create_task(self._safe_continue()))
            elif self._agent.has_queued_messages():
                asyncio.get_event_loop().call_later(0.1, lambda: asyncio.create_task(self._safe_continue()))

        except Exception as error:
            error_message = str(error) if isinstance(error, Exception) else "compaction failed"
            final_error = (
                f"Context overflow recovery failed: {error_message}" if reason == "overflow"
                else f"Auto-compaction failed: {error_message}"
            )
            self._emit(AutoCompactionEndEvent(
                result=None, aborted=False, will_retry=False, error_message=final_error
            ))
        finally:
            self._auto_compaction_abort_controller = None

    async def _safe_continue(self) -> None:
        try:
            await self._agent.continue_()
        except Exception:
            pass

    def set_auto_compaction_enabled(self, enabled: bool) -> None:
        self._settings_manager.set_compaction_enabled(enabled)

    # -------------------------------------------------------------------------
    # 自动重试
    # -------------------------------------------------------------------------

    def _is_retryable_error(self, message: AssistantMessage) -> bool:
        if message.stop_reason != "error" or not message.error_message:
            return False

        context_window = self.model.context_window if self.model else 0
        if is_context_overflow(message, context_window):
            return False

        err = message.error_message
        pattern = (
            r'overloaded|rate.?limit|too many requests|429|500|502|503|504|'
            r'service.?unavailable|server error|internal error|connection.?error|'
            r'connection.?refused|other side closed|fetch failed|upstream.?connect|'
            r'reset before headers|terminated|retry delay'
        )
        return bool(re.search(pattern, err, re.IGNORECASE))

    async def _handle_retryable_error(self, message: AssistantMessage) -> bool:
        settings = self._settings_manager.get_retry_settings()
        if not settings.enabled:
            return False

        self._retry_attempt += 1

        if self._retry_attempt == 1 and not self._retry_promise:
            self._retry_promise = asyncio.Future()
            self._retry_resolve = lambda: (
                self._retry_promise.set_result(None) if not self._retry_promise.done() else None
            )

        if self._retry_attempt > settings.max_retries:
            self._emit(AutoRetryEndEvent(
                success=False, attempt=self._retry_attempt - 1,
                final_error=message.error_message
            ))
            self._retry_attempt = 0
            self._resolve_retry()
            return False

        delay_ms = settings.base_delay_ms * (2 ** (self._retry_attempt - 1))

        self._emit(AutoRetryStartEvent(
            attempt=self._retry_attempt, max_attempts=settings.max_retries,
            delay_ms=delay_ms, error_message=message.error_message or "Unknown error"
        ))

        messages = self._agent.state.messages
        if messages and isinstance(messages[-1], AssistantMessage):
            self._agent.replace_messages(messages[:-1])

        self._retry_abort_controller = AbortSignal()
        try:
            await sleep(delay_ms, self._retry_abort_controller)
        except Exception:
            attempt = self._retry_attempt
            self._retry_attempt = 0
            self._retry_abort_controller = None
            self._emit(AutoRetryEndEvent(
                success=False, attempt=attempt, final_error="Retry cancelled"
            ))
            self._resolve_retry()
            return False

        self._retry_abort_controller = None
        asyncio.get_event_loop().call_later(0, lambda: asyncio.create_task(self._safe_continue()))
        return True

    def abort_retry(self) -> None:
        if self._retry_abort_controller:
            self._retry_abort_controller.set()
        self._resolve_retry()

    async def _wait_for_retry(self) -> None:
        if self._retry_promise and not self._retry_promise.done():
            await self._retry_promise

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        self._settings_manager.set_retry_enabled(enabled)

    async def reload(self) -> None:
        self._settings_manager.reload()
        reset_api_registry()
        await self._resource_loader.reload()
        self._build_runtime({'active_tool_names': self.get_active_tool_names()})