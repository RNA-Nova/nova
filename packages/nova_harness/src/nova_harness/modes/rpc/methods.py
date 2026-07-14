"""JSON-RPC method implementations."""

import json
import os
from typing import Any, Dict, List, Optional

from nova_harness.core.config.defaults import (
    AUTH_FILE_NAME,
    MODELS_FILE_NAME,
    get_agent_dir,
)
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions
from nova_harness.modes.rpc.errors import JSONRPCError


class RpcMethods:
    """All JSON-RPC method handlers for the nova_harness AgentSession bridge."""

    def __init__(self, ui_context: Optional[Any] = None) -> None:
        self._runtime: Optional[Any] = None
        self.ui_context = ui_context

    @property
    def runtime(self) -> Optional[Any]:
        return self._runtime

    def set_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    def dispose_session(self) -> None:
        if self._runtime is not None:
            self._runtime.dispose()
            self._runtime = None

    # ------------------------------------------------------------------
    # initialize
    # ------------------------------------------------------------------
    async def initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "version": "0.1.0",
            "capabilities": {
                "streaming": True,
                "tools": True,
                "sessions": True,
            },
        }

    # ------------------------------------------------------------------
    # createSession
    # ------------------------------------------------------------------
    async def createSession(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is not None:
            await self.dispose({})

        model = None
        model_param = params.get("model")
        if model_param:
            model = self._resolve_model(model_param)

        opts = CreateAgentSessionOptions(
            cwd=params.get("cwd"),
            model=model,
            thinking_level=params.get("thinking_level"),
            agent_name=params.get("agentName"),
            ui_context=self.ui_context,
        )
        self._runtime = await create_agent_session_runtime(opts)

        session_flag = params.get("sessionFlag")
        continue_last = params.get("continueLast")
        resumed = False

        if session_flag is not None:
            if session_flag == "":
                pass  # interactive selection handled by Node UI
            else:
                session_path = self._find_session_path(session_flag, params.get("cwd"))
                if session_path:
                    await self._runtime.switch_session(session_path)
                    resumed = True
                else:
                    raise JSONRPCError(-32001, f'Session "{session_flag}" not found')
        elif continue_last:
            session_path = self._find_most_recent_session(params.get("cwd"))
            if session_path:
                await self._runtime.switch_session(session_path)
                resumed = True

        return {
            "session_id": self._runtime.session.session_id,
            "session_name": self._runtime.session.session_name,
            "resumed": resumed,
        }

    def _resolve_model(self, model_param: Any) -> Any:
        if isinstance(model_param, str):
            parts = model_param.split("/", 1)
            if len(parts) != 2:
                raise JSONRPCError(
                    -32602,
                    f"Invalid model format: {model_param}. Expected 'provider/model_id'.",
                )
            return self._find_model(parts[0], parts[1])
        elif isinstance(model_param, dict):
            from nova_ai import Model

            return Model.model_validate(model_param)
        else:
            raise JSONRPCError(
                -32602, f"Invalid model type: {type(model_param).__name__}"
            )

    def _find_model(self, provider: str, model_id: str) -> Any:
        from nova_harness.core.config import (
            AuthStorage,
            ModelRegistry,
        )

        agent_dir = get_agent_dir()
        auth_path = os.path.join(agent_dir, AUTH_FILE_NAME)
        models_path = os.path.join(agent_dir, MODELS_FILE_NAME)
        auth_storage = AuthStorage.create(auth_path)
        registry = ModelRegistry(auth_storage, models_path)
        model = registry.find(provider, model_id)
        if model is None:
            raise JSONRPCError(
                -32002, f'Model "{provider}/{model_id}" not found in registry'
            )
        return model

    def _find_session_path(self, session_id: str, cwd: Optional[str]) -> Optional[str]:
        from nova_harness.core.harness.session.utils import (
            get_default_session_dir,
            is_valid_session_file,
        )

        session_dir = get_default_session_dir(cwd or os.getcwd())
        if not os.path.exists(session_dir):
            return None
        for f in os.listdir(session_dir):
            if not f.endswith(".jsonl"):
                continue
            path = os.path.join(session_dir, f)
            if not is_valid_session_file(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    first_line = fp.readline().strip()
                    if not first_line:
                        continue
                    data = json.loads(first_line)
                    if data.get("id") == session_id:
                        return path
            except Exception:
                continue
        return None

    def _find_most_recent_session(self, cwd: Optional[str]) -> Optional[str]:
        from nova_harness.core.harness.session.utils import (
            find_most_recent_session,
            get_default_session_dir,
        )

        session_dir = get_default_session_dir(cwd or os.getcwd())
        return find_most_recent_session(session_dir)

    # ------------------------------------------------------------------
    # listSessions
    # ------------------------------------------------------------------
    async def listSessions(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from nova_harness.core.harness.session.utils import (
            get_default_session_dir,
            is_valid_session_file,
        )

        session_dir = get_default_session_dir(params.get("cwd", os.getcwd()))
        if not os.path.exists(session_dir):
            return []

        sessions: List[Dict[str, Any]] = []
        for f in sorted(os.listdir(session_dir)):
            if not f.endswith(".jsonl"):
                continue
            path = os.path.join(session_dir, f)
            if not is_valid_session_file(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    first_line = fp.readline().strip()
                    if not first_line:
                        continue
                    data = json.loads(first_line)
                    stat = os.stat(path)
                    sessions.append(
                        {
                            "id": data.get("id", ""),
                            "name": data.get("name", ""),
                            "path": path,
                            "modified": stat.st_mtime,
                        }
                    )
            except Exception:
                continue

        sessions.sort(key=lambda s: s["modified"], reverse=True)
        return sessions

    # ------------------------------------------------------------------
    # prompt
    # ------------------------------------------------------------------
    async def prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        text = params.get("text", "")
        if not text:
            raise JSONRPCError(-32602, "Missing 'text' parameter")

        from nova_harness.core.agent_session.agent import PromptOptions

        options = PromptOptions(
            expand_prompt_templates=params.get("expandPromptTemplates", True),
            streaming_behavior=params.get("streamingBehavior"),
            source="rpc",
        )
        images = params.get("images")
        if images:
            from nova_ai import ImageContent

            options.images = [ImageContent.model_validate(img) for img in images]

        await self._runtime.session.prompt(text, options=options)
        return {"ok": True}

    # ------------------------------------------------------------------
    # abort
    # ------------------------------------------------------------------
    async def abort(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            return {"ok": False, "reason": "no session"}
        await self._runtime.session.abort()
        return {"ok": True}

    # ------------------------------------------------------------------
    # setModel
    # ------------------------------------------------------------------
    async def setModel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        model = self._resolve_model(params.get("model"))
        ok = await self._runtime.session.set_model(model)
        return {"ok": ok}

    # ------------------------------------------------------------------
    # setThinkingLevel
    # ------------------------------------------------------------------
    async def setThinkingLevel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        level = params.get("level", "medium")
        from nova_ai import ThinkingLevel

        mapping = {
            "off": None,
            "minimal": ThinkingLevel.MINIMAL,
            "low": ThinkingLevel.LOW,
            "medium": ThinkingLevel.MEDIUM,
            "high": ThinkingLevel.HIGH,
            "xhigh": ThinkingLevel.XHIGH,
        }
        self._runtime.session.agent.set_thinking_level(
            mapping.get(level, ThinkingLevel.MEDIUM)
        )
        return {"ok": True}

    # ------------------------------------------------------------------
    # getSessionStats
    # ------------------------------------------------------------------
    async def getSessionStats(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        stats = self._runtime.session.get_session_stats()
        tokens = getattr(stats, "tokens", None)
        return {
            "session_id": getattr(stats, "session_id", ""),
            "session_file": getattr(stats, "session_file", None),
            "user_messages": getattr(stats, "user_messages", 0),
            "assistant_messages": getattr(stats, "assistant_messages", 0),
            "tool_calls": getattr(stats, "tool_calls", 0),
            "tool_results": getattr(stats, "tool_results", 0),
            "total_messages": getattr(stats, "total_messages", 0),
            "tokens": (
                {
                    "input_tokens": getattr(tokens, "input_tokens", 0),
                    "output_tokens": getattr(tokens, "output_tokens", 0),
                    "cache_read": getattr(tokens, "cache_read", 0),
                    "cache_write": getattr(tokens, "cache_write", 0),
                    "total": getattr(tokens, "total", 0),
                }
                if tokens
                else None
            ),
            "cost": getattr(stats, "cost", 0.0),
        }

    # ------------------------------------------------------------------
    # getContextUsage
    # ------------------------------------------------------------------
    async def getContextUsage(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        usage = self._runtime.session.get_context_usage()
        return usage or {}

    # ------------------------------------------------------------------
    # getSessionMessages
    # ------------------------------------------------------------------
    async def getSessionMessages(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        from nova_ai import AssistantMessage, ToolResultMessage, UserMessage

        limit = params.get("limit", 50)
        if not isinstance(limit, int) or limit <= 0:
            limit = 50

        raw_messages = self._runtime.session.messages
        start_index = max(0, len(raw_messages) - limit)
        slice_messages = raw_messages[start_index:]

        # Build a lookup of tool_call_id -> arguments so toolResult entries can show their args.
        tool_args_by_id: Dict[str, Dict[str, Any]] = {}
        for msg in raw_messages:
            if isinstance(msg, AssistantMessage):
                for c in msg.content or []:
                    if getattr(c, "type", None) == "toolCall" and getattr(
                        c, "id", None
                    ):
                        tool_args_by_id[c.id] = getattr(c, "arguments", {}) or {}

        messages: List[Dict[str, Any]] = []
        for msg in slice_messages:
            if isinstance(msg, UserMessage):
                messages.append(self._serialize_user_message(msg))
            elif isinstance(msg, AssistantMessage):
                messages.append(self._serialize_assistant_message(msg))
            elif isinstance(msg, ToolResultMessage):
                messages.append(
                    self._serialize_tool_result_message(msg, tool_args_by_id)
                )

        return {"messages": messages}

    def _serialize_user_message(self, msg: Any) -> Dict[str, Any]:
        return {
            "role": "user",
            "content": msg.model_dump().get("content", ""),
            "timestamp": msg.timestamp,
        }

    def _serialize_assistant_message(self, msg: Any) -> Dict[str, Any]:
        data = msg.model_dump()
        content = data.get("content", []) or []
        # Keep only text/thinking for transcript rendering; tool calls have matching toolResult entries
        filtered = [
            c
            for c in content
            if isinstance(c, dict) and c.get("type") in ("text", "thinking")
        ]
        usage = data.get("usage") or {}
        return {
            "role": "assistant",
            "content": filtered,
            "model": data.get("model", ""),
            "usage": {
                "input": usage.get("input", 0),
                "output": usage.get("output", 0),
                "cache_read": usage.get("cache_read", 0),
                "cache_write": usage.get("cache_write", 0),
            },
            "timestamp": data.get("timestamp", 0),
        }

    def _serialize_tool_result_message(
        self, msg: Any, tool_args_by_id: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        data = msg.model_dump()
        tool_call_id = data.get("tool_call_id", "")
        return {
            "role": "toolResult",
            "tool_call_id": tool_call_id,
            "tool_name": data.get("tool_name", ""),
            "tool_args": tool_args_by_id.get(tool_call_id, {}),
            "content": data.get("content", []),
            "is_error": data.get("is_error", False),
            "timestamp": data.get("timestamp", 0),
        }

    # ------------------------------------------------------------------
    # newSession
    # ------------------------------------------------------------------
    async def newSession(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        await self._runtime.new_session()
        return {
            "session_id": self._runtime.session.session_id,
            "session_name": self._runtime.session.session_name,
        }

    # ------------------------------------------------------------------
    # dispose
    # ------------------------------------------------------------------
    async def dispose(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.dispose_session()
        return {"ok": True}

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    async def shutdown(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.dispose_session()
        return {"ok": True}

    # ------------------------------------------------------------------
    # listAgents
    # ------------------------------------------------------------------
    async def listAgents(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from nova_harness.core.sdk import list_installed_agents

        agents = list_installed_agents()
        return [{"name": name} for name in agents]

    # ------------------------------------------------------------------
    # changeAgent
    # ------------------------------------------------------------------
    async def changeAgent(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._runtime is None:
            raise JSONRPCError(-32000, "No active session")
        name = params.get("name")
        if not name:
            raise JSONRPCError(-32602, "Missing 'name' parameter")
        self._runtime.session.change_agent(name)
        return {
            "agent_name": name,
            "available_tools": self._runtime.session.get_active_tool_names(),
        }

    # ==================================================================
    # Package manager
    # ==================================================================
    def _package_manager(self, local: bool = False) -> "PackageManager":
        """构造与当前 session 信任状态一致的 PackageManager。

        若存在活跃 session，复用其 settings_manager 与 project_trusted 状态；
        否则退化为默认 PackageManager（与无 RPC session 时行为一致）。
        """
        from nova_harness.core.package import PackageManager

        if self._runtime is not None and self._runtime.session is not None:
            session = self._runtime.session
            project_trusted = (
                session.settings_manager.is_project_trusted()
                if session.settings_manager is not None
                else None
            )
            return PackageManager(
                cwd=session.cwd,
                settings_manager=session.settings_manager,
                project_trusted=project_trusted,
            )
        return PackageManager()

    async def pkgList(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pm = self._package_manager(local=params.get("local", False))
        views = pm.list_with_resources(local=params.get("local", False))
        return {k: v.model_dump() for k, v in views.items()}

    async def pkgInstall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pm = self._package_manager(local=params.get("local", False))
        meta = pm.install_and_persist(
            params["source"], local=params.get("local", False)
        )
        return meta.model_dump()

    async def pkgUninstall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pm = self._package_manager(local=params.get("local", False))
        result = pm.uninstall(
            params["name_or_source"], local=params.get("local", False)
        )
        return {"ok": result.removed, "messages": result.messages}

    async def pkgInfo(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pm = self._package_manager(local=params.get("local", False))
        meta = pm.info(params["name_or_source"], local=params.get("local", False))
        return meta.model_dump() if meta else None

    async def pkgUpdate(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        pm = self._package_manager(local=params.get("local", False))
        metas = await pm.update(
            params["name_or_source"], local=params.get("local", False)
        )
        return [m.model_dump() for m in metas]
