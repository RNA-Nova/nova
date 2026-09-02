"""事件类型常量。"""

from __future__ import annotations

SESSION_START = "session_start"
SESSION_SHUTDOWN = "session_shutdown"
SESSION_BEFORE_SWITCH = "session_before_switch"
SESSION_BEFORE_FORK = "session_before_fork"
SESSION_BEFORE_COMPACT = "session_before_compact"
SESSION_COMPACT = "session_compact"
SESSION_BEFORE_TREE = "session_before_tree"
SESSION_TREE = "session_tree"

COMPACTION_START = "compaction_start"
COMPACTION_END = "compaction_end"

CONTEXT = "context"
BEFORE_PROVIDER_REQUEST = "before_provider_request"
BEFORE_PROVIDER_HEADERS = "before_provider_headers"
AFTER_PROVIDER_RESPONSE = "after_provider_response"
BEFORE_AGENT_START = "before_agent_start"
AGENT_START = "agent_start"
AGENT_END = "agent_end"
AGENT_SETTLED = "agent_settled"
TURN_START = "turn_start"
TURN_END = "turn_end"
MESSAGE_START = "message_start"
MESSAGE_UPDATE = "message_update"
MESSAGE_END = "message_end"

TOOL_EXECUTION_START = "tool_execution_start"
TOOL_EXECUTION_UPDATE = "tool_execution_update"
TOOL_EXECUTION_END = "tool_execution_end"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"

USER_BASH = "user_bash"
INPUT = "input"
MODEL_SELECT = "model_select"
THINKING_LEVEL_SELECT = "thinking_level_select"
RESOURCES_DISCOVER = "resources_discover"

PREPARE_NEXT_TURN = "prepare_next_turn"
SHOULD_STOP_AFTER_TURN = "should_stop_after_turn"

AUTO_COMPACTION_START = "auto_compaction_start"
AUTO_COMPACTION_END = "auto_compaction_end"
AUTO_RETRY_START = "auto_retry_start"
AUTO_RETRY_END = "auto_retry_end"

QUEUE_UPDATE = "queue_update"
SESSION_INFO_CHANGED = "session_info_changed"
SESSION_RELOADED = "session_reloaded"
SESSION_REPLACED = "session_replaced"
THINKING_LEVEL_CHANGED = "thinking_level_changed"
MODEL_CHANGED = "model_changed"
USER_TOOL = "user_tool"

EXTENSION_ERROR = "extension_error"
