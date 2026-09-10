"""memory/ 骨架初始化、绑定/schema/任务页读写与召回内容组装（需求 §4.4 / §4.7）。

记忆跟随项目：一切数据落在 ``<项目>/memory/`` 下（可 git 追踪、Obsidian
兼容）；绑定与 schema 落 ``memory/system/``。所有读写失败 catch 后降级，
绝不中断用户任务。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MEMORY_DIR_NAME = "memory"
SYSTEM_DIR_NAME = "system"
BINDING_FILE_NAME = "binding.json"
STATE_FILE_NAME = "state.json"
SCHEMA_FILE_NAME = "skill_schema.json"
TASKS_DIR_NAME = "tasks"
LEARNINGS_DIR_NAME = "learnings"
INDEX_FILE_NAME = "INDEX.md"

# 骨架子目录（§4.4）
_SKELETON_DIRS = [
    "_templates",
    "tasks",
    "learnings",
    "playbooks",
    "discoveries",
    "hypotheses",
    "literature",
    "entities",
    "system",
]

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# learnings frontmatter 提取（轻量正则，不依赖 yaml 解析器——文件为人写，
# 格式不齐时降级为整行摘要）
_FM_SEVERITY_RE = re.compile(r"^severity:\s*(\w+)", re.MULTILINE)
_FM_TITLE_RE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

# 严重度排序权重（高 → 低）
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}

# 召回注入的 learnings 上限
_MAX_RECALL_LEARNINGS = 20


def memory_root(project_root: str) -> Path:
    """项目记忆根目录。"""
    return Path(project_root) / MEMORY_DIR_NAME


# ---------------------------------------------------------------------------
# 骨架初始化（§4.4）
# ---------------------------------------------------------------------------


def ensure_skeleton(project_root: str) -> bool:
    """首次激活时创建 memory 骨架（模板为 bundle 自带领域无关资源）。

    已存在的文件/目录一律不覆盖（幂等）；失败记日志返回 False。
    """
    root = memory_root(project_root)
    try:
        for name in _SKELETON_DIRS:
            (root / name).mkdir(parents=True, exist_ok=True)
        if _TEMPLATES_DIR.is_dir():
            for src in sorted(_TEMPLATES_DIR.rglob("*")):
                if not src.is_file():
                    continue
                dst = root / src.relative_to(_TEMPLATES_DIR)
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        return True
    except OSError as exc:
        logger.warning("experiment: memory 骨架初始化失败 %s: %s", root, exc)
        return False


# ---------------------------------------------------------------------------
# 绑定与 schema 读写
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError) as exc:
        logger.warning("experiment: 读取 %s 失败: %s", path, exc)
    return None


def _write_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except (OSError, ValueError) as exc:
        logger.warning("experiment: 写入 %s 失败: %s", path, exc)
        return False


def read_binding(project_root: str) -> Optional[Dict[str, Any]]:
    """读取项目级绑定（``memory/system/binding.json``）。"""
    return _read_json(memory_root(project_root) / SYSTEM_DIR_NAME / BINDING_FILE_NAME)


def write_binding(project_root: str, binding: Dict[str, Any]) -> bool:
    """写入项目级绑定（按项目持久化，后续会话自动沿用）。"""
    return _write_json(
        memory_root(project_root) / SYSTEM_DIR_NAME / BINDING_FILE_NAME, binding
    )


def read_schema(project_root: str) -> Optional[Dict[str, Any]]:
    """读取已推导的步骤 schema（``memory/system/skill_schema.json``）。"""
    return _read_json(memory_root(project_root) / SYSTEM_DIR_NAME / SCHEMA_FILE_NAME)


def write_schema(project_root: str, schema: Dict[str, Any]) -> bool:
    """落盘步骤 schema，后续记录/判定统一消费该文件。"""
    return _write_json(
        memory_root(project_root) / SYSTEM_DIR_NAME / SCHEMA_FILE_NAME, schema
    )


def read_recall_enabled(project_root: str) -> Optional[bool]:
    """读取项目级召回档位（``memory/system/state.json``；缺省 None=未设置）。"""
    state = _read_json(memory_root(project_root) / SYSTEM_DIR_NAME / STATE_FILE_NAME)
    if isinstance(state, dict) and isinstance(state.get("recall_enabled"), bool):
        return state["recall_enabled"]
    return None


def write_recall_enabled(project_root: str, enabled: bool) -> bool:
    """写入项目级召回档位（§5.1 双开关按项目持久化）。"""
    return _write_json(
        memory_root(project_root) / SYSTEM_DIR_NAME / STATE_FILE_NAME,
        {"recall_enabled": bool(enabled)},
    )


# ---------------------------------------------------------------------------
# 任务页与 INDEX 更新
# ---------------------------------------------------------------------------


def write_task_page(project_root: str, task_id: str, content: str) -> Optional[str]:
    """写入 ``memory/tasks/<task_id>.md``，返回文件路径（失败返回 None）。"""
    path = memory_root(project_root) / TASKS_DIR_NAME / f"{task_id}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)
    except OSError as exc:
        logger.warning("experiment: 任务页写入失败 %s: %s", path, exc)
        return None


def update_index(project_root: str, task_id: str, summary_line: str) -> bool:
    """在 INDEX.md 的任务区插入一行任务链接（幂等：同 task_id 不重复插）。

    找不到任务区锚点时在文件末尾追加小节；文件不存在则新建。
    """
    path = memory_root(project_root) / INDEX_FILE_NAME
    entry_line = f"- [[tasks/{task_id}|{summary_line}]]"
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        logger.warning("experiment: INDEX 读取失败 %s: %s", path, exc)
        text = ""
    if f"tasks/{task_id}|" in text:
        return True

    lines = text.splitlines()
    # 锚点：任务区标题（## 开头且含"任务"二字）
    anchor_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("##") and "任务" in line),
        None,
    )
    if anchor_idx is None:
        lines += ["", "## 📋 任务（情景记忆）", "", entry_line, ""]
    else:
        # 插到锚点后第一个 ``` 围栏块结束之后（跳过 Dataview 块）
        insert_at = anchor_idx + 1
        in_fence = False
        i = insert_at
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("```"):
                if in_fence:
                    insert_at = i + 1
                    break
                in_fence = True
            i += 1
        else:
            insert_at = anchor_idx + 1
        lines[insert_at:insert_at] = ["", entry_line]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("experiment: INDEX 写入失败 %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# 召回内容组装（§4.7）
# ---------------------------------------------------------------------------


def _summarize_step_doc(path: Path, max_chars: int = 200) -> str:
    """步骤文档摘要：首个标题 + 首段正文（截断）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    title = ""
    m = _H1_RE.search(text)
    if m:
        title = m.group(1).strip()
    # 首段正文：标题之后第一个非空非引用非分隔行
    body = ""
    for line in text[m.end() if m else 0 :].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "---", "```")):
            continue
        body = stripped
        break
    summary = title
    if body:
        summary = f"{title} — {body}" if title else body
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return summary


