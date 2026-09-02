"""AgentManager 单元测试（设计定案 §6：注册表视图 + 旋钮 + 解析链 + yaml 写回）。

覆盖：
- 默认解析链：保持现状 > 显式指定 > 第一个可用 > "base_agent"；显式名
  不存在即抛错（列出可用名）；
- change_agent 校验：未知名抛 ValueError 并列出可用名；
- 运行时视图：delegatable_agents 全量（无主从划分）+ delegation_menu
  source 标签（scope · origin）+ agent_entries current 标记；
- CapabilitySelection 汇集：报告经注入的 provider 读取（生产/汇集归
  AgentSession，manager 互不调用）；
- save_agent：三来源分流（包→影子 user 级 / user、project→就地写回 /
  as_name→新名 user 级）、生效状态序列化（persona override、tools 激活集、
  名单三态、model ref）、写盘后 reload 生效。
"""

from pathlib import Path
from typing import Callable, Dict, Optional

import pytest
import yaml
from nova_harness.core.harness.agents import AgentManager
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.selection import CapabilitySelection
from nova_harness.core.types.resources.tools import ToolInfo


class _FakeLoader:
    """最小 ResourceLoader 假件：承载 agents 注册表 + reload 重扫回调。"""

    def __init__(
        self,
        agents: Optional[Dict[str, AgentConfig]] = None,
        on_reload: Optional[Callable[[], Dict[str, AgentConfig]]] = None,
    ) -> None:
        self._agents = dict(agents or {})
        self._on_reload = on_reload
        self.reload_calls = 0

    def get_agents(self) -> Dict[str, AgentConfig]:
        return dict(self._agents)

    async def reload(self) -> None:
        self.reload_calls += 1
        if self._on_reload is not None:
            self._agents = self._on_reload()


class _FakeToolsManager:
    """最小 ToolsManager 假件：激活集 + 选配报告。"""

    def __init__(
        self,
        active: Optional[list] = None,
        report: Optional[list] = None,
    ) -> None:
        self._active = list(active or [])
        self._report = list(report or [])

    def get_active_tools(self) -> list:
        return list(self._active)

    @property
    def selection_report(self) -> list:
        return list(self._report)


class _FakePersonaManager:
    """最小 PersonaManager 假件：只有 override 旋钮读口。"""

    def __init__(self, override: Optional[str] = None) -> None:
        self.current_override = override


def _config(
    name: str,
    source_info: Optional[SourceInfo] = None,
    **kwargs,
) -> AgentConfig:
    return AgentConfig(name=name, agent_dir="", source_info=source_info, **kwargs)


def _source(scope: str, origin: str, path: str = "/x.yaml") -> SourceInfo:
    return SourceInfo(path=path, scope=scope, origin=origin)


# =============================================================================
# 默认解析链
# =============================================================================


def test_resolve_explicit_wins_over_first_available():
    """显式指定压过字母序兜底。"""
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    assert manager.resolve_current("zzz") == "zzz"
    assert manager.current == "zzz"


def test_resolve_falls_back_to_first_available():
    """未显式指定时回退到第一个可用 agent。"""
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    assert manager.resolve_current(None) == "aaa"


def test_resolve_falls_back_to_base_agent_when_empty():
    """注册表为空时兜底 base_agent。"""
    manager = AgentManager(resource_loader=_FakeLoader())
    assert manager.resolve_current(None) == "base_agent"
    assert manager.current == "base_agent"
    assert manager.current_config() is None


def test_resolve_explicit_unknown_raises():
    """显式名不存在即抛错（拼错名字不能静默落到别的 agent 上）。"""
    loader = _FakeLoader({"aaa": _config("aaa")})
    manager = AgentManager(resource_loader=loader)
    with pytest.raises(ValueError, match="Available agents: aaa"):
        manager.resolve_current("ghost")


