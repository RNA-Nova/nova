# 扩展开发（Extension）

扩展是会话生命周期的**事件监听 + 动作执行**单元：工具是被模型调用的，扩展是主动参与会话运转的（拦截工具调用、注册命令、门控权限、注入状态条……）。

## 最小扩展

```python
# backend/extensions/my_gate.py
"""拦截 rm -rf 类危险命令，弹确认。"""


def extension(nova):  # nova: NovaExtensionAPI——装载期注册面
    async def on_tool_call(event, ctx):  # ctx: ExtensionContext——运行期动作面
        if event.tool_name != "bash":
            return None
        command = event.params.get("command", "")
        if "rm -rf" not in command:
            return None
        if not ctx.has_ui:
            return None  # headless 降级：放行（或按你的策略拦）
        ok = await ctx.ui.request(
            "confirm", {"title": "危险命令", "message": f"确定执行？\n{command}"}
        )
        if ok.get("confirmed"):
            return None  # None = 放行
        return {"cancel": True, "reason": "用户取消了危险命令"}  # 拦截

    nova.on("tool_call", on_tool_call)
```

## 两个面的分工（最重要的设计）

| 面 | 对象 | 时机 | 干什么 |
|----|------|------|--------|
| **注册面** | `NovaExtensionAPI`（`extension(nova)` 的参数） | 装载时 | 声明"我有什么"：`on` / `registerCommand` / `registerShortcut` / `registerFlag` / `registerProvider` / `registerSpawnHook` / `events` |
| **动作面** | `ExtensionContext`（每个事件 handler 的 `ctx`） | 运行时 | 执行"我现在要"：`send_message` / `exec` / `set_active_tools` / `append_entry` / … |

分工判据：注册不依赖会话（装载期申报），动作没有活会话不成立（发给谁/改谁的注册表）。

## 事件面

### 会话生命周期

| 事件 | 时机 |
|------|------|
| `session_start` | 会话装配完成（`bind_extensions`）；`reason` 区分 cold/agent_change/reload |
| `session_shutdown` | 会话关闭 |
| `session_before_compact` / `session_compact` | 压缩前/后（可附加文件清单、改摘要策略） |
| `session_before_tree` / `session_tree` | 树导航前/后（分支状态恢复挂这里） |
| `session_before_switch` / `session_before_fork` | 切换/分叉前（确认门——返回 `cancel=True` 取消） |

### Agent 循环 hook

| 事件 | 时机 | 能做什么 |
|------|------|---------|
| `before_agent_start` | 每轮开始前 | 临时覆盖系统提示词（仅当轮） |
| `tool_call` | 工具执行前 | 拦截/整形（返回 `cancel` 拦截；官方 permission_gate/plan_mode 即此） |
| `tool_result` | 工具执行后 | 结果加工 |
| `prepare_next_turn` | 下一轮准备 | 注入引导消息 |
| `should_stop_after_turn` | 轮末 | 裁决是否停（返回 bool） |
| `input` / `context` | 用户输入/上下文变换 | 文本加工 |
| `before_provider_request` / `after_provider_response` | 请求/响应 | 观测与改写 |
| `user_bash` | 用户 `!` 命令 | 拦截让位（官方 interactive_shell 即此） |
| `project_trust` | 信任裁决 | 参与项目信任决策 |

注册方式：`nova.on("tool_call", handler)`（返回注销函数）或 `nova.on_input(handler)`。

## 动作面（ExtensionContext）全表

**消息与会话**：`send_message` / `send_user_message` / `append_entry`（写持久化自定义条目——分支恢复的事实源）/ `set_session_name` / `set_label`

**工具**：`get_active_tools` / `get_all_tools` / `set_active_tools`（绝对集应用）/ `refresh_tools`

**模型**：`model`（属性，活取）/ `set_model`（缺鉴权返回 False）/ `get_thinking_level` / `set_thinking_level`

**角色与人格**：`get_agents` / `change_agent` / `save_agent` / `get_personas` / `set_persona_override` / `clear_persona_override`

**执行与系统**：`exec(command, args, options)` / `compact` / `get_context_usage` / `get_system_prompt` / `refresh_system_prompt` / `abort` / `shutdown` / `is_idle` / `has_pending_messages`

**命令上下文扩展**（命令 handler 的 ctx 是 `ExtensionCommandContext`，多一层）：`new_session` / `fork` / `navigate_tree` / `switch_session` / `reload` / `wait_for_idle` / `get_session_info` / `get_scoped_models` / `trust_project` / `untrust_project` / `clone` / `export` / `import_session`

**环境**：`ui`（UIContext）/ `has_ui` / `cwd` / `is_project_trusted()` / `get_signal()` / `extension_path` / `session_manager` / `model_runtime`

## 注册面细目

### `registerCommand(name, options)`

```python
nova.registerCommand("weather", {
    "description": "查天气: /weather <城市>",
    "handler": _weather,           # async (args: str, ctx: ExtensionCommandContext)
    "get_argument_completions": ...,  # 可选：参数补全（async (prefix) -> [...]）
})
```

命令即 slash 命令——TUI 自动进补全与 `/help`。

### `registerShortcut(key, options)` / `registerFlag(name, options)` / `registerProvider(name, config)` / `registerSpawnHook(hook)`

- 快捷键：运行时在扩展侧，目录经 RPC 透出给前端（键位绑定归前端键位子系统）；
- flag：扩展的命名开关（`registerFlag` 注册，`nova.getFlag`/`ctx.getFlag` 读值，目录经 RPC `getExtensionFlags` 透出）；**注意**：本发布线暂无 CLI 投影与持久化（`setExtensionFlag` 只写内存态，重启即失——别把它当用户可用的启动参数宣传）；
- provider：注册模型 provider（可带 OAuth 配置与 `modify_models` 钩子）；
- spawn hook：进程 spawn 前整形（环境注入等）。

### `events`（扩展间事件总线）

`nova.events` 是所有扩展共享的事件总线——扩展间协作用（如 A 扩展发"任务完成"，B 扩展听着发通知），不跨会话。

## 状态持久化的正确姿势

扩展状态要**分支安全**：不写自己的文件，用 `ctx.append_entry(type="my-ext", data={...})` 写进会话条目，在 `session_start` / `session_tree` 重放恢复——官方 plan_mode（`/plan` 状态）与 tools_panel（面板开关）即此模式。这样 fork/导航后每条分支状态独立一致。

## UI 原语

`ctx.ui` 是泛型 transport（零词汇）：`ui.request(method, params)` / `ui.notify(...)` / `ui.has_capability(name)`。交互词汇由包定义；官方基线糖库在 `nova_base.ui_primitives`（`select`/`confirm`/`input`/`form`/`notify_message`/`set_status`）——`requires = ["nova-base"]` 后直接复用：

```python
from nova_base.ui_primitives import confirm, select_items

if await confirm(ctx.ui, "确认", "确认删除？"):
    ...
```

`set_status` 是展示类通知（footer 扩展状态行）：`await ctx.ui.notify("set_status", {"key": "my-ext", "text": "⏸ paused"})`，消失传空文本。

## 形态纪律

- 单文件优先（`extensions/x.py`）；目录形态（`x/extension.py` 或 `x/__init__.py`）仅当需要同目录子模块/资产——发现只收根级 `.py`，目录内 helper 不会被误载；
- 装载期不做 I/O（`extension(nova)` 里只注册）；
- handler 里先判 `ctx.has_ui` 再决定交互路径（headless 必须降级）。

下一页：[前端渲染器](frontend.md)。
