"""用户工具调用控制。"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from nova_agent import CustomAgentMessage
from nova_ai import AbortController

from nova_harness.core.harness.user_tools import UserToolManager
from nova_harness.core.types.events import USER_BASH, UserBashEvent, UserToolEvent
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.types.resources.user_tools import UserToolEventCallback


class UserToolController:
    """用户工具的会话集成：pending/flush、abort 级联、消息记录。"""

    def __init__(
        self,
        session: AgentSessionProtocol,
        manager: UserToolManager,
    ) -> None:
        self._session = session
        self._manager = manager
        # 活跃调用表：call_id -> (工具名, AbortController)
        self._active: Dict[str, Tuple[str, AbortController]] = {}

    @property
    def has_pending_messages(self) -> bool:
        return len(self._session._pending_session_messages) > 0

    def is_running(self, name: Optional[str] = None) -> bool:
        """是否有用户工具调用正在执行；name 给定时只看该工具。"""
        if name is None:
            return bool(self._active)
        return any(tool_name == name for tool_name, _ in self._active.values())

    async def invoke(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        on_event: Optional[UserToolEventCallback] = None,
        call_id: Optional[str] = None,
    ) -> CustomAgentMessage:
        """调用一个用户工具并把结果消息记录进会话。"""
        call_id = call_id or uuid.uuid4().hex[:8]
        controller = AbortController(f"user_tool:{name}")
        self._active[call_id] = (name, controller)
        # 拷贝参数：拦截注入（operations）不污染调用方字典
        params = dict(params or {})

        def _emit_event(event_name: str, data: Dict[str, Any]) -> None:
            # 工具进度统一经会话事件总线透出（RPC 映射为 user_tool 通知）
            self._session._emit(
                UserToolEvent(tool=name, event=event_name, data=data, call_id=call_id)
            )
            if on_event is not None:
                on_event(event_name, data)

        try:
            # 扩展拦截（pi user_bash 语义）：扩展返回完整 result 时
            # 跳过真实执行，直接记录其返回
            intercepted = await self._intercept_user_bash(name, params)
            if intercepted is not None:
                self.record(intercepted)
                return intercepted
            message = await self._manager.invoke(
                name, params, _emit_event, controller.signal
            )
        finally:
            self._active.pop(call_id, None)
        self.record(message)
        return message

    async def _intercept_user_bash(
        self, name: str, params: Dict[str, Any]
    ) -> Optional[CustomAgentMessage]:
        """bash 用户工具执行前发射 ``user_bash`` 事件让扩展拦截。

        对齐 pi ``handleBashCommand`` 语义（事件载荷同为
        command / exclude_from_context / cwd）：

        - 扩展返回完整 ``result``：扩展已接管执行，跳过真实执行，经
          工具声明的 ``build_result_message`` 把 result 翻译为本工具的
          消息形态返回（泛化层不认识具体消息类型，转换能力由工具自带）；
        - 扩展返回 ``operations``：注入 ``params["operations"]``，工具
          执行体经该注入点换用自定义执行后端（远程执行等），走原路径；
        - 非 bash 工具 / 无扩展 / handler 无返回：``None``，原路径不变。
        """
        if name != "bash":
            return None
        runner = self._session.extension_runner
        if runner is None or not runner.has_handlers(USER_BASH):
            return None

        event_result = await runner.emit_user_bash(
            UserBashEvent(
                command=str(params.get("command", "")),
                exclude_from_context=bool(params.get("exclude_from_context", False)),
                cwd=self._session.session_manager.get_cwd(),
            )
        )
        if event_result is None:
            return None

        if event_result.result is not None:
            definition = self._manager.get(name)
            builder = (
                definition.build_result_message if definition is not None else None
            )
            if builder is None:
                # 防御分支：工具未声明结果转换器，扩展的 result 无法翻译为
                # 消息——报出异常并按未拦截处理（正常不可达：官方 bash
                # 用户工具必带 message_from_result）
                runner.emit_error(
                    {
                        "event": USER_BASH,
                        "error": (
                            "扩展返回了 user_bash result，但 bash 用户工具未声明 "
                            "message_from_result 转换器，按未拦截处理"
                        ),
                    }
                )
                return None
            return builder(params, event_result.result)

        if event_result.operations is not None:
            # 自定义执行后端注入：bash 用户工具经 params["operations"] 接管
            params["operations"] = event_result.operations
        return None

    def record(self, message: CustomAgentMessage) -> None:
        """记录用户工具产出的消息：流式期间挂起，否则双写 + 事件定稿。

        事件发射（MessageStart/MessageEnd）与 ``send_custom_message``
        同款——没有它，前端流式卡片的"完结定稿"事件永远不到，
        卡片停留在初始空数据（命令串丢失——实证过的缺陷）。
        """
        if self._session.is_streaming:
            self._session._pending_session_messages.append(message)
        else:
            self._session.agent.state.messages.append(message)
            self._session.session_manager.append_message(message)
            self._emit_message_events(message)

    def _emit_message_events(self, message: CustomAgentMessage) -> None:
        """按会话消息生命周期发射 start/end（mirror 依此完结卡片）。"""
        from nova_harness.core.types.events.agent import (
            MessageEndEvent,
            MessageStartEvent,
        )

        self._session._emit(MessageStartEvent(message=message))
        self._session._emit(MessageEndEvent(message=message))

    def abort(self, name: Optional[str] = None) -> None:
        """取消正在运行的用户工具调用；name 为空则全部取消。"""
        for tool_name, controller in self._active.values():
            if name is None or tool_name == name:
                controller.abort()

    def flush_pending(self) -> None:
        """把运行期间累积的用户工具消息追加到会话（并逐条发射定稿事件）。"""
        pending: List[CustomAgentMessage] = self._session._pending_session_messages
        if not pending:
            return
        for message in pending:
            self._session.agent.state.messages.append(message)
            self._session.session_manager.append_message(message)
            self._emit_message_events(message)
        self._session._pending_session_messages = []


__all__ = ["UserToolController"]
