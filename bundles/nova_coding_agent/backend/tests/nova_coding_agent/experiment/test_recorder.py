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
        "final_product": "outputs/01_run/result.csv",
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
    assert cmd.script == "prepare.py"
    assert cmd.workdir == str(tmp_path)  # 无 cd → 会话 cwd
    assert cmd.yaml_snapshots == {"config/prep.yaml": "prep: 1\n"}

    unattributed = record.record_command("c9", "ls -la")
    assert unattributed.step_id is None
    assert unattributed.script is None
    assert unattributed.yaml_snapshots == {}


# ---------------------------------------------------------------------------
# 修复 1：产物盘点的路径解析基准 = 该条命令的 cd 目标目录
# ---------------------------------------------------------------------------


def test_record_command_cd_target_workdir_and_inventory(tmp_path):
    record = _make_record(tmp_path)
    # 产物只在 cd 目标目录下存在（项目根没有 outputs/）
    sub = tmp_path / "tasks" / "t1"
    _write(sub / "outputs" / "01_run" / "result.csv", "id,y\n1,p\n2,q\n3,r\n")
    (sub / "outputs" / "01_run" / "logs").mkdir(parents=True)
    cmd = record.record_command(
        "c1", f"cd {sub} && python scripts/run.py --config config/run.yaml"
    )
    assert cmd.workdir == str(sub)
    record.record_tool_end("c1", "bash", False)
    inv = {o.path: o for o in cmd.outputs}
    # 相对 cd 目标解析 → 找到（若按会话 cwd 解析会误报缺失）
    assert inv["outputs/01_run/result.csv"].exists
    assert inv["outputs/01_run/result.csv"].data_lines == 3
    assert (
        inv["outputs/01_run/logs"].exists and inv["outputs/01_run/logs"].kind == "dir"
    )


def test_record_command_cd_quoted_and_relative_forms(tmp_path):
    record = _make_record(tmp_path)
    sub = tmp_path / "dir with space"
    sub.mkdir()
    quoted = record.record_command("c1", f'cd "{sub}" && python scripts/run.py')
    assert quoted.workdir == str(sub)
    relative = record.record_command("c2", "cd tasks/t1 && python scripts/run.py")
    assert relative.workdir == str(tmp_path / "tasks" / "t1")
    # 变量形式不做静态解析 → 回退会话 cwd
    variable = record.record_command("c3", "cd $TASK_DIR && python scripts/run.py")
    assert variable.workdir == str(tmp_path)


def test_criteria_outputs_resolve_against_cd_target(tmp_path):
    record = _make_record(tmp_path)
    # 两步产物都在 cd 目标目录下；项目根无任何 outputs/
    sub = tmp_path / "tasks" / "t1"
    _write(sub / "outputs" / "00_prepare" / "items.csv", "id,x\n1,a\n2,b\n3,c\n")
    _write(sub / "outputs" / "01_run" / "result.csv", "id,y\n1,p\n2,q\n3,r\n")
    (sub / "outputs" / "01_run" / "logs").mkdir(parents=True)
    record.record_command("c1", f"cd {sub} && python scripts/prepare.py")
    record.record_tool_end("c1", "bash", False)
    record.record_command("c2", f"cd {sub} && python scripts/run.py")
    record.record_tool_end("c2", "bash", False)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert by_key["outputs_complete"]["ok"]
    # 终产物也经命令工作目录定位
    assert by_key["count_match"]["ok"]
    assert str(sub) in by_key["count_match"]["note"]


# ---------------------------------------------------------------------------
# 修复 A：前导变量赋值 + 变量 cd 的真实命令形态
# ---------------------------------------------------------------------------


def test_record_command_variable_cd_full_form(tmp_path):
    """TASK_DIR 变量赋值 + cd "$TASK_DIR" 形态：workdir 解析到任务目录。"""
    record = _make_record(tmp_path)
    work = tmp_path / "tasks" / "t1"
    _write(work / "outputs" / "00_prepare" / "items.csv", "id,x\n1,a\n2,b\n3,c\n")
    cmd_text = (
        f'TASK_DIR="{work}"\n'
        'cd "$TASK_DIR"\n'
        "CONDA_DEFAULT_ENV=demo python scripts/prepare.py --config config/prep.yaml"
    )
    cmd = record.record_command("c1", cmd_text)
    assert cmd.workdir == str(work)
    record.record_tool_end("c1", "bash", False)
    inv = {o.path: o for o in cmd.outputs}
    assert inv["outputs/00_prepare/items.csv"].exists
    assert inv["outputs/00_prepare/items.csv"].data_lines == 3


