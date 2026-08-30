"""默认 slash 命令扩展。

提供 /help、/compact、/fork、/clone、/export、/import、/model、/scoped-models、
/resume、/login、/logout、/session、/name、/new、/reload、/tree、/trust、
/untrust、/persona、/agent 等常用会话命令。

无参数时的交互化（对齐 pi）：/fork 弹用户消息选择器、/model 弹模型选择器、
/resume 弹会话选择器、/persona 弹人格选择器、/agent 弹角色选择器——均经
``ui.select`` 反向原语，无 UI 时退化为参数用法或错误提示；/scoped-models
无参数时文本列出 scoped 池（TUI 池面板在 frontend 段）。

/persona（persona 升格后的运行时切换器）：切换只换身份文本（人格部分），
能力面不动；选择结果经 ``persona_override`` 会话条目持久化（分支安全），
session_start / session_tree 从分支最新条目恢复。

/agent（AgentManager 的命令面）：切换当前角色（全量重建能力面）；/agent save
把当前生效状态物化回组合声明 yaml（包来源影子写 user 级），/agent save-as
<name> 按新名写 user 级。切换经 ``agent`` 会话条目持久化与分支恢复。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from nova_harness.core.config.auth.interaction import (
    LoginCancelledError,
    UIAuthInteraction,
)
from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.harness.session.listing import list_sessions_from_dir

from nova_coding_agent.ui_primitives import confirm, select_items


def _parse_args(text: str) -> tuple[str, list[str]]:
    """把命令参数字符串拆成第一个词和剩余词列表。"""
    parts = text.strip().split()
    if not parts:
        return "", []
    return parts[0], parts[1:]


def _reply(ctx: Any, text: str, level: str = "info") -> None:
    """命令反馈：转录卡片 + 持久化条目，不进 LLM 上下文（custom 条目结构性免疫——
    它不是消息，convert_to_llm 永远看不见；实时显示经 entry_appended → reducer
    映射 CustomItem 上线，恢复经 entries_to_items 同形）。"""
    ctx.append_entry("command_result", {"text": text, "level": level})


def _message_text(message: Any) -> str:
    """提取消息文本（content 为 str 或 content block 列表）。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _preview(text: str, limit: int = 60) -> str:
    """单行预览：取首行并截断。"""
    first = text.strip().split("\n", 1)[0]
    return first[:limit] + "…" if len(first) > limit else first


def _choice_index(choice: Optional[str], options: List[str]) -> Optional[int]:
    """把 select 返回的选项字符串映射回索引（取消/异常返回 None）。"""
    if choice is None:
        return None
    try:
        return options.index(choice)
    except ValueError:
        return None


async def _pick_user_message(ctx: Any) -> Optional[str]:
    """用户消息选择器（对齐 pi getUserMessagesForForking）：返回选中的 entry id。"""
    entries = ctx.session_manager.get_entries() if ctx.session_manager else []
    candidates: List[Tuple[str, str]] = []
    for entry in entries:
        if getattr(entry, "type", None) != "message":
            continue
        message = getattr(entry, "message", None)
        if getattr(message, "role", None) != "user":
            continue
        entry_id = getattr(entry, "id", "")
        if entry_id:
            candidates.append((entry_id, _preview(_message_text(message))))

    if not candidates:
        _reply(ctx, "会话中没有可分叉的用户消息", "error")
        return None

    recent = candidates[-20:][::-1]  # 最近 20 条，最新在前
    items = [
        {
            "value": entry_id,
            "label": text or "(空消息)",
            "description": entry_id[:8],
        }
        for entry_id, text in recent
    ]
    return await select_items(ctx.ui, "Fork from which message?", items)


def _auth_status_tag(model_runtime: Any, provider: str) -> str:
    """provider 认证状态标签（对齐 pi OAuthSelector）：

    已配置凭据 → ``✓ configured``；环境变量可得 → ``env: <VAR名>``；
    未配置（或状态查询不可用）→ 空串（无标签）。
    """
    get_status = getattr(model_runtime, "get_provider_auth_status", None)
    if get_status is None:
        return ""
    try:
        status = get_status(provider) or {}
    except Exception:
        return ""
    if not status.get("configured"):
        return ""
    if status.get("source") == "environment":
        label = status.get("label")
        if label:
            return f"env: {label}"
    return "✓ configured"


