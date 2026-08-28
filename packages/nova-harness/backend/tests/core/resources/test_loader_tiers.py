"""资源加载优先级分层与来源标签测试。

分层语义（对齐 pi 的单通道设计）：resolver（settings/自动发现/包）
> additional（CLI --skill/--prompt-template 与 SDK 注入共用）> 扩展贡献；
同名冲突 first-wins。显式扩展路径是例外：它们经 temporary 通道排在最前
（对齐 pi 的 -e 语义）。
"""

from pathlib import Path
from typing import Optional

import pytest

from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.package import PackageManager


class _FakeSettingsManager:
    def __init__(self, project_trusted: bool = True):
        self._project_trusted = project_trusted
        self._global = Settings()
        self._project = Settings()

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def set_project_trusted(self, value: bool) -> None:
        self._project_trusted = value

    def reload(self) -> None:
        pass

    def get_global_settings(self) -> Settings:
        return self._global

    def get_project_settings(self) -> Settings:
        return self._project

    def get_package_sources(
        self, local: bool = False, base_dir: Optional[str] = None
    ) -> list[PackageSourceSpec]:
        return []


def _make_loader(cwd: Path, agent_dir: Path, **kwargs) -> DefaultResourceLoader:
    settings_manager = _FakeSettingsManager(project_trusted=True)
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    kwargs.setdefault("no_extensions", True)
    kwargs.setdefault("no_tools", True)
    return DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            package_manager=package_manager,
            **kwargs,
        )
    )


def _write_skill(dir_path: Path, name: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    skill_file = dir_path / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\n", encoding="utf-8"
    )
    return skill_file


@pytest.mark.asyncio
async def test_additional_skill_loses_to_project_same_name(tmp_path: Path) -> None:
    """additional 通道（--skill / SDK 共用）同名冲突时输给项目资源（对齐 pi：
    策展环境优先，显式传入只是追加）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    (cwd / ".nova" / "backend" / "skills").mkdir(parents=True)
    agent_dir.mkdir()
    project_file = _write_skill(cwd / ".nova" / "backend" / "skills" / "dup", "dup")
    _write_skill(tmp_path / "sdk" / "dup", "dup")

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_skill_paths=[str((tmp_path / "sdk" / "dup"))],
        no_prompt_templates=True,
    )
    await loader.reload()

    skills = loader.get_skills()["skills"]
    assert skills["dup"].file_path == str(project_file.resolve())
    # 同名冲突应产生 collision 诊断
    diagnostics = loader.get_skills()["diagnostics"]
    assert any(d.category == "collision" for d in diagnostics)


@pytest.mark.asyncio
async def test_additional_prompt_loses_to_project_same_name(tmp_path: Path) -> None:
    """additional 通道（--prompt-template / SDK 共用）同名冲突时输给项目 prompt。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    proj_prompts = cwd / ".nova" / "backend" / "prompts"
    proj_prompts.mkdir(parents=True)
    agent_dir.mkdir()
    (proj_prompts / "dup.md").write_text(
        "---\ndescription: project\n---\nproject body", encoding="utf-8"
    )
    cli_prompts = tmp_path / "cli-prompts"
    cli_prompts.mkdir()
    (cli_prompts / "dup.md").write_text(
        "---\ndescription: cli\n---\ncli body", encoding="utf-8"
    )

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_prompt_template_paths=[str(cli_prompts)],
        no_skills=True,
    )
    await loader.reload()

    prompts = loader.get_prompts()["prompts"]
    dup = next(p for p in prompts if p.name == "dup")
    assert "project body" in dup.content


@pytest.mark.asyncio
async def test_additional_extension_paths_come_first_with_cli_source(
    tmp_path: Path,
) -> None:
    """显式传入的扩展路径排在最前，来源标签为 cli/temporary（对齐 TS）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    proj_ext = cwd / ".nova" / "backend" / "extensions"
    proj_ext.mkdir(parents=True)
    agent_dir.mkdir()
    (proj_ext / "proj.py").write_text("def extension(api): pass", encoding="utf-8")
    cli_ext = tmp_path / "cli_ext.py"
    cli_ext.write_text("def extension(api): pass", encoding="utf-8")

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_extension_paths=[str(cli_ext)],
        no_extensions=False,
        no_skills=True,
        no_prompt_templates=True,
    )
    await loader.reload()

    extensions = loader.get_extensions().extensions
    assert len(extensions) == 2
    first = extensions[0]
    assert str(Path(first.path).resolve()) == str(cli_ext.resolve())
    assert first.source_info.source == "cli"
    assert first.source_info.scope == "temporary"


@pytest.mark.asyncio
async def test_extend_resources_resolves_against_loader_cwd(tmp_path: Path) -> None:
    """扩展贡献的相对路径相对 loader cwd 解析，而非进程 cwd。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()
    _write_skill(cwd / "rel-skill", "rel-skill")

    loader = _make_loader(cwd, agent_dir, no_prompt_templates=True)

    from nova_harness.core.types.resources.extension_paths import (
        ResourceExtensionPathEntry,
        ResourceExtensionPaths,
    )

    loader.extend_resources(
        ResourceExtensionPaths(
            skill_paths=[ResourceExtensionPathEntry(path="rel-skill")]
        )
    )

    skills = loader.get_skills()["skills"]
    assert "rel-skill" in skills
    assert skills["rel-skill"].file_path.startswith(str(cwd.resolve()))


