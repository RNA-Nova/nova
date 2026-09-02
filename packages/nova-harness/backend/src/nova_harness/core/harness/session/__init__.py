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

# Cache waste analysis（类型在 types.session.stats，此处汇集函数与常量）
from nova_harness.core.harness.session.cache_stats import (
    CACHE_TTL_MS,
    NOISE_FLOOR_TOKENS,
    collect_cache_misses,
    compute_cache_waste,
    detect_cache_miss,
)

# Session listing (async scan of session directories)
from nova_harness.core.harness.session.listing import (
    build_session_info,
    list_sessions_from_dir,
)

# Main manager class
from nova_harness.core.harness.session.manager import SessionManager

# Utilities
from nova_harness.core.harness.session.utils import (
    assert_valid_session_id,
    build_context_entries,
    build_session_context,
    find_most_recent_session,
    generate_id,
    generate_session_id,
    get_default_session_dir,
    get_default_session_dir_path,
    get_last_activity_time,
    get_latest_compaction_entry,
    is_valid_session_file,
    load_entries_from_file,
    message_activity_time,
    parse_session_entries,
    parse_session_entry_line,
    session_entry_to_context_messages,
)
from nova_harness.core.types.session.constants import CURRENT_SESSION_VERSION
from nova_harness.core.types.session.stats import (
    CacheMiss,
    CacheWasteTotals,
    ModelPriceSource,
)

__all__ = [
    # Core manager
    "SessionManager",
    # Constants
    "CURRENT_SESSION_VERSION",
    "CACHE_TTL_MS",
    "NOISE_FLOOR_TOKENS",
    # Cache waste analysis
    "CacheMiss",
    "CacheWasteTotals",
    "ModelPriceSource",
    "collect_cache_misses",
    "compute_cache_waste",
    "detect_cache_miss",
    # Utility functions
    "assert_valid_session_id",
    "generate_id",
    "generate_session_id",
    "parse_session_entries",
    "parse_session_entry_line",
    "get_latest_compaction_entry",
    "build_context_entries",
    "build_session_context",
    "session_entry_to_context_messages",
    "get_default_session_dir",
    "get_default_session_dir_path",
    "load_entries_from_file",
    "is_valid_session_file",
    "find_most_recent_session",
    "get_last_activity_time",
    "message_activity_time",
    # Builder functions
    "build_session_tree",
    "create_branched_session_entries",
    # Model functions
    "build_session_info",
    "list_sessions_from_dir",
]
