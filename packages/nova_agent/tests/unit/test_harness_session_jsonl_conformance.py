"""JsonlSessionRepo 的一致性验证——同一套 30 用例考文件后端。

用例来自 ``harness/session/testing/conformance.py``（对齐 pi 的
``createSessionBackendConformance``）。fixture 工厂注入本地临时目录与默认
cwd（JSONL 后端的 create/fork 需要 cwd，属于该后端契约的一部分）。
"""

import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import Any

import pytest
from nova_agent.harness.session.jsonl import (
    JsonlForkOptions,
    JsonlSessionRepo,
    LocalJsonlFileSystem,
)
from nova_agent.harness.session.testing import create_session_backend_conformance
from nova_agent.harness.session.types import ForkOptions

DEFAULT_CWD = "/tmp/nova-conformance"


class _CwdInjectingRepo(JsonlSessionRepo):
    """create/fork 缺省 cwd 注入（JSONL 后端契约；其余行为零改动）。"""

    async def create(self, options=None) -> Any:  # type: ignore[override]
        from nova_agent.harness.session.types import SessionCreateOptions

        normalized = options or SessionCreateOptions()
        payload: dict = {"cwd": DEFAULT_CWD}
        if getattr(normalized, "id", None) is not None:
            payload["id"] = normalized.id
        if getattr(normalized, "parent_session_id", None) is not None:
            payload["parent_session_id"] = normalized.parent_session_id
        return await super().create(payload)

    async def fork(self, source, options=None) -> Any:  # type: ignore[override]
        opts: ForkOptions = options or ForkOptions()
        cwd = getattr(opts, "cwd", None) or source["cwd"]
        fork_opts = JsonlForkOptions(
            scope=opts.scope,
            entry_id=opts.entry_id,
            position=opts.position,
            id=opts.id,
            parent_session_id=opts.parent_session_id,
            cwd=cwd,
        )
        return await super().fork(source, fork_opts)


@asynccontextmanager
async def _jsonl_fixture():
    root = tempfile.mkdtemp(prefix="nova-jsonl-conformance-")
    try:
        yield _CwdInjectingRepo({"fs": LocalJsonlFileSystem(), "sessions_root": root})
    finally:
        shutil.rmtree(root, ignore_errors=True)


conformance = create_session_backend_conformance(_jsonl_fixture)


CASES = [(case.group, case.name) for case in conformance]


@pytest.mark.parametrize(("group", "name"), CASES, ids=[f"{g}::{n}" for g, n in CASES])
async def test_jsonl_backend_conformance(group: str, name: str) -> None:
    case = next(c for c in conformance if c.group == group and c.name == name)
    await case.run()
