"""执行后端切换扩展（/executor 命令）。

设计定案（`nova-harness/backend/examples/executor-integration.md`）：

- **用户主动切换**：``/executor``（选择器）/ ``/executor local`` /
  ``/executor remote <name|url|user@host> [远程目录]`` /
  ``/executor forget <name>``；v1 模型不能切；
- **三通道分离**（R2）：会话条目管记忆（分支恢复）、runtime 格管执行
  （``set_backend_selection`` 翻转，bash 引擎执行期直读）、notice 管
  用户回执（用户可见、不进模型上下文）；
- **SSH 远程供给**（/ssh 模式，类 VS Code Remote-SSH）：裸 ``user@host``
  直接可用——首次连接经终端让位输一次密码装入 Nova 管理密钥（之后
  BatchMode 免密），供给成功后**自动登记**进 settings
  ``executor.endpoints``（下次选择器直接可选，无需手改 JSON）；
- **远程执行 cwd**：远程文件系统与本地无关，本地 cwd 不能用——用户
  显式给目录则 ``test -d`` 校验后使用（并随端点记忆）；缺省按
  **会话隔离的远程工作区**（``<远程家目录>/.nova/agent/executor/
  workspaces/<session-id>``，切换时 mkdir -p 建好）。环境段 `<cwd>`
  渲染执行 cwd（codex 语义：命令在哪跑写哪）；
- 端点清单来自 settings ``executor.endpoints``（core 侧 ``ExecutorSettings``）。
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, List, Optional, Tuple

from nova_harness.core.extensions.api import NovaExtensionAPI

from nova_coding_agent.executor import (
    BackendSelection,
    ExecutorBashOperations,
    ProvisionError,
    get_backend_selection,
    get_executor_manager,
    is_ssh_url,
    parse_ssh_target,
    resolve_spawn_policy,
    set_backend_selection,
)
from nova_coding_agent.ui_primitives import input as input_dialog
from nova_coding_agent.ui_primitives import (
    notify_message,
    select_items,
    set_status,
)

_ENTRY_TYPE = "executor_backend"
# 供给进度的 footer 状态位（同 key 幂等覆盖，结束清除）
_STATUS_KEY = "executor-provision"
# 选择器中的"添加远程主机"伪项值
_ADD_REMOTE_VALUE = "__add_remote__"

_USAGE = "用法：/executor [local|remote <name|url|user@host> [远程目录]|forget <name>]"

# 会话隔离远程工作区：远程家目录下的固定基目录 + session-id
_REMOTE_WORKSPACE_BASE = ".nova/agent/executor/workspaces"


def _describe_url(url: str) -> str:
    """端点 url 的人类可读描述（ssh:// 显示为 ssh·user@host）。"""
    if is_ssh_url(url):
        try:
            return f"ssh·{parse_ssh_target(url).display}"
        except ProvisionError:
            return url
    return url


def _attach_policy(ctx: Any, selection: BackendSelection) -> None:
    """按 settings 沙箱档位为 executor 后端组装策略（挂到 selection 上）。

    策略作用目录三态：SSH 远程取 remote_cwd（会话隔离工作区）；本地
    回环 executor（url 为空）取本地 cwd；ws 直连端点远程 cwd 未知，
    v1 不沙箱（登记限制）。
    """
    if selection.backend != "executor":
        return
    getter = getattr(ctx, "get_executor_settings", None)
    settings = getter() if callable(getter) else None
    if selection.remote_cwd:
        effective_cwd: Optional[str] = selection.remote_cwd
    elif not selection.url:
        effective_cwd = getattr(ctx, "cwd", None)
    else:
        effective_cwd = None
    selection.spawn_policy = resolve_spawn_policy(settings, effective_cwd)


def _current_label(selection: BackendSelection) -> str:
    if selection.backend == "executor":
        return f"executor（{_describe_url(selection.url) if selection.url else '本地沙箱'}）"
    return "local（本地直接执行）"


