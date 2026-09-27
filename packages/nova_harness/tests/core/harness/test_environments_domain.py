"""environments 子域测试——选择解析链（升格链）+ 规则写门"""

import pytest
from nova_protocol import ExecutorConfig, ExecutorEnvironment, ResolvedEnvironment

from nova_harness.core.harness.environments import (
    RulesStore,
    resolve_environment_selection,
)


def _config(**kwargs) -> ExecutorConfig:
    kwargs.setdefault(
        "environments",
        [
            ExecutorEnvironment(id="devbox", url="ws://127.0.0.1:8765"),
            ExecutorEnvironment(id="ssh-dev", program="ssh", args=["dev"]),
        ],
    )
    return ExecutorConfig(**kwargs)


# ── 选择解析链（四层短路） ──


def test_session_override_wins():
    override = ResolvedEnvironment(id="devbox", kind="ws", url="ws://override")
    selection = resolve_environment_selection(
        _config(default_environment="ssh-dev"),
        session_override=override,
        agent_default="devbox",
    )
    assert selection.id == "devbox" and selection.url == "ws://override"


def test_agent_default_used_when_no_override():
    selection = resolve_environment_selection(
        _config(default_environment="ssh-dev"), agent_default="devbox"
    )
    assert selection.id == "devbox"


def test_agent_default_unknown_falls_through_to_config():
    selection = resolve_environment_selection(
        _config(default_environment="ssh-dev"), agent_default="missing"
    )
    assert selection.id == "ssh-dev"


def test_config_default_then_local():
    assert resolve_environment_selection(_config()).id == "local"
    assert (
        resolve_environment_selection(_config(default_environment="devbox")).id
        == "devbox"
    )
    with pytest.raises(Exception):
        resolve_environment_selection(
            _config(default_environment="none", include_local=False)
        )


# ── 规则写门 ──


def test_rules_store_missing_file_is_empty_policy(tmp_path):
    store = RulesStore(tmp_path)
    policy = store.load()
    assert policy.check(("rm", "-rf", "/")).decision.value == "allow"


def test_rules_store_amend_roundtrip(tmp_path):
    from nova_protocol import ExecPolicyAmendment

    store = RulesStore(tmp_path)
    line = store.append_amendment(ExecPolicyAmendment(command=("git", "push")))
    assert line == 'prefix_rule(pattern=["git", "push"], decision="allow")'

    policy = store.load()
    assert policy.check(("git", "push", "origin")).decision.value == "allow"
    assert policy.check(("rm", "-rf", "/")).decision.value == "allow"

    store.append_amendment(ExecPolicyAmendment(command=("echo",)))
    policy = store.load()
    assert len(policy.rules_by_program["git"]) == 1
    assert len(policy.rules_by_program["echo"]) == 1
