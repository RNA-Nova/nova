"""审计采集与对照实验支持（需求 §5 / §5.1）。

采集口径（以框架源码为准）：

- **逐步耗时**：tool_execution_start/end 事件自身不携带时间戳，由本模块在
  事件到达时记录墙钟时间差（同进程同事件循环，单调性足够）；
- **token / 缓存命中率**：``after_provider_response`` 事件载荷只有
  ``{status, headers, model}``——**没有 usage**；usage 取自 ``turn_end``
  事件的 assistant 消息 ``message.usage``（nova_ai ``Usage``：
  ``input/output/cache_read/cache_write/total_tokens``，``reasoning``
  provider 未上报时为 None）。缓存命中率口径 =
  ``cache_read / (input + cache_read)``；
- **过程顺畅度**：纯聚合一期命令流水（按命中脚本分组），零新采集。

对照实验（§5.1）：recall 档位按项目持久化（``memory/system/state.json``）；
盲态拦截为纯函数 ``should_block_memory_access``（recall off 时拦截
read/grep/ls/find 及 bash 命令中对 ``memory/`` 路径的访问）；审计流水
每条记录携带 ``recall_enabled`` 分组字段。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory_store import memory_root

logger = logging.getLogger(__name__)

AUDIT_DIR_NAME = "audit"

# ---------------------------------------------------------------------------
# 采集数据结构（可变运行时容器——普通 dataclass）
# ---------------------------------------------------------------------------


@dataclass
class ToolCallAudit:
    """一次工具执行的审计条目（耗时在 tool_execution_end 回填）。"""

    tool_call_id: str
    tool_name: str
    script: Optional[str]  # 命中的步骤脚本（未归属为 None）
    step_id: Optional[str]
    started_ms: int
    duration_ms: Optional[int] = None
    ok: Optional[bool] = None


@dataclass
class AuditTrail:
    """一次任务的审计轨迹：工具执行流水 + 逐轮 usage。"""

    tool_calls: List[ToolCallAudit] = field(default_factory=list)
    usages: List[Dict[str, Any]] = field(default_factory=list)  # 逐轮 usage dict
    _open: Dict[str, ToolCallAudit] = field(default_factory=dict)  # 未结束调用

    def on_tool_start(
        self,
        tool_call_id: str,
        tool_name: str,
        script: Optional[str],
        step_id: Optional[str],
        now_ms: int,
    ) -> None:
        """tool_execution_start：登记开始时间（步骤归属复用 tool_call 记录）。"""
        entry = ToolCallAudit(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            script=script,
            step_id=step_id,
            started_ms=now_ms,
        )
        self.tool_calls.append(entry)
        self._open[tool_call_id] = entry

    def on_tool_end(self, tool_call_id: str, is_error: bool, now_ms: int) -> None:
        """tool_execution_end：回填耗时与成败（start 缺失时补零耗时条目）。"""
        entry = self._open.pop(tool_call_id, None)
        if entry is None:
            # 未观测到 start（如会话中途 attach）：如实记录，耗时记 None
            entry = ToolCallAudit(
                tool_call_id=tool_call_id,
                tool_name="",
                script=None,
                step_id=None,
                started_ms=now_ms,
            )
            self.tool_calls.append(entry)
        entry.duration_ms = max(now_ms - entry.started_ms, 0)
        entry.ok = not is_error

    def record_usage(self, turn_index: int, usage: Any) -> None:
        """turn_end：提取 assistant 消息的 usage（字段缺失按 0/None 记）。"""
        self.usages.append({"turn_index": turn_index, **extract_usage(usage)})


# ---------------------------------------------------------------------------
# usage 提取与聚合
# ---------------------------------------------------------------------------

_USAGE_FIELDS = ("input", "output", "cache_read", "cache_write", "total_tokens")


def extract_usage(usage: Any) -> Dict[str, Any]:
    """从 nova_ai ``Usage``（或同形对象）提取字段。

    五个 token 字段缺失按 0 记；``reasoning`` provider 未上报时为 None
    （如实记 null，不编造）。
    """
    result: Dict[str, Any] = {
        name: int(getattr(usage, name, 0) or 0) for name in _USAGE_FIELDS
    }
    reasoning = getattr(usage, "reasoning", None)
    result["reasoning"] = int(reasoning) if reasoning is not None else None
    return result


def aggregate_usage(usages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总逐轮 usage：总量 + 缓存命中率。

    缓存命中率口径 ``cache_read / (input + cache_read)``；分母为 0
    （无模型调用）时记 None。provider 未上报缓存字段时 cache_* 全 0，
    命中率如实算为 0（任务页注明口径，不区分"未上报"与"全未命中"）。
    """
    totals: Dict[str, Any] = {name: 0 for name in _USAGE_FIELDS}
    reasoning_total = 0
    reasoning_seen = False
    for usage in usages:
        for name in _USAGE_FIELDS:
            totals[name] += int(usage.get(name, 0) or 0)
        if usage.get("reasoning") is not None:
            reasoning_total += int(usage["reasoning"])
            reasoning_seen = True
    totals["reasoning"] = reasoning_total if reasoning_seen else None
    denominator = totals["input"] + totals["cache_read"]
    totals["cache_hit_rate"] = (
        round(totals["cache_read"] / denominator, 4) if denominator > 0 else None
    )
    totals["turns"] = len(usages)
    return totals