def test_criteria_outputs_resolve_with_variable_cd(tmp_path):
    """变量 cd 形态下标准 2/3 均按任务目录解析（不误报缺项、终产物可核对）。"""
    record = _make_record(tmp_path)
    work = tmp_path / "tasks" / "t1"
    _write(work / "outputs" / "00_prepare" / "items.csv", "id,x\n1,a\n2,b\n3,c\n")
    _write(work / "outputs" / "01_run" / "result.csv", "id,y\n1,p\n2,q\n3,r\n")
    (work / "outputs" / "01_run" / "logs").mkdir(parents=True)
    for call_id, script in (("c1", "prepare.py"), ("c2", "run.py")):
        cmd_text = f'TASK_DIR="{work}" && cd "$TASK_DIR" && python scripts/{script}'
        record.record_command(call_id, cmd_text)
        record.record_tool_end(call_id, "bash", False)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert by_key["outputs_complete"]["ok"]
    assert by_key["count_match"]["ok"]
    assert str(work) in by_key["count_match"]["note"]


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


# ---------------------------------------------------------------------------
# 修复 2：标准 1 判定口径——只统计步骤脚本 + 重试宽限
# ---------------------------------------------------------------------------


def test_criteria_probe_failures_not_counted(tmp_path):
    """辅助命令（pip/探测等未归属命令）失败记流水但不参与判定。"""
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    record.record_command("p1", "pip install definitely-not-a-real-pkg-xyz")
    record.record_tool_end("p1", "bash", True, error_summary="no matching distribution")
    record.record_command("p2", "ls /nonexistent-dir-xyz")
    record.record_tool_end(
        "p2", "bash", True, error_summary="No such file or directory"
    )
    _run_happy_path(record)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["tools_ok"]["ok"], by_key["tools_ok"]["note"]
    # 失败仍留在流水与异常摘要（观测不丢），只是不判 ❌
    page = recorder.render_task_page(
        record,
        recorder.evaluate_criteria(record),
        "completed",
    )
    assert "pip install definitely-not-a-real-pkg-xyz" in page
    assert "未归属命令" in page


def test_criteria_script_retry_eventually_succeeds(tmp_path):
    """同脚本前几次失败但最终成功 → 不判 ❌，标注 ⚠️ 经 N 次尝试后成功。"""
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    record.record_command("a1", "python scripts/run.py --config config/run.yaml")
    record.record_tool_end("a1", "bash", True, error_summary="CUDA out of memory")
    record.record_command("a2", "python scripts/run.py --config config/run.yaml")
    record.record_tool_end("a2", "bash", True, error_summary="CUDA out of memory")
    record.record_command("a3", "python scripts/run.py --config config/run.yaml")
    record.record_tool_end("a3", "bash", False)
    record.record_command("c1", "python scripts/prepare.py --config config/prep.yaml")
    record.record_tool_end("c1", "bash", False)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["tools_ok"]["ok"]
    assert "run.py 经 3 次尝试后成功 ⚠️" in by_key["tools_ok"]["note"]


def test_criteria_script_ultimately_fails(tmp_path):
    """脚本最终未成功 → 判 ❌；失败清单引用脚本名 + 错误摘要，不塞超长命令。"""
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    long_command = "python scripts/run.py --config config/run.yaml " + "--x " * 100
    record.record_command("a1", long_command)
    record.record_tool_end("a1", "bash", True, error_summary="KeyError: 'pred_x'")
    record.record_command("a2", long_command)
    record.record_tool_end("a2", "bash", True, error_summary="KeyError: 'pred_x'")
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert not by_key["tools_ok"]["ok"]
    note = by_key["tools_ok"]["note"]
    assert "run.py" in note
    assert "KeyError: 'pred_x'" in note
    assert long_command not in note  # 判定区不塞整段超长命令
    assert recorder.overall_status(record, criteria) == "failed"


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
    # final_product 指向不可计数的目录 → 无法核对（中性）
    record.schema["final_product"] = "outputs/01_run/logs"
    _run_happy_path(record)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["count_match"]["ok"]
    assert "无法核对" in by_key["count_match"]["note"]


