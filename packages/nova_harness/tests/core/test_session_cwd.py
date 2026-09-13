"""session_cwd 模块测试。"""

from unittest.mock import MagicMock

import pytest
from nova_harness.core.utils.session_cwd import (
    MissingSessionCwdError,
    assert_session_cwd_exists,
    get_missing_session_cwd_issue,
)


def _make_source(cwd: str, session_file: str = "session.jsonl"):
    source = MagicMock()
    source.get_cwd.return_value = cwd
    source.get_session_file.return_value = session_file
    return source


def test_get_missing_session_cwd_issue_returns_none_when_cwd_exists(tmp_path):
    """cwd 存在时不应返回 issue。"""
    source = _make_source(str(tmp_path))
    assert get_missing_session_cwd_issue(source, "/fallback") is None


def test_get_missing_session_cwd_issue_returns_issue_when_cwd_missing():
    """cwd 不存在时应返回 issue。"""
    source = _make_source("/nonexistent/cwd")
    issue = get_missing_session_cwd_issue(source, "/fallback")
    assert issue is not None
    assert issue.session_cwd == "/nonexistent/cwd"
    assert issue.fallback_cwd == "/fallback"
    assert issue.session_file == "session.jsonl"


def test_assert_session_cwd_exists_raises_when_cwd_missing():
    """cwd 不存在时应抛出 MissingSessionCwdError。"""
    source = _make_source("/nonexistent/cwd")
    with pytest.raises(MissingSessionCwdError) as exc_info:
        assert_session_cwd_exists(source, "/fallback")

    assert "/nonexistent/cwd" in str(exc_info.value)
    assert "/fallback" in str(exc_info.value)


def test_assert_session_cwd_exists_passes_when_cwd_exists(tmp_path):
    """cwd 存在时不应抛出异常。"""
    source = _make_source(str(tmp_path))
    assert_session_cwd_exists(source, "/fallback")
