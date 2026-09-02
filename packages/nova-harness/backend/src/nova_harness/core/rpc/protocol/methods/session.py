"""Session 相关 JSON-RPC 方法。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Dict, List, Optional

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.harness.project_trust import make_resolve_project_trust_callback
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.jsonrpc import JsonRpcMessage
from nova_harness.core.rpc.protocol.methods.model import resolve_model
from nova_harness.core.rpc.protocol.methods.shapes import (
    CapabilitiesInfo,
    InitializeResult,
    ModelRef,
    SessionListItem,
    SessionStateResult,
    SyncSessionResult,
)
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry
from nova_harness.core.rpc.protocol.schema_export import (
    CONTRACT_VERSION_MAJOR,
    CONTRACT_VERSION_MINOR,
)
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions


def _find_session_path(session_id: str, cwd: Optional[str]) -> Optional[str]:
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


def _find_most_recent_session(cwd: Optional[str]) -> Optional[str]:
    from nova_harness.core.harness.session.utils import (
        find_most_recent_session,
        get_default_session_dir,
    )

    session_dir = get_default_session_dir(cwd or os.getcwd())
    return find_most_recent_session(session_dir)


def _resolve_session_file(session_file: str, cwd: Optional[str]) -> str:
    """把 createSession 的 ``sessionFile`` 解析为绝对路径。

    绝对路径直用；含路径分隔符的相对路径相对 cwd 解析；
    裸 id 在 cwd 的默认会话目录解析为 ``<id>.jsonl``（纯计算，不建目录）。
    """
    from nova_harness.core.harness.session.utils import get_default_session_dir_path

    expanded = os.path.expanduser(session_file)
    if os.path.isabs(expanded):
        return expanded
    if os.sep in expanded or (os.altsep and os.altsep in expanded):
        return os.path.abspath(os.path.join(cwd or os.getcwd(), expanded))
    name = expanded if expanded.endswith(".jsonl") else f"{expanded}.jsonl"
    return os.path.join(get_default_session_dir_path(cwd or os.getcwd()), name)


def _session_state_payload(session: Any) -> Dict[str, Any]:
    """会话状态快照载荷（getSessionState / syncSession 共用同一构造——
    契约与实现同源，无漂移）。"""
    model = session.model
    thinking = session.thinking_level
    return SessionStateResult(
        session_id=session.session_id,
        session_file=session.session_file,
        session_name=session.session_name,
        cwd=session.cwd,
        model=(ModelRef(provider=model.provider, id=model.id) if model else None),
        thinking_level=getattr(thinking, "value", thinking) or "off",
        supports_thinking=session.supports_thinking(),
        available_thinking_levels=[
            getattr(level, "value", level)
            for level in session.get_available_thinking_levels()
        ],
        active_tools=session.get_active_tool_names(),
        message_count=len(session.messages),
        pending_message_count=session.pending_message_count,
        steering_messages=session.get_steering_messages(),
        follow_up_messages=session.get_follow_up_messages(),
        is_streaming=session.is_streaming,
        is_compacting=session.is_compacting,
        is_retrying=session.is_retrying,
        auto_retry_enabled=session.auto_retry_enabled,
        auto_compaction_enabled=session.auto_compaction_enabled,
        steering_mode=session.steering_mode,
        follow_up_mode=session.follow_up_mode,
        project_trusted=(
            session.settings_manager.is_project_trusted()
            if session.settings_manager is not None
            else True
        ),
        leaf_id=session.session_manager.get_leaf_id(),
        allowed_commands=session.get_allowed_command_names(),
        disabled_commands=sorted(session.get_disabled_command_names()),
        capability_report=[
            s
            for s in (
                session.agent_manager.get_capability_report()
                if getattr(session, "agent_manager", None) is not None
                else []
            )
            if s.status != "ok"
        ],
        agent_name=(
            session.agent_manager.current
            if getattr(session, "agent_manager", None) is not None
            else None
        ),
        persona_override=(
            session.persona_manager.current_override
            if getattr(session, "persona_manager", None) is not None
            else None
        ),
    ).dump_wire()


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def initialize(params: Dict[str, Any]) -> Dict[str, Any]:
        """握手：服务器版本 + 契约版本（major/minor）+ 真实能力位（域/方法，来自注册表）。"""
        return InitializeResult(
            version="0.1.0",
            contractVersionMajor=CONTRACT_VERSION_MAJOR,
            contractVersionMinor=CONTRACT_VERSION_MINOR,
            capabilities=CapabilitiesInfo(
                domains=list(registry.domains().keys()),
                methods=registry.method_names(),
            ),
        ).dump_wire()

    async def createSession(params: Dict[str, Any]) -> Dict[str, Any]:
        session_flag = params.get("session_flag")
        continue_last = params.get("continue_last")
        session_file = params.get("session_file")
        no_session = bool(params.get("no_session"))

        # 临时会话（pi --no-session 对位）：与一切恢复来源互斥——
        # 临时语义与恢复语义矛盾；校验先于 runtime 重建，失败不毁现有会话
        if no_session and (
            session_flag is not None or continue_last or session_file is not None
        ):
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                "'noSession' is mutually exclusive with"
                " 'sessionFlag'/'continueLast'/'sessionFile'",
            )

        # 显式会话文件（pi --session <file|id> 启动恢复）：
        # 与 session_flag / continue_last 互斥——启动恢复只能有一个来源；
        # 文件必须存在且为合法会话文件（校验先于 runtime 重建，失败不毁现有会话）
        session_file_path: Optional[str] = None
        if session_file is not None:
            from nova_harness.core.harness.session.utils import is_valid_session_file

            if not session_file.strip():
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS, "'sessionFile' must not be blank"
                )
            if session_flag is not None or continue_last:
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS,
                    "'sessionFile' is mutually exclusive with"
                    " 'sessionFlag'/'continueLast'",
                )
            session_file_path = _resolve_session_file(session_file, params.get("cwd"))
            if not os.path.exists(session_file_path) or not is_valid_session_file(
                session_file_path
            ):
                raise JSONRPCError(
                    JSONRPCError.SESSION_NOT_FOUND,
                    f'Session file not found: "{session_file_path}"',
                )

        if state.runtime is not None:
            await state.dispose_runtime()

        model = None
        model_param = params.get("model")
        if model_param:
            model = resolve_model(model_param)

        # 临时会话：注入内存态 SessionManager（不落盘、不进会话列表，
        # 与 print 模式 --no-session 同一机制）
        session_manager = None
        if no_session:
            from nova_harness.core.harness.session import SessionManager

            session_manager = SessionManager.in_memory(params.get("cwd") or os.getcwd())

        opts = CreateAgentSessionOptions(
            cwd=params.get("cwd"),
            model=model,
            thinking_level=params.get("thinking_level"),
            agent_name=params.get("agent_name"),
            agent_dir=params.get("agent_dir"),
            session_manager=session_manager,
            ui_context=state.ui_context,
            # 信任决议回调（此前 RPC 未接线——启动永远默认不信任且不读
            # trust.json，"信任过下次还问" 的根因）：trust.json 记录 →
            # default_project_trust 设置 →（有 UI）启动信任框
            resolve_project_trust=make_resolve_project_trust_callback(
                cwd=params.get("cwd") or os.getcwd(),
                agent_dir=params.get("agent_dir") or str(get_agent_dir()),
                ui=state.ui_context,
                has_ui=True,
            ),
        )
        state.set_runtime(await create_agent_session_runtime(opts))

        resumed = False

        if session_file_path is not None:
            await state.runtime.switch_session(session_file_path)
            resumed = True
        elif session_flag is not None:
            if session_flag == "":
                pass  # interactive selection handled by frontend
            else:
                session_path = _find_session_path(session_flag, params.get("cwd"))
                if session_path:
                    await state.runtime.switch_session(session_path)
                    resumed = True
                else:
                    raise JSONRPCError(
                        JSONRPCError.SESSION_NOT_FOUND,
                        f'Session "{session_flag}" not found',
                    )
        elif continue_last:
            session_path = _find_most_recent_session(params.get("cwd"))
            if session_path:
                await state.runtime.switch_session(session_path)
                resumed = True

        return {
            "sessionId": state.runtime.session.session_id,
            "sessionName": state.runtime.session.session_name,
            "resumed": resumed,
        }

    async def listSessions(params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """列出会话（富 SessionInfo 透出，供前端会话选择器展示/搜索）。

        ``scope=current``（默认）按 cwd 的默认会话目录扫描；``scope=all``
        遍历全局 sessions 根下所有项目目录。listing 层自带并发限量
        （``MAX_CONCURRENT_SESSION_INFO_LOADS``），此处保持简单顺序组装。
        """
        from nova_harness.core.harness.session.listing import list_sessions_from_dir
        from nova_harness.core.harness.session.manager import SessionManager
        from nova_harness.core.harness.session.utils import (
            get_default_session_dir_path,
        )

        if params.get("scope") == "all":
            infos = await SessionManager.list_all_sessions()
        else:
            # 纯计算路径（只读扫描不创建目录），目录缺失时 listing 返回空
            session_dir = get_default_session_dir_path(params.get("cwd") or os.getcwd())
            infos = await list_sessions_from_dir(session_dir)
            infos.sort(key=lambda s: s.modified, reverse=True)

        items: List[Dict[str, Any]] = []
        for info in infos:
            # modified 沿用初版契约的 epoch 秒浮点；listing 层保证有值，0.0 兜底
            modified = info.modified.timestamp() if info.modified else 0.0
            items.append(
                SessionListItem(
                    id=info.id,
                    name=info.name or "",
                    path=info.path,
                    modified=modified,
                    message_count=info.message_count,
                    first_message=info.first_message,
                    cwd=info.cwd,
                    parent_session_path=info.parent_session_path,
                ).dump_wire()
            )
        return items

    async def deleteSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """删除会话文件（对齐 pi：trash CLI 优先、直接删除兜底；幂等）。

        守卫：当前活跃会话拒绝删除（前端需先切走再删）。
        无活跃会话时也可用（前端启动页的会话管理场景）。
        """
        path = os.path.abspath(os.path.expanduser(params["path"]))

        if state.runtime is not None:
            current = state.runtime.session.session_file
            if current and os.path.abspath(current) == path:
                raise JSONRPCError(
                    JSONRPCError.SESSION_IN_USE,
                    "Cannot delete the currently active session",
                )

        # 幂等：文件本就不存在视为成功
        if not os.path.exists(path):
            return {"deleted": True}

        # 可恢复优先：trash CLI（如 brew trash）存在即用；缺失/失败回退 os.remove
        trash = shutil.which("trash")
        if trash is not None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    trash,
                    path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if await proc.wait() == 0:
                    return {"deleted": True}
            except OSError:
                pass

        os.remove(path)
        return {"deleted": True}

    async def renameSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """重命名任意会话文件（追加一条 session_info 条目，最新一条生效）。

        空名字（trim 后）视为**显式清除**名字——对齐 ``append_session_info``
        现有语义（``get_session_name`` 遇空名返回 None，注释见 manager）。
        当前活跃会话走 live 通道（内存索引 + 事件广播保持一致），其余文件
        用独立 SessionManager 绑定追加，不触碰当前会话。
        """
        from nova_harness.core.harness.session.manager import SessionManager
        from nova_harness.core.harness.session.utils import is_valid_session_file

        path = os.path.abspath(os.path.expanduser(params["path"]))
        name = params["name"].strip()

        if not os.path.exists(path) or not is_valid_session_file(path):
            raise JSONRPCError(
                JSONRPCError.SESSION_NOT_FOUND,
                f'Session file not found: "{path}"',
            )

        if state.runtime is not None:
            current = state.runtime.session.session_file
            if current and os.path.abspath(current) == path:
                state.runtime.session.set_session_name(name)
                return {
                    "ok": True,
                    "sessionName": state.runtime.session.session_name,
                }

        manager = SessionManager.open(path)
        manager.append_session_info(name)
        return {"ok": True, "sessionName": manager.get_session_name()}

    async def prompt(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        text = params["text"]

        from nova_harness.core.agent_session.agent import PromptOptions

        options = PromptOptions(
            expand_prompt_templates=params.get("expand_prompt_templates", True),
            streaming_behavior=params.get("streaming_behavior"),
            source="rpc",
        )
        options.images = _parse_images(params.get("images"))

        await state.runtime.session.prompt(text, options=options)
        return {"ok": True}

    async def abort(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            return {"ok": False, "reason": "no session"}
        await state.runtime.session.abort()
        return {"ok": True}

    def _parse_images(raw: Any) -> Optional[list]:
        if not raw:
            return None
        from nova_ai import ImageContent

        return [ImageContent.model_validate(img) for img in raw]

    async def getSessionState(params: Dict[str, Any]) -> Dict[str, Any]:
        """完整状态快照（协议四件套之"快照"）。

        连接建立/恢复时前端镜像的全量来源；此后靠增量事件维持同步。
        形状即 ``shapes.SessionStateResult``（契约与实现同源，无漂移）。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return _session_state_payload(state.runtime.session)

    async def syncSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """原子同步快照（连接化 P2）：状态 + 条目页 + 事件高水位一帧拿齐。

        取代"getSessionState + getSessionEntries 两发"的旧路径——两发之间
        的增量事件可能既进快照又进事件流（重复应用）；高水位锚点
        （``event_seq``）让前端丢弃 ``seq <= eventSeq`` 的事件即精确对账。
        本 handler 同步段无 await——单循环上快照与 seq 读取天然原子。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        entries = session.session_manager.get_entries()
        offset = params.get("entries_offset", 0)
        limit = params.get("entries_limit", 0)
        page = entries[offset:] if limit <= 0 else entries[offset : offset + limit]
        return SyncSessionResult(
            state=_session_state_payload(session),
            entries=[
                entry.dump_wire() for entry in page if hasattr(entry, "model_dump")
            ],
            total=len(entries),
            entries_offset=offset,
            event_seq=state.event_seq,
        ).dump_wire()

    async def compact(params: Dict[str, Any]) -> Dict[str, Any]:
        """手动触发上下文压缩（长命令：进度经 compaction 事件流回报）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.compact(params.get("custom_instructions"))
        if hasattr(result, "model_dump"):
            return result.dump_wire()
        return {"ok": True}

    async def steer(params: Dict[str, Any]) -> Dict[str, Any]:
        """turn 进行中插入 steering 消息（当前 turn 结束后、下次 LLM 调用前送达）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.steer(
            params["text"], images=_parse_images(params.get("images"))
        )
        return {"ok": True}

    async def followUp(params: Dict[str, Any]) -> Dict[str, Any]:
        """排队 follow-up 消息（agent 完全空闲后处理）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.follow_up(
            params["text"], images=_parse_images(params.get("images"))
        )
        return {"ok": True}

    async def setSessionName(params: Dict[str, Any]) -> Dict[str, Any]:
        """重命名当前会话（持久化并广播 session_info_changed 事件）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        name = params["name"]
        if not name.strip():
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, "'name' must not be blank")
        state.runtime.session.set_session_name(name.strip())
        return {"ok": True, "sessionName": state.runtime.session.session_name}

    async def setSteeringMode(params: Dict[str, Any]) -> Dict[str, Any]:
        """设置 steering 模式（all / one-at-a-time），持久化到 settings。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        mode = params["mode"]
        state.runtime.session.set_steering_mode(mode)
        return {"ok": True, "steeringMode": mode}

    async def setFollowUpMode(params: Dict[str, Any]) -> Dict[str, Any]:
        """设置 follow-up 模式（all / one-at-a-time），持久化到 settings。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        mode = params["mode"]
        state.runtime.session.set_follow_up_mode(mode)
        return {"ok": True, "followUpMode": mode}

    async def clearQueue(params: Dict[str, Any]) -> Dict[str, Any]:
        """清空 steering 与 follow-up 队列，返回被清空的消息。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return state.runtime.session.clear_queue()

    async def setLabel(params: Dict[str, Any]) -> Dict[str, Any]:
        """给会话条目设置/清除标签（label 为 None 时清除）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_label(params["entry_id"], params.get("label"))
        return {"ok": True}

    async def abortRetry(params: Dict[str, Any]) -> Dict[str, Any]:
        """中止进行中的自动重试。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_retry()
        return {"ok": True}

    async def abortCompaction(params: Dict[str, Any]) -> Dict[str, Any]:
        """中止进行中的上下文压缩（域级 abort：只停压缩，不动 run/retry/用户工具）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_compaction()
        return {"ok": True}

    async def abortBranchSummary(params: Dict[str, Any]) -> Dict[str, Any]:
        """中止进行中的分支摘要（域级 abort：只停分支摘要，不动 run/压缩/重试）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_branch_summary()
        return {"ok": True}

    async def setAutoRetry(params: Dict[str, Any]) -> Dict[str, Any]:
        """开关自动重试（会话级，跟随 settings 的 retry.enabled 默认值）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        enabled = params["enabled"]
        state.runtime.session.set_auto_retry_enabled(enabled)
        return {"ok": True, "autoRetryEnabled": enabled}

    async def setAutoCompactionEnabled(params: Dict[str, Any]) -> Dict[str, Any]:
        """开关自动压缩（会话级，跟随 settings 的 compaction.enabled 默认值）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        enabled = params["enabled"]
        state.runtime.session.set_auto_compaction_enabled(enabled)
        return {"ok": True, "autoCompactionEnabled": enabled}

    async def reload(params: Dict[str, Any]) -> Dict[str, Any]:
        """热重载资源/扩展/settings（长命令：进度与诊断走事件流）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.reload()
        return {"ok": True}

    async def setActiveTools(params: Dict[str, Any]) -> Dict[str, Any]:
        """按名称设置激活工具（未知名称过滤；同步重建 system prompt 并广播）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        tool_names = params.get("tool_names")
        if tool_names is None:
            tool_names = params.get("tools") or []
        state.runtime.session.set_active_tools_by_name(list(tool_names))
        return {
            "ok": True,
            "activeTools": state.runtime.session.get_active_tool_names(),
        }

    async def navigateTree(params: Dict[str, Any]) -> Dict[str, Any]:
        """树导航：跳转到指定条目（可携带分支摘要选项）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return await state.runtime.session.navigate_tree(
            params["target_id"], params.get("options")
        )

    async def fork(params: Dict[str, Any]) -> Dict[str, Any]:
        """在指定条目处 fork 出新的分支会话。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return await state.runtime.session.fork_session(
            params["entry_id"], params.get("position", "before")
        )

    async def getSessionStats(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        stats = session.get_session_stats()
        tokens = getattr(stats, "tokens", None)
        # 缓存浪费分析（cache-stats 模块）：定价查询源取会话的 ModelRuntime
        cache_waste = session.get_cache_waste()
        return {
            "sessionId": getattr(stats, "session_id", ""),
            "sessionFile": getattr(stats, "session_file", None),
            "userMessages": getattr(stats, "user_messages", 0),
            "assistantMessages": getattr(stats, "assistant_messages", 0),
            "toolCalls": getattr(stats, "tool_calls", 0),
            "toolResults": getattr(stats, "tool_results", 0),
            "totalMessages": getattr(stats, "total_messages", 0),
            "tokens": (
                {
                    "inputTokens": getattr(tokens, "input_tokens", 0),
                    "outputTokens": getattr(tokens, "output_tokens", 0),
                    "cacheRead": getattr(tokens, "cache_read", 0),
                    "cacheWrite": getattr(tokens, "cache_write", 0),
                    "total": getattr(tokens, "total", 0),
                }
                if tokens
                else None
            ),
            "cost": getattr(stats, "cost", 0.0),
            "cache_waste": (
                cache_waste.dump_wire()
                if hasattr(cache_waste, "model_dump")
                else cache_waste
            ),
        }

    async def getContextUsage(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        usage = state.runtime.session.get_context_usage()
        return usage or {}

    async def getSessionEntries(params: Dict[str, Any]) -> Dict[str, Any]:
        """全量会话条目（全保真：id / parent_id / type / 原始 payload）。

        transcript 重建、树导航（navigateTree/fork 的 entryId）、标签
        （setLabel 的 target）与 compaction/分支摘要条目的统一数据源。
        哑管道原则：条目即运行时事实，原样 dump，不做呈现裁剪。

        分页（连接化 P2）：``offset``/``limit``（条目数，缺省全量）——
        大会话下避免单帧巨型 dump 冻结循环；``total`` 供翻页终止判定。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        entries = state.runtime.session.session_manager.get_entries()
        offset = params.get("offset", 0)
        limit = params.get("limit", 0)
        page = entries[offset:] if limit <= 0 else entries[offset : offset + limit]
        return {
            "entries": [
                entry.dump_wire() for entry in page if hasattr(entry, "model_dump")
            ],
            "total": len(entries),
            "offset": offset,
        }

    async def newSession(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.new_session()
        return {
            "sessionId": state.runtime.session.session_id,
            "sessionName": state.runtime.session.session_name,
        }

    async def switchSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """切换到既有会话文件（``path`` 绝对路径或 ``sessionId`` 解析）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session_path = params.get("path")
        if not session_path:
            session_id = params.get("session_id")
            if not session_id:
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS,
                    "Missing 'path' or 'sessionId' parameter",
                )
            session_path = _find_session_path(session_id, params.get("cwd"))
            if session_path is None:
                raise JSONRPCError(
                    JSONRPCError.SESSION_NOT_FOUND,
                    f'Session "{session_id}" not found',
                )
        result = await state.runtime.switch_session(session_path)
        if result.get("cancelled"):
            return {"ok": False, "cancelled": True}
        return {
            "ok": True,
            "sessionId": state.runtime.session.session_id,
            "sessionName": state.runtime.session.session_name,
        }

    async def cloneSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """克隆当前会话到新文件并切换过去。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.clone_session()
        if result.get("cancelled"):
            return {"ok": False, "cancelled": True}
        return {
            "ok": True,
            "sessionId": state.runtime.session.session_id,
            "sessionFile": state.runtime.session.session_file,
        }

    async def exportSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """把当前会话导出为 JSONL 文件（``path`` 必填）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return await state.runtime.session.export_session(params["path"])

    async def importSession(params: Dict[str, Any]) -> Dict[str, Any]:
        """从 JSONL 文件导入会话并切换过去（``path`` 必填）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.import_session(
            params["path"], cwd_override=params.get("cwd")
        )
        if result.get("cancelled"):
            return {"ok": False, "cancelled": True}
        return {
            "ok": True,
            "sessionId": state.runtime.session.session_id,
            "sessionName": state.runtime.session.session_name,
        }

    async def dispose(params: Dict[str, Any]) -> Dict[str, Any]:
        await state.dispose_runtime()
        return {"ok": True}

    async def shutdown(params: Dict[str, Any]) -> Dict[str, Any]:
        await state.dispose_runtime()
        return {"ok": True}

    async def listAgents(params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from nova_harness.core.sdk import list_installed_agents

        agents = list_installed_agents()
        return [{"name": name} for name in agents]

    async def changeAgent(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.change_agent(params["name"])
        return {
            "agentName": params["name"],
            "availableTools": [
                t.dump_wire() for t in state.runtime.session.get_available_tools_info()
            ],
        }

    async def saveAgent(params: Dict[str, Any]) -> Dict[str, Any]:
        """物化当前生效状态为组合声明 yaml（/agent save 的 RPC 面）。

        ``name`` 缺席 = 保存当前角色（包来源影子写 user 级，user/project
        来源就地写回）；提供 = save-as 新名（写 user 级）。写盘后 reload
        + 全量重建 runtime 生效（编排见 ``AgentSession.save_agent``）。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.save_agent(params.get("name"))
        return {
            "name": result["name"],
            "savedTo": result["path"],
            "shadowed": result["shadowed"],
        }

    async def getSessionAgents(params: Dict[str, Any]) -> Dict[str, Any]:
        """agents 注册表快照（含 current 标记）——/agent 选择器与前端 ctx 数据源。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return {
            "agents": state.runtime.session.agent_manager.agent_entries(),
        }

    async def getPersonas(params: Dict[str, Any]) -> Dict[str, Any]:
        """persona 注册表快照 + 当前 override——/persona 选择器与前端 ctx 数据源。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        personas = []
        override = (
            session.persona_manager.current_override
            if getattr(session, "persona_manager", None) is not None
            else None
        )
        for entry in session._get_persona_entries():
            personas.append({**entry, "is_override": entry["name"] == override})
        return {"personas": personas, "override": override}

    async def setPersonaOverride(params: Dict[str, Any]) -> Dict[str, Any]:
        """设置/清除 persona override（name 缺席或 null = 清除）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        name = params.get("name")
        if name:
            session._set_persona_override(name)
        else:
            session._clear_persona_override()
        override = (
            session.persona_manager.current_override
            if getattr(session, "persona_manager", None) is not None
            else None
        )
        return {"ok": True, "persona_override": override}

    async def appendEntry(params: Dict[str, Any]) -> Dict[str, Any]:
        """追加 custom 条目（B 型纯前端包经 invoke 也能产生条目——
        entry renderer 对全量包形态闭环）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        entry_id = state.runtime.session.append_entry(
            params["custom_type"], params.get("data")
        )
        return {"ok": True, "entry_id": entry_id}

    async def getTools(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            return {"tools": []}
        return {
            "tools": [
                t.dump_wire() for t in state.runtime.session.get_available_tools_info()
            ],
        }

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "session"
    registry.register(
        "initialize",
        initialize,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.InitializeResult,
    )
    registry.register(
        "createSession",
        createSession,
        domain=_D,
        params_model=_sh.CreateSessionParams,
        result_model=_sh.CreateSessionResult,
    )
    registry.register(
        "listSessions",
        listSessions,
        domain=_D,
        params_model=_sh.ListSessionsParams,
        result_model=_sh.ListSessionsResult,
    )
    registry.register(
        "deleteSession",
        deleteSession,
        domain=_D,
        params_model=_sh.DeleteSessionParams,
        result_model=_sh.DeleteSessionResult,
    )
    registry.register(
        "renameSession",
        renameSession,
        domain=_D,
        params_model=_sh.RenameSessionParams,
        result_model=_sh.RenameSessionResult,
    )
    registry.register(
        "prompt",
        prompt,
        domain=_D,
        params_model=_sh.PromptParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "abort",
        abort,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.AbortResult,
    )
    registry.register(
        "getSessionState",
        getSessionState,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.SessionStateResult,
    )
    registry.register(
        "syncSession",
        syncSession,
        domain=_D,
        params_model=_sh.SyncSessionParams,
        result_model=_sh.SyncSessionResult,
    )
    registry.register(
        "compact",
        compact,
        domain=_D,
        params_model=_sh.CompactParams,
        result_model=_sh.CompactResult,
    )
    registry.register(
        "steer",
        steer,
        domain=_D,
        params_model=_sh.SteerParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "followUp",
        followUp,
        domain=_D,
        params_model=_sh.FollowUpParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "setSessionName",
        setSessionName,
        domain=_D,
        params_model=_sh.SetSessionNameParams,
        result_model=_sh.SetSessionNameResult,
    )
    registry.register(
        "setSteeringMode",
        setSteeringMode,
        domain=_D,
        params_model=_sh.SetSteeringModeParams,
        result_model=_sh.SetSteeringModeResult,
    )
    registry.register(
        "setFollowUpMode",
        setFollowUpMode,
        domain=_D,
        params_model=_sh.SetFollowUpModeParams,
        result_model=_sh.SetFollowUpModeResult,
    )
    registry.register(
        "clearQueue",
        clearQueue,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.ClearQueueResult,
    )
    registry.register(
        "setLabel",
        setLabel,
        domain=_D,
        params_model=_sh.SetLabelParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "abortRetry",
        abortRetry,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "abortCompaction",
        abortCompaction,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "abortBranchSummary",
        abortBranchSummary,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "setAutoRetry",
        setAutoRetry,
        domain=_D,
        params_model=_sh.SetAutoRetryParams,
        result_model=_sh.SetAutoRetryResult,
    )
    registry.register(
        "setAutoCompactionEnabled",
        setAutoCompactionEnabled,
        domain=_D,
        params_model=_sh.SetAutoCompactionEnabledParams,
        result_model=_sh.SetAutoCompactionEnabledResult,
    )
    registry.register(
        "reload",
        reload,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "setActiveTools",
        setActiveTools,
        domain=_D,
        params_model=_sh.SetActiveToolsParams,
        result_model=_sh.SetActiveToolsResult,
    )
    registry.register(
        "navigateTree", navigateTree, domain=_D, params_model=_sh.NavigateTreeParams
    )
    registry.register("fork", fork, domain=_D, params_model=_sh.ForkParams)
    registry.register(
        "getSessionStats",
        getSessionStats,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.SessionStatsResult,
    )
    registry.register(
        "getContextUsage",
        getContextUsage,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetContextUsageResult,
    )
    registry.register(
        "getSessionEntries",
        getSessionEntries,
        domain=_D,
        params_model=_sh.GetSessionEntriesParams,
        result_model=_sh.GetSessionEntriesResult,
    )
    registry.register(
        "newSession",
        newSession,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.NewSessionResult,
    )
    registry.register(
        "switchSession",
        switchSession,
        domain=_D,
        params_model=_sh.SwitchSessionParams,
        result_model=_sh.SwitchSessionResult,
    )
    registry.register(
        "cloneSession",
        cloneSession,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.CloneSessionResult,
    )
    registry.register(
        "exportSession",
        exportSession,
        domain=_D,
        params_model=_sh.ExportSessionParams,
        result_model=_sh.ExportSessionResult,
    )
    registry.register(
        "importSession",
        importSession,
        domain=_D,
        params_model=_sh.ImportSessionParams,
        result_model=_sh.ImportSessionResult,
    )
    registry.register(
        "dispose",
        dispose,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "shutdown",
        shutdown,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "listAgents",
        listAgents,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.ListAgentsResult,
    )
    registry.register(
        "changeAgent",
        changeAgent,
        domain=_D,
        params_model=_sh.ChangeAgentParams,
        result_model=_sh.ChangeAgentResult,
    )
    registry.register(
        "saveAgent",
        saveAgent,
        domain=_D,
        params_model=_sh.SaveAgentParams,
        result_model=_sh.SaveAgentResult,
    )
    registry.register(
        "getSessionAgents",
        getSessionAgents,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetAgentsResult,
    )
    registry.register(
        "getPersonas",
        getPersonas,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetPersonasResult,
    )
    registry.register(
        "setPersonaOverride",
        setPersonaOverride,
        domain=_D,
        params_model=_sh.SetPersonaOverrideParams,
        result_model=_sh.SetPersonaOverrideResult,
    )
    registry.register(
        "appendEntry",
        appendEntry,
        domain=_D,
        params_model=_sh.AppendEntryParams,
        result_model=_sh.AppendEntryResult,
    )
    registry.register(
        "getTools",
        getTools,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetToolsResult,
    )