def test_criteria_final_product_undeclared_is_neutral(tmp_path):
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    record.schema["final_product"] = None
    _run_happy_path(record)
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["count_match"]["ok"]
    assert "未声明" in by_key["count_match"]["note"]


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


def test_placeholder_outputs_not_checked(tmp_path):
    """文档占位路径（XXXX/{var}/...）不是具体产物，不参与缺项判定与盘点。"""
    record = _make_record(tmp_path)
    record.schema["steps"][0]["outputs"] = [
        "outputs/00_prepare/items.csv",
        "outputs/00_prepare/model-XXXXXX/best.pth",
        "outputs/00_prepare/{task_id}/meta.yaml",
        "outputs/00_prepare/.../detail.md",
    ]
    _write(tmp_path / "outputs" / "00_prepare" / "items.csv", "id,x\n1,a\n")
    record.record_command("c1", "python scripts/prepare.py --config config/prep.yaml")
    record.record_tool_end("c1", "bash", False)
    # 盘点流水只含具体产物
    assert [o.path for o in record.commands[0].outputs] == [
        "outputs/00_prepare/items.csv"
    ]
    by_key = _criteria_by_key(recorder.evaluate_criteria(record))
    assert by_key["outputs_complete"]["ok"]


def test_detect_interruption():
    aborted = SimpleNamespace(role="assistant", stop_reason="aborted")
    normal = SimpleNamespace(role="assistant", stop_reason="stop")
    assert recorder.detect_interruption([normal, aborted])
    assert not recorder.detect_interruption([normal])
    assert not recorder.detect_interruption([])


# ---------------------------------------------------------------------------
# 修复 B：中性结果（未声明/无法核对/未检查）渲染 ➖，不渲染 ✅
# ---------------------------------------------------------------------------


def test_neutral_criteria_flagged_and_rendered_minus(tmp_path):
    record = _make_record(tmp_path)
    # 终产物文件不存在 → 标准 3 无法核对（neutral）
    record.schema["final_product"] = "outputs/01_run/missing.csv"
    # 无脚本命中的命令 → 标准 1 未检查（neutral）；无期望产物 → 标准 2 未检查
    record.schema["steps"] = []
    record.record_command("c1", "echo hi")
    record.record_tool_end("c1", "bash", False)
    criteria = recorder.evaluate_criteria(record)
    by_key = _criteria_by_key(criteria)
    assert by_key["tools_ok"]["neutral"] is True
    assert by_key["outputs_complete"]["neutral"] is True
    assert by_key["count_match"]["neutral"] is True
    assert not by_key["no_interrupt"].get("neutral")
    # 中性不判失败
    assert all(c["ok"] for c in criteria)

    page = recorder.render_task_page(record, criteria, "completed")
    section = page[page.index("## 四标准判定") :]
    assert "➖ **工具全部调用成功**" in section
    assert "➖ **产物完整性**" in section
    assert "➖ **终产物数量符合要求**" in section
    assert "✅ **工具全部调用成功**" not in section
    assert "✅ **终产物数量符合要求**" not in section
    # 真通过仍是 ✅（标准 4 无中断）
    assert "✅ **无中断**" in section


def test_real_pass_and_fail_icons_unchanged(tmp_path):
    """真通过 ✅、真失败 ❌ 不受中性图标影响。"""
    _setup_project(tmp_path)
    record = _make_record(tmp_path)
    _run_happy_path(record)
    criteria = recorder.evaluate_criteria(record)
    assert not any(c.get("neutral") for c in criteria)
    page = recorder.render_task_page(record, criteria, "completed")
    section = page[page.index("## 四标准判定") : page.index("## 异常摘要")]
    assert "➖" not in section
    assert "✅ **终产物数量符合要求**" in section

    # 标准 3 真失败 → ❌
    record2 = _make_record(tmp_path, prompt="跑出 top 9 的结果")
    _run_happy_path(record2)
    criteria2 = recorder.evaluate_criteria(record2)
    page2 = recorder.render_task_page(record2, criteria2, "failed")
    assert "❌ **终产物数量符合要求**" in page2


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
