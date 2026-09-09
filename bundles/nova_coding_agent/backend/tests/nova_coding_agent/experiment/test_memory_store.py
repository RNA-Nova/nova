"""memory_store.py 测试：骨架初始化、绑定/schema 读写、任务页与 INDEX、召回组装。"""

import json
from pathlib import Path

from nova_coding_agent.experiment import memory_store


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 骨架初始化
# ---------------------------------------------------------------------------


def test_ensure_skeleton_creates_dirs_and_templates(tmp_path):
    assert memory_store.ensure_skeleton(str(tmp_path))
    root = tmp_path / "memory"
    for name in [
        "_templates",
        "tasks",
        "learnings",
        "playbooks",
        "discoveries",
        "hypotheses",
        "literature",
        "entities",
        "system",
    ]:
        assert (root / name).is_dir(), name
    assert (root / "INDEX.md").is_file()
    assert (root / "README.md").is_file()
    assert (root / "_templates" / "task.md").is_file()
    assert (root / "_templates" / "learning.md").is_file()


def test_ensure_skeleton_idempotent_no_overwrite(tmp_path):
    memory_store.ensure_skeleton(str(tmp_path))
    index = tmp_path / "memory" / "INDEX.md"
    index.write_text("# 用户改过\n", encoding="utf-8")
    memory_store.ensure_skeleton(str(tmp_path))
    assert _read(index) == "# 用户改过\n"


# ---------------------------------------------------------------------------
# 绑定与 schema 读写
# ---------------------------------------------------------------------------


def test_binding_roundtrip(tmp_path):
    root = str(tmp_path)
    assert memory_store.read_binding(root) is None
    binding = {"skill": "demo", "skill_dir": "/x/y", "bound_at": "2026-01-01"}
    assert memory_store.write_binding(root, binding)
    assert memory_store.read_binding(root) == binding


def test_schema_roundtrip(tmp_path):
    root = str(tmp_path)
    schema = {"skill_name": "demo", "steps": [{"step_id": "00"}], "final_outputs": []}
    assert memory_store.write_schema(root, schema)
    assert memory_store.read_schema(root) == schema
    raw = json.loads(_read(tmp_path / "memory" / "system" / "skill_schema.json"))
    assert raw["skill_name"] == "demo"


# ---------------------------------------------------------------------------
# 任务页与 INDEX
# ---------------------------------------------------------------------------


def test_write_task_page_and_update_index(tmp_path):
    root = str(tmp_path)
    memory_store.ensure_skeleton(root)
    path = memory_store.write_task_page(root, "task-1", "# 任务页内容\n")
    assert path is not None and Path(path).is_file()

    assert memory_store.update_index(
        root, "task-1", "task-1 — demo（2026-01-01，completed）"
    )
    index = _read(tmp_path / "memory" / "INDEX.md")
    assert "- [[tasks/task-1|task-1 — demo（2026-01-01，completed）]]" in index
    # 插入位置：任务区标题之后、Dataview 围栏块之后
    section = index.index("## 📋 任务")
    fence_end = index.index("```", index.index("```", section) + 3)
    entry = index.index("[[tasks/task-1|")
    assert section < fence_end < entry

    # 幂等：同 task_id 不重复插入
    assert memory_store.update_index(root, "task-1", "task-1 — x")
    assert _read(tmp_path / "memory" / "INDEX.md").count("[[tasks/task-1|") == 1


def test_update_index_without_anchor_appends_section(tmp_path):
    root = str(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "INDEX.md").write_text("# 空索引\n", encoding="utf-8")
    assert memory_store.update_index(root, "task-9", "task-9 — demo（d，completed）")
    index = _read(tmp_path / "memory" / "INDEX.md")
    assert "## 📋 任务" in index
    assert "[[tasks/task-9|" in index


# ---------------------------------------------------------------------------
# learnings 与召回内容
# ---------------------------------------------------------------------------


def _write_learning(learnings_dir: Path, stem: str, severity: str, title: str) -> None:
    learnings_dir.mkdir(parents=True, exist_ok=True)
    (learnings_dir / f"{stem}.md").write_text(
        f"---\ntype: semantic\ntitle: {title}\nseverity: {severity}\n---\n\n# {title}\n",
        encoding="utf-8",
    )


def test_collect_learnings_sorted_by_severity(tmp_path):
    learnings = tmp_path / "memory" / "learnings"
    _write_learning(learnings, "b-low", "low", "低严重度坑")
    _write_learning(learnings, "a-high", "high", "高严重度坑")
    _write_learning(learnings, "c-mid", "medium", "中严重度坑")
    items = memory_store.collect_learnings(str(tmp_path))
    assert [i["severity"] for i in items] == ["high", "medium", "low"]
    assert items[0]["title"] == "高严重度坑"


def test_collect_learnings_empty(tmp_path):
    assert memory_store.collect_learnings(str(tmp_path)) == []


def test_build_recall_content_with_and_without_learnings(tmp_path):
    root = str(tmp_path)
    # 假 skill 步骤文档
    docs = tmp_path / "skill" / "docs"
    docs.mkdir(parents=True)
    (docs / "00_prepare.md").write_text(
        "# Step 0: Prepare\n\n准备输入数据。\n", encoding="utf-8"
    )
    schema = {
        "skill_name": "demo",
        "skill_dir": str(tmp_path / "skill"),
        "steps": [
            {"step_id": "00", "doc_name": "00_prepare.md", "title": "Step 0: Prepare"}
        ],
    }
    # 无 learnings：只含 skill 摘要
    content = memory_store.build_recall_content(root, schema)
    assert "Bound skill: demo" in content
    assert "Step 00" in content
    assert "Learnings" not in content
    # 有 learnings：附带按严重度排序的摘要
    _write_learning(tmp_path / "memory" / "learnings", "x", "high", "配置遮蔽坑")
    content = memory_store.build_recall_content(root, schema)
    assert "Learnings" in content
    assert "配置遮蔽坑" in content
    assert "🔴" in content