async def _pick_provider(ctx: Any, title: str) -> Optional[str]:
    """provider 选择器（候选取自已知模型的 provider 集合）。

    description 携带认证状态标签（``_auth_status_tag``，对齐 pi OAuthSelector）。
    """
    if ctx.model_runtime is None:
        _reply(ctx, "模型运行时不可用", "error")
        return None
    providers = sorted({m.provider for m in ctx.model_runtime.get_all()})
    if not providers:
        _reply(ctx, "没有已知 provider", "error")
        return None
    items: List[Dict[str, str]] = []
    for provider in providers:
        item: Dict[str, str] = {"value": provider, "label": provider}
        tag = _auth_status_tag(ctx.model_runtime, provider)
        if tag:
            item["description"] = tag
        items.append(item)
    return await select_items(ctx.ui, title, items)


async def _compact(args: str, ctx: Any) -> None:
    """手动压缩（空会话/已压缩等失败给真实错误，不静默）。"""
    instructions = args.strip() or None
    try:
        if instructions:
            await ctx.compact({"custom_instructions": instructions})
        else:
            await ctx.compact()
    except Exception as exc:
        _reply(ctx, f"压缩失败: {exc}", "error")


async def _fork(args: str, ctx: Any) -> None:
    entry_id, rest = _parse_args(args)
    position = rest[0] if rest else "after"

    if not entry_id:
        # 无参数：选择器从用户消息分叉（pi 语义——fork at 该消息，含它）。
        if not ctx.has_ui:
            _reply(ctx, "用法: /fork <entry_id> [at|before|after]", "error")
            return
        entry_id = await _pick_user_message(ctx)
        if entry_id is None:
            return  # 已取消或无候选（提示已发）
        position = "at"
    elif position not in ("at", "before", "after"):
        _reply(ctx, "position 必须是 at、before 或 after", "error")
        return
    await ctx.wait_for_idle()
    await ctx.fork(entry_id, position=position)