def _parse_learning(path: Path) -> Dict[str, str]:
    """解析单篇 learning 的 frontmatter 摘要（失败降级为文件名）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        text = ""
    severity_m = _FM_SEVERITY_RE.search(text)
    title_m = _FM_TITLE_RE.search(text)
    h1_m = _H1_RE.search(text)
    severity = severity_m.group(1).lower() if severity_m else "medium"
    if severity not in _SEVERITY_ORDER:
        severity = "medium"
    title = (
        title_m.group(1).strip()
        if title_m
        else (h1_m.group(1).strip() if h1_m else path.stem)
    )
    return {"severity": severity, "title": title, "file": path.stem}


def collect_learnings(project_root: str) -> List[Dict[str, str]]:
    """收集全部 learnings 的一行式摘要，按严重度排序（高在前）。"""
    learnings_dir = memory_root(project_root) / LEARNINGS_DIR_NAME
    items: List[Dict[str, str]] = []
    try:
        if learnings_dir.is_dir():
            for path in sorted(learnings_dir.glob("*.md")):
                items.append(_parse_learning(path))
    except OSError as exc:
        logger.warning("experiment: learnings 收集失败 %s: %s", learnings_dir, exc)
    items.sort(key=lambda item: (_SEVERITY_ORDER[item["severity"]], item["file"]))
    return items


def build_recall_content(project_root: str, schema: Dict[str, Any]) -> str:
    """组装召回注入内容：绑定 skill 步骤文档摘要 + learnings 一行式摘要。

    learnings 为空时只含 skill 摘要（§4.7）；skill 文档不可读时降级为
    schema 中的标题清单。
    """
    skill_name = schema.get("skill_name", "")
    skill_dir = Path(schema.get("skill_dir", ""))
    lines: List[str] = [
        "[EXPERIMENT MEMORY]",
        f"Bound skill: {skill_name}",
        "",
        "## Skill step summary (follow these steps in order)",
    ]
    for step in schema.get("steps", []):
        doc_path = skill_dir / "docs" / step.get("doc_name", "")
        summary = _summarize_step_doc(doc_path) or step.get("title", "")
        lines.append(f"- Step {step.get('step_id')}: {summary}")
    if not schema.get("steps"):
        lines.append("- (no step schema available)")

    learnings = collect_learnings(project_root)
    if learnings:
        lines += ["", "## Learnings from previous tasks (apply these rules)"]
        for item in learnings[:_MAX_RECALL_LEARNINGS]:
            mark = _SEVERITY_MARK.get(item["severity"], "🟡")
            lines.append(f"- {mark} [{item['severity']}] {item['title']}")
    return "\n".join(lines)
