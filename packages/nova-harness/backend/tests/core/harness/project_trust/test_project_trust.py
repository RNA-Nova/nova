"""Project Trust 单元测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from nova_harness.core.extensions.runner import emit_project_trust_event
from nova_harness.core.harness.project_trust import (
    ProjectTrustStore,
    has_trust_requiring_project_resources,
    resolve_project_trusted,
)
from nova_harness.core.types.extensions import Extension
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ProjectTrustEvent,
    ProjectTrustEventResult,
    ProjectTrustUpdate,
    ResolveProjectTrustedOptions,
)
from nova_harness.core.types.ui import NoOpUIContext


def test_has_trust_requiring_project_resources_detects_settings(tmp_path):
    """检测到 .nova/settings.json 时返回 True。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    assert has_trust_requiring_project_resources(str(project_dir)) is True


def test_has_trust_requiring_project_resources_empty(tmp_path):
    """没有 .nova 目录时返回 False。"""
    assert has_trust_requiring_project_resources(str(tmp_path)) is False


def test_trust_store_round_trip(tmp_path):
    """TrustStore 读写与查找最近父目录。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set("/a/b/c", True)
    store.set("/a/b", False)

    assert store.get("/a/b/c") is True
    assert store.get("/a/b/d") is False  # 向上找到 /a/b
    assert store.get("/a") is None


def test_trust_store_set_many_and_remove(tmp_path):
    """set_many 支持更新和删除记录（decision=None 删除）。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set("/a/b", True)

    store.set_many([ProjectTrustUpdate(path="/a/b", decision=None)])
    assert store.get("/a/b") is None

    store.set_many(
        [
            ProjectTrustUpdate(path="/a/b", decision=False),
            ProjectTrustUpdate(path="/a/c", decision=True),
        ]
    )
    assert store.get("/a/b") is False
    assert store.get("/a/c") is True


def test_trust_store_set_none_removes_record(tmp_path):
    """set(path, None) 删除键而不是写入 null（对齐 TS set→setMany 包装）。"""
    import json

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set("/a/b", True)
    store.set("/a/b", None)

    assert store.get("/a/b") is None
    # 键被删除而不是写成 null
    with open(tmp_path / "trust.json", "r", encoding="utf-8") as f:
        assert str(Path("/a/b").resolve()) not in json.load(f)


def test_trust_store_null_entry_falls_through_to_parent(tmp_path):
    """文件中的 null 条目是"显式清除"标记：跳过并继续向上查找父目录。

    对齐 TS findNearestTrustEntry：null 不阻断继承链。
    """
    import json

    trust_file = tmp_path / "trust.json"
    trust_file.write_text(
        json.dumps(
            {
                str(Path("/a").resolve()): True,
                str(Path("/a/b").resolve()): None,
            }
        ),
        encoding="utf-8",
    )
    store = ProjectTrustStore(str(trust_file))

    # /a/b 的 null 不阻断，向上继承 /a 的 True
    assert store.get("/a/b") is True
    assert store.get("/a/b/c") is True


def test_trust_store_corrupted_file_raises(tmp_path):
    """损坏的 trust.json 必须抛错而不是静默重置（对齐 TS readTrustFile）。

    trust 是安全状态：静默重置会抹掉用户的 "do not trust" 决策，
    落回 default/ask 分支形成 trust 降级通道。
    """
    trust_file = tmp_path / "trust.json"
    trust_file.write_text("{not valid json", encoding="utf-8")
    store = ProjectTrustStore(str(trust_file))

    with pytest.raises(ValueError, match="Failed to read trust store"):
        store.get("/a")

    # 非 object 顶层
    trust_file.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="expected an object"):
        store.get("/a")

    # 非法值
    import json

    trust_file.write_text(json.dumps({"/a": "yes"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be true, false, or null"):
        store.get("/a")


def test_resolve_options_require_context_with_extensions(tmp_path):
    """传入 extensions_result 就必须提供 project_trust_context（对齐 TS 必填）。

    否则扩展的 trust 裁决会被静默跳过——配置错误应该响亮而不是安静。
    """
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    with pytest.raises(ValueError, match="project_trust_context is required"):
        ResolveProjectTrustedOptions(
            cwd=str(tmp_path),
            trust_store=store,
            extensions_result=object(),  # 任意非 None
        )


@pytest.mark.asyncio
async def test_resolve_project_trusted_no_resources(tmp_path):
    """无项目资源时始终信任。"""
    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(tmp_path),
            trust_store=store,
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_override(tmp_path):
    """trust_override 优先级最高。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            trust_override=True,
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_saved_decision(tmp_path):
    """已保存的信任记录生效。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    store.set(str(project_dir), False)

    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
        )
    )
    assert trusted is False


@pytest.mark.asyncio
async def test_resolve_project_trusted_no_ui_defaults_to_false(tmp_path):
    """无 UI 时存在项目资源默认不信任（与 TS 对齐）。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="ask",
        )
    )
    assert trusted is False


@pytest.mark.asyncio
async def test_resolve_project_trusted_default_always(tmp_path):
    """default_project_trust=always 时直接信任。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="always",
        )
    )
    assert trusted is True


