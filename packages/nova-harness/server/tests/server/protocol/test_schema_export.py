"""线上契约导出的漂移测试与语义校验。

- ``test_committed_artifacts_are_fresh``：入仓工件（schema.json / .gen.ts）
  必须与当前类型再生成结果逐字节一致——类型变更后未重新导出即失败；
- ``test_envelope_covers_bus2_events``：信封联合覆盖 Bus 2 全部事件 type；
- ``test_entry_union_covers_session_entries``：条目联合覆盖全部条目 type。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nova_harness.server.protocol import schema_export


def test_committed_artifacts_are_fresh():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nova_harness.server.protocol.schema_export",
            "--check",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "线上契约工件已漂移，请重新导出："
        "python -m nova_harness.server.protocol.schema_export\n"
        f"{result.stdout}{result.stderr}"
    )


def test_envelope_covers_bus2_events():
    schema, ts_source = schema_export.build_artifacts()
    envelope_types = {
        variant["properties"]["type"]["const"]
        for variant in schema["novaEvent"]["oneOf"]
    }
    # Bus 2 当前 25 个域事件 type（与 AgentSessionEvent union 一一对应——
    # user_tool 事件组已消亡：用户工具进度归 item 发射通道）
    # + 3 个 item 通知（纯线上词汇，reducer 产物，手动并入信封）
    expected = {
        "agent_start",
        "agent_end",
        "agent_settled",
        "turn_start",
        "turn_end",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "auto_compaction_start",
        "auto_compaction_end",
        "auto_retry_start",
        "auto_retry_end",
        "model_changed",
        "queue_update",
        "session_info_changed",
        "session_reloaded",
        "session_replaced",
        "extension_error",
        "thinking_level_changed",
        "compaction_start",
        "compaction_end",
        "entry_appended",
        "cache_miss",
        "item_started",
        "item_delta",
        "item_completed",
    }
    assert envelope_types == expected
    # 每个信封 variant 在 TS 中也以判别联合存在
    for event_type in envelope_types:
        assert f'{{ type: "{event_type}";' in ts_source


def test_entry_union_covers_session_entries():
    schema, ts_source = schema_export.build_artifacts()
    entry_defs = {
        ref["$ref"].rsplit("/", 1)[-1] for ref in schema["sessionEntry"]["oneOf"]
    }
    assert entry_defs == {
        "SessionMessageEntry",
        "ThinkingLevelChangeEntry",
        "ModelChangeEntry",
        "CompactionEntry",
        "BranchSummaryEntry",
        "CustomEntry",
        "CustomMessageEntry",
        "LabelEntry",
        "SessionInfoEntry",
    }
    # SessionHeader 是命名模型但不混入条目联合
    assert "SessionHeader" in schema["$defs"]
    assert "SessionHeader" not in entry_defs
    assert "export interface SessionHeader" in ts_source


def test_methods_table_covers_all_registered_methods():
    """方法表与注册表一一对应：每个已注册方法都有域归属与形状声明。"""
    schema, ts_source = schema_export.build_artifacts()
    shapes = schema_export._collect_method_shapes()

    # 76 个方法全部带域与形状（75 + syncSession——连接化 P2 原子同步快照）
    assert len(shapes) == 76
    assert set(schema["methods"].keys()) == set(shapes.keys())
    for name, shape in shapes.items():
        assert shape.domain, name
        assert shape.params_model is not None, name
        entry = schema["methods"][name]
        assert entry["domain"] == shape.domain
        assert "params" in entry and "result" in entry

    # TS 方法表同步覆盖
    assert "export interface NovaWireMethodMap" in ts_source
    for name in shapes:
        assert f'"{name}":' in ts_source

    # 域聚合（能力位数据源）
    domains = {shape.domain for shape in shapes.values()}
    assert domains == {
        "session",
        "model",
        "auth",
        "resources",
        "settings",
        "system",
        "user_tools",
        "package",
    }


def test_contract_version_present():
    schema, ts_source = schema_export.build_artifacts()
    assert schema["contractVersionMajor"] == schema_export.CONTRACT_VERSION_MAJOR
    assert schema["contractVersionMinor"] == schema_export.CONTRACT_VERSION_MINOR
    assert (
        f"export const NOVA_CONTRACT_MAJOR = {schema_export.CONTRACT_VERSION_MAJOR};"
        in ts_source
    )
    assert (
        f"export const NOVA_CONTRACT_MINOR = {schema_export.CONTRACT_VERSION_MINOR};"
        in ts_source
    )
