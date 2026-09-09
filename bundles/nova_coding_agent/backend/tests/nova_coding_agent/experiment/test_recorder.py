"""recorder.py 测试：记录流程、四标准判定纯逻辑、任务页渲染。"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from nova_coding_agent.experiment import recorder


def _schema() -> dict:
    return {
        "skill_name": "demo",
        "skill_dir": "/nonexistent",
        "steps": [
            {
                "step_id": "00",
                "doc_name": "00_prepare.md",
                "title": "Prepare",
                "scripts": ["prepare.py"],
                "configs": ["config/prep.yaml"],
                "outputs": ["outputs/00_prepare/items.csv"],
            },
            {
                "step_id": "01",
                "doc_name": "01_run.md",
                "title": "Run",
                "scripts": ["run.py"],
                "configs": ["config/run.yaml"],
                "outputs": ["outputs/01_run/result.csv", "outputs/01_run/logs"],
            },
        ],
        "final_outputs": ["outputs/01_run/result.csv", "outputs/01_run/logs"],
    }


def _make_record(tmp_path, prompt="跑出 top 3 的结果", now=None) -> recorder.TaskRecord:
    return recorder.start_record(
        str(tmp_path),
        "demo",
        prompt,
        _schema(),
        now=now or datetime(2026, 1, 2, 3, 4, 5),
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_project(tmp_path) -> None:
    """搭好一个"全部成功"的项目现场：yaml + 两步产物。"""
    _write(tmp_path / "config" / "prep.yaml", "prep: 1\n")
    _write(tmp_path / "config" / "run.yaml", "run: 2\n")
    _write(tmp_path / "outputs" / "00_prepare" / "items.csv", "id,x\n1,a\n2,b\n3,c\n")
    _write(tmp_path / "outputs" / "01_run" / "result.csv", "id,y\n1,p\n2,q\n3,r\n")
    (tmp_path / "outputs" / "01_run" / "logs").mkdir(parents=True, exist_ok=True)


def _run_happy_path(record: recorder.TaskRecord) -> None:
    """模拟两步 bash 命令全部成功。"""
    record.record_command("c1", "python scripts/prepare.py --config config/prep.yaml")
    record.record_tool_end("c1", "bash", False)
    record.record_command("c2", "python scripts/run.py --config config/run.yaml")
    record.record_tool_end("c2", "bash", False)


# ---------------------------------------------------------------------------
# 记录流程
# ---------------------------------------------------------------------------


def test_start_record_parses_expected_count_and_id(tmp_path):
    record = _make_record(tmp_path)
    assert record.expected_count == 3
    assert record.task_id.startswith("task-20260102-030405-")
    assert record.skill_name == "demo"


def test_record_command_attribution_and_yaml_snapshot(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    cmd = record.record_command(
        "c1", "python scripts/prepare.py --config config/prep.yaml"
    )
    assert cmd.step_id == "00"
    assert cmd.yaml_snapshots == {"config/prep.yaml": "prep: 1\n"}

    unattributed = record.record_command("c9", "ls -la")
    assert unattributed.step_id is None
    assert unattributed.yaml_snapshots == {}


def test_record_tool_end_marks_result_and_inventories_outputs(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    record.record_command("c1", "python scripts/prepare.py --config config/prep.yaml")
    record.record_tool_end("c1", "bash", False)
    cmd = record.commands[0]
    assert cmd.ok is True
    assert len(cmd.outputs) == 1
    inv = cmd.outputs[0]
    assert inv.exists and inv.data_lines == 3 and inv.lines == 4


def test_record_tool_end_non_bash_failure_goes_to_other_failures(tmp_path):
    record = _make_record(tmp_path)
    record.record_tool_end("x1", "read", True)
    assert record.other_failures == [{"tool_name": "read", "tool_call_id": "x1"}]


# ---------------------------------------------------------------------------
# 四标准判定
# ---------------------------------------------------------------------------


def _criteria_by_key(criteria):
    return {c["key"]: c for c in criteria}


def test_criteria_all_pass(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    _run_happy_path(record)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert all(c["ok"] for c in criteria)
    assert "== 要求 3" in by_key["count_match"]["note"]
    assert recorder.overall_status(record, criteria) == "completed"


def test_criteria_tool_failure(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    record.record_command("c1", "python scripts/prepare.py --config config/prep.yaml")
    record.record_tool_end("c1", "bash", True)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert not by_key["tools_ok"]["ok"]
    assert recorder.overall_status(record, criteria) == "failed"


def test_criteria_missing_output(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    _run_happy_path(record)
    # 删掉步骤 00 的期望产物 → 产物完整性失败
    (tmp_path / "outputs" / "00_prepare" / "items.csv").unlink()
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert not by_key["outputs_complete"]["ok"]
    assert "items.csv" in by_key["outputs_complete"]["note"]


def test_criteria_count_mismatch(tmp_path):
    _setup_project(tmp_path)
    # 提示词要求 top 5，实际只有 3 行
    record = _make_record(tmp_path, prompt="跑出 top 5 的结果")
    _run_happy_path(record)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert not by_key["count_match"]["ok"]
    assert "!= 要求 5" in by_key["count_match"]["note"]


def test_criteria_count_undeclared_is_neutral(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path, prompt="跑一遍流程")
    assert record.expected_count is None
    _run_happy_path(record)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["count_match"]["ok"]
    assert "未声明" in by_key["count_match"]["note"]


def test_criteria_no_measurable_final_output_is_neutral(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    # final_outputs 无可计数文件
    record.schema["final_outputs"] = ["outputs/01_run/logs"]
    _run_happy_path(record)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["count_match"]["ok"]
    assert "无法核对" in by_key["count_match"]["note"]


def test_criteria_interrupted(tmp_path):
    record = _make_record(tmp_path)
    record.interrupted = True
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert not by_key["no_interrupt"]["ok"]
    assert recorder.overall_status(record, criteria) == "aborted"


def test_criteria_empty_schema_outputs_untested(tmp_path):
    record = _make_record(tmp_path)
    record.schema = {"skill_name": "demo", "steps": [], "final_outputs": []}
    record.expected_count = None
    record.record_command("c1", "echo hi")
    record.record_tool_end("c1", "bash", False)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["outputs_complete"]["ok"]
    assert "未检查" in by_key["outputs_complete"]["note"]


def test_detect_interruption():
    aborted = SimpleNamespace(role="assistant", stop_reason="aborted")
    normal = SimpleNamespace(role="assistant", stop_reason="stop")
    assert recorder.detect_interruption([normal, aborted])
    assert not recorder.detect_interruption([normal])
    assert not recorder.detect_interruption([])


# ---------------------------------------------------------------------------
# 任务页渲染
# ---------------------------------------------------------------------------


def test_render_task_page_sections(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    _run_happy_path(record)
    criteria = recorder.evaluate_criteria(record)
    status = recorder.overall_status(record, criteria)
    page = recorder.render_task_page(record, criteria, status)

    assert 'task_id: "' + record.task_id + '"' in page
    assert 'skill: "demo"' in page
    assert "status: completed" in page
    assert "expected_count: 3" in page
    assert "## 任务概要" in page
    assert "跑出 top 3 的结果" in page
    assert "## 参数快照" in page
    assert "prep: 1" in page  # yaml 全文快照
    assert "## 逐步 I/O 对账" in page
    assert "Step 00 Prepare" in page
    assert "outputs/01_run/result.csv" in page
    assert "3 行数据" in page
    assert "## 四标准判定" in page
    assert "总体判定：成功" in page
    assert "## 异常摘要" in page


def test_index_summary_line(tmp_path):
    record = _make_record(tmp_path)
    line = recorder.index_summary_line(record, "completed")
    assert record.task_id in line
    assert "demo" in line
    assert "2026-01-02" in line
