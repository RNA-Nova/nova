"""
sdk/high_level.py 测试。
"""

from nova_harness.core.sdk import list_installed_agents


def test_list_installed_agents(tmp_path, monkeypatch):
    agent_dir = tmp_path / "agent"
    agents_dir = agent_dir / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "coding").mkdir()
    (agents_dir / "coding" / "description.md").write_text(
        "Coding agent", encoding="utf-8"
    )
    (agents_dir / "empty").mkdir()

    monkeypatch.setenv("NOVA_AGENT_DIR", str(agent_dir))
    try:
        agents = list_installed_agents()
        assert agents == ["coding"]
    finally:
        monkeypatch.delenv("NOVA_AGENT_DIR", raising=False)


def test_list_installed_agents_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_AGENT_DIR", str(tmp_path / "nonexistent"))
    try:
        assert list_installed_agents() == []
    finally:
        monkeypatch.delenv("NOVA_AGENT_DIR", raising=False)
