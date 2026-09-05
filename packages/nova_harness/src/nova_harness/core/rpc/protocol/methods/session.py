"""Session 相关 JSON-RPC 方法。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Dict, Optional

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.harness.project_trust import make_resolve_project_trust_callback
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.model import resolve_model
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry
from nova_harness.core.rpc.protocol.schema_export import (
    CONTRACT_VERSION_MAJOR,
    CONTRACT_VERSION_MINOR,
)
from nova_harness.core.sdk import create_agent_session_runtime
from nova_harness.core.types.session.config import CreateAgentSessionOptions
from nova_harness.core.utils.version import harness_version

_D = "session"


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


def _session_state(session: Any) -> _sh.SessionStateResult:
    """会话状态快照（getSessionState / syncSession 共用同一构造——
    契约与实现同源，无漂移）。"""
    model = session.model
    thinking = session.thinking_level
    return _sh.SessionStateResult(
        session_id=session.session_id,
        session_file=session.session_file,
        session_name=session.session_name,
        cwd=session.cwd,
        model=(_sh.ModelRef(provider=model.provider, id=model.id) if model else None),
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
    )


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def initialize(params: _sh.EmptyParams) -> _sh.InitializeResult:
        """握手：服务器版本 + 契约版本（major/minor）+ 真实能力位（域/方法，来自注册表）。"""
        return _sh.InitializeResult(
            version=harness_version(),
            contract_version_major=CONTRACT_VERSION_MAJOR,
            contract_version_minor=CONTRACT_VERSION_MINOR,
            capabilities=_sh.CapabilitiesInfo(
                domains=list(registry.domains().keys()),
                methods=registry.method_names(),
            ),
        )

    async def createSession(params: _sh.CreateSessionParams) -> _sh.CreateSessionResult:
        # 临时会话（pi --no-session 对位）：与一切恢复来源互斥——
        # 临时语义与恢复语义矛盾；校验先于 runtime 重建，失败不毁现有会话
        if params.no_session and (
            params.session_flag is not None
            or params.continue_last
            or params.session_file is not None
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
        if params.session_file is not None:
            from nova_harness.core.harness.session.utils import is_valid_session_file

            if not params.session_file.strip():
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS, "'sessionFile' must not be blank"
                )
            if params.session_flag is not None or params.continue_last:
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS,
                    "'sessionFile' is mutually exclusive with"
                    " 'sessionFlag'/'continueLast'",
                )
            session_file_path = _resolve_session_file(params.session_file, params.cwd)
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
        if params.model:
            model = resolve_model(params.model)

        # 临时会话：注入内存态 SessionManager（不落盘、不进会话列表，
        # 与 print 模式 --no-session 同一机制）
        session_manager = None
        if params.no_session:
            from nova_harness.core.harness.session import SessionManager

            session_manager = SessionManager.in_memory(params.cwd or os.getcwd())

        opts = CreateAgentSessionOptions(
            cwd=params.cwd,
            model=model,
            thinking_level=params.thinking_level,
            agent_name=params.agent_name,
            agent_dir=params.agent_dir,
            session_manager=session_manager,
            extension_flag_values=params.extension_flags,
            ui_context=state.ui_context,
            # 信任决议回调（此前 RPC 未接线——启动永远默认不信任且不读
            # trust.json，"信任过下次还问" 的根因）：trust.json 记录 →
            # default_project_trust 设置 →（有 UI）启动信任框
            resolve_project_trust=make_resolve_project_trust_callback(
                cwd=params.cwd or os.getcwd(),
                agent_dir=params.agent_dir or str(get_agent_dir()),
                ui=state.ui_context,
                has_ui=True,
            ),
        )
        state.set_runtime(await create_agent_session_runtime(opts))

        resumed = False

        if session_file_path is not None:
            await state.runtime.switch_session(session_file_path)
            resumed = True
        elif params.session_flag is not None:
            if params.session_flag == "":
                pass  # interactive selection handled by frontend
            else:
                session_path = _find_session_path(params.session_flag, params.cwd)
                if session_path:
                    await state.runtime.switch_session(session_path)
                    resumed = True
                else:
                    raise JSONRPCError(
                        JSONRPCError.SESSION_NOT_FOUND,
                        f'Session "{params.session_flag}" not found',
                    )
        elif params.continue_last:
            session_path = _find_most_recent_session(params.cwd)
            if session_path:
                await state.runtime.switch_session(session_path)
                resumed = True

        return _sh.CreateSessionResult(
            session_id=state.runtime.session.session_id,
            session_name=state.runtime.session.session_name,
            resumed=resumed,
        )

    async def listSessions(params: _sh.ListSessionsParams) -> _sh.ListSessionsResult:
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

        if params.scope == "all":
            infos = await SessionManager.list_all_sessions()
        else:
            # 纯计算路径（只读扫描不创建目录），目录缺失时 listing 返回空
            session_dir = get_default_session_dir_path(params.cwd or os.getcwd())
            infos = await list_sessions_from_dir(session_dir)
            infos.sort(key=lambda s: s.modified, reverse=True)

        items = []
        for info in infos:
            # modified 沿用初版契约的 epoch 秒浮点；listing 层保证有值，0.0 兜底
            modified = info.modified.timestamp() if info.modified else 0.0
            items.append(
                _sh.SessionListItem(
                    id=info.id,
                    name=info.name or "",
                    path=info.path,
                    modified=modified,
                    message_count=info.message_count,
                    first_message=info.first_message,
                    cwd=info.cwd,
                    parent_session_path=info.parent_session_path,
                )
            )
        return _sh.ListSessionsResult(root=items)

    async def deleteSession(params: _sh.DeleteSessionParams) -> _sh.DeleteSessionResult:
        """删除会话文件（对齐 pi：trash CLI 优先、直接删除兜底；幂等）。

        守卫：当前活跃会话拒绝删除（前端需先切走再删）。
        无活跃会话时也可用（前端启动页的会话管理场景）。
        """
        path = os.path.abspath(os.path.expanduser(params.path))

        if state.runtime is not None:
            current = state.runtime.session.session_file
            if current and os.path.abspath(current) == path:
                raise JSONRPCError(
                    JSONRPCError.SESSION_IN_USE,
                    "Cannot delete the currently active session",
                )

        # 幂等：文件本就不存在视为成功
        if not os.path.exists(path):
            return _sh.DeleteSessionResult(deleted=True)

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
                    return _sh.DeleteSessionResult(deleted=True)
            except OSError:
                pass

        os.remove(path)
        return _sh.DeleteSessionResult(deleted=True)

    async def renameSession(params: _sh.RenameSessionParams) -> _sh.RenameSessionResult:
        """重命名任意会话文件（追加一条 session_info 条目，最新一条生效）。

        空名字（trim 后）视为**显式清除**名字——对齐 ``append_session_info``
        现有语义（``get_session_name`` 遇空名返回 None，注释见 manager）。
        当前活跃会话走 live 通道（内存索引 + 事件广播保持一致），其余文件
        用独立 SessionManager 绑定追加，不触碰当前会话。
        """
        from nova_harness.core.harness.session.manager import SessionManager
        from nova_harness.core.harness.session.utils import is_valid_session_file

        path = os.path.abspath(os.path.expanduser(params.path))
        name = params.name.strip()

        if not os.path.exists(path) or not is_valid_session_file(path):
            raise JSONRPCError(
                JSONRPCError.SESSION_NOT_FOUND,
                f'Session file not found: "{path}"',
            )

        if state.runtime is not None:
            current = state.runtime.session.session_file
            if current and os.path.abspath(current) == path:
                state.runtime.session.set_session_name(name)
                return _sh.RenameSessionResult(
                    ok=True, session_name=state.runtime.session.session_name
                )

        manager = SessionManager.open(path)
        manager.append_session_info(name)
        return _sh.RenameSessionResult(ok=True, session_name=manager.get_session_name())

    async def prompt(params: _sh.PromptParams) -> _sh.OkResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")

        from nova_harness.core.agent_session.agent import PromptOptions

        options = PromptOptions(
            expand_prompt_templates=params.expand_prompt_templates,
            streaming_behavior=params.streaming_behavior,
            source="rpc",
        )
        # images 已由分派层物化为 ImageContent 实例，直传（不再二次校验）
        options.images = params.images

        await state.runtime.session.prompt(params.text, options=options)
        return _sh.OkResult(ok=True)

    async def abort(params: _sh.EmptyParams) -> _sh.AbortResult:
        if state.runtime is None:
            return _sh.AbortResult(ok=False, reason="no session")
        await state.runtime.session.abort()
        return _sh.AbortResult(ok=True)

    async def getSessionState(params: _sh.EmptyParams) -> _sh.SessionStateResult:
        """完整状态快照（协议四件套之"快照"）。

        连接建立/恢复时前端镜像的全量来源；此后靠增量事件维持同步。
        形状即 ``shapes.SessionStateResult``（契约与实现同源，无漂移）。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return _session_state(state.runtime.session)

    async def syncSession(params: _sh.SyncSessionParams) -> _sh.SyncSessionResult:
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
        offset = params.entries_offset
        limit = params.entries_limit
        page = entries[offset:] if limit <= 0 else entries[offset : offset + limit]
        return _sh.SyncSessionResult(
            # state/entries 为自由负载字段（Dict/List[Dict]），嵌套字典取
            # 各自模型的线上形态（camel）——与事件流同一出货口径
            state=_session_state(session).dump_wire(),
            entries=[
                entry.dump_wire() for entry in page if hasattr(entry, "model_dump")
            ],
            total=len(entries),
            entries_offset=offset,
            event_seq=state.event_seq,
        )

    async def compact(params: _sh.CompactParams) -> _sh.CompactResult:
        """手动触发上下文压缩（长命令：进度经 compaction 事件流回报）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.compact(params.custom_instructions)
        return _sh.CompactResult(
            summary=result.summary,
            first_kept_entry_id=result.first_kept_entry_id,
            tokens_before=result.tokens_before,
            estimated_tokens_after=result.estimated_tokens_after,
            details=result.details,
        )

    async def steer(params: _sh.SteerParams) -> _sh.OkResult:
        """turn 进行中插入 steering 消息（当前 turn 结束后、下次 LLM 调用前送达）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.steer(params.text, images=params.images)
        return _sh.OkResult(ok=True)

    async def followUp(params: _sh.FollowUpParams) -> _sh.OkResult:
        """排队 follow-up 消息（agent 完全空闲后处理）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.follow_up(params.text, images=params.images)
        return _sh.OkResult(ok=True)

    async def setSessionName(
        params: _sh.SetSessionNameParams,
    ) -> _sh.SetSessionNameResult:
        """重命名当前会话（持久化并广播 session_info_changed 事件）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        if not params.name.strip():
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, "'name' must not be blank")
        state.runtime.session.set_session_name(params.name.strip())
        return _sh.SetSessionNameResult(
            ok=True, session_name=state.runtime.session.session_name
        )

    async def setSteeringMode(
        params: _sh.SetSteeringModeParams,
    ) -> _sh.SetSteeringModeResult:
        """设置 steering 模式（all / one-at-a-time），持久化到 settings。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_steering_mode(params.mode)
        return _sh.SetSteeringModeResult(ok=True, steering_mode=params.mode)

    async def setFollowUpMode(
        params: _sh.SetFollowUpModeParams,
    ) -> _sh.SetFollowUpModeResult:
        """设置 follow-up 模式（all / one-at-a-time），持久化到 settings。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_follow_up_mode(params.mode)
        return _sh.SetFollowUpModeResult(ok=True, follow_up_mode=params.mode)

    async def clearQueue(params: _sh.EmptyParams) -> _sh.ClearQueueResult:
        """清空 steering 与 follow-up 队列，返回被清空的消息。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        cleared = state.runtime.session.clear_queue()
        return _sh.ClearQueueResult(
            steering=cleared.get("steering", []),
            follow_up=cleared.get("follow_up", []),
        )

    async def setLabel(params: _sh.SetLabelParams) -> _sh.OkResult:
        """给会话条目设置/清除标签（label 为 None 时清除）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_label(params.entry_id, params.label)
        return _sh.OkResult(ok=True)

    async def abortRetry(params: _sh.EmptyParams) -> _sh.OkResult:
        """中止进行中的自动重试。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_retry()
        return _sh.OkResult(ok=True)

    async def abortCompaction(params: _sh.EmptyParams) -> _sh.OkResult:
        """中止进行中的上下文压缩（域级 abort：只停压缩，不动 run/retry/用户工具）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_compaction()
        return _sh.OkResult(ok=True)

    async def abortBranchSummary(params: _sh.EmptyParams) -> _sh.OkResult:
        """中止进行中的分支摘要（域级 abort：只停分支摘要，不动 run/压缩/重试）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_branch_summary()
        return _sh.OkResult(ok=True)

    async def setAutoRetry(params: _sh.SetAutoRetryParams) -> _sh.SetAutoRetryResult:
        """开关自动重试（会话级，跟随 settings 的 retry.enabled 默认值）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_auto_retry_enabled(params.enabled)
        return _sh.SetAutoRetryResult(ok=True, auto_retry_enabled=params.enabled)

    async def setAutoCompactionEnabled(
        params: _sh.SetAutoCompactionEnabledParams,
    ) -> _sh.SetAutoCompactionEnabledResult:
        """开关自动压缩（会话级，跟随 settings 的 compaction.enabled 默认值）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.set_auto_compaction_enabled(params.enabled)
        return _sh.SetAutoCompactionEnabledResult(
            ok=True, auto_compaction_enabled=params.enabled
        )

    async def reload(params: _sh.EmptyParams) -> _sh.OkResult:
        """热重载资源/扩展/settings（长命令：进度与诊断走事件流）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.reload()
        return _sh.OkResult(ok=True)

    async def setActiveTools(
        params: _sh.SetActiveToolsParams,
    ) -> _sh.SetActiveToolsResult:
        """按名称设置激活工具（未知名称过滤；同步重建 system prompt 并广播）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        tool_names = params.tool_names
        if tool_names is None:
            tool_names = params.tools or []
        state.runtime.session.set_active_tools_by_name(list(tool_names))
        return _sh.SetActiveToolsResult(
            ok=True,
            active_tools=state.runtime.session.get_active_tool_names(),
        )

    async def navigateTree(params: _sh.NavigateTreeParams) -> Dict[str, Any]:
        """树导航：跳转到指定条目（可携带分支摘要选项）。

        自由形状方法——本 handler 即过线点：会话层内部载荷为 snake
        （取消路径为单词键原样透传），成功路径在此翻译为线上 camel；
        ``summaryEntry`` 的条目实例由传输层 to_json_safe 单点 dump_wire。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.navigate_tree(
            params.target_id, params.options
        )
        if result.get("cancelled"):
            return result
        return {
            "editorText": result.get("editor_text"),
            "cancelled": False,
            "summaryEntry": result.get("summary_entry"),
        }

    async def fork(params: _sh.ForkParams) -> Dict[str, Any]:
        """在指定条目处 fork 出新的分支会话。

        自由形状方法——过线翻译同 navigateTree（``selectedText``/
        ``editorText`` 回填编辑器，pi fork 语义）。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.fork_session(
            params.entry_id, params.position
        )
        if result.get("cancelled"):
            return result
        return {
            "cancelled": False,
            "selectedText": result.get("selected_text"),
            "editorText": result.get("editor_text"),
        }

    async def getSessionStats(params: _sh.EmptyParams) -> _sh.SessionStatsResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        stats = session.get_session_stats()
        tokens = getattr(stats, "tokens", None)
        # 缓存浪费分析（cache-stats 模块）：定价查询源取会话的 ModelRuntime
        cache_waste = session.get_cache_waste()
        return _sh.SessionStatsResult(
            session_id=getattr(stats, "session_id", ""),
            session_file=getattr(stats, "session_file", None),
            user_messages=getattr(stats, "user_messages", 0),
            assistant_messages=getattr(stats, "assistant_messages", 0),
            tool_calls=getattr(stats, "tool_calls", 0),
            tool_results=getattr(stats, "tool_results", 0),
            total_messages=getattr(stats, "total_messages", 0),
            tokens=(
                _sh.TokenUsageSummary(
                    input_tokens=getattr(tokens, "input_tokens", 0),
                    output_tokens=getattr(tokens, "output_tokens", 0),
                    cache_read=getattr(tokens, "cache_read", 0),
                    cache_write=getattr(tokens, "cache_write", 0),
                    total=getattr(tokens, "total", 0),
                )
                if tokens
                else None
            ),
            cost=getattr(stats, "cost", 0.0),
            cache_waste=(
                cache_waste.dump_wire()
                if hasattr(cache_waste, "model_dump")
                else cache_waste
            ),
        )

    async def getContextUsage(params: _sh.EmptyParams) -> _sh.GetContextUsageResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        usage = state.runtime.session.get_context_usage()
        if not usage:
            return _sh.GetContextUsageResult()
        # 服务层载荷沿用线上 camel 键（RPC 透出形状），此处翻译回契约模型
        return _sh.GetContextUsageResult(
            tokens=usage.get("tokens"),
            context_window=usage.get("contextWindow"),
            percent=usage.get("percent"),
        )

    async def getSessionEntries(
        params: _sh.GetSessionEntriesParams,
    ) -> _sh.GetSessionEntriesResult:
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
        offset = params.offset
        limit = params.limit
        page = entries[offset:] if limit <= 0 else entries[offset : offset + limit]
        return _sh.GetSessionEntriesResult(
            entries=[
                entry.dump_wire() for entry in page if hasattr(entry, "model_dump")
            ],
            total=len(entries),
            offset=offset,
        )

    async def newSession(params: _sh.EmptyParams) -> _sh.NewSessionResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.new_session()
        return _sh.NewSessionResult(
            session_id=state.runtime.session.session_id,
            session_name=state.runtime.session.session_name,
        )

    async def switchSession(params: _sh.SwitchSessionParams) -> _sh.SwitchSessionResult:
        """切换到既有会话文件（``path`` 绝对路径或 ``sessionId`` 解析）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session_path = params.path
        if not session_path:
            if not params.session_id:
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS,
                    "Missing 'path' or 'sessionId' parameter",
                )
            session_path = _find_session_path(params.session_id, params.cwd)
            if session_path is None:
                raise JSONRPCError(
                    JSONRPCError.SESSION_NOT_FOUND,
                    f'Session "{params.session_id}" not found',
                )
        result = await state.runtime.switch_session(session_path)
        if result.get("cancelled"):
            return _sh.SwitchSessionResult(ok=False, cancelled=True)
        return _sh.SwitchSessionResult(
            ok=True,
            session_id=state.runtime.session.session_id,
            session_name=state.runtime.session.session_name,
        )

    async def cloneSession(params: _sh.EmptyParams) -> _sh.CloneSessionResult:
        """克隆当前会话到新文件并切换过去。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.clone_session()
        if result.get("cancelled"):
            return _sh.CloneSessionResult(ok=False, cancelled=True)
        return _sh.CloneSessionResult(
            ok=True,
            session_id=state.runtime.session.session_id,
            session_file=state.runtime.session.session_file,
        )

    async def exportSession(params: _sh.ExportSessionParams) -> _sh.ExportSessionResult:
        """把当前会话导出为 JSONL 文件（``path`` 必填）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.export_session(params.path)
        return _sh.ExportSessionResult(exported_to=result["exported_to"])

    async def importSession(params: _sh.ImportSessionParams) -> _sh.ImportSessionResult:
        """从 JSONL 文件导入会话并切换过去（``path`` 必填）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.import_session(
            params.path, cwd_override=params.cwd
        )
        if result.get("cancelled"):
            return _sh.ImportSessionResult(ok=False, cancelled=True)
        return _sh.ImportSessionResult(
            ok=True,
            session_id=state.runtime.session.session_id,
            session_name=state.runtime.session.session_name,
        )

    async def dispose(params: _sh.EmptyParams) -> _sh.OkResult:
        await state.dispose_runtime()
        return _sh.OkResult(ok=True)

    async def shutdown(params: _sh.EmptyParams) -> _sh.OkResult:
        await state.dispose_runtime()
        return _sh.OkResult(ok=True)

    async def listAgents(params: _sh.EmptyParams) -> _sh.ListAgentsResult:
        from nova_harness.core.sdk import list_installed_agents

        agents = list_installed_agents()
        return _sh.ListAgentsResult(
            root=[_sh.AgentListItem(name=name) for name in agents]
        )

    async def changeAgent(params: _sh.ChangeAgentParams) -> _sh.ChangeAgentResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        await state.runtime.session.change_agent(params.name)
        return _sh.ChangeAgentResult(
            agent_name=params.name,
            available_tools=state.runtime.session.get_available_tools_info(),
        )

    async def saveAgent(params: _sh.SaveAgentParams) -> _sh.SaveAgentResult:
        """物化当前生效状态为组合声明 yaml（/agent save 的 RPC 面）。

        ``name`` 缺席 = 保存当前角色（包来源影子写 user 级，user/project
        来源就地写回）；提供 = save-as 新名（写 user 级）。写盘后 reload
        + 全量重建 runtime 生效（编排见 ``AgentSession.save_agent``）。
        """
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        result = await state.runtime.session.save_agent(params.name)
        return _sh.SaveAgentResult(
            name=result["name"],
            saved_to=result["path"],
            shadowed=result["shadowed"],
        )

    async def getSessionAgents(params: _sh.EmptyParams) -> _sh.GetAgentsResult:
        """agents 注册表快照（含 current 标记）——/agent 选择器与前端 ctx 数据源。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return _sh.GetAgentsResult(
            agents=[
                _sh.AgentEntry(**entry)
                for entry in state.runtime.session.agent_manager.agent_entries()
            ],
        )

    async def getPersonas(params: _sh.EmptyParams) -> _sh.GetPersonasResult:
        """persona 注册表快照 + 当前 override——/persona 选择器与前端 ctx 数据源。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        override = (
            session.persona_manager.current_override
            if getattr(session, "persona_manager", None) is not None
            else None
        )
        return _sh.GetPersonasResult(
            personas=[
                _sh.PersonaEntry(
                    name=entry["name"],
                    path=entry["path"],
                    scope=entry["scope"],
                    origin=entry["origin"],
                    is_override=entry["name"] == override,
                )
                for entry in session._get_persona_entries()
            ],
            override=override,
        )

    async def setPersonaOverride(
        params: _sh.SetPersonaOverrideParams,
    ) -> _sh.SetPersonaOverrideResult:
        """设置/清除 persona override（name 缺席或 null = 清除）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        session = state.runtime.session
        if params.name:
            session._set_persona_override(params.name)
        else:
            session._clear_persona_override()
        override = (
            session.persona_manager.current_override
            if getattr(session, "persona_manager", None) is not None
            else None
        )
        return _sh.SetPersonaOverrideResult(ok=True, persona_override=override)

    async def appendEntry(params: _sh.AppendEntryParams) -> _sh.AppendEntryResult:
        """追加 custom 条目（B 型纯前端包经 invoke 也能产生条目——
        entry renderer 对全量包形态闭环）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        entry_id = state.runtime.session.append_entry(params.custom_type, params.data)
        return _sh.AppendEntryResult(ok=True, entry_id=entry_id)

    async def getTools(params: _sh.EmptyParams) -> _sh.GetToolsResult:
        if state.runtime is None:
            return _sh.GetToolsResult(tools=[])
        return _sh.GetToolsResult(
            tools=state.runtime.session.get_available_tools_info(),
        )

    registry.register("initialize", initialize, domain=_D)
    registry.register("createSession", createSession, domain=_D)
    registry.register("listSessions", listSessions, domain=_D)
    registry.register("deleteSession", deleteSession, domain=_D)
    registry.register("renameSession", renameSession, domain=_D)
    registry.register("prompt", prompt, domain=_D)
    registry.register("abort", abort, domain=_D)
    registry.register("getSessionState", getSessionState, domain=_D)
    registry.register("syncSession", syncSession, domain=_D)
    registry.register("compact", compact, domain=_D)
    registry.register("steer", steer, domain=_D)
    registry.register("followUp", followUp, domain=_D)
    registry.register("setSessionName", setSessionName, domain=_D)
    registry.register("setSteeringMode", setSteeringMode, domain=_D)
    registry.register("setFollowUpMode", setFollowUpMode, domain=_D)
    registry.register("clearQueue", clearQueue, domain=_D)
    registry.register("setLabel", setLabel, domain=_D)
    registry.register("abortRetry", abortRetry, domain=_D)
    registry.register("abortCompaction", abortCompaction, domain=_D)
    registry.register("abortBranchSummary", abortBranchSummary, domain=_D)
    registry.register("setAutoRetry", setAutoRetry, domain=_D)
    registry.register("setAutoCompactionEnabled", setAutoCompactionEnabled, domain=_D)
    registry.register("reload", reload, domain=_D)
    registry.register("setActiveTools", setActiveTools, domain=_D)
    registry.register("navigateTree", navigateTree, domain=_D)
    registry.register("fork", fork, domain=_D)
    registry.register("getSessionStats", getSessionStats, domain=_D)
    registry.register("getContextUsage", getContextUsage, domain=_D)
    registry.register("getSessionEntries", getSessionEntries, domain=_D)
    registry.register("newSession", newSession, domain=_D)
    registry.register("switchSession", switchSession, domain=_D)
    registry.register("cloneSession", cloneSession, domain=_D)
    registry.register("exportSession", exportSession, domain=_D)
    registry.register("importSession", importSession, domain=_D)
    registry.register("dispose", dispose, domain=_D)
    registry.register("shutdown", shutdown, domain=_D)
    registry.register("listAgents", listAgents, domain=_D)
    registry.register("changeAgent", changeAgent, domain=_D)
    registry.register("saveAgent", saveAgent, domain=_D)
    registry.register("getSessionAgents", getSessionAgents, domain=_D)
    registry.register("getPersonas", getPersonas, domain=_D)
    registry.register("setPersonaOverride", setPersonaOverride, domain=_D)
    registry.register("appendEntry", appendEntry, domain=_D)
    registry.register("getTools", getTools, domain=_D)
