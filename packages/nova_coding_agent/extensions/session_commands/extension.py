"""默认 slash 命令扩展。

提供 /compact、/fork、/clone、/export、/import、/model、/session、
/name、/new、/reload、/tree、/trust、/untrust 等常用会话命令。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_harness.core.extensions.api import NovaExtensionAPI


def _parse_args(text: str) -> tuple[str, list[str]]:
    """把命令参数字符串拆成第一个词和剩余词列表。"""
    parts = text.strip().split()
    if not parts:
        return "", []
    return parts[0], parts[1:]


async def _compact(args: str, ctx: Any) -> None:
    instructions = args.strip() or None
    if instructions:
        await ctx.compact({"custom_instructions": instructions})
    else:
        await ctx.compact()


async def _fork(args: str, ctx: Any) -> None:
    entry_id, rest = _parse_args(args)
    if not entry_id:
        await ctx.send_message(
            {
                "type": "error",
                "text": "用法: /fork <entry_id> [at|before|after]",
            }
        )
        return
    position = rest[0] if rest else "after"
    if position not in ("at", "before", "after"):
        await ctx.send_message(
            {
                "type": "error",
                "text": "position 必须是 at、before 或 after",
            }
        )
        return
    await ctx.wait_for_idle()
    await ctx.fork(entry_id, position=position)


async def _clone(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    result = await ctx.clone()
    info = ctx.get_session_info()
    await ctx.send_message(
        {
            "type": "info",
            "text": f"已克隆会话: {info.get('file')}",
        }
    )


async def _export(args: str, ctx: Any) -> None:
    path = args.strip()
    if not path:
        await ctx.send_message(
            {
                "type": "error",
                "text": "用法: /export <path>",
            }
        )
        return
    await ctx.wait_for_idle()
    result = await ctx.export(path)
    await ctx.send_message(
        {
            "type": "info",
            "text": f"已导出到: {result.get('exported_to')}",
        }
    )


async def _import(args: str, ctx: Any) -> None:
    path = args.strip()
    if not path:
        await ctx.send_message(
            {
                "type": "error",
                "text": "用法: /import <path>",
            }
        )
        return
    await ctx.wait_for_idle()
    await ctx.import_session(path)
    info = ctx.get_session_info()
    await ctx.send_message(
        {
            "type": "info",
            "text": f"已导入并切换到会话: {info.get('id')}",
        }
    )


async def _model(args: str, ctx: Any) -> None:
    model_ref = args.strip()
    if not model_ref:
        current = ctx.get_model()
        name = f"{current.provider}/{current.id}" if current else "未选择"
        await ctx.send_message(
            {
                "type": "info",
                "text": f"当前模型: {name}",
            }
        )
        return
    await ctx.set_model(model_ref)


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
    await ctx.send_message(
        {
            "type": "info",
            "text": "\n".join(lines),
        }
    )


async def _name(args: str, ctx: Any) -> None:
    name = args.strip()
    if not name:
        current = ctx.get_session_name()
        await ctx.send_message(
            {
                "type": "info",
                "text": f"当前会话名称: {current or '(未命名)'}",
            }
        )
        return
    ctx.set_session_name(name)


async def _new(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    await ctx.new_session()
    await ctx.send_message(
        {
            "type": "info",
            "text": "已创建新会话",
        }
    )


async def _reload(args: str, ctx: Any) -> None:
    await ctx.wait_for_idle()
    await ctx.reload()
    await ctx.send_message(
        {
            "type": "info",
            "text": "已重新加载资源与扩展",
        }
    )


async def _tree(args: str, ctx: Any) -> None:
    target_id = args.strip()
    if target_id:
        await ctx.wait_for_idle()
        await ctx.navigate_tree(target_id)
    else:
        # 无参数时让 runtime 决定如何展示树（例如弹选择器）
        await ctx.navigate_tree("")


async def _trust(args: str, ctx: Any) -> None:
    ctx.trust_project()
    await ctx.send_message(
        {
            "type": "info",
            "text": "已信任当前项目",
        }
    )


async def _untrust(args: str, ctx: Any) -> None:
    ctx.untrust_project()
    await ctx.send_message(
        {
            "type": "info",
            "text": "已取消信任当前项目",
        }
    )


def extension(nova: NovaExtensionAPI) -> None:
    """注册默认会话 slash 命令。"""
    commands: Dict[str, Dict[str, Any]] = {
        "compact": {
            "description": "手动压缩会话上下文",
            "handler": _compact,
        },
        "fork": {
            "description": "在指定条目处 fork 会话: /fork <entry_id> [at|before|after]",
            "handler": _fork,
        },
        "clone": {
            "description": "克隆当前会话",
            "handler": _clone,
        },
        "export": {
            "description": "导出会话为 JSONL: /export <path>",
            "handler": _export,
        },
        "import": {
            "description": "从 JSONL 导入会话: /import <path>",
            "handler": _import,
        },
        "model": {
            "description": "切换或查看当前模型: /model [provider/id]",
            "handler": _model,
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
