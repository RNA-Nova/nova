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
    script: Optional[str] = None  # 命中的步骤脚本名（参与判定的最小单位）
    workdir: str = ""  # 该条命令的工作目录（前导 cd 目标，无 cd 为会话 cwd）
    yaml_snapshots: Dict[str, str] = field(default_factory=dict)  # 路径 → 全文
    ok: Optional[bool] = None  # tool_execution_end 回填
    error_summary: Optional[str] = None  # 失败时的关键错误摘要（截断）
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
        """tool_call 捕获 bash 命令：步骤/脚本归属 + cd 工作目录 + yaml 全文快照。"""
        matched = steps_mod.match_script(command, self.schema)
        record = CommandRecord(
            tool_call_id=tool_call_id,
            command=command,
            step_id=matched[0] if matched else None,
            script=matched[1] if matched else None,
            workdir=steps_mod.resolve_command_workdir(command, self.project_root),
        )
        for rel in steps_mod.extract_yaml_paths(command):
            snapshot = _read_snapshot(self.project_root, rel)
            if snapshot is not None:
                record.yaml_snapshots[rel] = snapshot
        self.commands.append(record)
        return record

    def record_tool_end(
        self,
        tool_call_id: str,
        tool_name: str,
        is_error: bool,
        error_summary: Optional[str] = None,
    ) -> None:
        """tool_execution_end 回填结果；归属步骤的命令做产物盘点。

        盘点基准：该条命令的工作目录（前导 cd 目标；无 cd 为会话 cwd）。
        """
        record = next(
            (c for c in self.commands if c.tool_call_id == tool_call_id), None
        )
        if record is not None:
            record.ok = not is_error
            if is_error and error_summary:
                record.error_summary = error_summary
            if record.step_id and not is_error:
                record.outputs = inventory_step_outputs(
                    self.schema, record.step_id, record.workdir
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


def inventory_output(base_dir: str, rel: str) -> OutputInventory:
    """盘点单个期望产物（相对 ``base_dir`` 解析；文件：大小 + 行数；目录：清单）。"""
    inv = OutputInventory(path=rel)
    try:
        path = Path(base_dir) / rel
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
    schema: Dict[str, Any], step_id: str, base_dir: str
) -> List[OutputInventory]:
    """盘点某步骤的全部期望产物（schema 提取不全 → 只盘点已提取项）。"""
    for step in schema.get("steps", []):
        if step.get("step_id") == step_id:
            return [
                inventory_output(base_dir, rel)
                for rel in step.get("outputs", [])
                if _is_concrete_path(rel)
            ]
    return []


def summarize_error(result: Any, max_len: int = 160) -> str:
    """从 tool_execution_end 的 result 提取关键错误摘要（压缩空白 + 截断）。

    result 形态不固定（工具作者是第三方）：优先取 content 列表首个非空
    文本部件，其次字符串本体，最后 str() 兜底；提取不到返回空串。
    """
    text = ""
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for part in content:
            candidate = getattr(part, "text", None)
            if candidate is None and isinstance(part, dict):
                candidate = part.get("text")
            if isinstance(candidate, str) and candidate.strip():
                text = candidate
                break
    elif isinstance(result, str):
        text = result
    if not text and result is not None:
        text = str(result)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


# ---------------------------------------------------------------------------
# 四标准判定（§4.6）
# ---------------------------------------------------------------------------


def _is_file_like(rel: str) -> bool:
    """期望产物是否为文件型（basename 含扩展名）。"""
    return bool(_FILE_SUFFIX_RE.search(Path(rel).name))


# 文档中的占位写法（非具体产物，不参与缺项检查）：{var}、...、XXXX 连写
_PLACEHOLDER_RE = re.compile(r"\{|\}|\.\.\.|x{3,}", re.IGNORECASE)


def _is_concrete_path(rel: str) -> bool:
    """产物路径是否为具体路径（非文档占位示例）。"""
    return not _PLACEHOLDER_RE.search(rel)