@pytest.mark.asyncio
async def test_preloaded_inline_extensions_reused_factories_not_rerun(
    tmp_path: Path,
) -> None:
    """preloaded 提供时 inline 扩展实例整体复用，工厂不重跑——
    runtime（共享 event_bus）被复用时，重跑工厂会重复注册事件处理器。"""
    from nova_harness.core.extensions.event_bus import ExtensionEventBus
    from nova_harness.core.resources.loaders.extensions import load_extensions

    calls = []

    def factory(api):
        calls.append(1)
        api.registerCommand(
            "cmd", {"description": "x", "handler": lambda args, ctx: None}
        )

    bus = ExtensionEventBus()
    first = await load_extensions(
        str(tmp_path),
        str(tmp_path),
        None,
        bus,
        [],
        extension_factories=[factory],
    )
    assert len(calls) == 1
    inline_ext = next(e for e in first.extensions if e.path.startswith("<inline:"))

    second = await load_extensions(
        str(tmp_path),
        str(tmp_path),
        None,
        bus,
        [],
        runtime=first.runtime,
        preloaded=first,
        extension_factories=[factory],
    )

    assert len(calls) == 1
    assert any(e is inline_ext for e in second.extensions)


@pytest.mark.asyncio
async def test_additional_skill_gets_default_source_info(tmp_path: Path) -> None:
    """additional 通道的 skill 合成默认 SourceInfo（对齐 TS）：
    标准资源根外 → temporary；全局 skills 根（backend/ 半区）下 → user。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()
    outside = _write_skill(tmp_path / "outside" / "s", "s")
    inside = _write_skill(agent_dir / "backend" / "skills" / "s2", "s2")

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_skill_paths=[str(outside.parent), str(inside.parent)],
        no_skills=True,  # 禁用 resolver，隔离出 additional 通道的默认合成
        no_prompt_templates=True,
    )
    await loader.reload()

    skills = loader.get_skills()["skills"]
    assert skills["s"].source_info is not None
    assert skills["s"].source_info.scope == "temporary"
    assert skills["s2"].source_info is not None
    assert skills["s2"].source_info.scope == "user"


@pytest.mark.asyncio
async def test_additional_prompt_gets_default_source_info(tmp_path: Path) -> None:
    """additional 通道的 prompt 合成默认 SourceInfo：
    项目 prompts 根下 → project；根外 → temporary。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    proj_prompts = cwd / ".nova" / "backend" / "prompts"
    proj_prompts.mkdir(parents=True)
    agent_dir.mkdir()
    (proj_prompts / "p.md").write_text(
        "---\ndescription: p\n---\np body", encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "q.md").write_text(
        "---\ndescription: q\n---\nq body", encoding="utf-8"
    )

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_prompt_template_paths=[
            str(proj_prompts / "p.md"),
            str(elsewhere / "q.md"),
        ],
        no_skills=True,
        no_prompt_templates=True,  # 禁用 resolver，隔离出 additional 通道
    )
    await loader.reload()

    prompts = {p.name: p for p in loader.get_prompts()["prompts"]}
    assert prompts["p"].source_info is not None
    assert prompts["p"].source_info.scope == "project"
    assert prompts["q"].source_info is not None
    assert prompts["q"].source_info.scope == "temporary"


@pytest.mark.asyncio
async def test_missing_additional_skill_path_reports_once(tmp_path: Path) -> None:
    """缺失的 additional skill 路径只产生一条诊断（加载阶段的 warning），
    不再叠加 missing-path error（对齐 TS 按 path 去重的语义）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()
    missing = tmp_path / "no-such-skill"

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_skill_paths=[str(missing)],
        no_prompt_templates=True,
    )
    await loader.reload()

    diagnostics = [
        d for d in loader.get_skills()["diagnostics"] if str(missing) in d.path
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"


@pytest.mark.asyncio
async def test_missing_additional_prompt_path_reports_error(tmp_path: Path) -> None:
    """缺失的 additional prompt 路径产生一条 error 诊断（prompt 加载阶段
    对缺失路径静默，missing-path error 是唯一报告，不触发去重）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()
    missing = tmp_path / "no-such-prompt.md"

    loader = _make_loader(
        cwd,
        agent_dir,
        additional_prompt_template_paths=[str(missing)],
        no_skills=True,
    )
    await loader.reload()

    diagnostics = [
        d for d in loader.get_prompts()["diagnostics"] if str(missing) in d.path
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "error"