def test_resolve_explicit_unknown_raises_with_empty_registry():
    """显式名不存在且注册表为空：抛错并提示无已安装 agent。"""
    manager = AgentManager(resource_loader=_FakeLoader())
    with pytest.raises(ValueError, match="No agents installed"):
        manager.resolve_current("ghost")


def test_resolve_keeps_current_after_change():
    """保持现状优先：change_agent 后 resolve 不再看显式参数（reload 语义）。"""
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    manager.change_agent("zzz")
    assert manager.resolve_current("aaa") == "zzz"


def test_resolve_without_loader_is_base_agent():
    """无 loader：注册表视为空，兜底 base_agent。"""
    manager = AgentManager(resource_loader=None)
    assert manager.agents == {}
    assert manager.resolve_current(None) == "base_agent"


# =============================================================================
# change_agent 旋钮
# =============================================================================


def test_change_agent_switches_current():
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    manager.change_agent("zzz")
    assert manager.current == "zzz"
    assert manager.current_config().name == "zzz"


def test_change_agent_unknown_raises_with_available_names():
    """未知名抛 ValueError 并列出可用名。"""
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    with pytest.raises(ValueError, match=r"Available agents: aaa, zzz"):
        manager.change_agent("ghost")


# =============================================================================
# 运行时视图（委派菜单 / 选择器数据）
# =============================================================================


def test_delegatable_agents_returns_all_sorted():
    """全部注册 agent（按名字典序，无主从划分）。"""
    loader = _FakeLoader(
        {"zzz": _config("zzz"), "aaa": _config("aaa"), "mid": _config("mid")}
    )
    manager = AgentManager(resource_loader=loader)
    assert [c.name for c in manager.delegatable_agents()] == ["aaa", "mid", "zzz"]


def test_delegation_menu_carries_source_tags():
    """菜单数据：name/description/source 标签（scope · origin）。"""
    loader = _FakeLoader(
        {
            "scout": _config(
                "scout",
                description="侦察",
                source_info=_source("project", "package"),
            ),
            "plain": _config("plain"),  # 无 source_info → 空标签
        }
    )
    manager = AgentManager(resource_loader=loader)
    menu = {item["name"]: item for item in manager.delegation_menu()}
    assert menu["scout"] == {
        "name": "scout",
        "description": "侦察",
        "source": "project · package",
    }
    assert menu["plain"] == {"name": "plain", "description": "", "source": ""}


def test_agent_entries_mark_current():
    """选择器数据：current 标记打在解析后的当前角色上。"""
    loader = _FakeLoader({"aaa": _config("aaa"), "zzz": _config("zzz")})
    manager = AgentManager(resource_loader=loader)
    manager.resolve_current(None)
    entries = {e["name"]: e for e in manager.agent_entries()}
    assert entries["aaa"]["current"] is True
    assert entries["zzz"]["current"] is False


# =============================================================================
# CapabilitySelection 汇集点
# =============================================================================


def test_capability_report_reads_injected_provider():
    """汇集点经注入 provider 透出多资源域报告（生产归 AgentSession）。"""
    report = [
        CapabilitySelection(resource_type="tools", name="ghost", status="missing"),
        CapabilitySelection(
            resource_type="user_tools", name="bash", status="disabled_by_settings"
        ),
    ]
    manager = AgentManager(
        resource_loader=_FakeLoader(),
        capability_report_provider=lambda: report,
    )
    assert manager.get_capability_report() == report


def test_capability_report_empty_without_provider():
    manager = AgentManager(resource_loader=_FakeLoader())
    assert manager.get_capability_report() == []


