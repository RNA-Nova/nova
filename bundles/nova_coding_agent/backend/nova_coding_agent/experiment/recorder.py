"""自动记录与四标准判定（需求 §4.5 / §4.6）。

记录触发全部来自框架事件，不依赖用户或模型的自觉：

- 任务开始（input 事件）：建立 TaskRecord——参数快照在 bash 命令到达时
  现取（命令行引用的 yaml 全文复制存档 + 命令全文）；
- 每步结束（tool_execution_end）：产物盘点——产物文件清单 + csv/tsv 行数；
- 任务结束（agent_end）：四标准判定 + 生成任务页 + 更新 INDEX。

观测优先：schema 提取不全只影响"少检查"，不影响记录；判定中的"未声明 /
无法核对"为中性结果，不算失败。
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import steps as steps_mod

logger = logging.getLogger(__name__)

# yaml 快照大小上限（防止巨型配置撑爆任务页）
_MAX_SNAPSHOT_BYTES = 200 * 1024
# 目录产物清单的条目上限
_MAX_DIR_LISTING = 50
# 可计数行数的文本产物后缀
_COUNTABLE_SUFFIXES = {".csv", ".tsv"}
# 文件型产物判定（basename 含扩展名；目录型产物只做盘点不做缺项检查）
_FILE_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def new_task_id(now: Optional[datetime] = None) -> str:
    """生成任务 ID：``task-YYYYMMDD-HHMMSS-xxxx``（xxxx 为随机短码）。"""
    now = now or datetime.now()
    return f"task-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# ---------------------------------------------------------------------------
# 记录数据结构（可变运行时容器——普通 dataclass，不进 Pydantic）
# ---------------------------------------------------------------------------


@dataclass
class OutputInventory:
    """单个期望产物的盘点结果。"""

    path: str  # 相对项目根的产物路径
    exists: bool = False
    kind: str = "file"  # file / dir
    size: int = 0
    lines: Optional[int] = None  # 非空行数（可计数文本产物）
    data_lines: Optional[int] = None  # 数据行数（csv/tsv 扣表头）
    listing: List[str] = field(default_factory=list)  # 目录型产物的文件清单


@dataclass
class CommandRecord:
    """一条 bash 命令的档案（命令全文 + 参数快照 + 执行结果 + 产物盘点）。"""

    tool_call_id: str
    command: str
    step_id: Optional[str] = None  # schema 步骤归属（未命中为 None）
    yaml_snapshots: Dict[str, str] = field(default_factory=dict)  # 路径 → 全文
    ok: Optional[bool] = None  # tool_execution_end 回填
    outputs: List[OutputInventory] = field(default_factory=list)


@dataclass
class TaskRecord:
    """一次任务的完整档案（任务页的数据源）。"""

    task_id: str
    project_root: str
    skill_name: str
    prompt: str
    expected_count: Optional[int]
    schema: Dict[str, Any]
    started_at: str  # ISO 日期时间
    started_ms: int
    commands: List[CommandRecord] = field(default_factory=list)
    other_failures: List[Dict[str, str]] = field(default_factory=list)  # 非 bash 失败
    interrupted: bool = False

    # ------------------------------------------------------------------
    # 事件侧记录
    # ------------------------------------------------------------------

    def record_command(self, tool_call_id: str, command: str) -> CommandRecord:
        """tool_call 捕获 bash 命令：步骤归属 + 命令行引用 yaml 全文快照。"""
        record = CommandRecord(
            tool_call_id=tool_call_id,
            command=command,
            step_id=steps_mod.attribute_command(command, self.schema),
        )
        for rel in steps_mod.extract_yaml_paths(command):
            snapshot = _read_snapshot(self.project_root, rel)
            if snapshot is not None:
                record.yaml_snapshots[rel] = snapshot
        self.commands.append(record)
        return record

    def record_tool_end(
        self, tool_call_id: str, tool_name: str, is_error: bool
    ) -> None:
        """tool_execution_end 回填结果；bash 命令归属步骤时做产物盘点。"""
        record = next(
            (c for c in self.commands if c.tool_call_id == tool_call_id), None
        )
        if record is not None:
            record.ok = not is_error
            if record.step_id and not is_error:
                record.outputs = inventory_step_outputs(
                    self.schema, record.step_id, self.project_root
                )
        elif is_error:
            self.other_failures.append(
                {"tool_name": tool_name, "tool_call_id": tool_call_id}
            )

    def step_title(self, step_id: Optional[str]) -> str:
        """步骤标题（未归属返回占位名）。"""
        if step_id is None:
            return "未归属命令"
        for step in self.schema.get("steps", []):
            if step.get("step_id") == step_id:
                return f"Step {step_id} {step.get('title', '')}".strip()
        return f"Step {step_id}"


def _read_snapshot(project_root: str, rel: str) -> Optional[str]:
    """读取命令行引用的 yaml 全文（相对项目根解析；超限/失败返回 None）。"""
    try:
        path = Path(project_root) / rel
        if not path.is_file() or path.stat().st_size > _MAX_SNAPSHOT_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _count_lines(path: Path) -> Optional[tuple]:
    """统计文本产物行数，返回 ``(非空行数, 数据行数)``；失败返回 None。"""
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
    except OSError:
        return None
    total = len(lines)
    data = max(total - 1, 0) if path.suffix.lower() in _COUNTABLE_SUFFIXES else total
    return total, data


def inventory_output(project_root: str, rel: str) -> OutputInventory:
    """盘点单个期望产物（文件：大小 + 行数；目录：文件清单）。"""
    inv = OutputInventory(path=rel)
    try:
        path = Path(project_root) / rel
        if path.is_dir():
            inv.exists = True
            inv.kind = "dir"
            inv.listing = sorted(p.name for p in path.iterdir())[:_MAX_DIR_LISTING]
        elif path.is_file():
            inv.exists = True
            inv.size = path.stat().st_size
            if path.suffix.lower() in _COUNTABLE_SUFFIXES | {".txt", ".fasta", ".fa"}:
                counted = _count_lines(path)
                if counted is not None:
                    inv.lines, inv.data_lines = counted
    except OSError as exc:
        logger.debug("experiment: 产物盘点失败 %s: %s", rel, exc)
    return inv


def inventory_step_outputs(
    schema: Dict[str, Any], step_id: str, project_root: str
) -> List[OutputInventory]:
    """盘点某步骤的全部期望产物（schema 提取不全 → 只盘点已提取项）。"""
    for step in schema.get("steps", []):
        if step.get("step_id") == step_id:
            return [
                inventory_output(project_root, rel) for rel in step.get("outputs", [])
            ]
    return []


# ---------------------------------------------------------------------------
# 四标准判定（§4.6）
# ---------------------------------------------------------------------------


def _is_file_like(rel: str) -> bool:
    """期望产物是否为文件型（basename 含扩展名）。"""
    return bool(_FILE_SUFFIX_RE.search(Path(rel).name))


def evaluate_criteria(
    record: TaskRecord,
    path_exists: Optional[Callable[[str], bool]] = None,
) -> List[Dict[str, Any]]:
    """四标准逐项判定（纯逻辑；``path_exists`` 可注入便于单测）。

    返回 ``[{key, name, ok, note}]``；整体成功 = 全部 ok。
    中性结果（未声明 / 无法核对 / 未检查）记 note，不判失败。
    """
    if path_exists is None:
        root = Path(record.project_root)

        def _default_exists(rel: str) -> bool:
            return (root / rel).exists()

        path_exists = _default_exists

    criteria: List[Dict[str, Any]] = []

    # 标准 1：工具全成功（无失败 tool_result）
    failed = [c.command for c in record.commands if c.ok is False]
    failed += [f["tool_name"] for f in record.other_failures]
    criteria.append(
        {
            "key": "tools_ok",
            "name": "工具全部调用成功",
            "ok": not failed,
            "note": (
                "无失败"
                if not failed
                else f"失败 {len(failed)} 项：{'; '.join(failed[:5])}"
            ),
        }
    )

    # 标准 2：产物完整性——执行过的步骤对照期望清单不缺项（仅文件型产物）
    executed_steps = {c.step_id for c in record.commands if c.step_id}
    expected: List[str] = []
    for step in record.schema.get("steps", []):
        if step.get("step_id") in executed_steps:
            expected += [o for o in step.get("outputs", []) if _is_file_like(o)]
    missing = [rel for rel in expected if not path_exists(rel)]
    if not expected:
        criteria.append(
            {
                "key": "outputs_complete",
                "name": "产物完整性",
                "ok": True,
                "note": "未检查（schema 未提取到可核对的期望产物）",
            }
        )
    else:
        criteria.append(
            {
                "key": "outputs_complete",
                "name": "产物完整性",
                "ok": not missing,
                "note": "无缺项" if not missing else f"缺项：{'; '.join(missing[:10])}",
            }
        )

    # 标准 3：终产物数量符合提示词要求
    if record.expected_count is None:
        criteria.append(
            {
                "key": "count_match",
                "name": "终产物数量符合要求",
                "ok": True,
                "note": "未声明（提示词中未解析到数量要求）",
            }
        )
    else:
        measured: Optional[int] = None
        measured_path = ""
        for rel in record.schema.get("final_outputs", []):
            if Path(rel).suffix.lower() not in _COUNTABLE_SUFFIXES:
                continue
            inv = inventory_output(record.project_root, rel)
            if inv.exists and inv.data_lines is not None:
                measured = inv.data_lines
                measured_path = rel
                break
        if measured is None:
            criteria.append(
                {
                    "key": "count_match",
                    "name": "终产物数量符合要求",
                    "ok": True,
                    "note": "无法核对（未找到可计数的终产物文件）",
                }
            )
        else:
            ok = measured == record.expected_count
            criteria.append(
                {
                    "key": "count_match",
                    "name": "终产物数量符合要求",
                    "ok": ok,
                    "note": (
                        f"{measured_path} 数据行 {measured} == 要求 {record.expected_count}"
                        if ok
                        else f"{measured_path} 数据行 {measured} != 要求 {record.expected_count}"
                    ),
                }
            )

    # 标准 4：无中断
    criteria.append(
        {
            "key": "no_interrupt",
            "name": "无中断",
            "ok": not record.interrupted,
            "note": "正常结束" if not record.interrupted else "任务被中断（abort）",
        }
    )
    return criteria


def overall_status(record: TaskRecord, criteria: List[Dict[str, Any]]) -> str:
    """任务状态：completed / failed / aborted（中断优先标记）。"""
    if record.interrupted:
        return "aborted"
    return "completed" if all(c["ok"] for c in criteria) else "failed"


def detect_interruption(messages: List[Any]) -> bool:
    """从 agent_end 消息列表检测中断（assistant stop_reason == aborted）。"""
    for message in messages or []:
        if getattr(message, "role", "") == "assistant" and (
            getattr(message, "stop_reason", None) == "aborted"
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# 任务页渲染
# ---------------------------------------------------------------------------


def _fmt_count(inv: OutputInventory) -> str:
    if inv.data_lines is not None and inv.path.lower().endswith(
        tuple(_COUNTABLE_SUFFIXES)
    ):
        return f"{inv.data_lines} 行数据（共 {inv.lines} 行）"
    if inv.lines is not None:
        return f"{inv.lines} 行"
    return f"{inv.size} B"


def render_task_page(
    record: TaskRecord, criteria: List[Dict[str, Any]], status: str
) -> str:
    """生成 ``memory/tasks/<task_id>.md`` 的完整内容。"""
    date = record.started_at[:10]
    fm_lines = [
        "---",
        "type: episodic",
        f'task_id: "{record.task_id}"',
        f'skill: "{record.skill_name}"',
        f"date: {date}",
        f"status: {status}",
    ]
    if record.expected_count is not None:
        fm_lines.append(f"expected_count: {record.expected_count}")
    fm_lines += ["tags: [experiment]", "---"]

    lines: List[str] = fm_lines + [
        "",
        f"# {record.task_id} — {record.skill_name}",
        "",
        "## 任务概要",
        f"- 绑定 skill：`{record.skill_name}`",
        f"- 开始时间：{record.started_at}",
        f"- 状态：{status}",
        "",
        "### 任务提示词",
        "",
        "> " + record.prompt.replace("\n", "\n> "),
        "",
        "## 参数快照",
        "",
    ]

    snapshotted = [c for c in record.commands if c.yaml_snapshots]
    if not snapshotted:
        lines.append("（本任务命令行未引用可快照的 yaml 配置）")
    for cmd in snapshotted:
        lines += [f"### 命令 `{cmd.command}`", ""]
        for rel, content in cmd.yaml_snapshots.items():
            lines += [f"`{rel}` 全文：", "", "```yaml", content.rstrip("\n"), "```", ""]

    lines += ["## 逐步 I/O 对账", ""]
    if not record.commands:
        lines.append("（本任务未捕获到 bash 命令）")
    else:
        # 按步骤分组（保序）
        grouped: Dict[Optional[str], List[CommandRecord]] = {}
        for cmd in record.commands:
            grouped.setdefault(cmd.step_id, []).append(cmd)
        for step_id, cmds in grouped.items():
            lines += [f"### {record.step_title(step_id)}", ""]
            for cmd in cmds:
                mark = "✅" if cmd.ok else ("❌" if cmd.ok is False else "⏳")
                lines += [f"- {mark} `{cmd.command}`"]
                for inv in cmd.outputs:
                    if inv.exists:
                        if inv.kind == "dir":
                            listing = ", ".join(inv.listing[:10])
                            more = (
                                f" …（共 {len(inv.listing)} 项）"
                                if len(inv.listing) > 10
                                else ""
                            )
                            lines.append(
                                f"  - 📁 `{inv.path}/`：{len(inv.listing)} 项（{listing}{more}）"
                            )
                        else:
                            lines.append(f"  - 📄 `{inv.path}`：{_fmt_count(inv)}")
                    else:
                        lines.append(f"  - ⚠️ `{inv.path}`：缺失")
            lines.append("")

    lines += ["## 四标准判定", ""]
    for c in criteria:
        mark = "✅" if c["ok"] else "❌"
        lines.append(f"- {mark} **{c['name']}**：{c['note']}")
    lines.append(
        f"\n**总体判定：{'成功' if status == 'completed' else ('中断' if status == 'aborted' else '失败')}**"
    )

    failures = [c for c in record.commands if c.ok is False]
    lines += ["", "## 异常摘要", ""]
    if not failures and not record.other_failures and not record.interrupted:
        lines.append("（无异常）")
    else:
        for cmd in failures:
            lines.append(f"- 命令失败：`{cmd.command}`")
        for f in record.other_failures:
            lines.append(f"- 工具失败：{f['tool_name']}（{f['tool_call_id']}）")
        if record.interrupted:
            lines.append("- 任务被中断（abort）")
    lines.append("")
    return "\n".join(lines)


def index_summary_line(record: TaskRecord, status: str) -> str:
    """INDEX.md 任务行的摘要文本。"""
    date = record.started_at[:10]
    return f"{record.task_id} — {record.skill_name}（{date}，{status}）"


def start_record(
    project_root: str,
    skill_name: str,
    prompt: str,
    schema: Dict[str, Any],
    now: Optional[datetime] = None,
) -> TaskRecord:
    """任务开始：建立记录（任务 ID、提示词数量声明解析）。"""
    now = now or datetime.now()
    return TaskRecord(
        task_id=new_task_id(now),
        project_root=project_root,
        skill_name=skill_name,
        prompt=prompt,
        expected_count=steps_mod.parse_expected_count(prompt),
        schema=schema,
        started_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        started_ms=int(time.time() * 1000),
    )