def evaluate_criteria(
    record: TaskRecord,
    path_exists: Optional[Callable[[str, str], bool]] = None,
) -> List[Dict[str, Any]]:
    """四标准逐项判定（纯逻辑；``path_exists(base_dir, rel)`` 可注入便于单测）。

    返回 ``[{key, name, ok, note, neutral?}]``；整体成功 = 全部 ok。
    中性结果（未声明 / 无法核对 / 未检查）记 note + ``neutral=True``，
    不判失败；渲染为 ➖（✅ 只给真通过、❌ 只给真失败）。
    """
    if path_exists is None:

        def _default_exists(base_dir: str, rel: str) -> bool:
            return (Path(base_dir) / rel).exists()

        path_exists = _default_exists

    criteria: List[Dict[str, Any]] = []

    # 标准 1：步骤脚本全部最终成功（§4.6 口径）——只统计命中 schema 脚本
    # 清单的调用；辅助命令记流水不参与判定；同脚本前几次失败但最终成功
    # 视为经重试成功（⚠️ 标注尝试次数），仅最终未成功的脚本判 ❌。
    by_script: Dict[str, List[CommandRecord]] = {}
    for cmd in record.commands:
        if cmd.script:
            by_script.setdefault(cmd.script, []).append(cmd)
    retried: List[str] = []
    failed: List[str] = []
    for script, cmds in by_script.items():
        last = cmds[-1]
        if last.ok is True:
            if any(c.ok is False for c in cmds[:-1]):
                retried.append(f"{script} 经 {len(cmds)} 次尝试后成功 ⚠️")
        else:
            summary = last.error_summary or "执行失败"
            failed.append(f"{script}（{summary}）")
    if failed:
        note = "最终未成功：" + "；".join(failed)
        if retried:
            note += "；" + "；".join(retried)
    elif retried:
        note = "全部最终成功；" + "；".join(retried)
    elif by_script:
        note = f"{len(by_script)} 个步骤脚本全部成功"
    else:
        note = "未检查（未捕获到步骤脚本调用）"
    criteria.append(
        {
            "key": "tools_ok",
            "name": "工具全部调用成功",
            "ok": not failed,
            "note": note,
            # 无脚本调用可判时为中性（不渲染 ✅/❌）
            "neutral": not failed and not by_script,
        }
    )

    # 标准 2：产物完整性——执行过的步骤对照期望清单不缺项（仅文件型产物）；
    # 产物路径相对于该步骤各命令的工作目录（cd 目标）解析，任一命中即存在。
    step_workdirs: Dict[str, List[str]] = {}
    for cmd in record.commands:
        if cmd.step_id:
            dirs = step_workdirs.setdefault(cmd.step_id, [])
            if cmd.workdir not in dirs:
                dirs.append(cmd.workdir)
    expected: List[str] = []
    expected_bases: Dict[str, List[str]] = {}
    for step in record.schema.get("steps", []):
        bases = step_workdirs.get(step.get("step_id"))
        if not bases:
            continue
        for rel in step.get("outputs", []):
            if _is_file_like(rel) and _is_concrete_path(rel) and rel not in expected:
                expected.append(rel)
                expected_bases[rel] = bases
    missing = [
        rel
        for rel in expected
        if not any(path_exists(base, rel) for base in expected_bases[rel])
    ]
    if not expected:
        criteria.append(
            {
                "key": "outputs_complete",
                "name": "产物完整性",
                "ok": True,
                "note": "未检查（schema 未提取到可核对的期望产物）",
                "neutral": True,
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

    # 标准 3：终产物数量符合提示词要求。终产物来自 schema 的 final_product
    # （绑定瞬间从步骤文档识别）；文件定位依次尝试各命令工作目录与会话 cwd。
    if record.expected_count is None:
        criteria.append(
            {
                "key": "count_match",
                "name": "终产物数量符合要求",
                "ok": True,
                "note": "未声明（提示词中未解析到数量要求）",
                "neutral": True,
            }
        )
    else:
        final_product = record.schema.get("final_product")
        if not final_product:
            criteria.append(
                {
                    "key": "count_match",
                    "name": "终产物数量符合要求",
                    "ok": True,
                    "note": "未声明（schema 未提取到终产物）",
                    "neutral": True,
                }
            )
        else:
            bases = [record.project_root]
            for cmd in reversed(record.commands):
                if cmd.workdir and cmd.workdir not in bases:
                    bases.insert(0, cmd.workdir)
            measured: Optional[int] = None
            measured_at = ""
            for base in bases:
                inv = inventory_output(base, final_product)
                if inv.exists and inv.data_lines is not None:
                    measured = inv.data_lines
                    measured_at = str(Path(base) / final_product)
                    break
            if measured is None:
                criteria.append(
                    {
                        "key": "count_match",
                        "name": "终产物数量符合要求",
                        "ok": True,
                        "note": f"无法核对（未找到终产物 {final_product}）",
                        "neutral": True,
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
                            f"{measured_at} 数据行 {measured} == 要求 {record.expected_count}"
                            if ok
                            else f"{measured_at} 数据行 {measured} != 要求 {record.expected_count}"
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
        # 三态图标：✅ 真通过 / ❌ 真失败 / ➖ 中性（未声明/无法核对/未检查）
        if not c["ok"]:
            mark = "❌"
        elif c.get("neutral"):
            mark = "➖"
        else:
            mark = "✅"
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
            text = cmd.command
            if len(text) > 200:
                text = text[:199] + "…"
            lines.append(f"- 命令失败：`{text}`")
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