# =============================================================================
# save_agent：写入分流与生效状态序列化
# =============================================================================


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_save_user_source_writes_in_place(tmp_path: Path):
    """user 来源：就地写回原 yaml 路径；写盘后 reload 被调用。"""
    yaml_path = tmp_path / "user-home" / "agents" / "scout.yaml"
    _write_yaml(yaml_path, {"name": "scout", "description": "旧描述"})
    config = _config(
        "scout",
        description="旧描述",
        source_info=_source("user", "top-level", str(yaml_path)),
    )
    loader = _FakeLoader({"scout": config})
    manager = AgentManager(
        resource_loader=loader,
        agent_dir=str(tmp_path / "user-home"),
        tools_manager=_FakeToolsManager(active=["read", "bash"]),
    )
    manager.resolve_current(None)

    result = _run_save(manager)

    assert result["shadowed"] is False
    assert Path(result["path"]) == yaml_path
    data = _read_yaml(yaml_path)
    assert data["name"] == "scout"
    assert data["tools"] == ["read", "bash"]  # 当前激活集（生效状态）
    assert loader.reload_calls == 1


def test_save_package_source_shadows_to_user_level(tmp_path: Path):
    """包来源不可写红线：影子写到 <agent_dir>/agents/，包内 yaml 不动。"""
    pkg_yaml = tmp_path / "pkg" / "agents" / "scout.yaml"
    _write_yaml(pkg_yaml, {"name": "scout", "description": "包内原文"})
    config = _config(
        "scout",
        description="包内原文",
        source_info=_source("user", "package", str(pkg_yaml)),
    )
    loader = _FakeLoader({"scout": config})
    agent_dir = tmp_path / "user-home"
    manager = AgentManager(
        resource_loader=loader,
        agent_dir=str(agent_dir),
        tools_manager=_FakeToolsManager(active=["read"]),
    )
    manager.resolve_current(None)

    result = _run_save(manager)

    assert result["shadowed"] is True
    assert Path(result["path"]) == agent_dir / "agents" / "scout.yaml"
    assert _read_yaml(Path(result["path"]))["tools"] == ["read"]
    # 包内 yaml 未被触碰
    assert _read_yaml(pkg_yaml) == {"name": "scout", "description": "包内原文"}


def test_save_as_writes_new_name_to_user_level(tmp_path: Path):
    """as_name：按新名写到 user 级（save-as 不切换当前角色）。"""
    yaml_path = tmp_path / "proj" / ".nova" / "agents" / "scout.yaml"
    _write_yaml(yaml_path, {"name": "scout"})
    config = _config(
        "scout", source_info=_source("project", "top-level", str(yaml_path))
    )
    loader = _FakeLoader({"scout": config})
    agent_dir = tmp_path / "user-home"
    manager = AgentManager(resource_loader=loader, agent_dir=str(agent_dir))
    manager.resolve_current(None)

    result = _run_save(manager, as_name="my_scout")

    assert result["name"] == "my_scout"
    assert result["shadowed"] is False
    target = agent_dir / "agents" / "my_scout.yaml"
    assert Path(result["path"]) == target
    assert _read_yaml(target)["name"] == "my_scout"
    # 原 yaml 不动；当前角色不切换
    assert _read_yaml(yaml_path) == {"name": "scout"}
    assert manager.current == "scout"


def test_save_serializes_effective_state(tmp_path: Path):
    """生效状态序列化：persona override 单条目、名单三态、model ref。"""
    yaml_path = tmp_path / "user-home" / "agents" / "coding.yaml"
    _write_yaml(yaml_path, {"name": "coding"})
    config = _config(
        "coding",
        description="编程",
        model="openai/gpt-4o",
        persona=["../personas/core.md"],
        tools=[ToolInfo(name="read", description="")],
        extensions=["session_commands", "!plan_mode"],
        commands=None,  # 三态：None 不写
        skills=[],  # 三态：[] 写空列表
        source_info=_source("user", "top-level", str(yaml_path)),
    )
    loader = _FakeLoader({"coding": config})
    manager = AgentManager(
        resource_loader=loader,
        agent_dir=str(tmp_path / "user-home"),
        tools_manager=_FakeToolsManager(active=["read", "write"]),
        persona_manager=_FakePersonaManager(override="coding/core"),
    )
    manager.resolve_current(None)

    _run_save(manager, model="volcengine/deepseek-v3")

    data = _read_yaml(yaml_path)
    assert data["description"] == "编程"
    assert data["model"] == "volcengine/deepseek-v3"  # 当前模型 ref 优先
    assert data["persona"] == ["coding/core"]  # override 生效写 override 名
    assert data["tools"] == ["read", "write"]  # 激活集而非 yaml 原文
    assert data["extensions"] == ["session_commands", "!plan_mode"]  # 名单原文
    assert data["skills"] == []  # 显式空列表保留
    assert "commands" not in data  # None 不落盘