# ---------------------------------------------------------------------------
# 过程顺畅度（纯聚合一期命令流水）
# ---------------------------------------------------------------------------


def smoothness_stats(commands: List[Any]) -> Dict[str, Any]:
    """按脚本分组聚合：尝试次数 / 首次通过 / 重试次数 / 首次通过率。

    重试口径：脚本最终成功时 ``重试 = 尝试 - 1``；最终未成功时
    ``重试 = 尝试``（全部尝试均为失败重试）。无脚本调用时首次通过率 None。
    """
    by_script: Dict[str, List[Any]] = {}
    for cmd in commands:
        script = getattr(cmd, "script", None)
        if script:
            by_script.setdefault(script, []).append(cmd)
    scripts: Dict[str, Dict[str, Any]] = {}
    total_retries = 0
    first_try_passed = 0
    for script, cmds in by_script.items():
        attempts = len(cmds)
        succeeded = any(getattr(c, "ok", None) is True for c in cmds)
        retries = attempts - 1 if succeeded else attempts
        first_try = getattr(cmds[0], "ok", None) is True
        scripts[script] = {
            "attempts": attempts,
            "succeeded": succeeded,
            "first_try_success": first_try,
            "retries": retries,
        }
        total_retries += retries
        first_try_passed += 1 if first_try else 0
    return {
        "scripts": scripts,
        "script_count": len(by_script),
        "total_attempts": sum(s["attempts"] for s in scripts.values()),
        "total_retries": total_retries,
        "first_try_rate": (
            round(first_try_passed / len(by_script), 4) if by_script else None
        ),
    }


# ---------------------------------------------------------------------------
# 盲态拦截（§5.1，纯函数）
# ---------------------------------------------------------------------------

# 盲态期禁读 memory/ 的工具（read 系四件套；bash 单独按命令 token 判定）
_BLIND_TOOLS = {"read", "grep", "ls", "find"}
# 路径型参数键（grep 的 pattern 是检索词不是路径，不检查——避免误伤
# "搜索 memory 这个词" 的合法用法）
_PATH_KEYS = ("path", "paths", "dir", "directory", "cwd")
# memory 路径段匹配：memory/xxx、/memory/xxx、./memory/xxx、裸 memory
_MEMORY_SEGMENT_RE = re.compile(r"(?:^|/)memory(?:/|$)")

_BLIND_REASON = (
    "Experiment 盲态（recall off）：禁止访问 memory/ 路径，"
    "防止模型自发阅读记忆污染对照组。记录与审计不受影响。"
)


def _strings_at_path_keys(args: Any) -> List[str]:
    """收集 args 中路径型键下的字符串值（仅一层，保守不递归）。"""
    if not isinstance(args, dict):
        return []
    values: List[str] = []
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (list, tuple)):
            values += [v for v in value if isinstance(v, str)]
    return values


def _bash_tokens(command: str) -> List[str]:
    """bash 命令的候选路径 token：空白切分 + ``key=value`` 的右值（均去引号）。"""
    tokens: List[str] = []
    for token in re.split(r"\s+", command):
        token = token.strip("\"'")
        if not token:
            continue
        tokens.append(token)
        if "=" in token:
            rhs = token.split("=", 1)[1].strip("\"'")
            if rhs:
                tokens.append(rhs)
    return tokens


def should_block_memory_access(tool_name: str, args: Any) -> Optional[str]:
    """盲态拦截判定：命中返回 block reason，放行返回 None（纯函数）。"""
    if tool_name in _BLIND_TOOLS:
        for value in _strings_at_path_keys(args):
            if _MEMORY_SEGMENT_RE.search(value.strip("\"'")):
                return _BLIND_REASON + f"（{tool_name}: {value}）"
        return None
    if tool_name == "bash":
        command = args.get("command") if isinstance(args, dict) else None
        if not isinstance(command, str):
            return None
        for token in _bash_tokens(command):
            if _MEMORY_SEGMENT_RE.search(token):
                return _BLIND_REASON + f"（bash: {token}）"
    return None


