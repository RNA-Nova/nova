"""
Session Management Module

This module provides comprehensive session management functionality including:
- Session creation, persistence, and branching
- Message and event tracking
- Session tree structures
- Session listing and querying
"""

# Builders
from nova_harness.core.harness.session.builders import (
    build_session_tree,
    create_branched_session_entries,
)

# Main manager class
from nova_harness.core.harness.session.manager import SessionManager

# Models (async session listing)
from nova_harness.core.harness.session.models import (
    build_session_info,
    list_sessions_from_dir,
)

# Utilities
from nova_harness.core.harness.session.utils import (
    build_session_context,
    extract_text_content,
    find_most_recent_session,
    generate_id,
    generate_session_id,
    get_default_session_dir,
    get_last_activity_time,
    get_latest_compaction_entry,
    get_session_modified_date,
    is_valid_session_file,
    load_entries_from_file,
    parse_session_entries,
)
from nova_harness.core.types.session.constants import CURRENT_SESSION_VERSION

__all__ = [
    # Core manager
    "SessionManager",
    # Constants
    "CURRENT_SESSION_VERSION",
    # Utility functions
    "generate_id",
    "generate_session_id",
    "parse_session_entries",
    "get_latest_compaction_entry",
    "build_session_context",
    "get_default_session_dir",
    "load_entries_from_file",
    "is_valid_session_file",
    "find_most_recent_session",
    "extract_text_content",
    "get_last_activity_time",
    "get_session_modified_date",
    # Builder functions
    "build_session_tree",
    "create_branched_session_entries",
    # Model functions
    "build_session_info",
    "list_sessions_from_dir",
]
