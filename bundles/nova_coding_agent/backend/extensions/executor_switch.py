"""执行后端切换扩展（/executor 命令）。

- **用户主动切换**：``/executor``（选择器）/ ``/executor local`` /
  ``/executor remote <name|ws(s)-url>`` / ``/executor forget <name>``；
  v1 模型不能切；
- **三通道分离**（R2）：会话条目管记忆（分支恢复）、runtime 格管执行
  （``set_backend_selection`` 翻转，bash 引擎执行期直读）、notice 管
  用户回执（用户可见、不进模型上下文）；
- **远程接入边界**：本命令只切换既有 ws(s) 端点，不做 SSH 密钥引导、
  二进制上传、远程工作区供给或 ``ssh://`` 隧道管理。通用 stdio command
  环境仍可在 exec-server 配置中声明，由 SDK 直接消费；
- 端点清单来自 ``~/.nova/exec-server/config.toml`` 的 ``[[environments]]``
  （读写见 ``nova_coding_agent.executor.endpoints_store``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_exec_server_client import load_executor_config
from nova_harness.extensions.api import NovaExtensionAPI

from nova_coding_agent.executor import (
    BackendSelection,
    SpawnPolicy,
    get_backend_selection,
    set_backend_selection,
)
from nova_coding_agent.ui_primitives import notify_message, select_items

_ENTRY_TYPE = "executor_backend"
_USAGE = "用法：/executor [local|remote <name|ws(s)-url>|forget <name>]"


def _is_ws_url(url: str) -> bool:
    return url.startswith(("ws://", "wss://"))


def _attach_policy(ctx: Any, selection: BackendSelection) -> None:
    """物化套餐策略并挂到 selection 上。

    策略作用目录三态：显式远程执行上下文取 ``remote_cwd``；本地回环
    executor（url 为空）取本地 cwd；普通 ws 直连未声明远程 cwd 时不沙箱。
    """
    if selection.backend != "executor":
        return
    if selection.remote_cwd:
        effective_cwd: Optional[str] = selection.remote_cwd
    elif not selection.url:
        effective_cwd = getattr(ctx, "cwd", None)
    else:
        effective_cwd = None

    config = load_executor_config(project_trusted=False)
    resolved = config.resolve_execution(effective_cwd)
    if resolved.sandbox is None:
        selection.spawn_policy = None
        return
    selection.spawn_policy = SpawnPolicy(
        sandbox=resolved.sandbox.model_dump(by_alias=True, exclude_none=True)
    )


def _current_label(selection: BackendSelection) -> str:
    if selection.backend == "executor":
        return f"executor（{selection.url or '本地沙箱'}）"
    return "local（本地直接执行）"


def _endpoints_from_config() -> List[Dict[str, Optional[str]]]:
    """从 exec-server 配置根读已登记 ws(s) 端点。"""
    from nova_coding_agent.executor.endpoints_store import load_endpoints

    return load_endpoints()


def extension(nova: NovaExtensionAPI) -> None:
    """注册 /executor 命令与分支恢复钩子。"""

    async def _switch_to(
        selection: BackendSelection,
        ctx: Any,
        label: str,
    ) -> None:
        """翻转 runtime 格 + 会话条目 + 系统提示词 + 用户回执。"""
        _attach_policy(ctx, selection)
        set_backend_selection(selection)
        ctx.append_entry(
            _ENTRY_TYPE, {"backend": selection.backend, "url": selection.url}
        )
        refresh = getattr(ctx, "refresh_system_prompt", None)
        if refresh is not None:
            refresh()
        notify_message(ctx.ui, f"执行后端已切换：{label}")

    async def _switch_remote_url(url: str, ctx: Any, label: str) -> None:
        """ws(s):// 直连切换（无供给——端点自管活）。"""
        if not _is_ws_url(url):
            notify_message(ctx.ui, "远程端点仅支持 ws:// 或 wss://", "error")
            return
        await _switch_to(BackendSelection(backend="executor", url=url), ctx, label)

    async def _executor_command(args: str, ctx: Any) -> None:
        current = get_backend_selection()
        arg = args.strip()

        if arg == "local":
            await _switch_to(
                BackendSelection(backend="local"), ctx, "local（本地直接执行）"
            )
            return

        if arg.startswith("remote"):
            target = arg[len("remote") :].strip()
            if not target:
                notify_message(
                    ctx.ui,
                    "用法：/executor remote <name|ws(s)-url>",
                    "error",
                )
                return
            if target == "local":
                # 旧用法兼容：/executor remote local = 本地回环沙箱
                await _switch_to(
                    BackendSelection(backend="executor"),
                    ctx,
                    "executor（本地沙箱）",
                )
                return
            if "://" in target:
                await _switch_remote_url(target, ctx, f"executor（{target}）")
                return
            for endpoint in _endpoints_from_config():
                if endpoint["name"] == target:
                    url = endpoint["url"]
                    assert url is not None
                    await _switch_remote_url(url, ctx, f"executor·{target}")
                    return
            notify_message(ctx.ui, f"未知端点：{target}", "error")
            return

        if arg.startswith("forget"):
            name = arg[len("forget") :].strip()
            if not name:
                notify_message(ctx.ui, "用法：/executor forget <name>", "error")
                return
            from nova_coding_agent.executor.endpoints_store import unregister_endpoint

            removed = unregister_endpoint(name)
            if removed:
                notify_message(ctx.ui, f"已移除端点：{name}")
            else:
                notify_message(ctx.ui, f"未知端点：{name}", "error")
            return

        if arg:
            notify_message(ctx.ui, _USAGE, "error")
            return

        # 无参：只选择已存在的本地/远程端点，不承担远程供给职责。
        items = [
            {"value": "local", "label": "local", "description": "本地直接执行（默认）"},
            {
                "value": "executor-local",
                "label": "executor（本地沙箱）",
                "description": "本地 nova-executor 回环实例",
            },
        ]
        for endpoint in _endpoints_from_config():
            url = endpoint["url"] or ""
            desc = url
            if endpoint.get("cwd"):
                desc += f" · {endpoint['cwd']}"
            items.append(
                {
                    "value": f"endpoint:{endpoint['name']}",
                    "label": f"executor·{endpoint['name']}",
                    "description": desc,
                }
            )
        choice = await select_items(
            ctx.ui, f"选择执行后端（当前：{_current_label(current)}）", items
        )
        if choice is None:
            return
        if choice == "local":
            await _switch_to(
                BackendSelection(backend="local"), ctx, "local（本地直接执行）"
            )
        elif choice == "executor-local":
            await _switch_to(
                BackendSelection(backend="executor"), ctx, "executor（本地沙箱）"
            )
        elif choice.startswith("endpoint:"):
            name = choice[len("endpoint:") :]
            for endpoint in _endpoints_from_config():
                if endpoint["name"] == name:
                    url = endpoint["url"]
                    assert url is not None
                    await _switch_remote_url(url, ctx, f"executor·{name}")
                    return

    nova.register_command(
        "executor",
        {
            "handler": _executor_command,
            "description": "切换执行后端（local / executor 本地沙箱 / ws(s) 远程端点）",
        },
    )

    # 分支恢复：session_start/session_tree 时从最新 executor 条目恢复选择。
    # 旧 ssh:// 自有供给条目不再可恢复，安全落回本地直接执行。
    def _restore(ctx: Any) -> None:
        sm = getattr(ctx, "session_manager", None)
        if sm is None:
            return
        for entry in reversed(sm.get_branch()):
            if getattr(entry, "type", "") != "custom":
                continue
            if getattr(entry, "custom_type", "") != _ENTRY_TYPE:
                continue
            data = getattr(entry, "data", None)
            if isinstance(data, dict):
                backend = data.get("backend", "local")
                url = data.get("url")
                if backend == "executor" and url and not _is_ws_url(url):
                    selection = BackendSelection(backend="local")
                else:
                    selection = BackendSelection(
                        backend=backend,
                        url=url,
                        remote_cwd=data.get("remote_cwd"),
                        remote_home=data.get("remote_home"),
                    )
                _attach_policy(ctx, selection)
                set_backend_selection(selection)
                refresh = getattr(ctx, "refresh_system_prompt", None)
                if refresh is not None:
                    refresh()
            return

    nova.on("session_start", lambda _event, ctx: _restore(ctx))
    nova.on("session_tree", lambda _event, ctx: _restore(ctx))
