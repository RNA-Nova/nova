"""exec 裁决引擎（execpolicy py 移植——金标已固化：`tests/test_execpolicy_golden.py`）
+ 沙箱拒绝启发式。

包拆分对位 codex `execpolicy/src/` 模块边界（逐件对位，不臆造）：

- `rule.py`（rule.rs）：`prefix_rule`/`network_rule` 两类规则；
  `PrefixPattern` 前缀匹配（首 token 定值 + 后续 PatternToken 单值/多选）。
- `policy.py`（policy.rs）：`MatchOptions`/`Policy.check*`，
  `matches_for_command_with_options` 主链（精确规则 → host 可执行解析 →
  启发式兜底）+ `compiled_network_domains` 域名单编译。
- `parser.py`（parser.rs）：**声明式子集**——规则文件即 Python 语法兼容的
  `prefix_rule(...)`/`network_rule(...)`/`host_executable(...)` 顶层调用序列
  （codex 用 starlark 全量求值；本实现以 `ast` 静态提取字面参数，不执行
  任意表达式——纯度与安全双纪律）。`match`/`not_match` 样例在解析末尾
  随规则校验（对位 validate_pending_examples_from）。
- `amend.py`（amend.rs）：`format_prefix_rule`/`format_network_rule` 产规则行
  （serde_json 文本形态）+ `append_rule_line_text` 纯文本追加语义（精确行
  去重 + 补换行纪律）；咨询式文件锁与落盘 I/O 归消费方。
- `executable_name.py`（executable_name.rs）：可执行名/路径查找键。
- `denial.py`（sandboxing denial.rs）：沙箱拒绝启发式。

`Decision` 住 `nova_protocol.exec_server_verdict`（对位 decision.rs 的角色），
此处再导出保持 `nova_protocol.exec_server_policy.Decision` 导入路径。
"""

from ..exec_server_verdict import Decision
from .amend import append_rule_line_text, format_network_rule, format_prefix_rule
from .denial import (
    SANDBOX_DENIED_KEYWORDS,
    is_likely_executor_managed_sandbox_denied,
    is_likely_sandbox_denied,
)
from .executable_name import executable_lookup_key, executable_path_lookup_key
from .parser import PolicyParseError, parse_policy
from .policy import (
    Evaluation,
    HeuristicsRuleMatch,
    MatchOptions,
    Policy,
    PrefixRuleMatch,
)
from .rule import (
    NetworkRule,
    NetworkRuleProtocol,
    PatternToken,
    PrefixPattern,
    PrefixRule,
    normalize_network_rule_host,
    render_pattern_token,
)

__all__ = [
    "Decision",
    "Evaluation",
    "HeuristicsRuleMatch",
    "MatchOptions",
    "NetworkRule",
    "NetworkRuleProtocol",
    "PatternToken",
    "Policy",
    "PolicyParseError",
    "PrefixPattern",
    "PrefixRule",
    "PrefixRuleMatch",
    "SANDBOX_DENIED_KEYWORDS",
    "append_rule_line_text",
    "executable_lookup_key",
    "executable_path_lookup_key",
    "format_network_rule",
    "format_prefix_rule",
    "is_likely_executor_managed_sandbox_denied",
    "is_likely_sandbox_denied",
    "normalize_network_rule_host",
    "parse_policy",
    "render_pattern_token",
]
