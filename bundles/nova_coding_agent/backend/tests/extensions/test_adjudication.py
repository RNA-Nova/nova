"""adjudication 子系统单元测试。

覆盖：engine 的 bash 裁决（execpolicy 求值 + 危险启发式 + opaque 上移）、
write/edit 保护路径、ApprovalFlow 四结局（never 档/无 UI fail-closed、
会话缓存命中、允许一次、本会话允许、永远允许写规则、拒绝）。
"""

import asyncio

import pytest
from nova_protocol import (
    ApprovalPolicy,
    ExecPolicyAmendment,
    ReviewDecision,
)
from nova_protocol.exec_server_policy import Policy, PrefixPattern, PrefixRule, Decision

from nova_coding_agent.adjudication.approval import (
    ApprovalFlow,
    _CHOICE_FOREVER,
    _CHOICE_NO,
    _CHOICE_ONCE,
    _CHOICE_SESSION,
)
from nova_coding_agent.adjudication.engine import AdjudicationEngine


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, value=None, cancelled=False):
        self.value = value
        self.cancelled = cancelled


class _FakeUI:
    def __init__(self, script=(), capabilities=("select", "dialog:adjudication")):
        self._script = list(script)
        self._capabilities = set(capabilities)
        self.notifications = []
        self.requests = []

    def has_capability(self, method):
        return method in self._capabilities

    async def request(self, method, params):
        self.requests.append((method, params))
        if method in ("select", "dialog:adjudication"):
            return _FakeResponse(value=self._script.pop(0))
        return _FakeResponse()

    async def notify(self, method, params):
        self.notifications.append((method, params))


class _FakeCtx:
    def __init__(
        self, has_ui=True, script=(), capabilities=("select", "dialog:adjudication")
    ):
        self.has_ui = has_ui
        self.ui = _FakeUI(script, capabilities)
        self.entries = []

    def append_entry(self, custom_type, data):
        self.entries.append((custom_type, data))


# ── engine ──


def test_engine_safe_command_skips():
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    assert engine.adjudicate("bash", {"command": "ls -la"}, None).kind == "skip"


def test_engine_dangerous_heuristic_prompts():
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None)
    assert requirement.kind == "needs_approval"


def test_engine_rule_forbidden():
    policy = Policy()
    policy.add_prefix_rule(
        PrefixRule(pattern=PrefixPattern(first="rm"), decision=Decision.FORBIDDEN)
    )
    engine = AdjudicationEngine(policy, ApprovalPolicy.ON_REQUEST)
    assert engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None).kind == "forbidden"


def test_engine_rule_allow_beats_heuristic():
    policy = Policy()
    policy.add_prefix_rule(
        PrefixRule(pattern=PrefixPattern(first="rm"), decision=Decision.ALLOW)
    )
    engine = AdjudicationEngine(policy, ApprovalPolicy.ON_REQUEST)
    # 规则显式 allow：危险启发式不再升级到 prompt（规则命中即终局——allow<max 语义）
    assert engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None).kind == "skip"


def test_engine_opaque_needs_approval():
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "echo $HOME && rm -rf /x"}, None)
    assert requirement.kind == "needs_approval"
    assert "无法静态判定" in (requirement.reason or "")


def test_engine_write_path_protection():
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    assert engine.adjudicate("write", {"path": "/x/.env"}, None).kind == "forbidden"
    assert engine.adjudicate("edit", {"path": "/x/.git/config"}, None).kind == "forbidden"
    assert engine.adjudicate("write", {"path": "/x/main.py"}, None).kind == "skip"


def test_engine_other_tools_skip():
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    assert engine.adjudicate("read", {"path": "/x/.env"}, None).kind == "skip"


# ── approval flow ──


class _FakeRulesStore:
    def __init__(self):
        self.appended = []

    def append_amendment(self, amendment):
        self.appended.append(amendment)
        return f'prefix_rule(pattern={list(amendment.command)}, decision="allow")'