async def _clone(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    await ctx.clone()
    info = ctx.get_session_info()
    _reply(ctx, f"已克隆会话: {info.get('file')}")


async def _export(args: str, ctx: Any) -> None:
    path = args.strip()
    if not path:
        # 无参数：默认导出到 cwd 下 nova-session-<id前8>.jsonl（pi 默认文件名对位）
        info = ctx.get_session_info()
        session_id = str(info.get("id") or "session")[:8]
        path = f"nova-session-{session_id}.jsonl"
    await ctx.wait_for_idle()
    result = await ctx.export(path)
    _reply(ctx, f"已导出到: {result.get('exported_to')}")


async def _import(args: str, ctx: Any) -> None:
    path = args.strip()
    if not path:
        _reply(ctx, "用法: /import <path>", "error")
        return
    await ctx.wait_for_idle()
    await ctx.import_session(path)
    info = ctx.get_session_info()
    _reply(ctx, f"已导入并切换到会话: {info.get('id')}")


async def _model(args: str, ctx: Any) -> None:
    model_ref = args.strip()
    if model_ref:
        await ctx.set_model(model_ref)
        return

    # 无参数：选择器切换（对齐 pi 模型选择器）；无 UI 退化为显示当前模型。
    if not ctx.has_ui or ctx.model_runtime is None:
        current = ctx.model
        name = f"{current.provider}/{current.id}" if current else "未选择"
        _reply(ctx, f"当前模型: {name}")
        return

    models = ctx.model_runtime.get_available_snapshot()
    if not models:
        _reply(ctx, "没有可用模型", "error")
        return

    current = ctx.model
    current_ref = f"{current.provider}/{current.id}" if current else ""
    items = [
        {
            "value": f"{m.provider}/{m.id}",
            "label": m.id,
            "description": f"{m.provider}{'  ·  current' if f'{m.provider}/{m.id}' == current_ref else ''}",
            "group": m.provider,  # 分组头元信息（前端选择器按 provider 分段）
        }
        for m in models
    ]
    chosen = await select_items(ctx.ui, "Select model", items)
    if chosen is None:
        return
    await ctx.set_model(chosen)


async def _scoped_models(args: str, ctx: Any) -> None:
    """文本列出 scoped 模型池（headless 回退——TUI 下池面板在 frontend 段）。"""
    scoped = ctx.get_scoped_models()
    if not scoped:
        _reply(
            ctx,
            "Scoped 模型池为空（TUI 下 /scoped-models 面板配置；ctrl+p 循环启用集）",
        )
        return

    current = ctx.model
    current_ref = f"{current.provider}/{current.id}" if current else ""
    lines = []
    for index, entry in enumerate(scoped, 1):
        model = entry.model
        ref = f"{model.provider}/{model.id}"
        thinking = getattr(entry.thinking_level, "value", entry.thinking_level)
        parts = [f"{index}. {ref}"]
        if thinking:
            parts.append(f"thinking: {thinking}")
        if ref == current_ref:
            parts.append("current")
        lines.append("  ·  ".join(parts))
    _reply(ctx, "Scoped 模型池（ctrl+p 循环顺序）：\n" + "\n".join(lines))


async def _resume(args: str, ctx: Any) -> None:
    """浏览并恢复已有会话（会话选择器 + switch_session，对齐 pi /resume）。"""
    if not ctx.has_ui or ctx.session_manager is None:
        _reply(ctx, "/resume 需要 UI 与持久化会话目录", "error")
        return

    sessions = await list_sessions_from_dir(ctx.session_manager.get_session_dir())
    current_id = ctx.session_manager.get_session_id()
    candidates = [s for s in sessions if s.id != current_id]
    if not candidates:
        _reply(ctx, "没有其他会话可恢复")
        return
    candidates.sort(key=lambda s: s.modified or s.created or datetime.min, reverse=True)

    items = [
        {
            "value": s.path,
            "label": s.name or s.id[:8],
            "description": f"{s.message_count} 条消息 · {_preview(s.first_message, 40)}",
        }
        for s in candidates
    ]
    chosen = await select_items(ctx.ui, "Resume session", items)
    if chosen is None:
        return
    await ctx.wait_for_idle()
    await ctx.switch_session(chosen)


async def _login(args: str, ctx: Any) -> None:
    """交互式登录（OAuth device code 等），复用模型运行时的登录联动。

    device code 流程无 prompt 关卡（notify → 轮询直至过期，kimi 为 15 分钟），
    headless 下设备码无处展示、轮询无人授权——直接拒绝启动；有 UI 时把会话
    abort 信号接入交互，轮询可被用户中止。
    """
    if not ctx.has_ui:
        _reply(ctx, "/login 需要 UI（OAuth 交互无法展示）", "error")
        return
    provider = args.strip()
    if not provider:
        provider = await _pick_provider(ctx, "Login provider")
        if provider is None:
            return
    # 已配置凭据的 provider：确认后再重登（覆盖现有凭据不是无害操作——
    # OAuth 覆盖 refresh token、api key 覆盖存量值）
    existing = ctx.model_runtime.get_provider_auth_status(provider)
    if existing.get("configured"):
        ok = await confirm(
            ctx.ui,
            f"重新登录 {provider}",
            "该 provider 已配置凭据，重新登录将覆盖现有凭据。确定继续？",
        )
        if not ok:
            return
    try:
        credential = await ctx.model_runtime.login(
            provider, "oauth", UIAuthInteraction(ctx.ui, ctx.get_signal())
        )
    except LoginCancelledError:
        # 取消反馈归前端（Esc 发起、即时可靠）；后端任务取消后发反馈
        # 不可靠，且 headless 下 /login 在入口已拒绝——这里无受众
        return
    except Exception as exc:
        _reply(ctx, f"登录失败: {exc}", "error")
        return
    finally:
        # 清除状态行的等待提示（"Waiting for authentication..."——空 progress 即清除）
        ctx.ui.notify("notify", {"message": "", "type": "progress"})
    cred_type = getattr(credential, "type", None) or "oauth"
    _reply(ctx, f"已登录 {provider}（{cred_type}）")


async def _logout(args: str, ctx: Any) -> None:
    """删除 provider credential（联动模型刷新与可用性快照重算）。"""
    provider = args.strip()
    if not provider:
        if not ctx.has_ui:
            _reply(ctx, "用法: /logout <provider>", "error")
            return
        provider = await _pick_provider(ctx, "Logout provider")
        if provider is None:
            return
    # 登出前给准话：未配置 / 凭据不在 auth.json（环境变量、models.json、
    # 命令来源删不了）时不许报假成功
    status = ctx.model_runtime.get_provider_auth_status(provider)
    if not status.get("configured"):
        _reply(ctx, f"{provider} 未配置凭据，无需登出")
        return
    source = status.get("source")
    if source not in ("stored", "runtime"):
        label = status.get("label") or source
        _reply(
            ctx,
            f"{provider} 的凭据来自 {label}（不在 auth.json），无法经 /logout 移除——请删除对应环境变量或 models.json 配置",
        )
        return
    try:
        await ctx.model_runtime.logout(provider)
    except Exception as exc:
        _reply(ctx, f"登出失败: {exc}", "error")
        return
    _reply(ctx, f"已登出 {provider}")


async def _session(args: str, ctx: Any) -> None:
    info = ctx.get_session_info()
    lines = [
        f"ID: {info.get('id')}",
        f"名称: {info.get('name') or '(未命名)'}",
        f"CWD: {info.get('cwd')}",
        f"文件: {info.get('file')}",
        f"条目数: {info.get('entry_count')}",
        f"Leaf: {info.get('leaf_id')}",
        f"持久化: {info.get('persisted')}",
    ]
    _reply(ctx, "\n".join(lines))


async def _name(args: str, ctx: Any) -> None:
    name = args.strip()
    if not name:
        current = ctx.get_session_name()
        _reply(ctx, f"当前会话名称: {current or '(未命名)'}")
        return
    ctx.set_session_name(name)


async def _new(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    await ctx.new_session()
    _reply(ctx, "已创建新会话")


async def _reload(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    await ctx.reload()
    _reply(ctx, "已重新加载资源与扩展")


async def _tree(args: str, ctx: Any) -> None:
    target_id = args.strip()
    if target_id:
        await ctx.wait_for_idle()
        await ctx.navigate_tree(target_id)
        return
    # 无参数：会话树选择器（DFS 扁平化 + depth 缩进元信息，
    # pi tree-selector 的数据驱动 v1 对位——折叠/标签编辑等定制交互归后续）
    if not ctx.has_ui or ctx.session_manager is None:
        _reply(ctx, "/tree 需要 UI 与持久化会话目录", "error")
        return
    sm = ctx.session_manager
    entries = sm.get_entries()
    if not entries:
        _reply(ctx, "会话为空")
        return

    by_id = {e.id: e for e in entries}
    # 当前路径（叶 → 根回溯）——current 标记
    current_path: set = set()
    node = by_id.get(sm.get_leaf_id())
    while node is not None:
        current_path.add(node.id)
        node = by_id.get(getattr(node, "parent_id", None))

    # DFS 扁平化（根 = parent_id 缺失或悬空）
    flat: List[Tuple[Any, int]] = []

    def _visit(entry: Any, depth: int) -> None:
        flat.append((entry, depth))
        for child in sm.get_children(entry.id):
            _visit(child, depth + 1)

    for root in [e for e in entries if not e.parent_id or e.parent_id not in by_id]:
        _visit(root, 0)

    items = [
        {
            "value": entry.id,
            "label": _tree_entry_label(sm, entry),
            "description": ("current · " if entry.id in current_path else "")
            + entry.id[:8],
            "depth": depth,
        }
        for entry, depth in flat
    ]
    chosen = await select_items(ctx.ui, "会话树（选择目标节点）", items)
    if chosen is None or chosen == sm.get_leaf_id():
        return
    await ctx.wait_for_idle()
    await ctx.navigate_tree(chosen)


def _tree_entry_label(sm: Any, entry: Any) -> str:
    """树条目摘要：label 标签优先，message 取角色+预览，其余按类型标记。"""
    label = sm.get_label(entry.id)
    prefix = f"{label} · " if label else ""
    entry_type = getattr(entry, "type", "")
    if entry_type == "message":
        message = getattr(entry, "message", None)
        role = getattr(message, "role", "?")
        text = _preview(_message_text(message), 50)
        return f"{prefix}{role}: {text}" if text else f"{prefix}{role}"
    if entry_type == "label":
        return f"{prefix}[label]"
    return f"{prefix}[{entry_type or 'entry'}]"


async def _trust(args: str, ctx: Any) -> None:
    ctx.trust_project()
    # 信任翻转立即生效：重载资源（项目级工具/扩展/prompts 随即加载）
    await ctx.wait_for_idle()
    await ctx.reload()
    _reply(ctx, "已信任当前项目（决策已保存，资源已重新加载）")


async def _untrust(args: str, ctx: Any) -> None:
    ctx.untrust_project()
    # 取消信任同样立即生效：项目级资源随即卸载
    await ctx.wait_for_idle()
    await ctx.reload()
    _reply(ctx, "已取消信任当前项目（决策已保存，资源已重新加载）")


# ---------------------------------------------------------------------------
# /persona —— persona 运行时切换器（persona 升格：只换身份文本，能力面不动）
# ---------------------------------------------------------------------------

_DEFAULT_PERSONA_VALUE = ""  # 选择器首项："角色默认装配"（清除 override）


def _persona_source_tag(persona: Dict[str, Any]) -> str:
    """persona 来源标签（scope · origin），供选择器 description 列。"""
    parts = [p for p in (persona.get("scope"), persona.get("origin")) if p]
    return " · ".join(parts)


def _apply_persona_choice(ctx: Any, name: Optional[str]) -> bool:
    """应用选择：name 为 None 清除 override，否则设置；未知名报错并返回 False。"""
    if name is None:
        ctx.clear_persona_override()
        ctx.append_entry("persona_override", {"name": None})
        _reply(ctx, "已恢复角色默认人格装配")
        return True
    try:
        ctx.set_persona_override(name)
    except Exception:
        _reply(ctx, f"persona 不存在: {name}", "error")
        return False
    ctx.append_entry("persona_override", {"name": name})
    _reply(ctx, f"已切换 persona: {name}（仅人格文本，能力面不变）")
    return True


async def _persona(args: str, ctx: Any) -> None:
    name = args.strip()
    if name:
        # 带参数直切：/persona <name>；/persona default 恢复默认装配
        _apply_persona_choice(ctx, None if name == "default" else name)
        return

    personas = ctx.get_personas() or []
    current = ctx.get_persona_override()
    if not ctx.has_ui:
        # 无 UI 文本回退：列出当前 override 与注册表
        lines = [f"当前 persona: {current or '(角色默认装配)'}", ""]
        if personas:
            for p in personas:
                tag = _persona_source_tag(p)
                suffix = f"  {tag}" if tag else ""
                lines.append(f"{p.get('name', '?')}{suffix}")
        else:
            lines.append("(persona 注册表为空)")
        lines.append("")
        lines.append("用法: /persona <name> 切换；/persona default 恢复默认装配")
        _reply(ctx, "\n".join(lines))
        return

    items = [
        {
            "value": _DEFAULT_PERSONA_VALUE,
            "label": "角色默认装配",
            "description": "清除 override，恢复 yaml persona 装配"
            + ("  ·  current" if not current else ""),
        }
    ]
    for p in personas:
        tag = _persona_source_tag(p)
        description = tag or p.get("path", "")
        if p.get("name") == current:
            description = f"{description + '  ·  ' if description else ''}current"
        items.append(
            {
                "value": p.get("name", ""),
                "label": p.get("name", ""),
                "description": description,
            }
        )
    chosen = await select_items(ctx.ui, "Select persona", items)
    if chosen is None:
        return
    _apply_persona_choice(ctx, chosen or None)


_PERSONA_ENTRY_MISSING = object()  # 哨兵：分支无 persona_override 条目


def _latest_persona_override(ctx: Any) -> Any:
    """扫当前分支取最新一条 persona_override 条目的名字（无条目返回哨兵）。

    条目 data 形态：``{"name": str | None}``——None 表示"已清除 override"
    （显式清除也落条目，分支导航后所见即该历史点状态）。
    """
    sm = ctx.session_manager
    if sm is None:
        return _PERSONA_ENTRY_MISSING
    for entry in reversed(sm.get_branch()):
        if getattr(entry, "type", "") != "custom":
            continue
        if getattr(entry, "custom_type", "") != "persona_override":
            continue
        data = getattr(entry, "data", None)
        if isinstance(data, dict):
            name = data.get("name")
            return str(name) if isinstance(name, str) and name else None
    return _PERSONA_ENTRY_MISSING


async def _restore_persona_from_branch(ctx: Any) -> None:
    """session_start / session_tree：有条目则恢复 override，无则不动。"""
    saved = _latest_persona_override(ctx)
    if saved is _PERSONA_ENTRY_MISSING:
        return
    try:
        if saved is None:
            ctx.clear_persona_override()
        else:
            ctx.set_persona_override(saved)
    except Exception:
        # 恢复失败（如 persona 已随包卸载出注册表）不炸会话——保持当前状态
        pass


# ---------------------------------------------------------------------------
# /agent —— 角色切换与物化（AgentManager 的扩展命令面）
#
# 无参数弹选择器（description + source 标签，select_items）；/agent <name>
# 直切；/agent save 把当前生效状态写回组合声明 yaml（包来源影子写 user 级），
# /agent save-as <name> 按新名写到 user 级。切换经 ``agent`` 会话条目持久化
# （分支安全），session_start / session_tree 从分支最新条目恢复。
# ---------------------------------------------------------------------------


def _agent_source_tag(agent: Dict[str, Any]) -> str:
    """agent 来源标签（scope · origin），供选择器 description 列。"""
    parts = [p for p in (agent.get("scope"), agent.get("origin")) if p]
    return " · ".join(parts)


async def _apply_agent_choice(ctx: Any, name: str) -> bool:
    """应用角色切换：未知名报错并返回 False；成功落 ``agent`` 条目 + 反馈。"""
    try:
        await ctx.change_agent(name)
    except Exception:
        _reply(ctx, f"agent 不存在: {name}", "error")
        return False
    ctx.append_entry("agent", {"name": name})
    _reply(ctx, f"已切换角色: {name}")
    return True


async def _agent_save(ctx: Any, as_name: Optional[str]) -> None:
    """/agent save | save-as：物化当前生效状态为组合声明 yaml。"""
    try:
        result = await ctx.save_agent(as_name)
    except Exception as exc:
        _reply(ctx, f"保存失败: {exc}", "error")
        return
    name = result.get("name", "")
    path = result.get("path", "")
    if result.get("shadowed"):
        _reply(ctx, f"包内角色不可写——已影子保存到 user 级: {path}")
    else:
        _reply(ctx, f"已保存角色 {name}: {path}")
    ctx.append_entry("agent", {"action": "save", "name": name, "path": path})


async def _agent(args: str, ctx: Any) -> None:
    sub, rest = _parse_args(args)
    if sub == "save":
        await _agent_save(ctx, None)
        return
    if sub == "save-as":
        if not rest:
            _reply(ctx, "用法: /agent save-as <name>", "error")
            return
        await _agent_save(ctx, rest[0])
        return
    if sub:
        # 带参数直切：/agent <name>
        await _apply_agent_choice(ctx, sub)
        return

    agents = ctx.get_agents() or []
    current = next((a.get("name") for a in agents if a.get("current")), None)
    if not ctx.has_ui:
        # 无 UI 文本回退：列出当前角色与注册表
        lines = [f"当前角色: {current or '(无)'}", ""]
        if agents:
            for a in agents:
                tag = _agent_source_tag(a)
                suffix = f"  {tag}" if tag else ""
                lines.append(f"{a.get('name', '?')}{suffix}")
        else:
            lines.append("(agents 注册表为空)")
        lines.append("")
        lines.append("用法: /agent <name> 切换；/agent save | save-as <name> 保存")
        _reply(ctx, "\n".join(lines))
        return

    if not agents:
        _reply(ctx, "agents 注册表为空（无可切换角色）")
        return
    items = []
    for a in agents:
        tag = _agent_source_tag(a)
        description = a.get("description") or ""
        if tag:
            description = f"{tag}{'  ·  ' if description else ''}{description}"
        if a.get("current"):
            description = f"{description + '  ·  ' if description else ''}current"
        items.append(
            {
                "value": a.get("name", ""),
                "label": a.get("name", ""),
                "description": description,
            }
        )
    chosen = await select_items(ctx.ui, "Select agent", items)
    if chosen is None or chosen == current:
        return
    await _apply_agent_choice(ctx, chosen)


def _latest_agent_choice(ctx: Any) -> Optional[str]:
    """扫当前分支取最新一条角色切换条目的名字（无条目返回 None）。

    条目 data 形态：``{"name": str}``；保存动作条目（带 ``action`` 键）
    不影响角色恢复，跳过。
    """
    sm = ctx.session_manager
    if sm is None:
        return None
    for entry in reversed(sm.get_branch()):
        if getattr(entry, "type", "") != "custom":
            continue
        if getattr(entry, "custom_type", "") != "agent":
            continue
        data = getattr(entry, "data", None)
        if isinstance(data, dict) and not data.get("action"):
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
    return None


async def _restore_agent_from_branch(ctx: Any, reason: Optional[str] = None) -> None:
    """session_start / session_tree：有条目则恢复角色，无则不动。

    ``reason="agent_change"`` 时跳过——切换本身就是来源（分支最新条目是
    旧角色，恢复会把刚切的角色切回去）。
    """
    if reason == "agent_change":
        return
    saved = _latest_agent_choice(ctx)
    if saved is None:
        return
    # 已在该角色上则不重建（change_agent 是全量 runtime 重建，非廉价操作）
    current = next(
        (a.get("name") for a in (ctx.get_agents() or []) if a.get("current")), None
    )
    if saved == current:
        return
    try:
        await ctx.change_agent(saved)
    except Exception:
        # 恢复失败（如 agent 已随包卸载出注册表）不炸会话——保持当前角色
        pass


_TODO_STATUS_ICONS = {"pending": "○", "in_progress": "◐", "completed": "✓"}


def _latest_todo_list(ctx: Any) -> Optional[List[Dict[str, Any]]]:
    """扫当前分支取最新一条 todo 工具结果的清单（状态的单一事实源）。

    工具本身零服务端状态（全量替换语义），展示侧统一从会话历史派生——
    分支/树导航后所见即该历史点的快照。
    """
    sm = ctx.session_manager
    if sm is None:
        return None
    for entry in reversed(sm.get_branch()):
        if getattr(entry, "type", "") != "message":
            continue
        message = getattr(entry, "message", None)
        if getattr(message, "role", "") != "toolResult":
            continue
        if getattr(message, "tool_name", "") != "todo":
            continue
        details = getattr(message, "details", None) or {}
        todos = details.get("todos")
        if isinstance(todos, list):
            return todos
    return None


async def _todos(args: str, ctx: Any) -> None:
    """/todos 的 headless 回退（文本清单）；TUI 下由前端模态查看器接管。"""
    todos = _latest_todo_list(ctx)
    if todos is None:
        _reply(ctx, "当前分支还没有 todo 清单（让 agent 用 todo 工具创建）")
        return
    if not todos:
        _reply(ctx, "Todo 清单为空（已清空）")
        return
    completed = sum(1 for t in todos if t.get("status") == "completed")
    lines = [f"Todos — {completed}/{len(todos)} completed", ""]
    for t in todos:
        icon = _TODO_STATUS_ICONS.get(t.get("status"), "?")
        lines.append(f"{icon} {t.get('content', '')}")
    _reply(ctx, "\n".join(lines))


async def _help(args: str, ctx: Any) -> None:
    """列出全部已注册 slash 命令（含其他扩展注册的），按调用名排序。"""
    commands = sorted(
        ctx.get_commands() or [],
        key=lambda c: c.name,
    )
    if not commands:
        _reply(ctx, "当前没有已注册的命令。")
        return
    width = max(len(c.name) for c in commands)
    lines = [f"/{c.name.ljust(width)}  {c.description or '(无描述)'}" for c in commands]
    lines.append("")
    lines.append(
        "提示：输入 ! 前缀可直接执行 bash（!! 不进入上下文）；参数补全见编辑器自动提示。"
    )
    _reply(ctx, "\n".join(lines))


def extension(nova: NovaExtensionAPI) -> None:
    """注册默认会话 slash 命令。"""
    commands: Dict[str, Dict[str, Any]] = {
        "help": {
            "description": "列出全部可用命令",
            "handler": _help,
        },
        "compact": {
            "description": "手动压缩会话上下文",
            "handler": _compact,
        },
        "fork": {
            "description": "从用户消息分叉会话（无参数时弹选择器）: /fork [entry_id] [at|before|after]",
            "handler": _fork,
        },
        "clone": {
            "description": "克隆当前会话",
            "handler": _clone,
        },
        "export": {
            "description": "导出会话为 JSONL: /export <path.jsonl>（TUI 下无参数或 .html 后缀走前端 HTML 导出）",
            "handler": _export,
        },
        "import": {
            "description": "从 JSONL 导入会话: /import <path>",
            "handler": _import,
        },
        "model": {
            "description": "切换或查看当前模型（无参数时弹选择器）: /model [provider/id]",
            "handler": _model,
        },
        "scoped-models": {
            "description": "查看 scoped 模型池（TUI 下弹池面板，ctrl+p 循环启用集与顺序）",
            "handler": _scoped_models,
        },
        "resume": {
            "description": "浏览并恢复已有会话（弹选择器）",
            "handler": _resume,
        },
        "login": {
            "description": "配置 provider 认证（无参数时弹选择器）: /login [provider]",
            "handler": _login,
        },
        "logout": {
            "description": "移除 provider 认证（无参数时弹选择器）: /logout [provider]",
            "handler": _logout,
        },
        "session": {
            "description": "显示当前会话信息",
            "handler": _session,
        },
        "name": {
            "description": "设置或查看会话名称: /name [display_name]",
            "handler": _name,
        },
        "new": {
            "description": "创建新会话",
            "handler": _new,
        },
        "reload": {
            "description": "重新加载资源与扩展",
            "handler": _reload,
        },
        "tree": {
            "description": "导航会话树: /tree [target_id]",
            "handler": _tree,
        },
        "todos": {
            "description": "查看当前分支的 todo 清单（TUI 下弹模态查看器）",
            "handler": _todos,
        },
        "persona": {
            "description": "切换会话人格（无参数时弹选择器）: /persona [name|default]",
            "handler": _persona,
        },
        "agent": {
            "description": "切换/保存当前角色（无参数时弹选择器）: /agent [name|save|save-as <name>]",
            "handler": _agent,
        },
        "trust": {
            "description": "信任当前项目",
            "handler": _trust,
        },
        "untrust": {
            "description": "取消信任当前项目",
            "handler": _untrust,
        },
    }

    for name, options in commands.items():
        nova.registerCommand(name, options)

    # 条目持久化的分支恢复（session_start/session_tree 重放）：persona override
    # 与角色切换共用同一管道——同一扩展每事件注册一个合并 handler
    async def _restore_session_state(event: Any, ctx: Any) -> None:
        await _restore_persona_from_branch(ctx)
        await _restore_agent_from_branch(ctx, getattr(event, "reason", None))

    nova.on("session_start", lambda event, ctx: _restore_session_state(event, ctx))
    nova.on("session_tree", lambda event, ctx: _restore_session_state(event, ctx))
