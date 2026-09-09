"""skill 候选发现、资格判据与步骤 schema 现场推导（需求 §4.2 / §4.3）。

资格判据（"多步骤长任务"标准，三条同时满足）：
1. skill 根下存在 ``docs/`` 目录；
2. 其中含 ≥2 个编号步骤文档（``00_*.md`` / ``01_*.md`` ……）；
3. 步骤文档中可找到可执行脚本（``.py``）引用。

schema 推导为纯启发式正则解析，提取不全只影响"少检查"，不影响记录
（观测优先原则）。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 提取正则（全部领域无关）
# ---------------------------------------------------------------------------

# 编号步骤文档：00_init.md / 04_generate.md / 05a2_extra.md → ("04", "04_generate")
_STEP_DOC_RE = re.compile(r"^(\d+[a-z0-9]*)[_-](.+)\.md$", re.IGNORECASE)
# 步骤文档标题：首个 markdown 一级/二级标题
_DOC_TITLE_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.MULTILINE)
# 脚本引用：scripts/xxx.py（含子目录）
_SCRIPT_RE = re.compile(r"scripts/[\w.\-/]+\.py")
# 配置引用：任意 *.yaml / *.yml 路径
_CONFIG_RE = re.compile(r"[\w.\-/]+\.ya?ml")
# 产物路径引用：outputs/...（目录或文件）
_OUTPUT_RE = re.compile(r"outputs/[\w.\-/]+")
# bash 命令行中的 yaml 路径 token（允许引号包裹）
_CMD_YAML_RE = re.compile(r"[\"']?([^\s\"']+\.ya?ml)[\"']?")

# 提示词中的数量声明：top 20 / top-20 / top_20 / 20 条 / 20 candidates
_COUNT_PATTERNS = [
    re.compile(r"\btop[\s_\-]?(\d+)\b", re.IGNORECASE),
    re.compile(r"(\d+)\s*条"),
    re.compile(r"\b(\d+)\s+candidates?\b", re.IGNORECASE),
]

# 判据常量
MIN_STEP_DOCS = 2


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SkillCandidate:
    """扫描到的 skill 候选（合格与否均入列，理由供提示用户）。"""

    name: str  # SKILL.md frontmatter name 或目录名
    dir: str  # skill 根目录绝对路径
    source: str  # 来源标签（project / user / package）
    qualified: bool = False
    reasons: List[str] = field(default_factory=list)  # 不合格理由（合格时为空）
    step_docs: List[str] = field(default_factory=list)  # 编号步骤文档文件名


@dataclass
class StepSchema:
    """单个步骤的推导结果。"""

    step_id: str  # 编号前缀（如 "04"）
    doc_name: str  # 文档文件名（如 "04_generate.md"）
    title: str  # 文档首个标题（提取不到则为 doc_name）
    scripts: List[str] = field(default_factory=list)  # 脚本 basename 列表
    configs: List[str] = field(default_factory=list)  # 配置路径引用
    outputs: List[str] = field(default_factory=list)  # 产物路径引用


# ---------------------------------------------------------------------------
# skill 根目录发现
# ---------------------------------------------------------------------------


def _skill_name_from_dir(skill_dir: Path) -> str:
    """从 SKILL.md frontmatter 提取 name；缺失时回退目录名。"""
    skill_md = skill_dir / "SKILL.md"
    try:
        if skill_md.is_file():
            head = skill_md.read_text(encoding="utf-8", errors="replace")[:4096]
            m = re.search(r"^name:\s*[\"']?([\w.-]+)[\"']?\s*$", head, re.MULTILINE)
            if m:
                return m.group(1)
    except OSError:
        pass
    return skill_dir.name


def _iter_skill_dirs(root: Path) -> List[Path]:
    """列出技能根目录下的全部 skill 目录（含 SKILL.md 的一级子目录）。"""
    try:
        if not root.is_dir():
            return []
        return sorted(
            p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        )
    except OSError:
        return []


def _ancestors_up_to_git_root(cwd: Path) -> List[Path]:
    """从 cwd 向上收集祖先目录（到 git root 为止，含两端），近者优先。"""
    results: List[Path] = []
    current = cwd
    home = Path.home()
    while True:
        results.append(current)
        if (current / ".git").exists() or current == current.parent or current == home:
            break
        current = current.parent
    return results


def default_scan_roots(cwd: str) -> List[Tuple[Path, str]]:
    """标准候选扫描位置（需求 §4.2）：

    - 项目散养：``<cwd>/.agents/skills``（含祖先到 git root）与
      ``<cwd>/.nova/backend/skills``；
    - 用户散养：``~/.agents/skills`` 与 ``~/.nova/agent/backend/skills``；
    - 已安装包：``~/.nova/agent/packages/*/backend/skills`` 与
      ``~/.nova/agent/packages/*/skills``。

    返回 ``(根目录, 来源标签)`` 列表；不存在的目录由调用方跳过。
    """
    roots: List[Tuple[Path, str]] = []
    base = Path(cwd).resolve()
    for ancestor in _ancestors_up_to_git_root(base):
        roots.append((ancestor / ".agents" / "skills", "project"))
    roots.append((base / ".nova" / "backend" / "skills", "project"))
    home = Path.home()
    roots.append((home / ".agents" / "skills", "user"))
    nova_agent = home / ".nova" / "agent"
    roots.append((nova_agent / "backend" / "skills", "user"))
    packages_dir = nova_agent / "packages"
    try:
        if packages_dir.is_dir():
            for pkg in sorted(packages_dir.iterdir()):
                if pkg.is_dir():
                    roots.append((pkg / "backend" / "skills", "package"))
                    roots.append((pkg / "skills", "package"))
    except OSError:
        pass
    return roots


# ---------------------------------------------------------------------------
# 资格判据
# ---------------------------------------------------------------------------


def find_step_docs(skill_dir: Path) -> List[Tuple[str, str]]:
    """列出 ``docs/`` 下的编号步骤文档，返回 ``[(编号, 文件名)]`` 按编号排序。"""
    docs_dir = skill_dir / "docs"
    found: List[Tuple[str, str]] = []
    try:
        if not docs_dir.is_dir():
            return []
        for entry in sorted(docs_dir.iterdir()):
            if not entry.is_file():
                continue
            m = _STEP_DOC_RE.match(entry.name)
            if m:
                found.append((m.group(1), entry.name))
    except OSError:
        return []
    found.sort(key=lambda item: item[0])
    return found


def _read_doc_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("读取步骤文档失败 %s: %s", path, exc)
        return ""


def qualify_skill(skill_dir: Path) -> Tuple[bool, List[str], List[str]]:
    """资格判据：返回 ``(是否合格, 不合格理由, 编号步骤文档文件名列表)``。"""
    reasons: List[str] = []
    docs_dir = skill_dir / "docs"
    if not docs_dir.is_dir():
        reasons.append("缺少 docs/ 目录")
        return False, reasons, []
    step_docs = find_step_docs(skill_dir)
    if len(step_docs) < MIN_STEP_DOCS:
        reasons.append(f"编号步骤文档不足（{len(step_docs)} < {MIN_STEP_DOCS}）")
    has_script = any(
        _SCRIPT_RE.search(_read_doc_text(docs_dir / name)) for _, name in step_docs
    )
    if step_docs and not has_script:
        reasons.append("步骤文档中未找到可执行脚本（.py）引用")
    return not reasons, reasons, [name for _, name in step_docs]


def scan_candidates(
    roots: Optional[List[Tuple[Path, str]]] = None,
    cwd: Optional[str] = None,
) -> List[SkillCandidate]:
    """扫描候选 skill 并按资格判据标注（同名去重，先发现者优先）。

    ``roots`` 缺省时按 ``cwd`` 的标准扫描位置推导。
    """
    if roots is None:
        roots = default_scan_roots(cwd or ".")
    seen: set = set()
    candidates: List[SkillCandidate] = []
    for root, source in roots:
        for skill_dir in _iter_skill_dirs(Path(root)):
            real = str(skill_dir.resolve())
            if real in seen:
                continue
            seen.add(real)
            qualified, reasons, step_docs = qualify_skill(skill_dir)
            candidates.append(
                SkillCandidate(
                    name=_skill_name_from_dir(skill_dir),
                    dir=real,
                    source=source,
                    qualified=qualified,
                    reasons=reasons,
                    step_docs=step_docs,
                )
            )
    return candidates


def find_mentioned_skills(text: str, candidates: List[SkillCandidate]) -> List[str]:
    """解析输入文本中点名的 skill 名（候选名或目录名，词边界匹配）。"""
    mentioned: List[str] = []
    lowered = text.lower()
    for cand in candidates:
        names = {cand.name.lower(), Path(cand.dir).name.lower()}
        for name in names:
            if not name:
                continue
            pattern = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
            if pattern.search(lowered):
                mentioned.append(cand.name)
                break
    return mentioned


def parse_expected_count(text: str) -> Optional[int]:
    """从提示词解析终产物数量要求（如 "top 20" / "20 条"）；解析不到返回 None。"""
    for pattern in _COUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                value = int(m.group(1))
            except ValueError:
                continue
            if value > 0:
                return value
    return None


# ---------------------------------------------------------------------------
# schema 推导（§4.3）
# ---------------------------------------------------------------------------


def _dedup(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def derive_step_schema(step_id: str, doc_name: str, text: str) -> StepSchema:
    """从单篇步骤文档文本推导步骤 schema。"""
    title_match = _DOC_TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else doc_name
    scripts = _dedup([Path(m.group(0)).name for m in _SCRIPT_RE.finditer(text)])
    configs = _dedup([m.group(0) for m in _CONFIG_RE.finditer(text)])
    outputs = _dedup([m.group(0).rstrip(".,;)") for m in _OUTPUT_RE.finditer(text)])
    return StepSchema(
        step_id=step_id,
        doc_name=doc_name,
        title=title,
        scripts=scripts,
        configs=configs,
        outputs=outputs,
    )


# 终产物识别的通用线索词（文件名词元，领域无关）
_FINAL_PRODUCT_CLUES = ("final", "ranked", "ranking", "top", "result", "results")
# 可计数行数的终产物后缀
_FINAL_COUNTABLE_SUFFIXES = (".csv", ".tsv")
# 文档中的占位写法（非具体产物）：{var}、...、XXXX 连写
_PLACEHOLDER_RE = re.compile(r"\{|\}|\.\.\.|x{3,}", re.IGNORECASE)


def identify_final_product(step_dicts: List[Dict[str, Any]]) -> Optional[str]:
    """从各步骤的产物声明中识别"最终产物"（供数量符合性核对）。

    领域无关启发式：文件名词元含 final/ranked/top/result 等通用线索的
    可计数文件（csv/tsv）为强候选，同档内靠后步骤优先；全部无线索时
    退化为最靠后步骤的可计数产物。识别不到返回 None（记"未声明"，
    属中性结果而非失败）。
    """
    best: Optional[Tuple[int, int, int, str]] = None  # (线索分, 步骤序号, 声明序, 路径)
    for index, step in enumerate(step_dicts):
        for order, rel in enumerate(step.get("outputs", [])):
            name = Path(rel).name.lower()
            if Path(name).suffix not in _FINAL_COUNTABLE_SUFFIXES:
                continue
            if _PLACEHOLDER_RE.search(rel):
                continue
            # 文件型（basename 含扩展名）且含线索词元（下划线/连字符分隔）
            tokens = re.split(r"[^a-z0-9]+", Path(name).stem)
            clue = 1 if any(t in _FINAL_PRODUCT_CLUES for t in tokens) else 0
            # 排序键：线索分主导 → 步骤序号 → 声明顺序（靠后优先）
            key = (clue, index, order)
            if best is None or key > best[:3]:
                best = (clue, index, order, rel)
    return best[3] if best else None


def derive_skill_schema(skill_name: str, skill_dir: str) -> Dict[str, Any]:
    """绑定瞬间解析全部步骤文档，推导 skill 级 schema（§4.3）。

    返回可 JSON 序列化的 dict；提取失败降级为空清单，绝不抛错。
    """
    root = Path(skill_dir)
    steps: List[StepSchema] = []
    for step_id, doc_name in find_step_docs(root):
        text = _read_doc_text(root / "docs" / doc_name)
        steps.append(derive_step_schema(step_id, doc_name, text))
    final_outputs = steps[-1].outputs if steps else []
    step_dicts = [
        {
            "step_id": s.step_id,
            "doc_name": s.doc_name,
            "title": s.title,
            "scripts": s.scripts,
            "configs": s.configs,
            "outputs": s.outputs,
        }
        for s in steps
    ]
    return {
        "version": 1,
        "skill_name": skill_name,
        "skill_dir": str(root),
        "steps": step_dicts,
        "final_outputs": final_outputs,
        "final_product": identify_final_product(step_dicts),
    }


# ---------------------------------------------------------------------------
# 记录侧消费：步骤归属、yaml 提取与 cd 目标解析
# ---------------------------------------------------------------------------


def match_script(command: str, schema: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """bash 命令命中某步骤脚本名 → 返回 ``(步骤编号, 脚本名)``；未命中 None。"""
    for step in schema.get("steps", []):
        for script in step.get("scripts", []):
            if script and script in command:
                return step.get("step_id"), script
    return None


def attribute_command(command: str, schema: Dict[str, Any]) -> Optional[str]:
    """bash 命令命中某步骤脚本名 → 归该步；未命中返回 None。"""
    matched = match_script(command, schema)
    return matched[0] if matched else None


# 命令前导的 cd 目标：`cd <dir> && ...` / `cd "<dir>" ; ...`（常见形态；
# 含变量/命令替换的目录不可静态解析，按无 cd 处理）
# 前导变量赋值段：`VAR=value` / `export VAR=value`，段间以 &&/;/换行分隔。
# 仅接受简单字面量（含引号包裹）；值含 $ / 反引号的不收录（保守不展开）。
_ASSIGNMENT_RE = re.compile(
    r"\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|\n]+)\s*(?:&&|;|\n|$)"
)
# 赋值段之后的前导 cd：`cd <dir>` 以 &&/;/换行/结尾收束
_CD_PREFIX_RE = re.compile(
    r"\s*cd\s+(?P<dir>\"[^\"]+\"|'[^']+'|[^\s;&|\n]+)\s*(?:&&|;|\n|$)"
)
# 变量引用形态：$VAR / ${VAR}
_VAR_REF_RE = re.compile(r"^\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}?$")


def _extract_leading_assignments(command: str) -> Tuple[Dict[str, str], int]:
    """解析命令开头连续的变量赋值段，返回 ``(局部变量表, 赋值段结束位置)``。"""
    table: Dict[str, str] = {}
    pos = 0
    while True:
        m = _ASSIGNMENT_RE.match(command, pos)
        if not m:
            break
        value = m.group("value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if "$" not in value and "`" not in value:
            table[m.group("name")] = value
        pos = m.end()
    return table, pos


def extract_cd_target(command: str) -> Optional[str]:
    """解析命令的 cd 目标目录；不可解析返回 None。

    支持前导变量赋值代入：``TASK_DIR=/a/b && cd "$TASK_DIR" && ...``（含
    ``export`` 前缀与换行分隔形态）；变量查不到表、值含 $()/递归引用等
    不可静态解析的形态一律回退 None（宁可中性不可猜错）。
    """
    variables, pos = _extract_leading_assignments(command)
    m = _CD_PREFIX_RE.match(command, pos)
    if not m:
        return None
    raw = m.group("dir")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        raw = raw[1:-1]
    # 变量引用：查前导赋值表，查不到不可解析
    var_ref = _VAR_REF_RE.match(raw)
    if var_ref:
        resolved = variables.get(var_ref.group("name"))
        if resolved is None:
            return None
        raw = resolved
    # 其余变量/命令替换/拼接形式不做静态解析（按会话 cwd 处理）
    if "$" in raw or "`" in raw:
        return None
    return os.path.expanduser(raw)


def resolve_command_workdir(command: str, cwd: str) -> str:
    """该条 bash 命令的工作目录：cd 目标（绝对化）或会话 cwd。"""
    target = extract_cd_target(command)
    if target is None:
        return str(Path(cwd))
    path = Path(target)
    if not path.is_absolute():
        path = Path(cwd) / path
    return str(path)


def extract_yaml_paths(command: str) -> List[str]:
    """提取 bash 命令行中引用的 yaml/yml 路径 token（去重，保序）。"""
    return _dedup([m.group(1) for m in _CMD_YAML_RE.finditer(command)])
