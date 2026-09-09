"""steps.py 测试：候选发现、资格判据、schema 推导、命令归属与数量解析。

全部用临时目录构造假 skill 夹具，不依赖真实会话与真实 skill。
"""

from pathlib import Path

from nova_coding_agent.experiment import steps


def _make_skill(
    root: Path,
    name: str = "demo-skill",
    docs: dict = None,
    with_skill_md: bool = True,
) -> Path:
    """构造一个 skill 目录夹具。``docs`` 为 {文件名: 内容}。"""
    skill_dir = root / name
    (skill_dir / "docs").mkdir(parents=True, exist_ok=True)
    if with_skill_md:
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 测试用假 skill\n---\n\n# {name}\n",
            encoding="utf-8",
        )
    for doc_name, content in (docs or {}).items():
        (skill_dir / "docs" / doc_name).write_text(content, encoding="utf-8")
    return skill_dir


def _qualified_docs() -> dict:
    return {
        "00_prepare.md": (
            "# Step 0: Prepare\n\n"
            "Run `scripts/prepare.py` with `config/prep.yaml`.\n"
            "Produces `outputs/00_prepare/items.csv`.\n"
        ),
        "01_run.md": (
            "# Step 1: Run\n\n"
            "Run `scripts/run.py --config config/run.yaml`.\n"
            "Produces `outputs/01_run/result.csv` and `outputs/01_run/logs/`.\n"
        ),
    }


# ---------------------------------------------------------------------------
# 步骤文档发现与资格判据
# ---------------------------------------------------------------------------


def test_find_step_docs_sorted_by_number(tmp_path):
    skill = _make_skill(
        tmp_path,
        docs={
            "01_run.md": "x",
            "00_prepare.md": "x",
            "notes.md": "not numbered",
            "05a2_extra.md": "x",
        },
    )
    found = steps.find_step_docs(skill)
    assert found == [
        ("00", "00_prepare.md"),
        ("01", "01_run.md"),
        ("05a2", "05a2_extra.md"),
    ]


def test_qualify_skill_ok(tmp_path):
    skill = _make_skill(tmp_path, docs=_qualified_docs())
    qualified, reasons, doc_names = steps.qualify_skill(skill)
    assert qualified
    assert reasons == []
    assert doc_names == ["00_prepare.md", "01_run.md"]


def test_qualify_skill_missing_docs_dir(tmp_path):
    skill = _make_skill(tmp_path, docs={})
    # 移除 docs/ 整个目录
    for child in (skill / "docs").iterdir():
        child.unlink()
    (skill / "docs").rmdir()
    qualified, reasons, _ = steps.qualify_skill(skill)
    assert not qualified
    assert any("docs/" in r for r in reasons)


def test_qualify_skill_too_few_step_docs(tmp_path):
    skill = _make_skill(tmp_path, docs={"00_only.md": "Run scripts/x.py"})
    qualified, reasons, _ = steps.qualify_skill(skill)
    assert not qualified
    assert any("编号步骤文档不足" in r for r in reasons)


def test_qualify_skill_no_script_reference(tmp_path):
    skill = _make_skill(
        tmp_path,
        docs={"00_a.md": "# A\n\nno scripts here", "01_b.md": "# B\n\nnothing"},
    )
    qualified, reasons, _ = steps.qualify_skill(skill)
    assert not qualified
    assert any("脚本" in r for r in reasons)


def test_scan_candidates_marks_qualified_and_dedups(tmp_path):
    root = tmp_path / "skills"
    _make_skill(root, "good-one", docs=_qualified_docs())
    _make_skill(root, "bad-one", docs={})
    candidates = steps.scan_candidates(roots=[(root, "project")])
    by_name = {c.name: c for c in candidates}
    assert by_name["good-one"].qualified
    assert not by_name["bad-one"].qualified
    assert by_name["bad-one"].reasons


def test_scan_candidates_name_from_frontmatter(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "dir-name"
    (skill_dir / "docs").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fm-name\ndescription: d\n---\n", encoding="utf-8"
    )
    candidates = steps.scan_candidates(roots=[(root, "project")])
    assert candidates[0].name == "fm-name"


# ---------------------------------------------------------------------------
# 提及检测与数量解析
# ---------------------------------------------------------------------------


def test_find_mentioned_skills_word_boundary(tmp_path):
    root = tmp_path / "skills"
    _make_skill(root, "demo-skill", docs=_qualified_docs())
    candidates = steps.scan_candidates(roots=[(root, "project")])
    assert steps.find_mentioned_skills("请用 demo-skill 跑一遍", candidates) == [
        "demo-skill"
    ]
    # 子串不算点名（词边界）
    assert steps.find_mentioned_skills("demo-skillx 不算", candidates) == []
    assert steps.find_mentioned_skills("无关文本", candidates) == []


def test_parse_expected_count():
    assert steps.parse_expected_count("筛出 top 20 条结果") == 20
    assert steps.parse_expected_count("give me top-8 candidates") == 8
    assert steps.parse_expected_count("输出 15 条") == 15
    assert steps.parse_expected_count("跑一遍流程") is None


# ---------------------------------------------------------------------------
# schema 推导
# ---------------------------------------------------------------------------


def test_derive_skill_schema_extracts_all(tmp_path):
    skill = _make_skill(tmp_path, docs=_qualified_docs())
    schema = steps.derive_skill_schema("demo-skill", str(skill))
    assert schema["skill_name"] == "demo-skill"
    assert [s["step_id"] for s in schema["steps"]] == ["00", "01"]
    step0, step1 = schema["steps"]
    assert step0["scripts"] == ["prepare.py"]
    assert step0["configs"] == ["config/prep.yaml"]
    assert step0["outputs"] == ["outputs/00_prepare/items.csv"]
    assert step1["scripts"] == ["run.py"]
    assert "config/run.yaml" in step1["configs"]
    assert "outputs/01_run/result.csv" in step1["outputs"]
    # 终产物声明 = 最后一步产物
    assert schema["final_outputs"] == step1["outputs"]


def test_derive_skill_schema_empty_skill_degrades(tmp_path):
    skill = _make_skill(tmp_path, docs={})
    schema = steps.derive_skill_schema("demo-skill", str(skill))
    assert schema["steps"] == []
    assert schema["final_outputs"] == []


# ---------------------------------------------------------------------------
# 命令归属与 yaml 提取
# ---------------------------------------------------------------------------


def test_attribute_command_hit_and_miss(tmp_path):
    skill = _make_skill(tmp_path, docs=_qualified_docs())
    schema = steps.derive_skill_schema("demo-skill", str(skill))
    assert (
        steps.attribute_command("python scripts/run.py --config c.yaml", schema) == "01"
    )
    assert steps.attribute_command("ls -la", schema) is None


def test_extract_yaml_paths():
    cmd = "python x.py --config config/run.yaml --extra 'other/yy.yml' -v"
    assert steps.extract_yaml_paths(cmd) == ["config/run.yaml", "other/yy.yml"]
    assert steps.extract_yaml_paths("ls -la") == []
