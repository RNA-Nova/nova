"""写回文本（对位 codex `execpolicy/src/amend.rs` 的文本形态）：规则行产出 +
`append_rule_line_text` 纯文本追加语义（精确行去重 + 补换行纪律）。

分层纪律：引擎（本包）只产文本——咨询式文件锁与落盘 I/O 归消费方（对位
amend.rs `append_rule_line`/`append_locked_line` 的 I/O 半区；nova_protocol
纯洁性纪律禁 I/O 与未声明依赖，见 `tests/test_purity.py`）。
"""

from __future__ import annotations

import json

from ..exec_server_verdict import Decision
from .rule import NetworkRuleProtocol, normalize_network_rule_host


def format_prefix_rule(command_prefix: tuple[str, ...] | list[str]) -> str:
    """`prefix_rule(pattern=["a", "b"], decision="allow")` 规则行（JSON 引号——
    对位 amend.rs `blocking_append_allow_prefix_rule` 的 serde_json::to_string
    语义）"""
    if not command_prefix:
        raise ValueError("prefix rule requires at least one token")
    tokens = ", ".join(
        json.dumps(token, ensure_ascii=False) for token in command_prefix
    )
    return f'prefix_rule(pattern=[{tokens}], decision="allow")'


def format_network_rule(
    host: str,
    protocol: NetworkRuleProtocol,
    decision: Decision,
    justification: str | None = None,
) -> str:
    """`network_rule(...)` 规则行（对位 amend.rs `blocking_append_network_rule`
    的文本形态）。

    - decision 写盘文本对位 codex writer：Forbidden → `"deny"`（不是
      `decision.value` 的 `"forbidden"`；解析侧 `parse_network_rule_decision`
      把 "deny" 读回 Forbidden，写读互认）
    - justification 空串/纯空白即拒（对位 amend.rs 守卫——写出的行
      自己 parse 不回来：NetworkRule 校验拒绝空 justification）
    """
    host = normalize_network_rule_host(host)
    if justification is not None and not justification.strip():
        raise ValueError("justification cannot be empty")
    decision_text = "deny" if decision is Decision.FORBIDDEN else decision.value
    parts = [
        f"host={json.dumps(host, ensure_ascii=False)}",
        f"protocol={json.dumps(protocol.value)}",
        f"decision={json.dumps(decision_text)}",
    ]
    if justification is not None:
        parts.append(f"justification={json.dumps(justification, ensure_ascii=False)}")
    return f"network_rule({', '.join(parts)})"


def append_rule_line_text(contents: str, line: str) -> str:
    """规则文本追加（对位 amend.rs `append_locked_line` 的纯文本语义——
    咨询式文件锁与读写 I/O 归消费方）：

    - 去重：`contents` 逐行精确相等含 `line` 则原样返回（对位
      `contents.lines().any(|existing| existing == line)` 即返回不写）；
    - 追加：`contents` 非空且不以 `"\\n"` 结尾先补 `"\\n"`，再续
      `line + "\\n"`（对位同一函数的补换行纪律——已以换行结尾不多补，
      不产生空行）；空 `contents` 直接得 `line + "\\n"`。
    """
    if any(existing == line for existing in contents.splitlines()):
        return contents
    separator = "" if not contents or contents.endswith("\n") else "\n"
    return contents + separator + line + "\n"
