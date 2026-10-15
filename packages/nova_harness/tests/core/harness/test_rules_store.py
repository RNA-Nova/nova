"""RulesStore 测试——规则写门的文件行为（D7 分层实证：去重/补换行文本语义在
nova_protocol `append_rule_line_text`，咨询式文件锁与落盘归 RulesStore）。

对位 codex basic.rs `append_allow_prefix_rule_dedupes_existing_rule` 与
amend.rs 自带 `appends_rule_without_duplicate_newline` 的文件面。
"""

from nova_harness.core.harness.environments import RulesStore
from nova_protocol import ExecPolicyAmendment


def test_append_amendment_dedupes_existing_rule(tmp_path):
    """两次写同命令 → 文件精确单行（读回去重实证——对位
    append_allow_prefix_rule_dedupes_existing_rule）"""
    store = RulesStore(tmp_path)
    store.append_amendment(ExecPolicyAmendment(command=("python3",)))
    store.append_amendment(ExecPolicyAmendment(command=("python3",)))
    assert (
        store.path.read_text(encoding="utf-8")
        == 'prefix_rule(pattern=["python3"], decision="allow")\n'
    )


def test_append_amendment_appends_distinct_rules_without_blank_lines(tmp_path):
    """连续两条不同命令 → 逐行精确、无空行（对位
    appends_rule_without_duplicate_newline——已以换行结尾不多补）"""
    store = RulesStore(tmp_path)
    store.append_amendment(ExecPolicyAmendment(command=("python3",)))
    store.append_amendment(ExecPolicyAmendment(command=("git", "push")))
    assert store.path.read_text(encoding="utf-8") == (
        'prefix_rule(pattern=["python3"], decision="allow")\n'
        'prefix_rule(pattern=["git", "push"], decision="allow")\n'
    )