def _endpoints_from_settings(ctx: Any) -> List[Dict[str, Optional[str]]]:
    """从 settings 读已知远程端点清单（经 ExtensionContext.get_executor_settings）。"""
    getter = getattr(ctx, "get_executor_settings", None)
    if getter is None:
        return []
    executor = getter()
    if executor is None or not executor.endpoints:
        return []
    return [
        {"name": e.name, "url": e.url, "cwd": getattr(e, "cwd", None)}
        for e in executor.endpoints
    ]


def _session_id(ctx: Any) -> str:
    """当前会话 id（远程工作区目录名）。"""
    sm = getattr(ctx, "session_manager", None)
    getter = getattr(sm, "get_session_id", None) if sm is not None else None
    sid = getter() if callable(getter) else None
    return str(sid) if sid else "default"


async def _remote_exec(manager: Any, url: str, command: str) -> Any:
    """在远程执行一条探测/准备命令（cwd 用 "/"——永远存在的目录）。

    独立模块级函数：扩展单测可替换；复用 ExecutorBashOperations 的
    流式/退出码管线（client 已缓存，零额外连接）。
    """
    ops = ExecutorBashOperations(manager, url=url)
    return await ops.execute(command, "/", {})


def extension(nova: NovaExtensionAPI) -> None:
    """注册 /executor 命令与分支恢复钩子。"""

    async def _switch_to(
        selection: BackendSelection,
        ctx: Any,
        label: str,
        entry_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """翻转通道：runtime 格（执行）+ 会话条目（记忆）+ 系统提示词重建
        （环境段刷新）+ notice（用户回执——可见但不进模型上下文）。"""
        _attach_policy(ctx, selection)
        set_backend_selection(selection)
        data: Dict[str, Any] = {"backend": selection.backend, "url": selection.url}
        if entry_extra:
            data.update(entry_extra)
        ctx.append_entry(_ENTRY_TYPE, data)
        refresh = getattr(ctx, "refresh_system_prompt", None)
        if refresh is not None:
            refresh()
        notify_message(ctx.ui, f"执行后端已切换：{label}")

    async def _bootstrap_via_terminal(command: str, ctx: Any) -> int:
        """首连引导：终端让位执行交互 ssh（用户对着原生提示符输密码）。

        复用 dialog:interactive-shell（interactive_shell 扩展同款原语）——
        密码不经过 Nova 进程。返回命令 exit code。
        """
        resp = await ctx.ui.request(
            "dialog:interactive-shell",
            {"command": command, "cwd": getattr(ctx, "cwd", None)},
        )
        if resp.cancelled:
            return 130
        value = resp.value if isinstance(resp.value, dict) else {}
        code = value.get("exitCode")
        return code if isinstance(code, int) else -1

    async def _prepare_remote_cwd(
        target: Any, ctx: Any, explicit: Optional[str]
    ) -> Optional[Tuple[str, str, str]]:
        """确定并落实远程执行 cwd；失败回 None（已发 notice，不切换）。

        返回 (remote_cwd, remote_shell, remote_home)。显式路径做 ``test -d``
        校验（防笔误）；缺省按会话隔离工作区 ``mkdir -p`` 建好。
        """
        manager = get_executor_manager()
        handle = manager.get_ssh_handle(target)
        home = getattr(handle, "default_cwd", "") if handle else ""
        remote_shell = getattr(handle, "remote_shell", "") if handle else ""

        if explicit:
            remote_cwd = explicit
            if remote_cwd.startswith("~"):
                # executor 的 file:// cwd 不做 tilde 展开——归一到绝对路径
                remote_cwd = (home or "").rstrip("/") + remote_cwd[1:]
            result = await _remote_exec(
                manager, target.canonical_url, f"test -d {shlex.quote(remote_cwd)}"
            )
            if result.exit_code != 0:
                notify_message(
                    ctx.ui,
                    f"远程目录不存在：{remote_cwd}（已保持当前后端）",
                    "error",
                )
                return None
            return remote_cwd, remote_shell, home

        # 缺省：会话隔离工作区（固定基目录 + session-id）
        if not home:
            notify_message(ctx.ui, "未探测到远程家目录，无法建立工作区", "error")
            return None
        remote_cwd = f"{home.rstrip('/')}/{_REMOTE_WORKSPACE_BASE}/{_session_id(ctx)}"
        result = await _remote_exec(
            manager, target.canonical_url, f"mkdir -p {shlex.quote(remote_cwd)}"
        )
        if result.exit_code != 0:
            notify_message(
                ctx.ui,
                f"远程工作区创建失败：{remote_cwd}（{result.output.strip()[:200]}）",
                "error",
            )
            return None
        return remote_cwd, remote_shell, home

    async def _switch_ssh(
        target_text: str,
        ctx: Any,
        *,
        register: bool,
        name: Optional[str] = None,
        explicit_cwd: Optional[str] = None,
    ) -> None:
        """SSH 远程切换：eager 供给（进度+首连引导）→ 落实远程 cwd →
        自动登记 → 翻转（条目带 remote_cwd/remote_shell，环境段随之重建）。"""
        try:
            target = parse_ssh_target(target_text)
        except ProvisionError as exc:
            notify_message(ctx.ui, str(exc), "error")
            return

        manager = get_executor_manager()

        def _progress(text: str) -> None:
            set_status(ctx.ui, _STATUS_KEY, f"⏳ {text}")

        # 终端让位能力缺失（headless/无 TUI）→ 不传 bootstrap，
        # auth 失败时拿到的是 ssh-copy-id 指引而非"exit -1"
        bootstrap = None
        if ctx.has_ui and ctx.ui.has_capability("dialog:interactive-shell"):
            bootstrap = lambda command: _bootstrap_via_terminal(
                command, ctx
            )  # noqa: E731

        try:
            await manager.provision_ssh(
                target, on_progress=_progress, bootstrap=bootstrap
            )
        except ProvisionError as exc:
            notify_message(ctx.ui, f"远程供给失败（{exc.step}）：{exc}", "error")
            return
        except Exception as exc:  # WS 握手等供给后连接失败
            notify_message(ctx.ui, f"远程 executor 连接失败：{exc}", "error")
            return
        finally:
            set_status(ctx.ui, _STATUS_KEY, None)

        prepared = await _prepare_remote_cwd(target, ctx, explicit_cwd)
        if prepared is None:
            return
        remote_cwd, remote_shell, remote_home = prepared

        endpoint_name = name or target.default_name
        if register:
            registrar = getattr(ctx, "register_executor_endpoint", None)
            if registrar is not None:
                # 只记忆用户显式给过的目录；缺省会话工作区按会话现算，不记忆
                registrar(endpoint_name, target.canonical_url, explicit_cwd or None)
                notify_message(
                    ctx.ui,
                    f"已登记端点 {endpoint_name}（ssh·{target.display}）"
                    "——下次 /executor 直接选择",
                )
        await _switch_to(
            BackendSelection(
                backend="executor",
                url=target.canonical_url,
                remote_cwd=remote_cwd,
                remote_home=remote_home or None,
            ),
            ctx,
            f"executor·{endpoint_name}（ssh·{target.display} · {remote_cwd}）",
            entry_extra={
                "remote_cwd": remote_cwd,
                "remote_shell": remote_shell or None,
                "remote_home": remote_home or None,
            },
        )

    async def _switch_remote_url(url: str, ctx: Any, label: str) -> None:
        """ws(s):// 直连切换（无供给——端点自管活）。"""
        await _switch_to(BackendSelection(backend="executor", url=url), ctx, label)

    async def _executor_command(args: str, ctx: Any) -> None:
        getter = getattr(ctx, "get_executor_settings", None)
        executor_settings = getter() if getter is not None else None

        current = get_backend_selection(executor_settings)
        arg = args.strip()

        if arg == "local":
            await _switch_to(
                BackendSelection(backend="local"), ctx, "local（本地直接执行）"
            )
            return

        if arg.startswith("remote"):
            rest = arg[len("remote") :].strip()
            if not rest:
                notify_message(
                    ctx.ui,
                    "用法：/executor remote <name|url|user@host> [远程目录]",
                    "error",
                )
                return
            # 目标与可选远程目录（目录取余下整段——允许空格路径）
            target, _, path_arg = rest.partition(" ")
            path_arg = path_arg.strip() or None
            if target == "local":
                # 旧用法兼容：/executor remote local = 本地回环沙箱
                await _switch_to(
                    BackendSelection(backend="executor"),
                    ctx,
                    "executor（本地沙箱）",
                )
                return
            if "://" in target and not is_ssh_url(target):
                await _switch_remote_url(target, ctx, f"executor（{target}）")
                return
            if is_ssh_url(target):
                await _switch_ssh(target, ctx, register=True, explicit_cwd=path_arg)
                return
            # 裸名：先按名字查端点清单；未命中按 SSH 目标（user@host 直输，
            # ssh config 别名同享）——VS Code "Add SSH Host" 对位
            for endpoint in _endpoints_from_settings(ctx):
                if endpoint["name"] == target:
                    url = endpoint["url"]
                    assert url is not None
                    if is_ssh_url(url):
                        await _switch_ssh(
                            url,
                            ctx,
                            register=False,
                            name=target,
                            # 显式参数优先；缺省用端点记住的目录
                            explicit_cwd=path_arg or endpoint.get("cwd"),
                        )
                    else:
                        await _switch_remote_url(url, ctx, f"executor·{target}")
                    return
            await _switch_ssh(target, ctx, register=True, explicit_cwd=path_arg)
            return

        if arg.startswith("forget"):
            name = arg[len("forget") :].strip()
            if not name:
                notify_message(ctx.ui, "用法：/executor forget <name>", "error")
                return
            unregister = getattr(ctx, "unregister_executor_endpoint", None)
            removed = unregister(name) if unregister is not None else False
            if removed:
                notify_message(ctx.ui, f"已移除端点：{name}")
            else:
                notify_message(ctx.ui, f"未知端点：{name}", "error")
            return

        if arg:
            notify_message(ctx.ui, _USAGE, "error")
            return

        # 无参：选择器（含"添加远程主机"入口）
        items = [
            {"value": "local", "label": "local", "description": "本地直接执行（默认）"},
            {
                "value": "executor-local",
                "label": "executor（本地沙箱）",
                "description": "本地 nova-executor 回环实例",
            },
        ]
        for endpoint in _endpoints_from_settings(ctx):
            url = endpoint["url"] or ""
            desc = _describe_url(url)
            if endpoint.get("cwd"):
                desc += f" · {endpoint['cwd']}"
            items.append(
                {
                    "value": f"endpoint:{endpoint['name']}",
                    "label": f"executor·{endpoint['name']}",
                    "description": desc,
                }
            )
        items.append(
            {
                "value": _ADD_REMOTE_VALUE,
                "label": "＋ 添加远程主机…",
                "description": "输入 user@host——首次连接输一次密码，之后免密",
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
        elif choice == _ADD_REMOTE_VALUE:
            text = await input_dialog(
                ctx.ui, "添加远程主机", placeholder="user@host 或 user@host:port"
            )
            if not text or not text.strip():
                return
            path_text = await input_dialog(
                ctx.ui,
                "远程工作目录（留空 = 会话工作区）",
                placeholder="如 /data/proj；留空自动建会话工作区",
            )
            await _switch_ssh(
                text.strip(),
                ctx,
                register=True,
                explicit_cwd=(path_text or "").strip() or None,
            )
        elif choice.startswith("endpoint:"):
            name = choice[len("endpoint:") :]
            for endpoint in _endpoints_from_settings(ctx):
                if endpoint["name"] == name:
                    url = endpoint["url"]
                    assert url is not None
                    if is_ssh_url(url):
                        await _switch_ssh(
                            url,
                            ctx,
                            register=False,
                            name=name,
                            explicit_cwd=endpoint.get("cwd"),
                        )
                    else:
                        await _switch_remote_url(url, ctx, f"executor·{name}")
                    return

    nova.register_command(
        "executor",
        {
            "handler": _executor_command,
            "description": "切换执行后端（local / executor 本地沙箱 / 远程端点 / SSH 主机）",
        },
    )

    # 分支恢复：session_start/session_tree 时从最新 executor 条目恢复选择。
    # ssh:// 条目只翻转模式格——隧道在执行期首次使用时懒供给
    # （BatchMode；免密已在首连引导时就绪）。
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
                selection = BackendSelection(
                    backend=data.get("backend", "local"),
                    url=data.get("url"),
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