def test_save_without_override_keeps_yaml_persona_and_model(tmp_path: Path):
    """无 override 保留 yaml persona 原文；未给当前模型保留 yaml model。"""
    yaml_path = tmp_path / "user-home" / "agents" / "coding.yaml"
    _write_yaml(yaml_path, {"name": "coding"})
    config = _config(
        "coding",
        model="openai/gpt-4o",
        persona=["../personas/core.md"],
        source_info=_source("user", "top-level", str(yaml_path)),
    )
    loader = _FakeLoader({"coding": config})
    manager = AgentManager(
        resource_loader=loader,
        agent_dir=str(tmp_path / "user-home"),
        persona_manager=_FakePersonaManager(),
    )
    manager.resolve_current(None)

    _run_save(manager)

    data = _read_yaml(yaml_path)
    assert data["persona"] == ["../personas/core.md"]
    assert data["model"] == "openai/gpt-4o"


def test_save_without_source_info_writes_user_level(tmp_path: Path):
    """无 source_info（如 base_agent 兜底）：写到 user 级。"""
    loader = _FakeLoader()  # 注册表空 → current = base_agent，无 config
    agent_dir = tmp_path / "user-home"
    manager = AgentManager(resource_loader=loader, agent_dir=str(agent_dir))
    manager.resolve_current(None)

    result = _run_save(manager)

    assert Path(result["path"]) == agent_dir / "agents" / "base_agent.yaml"
    assert _read_yaml(Path(result["path"])) == {"name": "base_agent"}


def test_save_reload_makes_shadow_effective(tmp_path: Path):
    """写后 reload 生效：影子 yaml 重扫后进注册表（user 级覆盖包的语义闭环）。"""
    pkg_yaml = tmp_path / "pkg" / "agents" / "scout.yaml"
    _write_yaml(pkg_yaml, {"name": "scout", "description": "包内原文"})
    config = _config(
        "scout",
        description="包内原文",
        source_info=_source("user", "package", str(pkg_yaml)),
    )
    agent_dir = tmp_path / "user-home"

    def _rescan() -> Dict[str, AgentConfig]:
        # 模拟 loader reload：user 级影子优先于包（user > package 碰撞裁决）
        from nova_harness.core.resources.loaders.agent_config import (
            load_agent_config_from_yaml,
        )

        shadow = agent_dir / "agents" / "scout.yaml"
        if shadow.is_file():
            shadowed, _ = load_agent_config_from_yaml(str(shadow))
            if shadowed is not None:
                return {"scout": shadowed}
        return {"scout": config}

    loader = _FakeLoader({"scout": config}, on_reload=_rescan)
    manager = AgentManager(
        resource_loader=loader,
        agent_dir=str(agent_dir),
        tools_manager=_FakeToolsManager(active=["read"]),
    )
    manager.resolve_current(None)

    _run_save(manager)

    # reload 后活视图反映影子内容：影子 yaml 的 tools 激活集覆盖了
    # 包内原文（包内 yaml 无 tools 字段——读到 tools 即证明影子生效）
    assert loader.reload_calls == 1
    assert [t.name for t in manager.current_config().tools] == ["read"]


def _run_save(
    manager: AgentManager,
    as_name: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    import asyncio

    return asyncio.run(manager.save_agent(as_name, model))
