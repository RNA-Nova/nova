"""规则文件写门（`~/.nova/exec-server/rules.lark`）。

对位 codex `execpolicy/amend.rs` 的职责划分（D7 分层）：引擎（nova_protocol
exec_server_policy）产规则文本与追加纯语义（`append_rule_line_text`——
精确行去重 + 补换行纪律），本模块做全部文件 I/O——读取（→ Policy）与
"永远允许"写回（咨询式文件锁——对位 amend.rs 的 advisory locking）。

规则文件缺席 = 空 Policy（codex 同语义）；解析失败响亮抛错（配置是用户
资产，坏文件不静默吞）。
"""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock
from nova_protocol import ExecPolicyAmendment, format_prefix_rule, parse_policy
from nova_protocol.exec_server_policy import Policy, append_rule_line_text

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
        """ "永远允许"写回：追加 `prefix_rule(..., decision="allow")` 规则行。
        返回写入的规则行（去重命中时同样返回——语义是"该规则已在册"）。

        对位 amend.rs `append_rule_line` + `append_locked_line`（D7 分层——
        去重/补换行文本语义在 nova_protocol `append_rule_line_text`，本方法
        只做 I/O）：父目录**单级**创建（对位 `create_dir`——AlreadyExists
        容忍，非 create_dir_all）→ 侧车 FileLock（`str(path) + ".lock"`——
        nova 内部唯一写方同此惯例，对位 fs2 锁文件本体的 advisory locking；
        锁文件栖身父目录，故目录创建在取锁之前，与 codex 同序）→ 读全文
        （缺席 = 空串）→ 纯函数算出追加后文本 → 有变化才整文写回（去重
        命中即不写；规则文件极小，整文写回最简）。
        """
        line = format_prefix_rule(amendment.command)
        self._path.parent.mkdir(exist_ok=True)
        lock = FileLock(str(self._path) + ".lock")
        with lock:
            contents = (
                self._path.read_text(encoding="utf-8") if self._path.exists() else ""
            )
            updated = append_rule_line_text(contents, line)
            if updated != contents:
                self._path.write_text(updated, encoding="utf-8")
        return line