def _flow(policy=ApprovalPolicy.ON_REQUEST, live_policy=None):
    return ApprovalFlow(
        _FakeRulesStore(),
        policy,
        live_policy if live_policy is not None else Policy.empty(),
    )


def test_never_policy_fail_closed():
    flow = _flow(ApprovalPolicy.NEVER)
    ctx = _FakeCtx(has_ui=True, script=[_CHOICE_ONCE])
    action = _action()
    payload = _run(flow.request_approval(action, ctx))
    assert payload.decision is ReviewDecision.DENIED
    # never 档下不弹窗
    assert ctx.ui._script == [_CHOICE_ONCE]


def test_no_ui_fail_closed():
    flow = _flow()
    ctx = _FakeCtx(has_ui=False)
    payload = _run(flow.request_approval(_action(), ctx))
    assert payload.decision is ReviewDecision.DENIED


def test_approve_once():
    flow = _flow()
    ctx = _FakeCtx(script=[{"decision": "once"}])
    payload = _run(flow.request_approval(_action(), ctx))
    assert payload.decision is ReviewDecision.APPROVED


def test_dialog_path_params_and_channel():
    """dialog:adjudication 注册时走专用框——锁通道与载荷形状。"""
    flow = _flow()
    ctx = _FakeCtx(script=[{"decision": "once"}])
    action = _action(amendment=("rm", "-rf"))
    _run(flow.request_approval(action, ctx))
    method, params = ctx.ui.requests[0]
    assert method == "dialog:adjudication"
    assert params["title"] == "执行 bash 命令"
    assert params["command"] == "rm -rf /tmp/x"
    assert params["proposeAmendment"] is True


def test_dialog_cancel_counts_as_deny():
    """对话框取消/畸形回执 → 拒绝（与 select 降级语义一致）。"""
    flow = _flow()
    ctx = _FakeCtx(script=[None])
    payload = _run(flow.request_approval(_action(), ctx))
    assert payload.decision is ReviewDecision.DENIED


def test_select_fallback_when_dialog_unregistered():
    """dialog:adjudication 未注册时 select 降级（通道 + 结局双锁）。"""
    flow = _flow()
    ctx = _FakeCtx(script=[_CHOICE_ONCE], capabilities=("select",))
    payload = _run(flow.request_approval(_action(), ctx))
    assert payload.decision is ReviewDecision.APPROVED
    assert ctx.ui.requests[0][0] == "select"


def test_approve_for_session_caches_and_hits():
    flow = _flow()
    ctx = _FakeCtx(script=[{"decision": "session"}])
    action = _action()
    payload = _run(flow.request_approval(action, ctx))
    assert payload.decision is ReviewDecision.APPROVED_FOR_SESSION
    # 第二次同签名：缓存命中，不再弹窗
    ctx2 = _FakeCtx(script=[])
    payload2 = _run(flow.request_approval(action, ctx2))
    assert payload2.decision is ReviewDecision.APPROVED_FOR_SESSION


def test_approve_forever_writes_rule():
    flow = _flow()
    ctx = _FakeCtx(script=[{"decision": "forever"}])
    action = _action(amendment=("rm", "-rf"))
    payload = _run(flow.request_approval(action, ctx))
    assert payload.decision is ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT
    assert flow._rules_store.appended[0].command == ("rm", "-rf")


def test_deny():
    flow = _flow()
    ctx = _FakeCtx(script=[{"decision": "deny"}])
    payload = _run(flow.request_approval(_action(), ctx))
    assert payload.decision is ReviewDecision.DENIED
    assert payload.rejection == "用户拒绝"


def _action(amendment=None):
    from nova_harness.core.agent_session.controllers.orchestrator import ApprovalAction

    return ApprovalAction(
        title="执行 bash 命令",
        command="rm -rf /tmp/x",
        proposed_amendment_command=amendment,
    )


# ── engine：amendment 产出端（对位 upstream try_derive_execpolicy_amendment_*） ──