@pytest.mark.asyncio
async def test_resolve_project_trusted_default_never(tmp_path):
    """default_project_trust=never 时直接不信任。"""
    project_dir = tmp_path / "project"
    nova_dir = project_dir / ".nova"
    nova_dir.mkdir(parents=True)
    (nova_dir / "settings.json").write_text("{}", encoding="utf-8")

    store = ProjectTrustStore(str(tmp_path / "trust.json"))
    trusted = await resolve_project_trusted(
        ResolveProjectTrustedOptions(
            cwd=str(project_dir),
            trust_store=store,
            default_project_trust="never",
        )
    )
    assert trusted is False


def test_has_trust_requiring_project_resources_agents_skills_empty(tmp_path):
    """空的 .agents/skills 目录也会触发信任检查。"""
    agents_skills = tmp_path / "project" / ".agents" / "skills"
    agents_skills.mkdir(parents=True)

    assert has_trust_requiring_project_resources(str(tmp_path / "project")) is True


def test_has_trust_requiring_project_resources_excludes_home_agents_skills(
    monkeypatch, tmp_path
):
    """~/.agents/skills 不触发信任检查。"""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    agents_skills = home / ".agents" / "skills"
    agents_skills.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: home)
    assert has_trust_requiring_project_resources(str(home)) is False


# -----------------------------------------------------------------------------
# emit_project_trust_event（扩展层独立函数，core/extensions/runner.py）
# -----------------------------------------------------------------------------


def _trust_ctx() -> ProjectTrustContext:
    return ProjectTrustContext(
        cwd="/tmp",
        has_ui=False,
        ui=NoOpUIContext(),
    )


def _extensions_result(*extensions) -> SimpleNamespace:
    return SimpleNamespace(extensions=list(extensions))


@pytest.mark.asyncio
async def test_emit_project_trust_yes_wins():
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "yes"}]},
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_no_wins():
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "no"}]},
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="no", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_skips_undecided():
    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                lambda event, ctx: {"trusted": "undecided"},
                lambda event, ctx: {"trusted": "yes", "remember": True},
            ]
        },
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=True)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_all_undecided_returns_none():
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "undecided"}]},
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result is None
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_async_handler():
    async def handler(event, ctx):
        return {"trusted": "yes"}

    ext = Extension(path="e1", handlers={"project_trust": [handler]})
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_error_does_not_break_others():
    def bad_handler(event, ctx):
        raise RuntimeError("boom")

    on_error_messages = []
    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                bad_handler,
                lambda event, ctx: {"trusted": "yes"},
            ]
        },
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext),
        ProjectTrustEvent(cwd="/tmp"),
        _trust_ctx(),
        on_error=on_error_messages.append,
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert len(errors) == 1
    assert "e1" in errors[0] and "boom" in errors[0]
    # on_error 回调同步收到同一条错误
    assert on_error_messages == errors


@pytest.mark.asyncio
async def test_emit_project_trust_garbage_value_counts_as_no():
    """非 yes/no/undecided 的返回值归为 no（对齐 TS：非 yes 即不信任）。"""
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "maybe"}]},
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="no", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_none_return_collected_as_error():
    """handler 返回 None 视为错误（对齐 TS 响亮失败），收集后继续询问后续扩展。"""
    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                lambda event, ctx: None,  # 忘了 return 的 handler
                lambda event, ctx: {"trusted": "yes"},
            ]
        },
    )
    on_error_messages = []
    result, errors = await emit_project_trust_event(
        _extensions_result(ext),
        ProjectTrustEvent(cwd="/tmp"),
        _trust_ctx(),
        on_error=on_error_messages.append,
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert len(errors) == 1
    assert "e1" in errors[0] and "returned None" in errors[0]
    assert on_error_messages == errors


@pytest.mark.asyncio
async def test_emit_project_trust_bool_true_counts_as_no():
    """布尔 True 不算 yes——只认字符串 "yes"（对齐 TS 与 Literal 类型契约）。"""
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": True}]},
    )
    result, errors = await emit_project_trust_event(
        _extensions_result(ext), ProjectTrustEvent(cwd="/tmp"), _trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="no", remember=False)
    assert errors == []
