"""会话树导航与分支摘要。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from nova_agent import AbortController

from nova_harness.core.harness.compaction import branch_summarization as _branch_module
from nova_harness.core.types.compaction import GenerateBranchSummaryOptions
from nova_harness.core.types.events import SessionTreeEvent
from nova_harness.core.types.events.constants import SESSION_BEFORE_TREE
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.types.session.options import NavigateOptions
from nova_harness.core.utils.messages import extract_text_from_content


class TreeNavigator:
    """封装 AgentSession 的会话树导航与分支摘要生成。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    def _extract_user_message_text(self, content: Any) -> str:
        """从用户消息内容中提取纯文本。"""
        return extract_text_from_content(content)

    async def navigate(
        self,
        target_id: str,
        options: Optional[Union[NavigateOptions, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """在会话树中导航到指定节点。"""
        opts: Dict[str, Any]
        if options is None:
            opts = {}
        elif isinstance(options, dict):
            opts = options
        else:
            opts = {
                "summarize": getattr(options, "summarize", False),
                "custom_instructions": getattr(options, "custom_instructions", None),
                "replace_instructions": getattr(options, "replace_instructions", False),
                "label": getattr(options, "label", None),
                "reserve_tokens": getattr(options, "reserve_tokens", None),
            }

        old_leaf_id = self._session.session_manager.get_leaf_id()
        if target_id == old_leaf_id:
            return {"cancelled": False}

        target_entry = self._session.session_manager.get_entry(target_id)
        if target_entry is None:
            raise ValueError(f"Entry {target_id} not found")

        summarize = opts.get("summarize", False)
        if summarize and self._session.model is None:
            raise RuntimeError("No model available for summarization")

        collect_result = _branch_module.collect_entries_for_branch_summary(
            self._session.session_manager, old_leaf_id, target_id
        )

        custom_instructions = opts.get("custom_instructions")
        replace_instructions = opts.get("replace_instructions", False)
        label = opts.get("label")

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
            self._session._branch_summary_abort_controller = AbortController(
                "branch_summary"
            )
            try:
                result = await runner.emit(
                    SessionBeforeTreeEvent(
                        preparation=prep,
                        signal=self._session._branch_summary_abort_controller.signal,
                    )
                )
                if getattr(result, "cancel", False):
                    return {"cancelled": True}
                if getattr(result, "summary", None) is not None and summarize:
                    summary_text = result.summary.summary
                    summary_details = getattr(result.summary, "details", None)
                    from_extension = True
                else:
                    summary_text = None
                    summary_details = None
                    from_extension = False
                if getattr(result, "custom_instructions", None) is not None:
                    custom_instructions = result.custom_instructions
                if getattr(result, "replace_instructions", None) is not None:
                    replace_instructions = result.replace_instructions
                if getattr(result, "label", None) is not None:
                    label = result.label
            finally:
                self._session._branch_summary_abort_controller = None
        else:
            summary_text = None
            summary_details = None
            from_extension = False
            self._session._branch_summary_abort_controller = AbortController(
                "branch_summary"
            )

        # 生成默认摘要
        if summarize and collect_result.entries and summary_text is None:
            try:
                api_key = None
                registry = self._session.model_registry
                if hasattr(registry, "get_api_key"):
                    api_key = await registry.get_api_key(self._session.model)
                if not api_key:
                    raise RuntimeError(f"No API key for {self._session.model.provider}")

                branch_settings = (
                    self._session.settings_manager.get_branch_summary_settings()
                )
                reserve = opts.get("reserve_tokens") or getattr(
                    branch_settings, "reserve_tokens", 16384
                )
                result = await _branch_module.generate_branch_summary(
                    collect_result.entries,
                    GenerateBranchSummaryOptions(
                        model=self._session.model,
                        api_key=api_key,
                        signal=self._session._branch_summary_abort_controller.signal,
                        custom_instructions=custom_instructions,
                        replace_instructions=replace_instructions,
                        reserve_tokens=reserve,
                    ),
                )
                if getattr(result, "aborted", False):
                    return {"cancelled": True, "aborted": True}
                if getattr(result, "error", None):
                    raise RuntimeError(result.error)
                summary_text = result.summary
                summary_details = {
                    "read_files": getattr(result, "read_files", []) or [],
                    "modified_files": getattr(result, "modified_files", []) or [],
                }
            finally:
                self._session._branch_summary_abort_controller = None

        # 确定新 leaf 位置
        target_type = getattr(target_entry, "type", None)
        target_message = getattr(target_entry, "message", None)
        editor_text: Optional[str] = None

        if target_type == "message" and getattr(target_message, "role", None) == "user":
            new_leaf_id = getattr(target_entry, "parent_id", None)
            editor_text = self._extract_user_message_text(
                getattr(target_message, "content", "")
            )
        elif target_type == "custom_message":
            new_leaf_id = getattr(target_entry, "parent_id", None)
            content = getattr(target_entry, "content", "")
            editor_text = (
                content
                if isinstance(content, str)
                else self._extract_user_message_text(content)
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
            if getattr(entry, "type", None) != "message":
                continue
            message = getattr(entry, "message", None)
            if getattr(message, "role", None) != "user":
                continue
            text = self._extract_user_message_text(getattr(message, "content", ""))
            if text:
                result.append({"entryId": entry.id, "text": text})
        return result
