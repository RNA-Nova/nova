"""audit.py 测试：耗时聚合、usage 提取/聚合、顺畅度统计、jsonl 落盘、盲态拦截、
recall 开关状态机（含扩展级接线）。全部夹具模拟事件载荷，不依赖真实 provider。"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

from nova_coding_agent.experiment import audit, memory_store, recorder


def _usage(
    input=100,
    output=20,
    cache_read=50,
    cache_write=10,
    total_tokens=180,
    reasoning=None,
):
    """模拟 nova_ai Usage 同形对象（turn_end 消息载荷）。"""
    return SimpleNamespace(
        input=input,
        output=output,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=total_tokens,
        reasoning=reasoning,
    )


def _schema() -> dict:
    return {
        "skill_name": "demo",
        "skill_dir": "/nonexistent",
        "steps": [
            {
                "step_id": "01",
                "doc_name": "01_run.md",
                "title": "Run",
                "scripts": ["run.py"],
                "configs": [],
                "outputs": ["outputs/01_run/result.csv"],
            }
        ],
        "final_outputs": ["outputs/01_run/result.csv"],
        "final_product": "outputs/01_run/result.csv",
    }


# ---------------------------------------------------------------------------
# 耗时聚合
# ---------------------------------------------------------------------------


def test_tool_duration_aggregation():
    trail = audit.AuditTrail()
    trail.on_tool_start("c1", "bash", "run.py", "01", 1000)
    trail.on_tool_end("c1", False, 2500)
    trail.on_tool_start("c2", "read", None, None, 3000)
    trail.on_tool_end("c2", True, 4000)
    assert trail.tool_calls[0].duration_ms == 1500
    assert trail.tool_calls[0].ok is True
    assert trail.tool_calls[0].script == "run.py"
    assert trail.tool_calls[1].duration_ms == 1000
    assert trail.tool_calls[1].ok is False


def test_tool_end_without_start_degrades():
    """未观测到 start 的 end：如实补条目，耗时按 0 起算不为负。"""
    trail = audit.AuditTrail()
    trail.on_tool_end("c9", False, 5000)
    entry = trail.tool_calls[-1]
    assert entry.tool_call_id == "c9"
    assert entry.duration_ms == 0
    assert entry.ok is True


# ---------------------------------------------------------------------------
# usage 提取与聚合
# ---------------------------------------------------------------------------


def test_extract_usage_fields_and_null_reasoning():
    d = audit.extract_usage(_usage())
    assert d == {
        "input": 100,
        "output": 20,
        "cache_read": 50,
        "cache_write": 10,
        "total_tokens": 180,
        "reasoning": None,  # provider 未上报 → 如实记 null
    }
    assert audit.extract_usage(_usage(reasoning=30))["reasoning"] == 30
    # 字段全缺的对象：token 按 0 记，reasoning 记 null
    d3 = audit.extract_usage(SimpleNamespace())
    assert all(d3[k] == 0 for k in ("input", "output", "cache_read", "cache_write"))
    assert d3["reasoning"] is None


def test_aggregate_usage_and_cache_hit_rate():
    totals = audit.aggregate_usage(
        [
            {"turn_index": 0, **audit.extract_usage(_usage())},
            {
                "turn_index": 1,
                **audit.extract_usage(
                    _usage(input=200, output=40, cache_read=150, total_tokens=400)
                ),
            },
        ]
    )
    assert totals["turns"] == 2
    assert totals["input"] == 300
    assert totals["output"] == 60
    assert totals["cache_read"] == 200
    # 口径 cache_read / (input + cache_read)
    assert totals["cache_hit_rate"] == round(200 / 500, 4)


def test_aggregate_usage_empty_and_no_cache():
    assert audit.aggregate_usage([])["cache_hit_rate"] is None
    # provider 未上报缓存字段（全 0）→ 命中率如实为 0
    totals = audit.aggregate_usage(
        [{"turn_index": 0, **audit.extract_usage(_usage(cache_read=0, cache_write=0))}]
    )
    assert totals["cache_hit_rate"] == 0.0


# ---------------------------------------------------------------------------
# 过程顺畅度（纯聚合）
# ---------------------------------------------------------------------------


def test_smoothness_stats():
    commands = [
        SimpleNamespace(script="a.py", ok=False),
        SimpleNamespace(script="a.py", ok=True),  # 重试后成功：retries 1
        SimpleNamespace(script="b.py", ok=True),  # 一次通过：retries 0
        SimpleNamespace(script="c.py", ok=False),  # 最终未成功：retries 1
        SimpleNamespace(script=None, ok=False),  # 未归属不参与
    ]
    stats = audit.smoothness_stats(commands)
    assert stats["scripts"]["a.py"] == {
        "attempts": 2,
        "succeeded": True,
        "first_try_success": False,
        "retries": 1,
    }
    assert stats["scripts"]["b.py"]["first_try_success"] is True
    assert stats["scripts"]["c.py"]["retries"] == 1
    assert stats["script_count"] == 3
    assert stats["total_attempts"] == 4
    assert stats["total_retries"] == 2
    assert stats["first_try_rate"] == round(1 / 3, 4)


def test_smoothness_stats_empty():
    assert audit.smoothness_stats([])["first_try_rate"] is None


# ---------------------------------------------------------------------------
# 盲态拦截（纯函数）
# ---------------------------------------------------------------------------


def test_blind_blocks_memory_paths_for_read_tools():
    assert audit.should_block_memory_access("read", {"path": "memory/tasks/x.md"})
    assert audit.should_block_memory_access("ls", {"path": "./memory"})
    assert audit.should_block_memory_access("grep", {"path": "/home/u/proj/memory"})
    assert audit.should_block_memory_access("find", {"path": "memory"})
    # reason 说明盲态原因
    reason = audit.should_block_memory_access("read", {"path": "memory/x.md"})
    assert "盲态" in reason and "memory/" in reason


def test_blind_allows_normal_paths_for_read_tools():
    assert audit.should_block_memory_access("read", {"path": "src/a.py"}) is None
    # memory 作为文件名的一部分不算路径段
    assert (
        audit.should_block_memory_access("read", {"path": "src/memory_utils.py"})
        is None
    )
    # grep 的 pattern 是检索词不是路径——搜索 "memory" 这个词不拦
    assert (
        audit.should_block_memory_access("grep", {"pattern": "memory", "path": "src/"})
        is None
    )


def test_blind_bash_command_tokens():
    assert audit.should_block_memory_access(
        "bash", {"command": "cat memory/tasks/x.md"}
    )
    assert audit.should_block_memory_access("bash", {"command": "cd memory && ls"})
    assert audit.should_block_memory_access(
        "bash", {"command": "grep -r foo /proj/memory/tasks"}
    )
    assert audit.should_block_memory_access(
        "bash", {"command": 'python x.py --out="memory/r.md"'}
    )
    # 正常命令放行：in-memory 是连字符词，不是路径段
    assert (
        audit.should_block_memory_access(
            "bash", {"command": "python x.py --cache in-memory"}
        )
        is None
    )
    assert audit.should_block_memory_access("bash", {"command": "ls outputs/"}) is None


def test_blind_ignores_other_tools():
    # write/edit 不在盲态拦截名单（PRD 口径：read/grep/ls 等读取系）
    assert audit.should_block_memory_access("write", {"path": "memory/x.md"}) is None
    assert audit.should_block_memory_access("edit", {"path": "memory/x.md"}) is None
    assert (
        audit.should_block_memory_access("subagent", {"task": "read memory/"}) is None
    )


# ---------------------------------------------------------------------------
# jsonl 落盘结构
# ---------------------------------------------------------------------------


def test_jsonl_records_structure(tmp_path):
    record = recorder.start_record(
        str(tmp_path), "demo", "跑出 top 2 的结果", _schema(), recall_enabled=False
    )
    record.audit.on_tool_start("c1", "bash", "run.py", "01", 1000)
    record.audit.on_tool_end("c1", False, 1600)
    record.audit.record_usage(0, _usage())
    record.finished_ms = record.started_ms + 5000
    criteria = recorder.evaluate_criteria(record)
    records = audit.build_jsonl_records(record, criteria, "completed")

    path = audit.write_audit_jsonl(str(tmp_path), record.task_id, records)
    assert path is not None
    lines = [json.loads(line) for line in Path(path).read_text().splitlines()]
    assert [r["record"] for r in lines] == ["tool_call", "model_call", "task_summary"]
    # 每条记录携带 recall_enabled 分组字段（本任务 recall off）
    assert all(r["recall_enabled"] is False for r in lines)
    assert all(r["task_id"] == record.task_id for r in lines)

    tool_call = lines[0]
    assert tool_call["duration_ms"] == 600
    assert tool_call["script"] == "run.py"
    assert tool_call["step_id"] == "01"
    assert tool_call["ok"] is True

    model_call = lines[1]
    assert model_call["turn_index"] == 0
    assert model_call["usage"]["input"] == 100
    assert model_call["usage"]["reasoning"] is None

    summary = lines[2]
    assert summary["status"] == "completed"
    assert summary["duration_ms"] == 5000
    assert set(summary["criteria"]) == {
        "tools_ok",
        "outputs_complete",
        "count_match",
        "no_interrupt",
    }
    assert summary["retry"]["script_count"] == 0  # 命令流水为空（只走了审计面）
    assert summary["usage_totals"]["input"] == 100


# ---------------------------------------------------------------------------
# recall 档位持久化 + 扩展级状态机
# ---------------------------------------------------------------------------


def test_recall_state_file_roundtrip(tmp_path):
    root = str(tmp_path)
    assert memory_store.read_recall_enabled(root) is None
    assert memory_store.write_recall_enabled(root, False)
    assert memory_store.read_recall_enabled(root) is False
    assert memory_store.write_recall_enabled(root, True)
    assert memory_store.read_recall_enabled(root) is True


def _load_extension():
    """按路径加载 experiment_mode.py（对齐 test_plan_mode.py 的加载方式）。"""
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "extensions", "experiment_mode.py"
    )
    spec = importlib.util.spec_from_file_location("_test_ext_experiment_mode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNovaAPI:
    def __init__(self):
        self.handlers = {}
        self.commands = {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None

    def registerCommand(self, name, options=None):
        self.commands[name] = options or {}

    def registerFlag(self, name, options=None):
        pass

    def getFlag(self, name):
        return None


def _run(coro):
    return asyncio.run(coro)


def _make_ctx(tmp_path, entries):
    return SimpleNamespace(
        cwd=str(tmp_path),
        has_ui=False,
        ui=None,
        session_manager=None,
        append_entry=lambda t, d: entries.append(d),
    )


def _make_skill_project(tmp_path):
    docs = tmp_path / ".agents" / "skills" / "demo-skill" / "docs"
    docs.mkdir(parents=True)
    (docs.parent / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: d\n---\n", encoding="utf-8"
    )
    (docs / "00_prep.md").write_text(
        "# S0\n\nRun `scripts/prep.py`, outputs `outputs/00/x.csv`."
    )
    (docs / "01_rank.md").write_text(
        "# S1\n\nRun `scripts/rank.py`, outputs `outputs/01/top_items.csv`."
    )


def test_recall_command_state_machine_and_blind_gate(tmp_path):
    """recall on|off 状态机：档位文件 + 会话条目 + 召回门控 + 盲态拦截。"""
    _make_skill_project(tmp_path)
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    entries = []
    ctx = _make_ctx(tmp_path, entries)

    # 开启模式（1 个合格候选直绑）
    _run(api.commands["experiment"]["handler"]("", ctx))
    # 默认 recall on：召回注入存在
    rec = _run(api.handlers["before_agent_start"](SimpleNamespace(), ctx))
    assert rec is not None and rec.message.custom_type == "experiment-memory"
    # recall on 时零拦截
    r = _run(
        api.handlers["tool_call"](
            SimpleNamespace(
                tool_name="read", tool_call_id="x", args={"path": "memory/tasks/a.md"}
            ),
            ctx,
        )
    )
    assert r is None

    # recall off：档位文件 + 会话条目持久化
    _run(api.commands["experiment"]["handler"]("recall off", ctx))
    assert memory_store.read_recall_enabled(str(tmp_path)) is False
    assert entries[-1]["recall_enabled"] is False
    # 召回注入关闭
    assert _run(api.handlers["before_agent_start"](SimpleNamespace(), ctx)) is None
    # 盲态拦截生效：read memory/ → block + reason
    r = _run(
        api.handlers["tool_call"](
            SimpleNamespace(
                tool_name="read", tool_call_id="x", args={"path": "memory/tasks/a.md"}
            ),
            ctx,
        )
    )
    assert r is not None and r.block is True and "盲态" in r.reason
    # 正常路径放行
    r = _run(
        api.handlers["tool_call"](
            SimpleNamespace(
                tool_name="read", tool_call_id="y", args={"path": "src/a.py"}
            ),
            ctx,
        )
    )
    assert r is None

    # recall on：注入恢复 + 拦截解除
    _run(api.commands["experiment"]["handler"]("recall on", ctx))
    assert memory_store.read_recall_enabled(str(tmp_path)) is True
    rec = _run(api.handlers["before_agent_start"](SimpleNamespace(), ctx))
    assert rec is not None
    r = _run(
        api.handlers["tool_call"](
            SimpleNamespace(
                tool_name="read", tool_call_id="z", args={"path": "memory/tasks/a.md"}
            ),
            ctx,
        )
    )
    assert r is None

    # /experiment recall（无参数）只报告档位不切换
    _run(api.commands["experiment"]["handler"]("recall", ctx))
    assert memory_store.read_recall_enabled(str(tmp_path)) is True


def test_audit_pipeline_e2e(tmp_path):
    """扩展级全链路：耗时/usage 采集 → 任务页审计小节 + jsonl 流水。"""
    _make_skill_project(tmp_path)
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    ctx = _make_ctx(tmp_path, [])

    _run(api.commands["experiment"]["handler"]("", ctx))
    r = _run(
        api.handlers["input"](SimpleNamespace(text="跑出 top 1 的结果", images=[]), ctx)
    )
    assert r.action == "continue"
    # 产物备好（供判定）
    work = tmp_path / "outputs" / "01"
    work.mkdir(parents=True)
    (work / "top_items.csv").write_text("h\n1\n", encoding="utf-8")
    x0 = tmp_path / "outputs" / "00"
    x0.mkdir(parents=True)
    (x0 / "x.csv").write_text("h\n1\n", encoding="utf-8")
    # 工具执行链路：tool_call → start → end
    _run(
        api.handlers["tool_call"](
            SimpleNamespace(
                tool_name="bash",
                tool_call_id="c1",
                args={"command": "python scripts/rank.py"},
            ),
            ctx,
        )
    )
    _run(
        api.handlers["tool_execution_start"](
            SimpleNamespace(tool_call_id="c1", tool_name="bash", args={}), ctx
        )
    )
    _run(
        api.handlers["tool_execution_end"](
            SimpleNamespace(
                tool_call_id="c1", tool_name="bash", is_error=False, result="ok"
            ),
            ctx,
        )
    )
    # 逐轮 usage（夹具载荷）
    _run(
        api.handlers["turn_end"](
            SimpleNamespace(
                turn_index=0,
                message=SimpleNamespace(role="assistant", usage=_usage()),
                tool_results=[],
            ),
            ctx,
        )
    )
    _run(
        api.handlers["agent_end"](
            SimpleNamespace(
                messages=[SimpleNamespace(role="assistant", stop_reason="stop")]
            ),
            ctx,
        )
    )

    # jsonl 流水落盘：三种记录齐全、recall_enabled=true
    audit_files = list((tmp_path / "memory" / "audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    lines = [json.loads(line) for line in audit_files[0].read_text().splitlines()]
    assert [r["record"] for r in lines] == ["tool_call", "model_call", "task_summary"]
    assert all(r["recall_enabled"] is True for r in lines)
    assert lines[0]["duration_ms"] is not None and lines[0]["duration_ms"] >= 0
    assert lines[1]["usage"]["input"] == 100
    assert lines[2]["status"] == "completed"

    # 任务页含审计汇总小节
    page = next((tmp_path / "memory" / "tasks").glob("*.md")).read_text()
    assert "## 审计汇总" in page
    assert "recall_enabled：true" in page
    assert "缓存命中率" in page
    assert "rank.py" in page  # 逐步耗时表
