"""规则文件写门（`~/.nova/exec-server/rules.lark`）。

对位 codex `execpolicy/amend.rs` 的职责划分：引擎（nova_protocol
exec_server_policy）产规则文本与解析，本模块做全部文件 I/O——
读取（→ Policy）与"永远允许"写回（追加规则行，咨询式文件锁——
对位 amend.rs 的 advisory locking）。

规则文件缺席 = 空 Policy（codex 同语义）；解析失败响亮抛错（配置是用户
资产，坏文件不静默吞）。
"""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock
from nova_protocol import ExecPolicyAmendment, format_prefix_rule, parse_policy
from nova_protocol.exec_server_policy import Policy

RULES_FILE_NAME = "rules.lark"


class RulesStore:
    """规则文件的读写门（每次调用现造实例，无跨调用状态）"""

    def __init__(self, exec_server_home: str | Path) -> None:
        self._path = Path(exec_server_home) / RULES_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Policy:
        """读取规则文件 → Policy；文件缺席 = 空 Policy"""
        if not self._path.exists():
            return Policy.empty()
        contents = self._path.read_text(encoding="utf-8")
        return parse_policy(str(self._path), contents)

    def append_amendment(self, amendment: ExecPolicyAmendment) -> str:
        """ "永远允许"写回：追加 `prefix_rule(..., decision="allow")` 规则行
        （咨询式文件锁，对位 amend.rs append_locked_line）。返回写入的规则行。
        """
        line = format_prefix_rule(amendment.command)
        lock = FileLock(str(self._path) + ".lock")
        with lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            needs_newline = self._path.exists() and self._path.stat().st_size > 0
            with self._path.open("a", encoding="utf-8") as file:
                if needs_newline:
                    file.write("\n")
                file.write(line + "\n")
        return line
