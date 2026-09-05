"""真实 bundle 全生命周期联动（包管理 × 会话装配）。

真实路径双包：requires 顺序门（coding 无 base 拒装）→ 装配验证（工具/命令/
角色注册）→ requires 卸载守护（base 被引用拒卸）→ 卸载后摘除干净。
冻结形态（sys.frozen）下跑：包自安装走 sys.path 挂载（不 pip -e、不污染
当前环境），pip 依赖经 --no-deps 跳过（装配验证用不到 Pillow/rg）。

标 integration：真实包管理器 + 真实会话装配（不调 LLM，无需 API key）。
"""

import sys
from pathlib import Path

import pytest
from nova_harness.core.package.manager import PackageManager

REPO_ROOT = Path(__file__).resolve().parents[4]
BASE_SRC = str(REPO_ROOT / "bundles" / "nova_base")
CODING_SRC = str(REPO_ROOT / "bundles" / "nova_coding_agent")

pytestmark = pytest.mark.integration


@pytest.fixture()
def pkg_env(tmp_path, monkeypatch):
    """冻结形态 + 空 _MEIPASS（内建通道零动作）+ 离线 + 隔离 agent 目录。"""
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "proj"
    agent_dir.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv("NOVA_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    return agent_dir, cwd


def _manager(agent_dir: Path, cwd: Path) -> PackageManager:
    return PackageManager(agent_dir=str(agent_dir), cwd=str(cwd))


async def _tool_names(cwd: Path) -> set[str]:
    from nova_harness import CreateAgentSessionOptions, create_agent_session
    from nova_harness.core.harness.session import SessionManager

    result = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=str(cwd), session_manager=SessionManager.in_memory(str(cwd))
        )
    )
    session = result.session
    try:
        return {t.name for t in session.get_all_tools()}
    finally:
        session.dispose()


@pytest.mark.asyncio
async def test_real_bundle_lifecycle(pkg_env):
    agent_dir, cwd = pkg_env
    manager = _manager(agent_dir, cwd)

    # 1. requires 顺序门：先装 coding（requires nova-base）必须拒绝
    with pytest.raises(Exception, match="nova-base"):
        manager.install_and_persist(CODING_SRC, no_deps=True, quiet=True)

    # 2. 正装：base → coding
    manager.install_and_persist(BASE_SRC, no_deps=True, quiet=True)
    manager.install_and_persist(CODING_SRC, no_deps=True, quiet=True)

    # 3. 装配验证：工具（coding 8 + base 2）、命令（session_commands 系）、角色
    tools = await _tool_names(cwd)
    assert {"bash", "read", "write", "edit", "grep", "find", "ls", "subagent"} <= tools
    assert {"question", "todo"} <= tools

    from nova_harness import CreateAgentSessionOptions, create_agent_session
    from nova_harness.core.harness.session import SessionManager

    result = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=str(cwd), session_manager=SessionManager.in_memory(str(cwd))
        )
    )
    session = result.session
    try:
        runner = session._extension_runner
        command_names = (
            {c.name for c in runner.get_registered_commands()} if runner else set()
        )
        assert {"tree", "fork", "login", "model", "plan", "tools"} <= command_names
        # 角色注册：coding_agent 可按名直接起会话
        from nova_harness.core.sdk import create_agent_session_by_name

        named = await create_agent_session_by_name(
            "coding_agent",
            CreateAgentSessionOptions(
            cwd=str(cwd), session_manager=SessionManager.in_memory(str(cwd))
        ),
        )
        named.session.dispose()
    finally:
        session.dispose()

    # 4. 基础包守护：nova-base 不可卸载（会话基础设施；守护先于 requires 判定）
    with pytest.raises(Exception, match="基础包"):
        manager.uninstall("nova-base")

    # 5. 卸载 coding → coding 能力摘除，base 的会话基础设施保留
    manager.uninstall("nova-coding-agent")

    tools = await _tool_names(cwd)
    assert "bash" not in tools  # coding 工具摘除
    assert "subagent" not in tools
    assert "todo" in tools  # base 工具保留
    assert "question" in tools

    from nova_harness import CreateAgentSessionOptions, create_agent_session
    from nova_harness.core.harness.session import SessionManager

    result = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=str(cwd), session_manager=SessionManager.in_memory(str(cwd))
        )
    )
    session = result.session
    try:
        runner = session._extension_runner
        command_names = (
            {c.name for c in runner.get_registered_commands()} if runner else set()
        )
        assert "plan" not in command_names  # coding 扩展摘除
        assert "tree" in command_names  # base 命令保留
        assert "login" in command_names
    finally:
        session.dispose()
