"""
Session Management Module

This module provides comprehensive session management functionality including:
- Session creation, persistence, and branching
- Message and event tracking
- Session tree structures
- Session listing and querying
"""

# Core types
from .types import (
    # Base types
    SessionHeader,
    SessionEntryBase,
    # Entry types
    SessionMessageEntry,
    ThinkingLevelChangeEntry,
    ModelChangeEntry,
    CompactionDetails,
    CompactionEntry,
    BranchSummaryEntry,
    FrontendToAgentEntry,
    AgentToFrontendEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    SessionInfoEntry,
    # Union types
    SessionEntry,
    FileEntry,
    # Data structures
    SessionTreeNode,
    SessionContext,
    SessionInfo,
)

# Constants
from .constants import CURRENT_SESSION_VERSION

# Utilities
from .utils import (
    generate_id,
    parse_session_entries,
    get_latest_compaction_entry,
    build_session_context,
    get_default_session_dir,
    load_entries_from_file,
    is_valid_session_file,
    find_most_recent_session,
    extract_text_content,
    get_last_activity_time,
    get_session_modified_date,
)

# Builders
from .builders import (
    build_session_tree,
    create_branched_session_entries,
)

# Models (async session listing)
from .models import (
    build_session_info,
    list_sessions_from_dir,
)

# Main manager class
from .manager import SessionManager

__all__ = [
    # Core manager
    "SessionManager",
    
    # Constants
    "CURRENT_SESSION_VERSION",
    
    # Types
    "SessionHeader",
    "SessionEntryBase",
    "SessionMessageEntry",
    "ThinkingLevelChangeEntry",
    "ModelChangeEntry",
    "CompactionDetails",
    "CompactionEntry",
    "BranchSummaryEntry",
    "FrontendToAgentEntry",
    "AgentToFrontendEntry",
    "CustomEntry",
    "CustomMessageEntry",
    "LabelEntry",
    "SessionInfoEntry",
    "SessionEntry",
    "FileEntry",
    "SessionTreeNode",
    "SessionContext",
    "SessionInfo",
    
    # Utility functions
    "generate_id",
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

# Package metadata
__version__ = "1.0.0"
__author__ = "Nova AI Team"