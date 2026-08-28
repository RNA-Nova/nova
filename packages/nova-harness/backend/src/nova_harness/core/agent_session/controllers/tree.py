"""会话树导航与分支摘要。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from nova_ai import AbortController

from nova_harness.core.agent_session.controllers.compaction import (
    get_summarization_request_auth,
)
from nova_harness.core.harness.compaction import branch_summarization as _branch_module
from nova_harness.core.types.compaction import GenerateBranchSummaryOptions
from nova_harness.core.types.events import SessionReplacedEvent, SessionTreeEvent
from nova_harness.core.types.events.constants import SESSION_BEFORE_TREE
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.types.session.options import NavigateOptions
from nova_harness.core.utils.messages import extract_text_from_content


class TreeNavigator:
    """封装 AgentSession 的会话树导航与分支摘要生成。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    async def navigate(
        self,
        target_id: str,
        options: Optional[Union[NavigateOptions, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """在会话树中导航到指定节点。"""
        if options is None:
            opts = NavigateOptions()
        elif isinstance(options, dict):
            opts = NavigateOptions.model_validate(options)
        else:
            opts = options

        old_leaf_id = self._session.session_manager.get_leaf_id()
        if target_id == old_leaf_id:
            return {"cancelled": False}

        target_entry = self._session.session_manager.get_entry(target_id)
        if target_entry is None:
            raise ValueError(f"Entry {target_id} not found")

        summarize = opts.summarize
        if summarize and self._session.model is None:
            raise RuntimeError("No model available for summarization")

        collect_result = _branch_module.collect_entries_for_branch_summary(
            self._session.session_manager, old_leaf_id, target_id
        )

        custom_instructions = opts.custom_instructions
        replace_instructions = opts.replace_instructions
        label = opts.label

        self._session._branch_summary_abort_controller = AbortController(
            "branch_summary"
        )
        try:
            # session_before_tree 扩展 hook
            runner = self._session._extension_runner
            if runner is not None and runner.has_handlers(SESSION_BEFORE_TREE):
                from nova_harness.core.types.events import (
                    SessionBeforeTreeEvent,
                    TreePreparation,
                )

                prep = TreePreparation(
                    target_id=target_id,
                    old_leaf_id=old_leaf_id,
                    common_ancestor_id=collect_result.common_ancestor_id,
                    entries_to_summarize=collect_result.entries,
                    user_wants_summary=summarize,
                    custom_instructions=custom_instructions,
                    replace_instructions=replace_instructions,
                    label=label,
                )
                result = await runner.emit(
                    SessionBeforeTreeEvent(
                        preparation=prep,
                        signal=self._session._branch_summary_abort_controller.signal,
                    )
                )
                if getattr(result, "cancel", False):
                    return {"cancelled": True}

                extension_summary = (
                    getattr(result, "summary", None) if summarize else None
                )
                from_extension = extension_summary is not None
                if getattr(result, "custom_instructions", None) is not None:
                    custom_instructions = result.custom_instructions
                if getattr(result, "replace_instructions", None) is not None:
                    replace_instructions = result.replace_instructions
                if getattr(result, "label", None) is not None:
                    label = result.label
            else:
                extension_summary = None
                from_extension = False

            # 生成默认摘要
            summary_text: Optional[str] = None
            summary_details: Any = None
            if summarize and collect_result.entries and extension_summary is None:
                api_key, headers, env = await get_summarization_request_auth(
                    self._session, self._session.model, required=True
                )

                branch_settings = (
                    self._session.settings_manager.get_branch_summary_settings()
                )
                reserve = opts.reserve_tokens or branch_settings.reserve_tokens or 16384
                result = await _branch_module.generate_branch_summary(
                    collect_result.entries,
                    GenerateBranchSummaryOptions(
                        model=self._session.model,
                        api_key=api_key,
                        headers=headers,
                        env=env,
                        signal=self._session._branch_summary_abort_controller.signal,
                        custom_instructions=custom_instructions,
                        replace_instructions=replace_instructions,
                        reserve_tokens=reserve,
                        stream_fn=self._session.agent.stream_fn,
                    ),
                )
                if result.aborted:
                    return {"cancelled": True, "aborted": True}
                if result.error:
                    raise RuntimeError(result.error)
                summary_text = result.summary
                summary_details = {
                    "read_files": result.read_files or [],
                    "modified_files": result.modified_files or [],
                }
            elif extension_summary is not None:
                summary_text = extension_summary.summary
                summary_details = getattr(extension_summary, "details", None)
        finally:
            self._session._branch_summary_abort_controller = None

        # 确定新 leaf 位置
        editor_text: Optional[str] = None

        if target_entry.type == "message" and target_entry.message.role == "user":
            # 用户消息：leaf = parent（根则为 None），文本进编辑器
            new_leaf_id = target_entry.parent_id
            editor_text = extract_text_from_content(target_entry.message.content)
        elif target_entry.type == "custom_message":
            new_leaf_id = target_entry.parent_id
            content = target_entry.content
            editor_text = (
                content
                if isinstance(content, str)
                else extract_text_from_content(content)
            )
        else:
            new_leaf_id = target_id

        summary_entry = None
        if summary_text:
            summary_id = self._session.session_manager.branch_with_summary(
                new_leaf_id, summary_text, summary_details, from_extension
            )
            summary_entry = self._session.session_manager.get_entry(summary_id)
            if label:
                self._session.session_manager.append_label_change(summary_id, label)
        elif new_leaf_id is None:
            self._session.session_manager.reset_leaf()
        else:
            self._session.session_manager.branch(new_leaf_id)
            if label:
                self._session.session_manager.append_label_change(target_id, label)

        self._session.agent.state.messages = (
            self._session.session_manager.build_session_context().messages
        )

        if runner is not None:
            await runner.emit(
                SessionTreeEvent(
                    new_leaf_id=self._session.session_manager.get_leaf_id(),
                    old_leaf_id=old_leaf_id,
                    summary_entry=summary_entry,
                    from_extension=from_extension if summary_text else None,
                )
            )

        # Bus 2 通知：leaf 迁移改变了可见消息集，前端需全量重同步 transcript
        self._session._emit(SessionReplacedEvent(reason="navigate"))

        return {
            "editorText": editor_text,
            "cancelled": False,
            "summaryEntry": summary_entry,
        }

    def get_user_messages_for_forking(self) -> List[Dict[str, str]]:
        """返回可用于 fork 选择器的所有用户消息。"""
        entries = self._session.session_manager.get_entries()
        result: List[Dict[str, str]] = []
        for entry in entries:
            if entry.type != "message":
                continue
            message = entry.message
            if message.role != "user":
                continue
            text = extract_text_from_content(message.content)
            if text:
                result.append({"entryId": entry.id, "text": text})
        return result