# ---------------------------------------------------------------------------
# 双形态输出：jsonl 流水 + 任务页审计小节
# ---------------------------------------------------------------------------


def build_jsonl_records(
    record: Any, criteria: List[Dict[str, Any]], status: str
) -> List[Dict[str, Any]]:
    """组装 ``memory/audit/<task_id>.jsonl`` 的记录序列。

    三种记录：``tool_call``（逐工具执行）、``model_call``（逐轮 usage）、
    ``task_summary``（四标准 + 重试统计 + usage 汇总）。每条携带
    ``recall_enabled`` 分组字段（§5.1 对照统计按该字段分组）。
    """
    recall = bool(getattr(record, "recall_enabled", True))
    records: List[Dict[str, Any]] = []
    for seq, call in enumerate(record.audit.tool_calls, start=1):
        records.append(
            {
                "record": "tool_call",
                "task_id": record.task_id,
                "recall_enabled": recall,
                "seq": seq,
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "script": call.script,
                "step_id": call.step_id,
                "duration_ms": call.duration_ms,
                "ok": call.ok,
            }
        )
    for usage in record.audit.usages:
        records.append(
            {
                "record": "model_call",
                "task_id": record.task_id,
                "recall_enabled": recall,
                "turn_index": usage["turn_index"],
                "usage": {k: v for k, v in usage.items() if k != "turn_index"},
            }
        )
    finished_ms = getattr(record, "finished_ms", None)
    records.append(
        {
            "record": "task_summary",
            "task_id": record.task_id,
            "recall_enabled": recall,
            "skill": record.skill_name,
            "started_at": record.started_at,
            "duration_ms": (
                max(finished_ms - record.started_ms, 0)
                if finished_ms is not None
                else None
            ),
            "status": status,
            "expected_count": record.expected_count,
            "criteria": {c["key"]: c["ok"] for c in criteria},
            "retry": smoothness_stats(record.commands),
            "usage_totals": aggregate_usage(record.audit.usages),
        }
    )
    return records


def write_audit_jsonl(
    project_root: str, task_id: str, records: List[Dict[str, Any]]
) -> Optional[str]:
    """落盘审计流水（每条一行 JSON）；失败记日志返回 None。"""
    path = memory_root(project_root) / AUDIT_DIR_NAME / f"{task_id}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for entry in records:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return str(path)
    except (OSError, ValueError) as exc:
        logger.warning("experiment: 审计流水写入失败 %s: %s", path, exc)
        return None


def _fmt_duration(ms: Optional[int]) -> str:
    """耗时人性化：毫秒 <1s、秒 <1min、分:秒。"""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def render_audit_section(record: Any) -> List[str]:
    """任务页"审计汇总"小节（人类可读表格 + 口径注明）。"""
    lines: List[str] = ["## 审计汇总", ""]
    recall = bool(getattr(record, "recall_enabled", True))
    lines.append(f"- recall_enabled：{'true' if recall else 'false'}")

    totals = aggregate_usage(record.audit.usages)
    if totals["turns"] == 0:
        lines.append("- 模型调用：未采集到 usage（turn_end 未携带 usage 字段）")
    else:
        rate = totals["cache_hit_rate"]
        rate_text = f"{rate * 100:.1f}%" if rate is not None else "—"
        lines.append(
            f"- 模型调用：{totals['turns']} 轮；tokens in/out/total："
            f"{totals['input']}/{totals['output']}/{totals['total_tokens']}；"
            f"缓存命中率：{rate_text}"
            "（口径 cache_read/(input+cache_read)，provider 未上报缓存字段按 0 计）"
        )

    stats = smoothness_stats(record.commands)
    if stats["script_count"] == 0:
        lines.append("- 过程顺畅度：未捕获到步骤脚本调用")
    else:
        rate = stats["first_try_rate"]
        lines.append(
            f"- 过程顺畅度：脚本 {stats['script_count']} 个 / 尝试 "
            f"{stats['total_attempts']} 次 / 首次通过率 "
            f"{rate * 100:.0f}% / 总重试 {stats['total_retries']} 次"
        )

    if record.audit.tool_calls:
        lines += [
            "",
            "| 步骤 | 脚本 | 工具 | 耗时 | 结果 |",
            "|---|---|---|---|---|",
        ]
        for call in record.audit.tool_calls:
            step = call.step_id or "—"
            script = call.script or "—"
            mark = "✅" if call.ok else ("❌" if call.ok is False else "⏳")
            lines.append(
                f"| {step} | {script} | {call.tool_name} | "
                f"{_fmt_duration(call.duration_ms)} | {mark} |"
            )
    lines.append("")
    return lines