def test_engine_heuristics_prompt_carries_amendment():
    """启发式 prompt（无规则命中）→ 提案写回候选 = 触发段的命令词。"""
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None)
    assert requirement.kind == "needs_approval"
    assert requirement.proposed_amendment is not None
    assert requirement.proposed_amendment.command == ("rm", "-rf", "/tmp/x")


def test_engine_multi_segment_amendment_is_first_prompting_segment():
    """复合命令：首个启发式 prompt 段作为写回候选（上游示例语义）。"""
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "ls /tmp && rm -rf /x"}, None)
    assert requirement.kind == "needs_approval"
    assert requirement.proposed_amendment is not None
    assert requirement.proposed_amendment.command == ("rm", "-rf", "/x")


def test_engine_rule_prompt_blocks_amendment():
    """规则 prompt 存在 → 不带写回候选（写回也绕不过规则）。"""
    policy = Policy()
    policy.add_prefix_rule(
        PrefixRule(pattern=PrefixPattern(first="rm"), decision=Decision.PROMPT)
    )
    engine = AdjudicationEngine(policy, ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None)
    assert requirement.kind == "needs_approval"
    assert requirement.proposed_amendment is None


def test_engine_all_segments_rule_allowed_bypasses_sandbox():
    """全部段被规则显式 allow → skip(bypass_sandbox=True)（对位 codex
    Skip{bypass_sandbox}——启发式放行不算数）。"""
    policy = Policy()
    policy.add_prefix_rule(
        PrefixRule(pattern=PrefixPattern(first="rm"), decision=Decision.ALLOW)
    )
    engine = AdjudicationEngine(policy, ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None)
    assert requirement.kind == "skip"
    assert requirement.bypass_sandbox is True
    # 规则命中的 allow 结局不带豁免候选（本就在规则面放行）
    assert requirement.proposed_amendment is None


def test_engine_heuristics_allow_does_not_bypass():
    """启发式 allow（无规则）→ skip 但不脱沙箱；带沙箱豁免候选。"""
    engine = AdjudicationEngine(Policy.empty(), ApprovalPolicy.ON_REQUEST)
    requirement = engine.adjudicate("bash", {"command": "ls /tmp"}, None)
    assert requirement.kind == "skip"
    assert requirement.bypass_sandbox is False
    assert requirement.proposed_amendment is not None
    assert requirement.proposed_amendment.command == ("ls", "/tmp")


# ── approval flow：规则热更新（对位 append_amendment_and_update） ──


def test_forever_hot_updates_live_policy():
    """永远允许：写回 + 热更新活 Policy——后续同类命令引擎直接 skip。"""
    live_policy = Policy.empty()
    engine = AdjudicationEngine(live_policy, ApprovalPolicy.ON_REQUEST)
    flow = _flow(live_policy=live_policy)
    ctx = _FakeCtx(script=[{"decision": "forever"}])
    action = _action(amendment=("rm", "-rf"))
    payload = _run(flow.request_approval(action, ctx))
    assert payload.decision is ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT
    # 热更新生效：同前缀命令现在规则放行（危险启发式被规则 allow 盖过）
    assert engine.adjudicate("bash", {"command": "rm -rf /tmp/x"}, None).kind == "skip"


def test_hot_update_dedupes_existing_allow():
    """规则已允许的前缀 → 热更新跳过去重（对位 append_amendment_and_update
    的 already_allowed 短路）。"""
    live_policy = Policy.empty()
    live_policy.add_prefix_rule(
        PrefixRule(pattern=PrefixPattern(first="rm"), decision=Decision.ALLOW)
    )
    flow = _flow(live_policy=live_policy)
    amendment = ExecPolicyAmendment(command=("rm", "-rf"))
    flow._hot_update_policy(amendment)
    evaluation = live_policy.check(("rm", "-rf", "/x"))
    # 只有既有那条规则命中（没有重复追加）
    assert len(evaluation.matched_rules) == 1
