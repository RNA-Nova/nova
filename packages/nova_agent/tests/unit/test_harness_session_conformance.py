"""InMemorySessionRepo 的一致性验证 + Session id 生成器注入。

用例来自 ``harness/session/testing/conformance.py``（对齐 pi 的
``createSessionBackendConformance``）——任何 SessionRepo 后端（内存 / JSONL /
SQLite…）都必须通过同一组用例。
"""

from contextlib import asynccontextmanager

import pytest
from nova_agent.harness.session import (
    InMemorySessionRepo,
    InMemorySessionStorage,
    Session,
)
from nova_agent.harness.session.testing import create_session_backend_conformance

conformance = create_session_backend_conformance(lambda: _in_memory_fixture())


@asynccontextmanager
async def _in_memory_fixture():
    yield InMemorySessionRepo()


GROUPS = sorted({case.group for case in conformance})
CASES = [(case.group, case.name) for case in conformance]


@pytest.mark.parametrize(("group", "name"), CASES, ids=[f"{g}::{n}" for g, n in CASES])
async def test_backend_conformance(group: str, name: str) -> None:
    case = next(c for c in conformance if c.group == group and c.name == name)
    await case.run()


def test_conformance_covers_all_groups() -> None:
    """30 个用例分布在 5 个组——防止后续移植漏组。"""
    assert GROUPS == [
        "entries and lanes",
        "queries and facts",
        "records and log",
        "repository and forks",
        "validation and immutability",
    ]
    assert len(CASES) == 30


async def test_session_uses_one_injectable_id_generator_across_lane_views() -> None:
    counter = {"next": 0}

    class CountingGenerator:
        def next(self) -> str:
            counter["next"] += 1
            return f"generated-{counter['next']}"

    session = Session(
        InMemorySessionStorage({"id": "session", "created_at": 1}),
        id_generator=CountingGenerator(),
    )
    main_id = await session.append_custom_entry("note")
    await session.create_lane("thread", main_id)
    thread_id = await session.view("thread").append_custom_entry("note")

    assert main_id == "generated-1"
    assert thread_id == "generated-2"


def test_assert_json_serializable_allows_shared_refs_rejects_cycles() -> None:
    """菱形/共享引用（DAG）合法；真正的环拒绝——回溯式 DFS 的语义锚。"""
    from nova_agent.harness.session import SessionError, assert_json_serializable

    shared = {"value": 1}
    assert_json_serializable({"x": shared, "y": shared})  # 共享引用：放行
    assert_json_serializable([shared, shared, [shared]])  # 多路共享：放行

    cyclic: dict = {"self": None}
    cyclic["self"] = cyclic
    try:
        assert_json_serializable(cyclic)
    except SessionError as exc:
        assert exc.code == "invalid_payload"
    else:
        raise AssertionError("cycle must be rejected")

    nested_cycle = {"a": []}
    nested_cycle["a"].append(nested_cycle)
    try:
        assert_json_serializable(nested_cycle)
    except SessionError as exc:
        assert exc.code == "invalid_payload"
    else:
        raise AssertionError("nested cycle must be rejected")
