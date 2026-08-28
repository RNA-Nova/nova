"""import_session 同名文件防护测试。"""

from pathlib import Path

import pytest

from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.sdk import create_agent_session_from_services
from nova_harness.core.types.session.config import CreateAgentSessionOptions


@pytest.mark.asyncio
async def test_import_session_does_not_overwrite_existing_file(tmp_path: Path):
    """导入与会话目录中已有文件同名的 JSONL 时，必须改名复制而非覆盖。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    session_dir = tmp_path / "sessions"
    external_dir = tmp_path / "external"
    for d in (cwd, agent_dir, session_dir, external_dir):
        d.mkdir(parents=True)

    # 造一个合法的源会话文件（含 assistant 消息触发落盘），并改名为与目标冲突的 foo.jsonl
    from nova_ai import AssistantMessage, TextContent, UserMessage

    src_manager = SessionManager.create(str(cwd), str(external_dir))
    src_manager.append_message(
        UserMessage(role="user", content=[TextContent(type="text", text="hi")])
    )
    src_manager.append_message(
        AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="hello")],
            provider="p",
            model="m",
        )
    )
    src_file = src_manager.get_session_file()
    assert src_file is not None
    colliding = external_dir / "foo.jsonl"
    Path(src_file).replace(colliding)

    # 目标会话目录中预置同名文件（内容是标记，不能被覆盖）
    existing = session_dir / "foo.jsonl"
    existing.write_text('{"type":"header","id":"keep-me"}\n', encoding="utf-8")

    services = await AgentSessionServices.create(
        cwd=str(cwd), agent_dir=str(agent_dir), project_trusted=True
    )
    dest_manager = SessionManager.create(str(cwd), str(session_dir))
    result = await create_agent_session_from_services(
        services, dest_manager, CreateAgentSessionOptions()
    )
    session = result.session

    await session.import_session(str(colliding))

    # 已有文件未被覆盖；导入复制到了带 -import- 后缀的新文件
    assert existing.read_text(encoding="utf-8") == '{"type":"header","id":"keep-me"}\n'
    imported_copies = list(session_dir.glob("foo-import-*.jsonl"))
    assert len(imported_copies) == 1
    assert session.session_file == str(imported_copies[0])
